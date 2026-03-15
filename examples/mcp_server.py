"""Minimal MCP stdio server example.

Run:
    python examples/mcp_server.py

This starts an MCP server over stdio, suitable for use as a subprocess
by MCP-compatible clients (e.g., Claude Desktop, Claude Code).
"""

import asyncio

from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.mcp import create_mcp_server, run_stdio

from tools import add, greet, multiply

# 1. Create registry and register tools
registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

# 2. Build route table
route_table = RouteTable(registry)

# 3. Create MCP server and run over stdio
server = create_mcp_server(route_table)

if __name__ == "__main__":
    asyncio.run(run_stdio(server))
