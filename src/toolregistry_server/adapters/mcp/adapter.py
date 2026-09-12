"""MCP adapter that creates an MCP low-level Server from a RouteTable.

This module bridges RouteTable and the MCP Python SDK's low-level Server API,
ensuring tool enable/disable state is always read directly from the route table
at request time (no drift).
"""

import inspect
import json
import weakref
from typing import TYPE_CHECKING, Any, Literal

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
    from mcp.types import ContentBlock as MCPContentBlock

    from ...route_table import RouteTable

logger = get_logger()

# --- Multimodal content support (module-level capability probes) -----------

from mcp.types import (  # noqa: E402
    AudioContent,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
)

try:
    from toolregistry.llm.content_blocks import is_content_block_list  # noqa: E402

    _HAS_CONTENT_BLOCKS = True
except ImportError:
    _HAS_CONTENT_BLOCKS = False


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


def _get_mime_type(d: dict) -> str | None:
    """Read MIME type from a content block dict, accepting both key conventions."""
    return d.get("mimeType", d.get("media_type"))


def _result_to_mcp_content(result: Any) -> "list[MCPContentBlock]":
    """Convert a tool result to MCP content blocks.

    If the result is a multimodal content block list (containing image
    blocks), each block is converted to the corresponding MCP content
    type.  Otherwise the result is serialized as a single TextContent.

    Args:
        result: The raw tool execution result.

    Returns:
        A list of MCP content objects (TextContent and/or ImageContent).
    """
    if (
        _HAS_CONTENT_BLOCKS
        and isinstance(result, list)
        and is_content_block_list(result)  # type: ignore[possibly-unresolved-reference]
    ):
        content: list = []
        for block in result:
            btype = block["type"]
            if btype == "text":
                content.append(TextContent(type="text", text=block["text"]))
            elif btype == "image":
                source = block["source"]
                mime = _get_mime_type(source)
                content.append(
                    ImageContent(  # type: ignore[call-arg]
                        type="image",
                        data=source["data"],
                        **{"mimeType": mime} if mime else {},
                    )
                )
            elif btype == "audio":
                mime = _get_mime_type(block)
                content.append(
                    AudioContent(  # type: ignore[call-arg]
                        type="audio",
                        data=block["data"],
                        **{"mimeType": mime} if mime else {},
                    )
                )
            elif btype == "resource_link":
                mime = _get_mime_type(block)
                content.append(
                    ResourceLink(  # type: ignore[call-arg]
                        type="resource_link",
                        uri=block["uri"],
                        name=block["name"],
                        **{"mimeType": mime} if mime else {},
                    )
                )
            elif btype == "resource":
                content.append(
                    EmbeddedResource(  # type: ignore[call-arg]
                        type="resource",
                        resource=block["resource"],
                    )
                )
            else:
                logger.warning(
                    f"Unhandled content block type '{btype}', falling back to text"
                )
                content.append(
                    TextContent(
                        type="text",
                        text=json.dumps(block, ensure_ascii=False, default=str),
                    )
                )
        if content:
            return content

    return [TextContent(type="text", text=_serialize_result(result))]


