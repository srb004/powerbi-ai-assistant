import asyncio
import json
import os
import threading
import requests
from azure.identity import ClientSecretCredential
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class PowerBIMCPClient:
    def __init__(self, tenant_id, client_id, client_secret, mcp_exe_path):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.mcp_exe_path = mcp_exe_path

        self.session = None
        self._stdio_ctx = None
        self.connected = False
        self.workspace_name = ""
        self.semantic_model_name = ""
        self._token_cache = None
        self._loop = asyncio.new_event_loop()
        self._mcp_lock = asyncio.Lock()    
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=120)

    def _get_access_token(self):
        credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        token = credential.get_token("https://analysis.windows.net/powerbi/api/.default")
        self._token_cache = token.token
        return token.token

    def list_workspaces(self):
        token = self._get_access_token()
        resp = requests.get(
            "https://api.powerbi.com/v1.0/myorg/groups",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return [w["name"] for w in resp.json().get("value", [])]

    def list_datasets(self, workspace_name):
        token = self._token_cache or self._get_access_token()

        resp = requests.get(
            "https://api.powerbi.com/v1.0/myorg/groups",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        workspaces = resp.json().get("value", [])
        ws = next((w for w in workspaces if w["name"] == workspace_name), None)
        if not ws:
            return []

        ws_id = ws["id"]
        resp2 = requests.get(
            f"https://api.powerbi.com/v1.0/myorg/groups/{ws_id}/datasets",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp2.raise_for_status()
        datasets = resp2.json().get("value", [])
        system_prefixes = ("DataflowsStaging", "Report Usage Metrics")
        return [
            d["name"] for d in datasets
            if not any(d["name"].startswith(p) for p in system_prefixes)
        ]

    async def _start_session(self):
        token = self._get_access_token()
        params = StdioServerParameters(
            command=self.mcp_exe_path,
            args=["--start", "--readonly"],
            env={**os.environ, "PBI_MODELING_MCP_ACCESS_TOKEN": token},
        )
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()
        self.session = ClientSession(read, write)
        await self.session.__aenter__()
        await self.session.initialize()

    async def _connect_async(self, workspace_name, semantic_model_name):
        if not workspace_name or not semantic_model_name:
            raise ValueError(
                f"workspace_name and semantic_model_name must be non-empty. "
                f"Got: {workspace_name!r}, {semantic_model_name!r}"
            )
        result = await self._locked_call(
            "connection_operations",
            {
                "request": {
                    "operation": "ConnectFabric",
                    "workspaceName": workspace_name,
                    "semanticModelName": semantic_model_name,
                }
            },
        )
        if not result.get("success"):
            raise RuntimeError(
                f"ConnectFabric failed: {result.get('message', 'unknown error')}"
            )
        self.workspace_name = workspace_name
        self.semantic_model_name = semantic_model_name
        self.connected = True
        return result

    async def _locked_call(self, tool_name: str, payload: dict):

        async with self._mcp_lock:
            if not self.session:
                raise RuntimeError("MCP session not started.")
            result = await self.session.call_tool(tool_name, payload)
            text = result.content[0].text
            print(f"[MCP RAW] tool={tool_name} response={text[:300]}")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"success": False, "message": text}

    async def _call_tool_async(self, tool_name: str, payload: dict):
        return await self._locked_call(tool_name, payload)

    async def _build_schema_async(self):

        lines = []

        tables_data = await self._locked_call(
            "table_operations", {"request": {"operation": "List"}}
        )
        if not tables_data.get("success"):
            return "Schema unavailable."

        hidden_prefixes = ("DateTableTemplate", "LocalDateTable")
        tables = [
            t for t in tables_data.get("data", [])
            if not t["name"].startswith(hidden_prefixes)
        ]

        lines.append("=== TABLES & COLUMNS ===")
        for table in tables:
            col_data = await self._locked_call(
                "column_operations",
                {"request": {"operation": "List", "tableName": table["name"]}},
            )
            cols = []
            if col_data.get("success") and col_data.get("data"):
                raw_cols = col_data["data"][0].get("columns", [])
                for c in raw_cols:
                    if c.get("isHidden", False) or c["name"].startswith("RowNumber"):
                        continue
                    dtype = c.get("dataType", "")
                    date_flag = (
                        " [DATE — use YEAR('{}')[{}] for filtering]".format(
                            table["name"], c["name"]
                        )
                        if dtype in ("DateTime", "Date")
                        else ""
                    )
                    cols.append(f"    {c['name']} ({dtype}){date_flag}")

            lines.append(f"\nTable: '{table['name']}'")
            if cols:
                lines.extend(cols)

        measures_data = await self._locked_call(
            "measure_operations", {"request": {"operation": "List"}}
        )
        if measures_data.get("success"):
            lines.append("\n=== MEASURES ===")
            lines.append(
                "Reference measures as [MeasureName] — "
                "do NOT use SUM/COUNT on columns if a measure exists."
            )
            for m in measures_data.get("data", []):
                expr = m.get("expression", "")
                expr_short = expr.replace("\n", " ")[:80] if expr else ""
                if expr_short:
                    lines.append(f"  [{m['name']}]  →  {expr_short}")
                else:
                    lines.append(f"  [{m['name']}]")

        rel_data = await self._locked_call(
            "relationship_operations", {"request": {"operation": "List"}}
        )
        if rel_data.get("success") and rel_data.get("data"):
            lines.append("\n=== RELATIONSHIPS ===")
            for r in rel_data.get("data", []):
                lines.append(
                    f"  '{r.get('fromTable')}[{r.get('fromColumn')}]' → "
                    f"'{r.get('toTable')}[{r.get('toColumn')}]'"
                )

        return "\n".join(lines)

    async def _list_tools_async(self):
        result = await self.session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema,
            }
            for t in result.tools
        ]

    async def _disconnect_async(self):
        self.connected = False
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception:
                pass
        if self._stdio_ctx:
            try:
                await self._stdio_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self.session = None
        self._stdio_ctx = None

    def start_session(self):
        self.run(self._start_session())

    def connect(self, workspace_name, semantic_model_name):
        return self.run(self._connect_async(workspace_name, semantic_model_name))

    def list_tools(self):
        return self.run(self._list_tools_async())

    def call_tool(self, tool_name: str, payload: dict):

        future = asyncio.run_coroutine_threadsafe(
            self._locked_call(tool_name, payload), self._loop
        )
        return future.result(timeout=60)

    def build_schema(self):
        return self.run(self._build_schema_async())

    def disconnect(self):
        self.run(self._disconnect_async())
        self._loop.call_soon_threadsafe(self._loop.stop)