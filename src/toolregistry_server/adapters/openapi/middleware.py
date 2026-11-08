"""
ASGI middleware for the OpenAPI server.

* :class:`ETagMiddleware` adds ETag-based cache validation, supporting
  conditional requests with If-None-Match headers.
* :class:`BodySizeLimitMiddleware` caps request bodies so the FastAPI and
  httpserver backends share one resource limit and one 413 envelope.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from ..._vendor.httpserver import DEFAULT_MAX_BODY_SIZE

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

    from fastapi import FastAPI

    from ...route_table import RouteTable

    Scope = MutableMapping[str, Any]
    Receive = Callable[[], Any]
    Send = Callable[[Any], Any]
    ASGIApp = Callable[[Scope, Receive, Send], Any]

#: Body of the 413 response.  Compact separators match FastAPI's
#: ``JSONResponse``, so both OpenAPI backends return the same bytes (#146).
_TOO_LARGE_BODY = b'{"detail":"Request body too large"}'


def _content_length(scope: Scope) -> int | None:
    """Return the declared ``Content-Length``, or ``None`` when absent/invalid."""
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


async def _send_too_large(send: Send) -> None:
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
        self, app: ASGIApp, max_body_size: int = DEFAULT_MAX_BODY_SIZE
    ) -> None:
        if max_body_size <= 0:
            raise ValueError(f"max_body_size must be positive, got {max_body_size}")
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Apply the limit to HTTP requests and pass everything else through."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self.max_body_size:
            await _send_too_large(send)
            return

        await self._call_guarded(scope, receive, send)

    async def _call_guarded(self, scope: Scope, receive: Receive, send: Send) -> None:
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
    app: FastAPI, max_body_size: int = DEFAULT_MAX_BODY_SIZE
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
        from toolregistry_server.openapi.middleware import ETagMiddleware

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
        app: ASGIApp,
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

    def _etag(self, scope: Scope) -> str:
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
        scope: Scope,
        receive: Receive,
        send: Send,
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
    app: FastAPI,
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
        from toolregistry_server.openapi.middleware import add_etag_middleware

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
