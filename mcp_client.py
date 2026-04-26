# mcp_client.py
import asyncio
import json
import logging
import os
import threading

import httpx
import requests
from azure.identity import ClientSecretCredential
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PBI_MCP_NPM_PACKAGE = "@microsoft/powerbi-modeling-mcp"

logging.getLogger("mcp.client.stdio").setLevel(logging.CRITICAL)


class PowerBIMCPClient:
    def __init__(self, tenant_id, client_id, client_secret):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret

        self.session = None
        self._stdio_ctx = None
        self.connected = False
        self.workspace_name = ""
        self.semantic_model_name = ""
        self._workspace_id = ""
        self._dataset_id = ""
        self._token_cache = None

        self._loop = asyncio.new_event_loop()
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
        workspaces = resp.json().get("value", [])
        return [w["name"] for w in workspaces]

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
        if not token:
            raise RuntimeError("Service principal returned an empty access token.")
        print(f"[AUTH] Token minted (len={len(token)}, prefix={token[:30]}...)")

        # Run the official Microsoft Power BI Modeling MCP via npx.
        # On Windows the npx shim is a .cmd file, so go through cmd.exe so the
        # MCP SDK can spawn it without PATH-resolution surprises.
        npx_args = ["-y", PBI_MCP_NPM_PACKAGE, "--start", "--readonly"]
        if os.name == "nt":
            command, args = "cmd", ["/c", "npx", *npx_args]
        else:
            command, args = "npx", npx_args

        print(f"[MCP] Spawning: {command} {' '.join(args)}")

        env = {**os.environ, "PBI_MODELING_MCP_ACCESS_TOKEN": token}
        # Confirm the env var actually carries the token into the child process.
        print(f"[MCP] PBI_MODELING_MCP_ACCESS_TOKEN set "
              f"(len={len(env['PBI_MODELING_MCP_ACCESS_TOKEN'])})")

        params = StdioServerParameters(command=command, args=args, env=env)
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()
        self.session = ClientSession(read, write)
        await self.session.__aenter__()
        await self.session.initialize()

    def _resolve_ids(self, workspace_name: str, dataset_name: str) -> None:
        """Look up workspace and dataset IDs by name. Cached on self for use
        by REST executeQueries (which needs IDs, not names)."""
        token = self._token_cache or self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        ws_resp = requests.get(
            "https://api.powerbi.com/v1.0/myorg/groups",
            headers=headers, timeout=30,
        )
        ws_resp.raise_for_status()
        ws = next(
            (w for w in ws_resp.json().get("value", [])
             if w.get("name") == workspace_name),
            None,
        )
        if not ws:
            raise RuntimeError(f"Workspace '{workspace_name}' not found via REST.")
        self._workspace_id = ws["id"]

        ds_resp = requests.get(
            f"https://api.powerbi.com/v1.0/myorg/groups/{self._workspace_id}/datasets",
            headers=headers, timeout=30,
        )
        ds_resp.raise_for_status()
        ds = next(
            (d for d in ds_resp.json().get("value", [])
             if d.get("name") == dataset_name),
            None,
        )
        if not ds:
            raise RuntimeError(
                f"Dataset '{dataset_name}' not found in workspace "
                f"'{workspace_name}' via REST."
            )
        self._dataset_id = ds["id"]
        print(f"[REST] Resolved IDs: workspace={self._workspace_id}, "
              f"dataset={self._dataset_id}")

    async def _execute_dax_rest(self, query: str) -> dict:
        """Execute a DAX query via Power BI REST POST /executeQueries.

        Returns a structured dict so callers can distinguish HTTP failures,
        DAX errors, and genuinely empty result sets:
            {"success": bool, "rows": list[dict], "error": str | None}
        """
        if not self._workspace_id or not self._dataset_id:
            return {
                "success": False,
                "rows": [],
                "error": "Workspace/dataset IDs not resolved — call connect() first.",
            }
        token = self._token_cache or self._get_access_token()
        url = (
            f"https://api.powerbi.com/v1.0/myorg/groups/{self._workspace_id}"
            f"/datasets/{self._dataset_id}/executeQueries"
        )
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"queries": [{"query": query}]},
                )
        except Exception as e:
            print(f"[REST DAX] transport error: {e}")
            return {"success": False, "rows": [], "error": str(e)}

        if resp.status_code != 200:
            try:
                err_body = resp.json().get("error", {})
                err_msg = err_body.get("message") or resp.text[:300]
            except Exception:
                err_msg = resp.text[:300]
            print(f"[REST DAX] HTTP {resp.status_code}: {err_msg}")
            return {
                "success": False,
                "rows": [],
                "error": f"HTTP {resp.status_code}: {err_msg}",
            }

        payload = resp.json()
        results = payload.get("results", [])
        if not results:
            return {"success": True, "rows": [], "error": None}

        first = results[0]
        # Per-query DAX errors come back as {"error": {"code": ..., "message": ...}}
        if "error" in first:
            err = first["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            print(f"[REST DAX] query error: {msg}")
            return {"success": False, "rows": [], "error": msg}

        tables = first.get("tables", [])
        rows = tables[0].get("rows", []) if tables else []
        return {"success": True, "rows": rows or [], "error": None}

    async def _connect_async(self, workspace_name, semantic_model_name):
        if not workspace_name or not semantic_model_name:
            raise ValueError(
                f"workspace_name and semantic_model_name must be non-empty. "
                f"Got: {workspace_name!r}, {semantic_model_name!r}"
            )
        # Resolve IDs first so REST executeQueries is ready by the time we
        # build the schema.
        self._resolve_ids(workspace_name, semantic_model_name)

        result = await self.session.call_tool(
            "connection_operations",
            {
                "request": {
                    "operation": "ConnectFabric",
                    "workspaceName": workspace_name,
                    "semanticModelName": semantic_model_name,
                }
            },
        )
        data = json.loads(result.content[0].text)
        if not data.get("success"):
            raise RuntimeError(f"ConnectFabric failed: {data.get('message', 'unknown error')}")
        self.workspace_name = workspace_name
        self.semantic_model_name = semantic_model_name
        self.connected = True
        return data

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

    async def _call_tool_async(self, tool_name, payload):
        if not self.session:
            raise RuntimeError("MCP session not started.")
        result = await self.session.call_tool(tool_name, payload)
        text = result.content[0].text
        print(f"[MCP RAW] tool={tool_name} response={text[:300]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"success": False, "message": text}

    async def _build_schema_async(self):
        """
        Build the textual schema dump fed to the LLM system prompt.

        Tables, measures, and relationships are still pulled via MCP (those
        tools work). Column metadata is fetched via the Power BI REST
        executeQueries endpoint, running INFO.VIEW.COLUMNS() directly against
        the model engine. This bypasses the broken column_operations tool in
        the 0.5.0-beta.4 Power BI Modeling MCP entirely.
        """
        hidden_prefixes = ("DateTableTemplate", "LocalDateTable")

        info_view_query = (
            "EVALUATE SELECTCOLUMNS(INFO.VIEW.COLUMNS(), "
            '"TableName", [Table], '
            '"ColumnName", [Name], '
            '"DataType", [DataType], '
            '"IsHidden", [IsHidden])'
        )
        tables_task = asyncio.create_task(self._call_tool_async(
            "table_operations", {"request": {"operation": "List"}}
        ))
        measures_task = asyncio.create_task(self._call_tool_async(
            "measure_operations", {"request": {"operation": "List"}}
        ))
        relationships_task = asyncio.create_task(self._call_tool_async(
            "relationship_operations", {"request": {"operation": "List"}}
        ))
        columns_task = asyncio.create_task(self._execute_dax_rest(info_view_query))
        # INFO.MEASURES() returns the actual DAX expression for every measure,
        # which the LLM needs to reason about grain (line-item vs order-level
        # vs subtotal). measure_operations.List often returns empty strings
        # for [Expression], so we fetch them directly via REST.
        measure_expr_task = asyncio.create_task(self._execute_dax_rest(
            "EVALUATE SELECTCOLUMNS(INFO.MEASURES(), "
            '"Name", [Name], "Expression", [Expression])'
        ))

        tables_data = await tables_task
        if not tables_data.get("success"):
            return "Schema unavailable."

        tables = [
            t for t in tables_data.get("data", [])
            if not t["name"].startswith(hidden_prefixes)
        ]

        columns_result = await columns_task
        column_rows = columns_result.get("rows", []) if columns_result.get("success") else []
        measures_data = await measures_task
        rel_data = await relationships_task
        measure_expr_result = await measure_expr_task

        # name -> expression map sourced via INFO.MEASURES().
        measure_expressions: dict[str, str] = {}
        if measure_expr_result.get("success"):
            for row in measure_expr_result.get("rows", []):
                name = row.get("[Name]") or row.get("Name")
                expr = row.get("[Expression]") or row.get("Expression")
                if name and expr:
                    measure_expressions[str(name)] = str(expr)
            print(f"[Schema] INFO.MEASURES() returned "
                  f"{len(measure_expressions)} measure expressions.")

        # REST executeQueries returns SELECTCOLUMNS-aliased keys as bracketed
        # strings, e.g. "[TableName]". Handle unbracketed too just in case.
        def _row_get(row: dict, *keys):
            for k in keys:
                if k in row:
                    return row[k]
                if f"[{k}]" in row:
                    return row[f"[{k}]"]
            return None

        columns_by_table: dict[str, list[dict]] = {}
        for row in column_rows:
            tname = _row_get(row, "TableName")
            cname = _row_get(row, "ColumnName")
            dtype = _row_get(row, "DataType") or ""
            ihid = _row_get(row, "IsHidden")
            if not tname or not cname:
                continue
            columns_by_table.setdefault(tname, []).append({
                "name": cname,
                "dataType": str(dtype),
                "isHidden": bool(ihid),
            })

        print(f"[Schema] REST INFO.VIEW.COLUMNS() returned "
              f"{len(column_rows)} rows across {len(columns_by_table)} tables.")

        # Fallback if INFO.VIEW.COLUMNS() isn't supported by the engine
        # (older models). Joins INFO.TABLES() with INFO.COLUMNS() on TableID
        # and translates numeric data-type IDs to names.
        if not columns_by_table:
            print("[Schema] INFO.VIEW.COLUMNS() empty via REST; "
                  "falling back to INFO.COLUMNS().")
            fallback_query = (
                "EVALUATE NATURALINNERJOIN("
                'SELECTCOLUMNS(INFO.TABLES(), "TID", [ID], "TableName", [Name]),'
                "SELECTCOLUMNS(INFO.COLUMNS(), "
                '"TID", [TableID], '
                '"ColumnName", [ExplicitName], '
                '"DataTypeId", [ExplicitDataType], '
                '"IsHidden", [IsHidden]))'
            )
            fallback_result = await self._execute_dax_rest(fallback_query)
            fallback_rows = fallback_result.get("rows", []) if fallback_result.get("success") else []
            DAX_TYPE_NAMES = {
                1: "Automatic", 2: "String", 6: "Int64", 8: "Double",
                9: "DateTime", 10: "Decimal", 11: "Boolean",
                17: "Binary", 19: "Unknown", 20: "Variant",
            }
            for row in fallback_rows:
                tname = _row_get(row, "TableName")
                cname = _row_get(row, "ColumnName")
                tid = _row_get(row, "DataTypeId")
                ihid = _row_get(row, "IsHidden")
                if not tname or not cname:
                    continue
                try:
                    tid_int = int(tid) if tid is not None else None
                except (TypeError, ValueError):
                    tid_int = None
                columns_by_table.setdefault(tname, []).append({
                    "name": cname,
                    "dataType": DAX_TYPE_NAMES.get(tid_int, str(tid) if tid is not None else ""),
                    "isHidden": bool(ihid),
                })
            print(f"[Schema] INFO.COLUMNS() fallback returned "
                  f"{len(fallback_rows)} rows across {len(columns_by_table)} tables.")

        # ── Probe actual data date range ──
        # The agent will hardcode dates against "today" if it doesn't know
        # the model's real data window, so query MIN/MAX up front and
        # surface them in the schema.
        def _fmt_date(s):
            if isinstance(s, str):
                return s.split("T")[0] if "T" in s else s
            return str(s) if s is not None else ""

        date_probes: list[tuple[str, str]] = []
        for tname in columns_by_table:
            if tname.startswith(hidden_prefixes):
                continue
            for c in columns_by_table[tname]:
                if c.get("isHidden"):
                    continue
                if "Date" in str(c.get("dataType", "")):
                    date_probes.append((tname, c["name"]))
                    break
            if len(date_probes) >= 8:
                break

        date_range_lines: list[str] = []
        if date_probes:
            range_parts = []
            for i, (tname, cname) in enumerate(date_probes):
                range_parts.append(f'"min_{i}", CALCULATE(MIN(\'{tname}\'[{cname}]))')
                range_parts.append(f'"max_{i}", CALCULATE(MAX(\'{tname}\'[{cname}]))')
            range_query = "EVALUATE ROW(" + ", ".join(range_parts) + ")"
            range_result = await self._execute_dax_rest(range_query)
            if range_result.get("success") and range_result.get("rows"):
                row = range_result["rows"][0]
                for i, (tname, cname) in enumerate(date_probes):
                    mn = row.get(f"[min_{i}]")
                    mx = row.get(f"[max_{i}]")
                    if mn and mx:
                        date_range_lines.append(
                            f"  {tname}[{cname}]: {_fmt_date(mn)} → {_fmt_date(mx)}"
                        )
                print(f"[Schema] Date ranges resolved for {len(date_range_lines)} columns.")
            else:
                print(f"[Schema] Date range probe failed: "
                      f"{range_result.get('error', 'unknown')}")

        # ── Build schema text ──
        lines: list[str] = []
        if date_range_lines:
            lines.append("=== DATA DATE RANGE ===")
            lines.append("CRITICAL: anchor relative date filters (last N months, "
                         "YoY, this year) to MAX of these — NEVER hardcode years "
                         "from today. The model may not contain current data.")
            lines.extend(date_range_lines)
            lines.append("")
        lines.append("=== TABLES & COLUMNS ===")
        # Flat (table, column) index for one-pass lookup by the agent.
        column_index: list[str] = []

        for table in tables:
            cols = []
            for c in columns_by_table.get(table["name"], []):
                if c.get("isHidden"):
                    continue
                cname = c["name"]
                if str(cname).startswith("RowNumber"):
                    continue
                dtype = c.get("dataType", "")
                date_flag = (
                    f" [DATE — use YEAR('{table['name']}'[{cname}]) for filtering]"
                    if "Date" in str(dtype) else ""
                )
                cols.append(f"    {cname} ({dtype}){date_flag}")
                column_index.append(f"  {table['name']}[{cname}]")
            lines.append(f"\nTable: '{table['name']}'")
            if cols:
                lines.extend(cols)

        if measures_data.get("success"):
            lines.append("\n=== MEASURES ===")
            lines.append("Reference measures as [MeasureName]. The DAX shown after → "
                         "is each measure's definition — read it to understand grain "
                         "(e.g. is the source pbi_orders or pbi_order_items?) before "
                         "grouping by a dimension.")
            for m in measures_data.get("data", []):
                name = m.get("name", "")
                # Prefer the REST-fetched expression; fall back to whatever
                # measure_operations returned.
                expr = measure_expressions.get(name) or m.get("expression") or ""
                expr_one_line = " ".join(expr.split())  # collapse whitespace
                if len(expr_one_line) > 240:
                    expr_short = expr_one_line[:240].rstrip() + "…"
                else:
                    expr_short = expr_one_line
                if expr_short:
                    lines.append(f"  [{name}]  →  {expr_short}")
                else:
                    lines.append(f"  [{name}]")

        if rel_data.get("success") and rel_data.get("data"):
            lines.append("\n=== RELATIONSHIPS ===")
            for r in rel_data.get("data", []):
                from_t, to_t = r.get("fromTable"), r.get("toTable")
                # Drop auto-generated date hierarchy relationships — they're
                # noise that confuses the agent into joining fake date dims.
                if (from_t and from_t.startswith(hidden_prefixes)) or \
                   (to_t and to_t.startswith(hidden_prefixes)):
                    continue
                lines.append(
                    f"  '{from_t}[{r.get('fromColumn')}]' → "
                    f"'{to_t}[{r.get('toColumn')}]'"
                )

        if column_index:
            lines.append("\n=== COLUMN INDEX (table[column]) ===")
            lines.append("Use this to confirm which table holds a column before writing DAX.")
            lines.extend(column_index)

        return "\n".join(lines)

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

    async def _dax_via_rest_for_agent(self, payload: dict) -> dict:
        """Execute the agent's dax_query_operations Execute call via REST and
        return a response in the MCP-shaped dict the agent expects:
            {"success": bool, "data": {"rowCount": N, "rows": [...]},
             "operation": "Execute", "message": "..."}
        Errors surface as {"success": False, "message": "..."} so the existing
        tool-node error rewrite path still kicks in.
        """
        inner = (payload or {}).get("request", {}) or {}
        operation = inner.get("operation", "Execute")
        query = inner.get("query") or ""
        if operation != "Execute" or not query:
            return {
                "success": False,
                "message": f"REST DAX wrapper only supports Execute "
                           f"(got operation={operation!r}, query empty={not query}).",
            }
        result = await self._execute_dax_rest(query)
        if not result.get("success"):
            return {
                "success": False,
                "message": result.get("error") or "DAX execution failed",
                "operation": "Execute",
            }
        rows = result.get("rows", [])
        return {
            "success": True,
            "operation": "Execute",
            "data": {"rowCount": len(rows), "rows": rows},
        }

    def call_tool(self, tool_name, payload):
        # Route the agent's DAX execution through REST executeQueries —
        # avoids a buggy MCP build, returns documented JSON, and skips a
        # subprocess hop on every query. Other tool calls (and non-Execute
        # operations on dax_query_operations) still go through MCP.
        if tool_name == "dax_query_operations":
            inner = (payload or {}).get("request", {}) or {}
            if inner.get("operation", "Execute") == "Execute":
                future = asyncio.run_coroutine_threadsafe(
                    self._dax_via_rest_for_agent(payload), self._loop
                )
                return future.result(timeout=60)

        future = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(tool_name, payload), self._loop
        )
        return future.result(timeout=60)

    def build_schema(self):
        return self.run(self._build_schema_async())

    def disconnect(self):
        self.run(self._disconnect_async())
        self._loop.call_soon_threadsafe(self._loop.stop)