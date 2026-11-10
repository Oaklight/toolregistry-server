"""OpenAPI app served by the vendored ``httpserver``.

This is the zero-dependency server behind the standalone
``toolregistry-server openapi`` command and the httpserver gateway backend.
It serves the same routes and payloads as
:func:`toolregistry_server.gateway.openapi_asgi.create_openapi_app`, without
FastAPI.

Routes:

* ``POST {route.path}`` — one per :class:`~toolregistry_server.RouteEntry`
* ``GET /tools`` — tool listing with ETag validation
* ``GET /openapi.json`` — the document from
  :func:`~toolregistry_server.adapters.openapi.spec.build_openapi_schema`
* ``GET /events`` — tool change events over Server-Sent Events
* ``GET /docs`` — Swagger UI, loaded from the CDN
* ``GET /redoc`` — ReDoc, loaded from the CDN

A tool registered after the app is built is reachable without a restart:
the route table's change listener appends one POST route to the vendored
app's plain route list.  A removed tool keeps its route and answers 503,
the same as the FastAPI backend's stale route.

Example:
    ```python
    from toolregistry_server.adapters.openapi.server_app import (
        create_httpserver_app,
    )

    app = create_httpserver_app(route_table)
    app.run(host="127.0.0.1", port=8000)
    ```
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
import threading
import time
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from ..._vendor.httpserver import (
    DEFAULT_MAX_BODY_SIZE,
    App,
    Response,
    StreamingResponse,
)
from ..._vendor.structlog import get_logger
from ...auth import verify_token
from ...isolation import (
    identity_header,
    policy_refusal,
    principal_key,
    record_tool_call,
)
from ...regex_guard import (
    DEFAULT_REGEX_BUDGET,
    DEFAULT_REGEX_TIMEOUT,
    PatternMatcher,
    build_matcher_factory,
    log_status,
)
from ...scoped_discovery import (
    DISCOVERY_TOOL_NAME,
    filter_discovery_result,
    refusing_target,
)
from .adapter import DEFAULT_MAX_SSE_SUBSCRIBERS, _validate_arguments
from .serialisation import encode_tool_result

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...route_table import RouteTable

logger = get_logger()

#: Swagger UI release served to ``/docs``.  Pinned, not a floating major
#: range: the CDN copy must be the exact asset whose integrity hash is below.
SWAGGER_UI_VERSION = "5.34.1"
SWAGGER_UI_CSS_URL = (
    f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css"
)
SWAGGER_UI_CSS_INTEGRITY = (
    "sha384-4c3vsE5f+trhFyHrFrLM1fFGUn531NIaC7dcqEWIJdoRQJ5CkJbSuSCwXLpaS1KF"
)
SWAGGER_UI_BUNDLE_URL = (
    f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"
    "/swagger-ui-bundle.js"
)
SWAGGER_UI_BUNDLE_INTEGRITY = (
    "sha384-XFYN47VzPiDY6qPtxYKg0nqUeW/upo20xf1j2SUE7gT/i35FJB4cuS/71Iegzk8Z"
)

#: ReDoc release served to ``/redoc``.  Pinned and integrity-checked for the
#: same reason as the Swagger UI assets above.
REDOC_VERSION = "2.5.0"
REDOC_BUNDLE_URL = (
    f"https://cdn.jsdelivr.net/npm/redoc@{REDOC_VERSION}/bundles/redoc.standalone.js"
)
REDOC_BUNDLE_INTEGRITY = (
    "sha384-4vOjrBu7SuDWXcAw1qFznVLA/sKL+0l4nn+J1HY8w7cpa6twQEYuh4b0Cwuo7CyX"
)


def _json_response(data: Any, status: int = 200, headers: dict | None = None):
    """Serialize *data* the way FastAPI's ``JSONResponse`` does."""
    return Response(
        encode_tool_result(data),
        status_code=status,
        headers=headers,
        content_type="application/json",
    )


def _error(status: int, detail: Any) -> Response:
    """Build the ``{"detail": ...}`` envelope FastAPI clients expect."""
    return _json_response({"detail": detail}, status=status)


