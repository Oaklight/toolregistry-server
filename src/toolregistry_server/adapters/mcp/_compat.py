"""MCP SDK v1/v2 compatibility layer.

Abstracts the breaking changes between mcp v1.x and v2.x so that the
adapter and test code works with either version.  When we eventually
implement our own MCP SDK, only this file needs a new backend.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Coroutine, MutableSet
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server
    from mcp.types import ContentBlock as MCPContentBlock
    from mcp.types import Tool as MCPTool

# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------


def _detect_mcp_version() -> int:
    try:
        from mcp.shared.exceptions import MCPError  # noqa: F811

        del MCPError
        return 2
    except ImportError:
        return 1


MCP_VERSION: int = _detect_mcp_version()

# MCP specification revision this adapter is written against.  The
# ``mcp-upstream-drift`` workflow compares it with the latest release of
# modelcontextprotocol/modelcontextprotocol and opens an issue when the
# protocol moves ahead of us.
MCP_SPEC_VERSION = "2026-07-28"

# ---------------------------------------------------------------------------
# Error abstraction
# ---------------------------------------------------------------------------

if MCP_VERSION >= 2:
    from mcp.shared.exceptions import (
        MCPError as McpErrorClass,  # type: ignore[assignment]
    )

    def make_mcp_error(code: int, message: str) -> Exception:
        """Create an MCP error (v2 constructor)."""
        return McpErrorClass(code, message)  # type: ignore[call-arg]

else:
    from mcp.shared.exceptions import (
        McpError as McpErrorClass,  # type: ignore[assignment,no-redef]
    )
    from mcp.types import ErrorData as _ErrorData

    def make_mcp_error(code: int, message: str) -> Exception:  # type: ignore[misc]
        """Create an MCP error (v1 constructor wraps ErrorData)."""
        return McpErrorClass(_ErrorData(code=code, message=message))


def _get_mcp_error_code(error: Exception) -> int | None:
    """Return the JSON-RPC error code carried by an MCP error, if any.

    v1 stores it on ``error.error.code``, v2 on ``error.code``; both shapes
    are probed so the caller can branch on the code without knowing which
    SDK version raised it.
    """
    error_data = getattr(error, "error", None)
    code = getattr(error_data, "code", None)
    if code is None:
        code = getattr(error, "code", None)
    return code if isinstance(code, int) else None


# ---------------------------------------------------------------------------
# Request context bridge
# ---------------------------------------------------------------------------

# In v2 the lowlevel handler receives ``ctx`` as a parameter; we stash it
# here so that ``get_mcp_session_info()`` can retrieve it the same way
# v1's ``request_ctx`` contextvar works.
_v2_request_ctx: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "_v2_request_ctx", default=None
)


_STDIO_SESSION_KEY = "stdio-singleton"

# The v1 pre-dispatch hook resolves the route to turn protocol errors into
# JSON-RPC errors.  Carrying that result to the handler keeps a call to a
# single resolution: with two lookups, a disable landing between them made
# v1 return an isError result where v2 returns -32602 (#97).
_prevalidated_route: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "_prevalidated_route", default=None
)


def trackable_session(ctx: Any) -> Any:
    """Return the object a route-change notification should be sent through.

    On SDK v2 the handler receives a **per-request** ``ServerSession`` — the
    SDK builds one per inbound message and it dies with the request — so
    holding it in the session set leaves nothing to notify later.  The
    connection-scoped object is the session's ``_connection``; both expose
    ``send_tool_list_changed()``, so callers do not care which they hold.

    Falls back to the session itself when there is no ``_connection`` (SDK
    v1, or a shape we do not know), which keeps the v1 path and any future
    SDK change working.

    Args:
        ctx: The v2 request context handed to a handler.

    Returns:
        The object to track for ``tools/list_changed``.
    """
    session = ctx.session
    return getattr(session, "_connection", None) or session


def get_prevalidated_route() -> Any | None:
    """Return the route resolved earlier in this request, if any.

    Set by the SDK v1 pre-dispatch hook; ``None`` when the handler has to
    resolve the route itself (SDK v2, or a server built without the hook).
    """
    return _prevalidated_route.get()


def get_mcp_session_info() -> tuple[Any, Any | None, Any] | None:
    """Return ``(mcp_session, request, session_key)`` for the current request.

    Works transparently with both v1 and v2.  The *session_key* is a
    hashable value suitable for deduplicating session contexts:

    - v1: ``id(session)`` (the same ``ServerSession`` object is reused)
    - v2 with HTTP request: ``mcp-session-id`` header value
    - v2 without request (stdio/direct): a module-level constant
      (single logical session)

    Returns:
        A 3-tuple or ``None`` when called outside an MCP request context.
    """
    if MCP_VERSION >= 2:
        ctx = _v2_request_ctx.get(None)
        if ctx is None:
            return None
        session = ctx.session
        request = getattr(ctx, "request", None)
        if request is not None:
            headers = getattr(request, "headers", {})
            session_key = headers.get("mcp-session-id", id(session))
        else:
            session_key = _STDIO_SESSION_KEY
        return session, request, session_key
    else:
        try:
            # Internal API — no public accessor for v1 request context exists.
            from mcp.server.lowlevel.server import request_ctx
        except ImportError:
            return None
        mcp_ctx = request_ctx.get(None)
        if mcp_ctx is None:
            return None
        session = mcp_ctx.session
        request = getattr(mcp_ctx, "request", None)
        return session, request, id(session)


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------

# Handler type aliases (our uniform internal signatures)
ListToolsHandler = Callable[[], Coroutine[Any, Any, "list[MCPTool]"]]
CallToolHandler = Callable[
    [str, dict[str, Any]],
    Coroutine[Any, Any, "tuple[list[MCPContentBlock], Any]"],
]
CallToolRouteGetter = Callable[[str], object]


def create_mcp_server(
    name: str,
    *,
    list_tools_handler: ListToolsHandler,
    call_tool_handler: CallToolHandler,
    call_tool_route_getter: CallToolRouteGetter | None = None,
    list_tools_ttl_ms: int | None = None,
    list_tools_cache_scope: Literal["public", "private"] | None = None,
    session_tracker: MutableSet | None = None,
    version: str = "",
    instructions: str | None = None,
) -> Server:
    """Create an MCP lowlevel Server with handlers registered.

    Our handlers use a stable internal signature; this function adapts
    them to whatever the installed SDK version expects.

    Args:
        name: Server name for MCP identification.
        list_tools_handler: ``async () -> list[Tool]``
        call_tool_handler: ``async (name, arguments) -> list[ContentBlock]``
        call_tool_route_getter: Synchronous route lookup used to preserve
            protocol errors in SDK v1, whose call-tool decorator catches them.
            The returned route is carried to the handler, so a call resolves
            its route once.
        list_tools_ttl_ms: Optional cache lifetime in milliseconds for
            ``tools/list`` responses (MCP spec 2026-07-28). Ignored on v1.
        list_tools_cache_scope: Cache scope for ``tools/list``. Ignored on v1.
        session_tracker: Optional mutable set (``set`` or ``WeakSet``) to
            collect active ``ServerSession`` objects for sending
            ``notifications/tools/list_changed``.
        version: Implementation version reported in ``serverInfo``.
            ``""`` leaves it empty, which is what the SDK default does but
            makes deployments indistinguishable in client logs (#79).
        instructions: Optional usage guidance returned from ``initialize``.

    Returns:
        A configured ``mcp.server.lowlevel.Server``.
    """
    if MCP_VERSION >= 2:
        return _create_server_v2(
            name,
            list_tools_handler,
            call_tool_handler,
            list_tools_ttl_ms,
            list_tools_cache_scope,
            session_tracker,
            version,
            instructions,
        )
    else:
        return _create_server_v1(
            name,
            list_tools_handler,
            call_tool_handler,
            call_tool_route_getter,
            session_tracker,
            version,
            instructions,
        )


def _create_server_v1(
    name: str,
    list_tools_handler: ListToolsHandler,
    call_tool_handler: CallToolHandler,
    call_tool_route_getter: CallToolRouteGetter | None = None,
    session_tracker: MutableSet | None = None,
    version: str = "",
    instructions: str | None = None,
) -> Server:
    from mcp.server.lowlevel import Server

    server = Server(name, version=version or None, instructions=instructions)

    @server.list_tools()
    async def _list_tools() -> list:
        if session_tracker is not None:
            try:
                # Internal API — no public accessor for v1 request context exists.
                from mcp.server.lowlevel.server import request_ctx

                mcp_ctx = request_ctx.get(None)
                if mcp_ctx is not None:
                    session_tracker.add(mcp_ctx.session)
            except ImportError:
                pass
        return await list_tools_handler()

    @server.call_tool(validate_input=False)
    async def _call_tool(tool_name: str, arguments: dict) -> Any:
        content, structured = await call_tool_handler(tool_name, arguments)
        if structured is not None:
            return content, structured
        return content

    if call_tool_route_getter is not None:
        # SDK v1's ``call_tool`` decorator wraps the callback in a
        # try/except that turns every exception -- including ``McpError``
        # -- into an ``isError`` result.  Protocol errors must reach the
        # client as JSON-RPC errors instead, so the lookup runs one level
        # up, in the registered request handler.  ``request_handlers`` is
        # an internal mapping, but it is stable across the v1 range we
        # support and this file is exactly where SDK coupling belongs.
        from mcp.types import CallToolRequest

        inner_handler = server.request_handlers[CallToolRequest]

        async def _handle_call_tool_request(request: CallToolRequest) -> Any:
            # Hand the resolved route down so the handler does not look it
            # up a second time (#97).
            token = _prevalidated_route.set(call_tool_route_getter(request.params.name))
            try:
                return await inner_handler(request)
            finally:
                _prevalidated_route.reset(token)

        server.request_handlers[CallToolRequest] = _handle_call_tool_request

    return server


def _create_server_v2(
    name: str,
    list_tools_handler: ListToolsHandler,
    call_tool_handler: CallToolHandler,
    list_tools_ttl_ms: int | None = None,
    list_tools_cache_scope: Literal["public", "private"] | None = None,
    session_tracker: MutableSet | None = None,
    version: str = "",
    instructions: str | None = None,
) -> Server:
    from mcp.server.lowlevel import Server
    from mcp.types import INVALID_PARAMS, CallToolResult, ListToolsResult

    _cache_supported = supports_list_tools_cache()

    async def on_list_tools(ctx: Any, params: Any) -> Any:
        if session_tracker is not None:
            # The per-request session dies with this request (#107).
            session_tracker.add(trackable_session(ctx))
        tools = await list_tools_handler()
        extra: dict[str, Any] = {}
        if list_tools_ttl_ms is not None and _cache_supported:
            extra["ttl_ms"] = list_tools_ttl_ms
        if list_tools_cache_scope is not None and _cache_supported:
            extra["cache_scope"] = list_tools_cache_scope
        return ListToolsResult(tools=tools, **extra)

    async def on_call_tool(ctx: Any, params: Any) -> Any:
        from mcp.types import TextContent

        token = _v2_request_ctx.set(ctx)
        try:
            tool_name = params.name
            arguments = params.arguments or {}
            content, structured = await call_tool_handler(tool_name, arguments)
            kwargs: dict[str, Any] = {}
            if structured is not None:
                kwargs["structured_content"] = structured
            return CallToolResult(content=content, is_error=False, **kwargs)  # type: ignore[call-arg]
        except McpErrorClass as e:
            # Invalid params (unknown or disabled tool) is a protocol-level
            # failure, not a tool execution result: let it out so the
            # runtime serializes it as a JSON-RPC error.
            if _get_mcp_error_code(e) == INVALID_PARAMS:
                raise
            return CallToolResult(  # type: ignore[call-arg]
                content=[TextContent(type="text", text=str(e))],
                is_error=True,
            )
        except Exception as e:
            # Everything else stays an LLM-visible tool result, matching
            # SDK v1 behavior where execution errors are ``isError``.
            return CallToolResult(  # type: ignore[call-arg]
                content=[TextContent(type="text", text=str(e))],
                is_error=True,
            )
        finally:
            _v2_request_ctx.reset(token)

    return Server(  # type: ignore[call-arg]
        name,
        version=version,
        instructions=instructions,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


# ---------------------------------------------------------------------------
# Field accessor
# ---------------------------------------------------------------------------


_ANNOTATION_NAMES: dict[str, str] = {
    "read_only_hint": "readOnlyHint",
    "destructive_hint": "destructiveHint",
    "idempotent_hint": "idempotentHint",
    "open_world_hint": "openWorldHint",
}


def make_tool_annotations(hints: dict[str, bool]) -> Any:
    """Build MCP tool annotations from resolved execution hints.

    Args:
        hints: Map of snake_case ``ToolMetadata`` field names to resolved
            values, as returned by ``Tool.execution_hints``.  Unset hints
            are absent from the mapping and stay absent here, so a host
            sees "unset" rather than a fabricated default.

    Returns:
        A ``ToolAnnotations`` instance, or ``None`` when no hint is set.
        ``None`` keeps the tool definition at the minimal
        ``{name, description, inputSchema}`` shape it had before.
    """
    values = {
        core_name: hints[core_name]
        for core_name in _ANNOTATION_NAMES
        if hints.get(core_name) is not None
    }
    if not values:
        return None

    from mcp.types import ToolAnnotations

    if MCP_VERSION >= 2:
        # v2 renamed the fields to snake_case (serialized with camelCase
        # aliases).
        return ToolAnnotations(**values)
    return ToolAnnotations(
        **{_ANNOTATION_NAMES[name]: value for name, value in values.items()}
    )


def make_mcp_tool(
    name: str,
    description: str,
    schema: dict,
    output_schema: dict | None = None,
    title: str | None = None,
    annotations: Any | None = None,
) -> Any:
    """Create an MCP Tool with the correct field names for the SDK version."""
    from mcp.types import Tool

    if MCP_VERSION >= 2:
        kwargs: dict[str, Any] = {"input_schema": schema}
        if output_schema is not None:
            kwargs["output_schema"] = output_schema
    else:
        kwargs = {"inputSchema": schema}
        if output_schema is not None:
            kwargs["outputSchema"] = output_schema
    if title is not None:
        kwargs["title"] = title
    if annotations is not None:
        kwargs["annotations"] = annotations
    return Tool(name=name, description=description, **kwargs)


def supports_list_tools_cache() -> bool:
    """Return True when the SDK's ``ListToolsResult`` supports cache hints.

    ``ttlMs`` / ``cacheScope`` were introduced by MCP spec 2026-07-28 and are
    only present on SDK v2.
    """
    try:
        from mcp.types import ListToolsResult
    except ImportError:
        return False
    return "ttl_ms" in getattr(ListToolsResult, "model_fields", {})


_MISSING = object()


def get_field(obj: Any, snake_name: str, camel_name: str, default: Any = None) -> Any:
    """Access a field that may be snake_case (v2) or camelCase (v1).

    Uses a sentinel so that legitimate ``None`` values (e.g. optional
    ``mime_type``) are not confused with "attribute missing".
    """
    val = getattr(obj, snake_name, _MISSING)
    if val is not _MISSING:
        return val
    return getattr(obj, camel_name, default)


def _snake_to_camel(name: str) -> str:
    """Convert ``snake_case`` to ``camelCase``."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _camel_to_snake(name: str) -> str:
    """Convert ``camelCase`` to ``snake_case``."""
    import re

    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


class CompatProxy:
    """Wrapper that resolves both snake_case and camelCase attribute access.

    MCP SDK v1 uses camelCase (``isError``, ``inputSchema``), while v2
    uses snake_case (``is_error``, ``input_schema``).  Wrapping an SDK
    object in ``CompatProxy`` lets test code use either convention
    without manual ``get_field()`` calls::

        result = CompatProxy(await client.call_tool(...))
        assert result.is_error is False   # works on both v1 and v2

    Attribute lookup order: try the requested name first, then try the
    opposite convention (snake ↔ camel).  Non-string keys and dunder
    attributes bypass the fallback.
    """

    __slots__ = ("_obj",)

    def __init__(self, obj: Any) -> None:
        object.__setattr__(self, "_obj", obj)

    def __getattr__(self, name: str) -> Any:
        obj = object.__getattribute__(self, "_obj")
        val = getattr(obj, name, _MISSING)
        if val is not _MISSING:
            return val
        alt = _snake_to_camel(name) if "_" in name else _camel_to_snake(name)
        val = getattr(obj, alt, _MISSING)
        if val is not _MISSING:
            return val
        raise AttributeError(
            f"{type(obj).__name__!r} has no attribute {name!r} or {alt!r}"
        )

    def __repr__(self) -> str:
        return f"CompatProxy({object.__getattribute__(self, '_obj')!r})"

    def __iter__(self):
        return iter(object.__getattribute__(self, "_obj"))

    def __len__(self):
        return len(object.__getattribute__(self, "_obj"))

    def __getitem__(self, key: Any) -> Any:
        return object.__getattribute__(self, "_obj")[key]

    def __eq__(self, other):
        return object.__getattribute__(self, "_obj") == other

    def __bool__(self):
        return bool(object.__getattribute__(self, "_obj"))


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


class _CompatClient:
    """Wraps an MCP client/session so results auto-resolve snake/camelCase."""

    def __init__(self, raw):
        self._raw = raw

    async def list_tools(self, *args, **kwargs):
        result = await self._raw.list_tools(*args, **kwargs)
        return CompatProxy(result)

    async def call_tool(self, *args, **kwargs):
        result = await self._raw.call_tool(*args, **kwargs)
        return CompatProxy(result)

    def __getattr__(self, name):
        return getattr(self._raw, name)


@asynccontextmanager
async def create_test_client(server: Server):
    """Create an in-memory test client connected to a server.

    v1: ``create_connected_server_and_client_session``
    v2: ``mcp.client.Client``

    Results from ``list_tools()`` and ``call_tool()`` are wrapped in
    :class:`CompatProxy` so tests can use either snake_case or
    camelCase attribute names.

    Yields:
        A :class:`_CompatClient` wrapping the raw session/client.
    """
    if MCP_VERSION >= 2:
        from mcp.client import Client

        async with Client(server) as client:
            yield _CompatClient(client)
    else:
        from mcp.shared.memory import (
            create_connected_server_and_client_session as _create,
        )

        async with _create(server) as session:
            yield _CompatClient(session)


def tools_changed_notification_options():
    """Return a NotificationOptions with tools_changed=True.

    This advertises that the server supports
    notifications/tools/list_changed so clients know to listen
    for dynamic tool list updates.

    Returns:
        A NotificationOptions instance.
    """
    from mcp.server import NotificationOptions

    return NotificationOptions(tools_changed=True)


__all__ = [
    "MCP_VERSION",
    "McpErrorClass",
    "make_mcp_error",
    "get_mcp_session_info",
    "create_mcp_server",
    "supports_list_tools_cache",
    "tools_changed_notification_options",
    "get_field",
    "get_prevalidated_route",
    "trackable_session",
    "CompatProxy",
    "create_test_client",
]
