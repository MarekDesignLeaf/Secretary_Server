import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.getenv("API_BASE_URL", "https://web-production-4b451.up.railway.app").rstrip("/")

app = FastAPI(title="Secretary MCP")
mcp = FastMCP("Secretary MCP", stateless_http=True)

@mcp.tool()
async def get_clients():
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API_BASE_URL}/crm/clients")
        r.raise_for_status()
        return r.json()

mcp_app = mcp.streamable_http_app()
app.mount("/mcp", mcp_app)

@app.get("/")
async def root():
    return {
        "ok": True,
        "mcp": "/mcp",
        "api_base_url": API_BASE_URL
    }

@app.api_route("/mcp-test", methods=["GET", "POST"])
async def mcp_test(request: Request):
    return JSONResponse({
        "ok": True,
        "path": str(request.url.path),
        "method": request.method
    })