def _request_etag(route_table: RouteTable, principal_resolver: Any, request) -> str:
    """Return the ETag for one request.

    A scoped request folds a digest of its credential into the tag so two
    keys that see different tool sets cannot share a cached document.  The
    header value is a digest, never the token.
    """
    if principal_resolver is None:
        return route_table.etag
    credential = request.headers.get("authorization", "")
    suffix = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:16]
    return f'"{route_table.version}:{suffix}"'


def _scope_headers(principal_resolver: Any) -> dict[str, str]:
    """Return the privacy headers required when the view is per-caller."""
    if principal_resolver is None:
        return {}
    return {"Cache-Control": "private", "Vary": "Authorization"}


def _tool_listing(
    route_table: RouteTable,
    etag: str,
    principal_resolver: Any | None = None,
    principal: Any | None = None,
) -> dict[str, Any]:
    """Build the ``/tools`` payload, filtered by the caller's key scope."""
    routes = route_table.list_routes(enabled_only=True, include_deferred=False)
    if principal_resolver is not None:
        routes = principal_resolver.filter_routes(principal, routes)
    tools: list[dict[str, Any]] = []
    for route in routes:
        info: dict[str, Any] = {
            "name": route.tool_name,
            "namespace": route.namespace,
            "method": route.method_name,
            "path": route.path,
            "description": route.description,
        }
        if route.schema_hash:
            info["schema_hash"] = route.schema_hash
        if route.last_refreshed_at:
            info["last_refreshed_at"] = route.last_refreshed_at
        tools.append(info)
    return {"tools": tools, "etag": etag}


def _scope_refusal(
    request: Any,
    current: Any,
    tool_name: str,
    principal_resolver: Any | None,
    registry: Any,
) -> Response | None:
    """Return the ``403`` response when the route is outside the scope."""
    if principal_resolver is None:
        return None
    principal = getattr(request.state, "principal", None)
    if principal_resolver.allows_route(principal, current):
        return None
    message = f"Tool '{tool_name}' is not permitted for this credential"
    _record_denial(registry, tool_name, {}, principal, message)
    return _error(403, message)


def _proxy_refusal(
    request: Any,
    tool_name: str,
    arguments: Any,
    registry: Any,
    principal_resolver: Any | None,
) -> Response | None:
    """Return the ``403`` response when a proxy's target is out of scope.

    The detail names the target, exactly as a direct out-of-scope call to
    that tool does, so the two paths are indistinguishable to a client.
    """
    principal = getattr(request.state, "principal", None)
    target = refusing_target(
        tool_name, arguments, registry, principal_resolver, principal
    )
    if target is None:
        return None
    message = f"Tool '{target}' is not permitted for this credential"
    _record_denial(registry, target, arguments, principal, message)
    return _error(403, message)


def _record_denial(
    registry: Any,
    tool_name: str,
    arguments: Any,
    principal: Any,
    message: str,
) -> None:
    """Record one refused call so the key's usage counts it."""
    record_tool_call(
        registry,
        tool_name,
        arguments,
        principal=principal,
        error=ValueError(message),
        outcome="denied",
    )


async def _authorisation_refusal(
    request: Any,
    tool_name: str,
    arguments: Any,
    registry: Any,
    principal_resolver: Any,
    route: Any,
) -> Response | None:
    """Return the response for a call the scope or the process policy refuses."""
    refusal = _proxy_refusal(
        request, tool_name, arguments, registry, principal_resolver
    )
    if refusal is not None:
        return refusal
    message = await policy_refusal(registry, route, arguments)
    if message is None:
        return None
    _record_denial(
        registry,
        tool_name,
        arguments,
        getattr(request.state, "principal", None),
        message,
    )
    return _error(403, message)


def _filtered_result(
    request: Any,
    tool_name: str,
    result: Any,
    principal_resolver: Any | None,
) -> Any:
    """Apply the discovery scope filter when the call was ``discover_tools``."""
    if tool_name != DISCOVERY_TOOL_NAME:
        return result
    principal = getattr(request.state, "principal", None)
    return filter_discovery_result(result, principal_resolver, principal)


