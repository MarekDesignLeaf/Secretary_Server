import os
import httpx
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.getenv("API_BASE_URL", "https://web-production-4b451.up.railway.app").rstrip("/")

app = FastAPI()
mcp = FastMCP("Secretary MCP", stateless_http=True)

@mcp.tool()
async def get_clients():
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API_BASE_URL}/crm/clients")
        r.raise_for_status()
        return r.json()

@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "Secretary MCP",
        "mcp": "/mcp"
    }

@app.get("/health")
async def health():
    return {"ok": True}

app.mount("/mcp", mcp.streamable_http_app())
