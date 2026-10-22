"""MCP adapter that creates an MCP low-level Server from a RouteTable.

This module bridges RouteTable and the MCP Python SDK's low-level Server API,
ensuring tool enable/disable state is always read directly from the route table
at request time (no drift).
"""

import dataclasses
import datetime
import enum
import inspect
import json
import pathlib
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from toolregistry.tool import Tool as _Tool

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

    from ...route_table import RouteEntry, RouteTable

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


def _json_default(value: Any) -> Any:
    """Encode a tool-result value that ``json.dumps`` does not know.

    ``default=str`` turns a dataclass into its ``repr``, so
    ``list[SearchResult]`` reached the model as a JSON array of Python repr
    strings — not machine-readable, and impossible for a client to index by
    field (#187, #192).  Structured values are encoded structurally; ``str``
    remains only the last resort.

    Args:
        value: A value the standard JSON encoder rejected.

    Returns:
        A JSON-encodable representation of *value*.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (datetime.date, datetime.time)):
        # ``datetime.datetime`` is a ``date`` subclass; isoformat covers all.
        return value.isoformat()
    if isinstance(value, pathlib.PurePath):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _serialize_result(result: Any) -> str:
    """Convert tool result to a string for MCP TextContent.

    Handles:
    - str → returned directly
    - dict/list/Pydantic model/dataclass → JSON-serialized
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
            return json.dumps(
                result.model_dump(), ensure_ascii=False, default=_json_default
            )
        elif dataclasses.is_dataclass(result) and not isinstance(result, type):
            return json.dumps(
                dataclasses.asdict(result), ensure_ascii=False, default=_json_default
            )
        elif isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False, default=_json_default)
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
                        text=json.dumps(
                            block, ensure_ascii=False, default=_json_default
                        ),
                    )
                )
        if content:
            return content

    return [TextContent(type="text", text=_serialize_result(result))]


_BOOL_TRUE = {"true", "1", "yes"}
_BOOL_FALSE = {"false", "0", "no"}


