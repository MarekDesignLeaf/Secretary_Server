import httpx
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Secretary MCP")

@mcp.tool()
async def ping():
    return {"ok": True, "message": "pong"}

# SSE app exposes /sse and /messages
app = mcp.sse_app()
