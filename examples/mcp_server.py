"""Minimal MCP stdio server example.

Run:
    python examples/mcp_server.py

This starts an MCP server over stdio, suitable for use as a subprocess
by MCP-compatible clients (e.g., Claude Desktop, Claude Code).
"""

import asyncio

from toolregistry import ToolRegistry
from tools import add, greet, multiply

from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp import MCPAdapter, run_stdio

# 1. Create registry and register tools
registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

# 2. Build route table
route_table = RouteTable(registry)

# 3. Create MCP adapter and run over stdio
adapter = MCPAdapter(route_table)

if __name__ == "__main__":
    asyncio.run(run_stdio(adapter.server))
