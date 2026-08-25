"""MCP SDK v1/v2 compatibility layer.

Abstracts the breaking changes between mcp v1.x and v2.x so that the
adapter and test code works with either version.  When we eventually
implement our own MCP SDK, only this file needs a new backend.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Coroutine
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


def create_mcp_server(
    name: str,
    *,
    list_tools_handler: ListToolsHandler,
    call_tool_handler: CallToolHandler,
    list_tools_ttl_ms: int | None = None,
    list_tools_cache_scope: Literal["public", "private"] | None = None,
) -> Server:
    """Create an MCP lowlevel Server with handlers registered.

    Our handlers use a stable internal signature; this function adapts
    them to whatever the installed SDK version expects.

    Args:
        name: Server name for MCP identification.
        list_tools_handler: ``async () -> list[Tool]``
        call_tool_handler: ``async (name, arguments) -> list[ContentBlock]``
        list_tools_ttl_ms: Optional cache lifetime in milliseconds for
            ``tools/list`` responses (MCP spec 2026-07-28). Ignored on v1.
        list_tools_cache_scope: Cache scope for ``tools/list``. Ignored on v1.

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
        )
    else:
        return _create_server_v1(name, list_tools_handler, call_tool_handler)


def _create_server_v1(
    name: str,
    list_tools_handler: ListToolsHandler,
    call_tool_handler: CallToolHandler,
) -> Server:
    from mcp.server.lowlevel import Server

    server = Server(name)

    @server.list_tools()
    async def _list_tools() -> list:
        return await list_tools_handler()

    @server.call_tool(validate_input=False)
    async def _call_tool(tool_name: str, arguments: dict) -> Any:
        content, structured = await call_tool_handler(tool_name, arguments)
        if structured is not None:
            return content, structured
        return content

    return server


def _create_server_v2(
    name: str,
    list_tools_handler: ListToolsHandler,
    call_tool_handler: CallToolHandler,
    list_tools_ttl_ms: int | None = None,
    list_tools_cache_scope: Literal["public", "private"] | None = None,
) -> Server:
    from mcp.server.lowlevel import Server
    from mcp.types import CallToolResult, ListToolsResult

    _cache_supported = supports_list_tools_cache()

    # list_tools does not set _v2_request_ctx because list_tools
    # handlers don't need session context.
    async def on_list_tools(ctx: Any, params: Any) -> Any:
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
        except Exception as e:
            # In v2, MCPError raised from on_call_tool becomes a JSON-RPC
            # protocol error (client raises instead of getting is_error=True).
            # Catch everything and return as is_error=True to preserve v1
            # behavior where errors are LLM-visible tool results.
            return CallToolResult(  # type: ignore[call-arg]
                content=[TextContent(type="text", text=str(e))],
                is_error=True,
            )
        finally:
            _v2_request_ctx.reset(token)

    return Server(  # type: ignore[call-arg]
        name,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


# ---------------------------------------------------------------------------
# Field accessor
# ---------------------------------------------------------------------------


def make_mcp_tool(
    name: str,
    description: str,
    schema: dict,
    output_schema: dict | None = None,
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
    return Tool(name=name, description=description, **kwargs)


def make_resource_link(uri: str, name: str, **kwargs: Any) -> Any:
    """Build a ``ResourceLink`` content block with version-correct fields.

    ``mime_type`` is the v2 field name and ``mimeType`` the v1 alias; callers
    pass ``mime_type`` and this normalizes it.
    """
    from mcp.types import ResourceLink

    mime_type = kwargs.pop("mime_type", None)
    if mime_type is not None:
        kwargs["mime_type" if MCP_VERSION >= 2 else "mimeType"] = mime_type
    return ResourceLink(type="resource_link", uri=uri, name=name, **kwargs)


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


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def create_test_client(server: Server):
    """Create an in-memory test client connected to a server.

    v1: ``create_connected_server_and_client_session``
    v2: ``mcp.client.Client``

    Yields:
        A session/client object with ``list_tools()`` and ``call_tool()``
        methods.
    """
    if MCP_VERSION >= 2:
        from mcp.client import Client

        async with Client(server) as client:
            yield client
    else:
        from mcp.shared.memory import (
            create_connected_server_and_client_session as _create,
        )

        async with _create(server) as session:
            yield session


__all__ = [
    "MCP_VERSION",
    "McpErrorClass",
    "make_mcp_error",
    "get_mcp_session_info",
    "create_mcp_server",
    "supports_list_tools_cache",
    "get_field",
    "create_test_client",
]
