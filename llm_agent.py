import json
import os
from typing import Annotated
import httpx
import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage
from langchain_core.tools import StructuredTool
from langchain_openai import AzureChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

load_dotenv()

MAX_TOOL_CALLS = 4

SYSTEM_PROMPT = """You are a senior Power BI data analyst. Answer business questions by querying
the semantic model using DAX tools.

RULES:
- NEVER ask the user for clarification — make reasonable assumptions and query immediately
- Call dax_query_operations at most {max_tool_calls} times per question
- Once you have data rows returned, STOP calling tools and write your answer immediately
- If a query fails or returns 0 rows, try ONE simpler alternative then stop
- NEVER keep retrying the same question repeatedly

SCHEMA:
{schema}

DAX SYNTAX RULES — violations cause query failures or bad data:

1. VAR must be INSIDE EVALUATE, not before it:
   CORRECT:  EVALUATE VAR x = 1 RETURN ROW("v", x)
   WRONG:    VAR x = 1\nEVALUATE ...

2. SUMMARIZECOLUMNS does NOT accept boolean filter arguments:
   CORRECT:  CALCULATE([M], FILTER(ALL('T'), YEAR('T'[d]) = 2023))
   WRONG:    SUMMARIZECOLUMNS('T'[col], YEAR('T'[d]) = 2023, ...)

3. Never use DATESINPERIOD — it fails on non-date-dimension tables.
   Use YEAR()/MONTH() filters inside CALCULATE+FILTER(ALL(...)) instead.

4. NEVER group by both product_id AND product_name in the same SUMMARIZECOLUMNS.
   They are from different tables and produce a cross-join of 4×4=16 rows.
   CORRECT:  SUMMARIZECOLUMNS('pbi_products'[product_name], "Score", [Fulfillment Score])
   WRONG:    SUMMARIZECOLUMNS('pbi_order_items'[product_id], 'pbi_products'[product_name], ...)

5. For "last N months" — use YEAR and MONTH arithmetic with DATE():
   EVALUATE
   ROW(
       "Net Profit Last 6M",
       CALCULATE(
           [Net Profit],
           FILTER(ALL('pbi_orders'), 'pbi_orders'[created_at] >= DATE(2025, 10, 1))
       )
   )

6. For simple scalar results use ROW():
   EVALUATE ROW("Net Profit", [Net Profit])

7. Only use table/column/measure names VERBATIM from the schema.
   Use [MeasureName] for existing measures — never recreate with SUM/COUNT.

8. Always use lowercase keys in tool calls:
   {{"request": {{"operation": "Execute", "query": "EVALUATE ..."}}}}

VERIFIED DAX PATTERNS FOR THIS MODEL:
{dax_examples}

RESPONSE FORMAT:
1. One short intro sentence.
2. For tabular data:
   RENDER_TABLE:
   [JSON array — double-quoted keys, no trailing commas]
3. 2-3 sentences of insight.
4. Exactly 2 follow-ups:
   FOLLOW_UP: <question>
   FOLLOW_UP: <question>
"""

FEW_SHOT_GENERATOR_PROMPT = """Write 2 short DAX queries for this Power BI model.
Rules: use ONLY names from schema, start with EVALUATE, VAR must be INSIDE EVALUATE not before it,
use [MeasureName] for measures, no DATESINPERIOD, never group by both product_id and product_name.

1. EVALUATE ROW(...) — single scalar measure
2. EVALUATE SUMMARIZECOLUMNS(...) — grouped by ONE dimension column only

SCHEMA:
{schema}

Output ONLY the 2 DAX blocks. No markdown."""

def _call_azure_openai_direct(
    endpoint: str, api_key: str, deployment: str, api_version: str,
    prompt: str, max_tokens: int = 600, timeout: int = 20,
) -> str:
    url = (
        f"{endpoint.rstrip('/')}/openai/deployments/{deployment}"
        f"/chat/completions?api-version={api_version}"
    )
    try:
        resp = httpx.post(
            url,
            json={"messages": [{"role": "user", "content": prompt}],
                  "max_completion_tokens": max_tokens},
            headers={"api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            print(f"[LLMAgent] DAX gen HTTP {resp.status_code}: {resp.text[:200]}")
            return ""
        data = resp.json()
        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})
        print(
            f"[LLMAgent] DAX gen: finish={choice.get('finish_reason')} "
            f"prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')}"
        )
        return (choice.get("message", {}).get("content") or "").strip()
    except Exception as e:
        print(f"[LLMAgent] DAX gen skipped: {e}")
        return ""

