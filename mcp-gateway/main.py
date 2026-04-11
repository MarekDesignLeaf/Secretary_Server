import os
import httpx
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.getenv("API_BASE_URL", "https://web-production-4b451.up.railway.app").rstrip("/")

mcp = FastMCP("Secretary MCP", stateless_http=True)

@mcp.tool()
async def get_clients():
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API_BASE_URL}/crm/clients")
        r.raise_for_status()
        return r.json()

app = mcp.streamable_http_app()