def _pre_coerce_bools(
    arguments: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    """Pre-coerce string values to booleans for bool-typed parameters.

    MCP clients may send ``"true"``/``"false"`` strings for boolean
    parameters.  ``Tool.validate_parameters`` handles int/float coercion
    but not string-to-bool, so this step converts them before validation.

    Args:
        arguments: Raw arguments from the MCP client.
        schema: The tool's JSON Schema.

    Returns:
        Arguments with string booleans coerced to ``bool``.
    """
    properties = schema.get("properties", {})
    result = dict(arguments)
    for key, value in result.items():
        if not isinstance(value, str):
            continue
        prop_schema = properties.get(key, {})
        prop_type = prop_schema.get("type")
        if isinstance(prop_type, list):
            non_null = [t for t in prop_type if t != "null"]
            prop_type = non_null[0] if non_null else None
        if prop_type == "boolean":
            result[key] = value.lower() in ("true", "1", "yes")
    return result


async def _execute_tool(
    route: Any,
    arguments: dict,
    session_ctx: SessionContext | None,
    session_mgr: "SessionManager",
) -> Any:
    """Resolve the handler for a route and execute it with the given arguments.

    Handles session-scoped handler resolution, parameter validation/coercion
    via ``Tool.validate_parameters``, optional session injection, and
    async/sync dispatch.

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

    # Validate and coerce parameter types using the Tool's built-in
    # validator.  This strips framework-injected fields (e.g.
    # toolcall_reason) and coerces string values to their declared
    # types (e.g. string "8" → int 8 for MCP clients that send all
    # values as strings).
    from toolregistry.tool import Tool as _Tool

    needs_session = session_ctx is not None and should_inject_session(handler)

    if isinstance(route.tool, _Tool) and not needs_session:
        # Use the Tool's built-in validator for coercion + stripping.
        # Pre-coerce string booleans that the core validator doesn't handle.
        arguments = _pre_coerce_bools(arguments, route.tool.parameters)
        arguments = route.tool.validate_parameters(arguments)
    else:
        # For session-injected tools or non-Tool objects (mocks):
        # strip toolcall_reason manually.  We cannot use validate_parameters
        # for session tools because _session is a required schema field
        # that the MCP client never sends.
        arguments = {k: v for k, v in arguments.items() if k != "toolcall_reason"}

    # Inject session if handler requests it
    if needs_session:
        arguments["_session"] = session_ctx

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


def _setup_tools_changed_notifications(
    server: "Server",
    route_table: "RouteTable",
    sessions: "weakref.WeakSet[Any]",
) -> None:
    """Wire up MCP tools/list_changed notifications.

    Patches the server's create_initialization_options to always advertise
    tools_changed=True, and registers a RouteTable listener that sends
    send_tool_list_changed() to all tracked sessions on change.

    Args:
        server: The MCP Server instance to patch.
        route_table: The RouteTable to listen for changes.
        sessions: The session tracker WeakSet (populated by session_tracker
            in create_mcp_server).
    """
    import asyncio

    # Patch create_initialization_options so that tools_changed is always
    # advertised, even when called without arguments by the SDK's internal
    # StreamableHTTPSessionManager.
    _orig_create_init_opts = server.create_initialization_options

    def _patched_create_init_opts(
        notification_options=None, experimental_capabilities=None
    ):
        from mcp.server import NotificationOptions as _NO

        opts = notification_options or _NO()
        opts.tools_changed = True
        return _orig_create_init_opts(opts, experimental_capabilities)

    server.create_initialization_options = _patched_create_init_opts  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]

    # Hold strong references to pending notification tasks so they are
    # not garbage-collected before completion ("Task exception was never
    # retrieved" warning).
    _pending_tasks: set[asyncio.Task[None]] = set()

    async def _send_tool_list_changed(session: Any) -> None:
        """Send the notification, removing dead sessions on failure."""
        try:
            await session.send_tool_list_changed()
        except Exception:
            sessions.discard(session)

    def _on_route_change(tool_name: str, event: str) -> None:
        """Send tool-list-changed to all tracked MCP sessions."""
        for session in list(sessions):
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(_send_tool_list_changed(session))
                _pending_tasks.add(task)
                task.add_done_callback(_pending_tasks.discard)
            except RuntimeError:
                # No running event loop — skip silently
                pass

    route_table.add_listener(_on_route_change)


def route_table_to_mcp_server(
    route_table: "RouteTable",
    name: str = "ToolRegistry-Server",
    *,
    list_tools_ttl_ms: int | None = None,
    list_tools_cache_scope: Literal["public", "private"] | None = None,
) -> "Server":
    """Create an MCP low-level Server from a RouteTable.

    Registers list_tools and call_tool handlers that read directly
    from the route table, ensuring enable/disable state is always
    in sync (no drift).

    Args:
        route_table: The RouteTable instance to expose as MCP tools.
        name: Server name for MCP identification.
        list_tools_ttl_ms: Optional ``tools/list`` cache lifetime in
            milliseconds (MCP spec 2026-07-28). Only effective on SDK v2.
        list_tools_cache_scope: Optional ``"public"`` or ``"private"``
            cache scope for ``tools/list``. Only effective on SDK v2.

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
                    output_schema=route.output_schema,
                )
            )
        logger.debug(f"list_tools: returning {len(tools)} enabled tools")
        return tools

    async def handle_call_tool(
        tool_name: str, arguments: dict
    ) -> "tuple[list[MCPContentBlock], Any]":
        """Execute a tool by name with the given arguments.

        Args:
            tool_name: The tool name to invoke.
            arguments: The input arguments for the tool.

        Returns:
            A ``(content, structured)`` pair. *structured* is the raw
            result when the tool declares an ``outputSchema``, else None.

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

        # --- Session context & notification tracking ---
        session_ctx = _get_session_context(session_mgr)
        token = None
        if session_ctx is not None:
            token = session_context_var.set(session_ctx)

        try:
            result = await _execute_tool(route, arguments, session_ctx, session_mgr)
            content = _result_to_mcp_content(result)
            is_multimodal = (
                _HAS_CONTENT_BLOCKS
                and isinstance(result, list)
                and is_content_block_list(result)  # type: ignore[possibly-unresolved-reference]
            )
            structured = (
                result
                if route.output_schema
                and isinstance(result, (dict, list))
                and not is_multimodal
                else None
            )
            logger.debug(f"call_tool '{tool_name}': success")
            return content, structured

        except McpErrorClass:
            raise
        except Exception as e:
            logger.warning(f"call_tool '{tool_name}': error - {e}")
            raise make_mcp_error(INTERNAL_ERROR, str(e)) from e
        finally:
            if token is not None:
                session_context_var.reset(token)

    # Track active MCP sessions so we can push tool-list-changed
    # notifications when the route table changes.
    _sessions: weakref.WeakSet[Any] = weakref.WeakSet()

    server = create_mcp_server(
        name,
        list_tools_handler=handle_list_tools,
        call_tool_handler=handle_call_tool,
        list_tools_ttl_ms=list_tools_ttl_ms,
        list_tools_cache_scope=list_tools_cache_scope,
        session_tracker=_sessions,
    )

    _setup_tools_changed_notifications(server, route_table, _sessions)

    logger.info(
        f"MCP server '{name}' created with {len(route_table.list_routes())} "
        f"enabled tool(s) out of {len(route_table.list_routes(enabled_only=False))} total"
    )
    return server