async def _execute_and_record(
    registry: Any,
    tool_name: str,
    handler: Callable[..., Any],
    arguments: dict[str, Any],
    principal: Any,
) -> Any:
    """Run one tool handler and record the call in the execution log.

    A handler may be a coroutine function or a plain function returning a
    coroutine; both are awaited here.  The exception is recorded before it is
    re-raised, so the caller's 500 response and the log agree.
    """
    started = time.monotonic()
    try:
        result = handler(**arguments)
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:
        record_tool_call(
            registry,
            tool_name,
            arguments,
            principal=principal,
            error=exc,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        raise
    record_tool_call(
        registry,
        tool_name,
        arguments,
        principal=principal,
        result=result,
        duration_ms=(time.monotonic() - started) * 1000,
    )
    return result


def _make_tool_handler(
    route_table: RouteTable,
    tool_name: str,
    matcher_factory: Callable[[], PatternMatcher] | None = None,
    principal_resolver: Any | None = None,
):
    """Build the POST handler for one tool route."""

    async def _handler(request) -> Any:
        current = route_table.get_route(tool_name)
        if current is None or not current.enabled:
            return _error(503, f"Tool '{tool_name}' is currently disabled")

        refusal = _scope_refusal(
            request, current, tool_name, principal_resolver, route_table.registry
        )
        if refusal is not None:
            return refusal

        try:
            payload = request.json()
        except ValueError:
            return _error(
                422,
                [
                    {
                        "loc": ["body", 1],
                        "msg": "JSON decode error",
                        "type": "json_invalid",
                    }
                ],
            )

        matcher = matcher_factory() if matcher_factory is not None else None
        try:
            # Validation is synchronous and CPU-bound; a large body or schema
            # would otherwise stall every request, SSE stream and meta route
            # on this loop. The default executor is enough for a short CPU
            # task, so no custom executor is installed.
            arguments = await asyncio.to_thread(
                _validate_arguments, current, payload, pattern_matcher=matcher
            )
        except Exception as exc:  # RequestValidationError carries them
            status = getattr(exc, "status_code", None)
            if status is not None:
                return _error(status, getattr(exc, "detail", str(exc)))
            raise

        refusal = await _authorisation_refusal(
            request,
            tool_name,
            arguments,
            route_table.registry,
            principal_resolver,
            current,
        )
        if refusal is not None:
            return refusal

        try:
            result = await _execute_and_record(
                route_table.registry,
                tool_name,
                current.handler,
                arguments,
                getattr(request.state, "principal", None),
            )
        except Exception:
            # Keep the exception text in the log; it can carry connection
            # strings, paths and upstream URLs.  The client only needs the
            # tool name.
            logger.error(
                "Tool execution failed",
                tool=tool_name,
                exception=traceback.format_exc(),
            )
            return _error(500, f"Tool '{tool_name}' execution failed")

        result = _filtered_result(request, tool_name, result, principal_resolver)

        try:
            # Compact separators and no ASCII escaping match FastAPI's
            # JSONResponse byte for byte.
            body = encode_tool_result(result)
        except (TypeError, ValueError):
            # ``json.dumps`` raises TypeError for a value with no JSON
            # projection and ValueError for a circular reference.  Keep the
            # exception text in the log; a custom ``to_dict`` / ``model_dump``
            # may raise with data in the message.  The client gets a stable
            # reason.
            logger.error(
                "Tool result is not serialisable",
                tool=tool_name,
                exception=traceback.format_exc(),
            )
            return _error(500, f"Tool '{tool_name}' result is not serialisable")
        return Response(body, content_type="application/json; charset=utf-8")

    return _handler


def _add_tools_route(
    app: App, route_table: RouteTable, principal_resolver: Any | None = None
) -> None:
    """Register ``GET /tools`` with the same ETag semantics as FastAPI."""

    @app.get("/tools")
    async def _tools(request) -> Response:
        etag = _request_etag(route_table, principal_resolver, request)
        headers = {"ETag": etag, **_scope_headers(principal_resolver)}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        principal = getattr(request.state, "principal", None)
        return _json_response(
            _tool_listing(route_table, etag, principal_resolver, principal),
            headers=headers,
        )


def _add_openapi_route(
    app: App,
    route_table: RouteTable,
    title: str,
    version: str,
    description: str,
    principal_resolver: Any | None = None,
    openapi_auth: Any | None = None,
) -> None:
    """Register ``GET /openapi.json`` from the RouteTable builder."""
    from .spec import build_openapi_schema

    @app.get("/openapi.json")
    async def _schema(request) -> Response:
        etag = _request_etag(route_table, principal_resolver, request)
        headers = {"ETag": etag, **_scope_headers(principal_resolver)}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        principal = getattr(request.state, "principal", None)
        security_schemes: dict[str, Any] | None = None
        security: list[dict[str, list[str]]] | None = None
        if openapi_auth is not None:
            security_schemes, security = openapi_auth.build()
        elif principal_resolver is not None:
            security_schemes = {"HTTPBearer": {"type": "http", "scheme": "bearer"}}
            security = [{"HTTPBearer": []}]
        document = build_openapi_schema(
            route_table,
            title=title,
            version=version,
            description=description,
            security_schemes=security_schemes,
            security=security,
            route_filter=(
                None
                if principal_resolver is None
                else lambda route: principal_resolver.allows_route(principal, route)
            ),
        )
        return _json_response(document, headers=headers)


def _add_docs_route(app: App, title: str) -> None:
    """Register ``GET /docs`` serving Swagger UI from the CDN.

    The release is pinned and both assets carry an integrity hash, so a
    changed CDN response is refused by the browser instead of executed.
    The title is operator configuration and is HTML-escaped.
    """
    safe_title = html.escape(title)
    html_page = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{safe_title} - Swagger UI</title>
  <link rel="stylesheet"
        href="{SWAGGER_UI_CSS_URL}"
        integrity="{SWAGGER_UI_CSS_INTEGRITY}"
        crossorigin="anonymous">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{SWAGGER_UI_BUNDLE_URL}"
          integrity="{SWAGGER_UI_BUNDLE_INTEGRITY}"
          crossorigin="anonymous"></script>
  <script>
    window.ui = SwaggerUIBundle({{url: "/openapi.json", dom_id: "#swagger-ui"}});
  </script>
</body>
</html>"""

    @app.get("/docs")
    async def _docs(request) -> Response:
        return Response(html_page, content_type="text/html; charset=utf-8")


def _add_redoc_route(app: App, title: str) -> None:
    """Register ``GET /redoc`` serving ReDoc from the CDN.

    Mirrors FastAPI's default ``redoc_url``: the page points at
    ``/openapi.json``.  The release is pinned and the bundle carries an
    integrity hash, so a changed CDN response is refused by the browser
    instead of executed.  The title is operator configuration and is
    HTML-escaped.
    """
    safe_title = html.escape(title)
    html_page = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} - ReDoc</title>
</head>
<body>
  <redoc spec-url="/openapi.json"></redoc>
  <script src="{REDOC_BUNDLE_URL}"
          integrity="{REDOC_BUNDLE_INTEGRITY}"
          crossorigin="anonymous"></script>
</body>
</html>"""

    @app.get("/redoc")
    async def _redoc(request) -> Response:
        return Response(html_page, content_type="text/html; charset=utf-8")


def _httpserver_sse_listener(
    subscribers: set[tuple[asyncio.Queue[str], asyncio.AbstractEventLoop, Any]],
    route_table: RouteTable,
    principal_resolver: Any | None,
) -> Callable[[str, str], None]:
    """Return the RouteTable listener that fans change events out as SSE."""

    def _deliver(queue: asyncio.Queue[str], message: str) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.debug("SSE client queue full, dropping an event")

    def _on_change(tool_name: str, event: str) -> None:
        payload = json.dumps({"tool": tool_name, "event": event})
        message = f"event: tool_change\ndata: {payload}\n\n"
        for queue, loop, principal in list(subscribers):
            if principal_resolver is not None and tool_name != "*":
                route = route_table.get_route(tool_name)
                if route is None or not principal_resolver.allows_route(
                    principal, route
                ):
                    continue
            try:
                loop.call_soon_threadsafe(_deliver, queue, message)
            except RuntimeError:  # loop already closed
                subscribers.discard((queue, loop, principal))

    return _on_change


def _httpserver_sse_endpoint(
    subscribers: set[tuple[asyncio.Queue[str], asyncio.AbstractEventLoop, Any]],
    max_subscribers: int,
    principal_resolver: Any | None,
):
    """Return the ``/events`` handler for the configured subscriber set."""
    keepalive_interval = 30.0

    async def _generate(
        queue: asyncio.Queue[str],
        loop: asyncio.AbstractEventLoop,
        principal: Any,
    ) -> AsyncIterator[str]:
        try:
            while True:
                try:
                    yield await asyncio.wait_for(
                        queue.get(), timeout=keepalive_interval
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    # SSE comment keeps proxies from closing an idle stream.
                    yield ": keepalive\n\n"
        finally:
            subscribers.discard((queue, loop, principal))

    async def _events(request) -> Response | StreamingResponse:
        if len(subscribers) >= max_subscribers:
            logger.warning(
                "SSE subscriber limit reached, rejecting a connection",
                subscribers=len(subscribers),
                limit=max_subscribers,
            )
            return _error(503, "Too many event subscribers")
        principal = getattr(request.state, "principal", None)
        if principal_resolver is not None and principal is None:
            return _error(403, "Not authenticated")
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        subscribers.add((queue, loop, principal))
        return StreamingResponse(
            _generate(queue, loop, principal),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return _events


def _add_events_route(
    app: App,
    route_table: RouteTable,
    max_subscribers: int = DEFAULT_MAX_SSE_SUBSCRIBERS,
    principal_resolver: Any | None = None,
) -> None:
    """Register ``GET /events`` streaming tool change events as SSE.

    At most *max_subscribers* clients may stream at once; further requests
    get a ``503`` instead of growing the subscriber set without bound.  When
    a resolver is configured, a stream receives events only for tools its
    key may call; ``"*"`` (a full refresh) carries no name and goes to all.
    """
    # RouteTable listeners may fire on any thread (an admin request, a
    # background refresh), so each subscriber pairs its queue with the loop
    # that reads it and wakes that loop explicitly.
    subscribers: set[tuple[asyncio.Queue[str], asyncio.AbstractEventLoop, Any]] = set()
    listener = _httpserver_sse_listener(subscribers, route_table, principal_resolver)
    route_table.add_listener(listener)
    app.on_shutdown(lambda: route_table.remove_listener(listener))
    app.get("/events")(
        _httpserver_sse_endpoint(subscribers, max_subscribers, principal_resolver)
    )


def _add_auth(
    app: App,
    tokens: Sequence[str],
    principal_resolver: Any | None = None,
) -> None:
    """Reject requests whose ``Authorization`` header carries no known token.

    The credential is compared against every configured token in constant
    time (see :func:`toolregistry_server.auth.verify_token`).  When a
    principal resolver is configured, the token is resolved through the API
    key store instead, and the caller's principal is stored on the request
    for the route and document handlers.
    """

    @app.before_request
    async def _require_token(request) -> Response | None:
        header = request.headers.get("authorization", "")
        scheme, _, credential = header.partition(" ")
        principal = None
        if scheme.lower() != "bearer" or not credential:
            response = _error(401, "Not authenticated")
        elif principal_resolver is not None:
            principal = await asyncio.to_thread(principal_resolver.resolve, credential)
            response = (
                None
                if principal is not None
                else _error(401, "Invalid authentication token")
            )
        elif not verify_token(credential, tokens):
            response = _error(401, "Invalid authentication token")
        else:
            response = None
        if response is not None:
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        if principal_resolver is not None:
            request.state.principal = principal
        claimed = identity_header(getattr(request, "headers", None))
        if claimed is not None:
            # Identity comes only from the credential; a shared server must
            # not let a client name itself, its cwd or its environment.
            logger.warning(
                "refused a reserved client header",
                header=claimed,
                client_key=(
                    principal_key(principal)
                    if principal is not None
                    else "static-token"
                ),
                path=getattr(request, "path", ""),
            )
            return _error(
                400,
                f"{claimed} is not accepted: identity comes from the bearer credential",
            )
        return None


_ETAG_PATHS = frozenset({"/tools", "/openapi.json"})


def _add_etag(
    app: App, route_table: RouteTable, principal_resolver: Any | None = None
) -> None:
    """Tenant the ETag on the two cacheable GETs, as the FastAPI middleware did."""

    @app.after_request
    async def _etag(request, response):
        if (
            request.method == "GET"
            and request.path in _ETAG_PATHS
            and response.status_code == 200
            and "ETag" not in response.headers
        ):
            response.headers["ETag"] = _request_etag(
                route_table, principal_resolver, request
            )
        for name, value in _scope_headers(principal_resolver).items():
            response.headers.setdefault(name, value)
        return response


def _add_framework_errors(app: App) -> None:
    """Answer unmatched routes and methods with FastAPI's envelope."""

    @app.errorhandler(404)
    async def _not_found(request, exc):
        return _error(404, "Not Found")

    @app.errorhandler(405)
    async def _not_allowed(request, exc):
        return _error(405, "Method Not Allowed")


class _DetailEnvelopeApp(App):
    """An :class:`App` that renders read-phase ``413`` bodies as ``detail``.

    The vendored server rejects an oversized body while reading it, before
    dispatch, so :meth:`App.errorhandler` never sees the ``413``.  Rewriting
    the body just before it is written keeps the vendored drain and close
    logic untouched and gives transport errors the same top-level key as the
    adapter's ``404`` and ``405`` (issue #146).
    """

    async def _send_error_and_close(
        self,
        writer,
        response,
        reader=None,
        exc=None,
        state=None,
    ) -> None:
        if getattr(response, "status_code", None) == 413:
            response.body = json.dumps(
                {"detail": "Request body too large"},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            response.headers["Content-Type"] = "application/json"
        await super()._send_error_and_close(writer, response, reader, exc, state)


def _add_tool_routes(
    app: App,
    route_table: RouteTable,
    matcher_factory: Callable[[], PatternMatcher] | None,
    principal_resolver: Any | None,
) -> None:
    """Register one POST route per tool, including tools registered later.

    The vendored app keeps its routes in a plain list, so a tool the
    registry gains after startup is one append rather than a rebuild.  The
    change listener may fire on any thread; a lock keeps two concurrent
    registrations from adding the same path twice.  A removed tool keeps its
    route and answers ``503``, the same as FastAPI's stale route.
    """
    lock = threading.Lock()
    registered: set[str] = set()

    def _register(tool_name: str) -> None:
        with lock:
            if tool_name in registered:
                return
            route = route_table.get_route(tool_name)
            if route is None:
                return
            app.route(route.path, methods=["POST"])(
                _make_tool_handler(
                    route_table,
                    tool_name,
                    matcher_factory,
                    principal_resolver,
                )
            )
            registered.add(tool_name)

    def _on_change(tool_name: str, event: str) -> None:
        if tool_name == "*":
            for route in route_table.list_routes(enabled_only=False):
                _register(route.tool_name)
            return
        _register(tool_name)

    # Subscribe before the seed loop: a change that lands between the loop
    # and the subscription would otherwise miss its route.  ``_register`` is
    # idempotent, so the overlap is harmless.
    route_table.add_listener(_on_change)
    for route in route_table.list_routes(enabled_only=False):
        _register(route.tool_name)

    def _remove_listener() -> None:
        with contextlib.suppress(ValueError):  # removed by a second shutdown
            route_table.remove_listener(_on_change)

    app.on_shutdown(_remove_listener)


def create_httpserver_app(
    route_table: RouteTable,
    *,
    title: str = "ToolRegistry Server",
    version: str = "1.0.0",
    description: str = "OpenAPI server for ToolRegistry tools",
    tokens: Sequence[str] | None = None,
    enable_etag: bool = True,
    max_body_size: int = DEFAULT_MAX_BODY_SIZE,
    max_sse_subscribers: int = DEFAULT_MAX_SSE_SUBSCRIBERS,
    regex_timeout: float = DEFAULT_REGEX_TIMEOUT,
    regex_budget: float = DEFAULT_REGEX_BUDGET,
    principal_resolver: Any | None = None,
    openapi_auth: Any | None = None,
) -> App:
    """Build a :class:`~httpserver.App` exposing the RouteTable.

    Args:
        route_table: The RouteTable to expose.
        title: API title in the OpenAPI document.
        version: API version in the OpenAPI document.
        description: API description in the OpenAPI document.
        tokens: Bearer tokens accepted by the server. ``None`` or an empty
            sequence disables authentication.
        enable_etag: Add a content ETag to successful responses.
        max_body_size: Largest request body accepted, in bytes. A body above
            this returns ``413`` with a JSON body. Defaults to 1 MiB.
        max_sse_subscribers: Largest number of concurrent ``/events``
            streams. Further connections get ``503``. ``0`` disables the
            endpoint. Defaults to 64.
        regex_timeout: Per-match ceiling for spec-supplied regexes, in
            seconds. Requires the optional ``regex`` extra; ``0`` disables
            execution and keeps spec patterns out of validation.
        regex_budget: Per-request ceiling for all spec-supplied regex
            matches, in seconds. ``0`` disables execution.
        principal_resolver: Optional API key resolver. When set, key
            authentication runs through the SQLite store and the caller's
            scope filters tool routes, ``/tools``, ``/openapi.json`` and
            ``/events``. Static *tokens* keep full, unscoped access.
        openapi_auth: Optional document-security builder (an
            :class:`~toolregistry_server.oauth.OpenAPIAuth`).  When set, its
            schemes and requirements replace the legacy bearer-only
            document; ``None`` keeps the previous behaviour.

    Returns:
        An :class:`~httpserver.App` with the tool routes, ``/tools``,
        ``/openapi.json``, ``/docs``, ``/redoc`` and ``/events``
        registered.  A tool registered after this call is reachable through
        a route the change listener appends.

    Raises:
        ValueError: If *max_body_size* is not positive, or either regex
            bound is negative or not finite, or *max_sse_subscribers* is
            negative.
    """
    if max_body_size <= 0:
        raise ValueError(f"max_body_size must be positive, got {max_body_size}")
    if max_sse_subscribers < 0:
        raise ValueError(
            f"max_sse_subscribers must not be negative, got {max_sse_subscribers}"
        )

    matcher_factory = build_matcher_factory(regex_timeout, regex_budget)
    log_status(logger, regex_timeout, regex_budget, matcher_factory is not None)

    app = _DetailEnvelopeApp(max_body_size=max_body_size)

    _add_tool_routes(app, route_table, matcher_factory, principal_resolver)

    _add_tools_route(app, route_table, principal_resolver)
    _add_openapi_route(
        app,
        route_table,
        title,
        version,
        description,
        principal_resolver,
        openapi_auth,
    )
    _add_docs_route(app, title)
    _add_redoc_route(app, title)
    _add_events_route(app, route_table, max_sse_subscribers, principal_resolver)
    _add_framework_errors(app)

    if enable_etag:
        _add_etag(app, route_table, principal_resolver)
    if tokens or principal_resolver is not None:
        _add_auth(app, tokens or (), principal_resolver)
        if principal_resolver is not None:
            logger.info("API key scoping enabled; static tokens keep full access")
        else:
            logger.info(f"Authentication enabled with {len(tokens or ())} token(s)")

    return app


__all__ = ["create_httpserver_app"]
