"""
toolregistry-server: Define custom tools and serve them via OpenAPI or MCP.

This package lets you register Python functions as tools and expose them
as services via OpenAPI (REST) and MCP (Model Context Protocol) interfaces.

Main Components:
    - RouteTable: Central routing layer that bridges ToolRegistry and protocol adapters
    - registry_builder: Config loading, source registration, profile filtering
    - adapters.openapi: OpenAPI/REST adapter using FastAPI
    - adapters.mcp: MCP adapter for LLM integration
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

__version__ = "0.3.3"

from .app import App, serve_mcp, serve_openapi
from .auth import load_tokens
from .registry_builder import (
    PROFILE_DISABLE_TAGS,
    apply_config,
    apply_profile,
    load_config,
    register_mcp_source,
    register_openapi_source,
    register_python_source,
    registry_from_config,
)
from .route_table import RouteEntry, RouteTable
from .session import SessionContext

__all__ = [
    "App",
    "__version__",
    "PROFILE_DISABLE_TAGS",
    "RouteEntry",
    "RouteTable",
    "SessionContext",
    "apply_config",
    "apply_profile",
    "load_config",
    "load_tokens",
    "register_mcp_source",
    "register_openapi_source",
    "register_python_source",
    "registry_from_config",
    "serve_mcp",
    "serve_openapi",
]
