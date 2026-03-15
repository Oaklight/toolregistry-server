"""
toolregistry-server: Server library for ToolRegistry.

This package provides protocol adapters for exposing ToolRegistry tools
as services via OpenAPI (REST) and MCP (Model Context Protocol).

Main Components:
    - RouteTable: Central routing layer that bridges ToolRegistry and protocol adapters
    - openapi: OpenAPI/REST adapter using FastAPI
    - mcp: MCP adapter for LLM integration
    - auth: Authentication utilities
    - cli: Command-line interface

Example:
    ```python
    from toolregistry import ToolRegistry
    from toolregistry_server import RouteTable

    registry = ToolRegistry()
    route_table = RouteTable(registry)
    for route in route_table.list_routes():
        print(route.path)
    ```
"""

__version__ = "0.1.0"

from .route_table import RouteEntry, RouteTable

__all__ = [
    "__version__",
    "RouteTable",
    "RouteEntry",
]
