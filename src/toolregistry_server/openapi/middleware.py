"""
ETag middleware for OpenAPI server.

This module provides middleware for ETag-based cache validation,
supporting conditional requests with If-None-Match headers.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..route_table import RouteTable


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
        >>> from fastapi import FastAPI
        >>> from toolregistry_server import RouteTable
        >>> from toolregistry_server.openapi.middleware import ETagMiddleware
        >>>
        >>> app = FastAPI()
        >>> route_table = RouteTable(registry)
        >>> app.add_middleware(ETagMiddleware, route_table=route_table)

    Attributes:
        app: The ASGI application to wrap.
        route_table: The RouteTable instance for ETag generation.
        etag_paths: Set of paths that should have ETag handling.
    """

    # Paths that should have ETag handling applied
    ETAG_PATHS = frozenset({"/tools", "/openapi.json"})

    def __init__(self, app: "ASGIApp", route_table: "RouteTable") -> None:  # noqa: F821
        """Initialize the ETag middleware.

        Args:
            app: The ASGI application to wrap.
            route_table: The RouteTable instance for ETag generation.
        """
        self.app = app
        self.route_table = route_table

    async def __call__(
        self,
        scope: "Scope",  # noqa: F821
        receive: "Receive",  # noqa: F821
        send: "Send",  # noqa: F821
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

        current_etag = self.route_table.etag

        # If ETag matches, return 304 Not Modified
        if if_none_match and if_none_match == current_etag:
            await send(
                {
                    "type": "http.response.start",
                    "status": 304,
                    "headers": [
                        (b"etag", current_etag.encode("utf-8")),
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
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_etag)


def add_etag_middleware(app: "FastAPI", route_table: "RouteTable") -> None:  # noqa: F821
    """Add ETag middleware to a FastAPI application.

    This is a convenience function to add the ETagMiddleware to a FastAPI
    application with the correct configuration.

    Args:
        app: The FastAPI application instance.
        route_table: The RouteTable instance for ETag generation.

    Example:
        >>> from fastapi import FastAPI
        >>> from toolregistry_server import RouteTable
        >>> from toolregistry_server.openapi.middleware import add_etag_middleware
        >>>
        >>> app = FastAPI()
        >>> route_table = RouteTable(registry)
        >>> add_etag_middleware(app, route_table)
    """
    app.add_middleware(ETagMiddleware, route_table=route_table)


__all__ = ["ETagMiddleware", "add_etag_middleware"]
