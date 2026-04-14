import json
import os

from dotenv import load_dotenv
load_dotenv()

from mcp_client import PowerBIMCPClient

WORKSPACE = "PBI_AIBI_Migration"
MODEL     = "Toy Factory Sales Report_v2"

client = PowerBIMCPClient(
    tenant_id=os.getenv("TENANT_ID"),
    client_id=os.getenv("CLIENT_ID"),
    client_secret=os.getenv("CLIENT_SECRET"),
    mcp_exe_path=os.getenv("MCP_EXE_PATH"),
)

print("Starting session...")
client.start_session()

print("Connecting...")
client.connect(WORKSPACE, MODEL)
print("Connected.\n")

# All calls below are from the main thread — same as how Streamlit calls them
# No asyncio.run_coroutine_threadsafe wrapping needed here

print("=" * 60)
print("TEST 3: call_tool() sync wrapper from main thread")
result = client.call_tool(
    "dax_query_operations",
    {"request": {"operation": "Execute", "query": "EVALUATE ROW(\"Test\", 1)"}}
)
print(f"Result: {result}\n")

print("=" * 60)
print("TEST 4: Real DAX query — list products with sales")
result2 = client.call_tool(
    "dax_query_operations",
    {
        "request": {
            "operation": "Execute",
            "query": """EVALUATE
TOPN(
    5,
    SUMMARIZECOLUMNS(
        'products'[name],
        \"Sales\", SUM('order_items'[sale_price])
    ),
    [Sales],
    DESC
)"""
        }
    }
)
print(f"Result: {json.dumps(result2)[:600]}\n")

print("=" * 60)
print("TEST 5: Simulate exactly what LangGraph tool fn does")
import concurrent.futures

def simulate_langgraph_tool_call(tool_input_str):
    tool_input = json.loads(tool_input_str)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(client.call_tool, "dax_query_operations", tool_input)
        return future.result(timeout=60)

agent_input = json.dumps({
    "request": {
        "operation": "Execute",
        "query": "EVALUATE ROW(\"AgentTest\", 42)"
    }
})
result3 = simulate_langgraph_tool_call(agent_input)
print(f"Result: {result3}")