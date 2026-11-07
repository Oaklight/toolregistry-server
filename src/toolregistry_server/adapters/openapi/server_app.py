"""OpenAPI app served by the vendored ``httpserver``.

This is the zero-dependency counterpart of
:func:`toolregistry_server.adapters.openapi.create_openapi_app`: same routes,
same payloads, no FastAPI. It is the base of epic #101's workstream 4 and is
not yet wired into the CLI or the adapter class.

Routes:

* ``POST {route.path}`` — one per :class:`~toolregistry_server.RouteEntry`
* ``GET /tools`` — tool listing with ETag validation
* ``GET /openapi.json`` — the document from
  :func:`~toolregistry_server.adapters.openapi.spec.build_openapi_schema`
* ``GET /events`` — tool change events over Server-Sent Events
* ``GET /docs`` — Swagger UI, loaded from the CDN

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
import html
import json
import traceback
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


def _tool_listing(route_table: RouteTable, etag: str) -> dict[str, Any]:
    """Build the ``/tools`` payload."""
    tools: list[dict[str, Any]] = []
    for route in route_table.list_routes(enabled_only=True, include_deferred=False):
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


def _make_tool_handler(route_table: RouteTable, tool_name: str):
    """Build the POST handler for one tool route."""

    async def _handler(request) -> Any:
        current = route_table.get_route(tool_name)
        if current is None or not current.enabled:
            return _error(503, f"Tool '{tool_name}' is currently disabled")

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

        try:
            # Validation is synchronous and CPU-bound; a large body or schema
            # would otherwise stall every request, SSE stream and meta route
            # on this loop. The default executor is enough for a short CPU
            # task, so no custom executor is installed.
            arguments = await asyncio.to_thread(_validate_arguments, current, payload)
        except Exception as exc:  # HTTPException carries the 422 detail
            status = getattr(exc, "status_code", None)
            if status is not None:
                return _error(status, getattr(exc, "detail", str(exc)))
            raise

        try:
            result = current.handler(**arguments)
            if asyncio.iscoroutine(result):
                result = await result
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


def _add_tools_route(app: App, route_table: RouteTable) -> None:
    """Register ``GET /tools`` with the same ETag semantics as FastAPI."""

    @app.get("/tools")
    async def _tools(request) -> Response:
        etag = route_table.etag
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return _json_response(_tool_listing(route_table, etag), headers={"ETag": etag})


def _add_openapi_route(
    app: App, route_table: RouteTable, title: str, version: str, description: str
) -> None:
    """Register ``GET /openapi.json`` from the RouteTable builder."""
    from .spec import build_openapi_schema

    @app.get("/openapi.json")
    async def _schema(request) -> Response:
        etag = route_table.etag
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        document = build_openapi_schema(
            route_table, title=title, version=version, description=description
        )
        return _json_response(document, headers={"ETag": etag})


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


def _add_events_route(
    app: App,
    route_table: RouteTable,
    max_subscribers: int = DEFAULT_MAX_SSE_SUBSCRIBERS,
) -> None:
    """Register ``GET /events`` streaming tool change events as SSE.

    At most *max_subscribers* clients may stream at once; further requests
    get a ``503`` instead of growing the subscriber set without bound.
    """
    # RouteTable listeners may fire on any thread (an admin request, a
    # background refresh), so each subscriber pairs its queue with the loop
    # that reads it and wakes that loop explicitly.
    subscribers: set[tuple[asyncio.Queue[str], asyncio.AbstractEventLoop]] = set()
    keepalive_interval = 30.0

    def _deliver(queue: asyncio.Queue[str], message: str) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.debug("SSE client queue full, dropping an event")

    def _on_change(tool_name: str, event: str) -> None:
        payload = json.dumps({"tool": tool_name, "event": event})
        message = f"event: tool_change\ndata: {payload}\n\n"
        for queue, loop in list(subscribers):
            try:
                loop.call_soon_threadsafe(_deliver, queue, message)
            except RuntimeError:  # loop already closed
                subscribers.discard((queue, loop))

    route_table.add_listener(_on_change)
    app.on_shutdown(lambda: route_table.remove_listener(_on_change))

    async def _generate(
        queue: asyncio.Queue[str], loop: asyncio.AbstractEventLoop
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
            subscribers.discard((queue, loop))

    @app.get("/events")
    async def _events(request) -> Response | StreamingResponse:
        if len(subscribers) >= max_subscribers:
            logger.warning(
                "SSE subscriber limit reached, rejecting a connection",
                subscribers=len(subscribers),
                limit=max_subscribers,
            )
            return _error(503, "Too many event subscribers")
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        subscribers.add((queue, loop))
        return StreamingResponse(
            _generate(queue, loop),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


def _add_auth(app: App, tokens: Sequence[str]) -> None:
    """Reject requests whose ``Authorization`` header carries no known token.

    The credential is compared against every configured token in constant
    time (see :func:`toolregistry_server.auth.verify_token`).
    """

    @app.before_request
    async def _require_token(request) -> Response | None:
        header = request.headers.get("authorization", "")
        scheme, _, credential = header.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            response = _error(401, "Not authenticated")
        elif not verify_token(credential, tokens):
            response = _error(401, "Invalid authentication token")
        else:
            return None
        response.headers["WWW-Authenticate"] = "Bearer"
        return response


_ETAG_PATHS = frozenset({"/tools", "/openapi.json"})


def _add_etag(app: App, route_table: RouteTable) -> None:
    """Tenant the ETag on the two cacheable GETs, as the FastAPI middleware did."""

    @app.after_request
    async def _etag(request, response):
        if (
            request.method == "GET"
            and request.path in _ETAG_PATHS
            and response.status_code == 200
            and "ETag" not in response.headers
        ):
            response.headers["ETag"] = route_table.etag
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

    Returns:
        An :class:`~httpserver.App` with the tool routes, ``/tools``,
        ``/openapi.json``, ``/docs`` and ``/events`` registered.

    Raises:
        ValueError: If *max_body_size* is not positive, or
            *max_sse_subscribers* is negative.
    """
    if max_body_size <= 0:
        raise ValueError(f"max_body_size must be positive, got {max_body_size}")
    if max_sse_subscribers < 0:
        raise ValueError(
            f"max_sse_subscribers must not be negative, got {max_sse_subscribers}"
        )

    app = _DetailEnvelopeApp(max_body_size=max_body_size)

    for route in route_table.list_routes(enabled_only=False):
        app.route(route.path, methods=["POST"])(
            _make_tool_handler(route_table, route.tool_name)
        )

    _add_tools_route(app, route_table)
    _add_openapi_route(app, route_table, title, version, description)
    _add_docs_route(app, title)
    _add_events_route(app, route_table, max_sse_subscribers)
    _add_framework_errors(app)

    if enable_etag:
        _add_etag(app, route_table)
    if tokens:
        _add_auth(app, tokens)
        logger.info(f"Authentication enabled with {len(tokens)} token(s)")

    return app


__all__ = ["create_httpserver_app"]
