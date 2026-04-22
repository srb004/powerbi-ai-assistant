import asyncio
import json
import os
from dotenv import load_dotenv
load_dotenv()

from azure.identity import ClientSecretCredential
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

MCP_EXE = os.getenv("MCP_EXE_PATH")
WORKSPACE = "PBI_AIBI_Migration"
MODEL = "Toy Factory Sales Report Coe"

def get_token():
    cred = ClientSecretCredential(
        os.getenv("TENANT_ID"),
        os.getenv("CLIENT_ID"),
        os.getenv("CLIENT_SECRET"),
    )
    return cred.get_token("https://analysis.windows.net/powerbi/api/.default").token

async def main():
    token = get_token()
    print(f"Token (first 40 chars): {token[:40]}")

    # Set env explicitly and verify
    env = {**os.environ, "PBI_MODELING_MCP_ACCESS_TOKEN": token}
    print(f"Env token set: {env.get('PBI_MODELING_MCP_ACCESS_TOKEN', 'NOT SET')[:40]}")

    params = StdioServerParameters(
        command=MCP_EXE,
        args=["--start", "--readonly"],
        env=env,
    )

    print("Starting MCP session...")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Session initialized. Attempting ConnectFabric...")

            result = await session.call_tool(
                "connection_operations",
                {
                    "request": {
                        "operation": "ConnectFabric",
                        "workspaceName": WORKSPACE,
                        "semanticModelName": MODEL,
                        "clearCredential": False,
                    }
                },
            )
            resp = json.loads(result.content[0].text)
            print(f"ConnectFabric result: {json.dumps(resp, indent=2)}")

asyncio.run(main()) 