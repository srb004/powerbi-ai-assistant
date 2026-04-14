import concurrent.futures
import json
from typing import Annotated
import streamlit as st
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage
from langchain_core.tools import StructuredTool
from langchain_openai import AzureChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

SYSTEM_PROMPT = """You are a senior Power BI data analyst. Answer business questions by querying
the semantic model using DAX tools. NEVER ask the user for clarification — make reasonable
assumptions and run the query immediately.

SCHEMA:
{schema}

DAX RULES:
- Always start with EVALUATE
- Use SUMMARIZECOLUMNS for grouped results
- Use TOPN(N, <table_expr>, [Measure], DESC) to limit rows
- ONLY use measures, tables, and columns that appear in the SCHEMA above — never invent names
- Use [MeasureName] for measures — never recreate with SUM/COUNT if a measure exists
- Date columns are tagged [DATE] — filter using YEAR('Table'[col]) or MONTH('Table'[col])
- SUMMARIZECOLUMNS does NOT accept boolean filter arguments. For date filters use CALCULATE:
    CORRECT: "Revenue", CALCULATE([Measure], FILTER(ALL('Table'), YEAR('Table'[col]) = 2023))
    WRONG:   SUMMARIZECOLUMNS('T'[col], YEAR('T'[date]) = 2023, ...)
- For YoY use VAR to capture years then CALCULATE with FILTER(ALL(...)):
    VAR CurYear  = YEAR(MAX('Table'[date_col]))
    VAR PrevYear = CurYear - 1
    RETURN SUMMARIZECOLUMNS(...)
- When calling dax_query_operations always use lowercase key names: {{"request": {{"operation": "Execute", "query": "EVALUATE ..."}}}}
- Never use PascalCase keys like "Operation" or "Query" — always lowercase "operation" and "query"

VERIFIED DAX PATTERNS FOR THIS MODEL:
{dax_examples}

RESPONSE FORMAT:
- Write a clear business narrative answering the question
- For tabular results write exactly:
  RENDER_TABLE:
  [JSON array on next line]
- Add 2-3 sentences of insight after the table
- End with exactly 2 follow-up questions each prefixed with FOLLOW_UP:
"""

FEW_SHOT_GENERATOR_PROMPT = """You are a DAX expert. Generate exactly 4 working DAX queries for
the Power BI semantic model described in the schema below.

MANDATORY RULES — any violation makes the examples useless:
- ONLY use table names, column names, and measure names that appear VERBATIM in the schema
- Date columns are tagged with [DATE] — filter them with YEAR() or MONTH() inside CALCULATE
- Use existing measures with [MeasureName] — never write SUM/COUNT/AVERAGE to recreate them
- NEVER pass boolean expressions as arguments to SUMMARIZECOLUMNS
- Every query must begin with EVALUATE
- Pattern coverage required:
    1. ROW() — single scalar metric
    2. SUMMARIZECOLUMNS — grouped result with CALCULATE for filtering
    3. TOPN — top N rows by a measure
    4. YoY — VAR CurYear / PrevYear + CALCULATE + FILTER(ALL(...))
- Add one comment line above each query describing what business question it answers

SCHEMA:
{schema}

Output ONLY the 4 annotated DAX blocks. No markdown fences. No prose explanation."""


class MCPRequest(BaseModel):
    request: dict = Field(..., description="Request object for the MCP tool.")


def generate_dax_examples(llm, schema):
    try:
        prompt = FEW_SHOT_GENERATOR_PROMPT.replace("{schema}", schema)
        response = llm.invoke([HumanMessage(content=prompt)])
        examples = response.content.strip()

        bad_tokens = ["DateCol", "[date]", "Date_Col", "OrderDate", "SaleDate"]
        if any(tok.lower() in examples.lower() for tok in bad_tokens):
            fix_prompt = (
                "The DAX examples below contain column or table names that do NOT exist in the schema. "
                "Rewrite them using ONLY the names from the schema. Return only corrected DAX.\n\n"
                f"SCHEMA:\n{schema}\n\nDAX TO FIX:\n{examples}"
            )
            examples = llm.invoke([HumanMessage(content=fix_prompt)]).content.strip()

        return examples
    except Exception as e:
        return f"-- Example generation failed: {e}"


def build_langchain_tools(mcp_client):
    mcp_tools = mcp_client.list_tools()
    langchain_tools = []

    for t in mcp_tools:
        tool_name = t["name"]
        tool_desc = t.get("description") or f"MCP tool: {tool_name}"

        def make_fn(name):
            def fn(request):
                if isinstance(request, dict):
                    if "request" not in request and ("operation" in request or "Operation" in request):
                        request = {"request": request}
                    inner = request.get("request", {})
                    if isinstance(inner, dict):
                        normalized = {k[0].lower() + k[1:]: v for k, v in inner.items()}
                        request = {"request": normalized}
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        future = ex.submit(mcp_client.call_tool, name, {"request": request.get("request", request)})
                        result = future.result(timeout=60)
                    return result if isinstance(result, str) else json.dumps(result)
                except Exception as e:
                    return json.dumps({"error": str(e)})
            return fn

        langchain_tools.append(
            StructuredTool(
                name=tool_name,
                description=tool_desc,
                func=make_fn(tool_name),
                args_schema=MCPRequest,
            )
        )

    return langchain_tools

