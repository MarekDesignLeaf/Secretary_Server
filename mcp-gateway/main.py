from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

app = FastAPI()
mcp = FastMCP("Secretary MCP", stateless_http=True)

@mcp.tool()
async def ping():
    return {"ok": True, "message": "pong"}

@app.get("/")
async def root():
    return {"ok": True, "service": "Secretary MCP", "mcp": "/mcp"}

@app.get("/health")
async def health():
    return {"ok": True}

app.mount("/mcp", mcp.streamable_http_app())