def _try_generate_dax_examples(
    endpoint: str, api_key: str, deployment: str, api_version: str, schema: str
) -> str:
    prompt = FEW_SHOT_GENERATOR_PROMPT.replace("{schema}", schema[:1200])
    return _call_azure_openai_direct(
        endpoint, api_key, deployment, api_version, prompt,
        max_tokens=4000, timeout=90
    )

class MCPRequest(BaseModel):
    request: dict = Field(..., description="Request object for the MCP tool.")

def build_langchain_tools(mcp_client) -> list[StructuredTool]:
    langchain_tools: list[StructuredTool] = []
    for t in mcp_client.list_tools():
        tool_name = t["name"]
        tool_desc = t.get("description") or f"MCP tool: {tool_name}"

        def make_fn(name: str):
            def fn(request):
                if isinstance(request, dict):
                    if "request" not in request and (
                        "operation" in request or "Operation" in request
                    ):
                        request = {"request": request}
                    inner = request.get("request", {})
                    if isinstance(inner, dict):
                        request = {"request": {k[0].lower() + k[1:]: v
                                               for k, v in inner.items()}}
                try:
                    result = mcp_client.call_tool(
                        name, {"request": request.get("request", request)}
                    )
                    return result if isinstance(result, str) else json.dumps(result)
                except Exception as e:
                    return json.dumps({"error": str(e)})
            return fn

        langchain_tools.append(StructuredTool(
            name=tool_name, description=tool_desc,
            func=make_fn(tool_name), args_schema=MCPRequest,
        ))
    return langchain_tools

