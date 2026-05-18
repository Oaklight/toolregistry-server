"""
MCP server startup module.

This module provides functions to start an MCP server from the CLI.
"""

import asyncio
import sys
from typing import TYPE_CHECKING

from .._vendor.structlog import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from toolregistry import PostRegisterHook, ToolRegistry
    from toolregistry.config import ToolConfig


def create_registry_from_config(
    config: "ToolConfig | None",
    post_register_hooks: "list[PostRegisterHook] | None" = None,
) -> "ToolRegistry":
    """Create a ToolRegistry from configuration.

    Args:
        config: Parsed ``ToolConfig``, or None for empty registry.
        post_register_hooks: Optional list of hooks invoked after each tool
            is registered. Each hook has signature
            ``(tool_name: str, tool: Tool, registry: ToolRegistry) -> str | None``.
            Returning a non-empty string auto-disables the tool with that
            string as the reason.

    Returns:
        Configured ToolRegistry instance.
    """
    from .openapi import create_registry_from_config as _create_registry

    return _create_registry(config, post_register_hooks=post_register_hooks)


def load_config(config_path: str | None) -> "ToolConfig | None":
    """Load configuration from a JSONC or YAML file.

    Args:
        config_path: Path to the configuration file, or None.

    Returns:
        Parsed ``ToolConfig``, or None if no config specified.
    """
    from .openapi import load_config as _load_config

    return _load_config(config_path)


def run_mcp_server(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    config_path: str | None = None,
    registry: "ToolRegistry | None" = None,
    profile: str | None = None,
) -> None:
    """Start the MCP server.

    Args:
        transport: Transport type: stdio, sse, or streamable-http.
        host: Host for SSE/HTTP transport.
        port: Port for SSE/HTTP transport.
        config_path: Path to configuration file.
        registry: Pre-built ToolRegistry to use directly. When provided,
            ``config_path`` is ignored and ``create_registry_from_config``
            is skipped.
        profile: Deployment profile for tag-based tool filtering.
            ``"remote"`` disables tools tagged ``file_system``, ``destructive``,
            or ``privileged``. ``"local"`` applies no filter. ``None`` (default)
            skips profile filtering entirely.
    """
    try:
        from toolregistry_server import RouteTable
        from toolregistry_server.mcp import route_table_to_mcp_server
        from toolregistry_server.mcp.server import (
            run_sse,
            run_stdio,
            run_streamable_http,
        )
    except ImportError as e:
        logger.error(f"MCP server dependencies not installed: {e}")
        logger.info("Install with: pip install toolregistry-server[mcp]")
        sys.exit(1)

    config: ToolConfig | None = None
    if registry is None:
        # Load configuration and build registry from config
        config = load_config(config_path)
        registry = create_registry_from_config(config)

    if profile is not None:
        from .openapi import apply_profile

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
        asyncio.run(run_streamable_http(mcp_server, host=host, port=port))
    else:
        logger.error(f"Unknown transport type: {transport}")
        sys.exit(1)
