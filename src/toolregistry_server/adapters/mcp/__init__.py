"""MCP (Model Context Protocol) adapter for ToolRegistry.

This module provides functionality to expose ToolRegistry tools via
the Model Context Protocol for LLM integration.

Main Components:
    - MCPAdapter: Adapter class for serving tools via MCP
    - route_table_to_mcp_server: Create an MCP server from a RouteTable
    - run_stdio / run_sse / run_streamable_http: Transport runners

Example:
    ```python
    from toolregistry import ToolRegistry
    from toolregistry_server import RouteTable
    from toolregistry_server.adapters.mcp import MCPAdapter

    registry = ToolRegistry()
    route_table = RouteTable(registry)
    adapter = MCPAdapter(route_table)
    adapter.serve(host="127.0.0.1", port=8000, transport="stdio")
    ```

Note:
    This module requires the 'mcp' extra to be installed:
    pip install toolregistry-server[mcp]
"""

import asyncio
from typing import TYPE_CHECKING

from ..._vendor.structlog import get_logger
from .. import Adapter

logger = get_logger()

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

    from ...route_table import RouteTable


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def route_table_to_mcp_server(
    route_table: "RouteTable",
    name: str = "ToolRegistry-Server",
) -> "Server":
    """Create an MCP low-level server from a RouteTable.

    Registers list_tools and call_tool handlers that read directly
    from the route table, ensuring enable/disable state is always
    in sync (no drift).

    Args:
        route_table: The RouteTable to convert.
        name: Server name for MCP identification.

    Returns:
        A configured MCP Server instance.

    Raises:
        ImportError: If MCP SDK is not installed.
    """
    from .adapter import route_table_to_mcp_server as _route_table_to_mcp_server

    return _route_table_to_mcp_server(route_table, name)


# ---------------------------------------------------------------------------
# Transport runners
# ---------------------------------------------------------------------------


async def run_stdio(server: "Server") -> None:
    """Run an MCP server over stdio transport.

    Args:
        server: The MCP Server instance to run.
    """
    from .server import run_stdio as _run_stdio

    await _run_stdio(server)


async def run_sse(
    server: "Server",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/sse",
) -> None:
    """Run an MCP server over SSE (Server-Sent Events) transport.

    Args:
        server: The MCP Server instance to run.
        host: Host address to bind to.
        port: Port number to bind to.
        path: URL path for the SSE endpoint.
    """
    from .server import run_sse as _run_sse

    await _run_sse(server, host, port, path)


async def run_streamable_http(
    server: "Server",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
    valid_tokens: set[str] | None = None,
    server_url: str | None = None,
) -> None:
    """Run an MCP server over streamable HTTP transport.

    Args:
        server: The MCP Server instance to run.
        host: Host address to bind to.
        port: Port number to bind to.
        path: URL path for the MCP endpoint.
        valid_tokens: Optional set of accepted Bearer tokens.
        server_url: Public URL of this server for metadata generation.
    """
    from .server import run_streamable_http as _run_streamable_http

    await _run_streamable_http(
        server, host, port, path, valid_tokens=valid_tokens, server_url=server_url
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class MCPAdapter(Adapter):
    """Serve tools via the Model Context Protocol.

    Args:
        route_table: The RouteTable to expose.
        name: Server name for MCP identification.
    """

    def __init__(
        self,
        route_table: "RouteTable",
        *,
        name: str = "ToolRegistry-Server",
    ) -> None:
        super().__init__(route_table)
        self._server = route_table_to_mcp_server(route_table, name)

    @property
    def server(self) -> "Server":
        """The MCP Server instance."""
        return self._server

    def serve(self, *, host: str = "127.0.0.1", port: int = 8000, **kwargs) -> None:
        """Start the MCP server.

        Args:
            host: Host address to bind to.
            port: Port number to bind to.
            transport: MCP transport type (default: ``"stdio"``).
                One of ``"stdio"``, ``"sse"``, ``"streamable-http"``,
                or ``"http"`` (alias for ``"streamable-http"``).
            tokens_path: Path to Bearer token file (streamable-http only).
            server_url: Public server URL (streamable-http only).
        """
        transport = kwargs.get("transport", "stdio")
        if transport == "http":
            transport = "streamable-http"
        tokens_path = kwargs.get("tokens_path")
        server_url = kwargs.get("server_url")

        logger.info(f"Starting MCP server with {transport} transport")
        logger.info(f"Registered {len(self._route_table.list_routes())} tool(s)")

        if transport == "stdio":
            asyncio.run(run_stdio(self._server))
        elif transport == "sse":
            logger.info(f"SSE endpoint: http://{host}:{port}/sse")
            asyncio.run(run_sse(self._server, host=host, port=port))
        elif transport == "streamable-http":
            logger.info(f"HTTP endpoint: http://{host}:{port}/mcp")
            from ...auth import load_tokens

            valid_tokens = set(load_tokens(tokens_path)) or None
            asyncio.run(
                run_streamable_http(
                    self._server,
                    host=host,
                    port=port,
                    valid_tokens=valid_tokens,
                    server_url=server_url,
                )
            )
        else:
            raise ValueError(f"Unknown transport type: {transport}")


__all__ = [
    "MCPAdapter",
    "route_table_to_mcp_server",
    "run_stdio",
    "run_sse",
    "run_streamable_http",
]
