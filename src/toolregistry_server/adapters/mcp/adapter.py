"""MCP adapter that creates an MCP low-level Server from a RouteTable.

This module bridges RouteTable and the MCP Python SDK's low-level Server API,
ensuring tool enable/disable state is always read directly from the route table
at request time (no drift).
"""

import inspect
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..._vendor.structlog import get_logger
from ...route_table import normalize_parameters_schema
from ...session import (
    SessionContext,
    SessionManager,
    session_context_var,
    should_inject_session,
)

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

    from ...route_table import RouteTable

logger = get_logger()


def _get_session_context(session_mgr: "SessionManager") -> SessionContext | None:
    """Extract or create a SessionContext from the current MCP request.

    Uses the compat layer to read the active MCP request context
    (works with both mcp v1 and v2), then delegates to *session_mgr*
    for deduplication and lifecycle management.

    Args:
        session_mgr: The SessionManager that owns session state.

    Returns:
        A :class:`SessionContext`, or ``None`` when called outside an
        MCP request context (should not happen in practice).
    """
    from ._compat import _STDIO_SESSION_KEY, get_mcp_session_info

    info = get_mcp_session_info()
    if info is None:
        return None

    mcp_session, request, session_key = info

    def _factory() -> SessionContext:
        if request is None:
            transport = "stdio"
        else:
            headers = getattr(request, "headers", {})
            transport = "streamable-http" if headers.get("mcp-session-id") else "sse"

        return SessionContext(
            session_id=SessionManager.new_session_id(),
            transport=transport,
        )

    ctx = session_mgr.get_or_create(session_key, _factory)

    # In v2 direct/stdio transport, ctx.session is a new object per call,
    # so a weak-ref finalizer would GC the session between calls. Only
    # register when the session object is stable (v1, or v2 HTTP).
    if session_key is not _STDIO_SESSION_KEY:
        session_mgr.register_finalizer(mcp_session, session_key)

    return ctx


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


def _result_to_mcp_content(result: Any) -> list:
    """Convert a tool result to MCP content blocks.

    If the result is a multimodal content block list (containing image
    blocks), each block is converted to the corresponding MCP content
    type.  Otherwise the result is serialized as a single TextContent.

    Args:
        result: The raw tool execution result.

    Returns:
        A list of MCP content objects (TextContent and/or ImageContent).
    """
    from mcp.types import TextContent

    try:
        from mcp.types import ImageContent

        _has_image_content = True
    except ImportError:
        _has_image_content = False

    try:
        from toolregistry.llm.content_blocks import is_content_block_list

        _has_content_blocks = True
    except ImportError:
        _has_content_blocks = False

    if (
        _has_content_blocks
        and _has_image_content
        and isinstance(result, list)
        and is_content_block_list(result)  # type: ignore[possibly-unresolved-reference]
    ):
        content: list = []
        for block in result:
            if block["type"] == "text":
                content.append(TextContent(type="text", text=block["text"]))
            elif block["type"] == "image":
                source = block["source"]
                content.append(
                    ImageContent(
                        type="image",
                        data=source["data"],
                        mimeType=source["media_type"],
                    )
                )
        if content:
            return content

    return [TextContent(type="text", text=_serialize_result(result))]


async def _execute_tool(
    route: Any,
    arguments: dict,
    session_ctx: SessionContext | None,
    session_mgr: "SessionManager",
) -> Any:
    """Resolve the handler for a route and execute it with the given arguments.

    Handles session-scoped handler resolution, parameter validation/coercion
    via the route's Pydantic model, optional session injection, and async/sync
    dispatch.

    Args:
        route: The RouteEntry for the tool being invoked.
        arguments: The input arguments for the tool.
        session_ctx: The current session context, or None.
        session_mgr: The SessionManager for handler caching.

    Returns:
        The raw result from the tool handler.
    """
    # Resolve handler (possibly session-scoped)
    handler = route.handler
    if route.handler_factory and session_ctx:
        handler = session_mgr.get_session_handler(session_ctx.session_id, route)

    # Validate and coerce parameters (e.g. string "8" → int 8)
    if isinstance(route.parameters_model, type) and issubclass(
        route.parameters_model, BaseModel
    ):
        model = route.parameters_model(**arguments)
        arguments = model.model_dump_one_level()

    # Inject session if handler requests it
    if session_ctx and should_inject_session(handler):
        arguments = {**arguments, "_session": session_ctx}

    # Execute the tool handler.
    # Always check for awaitable results: _FunctionToolWrapper.__call__
    # returns a coroutine when a running event loop is detected, even for
    # sync functions (is_async=False).
    if route.is_async:
        return await handler(**arguments)
    result = handler(**arguments)
    if inspect.isawaitable(result):
        return await result
    return result


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
        from mcp.types import INTERNAL_ERROR

        from ._compat import (
            McpErrorClass,
            create_mcp_server,
            make_mcp_error,
            make_mcp_tool,
        )
    except ImportError as e:
        raise ImportError(
            "MCP SDK is required for MCP support. "
            "Install with: pip install toolregistry-server[mcp]"
        ) from e

    session_mgr = SessionManager()

    async def handle_list_tools() -> list:
        """Return MCP tool definitions for non-deferred enabled tools.

        Deferred tools are excluded from the initial listing so that LLMs
        discover them via discover_tools.
        """
        tools: list = []
        for route in route_table.list_routes(enabled_only=True, include_deferred=False):
            tools.append(
                make_mcp_tool(
                    name=route.tool_name,
                    description=route.description or "",
                    schema=normalize_parameters_schema(route.parameters_schema),
                )
            )
        logger.debug(f"list_tools: returning {len(tools)} enabled tools")
        return tools

    async def handle_call_tool(tool_name: str, arguments: dict) -> list:
        """Execute a tool by name with the given arguments.

        Args:
            tool_name: The tool name to invoke.
            arguments: The input arguments for the tool.

        Returns:
            A list of MCP content blocks (TextContent and/or ImageContent).

        Raises:
            McpErrorClass: If the tool is disabled or not found.
        """
        route = route_table.get_route(tool_name)

        if route is None:
            raise make_mcp_error(INTERNAL_ERROR, f"Tool '{tool_name}' not found")

        if not route.enabled:
            reason = route.disable_reason or "unknown reason"
            raise make_mcp_error(
                INTERNAL_ERROR, f"Tool '{tool_name}' is disabled: {reason}"
            )

        # --- Session context ---
        session_ctx = _get_session_context(session_mgr)
        token = None
        if session_ctx is not None:
            token = session_context_var.set(session_ctx)

        try:
            result = await _execute_tool(route, arguments, session_ctx, session_mgr)
            content = _result_to_mcp_content(result)
            logger.debug(f"call_tool '{tool_name}': success")
            return content

        except McpErrorClass:
            raise
        except Exception as e:
            logger.warning(f"call_tool '{tool_name}': error - {e}")
            raise make_mcp_error(INTERNAL_ERROR, str(e)) from e
        finally:
            if token is not None:
                session_context_var.reset(token)

    server = create_mcp_server(
        name,
        list_tools_handler=handle_list_tools,
        call_tool_handler=handle_call_tool,
    )

    logger.info(
        f"MCP server '{name}' created with {len(route_table.list_routes())} "
        f"enabled tool(s) out of {len(route_table.list_routes(enabled_only=False))} total"
    )
    return server
