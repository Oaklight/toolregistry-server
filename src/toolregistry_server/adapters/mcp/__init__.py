"""MCP (Model Context Protocol) adapter for ToolRegistry.

This module provides functionality to expose ToolRegistry tools via
the Model Context Protocol for LLM integration.

Main Components:
    - create_mcp_server: Create an MCP server from a RouteTable
    - run_mcp_server: Start an MCP server (registry + transport)
    - run_stdio / run_sse / run_streamable_http: Transport runners

Example:
    ```python
    import asyncio
    from toolregistry import ToolRegistry
    from toolregistry_server import RouteTable
    from toolregistry_server.adapters.mcp import create_mcp_server, run_stdio

    registry = ToolRegistry()
    route_table = RouteTable(registry)
    server = create_mcp_server(route_table)
    asyncio.run(run_stdio(server))
    ```

Note:
    This module requires the 'mcp' extra to be installed:
    pip install toolregistry-server[mcp]
"""

import asyncio
import os
from typing import TYPE_CHECKING

from ..._vendor.structlog import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server
    from toolregistry import ToolRegistry
    from toolregistry.config import ToolConfig

    from ...route_table import RouteTable


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_mcp_server(
    route_table: "RouteTable",
    name: str = "ToolRegistry-Server",
) -> "Server":
    """Create an MCP server from a RouteTable.

    This is an alias for route_table_to_mcp_server() for convenience.

    Args:
        route_table: The RouteTable to expose.
        name: Server name for MCP identification.

    Returns:
        A configured MCP Server instance.

    Raises:
        ImportError: If MCP SDK is not installed.
    """
    from .adapter import route_table_to_mcp_server

    return route_table_to_mcp_server(route_table, name)


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
# Token collection
# ---------------------------------------------------------------------------


def _collect_bearer_tokens(tokens_path: str | None = None) -> set[str] | None:
    """Collect Bearer tokens from env var and/or file.

    Reads tokens from:
    1. ``API_BEARER_TOKEN`` env var (single or comma-separated)
    2. ``tokens_path`` file (one token per line)

    Args:
        tokens_path: Optional path to a tokens file.

    Returns:
        Set of tokens, or None if no tokens configured.
    """
    tokens: set[str] = set()

    env_val = os.environ.get("API_BEARER_TOKEN", "").strip()
    if env_val:
        tokens.update(t.strip() for t in env_val.split(",") if t.strip())

    if tokens_path:
        try:
            with open(tokens_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        tokens.add(line)
        except OSError as e:
            logger.error(f"Failed to read tokens file {tokens_path}: {e}")

    return tokens if tokens else None


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------


def run_mcp_server(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    config_path: str | None = None,
    registry: "ToolRegistry | None" = None,
    profile: str | None = None,
    tokens_path: str | None = None,
    server_url: str | None = None,
) -> None:
    """Start the MCP server.

    Args:
        transport: Transport type: stdio, sse, or streamable-http.
        host: Host for SSE/HTTP transport.
        port: Port for SSE/HTTP transport.
        config_path: Path to configuration file.
        registry: Pre-built ToolRegistry to use directly. When provided,
            ``config_path`` is ignored and ``registry_from_config``
            is skipped.
        profile: Deployment profile for tag-based tool filtering.
            ``"remote"`` disables tools tagged ``file_system``, ``destructive``,
            or ``privileged``. ``"local"`` disables tools tagged ``network``.
            ``None`` (default) skips profile filtering entirely.
        tokens_path: Path to a file containing Bearer tokens (one per line).
            Also reads ``API_BEARER_TOKEN`` env var. Only used with
            ``streamable-http`` transport.
        server_url: Public URL of this server for auth metadata generation.

    Raises:
        ImportError: If MCP SDK is not installed.
        ValueError: If an unknown transport type is specified.
    """
    from ...registry_builder import apply_profile, load_config, registry_from_config
    from ...route_table import RouteTable

    config: ToolConfig | None = None
    if registry is None:
        config = load_config(config_path)
        if config:
            registry = registry_from_config(config)
        else:
            from toolregistry import ToolRegistry

            registry = ToolRegistry()

    if profile is not None:
        apply_profile(registry, profile, config=config)

    # Create route table
    route_table = RouteTable(registry)

    # Create MCP server
    mcp_server = route_table_to_mcp_server(route_table)

    # Log startup info
    logger.info(f"Starting MCP server with {transport} transport")
    logger.info(f"Registered {len(route_table.list_routes())} tool(s)")

    # Run the appropriate transport
    if transport == "stdio":
        asyncio.run(run_stdio(mcp_server))
    elif transport == "sse":
        logger.info(f"SSE endpoint: http://{host}:{port}/sse")
        asyncio.run(run_sse(mcp_server, host=host, port=port))
    elif transport == "streamable-http":
        logger.info(f"HTTP endpoint: http://{host}:{port}/mcp")
        valid_tokens = _collect_bearer_tokens(tokens_path)
        asyncio.run(
            run_streamable_http(
                mcp_server,
                host=host,
                port=port,
                valid_tokens=valid_tokens,
                server_url=server_url,
            )
        )
    else:
        raise ValueError(f"Unknown transport type: {transport}")


__all__ = [
    "create_mcp_server",
    "route_table_to_mcp_server",
    "run_mcp_server",
    "run_stdio",
    "run_sse",
    "run_streamable_http",
]
