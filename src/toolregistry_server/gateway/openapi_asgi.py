"""FastAPI ASGI assembly for the gateway's OpenAPI frontend.

This module is private to :mod:`toolregistry_server.gateway`.  It builds the
FastAPI application that the ASGI gateway mounts when it serves the
``openapi`` frontend, and it owns every FastAPI/Starlette dependency the
server package used to carry in the public OpenAPI adapter.  The standalone
OpenAPI adapter and every shared helper now run on the vendored
``httpserver``; importing this module is the only thing that requires the
``gateway`` extra.

Install it with::

    pip install toolregistry-server[gateway]

The assembly is not part of the public API.  Use
:func:`toolregistry_server.gateway.create_gateway_app` instead.
"""

import asyncio
import hashlib
import time
import traceback
from collections.abc import Callable, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any

from .._vendor.httpserver import DEFAULT_MAX_BODY_SIZE
from .._vendor.structlog import get_logger
from ..adapters.openapi.adapter import (
    DEFAULT_MAX_SSE_SUBSCRIBERS,
    RequestValidationError,
    _openapi_extensions,
    _validate_arguments,
)
from ..adapters.openapi.serialisation import encode_tool_result
from ..isolation import policy_refusal, policy_refusal_sync, record_tool_call
from ..regex_guard import (
    DEFAULT_REGEX_BUDGET,
    DEFAULT_REGEX_TIMEOUT,
    PatternMatcher,
    build_matcher_factory,
    log_status,
)
from ..route_table import RouteEntry, RouteTable
from ..scoped_discovery import (
    DISCOVERY_TOOL_NAME,
    filter_discovery_result,
    refusing_target,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from fastapi import APIRouter, FastAPI, Response

    Scope = MutableMapping[str, Any]
    Receive = Callable[[], Any]
    Send = Callable[[Any], Any]
    ASGIApp = Callable[[Scope, Receive, Send], Any]


# ---------------------------------------------------------------------------
# Request-body limit (ASGI)
# ---------------------------------------------------------------------------

#: Body of the 413 response.  Compact separators match FastAPI's
#: ``JSONResponse``, so both OpenAPI backends return the same bytes (#146).
_TOO_LARGE_BODY = b'{"detail":"Request body too large"}'


def _content_length(scope: "Scope") -> int | None:
    """Return the declared ``Content-Length``, or ``None`` when absent/invalid."""
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


async def _send_too_large(send: "Send") -> None:
    """Send the 413 response without involving the wrapped application."""
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE_BODY)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})


