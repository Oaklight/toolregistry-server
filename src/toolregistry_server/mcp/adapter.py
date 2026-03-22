"""MCP adapter that creates an MCP low-level Server from a RouteTable.

This module bridges RouteTable and the MCP Python SDK's low-level Server API,
ensuring tool enable/disable state is always read directly from the route table
at request time (no drift).
"""

import json
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

    from ..route_table import RouteTable


def _serialize_result(result: Any) -> str:
    """Convert tool result to a string for MCP TextContent.

    Handles:
    - str → returned directly
    - dict/list/Pydantic model → JSON-serialized
    - Other types → str() fallback

    Args:
        result: The tool execution result.

    Returns:
        A string representation of the result.
    """
    if isinstance(result, str):
        return result

    # Try JSON serialization for structured data
    try:
        if hasattr(result, "model_dump"):
            # Pydantic model
            return json.dumps(result.model_dump(), ensure_ascii=False, default=str)
        elif isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False, default=str)
        else:
            return str(result)
    except (TypeError, ValueError):
        return str(result)


def route_table_to_mcp_server(
    route_table: "RouteTable",
    name: str = "ToolRegistry-Server",
) -> "Server":
    """Create an MCP low-level Server from a RouteTable.

    Registers list_tools and call_tool handlers that read directly
    from the route table, ensuring enable/disable state is always
    in sync (no drift).

    Args:
        route_table: The RouteTable instance to expose as MCP tools.
        name: Server name for MCP identification.

    Returns:
        A configured mcp.server.lowlevel.Server instance.

    Raises:
        ImportError: If MCP SDK is not installed.
    """
    try:
        from mcp.server.lowlevel import Server
        from mcp.shared.exceptions import McpError
        from mcp.types import INTERNAL_ERROR, ErrorData, TextContent
        from mcp.types import Tool as MCPTool
    except ImportError as e:
        raise ImportError(
            "MCP SDK is required for MCP support. "
            "Install with: pip install toolregistry-server[mcp]"
        ) from e

    server = Server(name)

    @server.list_tools()
    async def handle_list_tools() -> list[MCPTool]:
        """Return MCP tool definitions for all enabled tools in the route table."""
        tools: list[MCPTool] = []
        for route in route_table.list_routes(enabled_only=True):
            tools.append(
                MCPTool(
                    name=route.tool_name,
                    description=route.description or "",
                    inputSchema=route.parameters_schema,
                )
            )
        logger.debug(f"list_tools: returning {len(tools)} enabled tools")
        return tools

    @server.call_tool(validate_input=False)
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Execute a tool by name with the given arguments.

        Args:
            name: The tool name to invoke.
            arguments: The input arguments for the tool.

        Returns:
            A list containing a single TextContent with the result.

        Raises:
            McpError: If the tool is disabled or not found.
        """
        # Get the route entry
        route = route_table.get_route(name)

        # Check if tool exists
        if route is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Tool '{name}' not found",
                )
            )

        # Check if tool is disabled
        if not route.enabled:
            reason = route.disable_reason or "unknown reason"
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Tool '{name}' is disabled: {reason}",
                )
            )

        try:
            # Validate and coerce parameters (e.g. string "8" → int 8)
            if isinstance(route.parameters_model, type) and issubclass(
                route.parameters_model, BaseModel
            ):
                model = route.parameters_model(**arguments)
                arguments = model.model_dump_one_level()

            # Execute the tool handler
            if route.is_async:
                result = await route.handler(**arguments)
            else:
                result = route.handler(**arguments)

            # Serialize result to text
            text = _serialize_result(result)

            logger.debug(f"call_tool '{name}': success")
            return [TextContent(type="text", text=text)]

        except McpError:
            raise
        except Exception as e:
            logger.warning(f"call_tool '{name}': error - {e}")
            # Return error as content; the SDK's handler wrapper will catch
            # exceptions and set isError=True via _make_error_result.
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=str(e),
                )
            ) from e

    logger.info(
        f"MCP server '{name}' created with {len(route_table.list_routes())} "
        f"enabled tool(s) out of {len(route_table.list_routes(enabled_only=False))} total"
    )
    return server
