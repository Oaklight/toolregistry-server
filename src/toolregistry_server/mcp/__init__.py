"""
MCP (Model Context Protocol) adapter for ToolRegistry.

This module provides functionality to expose ToolRegistry tools via
the Model Context Protocol for LLM integration.

Main Components:
    - create_mcp_server: Create an MCP server from a RouteTable
    - route_table_to_mcp_server: Convert a RouteTable to an MCP server

Example:
    >>> from toolregistry import ToolRegistry
    >>> from toolregistry_server import RouteTable
    >>> from toolregistry_server.mcp import create_mcp_server
    >>>
    >>> registry = ToolRegistry()
    >>> route_table = RouteTable(registry)
    >>> server = create_mcp_server(route_table)

Note:
    This module requires the 'mcp' extra to be installed:
    pip install toolregistry-server[mcp]
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

    from ..route_table import RouteTable


def create_mcp_server(
    route_table: "RouteTable",
    name: str = "ToolRegistry-Server",
) -> "Server":
    """Create an MCP server from a RouteTable.

    Args:
        route_table: The RouteTable to expose.
        name: Server name for MCP identification.

    Returns:
        A configured MCP Server instance.

    Raises:
        ImportError: If MCP SDK is not installed.
    """
    return route_table_to_mcp_server(route_table, name)


def route_table_to_mcp_server(
    route_table: "RouteTable",
    name: str = "ToolRegistry-Server",
) -> "Server":
    """Create an MCP server from a RouteTable.

    Args:
        route_table: The RouteTable to convert.
        name: Server name for MCP identification.

    Returns:
        A configured MCP Server instance.

    Raises:
        ImportError: If MCP SDK is not installed.
    """
    try:
        from mcp.server.lowlevel import Server
        from mcp.types import Tool as MCPTool
    except ImportError as e:
        raise ImportError(
            "MCP SDK is required for MCP support. "
            "Install with: pip install toolregistry-server[mcp]"
        ) from e

    server = Server(name)

    @server.list_tools()
    async def handle_list_tools() -> list[MCPTool]:
        """List all available tools."""
        return [
            MCPTool(
                name=route.tool_name,
                description=route.description,
                inputSchema=route.parameters_schema,
            )
            for route in route_table.list_routes(enabled_only=True)
        ]

    # TODO: Implement call_tool handler
    # This is a skeleton implementation - full implementation will be
    # migrated from toolregistry-hub's mcp_adapter.py

    return server


__all__ = [
    "create_mcp_server",
    "route_table_to_mcp_server",
]