class BodySizeLimitMiddleware:
    """Reject request bodies larger than *max_body_size* with a 413.

    The check runs before the application sees the body.  A declared
    ``Content-Length`` above the cap is answered immediately, without reading
    anything; a chunked body, or one whose ``Content-Length`` lies, is counted
    per ASGI message as the application reads it, so the body is never
    buffered beyond what the server already produced.

    Both OpenAPI backends share :data:`DEFAULT_MAX_BODY_SIZE` and the same
    ``{"detail": ...}`` envelope.

    Args:
        app: The ASGI application to wrap.
        max_body_size: Largest request body accepted, in bytes. Must be
            positive.

    Raises:
        ValueError: If *max_body_size* is not positive.
    """

    def __init__(
        self, app: "ASGIApp", max_body_size: int = DEFAULT_MAX_BODY_SIZE
    ) -> None:
        if max_body_size <= 0:
            raise ValueError(f"max_body_size must be positive, got {max_body_size}")
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        """Apply the limit to HTTP requests and pass everything else through."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self.max_body_size:
            await _send_too_large(send)
            return

        await self._call_guarded(scope, receive, send)

    async def _call_guarded(
        self, scope: "Scope", receive: "Receive", send: "Send"
    ) -> None:
        """Run the app while counting the body it receives."""
        received = 0
        rejected = False
        response_started = False

        async def limited_receive() -> Any:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    rejected = True
                    # A response that already started cannot be replaced; the
                    # client keeps it, and later sends are dropped below.
                    if not response_started:
                        await _send_too_large(send)
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message: Any) -> None:
            nonlocal response_started
            if rejected:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            # The framework turns our http.disconnect into ClientDisconnect
            # (or a similar read error); the 413 is already on the wire.
            if not rejected:
                raise


def add_body_size_middleware(
    app: "FastAPI", max_body_size: int = DEFAULT_MAX_BODY_SIZE
) -> None:
    """Add request-body limiting to a FastAPI application.

    Args:
        app: The FastAPI application instance.
        max_body_size: Largest request body accepted, in bytes.

    Raises:
        ValueError: If *max_body_size* is not positive.
    """
    # Starlette instantiates middleware lazily, so validate now: a bad cap
    # must fail the factory call, not the first request.
    if max_body_size <= 0:
        raise ValueError(f"max_body_size must be positive, got {max_body_size}")
    app.add_middleware(BodySizeLimitMiddleware, max_body_size=max_body_size)


__all__ = [
    "BodySizeLimitMiddleware",
    "ETagMiddleware",
    "add_body_size_middleware",
    "add_etag_middleware",
]


class ETagMiddleware:
    """Middleware for ETag-based cache validation.

    This middleware intercepts requests and checks the If-None-Match header
    against the current ETag from the RouteTable. If they match, it returns
    a 304 Not Modified response. Otherwise, it adds the current ETag to the
    response headers.

    The middleware only applies ETag handling to GET requests on specific
    paths (e.g., /tools, /openapi.json) to avoid interfering with tool
    execution endpoints.

    Example:
        ```python
        from fastapi import FastAPI
        from toolregistry_server import RouteTable
        from toolregistry_server.gateway.openapi_asgi import ETagMiddleware

        app = FastAPI()
        route_table = RouteTable(registry)
        app.add_middleware(ETagMiddleware, route_table=route_table)
        ```

    Attributes:
        app: The ASGI application to wrap.
        route_table: The RouteTable instance for ETag generation.
        etag_paths: Set of paths that should have ETag handling.
    """

    # Paths that should have ETag handling applied
    ETAG_PATHS = frozenset({"/tools", "/openapi.json"})

    def __init__(
        self,
        app: "ASGIApp",
        route_table: RouteTable,
        principal_resolver: Any | None = None,
    ) -> None:
        """Initialize the ETag middleware.

        Args:
            app: The ASGI application to wrap.
            route_table: The RouteTable instance for ETag generation.
            principal_resolver: Optional API key resolver.  When set, the
                ETag is derived from the request's ``Authorization`` header
                as well, so two keys that see different tool sets cannot
                share a cached document.  Responses also carry
                ``Cache-Control: private`` and ``Vary: Authorization``.
        """
        self.app = app
        self.route_table = route_table
        self.principal_resolver = principal_resolver

    def _etag(self, scope: "Scope") -> str:
        """Return the ETag for one request.

        With a resolver configured, the credential is folded into the tag.
        The value is a digest of the header, never the token itself.
        """
        if self.principal_resolver is None:
            return self.route_table.etag
        headers = dict(scope.get("headers", []))
        credential = headers.get(b"authorization", b"")
        suffix = hashlib.sha256(credential).hexdigest()[:16]
        return f'"{self.route_table.version}:{suffix}"'

    def _scoped_headers(self) -> list[tuple[bytes, bytes]]:
        """Return the privacy headers required when the view is per-caller."""
        if self.principal_resolver is None:
            return []
        return [
            (b"cache-control", b"private"),
            (b"vary", b"Authorization"),
        ]

    async def __call__(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
    ) -> None:
        """Process the request through the middleware.

        Args:
            scope: The ASGI scope dictionary.
            receive: The ASGI receive callable.
            send: The ASGI send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        # Only apply ETag handling to GET requests on specific paths
        if method != "GET" or path not in self.ETAG_PATHS:
            await self.app(scope, receive, send)
            return

        # Extract If-None-Match header
        headers = dict(scope.get("headers", []))
        if_none_match = headers.get(b"if-none-match", b"").decode("utf-8")

        current_etag = self._etag(scope)

        # If ETag matches, return 304 Not Modified
        if if_none_match and if_none_match == current_etag:
            await send(
                {
                    "type": "http.response.start",
                    "status": 304,
                    "headers": [
                        (b"etag", current_etag.encode("utf-8")),
                        *self._scoped_headers(),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        # Otherwise, process the request and add ETag to response
        async def send_with_etag(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Add ETag header if not already present
                header_names = {h[0].lower() for h in headers}
                if b"etag" not in header_names:
                    headers.append((b"etag", current_etag.encode("utf-8")))
                for name, value in self._scoped_headers():
                    if name not in header_names:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_etag)


def add_etag_middleware(
    app: "FastAPI",
    route_table: RouteTable,
    principal_resolver: Any | None = None,
) -> None:
    """Add ETag middleware to a FastAPI application.

    This is a convenience function to add the ETagMiddleware to a FastAPI
    application with the correct configuration.

    Args:
        app: The FastAPI application instance.
        route_table: The RouteTable instance for ETag generation.
        principal_resolver: Optional API key resolver, so the ETag is
            per-credential and a shared cache cannot mix scoped documents.

    Example:
        ```python
        from fastapi import FastAPI
        from toolregistry_server import RouteTable
        from toolregistry_server.gateway.openapi_asgi import add_etag_middleware

        app = FastAPI()
        route_table = RouteTable(registry)
        add_etag_middleware(app, route_table)
        ```
    """
    app.add_middleware(
        ETagMiddleware,
        route_table=route_table,
        principal_resolver=principal_resolver,
    )


# ---------------------------------------------------------------------------
# Tool route generation
# ---------------------------------------------------------------------------


def _tool_result_response(result: Any) -> "Response":
    """Render a tool result the way the httpserver backend does.

    Both OpenAPI backends call :func:`encode_tool_result`, so non-finite
    floats and ``Decimal`` values reach the wire as ``null`` instead of a
    bare ``NaN`` or the string ``"NaN"``.  FastAPI would otherwise serialise
    the raw result through pydantic, whose JSON mode keeps a non-finite
    ``Decimal`` as a string.

    Args:
        result: The value returned by the tool.

    Returns:
        A JSON response carrying the encoded body.
    """
    from fastapi import Response

    return Response(
        content=encode_tool_result(result),
        media_type="application/json; charset=utf-8",
    )


def _require_route_scope(
    request: Any,
    route: RouteEntry,
    tool_name: str,
    principal_resolver: Any,
    registry: Any,
) -> None:
    """Refuse a route outside the caller's key scope.

    The principal was resolved by the app-level bearer dependency and stored
    on ``request.state``. A resolver without a principal denies, so a
    mis-wired app fails closed instead of open.

    Args:
        request: The incoming request carrying ``state.principal``.
        route: The route being called.
        tool_name: The route's tool name, used in the message.
        principal_resolver: The configured resolver, or ``None`` when key
            scoping is off.
        registry: The live registry, used to record the refusal in the
            execution log so the key's usage shows it.

    Raises:
        fastapi.HTTPException: ``403`` when the principal may not call the
            route.
    """
    if principal_resolver is None:
        return
    principal = getattr(request.state, "principal", None)
    if principal_resolver.allows_route(principal, route):
        return
    from fastapi import HTTPException

    error = HTTPException(
        status_code=403,
        detail=f"Tool '{tool_name}' is not permitted for this credential",
    )
    _record_denial(registry, tool_name, {}, principal, error)
    raise error


def _record_denial(
    registry: Any,
    tool_name: str,
    arguments: Any,
    principal: Any,
    error: BaseException,
    started: float | None = None,
) -> None:
    """Record one refused OpenAPI call with the caller's key."""
    record_tool_call(
        registry,
        tool_name,
        arguments,
        principal=principal,
        error=error,
        duration_ms=(0.0 if started is None else (time.monotonic() - started) * 1000),
        outcome="denied",
    )


def _filtered_result(
    tool_name: str,
    result: Any,
    principal_resolver: Any,
    principal: Any,
) -> Any:
    """Apply the discovery scope filter when the call was ``discover_tools``.

    Keeping the conditional here leaves the tool endpoints with one statement
    for every tool, so their complexity and their shared shape stay flat.

    Args:
        tool_name: The tool that ran.
        result: Its result.
        principal_resolver: The configured resolver, or ``None``.
        principal: The caller's principal, or ``None``.

    Returns:
        The result, with out-of-scope discovered tools removed.
    """
    if tool_name == DISCOVERY_TOOL_NAME:
        return filter_discovery_result(result, principal_resolver, principal)
    return result


def _require_policy_sync(
    route: Any, arguments: Any, registry: Any, principal: Any = None
) -> None:
    """Synchronous counterpart of :func:`_require_policy`.

    Raises:
        fastapi.HTTPException: ``403`` with the policy's message.
    """
    from fastapi import HTTPException

    message = policy_refusal_sync(registry, route, arguments)
    if message is not None:
        error = HTTPException(status_code=403, detail=message)
        _record_denial(registry, _route_name(route), arguments, principal, error)
        raise error


async def _require_policy(
    route: Any, arguments: Any, registry: Any, principal: Any = None
) -> None:
    """Refuse a call the process permission policy denies.

    Raises:
        fastapi.HTTPException: ``403`` with the policy's message.
    """
    from fastapi import HTTPException

    message = await policy_refusal(registry, route, arguments)
    if message is not None:
        error = HTTPException(status_code=403, detail=message)
        _record_denial(registry, _route_name(route), arguments, principal, error)
        raise error


def _route_name(route: Any) -> str:
    """Return the tool name to record for a refused route."""
    return str(getattr(route, "tool_name", getattr(route, "name", "tool")))


async def _invoke_tool(
    registry: Any,
    tool_name: str,
    handler: Any,
    arguments: Any,
    principal: Any,
) -> Any:
    """Run one async tool handler and record the call in the execution log.

    Every outcome that reached the handler is recorded, including an
    ``HTTPException`` the handler raises itself.  The httpserver backend has
    no separate path for that exception, so recording it here keeps the two
    backends agreeing on what counts as an executed call.  The exception is
    re-raised untouched for the caller's error wrapper.
    """
    started = time.monotonic()
    try:
        result = await handler(**arguments)
    except Exception as exc:
        _record(registry, tool_name, arguments, principal, started, error=exc)
        raise
    _record(registry, tool_name, arguments, principal, started, result=result)
    return result


def _invoke_tool_sync(
    registry: Any,
    tool_name: str,
    handler: Any,
    arguments: Any,
    principal: Any,
) -> Any:
    """Run one synchronous tool handler and record the call.

    See :func:`_invoke_tool` for why a handler-raised ``HTTPException`` is
    recorded like any other failure.
    """
    started = time.monotonic()
    try:
        result = handler(**arguments)
    except Exception as exc:
        _record(registry, tool_name, arguments, principal, started, error=exc)
        raise
    _record(registry, tool_name, arguments, principal, started, result=result)
    return result


def _record(
    registry: Any,
    tool_name: str,
    arguments: Any,
    principal: Any,
    started: float,
    *,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    """Append the attributed entry for a call that reached the handler."""
    record_tool_call(
        registry,
        tool_name,
        arguments,
        principal=principal,
        result=result,
        error=error,
        duration_ms=(time.monotonic() - started) * 1000,
    )


def _require_proxy_target(
    tool_name: str,
    arguments: Any,
    registry: Any,
    principal_resolver: Any,
    principal: Any,
) -> None:
    """Refuse ``call_deferred`` when its target is outside the scope.

    The detail names the target, exactly as a direct out-of-scope call to
    that tool does, so the two paths are indistinguishable to a client.

    Args:
        tool_name: The tool the client called.
        arguments: The validated call arguments.
        registry: The live registry behind the served routes.
        principal_resolver: The configured resolver, or ``None``.
        principal: The caller's principal, or ``None``.

    Raises:
        fastapi.HTTPException: ``403`` when the target is outside the scope.
    """
    target = refusing_target(
        tool_name, arguments, registry, principal_resolver, principal
    )
    if target is None:
        return
    from fastapi import HTTPException

    error = HTTPException(
        status_code=403,
        detail=f"Tool '{target}' is not permitted for this credential",
    )
    _record_denial(registry, target, arguments, principal, error)
    raise error


def _add_route_from_entry(
    router: "APIRouter",
    route: RouteEntry,
    route_table: RouteTable,
    matcher_factory: Callable[[], PatternMatcher] | None = None,
    principal_resolver: Any | None = None,
) -> None:
    """Create and register a POST route for a single RouteEntry.

    The route path is taken from the RouteEntry.path attribute.

    For async tools the handler is an ``async def``; for sync tools a plain
    ``def`` is used. A closure is used to correctly capture the loop
    variable.

    Each endpoint checks at request time whether the tool is still enabled
    via ``route_table.get_route()``. If the tool has been disabled at runtime,
    the endpoint returns HTTP 503 Service Unavailable.

    Args:
        router: The :class:`~fastapi.APIRouter` to add the route to.
        route: The :class:`~toolregistry_server.RouteEntry` to expose.
        route_table: The :class:`~toolregistry_server.RouteTable` used for
            runtime enable/disable checks.
        matcher_factory: Optional zero-argument callable returning a fresh
            bounded pattern matcher for one request. ``None`` keeps spec
            regexes out of validation.
        principal_resolver: Optional API key resolver. When set, a request
            whose principal may not call this route is refused with ``403``
            before validation or execution. ``None`` keeps the previous
            behaviour.
    """
    from fastapi import Body, HTTPException, Request

    def _wrap_tool_error(tname: str, e: Exception) -> HTTPException:
        """Convert an unhandled tool exception into a structured HTTP 500.

        The exception text stays in the server log: it can carry connection
        strings, internal paths and upstream URLs, and the client only needs
        to know which tool failed.
        """
        logger.error(
            "Tool execution failed",
            tool=tname,
            exception=traceback.format_exc(),
        )
        return HTTPException(
            status_code=500,
            detail=f"Tool '{tname}' execution failed",
        )

    summary = (route.description or "")[:120]
    extensions = _openapi_extensions(route) or {}
    # FastAPI no longer derives the request schema from a Pydantic model;
    # publish the tool's own JSON Schema so /openapi.json keeps describing
    # the body exactly as the tool declares it.
    extensions["requestBody"] = {
        "required": True,
        "content": {"application/json": {"schema": route.served_parameters_schema}},
    }
    # Use the top-level segment of the namespace as the tag for grouping
    # e.g. "web/brave_search" → tag "web", "calculator" → tag "calculator"
    namespace = route.namespace
    tags: list[str | Enum] = []
    if namespace:
        tags = [namespace.split("/")[0]]

    # Capture tool_name as a string to avoid closure-over-loop-variable issues.
    tool_name = route.tool_name
    handler = route.handler

    # Use factory functions to capture the handler, route_table, and tool_name
    # per iteration via closure.

    if route.is_async:

        def _make_async_endpoint(
            h: Any = handler,
            rt: RouteTable = route_table,
            tname: str = tool_name,
            mf: Callable[[], PatternMatcher] | None = matcher_factory,
        ):
            async def _endpoint(data: Annotated[Any, Body()], request: Request) -> Any:
                # Mark the request so the app's server-error handler can tell
                # a response-rendering failure from any other 500.
                request.state.tool_name = tname
                current_route = rt.get_route(tname)
                if current_route is None or not current_route.enabled:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Tool '{tname}' is currently disabled",
                    )
                _require_route_scope(
                    request, current_route, tname, principal_resolver, rt.registry
                )
                matcher = mf() if mf is not None else None
                # Validation is CPU-bound; keep this loop free while it runs,
                # the same way the httpserver handler does.
                arguments = await asyncio.to_thread(
                    _validate_arguments,
                    current_route,
                    data,
                    pattern_matcher=matcher,
                )
                principal = getattr(request.state, "principal", None)
                _require_proxy_target(
                    tname, arguments, rt.registry, principal_resolver, principal
                )
                await _require_policy(current_route, arguments, rt.registry, principal)
                try:
                    result = await _invoke_tool(
                        rt.registry, tname, h, arguments, principal
                    )
                except HTTPException:
                    raise
                except Exception as e:
                    raise _wrap_tool_error(tname, e) from e
                result = _filtered_result(tname, result, principal_resolver, principal)
                return _tool_result_response(result)

            return _endpoint

        router.add_api_route(
            route.path,
            _make_async_endpoint(),
            methods=["POST"],
            operation_id=route.tool_name,
            summary=summary,
            tags=tags,
            openapi_extra=extensions,
        )
    else:

        def _make_sync_endpoint(
            h: Any = handler,
            rt: RouteTable = route_table,
            tname: str = tool_name,
            mf: Callable[[], PatternMatcher] | None = matcher_factory,
        ):
            def _endpoint(data: Annotated[Any, Body()], request: Request) -> Any:
                # Mark the request so the app's server-error handler can tell
                # a response-rendering failure from any other 500.
                request.state.tool_name = tname
                current_route = rt.get_route(tname)
                if current_route is None or not current_route.enabled:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Tool '{tname}' is currently disabled",
                    )
                _require_route_scope(
                    request, current_route, tname, principal_resolver, rt.registry
                )
                matcher = mf() if mf is not None else None
                arguments = _validate_arguments(
                    current_route, data, pattern_matcher=matcher
                )
                principal = getattr(request.state, "principal", None)
                _require_proxy_target(
                    tname, arguments, rt.registry, principal_resolver, principal
                )
                _require_policy_sync(current_route, arguments, rt.registry, principal)
                try:
                    result = _invoke_tool_sync(
                        rt.registry, tname, h, arguments, principal
                    )
                except HTTPException:
                    raise
                except Exception as e:
                    raise _wrap_tool_error(tname, e) from e
                if tname == DISCOVERY_TOOL_NAME:
                    result = filter_discovery_result(
                        result, principal_resolver, principal
                    )
                return _tool_result_response(result)

            return _endpoint

        router.add_api_route(
            route.path,
            _make_sync_endpoint(),
            methods=["POST"],
            operation_id=route.tool_name,
            summary=summary,
            tags=tags,
            openapi_extra=extensions,
        )


def route_table_to_router(
    route_table: RouteTable,
    prefix: str = "",
    matcher_factory: Callable[[], PatternMatcher] | None = None,
    principal_resolver: Any | None = None,
) -> "APIRouter":
    """Convert a :class:`~toolregistry_server.RouteTable` into a FastAPI router.

    Routes are generated for **all** registered tools regardless of their
    current enabled/disabled state. Each endpoint checks the route's enabled
    state at request time and returns HTTP 503 if the tool has been disabled,
    allowing runtime enable/disable without restarting the server.

    Args:
        route_table: The route table to convert.
        prefix: URL prefix for all generated routes.
        matcher_factory: Optional bounded-matcher factory; see
            :func:`_add_route_from_entry`.
        principal_resolver: Optional API key resolver; see
            :func:`_add_route_from_entry`.

    Returns:
        A :class:`~fastapi.APIRouter` with one POST route per registered tool.

    Raises:
        ImportError: If FastAPI is not installed.
    """
    try:
        from fastapi import APIRouter
    except ImportError as e:
        raise ImportError(
            "The ASGI gateway backend requires FastAPI and uvicorn. "
            "Install with: pip install toolregistry-server[gateway]"
        ) from e

    router = APIRouter(prefix=prefix)

    for route in route_table.list_routes(enabled_only=False):
        _add_route_from_entry(
            router,
            route,
            route_table,
            matcher_factory,
            principal_resolver,
        )

    return router


# ---------------------------------------------------------------------------
# Tools list endpoint
# ---------------------------------------------------------------------------


def add_tools_endpoint(
    app: "FastAPI",
    route_table: RouteTable,
    principal_resolver: Any | None = None,
) -> None:
    """Add a /tools endpoint that returns the list of available tools.

    This endpoint supports ETag-based cache validation. The response includes
    the current ETag in both the response body and headers.

    Args:
        app: The FastAPI application instance.
        route_table: The RouteTable to query for tools.
        principal_resolver: Optional API key resolver. When set, the listing
            contains only the tools the caller's key may call.

    Example:
        ```python
        from fastapi import FastAPI
        from toolregistry_server import RouteTable
        from toolregistry_server.openapi.adapter import add_tools_endpoint

        app = FastAPI()
        route_table = RouteTable(registry)
        add_tools_endpoint(app, route_table)
        ```
    """
    try:
        from fastapi import Request
        from fastapi.responses import JSONResponse
    except ImportError as e:
        raise ImportError(
            "The ASGI gateway backend requires FastAPI and uvicorn. "
            "Install with: pip install toolregistry-server[gateway]"
        ) from e

    @app.get("/tools", tags=["meta"])
    async def list_tools(request: Request):
        """List all available tools with their metadata.

        Returns a JSON object containing:
        - tools: List of tool information (name, namespace, method, path, description)
        - etag: Current ETag for cache validation

        Supports conditional requests via If-None-Match header.
        Returns 304 Not Modified if the ETag matches.
        """
        # Check If-None-Match header
        if_none_match = request.headers.get("If-None-Match")
        current_etag = route_table.etag

        if if_none_match and if_none_match == current_etag:
            return JSONResponse(
                content=None,
                status_code=304,
                headers={"ETag": current_etag},
            )

        # Build tools list — deferred tools are excluded from the initial
        # listing so that LLMs discover them via discover_tools.
        routes = route_table.list_routes(enabled_only=True, include_deferred=False)
        if principal_resolver is not None:
            principal = getattr(request.state, "principal", None)
            routes = principal_resolver.filter_routes(principal, routes)
        tools = []
        for route in routes:
            tool_info: dict[str, Any] = {
                "name": route.tool_name,
                "namespace": route.namespace,
                "method": route.method_name,
                "path": route.path,
                "description": route.description,
            }
            if route.schema_hash:
                tool_info["schema_hash"] = route.schema_hash
            if route.last_refreshed_at:
                tool_info["last_refreshed_at"] = route.last_refreshed_at
            tools.append(tool_info)

        return JSONResponse(
            content={"tools": tools, "etag": current_etag},
            headers={"ETag": current_etag},
        )


# ---------------------------------------------------------------------------
# SSE event stream
# ---------------------------------------------------------------------------


def _sse_listener(
    queues: dict[Any, Any], route_table: RouteTable, principal_resolver: Any
) -> Callable[[str, str], None]:
    """Return the RouteTable listener that fans change events out as SSE.

    A scoped client is not told about a tool outside its key's scope: a name
    alone is information it could not obtain from ``tools/list``. ``"*"`` (a
    full refresh) carries no name, so it goes to everyone.
    """
    import asyncio
    import json as _json

    def _on_change(tool_name: str, event: str) -> None:
        payload = _json.dumps({"tool": tool_name, "event": event})
        # TODO: SSE events do not include an ``id:`` field, so clients
        # cannot resume from a Last-Event-ID after reconnection.
        msg = f"event: tool_change\ndata: {payload}\n\n"
        for q, principal in list(queues.items()):
            if principal_resolver is not None and tool_name != "*":
                route = route_table.get_route(tool_name)
                if route is None or not principal_resolver.allows_route(
                    principal, route
                ):
                    continue
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                logger.debug("SSE client queue full, dropping event", tool=tool_name)

    return _on_change


def _sse_endpoint(
    queues: dict[Any, Any],
    principal_resolver: Any,
    max_subscribers: int,
):
    """Return the ``/events`` handler for the configured subscriber set."""
    import asyncio

    from fastapi import HTTPException, Request
    from starlette.responses import StreamingResponse

    keepalive = 30.0

    async def sse_events(request: Request):
        """Stream tool change events via Server-Sent Events."""
        if len(queues) >= max_subscribers:
            logger.warning(
                "SSE subscriber limit reached, rejecting a connection",
                subscribers=len(queues),
                max_subscribers=max_subscribers,
            )
            raise HTTPException(status_code=503, detail="Too many event subscribers")
        principal = getattr(request.state, "principal", None)
        if principal_resolver is not None and principal is None:
            raise HTTPException(status_code=403, detail="Not authenticated")
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        queues[q] = principal

        async def _generate():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=keepalive)
                        yield msg
                    except asyncio.TimeoutError:
                        # Send SSE comment as keepalive to prevent reverse
                        # proxies from closing idle connections.
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                return
            finally:
                queues.pop(q, None)

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return sse_events


def add_events_endpoint(
    app: "FastAPI",
    route_table: RouteTable,
    *,
    max_subscribers: int = DEFAULT_MAX_SSE_SUBSCRIBERS,
    principal_resolver: Any | None = None,
) -> None:
    """Add a GET /events SSE endpoint that streams tool change events.

    Each connected client receives server-sent events whenever a tool is
    registered, unregistered, enabled, disabled, or otherwise modified.

    Event format::

        event: tool_change
        data: {"tool": "search", "event": "register"}

    Args:
        app: The FastAPI application instance.
        route_table: The RouteTable whose changes are broadcast.
        max_subscribers: Largest number of concurrent streams. Further
            connections get ``503``. ``0`` disables the endpoint.
        principal_resolver: Optional API key resolver. When set, a stream
            receives change events only for tools its key may call.

    Raises:
        ValueError: If *max_subscribers* is negative.
    """
    if max_subscribers < 0:
        raise ValueError(f"max_subscribers must not be negative, got {max_subscribers}")

    queues: dict[Any, Any] = {}
    route_table.add_listener(_sse_listener(queues, route_table, principal_resolver))
    app.get("/events", tags=["meta"], include_in_schema=True)(
        _sse_endpoint(queues, principal_resolver, max_subscribers)
    )


# ---------------------------------------------------------------------------
# Dynamic OpenAPI schema generation
# ---------------------------------------------------------------------------


def setup_dynamic_openapi(
    app: "FastAPI",
    route_table: RouteTable,
    principal_resolver: Any | None = None,
    openapi_auth: Any | None = None,
) -> None:
    """Serve ``/openapi.json`` from the RouteTable itself.

    The document is built by
    :func:`~toolregistry_server.adapters.openapi.spec.build_openapi_schema`,
    so disabled tools are absent by construction and no post-filtering or
    ``get_openapi`` monkeypatching is needed. Nothing is cached: the schema
    is regenerated on every request, which is what makes enable/disable
    show up immediately.

    When *principal_resolver* is set, the built-in ``/openapi.json`` route is
    replaced by one that filters the document through the caller's key scope.
    The app-level bearer dependency has already stored the principal on
    ``request.state``; a missing principal yields an empty tool section, so a
    scoped document never discloses a path the key cannot call.

    Args:
        app: The FastAPI application instance.
        route_table: The route table to describe.
        principal_resolver: Optional API key resolver.
        openapi_auth: Optional document-security builder (an
            :class:`~toolregistry_server.oauth.OpenAPIAuth`).  When set, its
            schemes and requirements replace the legacy bearer-only
            document.
    """
    from ..adapters.openapi.spec import build_openapi_schema

    def _document_security() -> tuple[Any, Any]:
        if openapi_auth is not None:
            return openapi_auth.build()
        return None, None

    def custom_openapi() -> dict[str, Any]:
        security_schemes, security = _document_security()
        return build_openapi_schema(
            route_table,
            title=app.title,
            version=app.version,
            description=app.description,
            security_schemes=security_schemes,
            security=security,
        )

    app.openapi = custom_openapi  # ty: ignore[invalid-assignment]

    if principal_resolver is None:
        return

    from fastapi import Request
    from fastapi.responses import JSONResponse

    openapi_path = getattr(app, "openapi_url", None) or "/openapi.json"
    # FastAPI registers the built-in document route during construction.
    # Remove it so the request-aware route below is the only handler for the
    # path; otherwise the first match would serve an unfiltered document.
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) != openapi_path
    ]

    @app.get(openapi_path, include_in_schema=False)
    async def _scoped_openapi(request: Request) -> JSONResponse:
        principal = getattr(request.state, "principal", None)
        security_schemes, security = _document_security()
        if openapi_auth is None:
            security_schemes = {"HTTPBearer": {"type": "http", "scheme": "bearer"}}
            security = [{"HTTPBearer": []}]
        document = build_openapi_schema(
            route_table,
            title=app.title,
            version=app.version,
            description=app.description,
            security_schemes=security_schemes,
            security=security,
            route_filter=lambda route: principal_resolver.allows_route(
                principal, route
            ),
        )
        return JSONResponse(content=document)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_openapi_app(
    route_table: RouteTable,
    title: str = "ToolRegistry Server",
    version: str = "1.0.0",
    description: str = "OpenAPI server for ToolRegistry tools",
    dependencies: Sequence[Any] | None = None,
    enable_etag: bool = True,
    max_sse_subscribers: int | None = None,
    max_body_size: int = DEFAULT_MAX_BODY_SIZE,
    regex_timeout: float = DEFAULT_REGEX_TIMEOUT,
    regex_budget: float = DEFAULT_REGEX_BUDGET,
    principal_resolver: Any | None = None,
    openapi_auth: Any | None = None,
) -> "FastAPI":
    """Create a FastAPI application from a RouteTable.

    Args:
        route_table: The RouteTable to expose.
        title: API title for OpenAPI schema.
        version: API version for OpenAPI schema.
        description: API description for OpenAPI schema.
        dependencies: Optional list of global dependencies (e.g., authentication).
        enable_etag: Whether to enable ETag middleware for cache validation.
            Defaults to True.
        max_sse_subscribers: Largest number of concurrent ``/events``
            streams. Further connections get ``503``. ``None`` keeps the
            default (64); ``0`` disables the endpoint.
        max_body_size: Largest request body accepted, in bytes. Larger bodies
            return ``413`` with ``{"detail": "Request body too large"}``.
            Defaults to 1 MiB, the same as the httpserver backend.
        regex_timeout: Per-match ceiling for spec-supplied regexes, in
            seconds. Only used when the optional ``regex`` extra is
            installed; ``0`` disables execution and restores the behaviour
            of ignoring spec patterns.
        regex_budget: Per-request ceiling for all spec-supplied regex
            matches, in seconds. ``0`` disables execution.
        principal_resolver: Optional API key resolver.  When set, tool
            routes, ``/tools``, ``/openapi.json`` and ``/events`` are all
            filtered through the caller's key scope.  The caller installs
            the app-level dependency returned by
            :func:`toolregistry_server.auth.create_scoped_bearer_dependency`.
            ``None`` keeps the previous behaviour.
        openapi_auth: Optional document-security builder (an
            :class:`~toolregistry_server.oauth.OpenAPIAuth`).  When set, the
            served document describes the OAuth2 scheme and, when keys or
            static tokens are also accepted, the OR of both credentials.

    Returns:
        A configured FastAPI application with:
        - Tool routes from the RouteTable
        - /tools endpoint for listing available tools
        - ETag middleware for cache validation (if enabled)
        - A request-body limit shared with the httpserver backend
        - Dynamic OpenAPI schema that filters disabled tools

    Raises:
        ImportError: If FastAPI is not installed.
        ValueError: If *max_body_size* is not positive, or either regex
            bound is negative or not finite.
    """
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
    except ImportError as e:
        raise ImportError(
            "The ASGI gateway backend requires FastAPI and uvicorn. "
            "Install with: pip install toolregistry-server[gateway]"
        ) from e

    # Create app with optional global dependencies
    if dependencies:
        app = FastAPI(
            title=title,
            version=version,
            description=description,
            dependencies=list(dependencies),
        )
    else:
        app = FastAPI(
            title=title,
            version=version,
            description=description,
        )

    async def _server_error(request: Request, exc: Exception) -> JSONResponse:
        """Render an unexpected failure in the httpserver's JSON envelope.

        FastAPI's default is a plain-text 500, which leaves the client unable
        to read the reason and makes the two backends disagree when a tool
        result cannot be serialised.  The httpserver answers with
        ``{"detail": ...}``; this keeps the same shape.  ``tool_name`` is set
        by the endpoint, so an exception raised while rendering a successful
        result is reported as a serialisation failure.

        The traceback goes to the log, matching the httpserver path; the
        client only gets the stable reason.
        """
        tool_name = getattr(request.state, "tool_name", None)
        if tool_name:
            logger.exception("Tool result is not serialisable", tool=tool_name)
            detail = f"Tool '{tool_name}' result is not serialisable"
        else:
            logger.exception(
                "Unhandled error while rendering a response",
                path=request.url.path,
            )
            detail = "Internal Server Error"
        return JSONResponse({"detail": detail}, status_code=500)

    app.add_exception_handler(Exception, _server_error)

    async def _request_validation_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Render a shared validation failure in the same 422 envelope.

        ``_validate_arguments`` is backend-neutral and raises
        :class:`RequestValidationError`; FastAPI's own ``HTTPException``
        handler would drop the status code it carries.  The handler
        signature takes ``Exception`` because that is the contract Starlette
        types; the registered class narrows it at runtime.
        """
        assert isinstance(exc, RequestValidationError)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    app.add_exception_handler(RequestValidationError, _request_validation_error)

    # Add ETag middleware for cache validation
    if enable_etag:
        add_etag_middleware(app, route_table, principal_resolver)

    # Add /tools endpoint for listing available tools
    add_tools_endpoint(app, route_table, principal_resolver)

    # Add /events SSE endpoint for streaming tool change events
    if max_sse_subscribers is None:
        add_events_endpoint(app, route_table, principal_resolver=principal_resolver)
    else:
        add_events_endpoint(
            app,
            route_table,
            max_subscribers=max_sse_subscribers,
            principal_resolver=principal_resolver,
        )

    # Add routes from route table
    matcher_factory = build_matcher_factory(regex_timeout, regex_budget)
    log_status(logger, regex_timeout, regex_budget, matcher_factory is not None)
    router = route_table_to_router(
        route_table,
        matcher_factory=matcher_factory,
        principal_resolver=principal_resolver,
    )
    app.include_router(router)

    # Setup dynamic OpenAPI schema that filters disabled tools
    setup_dynamic_openapi(app, route_table, principal_resolver, openapi_auth)

    # Added last so it wraps every other middleware: an oversized body is
    # rejected before routing, auth or ETag short-circuits see it.
    add_body_size_middleware(app, max_body_size=max_body_size)

    return app