class AgentState(BaseModel):
    messages: Annotated[list, add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


def make_agent_node(llm_with_tools):
    def agent_node(state: AgentState):
        return {"messages": [llm_with_tools.invoke(state.messages)]}
    return agent_node


def make_tool_node(tools_by_name: dict):
    def tool_node(state: AgentState):
        last = state.messages[-1]
        tool_messages: list[ToolMessage] = []
        for tc in last.tool_calls:
            tool = tools_by_name.get(tc["name"])
            if tool is None:
                result = json.dumps({"error": f"Unknown tool: {tc['name']}"})
            else:
                try:
                    result = tool.invoke(tc["args"])
                except Exception as e:
                    result = json.dumps({"error": str(e)})

            try:
                parsed = json.loads(result) if isinstance(result, str) else result
                if isinstance(parsed, dict) and not parsed.get("success", True):
                    result = json.dumps({
                        "ERROR": parsed.get("message", "Query failed")[:300],
                        "fix_hint": "Check names against schema; fix syntax and retry once",
                    })
            except Exception:
                pass

            tool_messages.append(ToolMessage(
                content=str(result), tool_call_id=tc["id"], name=tc["name"]
            ))
        return {"messages": tool_messages}
    return tool_node


def make_should_continue(max_calls: int):
    def should_continue(state: AgentState):
        last = state.messages[-1]
        if not (isinstance(last, AIMessage) and last.tool_calls):
            return END
        completed = sum(1 for m in state.messages if isinstance(m, ToolMessage))
        if completed >= max_calls:
            print(f"[LLMAgent] Hard stop: {max_calls} tool calls reached")
            return END
        return "tools"
    return should_continue

class LLMAgent:
    def __init__(
        self,
        endpoint: str, api_key: str, deployment: str, api_version: str,
        mcp_client, schema: str,
        max_iterations: int = 8,
        dax_cache: dict | None = None,
        cache_key: str | None = None,
    ):
        self.mcp_client = mcp_client
        self.max_iterations = max_iterations

        self.llm = AzureChatOpenAI(
            azure_endpoint=endpoint, api_key=api_key,
            azure_deployment=deployment, api_version=api_version,
            max_completion_tokens=8000, streaming=True, request_timeout=60,
        )

        self.tools = build_langchain_tools(mcp_client)
        tools_by_name = {t.name: t for t in self.tools}
        llm_with_tools = self.llm.bind_tools(self.tools)

        graph = StateGraph(AgentState)
        graph.add_node("agent", make_agent_node(llm_with_tools))
        graph.add_node("tools", make_tool_node(tools_by_name))
        graph.set_entry_point("agent")
        graph.add_conditional_edges(
            "agent",
            make_should_continue(MAX_TOOL_CALLS),
            {"tools": "tools", END: END},
        )
        graph.add_edge("tools", "agent")
        self.graph = graph.compile()
        self._recursion_limit = (MAX_TOOL_CALLS * 2) + 4

        if dax_cache is not None and cache_key and cache_key in dax_cache:
            dax_examples = dax_cache[cache_key]
            print(f"[LLMAgent] Cached DAX examples: {cache_key}")
        else:
            print("[LLMAgent] Generating DAX examples (20s timeout)…")
            dax_examples = _try_generate_dax_examples(
                endpoint, api_key, deployment, api_version, schema
            )
            if dax_examples:
                print(f"[LLMAgent] DAX examples OK ({len(dax_examples)} chars)")
                if dax_cache is not None and cache_key:
                    dax_cache[cache_key] = dax_examples
            else:
                dax_examples = ""
                print("[LLMAgent] DAX examples skipped — schema-only mode")

        base = SYSTEM_PROMPT.replace("{max_tool_calls}", str(MAX_TOOL_CALLS))
        base = base.replace("{schema}", schema)
        base = base.replace("{dax_examples}", dax_examples if dax_examples
                            else "-- None available; use schema above")
        self.system_prompt = base

    def run(
        self,
        user_message: str,
        chat_history: list[dict],
        schema: str | None = None,
        status_container=None,
        stream_placeholder=None,
    ) -> tuple[str, list[dict]]:

        history_lines: list[str] = []
        for turn in chat_history[-4:]:
            role = turn.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = turn["content"][:200] if role == "assistant" else turn["content"]
            history_lines.append(f"{'User' if role=='user' else 'Assistant'}: {content}")

        full_question = user_message
        if history_lines:
            full_question = (
                "[Conversation so far]\n" + "\n".join(history_lines)
                + f"\n\n[New question]\n{user_message}"
            )

        init_messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=full_question),
        ]

        tool_log: list[dict] = []
        answer = ""
        step_num = 0

        try:
            for event in self.graph.stream(
                {"messages": init_messages},
                config={"recursion_limit": self._recursion_limit},
                stream_mode="updates",
            ):
                for node_name, node_output in event.items():
                    msgs = node_output.get("messages", [])
                    if not msgs:
                        continue
                    last_msg = msgs[-1]

                    if (node_name == "agent"
                            and isinstance(last_msg, AIMessage)
                            and last_msg.tool_calls):
                        for tc in last_msg.tool_calls:
                            step_num += 1
                            dax = _extract_dax(tc.get("args", {}))
                            if status_container:
                                with status_container:
                                    st.markdown(f"**Step {step_num}** — `{tc['name']}`")
                                    if dax:
                                        st.code(dax, language="sql")

                    elif node_name == "tools":
                        for msg in msgs:
                            if not isinstance(msg, ToolMessage):
                                continue
                            content_str = str(msg.content)
                            tool_name = msg.name or "tool"
                            try:
                                rj = json.loads(content_str)
                                if "ERROR" in rj:
                                    display = rj["ERROR"][:120]
                                elif not rj.get("success", True):
                                    display = rj.get("message", "failed")[:120]
                                else:
                                    rows = rj.get("data", {})
                                    display = f"{rows.get('rowCount','?') if isinstance(rows,dict) else '?'} rows"
                            except Exception:
                                display = f"{len(content_str)} chars"

                            tool_log.append({"tool": tool_name, "result": content_str[:600]})
                            if status_container:
                                with status_container:
                                    st.caption(f"`{tool_name}` → {display}")

                    elif (node_name == "agent"
                          and isinstance(last_msg, AIMessage)
                          and not last_msg.tool_calls):
                        answer = last_msg.content or ""

        except Exception as e:
            if not answer:
                return f"Agent error: {e}", tool_log

        if status_container:
            status_container.update(
                label=f"Done — {step_num} tool call(s)",
                state="complete", expanded=False,
            )

        return answer, tool_log


def _extract_dax(tool_args: dict) -> str:
    if isinstance(tool_args, dict):
        q = tool_args.get("request", {}).get("query", "")
        if q and "EVALUATE" in q.upper():
            return q[:500]
    return ""