class AgentState(BaseModel):
    messages: Annotated[list, add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

def make_agent_node(llm_with_tools):
    def agent_node(state):
        response = llm_with_tools.invoke(state.messages)
        return {"messages": [response]}
    return agent_node


def make_tool_node(tools_by_name):
    def tool_node(state):
        last = state.messages[-1]
        tool_messages = []
        for tc in last.tool_calls:
            name = tc["name"]
            tool = tools_by_name.get(name)
            if tool is None:
                result = json.dumps({"error": f"Unknown tool: {name}. Available tools: {list(tools_by_name.keys())}"})
            else:
                try:
                    result = tool.invoke(tc["args"])
                except Exception as e:
                    result = json.dumps({"error": str(e)})

            try:
                parsed = json.loads(result) if isinstance(result, str) else result
                if isinstance(parsed, dict) and not parsed.get("success", True):
                    result = json.dumps({
                        "ERROR": parsed.get("message", "Query failed"),
                        "fix_hint": "Check column/table/measure names against the schema and retry with corrected DAX"
                    })
            except Exception:
                pass

            tool_messages.append(
                ToolMessage(
                    content=str(result),
                    tool_call_id=tc["id"],
                    name=name,          
                )
            )
        return {"messages": tool_messages}
    return tool_node


def should_continue(state):
    last = state.messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


class LLMAgent:
    def __init__(self, endpoint, api_key, deployment, api_version, mcp_client, schema,
                 max_iterations=8, dax_cache=None, cache_key=None):
        self.mcp_client = mcp_client
        self.max_iterations = max_iterations

        self.llm = AzureChatOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            azure_deployment=deployment,
            api_version=api_version,
            reasoning_effort='medium',
            max_completion_tokens=2000,
            streaming=True,
        )

        self.tools = build_langchain_tools(mcp_client)
        tools_by_name = {t.name: t for t in self.tools}
        llm_with_tools = self.llm.bind_tools(self.tools)

        graph = StateGraph(AgentState)
        graph.add_node("agent", make_agent_node(llm_with_tools))
        graph.add_node("tools", make_tool_node(tools_by_name))
        graph.set_entry_point("agent")
        graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
        graph.add_edge("tools", "agent")
        self.graph = graph.compile()

        # Use cached examples if available for this model, else generate and cache
        if dax_cache is not None and cache_key and cache_key in dax_cache:
            dax_examples = dax_cache[cache_key]
            print(f"Using cached DAX examples for: {cache_key}")
        else:
            print("Generating DAX examples for this model...")
            dax_examples = generate_dax_examples(self.llm, schema)
            print(f"DAX examples:\n{dax_examples}\n")
            if dax_cache is not None and cache_key:
                dax_cache[cache_key] = dax_examples

        self.system_prompt = (
            SYSTEM_PROMPT
            .replace("{schema}", schema)
            .replace("{dax_examples}", dax_examples)
        )

    def run(self, user_message, chat_history, schema=None, status_container=None, stream_placeholder=None):
        history_lines = []
        for turn in chat_history[-6:]:
            if turn["role"] in ("user", "assistant"):
                prefix = "User" if turn["role"] == "user" else "Assistant"
                content = turn["content"][:400] if turn["role"] == "assistant" else turn["content"]
                history_lines.append(f"{prefix}: {content}")

        full_question = user_message
        if history_lines:
            full_question = (
                "[Conversation so far]\n"
                + "\n".join(history_lines)
                + f"\n\n[New question]\n{user_message}"
            )

        init_messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=full_question),
        ]

        tool_log = []
        answer = ""
        step_num = 0

        try:
            for event in self.graph.stream(
                {"messages": init_messages},
                config={"recursion_limit": self.max_iterations * 2},
                stream_mode="values",
            ):
                last_msg = event["messages"][-1]

                if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
                    for tc in last_msg.tool_calls:
                        step_num += 1
                        dax = _extract_dax(tc.get("args", {}))
                        if status_container:
                            with status_container:
                                st.markdown(f"**Step {step_num}** — calling `{tc['name']}`…")
                                if dax:
                                    st.code(dax, language="sql")

                elif isinstance(last_msg, ToolMessage):
                    content_str = str(last_msg.content)
                    tool_name = last_msg.name or "tool"

                    try:
                        result_json = json.loads(content_str)
                        if "ERROR" in result_json:
                            display = f"{result_json['ERROR'][:150]}"
                        elif not result_json.get("success", True):
                            display = f"{result_json.get('message', 'failed')[:150]}"
                        else:
                            rows = result_json.get("data", {})
                            row_count = rows.get("rowCount", "?") if isinstance(rows, dict) else "?"
                            display = f"{row_count} rows"
                    except Exception:
                        display = f"{len(content_str)} chars"

                    tool_log.append({"tool": tool_name, "result": content_str[:600]})
                    if status_container:
                        with status_container:
                            st.caption(f"`{tool_name}` → {display}")

                elif isinstance(last_msg, AIMessage) and not last_msg.tool_calls:
                    answer = last_msg.content or ""

        except Exception as e:
            return f"Agent error: {e}", tool_log

        if status_container:
            status_container.update(
                label=f"Done — {step_num} tool call(s)",
                state="complete",
                expanded=False,
            )

        return answer, tool_log


def _extract_dax(tool_args):
    if isinstance(tool_args, dict):
        request = tool_args.get("request", {})
        if isinstance(request, dict):
            q = request.get("query", "")
            if q and "EVALUATE" in q.upper():
                return q[:500]
    return ""