def _pre_coerce_bools(
    arguments: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    """Pre-coerce string values to booleans for bool-typed parameters.

    MCP clients may send ``"true"``/``"false"`` strings for boolean
    parameters.  ``Tool.validate_parameters`` handles int/float coercion
    but not string-to-bool, so this step converts them before validation.

    Only recognized boolean strings (true/false/1/0/yes/no) are coerced.
    Unrecognized values are left as strings so that ``validate_parameters``
    raises a proper validation error.

    Args:
        arguments: Raw arguments from the MCP client.
        schema: The tool's JSON Schema.

    Returns:
        Arguments with recognized string booleans coerced to ``bool``.
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
            lowered = value.lower()
            if lowered in _BOOL_TRUE:
                result[key] = True
            elif lowered in _BOOL_FALSE:
                result[key] = False
            # else: leave as string for validate_parameters to reject
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
    # validator.  Coerces string values to their declared types
    # (e.g. string "8" → int 8 for MCP clients that send all values
    # as strings).  Also strips toolcall_reason — tool.parameters is
    # a clean schema, but get_schema() injects toolcall_reason when
    # think_augment is enabled, so clients may still send it.
    needs_session = session_ctx is not None and should_inject_session(handler)

    if isinstance(route.tool, _Tool) and not needs_session:
        # Use the Tool's built-in validator for coercion + stripping.
        # Pre-coerce string booleans that the core validator doesn't handle.
        arguments = _pre_coerce_bools(arguments, route.tool.parameters)
        arguments = route.tool.validate_parameters(arguments)
    else:
        # Session-injected tools skip validate_parameters because _session
        # is a required schema field that MCP clients never send — validation
        # would reject the missing field.  Session tools must accept string
        # params or handle coercion internally.
        # Non-Tool objects (mocks) also fall through here; strip
        # toolcall_reason manually.
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
    subscription_bus: Any | None = None,
) -> None:
    """Wire up MCP tools/list_changed notifications.

    Patches the server's create_initialization_options to always advertise
    tools_changed=True, and registers a RouteTable listener that sends
    send_tool_list_changed() to all tracked sessions on change.

    On SDK builds that serve ``subscriptions/listen`` the same change is
    also published on ``subscription_bus``: the 2026-07-28 wire has no
    standing channel, so a modern client only ever learns about a change
    through a listen stream it opened (#107).

    Args:
        server: The MCP Server instance to patch.
        route_table: The RouteTable to listen for changes.
        sessions: The session tracker WeakSet (populated by session_tracker
            in create_mcp_server).
        subscription_bus: Optional bus for ``subscriptions/listen`` streams.
    """
    import asyncio

    # StreamableHTTP's SessionManager calls create_initialization_options()
    # internally without arguments.  The explicit notification_options= calls
    # in server.py cover stdio/SSE, but this patch is needed to ensure
    # StreamableHTTP also advertises tools_changed=True.
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

    async def _publish_tool_list_changed(bus: Any) -> None:
        """Publish the change to every open ``subscriptions/listen`` stream."""
        from ._compat import publish_tools_changed

        try:
            await publish_tools_changed(bus)
        except Exception:
            logger.warning("failed to publish tools/list_changed", exc_info=True)

    def _schedule(make_coro: Any) -> None:
        """Create and run the coroutine on the current loop, if there is one.

        Takes a factory, not a coroutine: a route change outside a running
        loop is normal (building a server, mutating a table in a script), and
        creating the coroutine before the loop check would leave it unawaited
        and emit ``RuntimeWarning: coroutine was never awaited``.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop — nothing can be listening either.
            return
        task = loop.create_task(make_coro())
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)

    def _on_route_change(tool_name: str, event: str) -> None:
        """Send tool-list-changed to all tracked MCP sessions."""
        for session in list(sessions):
            _schedule(lambda session=session: _send_tool_list_changed(session))
        if subscription_bus is not None:
            _schedule(lambda: _publish_tool_list_changed(subscription_bus))

    route_table.add_listener(_on_route_change)


def _resolve_route_for_call(
    tool_name: str,
    get_enabled_route: "Callable[[str], Any]",
) -> Any:
    """Return the route for a ``tools/call``, resolving it at most once.

    SDK v1 resolves the route in a pre-dispatch hook so that protocol
    errors become JSON-RPC errors; that result arrives here.  Reusing it
    keeps a call to one lookup, so a disable landing mid-call cannot make
    v1 and v2 answer the same request differently (#97).  The route is
    matched by name so a nested call cannot borrow another route.

    Args:
        tool_name: The tool named by the request.
        get_enabled_route: Fallback lookup that raises for an unknown or
            disabled tool.

    Returns:
        The resolved route entry.
    """
    from ._compat import get_prevalidated_route

    route = get_prevalidated_route()
    if route is None or getattr(route, "tool_name", None) != tool_name:
        return get_enabled_route(tool_name)
    return route


#: Tool registered by ``ToolRegistry.enable_tool_discovery()``.  Used only to
#: decide whether the deferred-tool guidance applies.
_DISCOVERY_TOOL = "discover_tools"

#: Proxy tool registered alongside discovery.  The default instructions name
#: it too, so it must be enabled for the guidance to be true.
_CALL_DEFERRED_TOOL = "call_deferred"

#: Instructions returned from ``initialize`` when discovery is served.  This
#: is the two-line model of the server that a client would otherwise have to
#: rediscover by reading tool descriptions (#79).
DEFAULT_INSTRUCTIONS = (
    "This server exposes tools through ToolRegistry. "
    "`discover_tools` returns schemas for tools that are not listed by "
    "`tools/list` (deferred tools); invoke them with `call_deferred`, "
    "passing the tool name as `_target_tool` and its parameters as keyword "
    "arguments."
)

#: Default ``ttlMs`` for ``tools/list``.  Zero — the SDK default — means
#: "never cache".  That is not what we want to ship yet: on MCP SDK v2 the
#: ``notifications/tools/list_changed`` that would correct a cached list is
#: never delivered (#107), so a non-zero TTL here would leave a client
#: showing tools that no longer exist for the whole lifetime.  The cache
#: hints are configurable now (``--tools-list-ttl-ms``); raise this default
#: to 300000, the spec's example, once the notification path is verified
#: end to end.
DEFAULT_TOOLS_LIST_TTL_MS = 0

#: Default ``cacheScope`` for ``tools/list``.  The served set can differ by
#: profile and authorization, so it must not be cached by shared proxies.
DEFAULT_TOOLS_LIST_CACHE_SCOPE: Literal["public", "private"] = "private"


def _resolve_server_info(
    route_table: "RouteTable",
    version: str | None,
    instructions: str | None,
) -> tuple[str, str | None]:
    """Fill in the ``serverInfo`` defaults (#79).

    Args:
        route_table: The routes being served.
        version: Caller-supplied version, or ``None`` for the default.
        instructions: Caller-supplied instructions, or ``None`` for the
            default.

    Returns:
        The effective ``(version, instructions)`` pair.  The version falls
        back to this package's version; the instructions fall back to
        :data:`DEFAULT_INSTRUCTIONS` only when ``discover_tools`` is served,
        because that is the one thing a client cannot infer from
        ``tools/list`` alone.
    """
    if version is None:
        from ... import __version__ as server_version

        version = server_version
    if instructions is None and _discovery_available(route_table):
        instructions = DEFAULT_INSTRUCTIONS
    return version, instructions


def _discovery_available(route_table: "RouteTable") -> bool:
    """Whether the deferred-tool flow the default instructions describe works.

    ``get_route`` returns disabled routes too, so checking presence alone told
    clients to use ``discover_tools``/``call_deferred`` even when the profile
    had disabled them — advice that leads straight to a ``-32602`` (#112).

    Args:
        route_table: The routes being served.

    Returns:
        True only when both tools the guidance names exist and are enabled.
    """
    for name in (_DISCOVERY_TOOL, _CALL_DEFERRED_TOOL):
        route = route_table.get_route(name)
        if route is None or not route.enabled:
            return False
    return True


def route_table_to_mcp_server(
    route_table: "RouteTable",
    name: str = "ToolRegistry-Server",
    *,
    list_tools_ttl_ms: int | None = None,
    list_tools_cache_scope: Literal["public", "private"] | None = None,
    version: str | None = None,
    instructions: str | None = None,
) -> "Server":
    """Create an MCP low-level Server from a RouteTable.

    Registers list_tools and call_tool handlers that read directly
    from the route table, ensuring enable/disable state is always
    in sync (no drift).

    Args:
        route_table: The RouteTable instance to expose as MCP tools.
        name: Server name for MCP identification.
        list_tools_ttl_ms: ``tools/list`` cache lifetime in milliseconds
            (MCP spec 2026-07-28).  ``None`` uses
            :data:`DEFAULT_TOOLS_LIST_TTL_MS`; pass ``0`` to advertise
            "never cache".  Only effective on SDK v2.
        list_tools_cache_scope: ``"public"`` or ``"private"`` cache scope
            for ``tools/list``.  ``None`` uses
            :data:`DEFAULT_TOOLS_LIST_CACHE_SCOPE`.  Only effective on
            SDK v2.
        version: Implementation version reported in ``serverInfo``.
            ``None`` uses the installed ``toolregistry-server`` version;
            an empty string leaves it blank.
        instructions: Usage guidance returned from ``initialize``.  ``None``
            uses :data:`DEFAULT_INSTRUCTIONS` when tool discovery is served,
            and no instructions otherwise.

    Returns:
        A configured mcp.server.lowlevel.Server instance.

    Raises:
        ImportError: If MCP SDK is not installed.
    """
    try:
        from mcp.types import INTERNAL_ERROR, INVALID_PARAMS

        from ._compat import (
            McpErrorClass,
            create_mcp_server,
            make_mcp_error,
            make_mcp_tool,
            make_tool_annotations,
        )
    except ImportError as e:
        raise ImportError(
            "MCP SDK is required for MCP support. "
            "Install with: pip install toolregistry-server[mcp]"
        ) from e

    session_mgr = SessionManager()

    from ._compat import make_subscription_bus

    subscription_bus = make_subscription_bus()

    def get_enabled_route(tool_name: str) -> "RouteEntry":
        route = route_table.get_route(tool_name)
        if route is None:
            raise make_mcp_error(INVALID_PARAMS, f"Tool '{tool_name}' not found")
        if not route.enabled:
            reason = route.disable_reason or "unknown reason"
            raise make_mcp_error(
                INVALID_PARAMS, f"Tool '{tool_name}' is disabled: {reason}"
            )
        return route

    async def handle_list_tools() -> list:
        """Return MCP tool definitions for non-deferred enabled tools.

        Deferred tools are excluded from the initial listing so that LLMs
        discover them via discover_tools.  Tools whose metadata declares a
        title or execution hints carry ``title`` and ``annotations``;
        everything else keeps the minimal definition.
        """
        tools: list = []
        for route in route_table.list_routes(enabled_only=True, include_deferred=False):
            tools.append(
                make_mcp_tool(
                    name=route.tool_name,
                    description=route.description or "",
                    schema=normalize_parameters_schema(route.parameters_schema),
                    output_schema=route.output_schema,
                    title=route.title,
                    annotations=make_tool_annotations(route.execution_hints),
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
        # Reuse the route the v1 pre-dispatch hook already resolved, so a
        # call has one lookup and one outcome on both SDK generations (#97).
        route = _resolve_route_for_call(tool_name, get_enabled_route)

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

    version, instructions = _resolve_server_info(route_table, version, instructions)

    server = create_mcp_server(
        name,
        version=version,
        instructions=instructions,
        list_tools_handler=handle_list_tools,
        call_tool_handler=handle_call_tool,
        call_tool_route_getter=get_enabled_route,
        # ``None`` means "use the server default", not "omit the hint".  The
        # default is 0 — clients must not cache until #107 is fixed — which
        # happens to match the SDK's own default; an explicit 0 is preserved
        # rather than treated as unset (#83).
        list_tools_ttl_ms=(
            DEFAULT_TOOLS_LIST_TTL_MS
            if list_tools_ttl_ms is None
            else list_tools_ttl_ms
        ),
        list_tools_cache_scope=(
            DEFAULT_TOOLS_LIST_CACHE_SCOPE
            if list_tools_cache_scope is None
            else list_tools_cache_scope
        ),
        session_tracker=_sessions,
        subscription_bus=subscription_bus,
    )

    _setup_tools_changed_notifications(
        server, route_table, _sessions, subscription_bus=subscription_bus
    )

    logger.info(
        f"MCP server '{name}' created with {len(route_table.list_routes())} "
        f"enabled tool(s) out of {len(route_table.list_routes(enabled_only=False))} total"
    )
    return server
