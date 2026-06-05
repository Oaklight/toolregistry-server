"""MCP server runner functions.

This module provides functions to run an MCP server with different transports:
- stdio: Standard input/output transport
- SSE: Server-Sent Events over HTTP
- streamable-http: Streamable HTTP transport

The server should be created using route_table_to_mcp_server() from adapter.py,
then run using the functions in this module.

Example:
    ```python
    from toolregistry import ToolRegistry
    from toolregistry_server import RouteTable
    from toolregistry_server.mcp import route_table_to_mcp_server, run_stdio

    registry = ToolRegistry()
    route_table = RouteTable(registry)
    server = route_table_to_mcp_server(route_table)
    asyncio.run(run_stdio(server))
    ```
"""

import asyncio
from typing import TYPE_CHECKING

from .._vendor.structlog import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server


def _make_http_exception_handlers() -> dict:
    """Return Starlette exception_handlers that emit JSON-RPC-shaped errors.

    Claude Code probes OAuth discovery endpoints (``.well-known/oauth-*``)
    before connecting to remote MCP servers. The error response must NOT
    use OAuth 2.0 error format (``error`` + ``error_description`` fields
    per RFC 6749 §5.2), because Claude Code would interpret that as
    "OAuth is present but misconfigured" rather than "no OAuth needed".

    Instead we use a plain JSON format that is clearly non-OAuth, so
    the client correctly treats the 404 as "no OAuth required".
    """
    from starlette.exceptions import HTTPException
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    async def _json_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
        )

    return {404: _json_http_error, 405: _json_http_error}


async def run_stdio(server: "Server") -> None:
    """Run an MCP server over stdio transport.

    This is the simplest transport, suitable for local tool execution
    where the MCP client spawns the server as a subprocess.

    Args:
        server: The MCP Server instance to run.
    """
    try:
        from mcp.server.stdio import stdio_server
    except ImportError as e:
        raise ImportError(
            "MCP SDK is required for MCP support. "
            "Install with: pip install toolregistry-server[mcp]"
        ) from e

    logger.info("Starting MCP server with stdio transport")
    try:
        async with stdio_server() as (read, write):
            await server.run(
                read,
                write,
                server.create_initialization_options(),
            )
    except KeyboardInterrupt:
        logger.info("MCP stdio server shutdown requested (KeyboardInterrupt)")
    except asyncio.CancelledError:
        logger.info("MCP stdio server shutdown requested (CancelledError)")


async def run_sse(
    server: "Server",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/sse",
) -> None:
    """Run an MCP server over SSE (Server-Sent Events) transport.

    This transport is suitable for web-based MCP clients that connect
    via HTTP and receive events through SSE.

    Args:
        server: The MCP Server instance to run.
        host: Host address to bind to.
        port: Port number to bind to.
        path: URL path for the SSE endpoint.
    """
    try:
        import uvicorn
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import Response
        from starlette.routing import Mount, Route
    except ImportError as e:
        raise ImportError(
            "MCP SDK and Starlette are required for SSE transport. "
            "Install with: pip install toolregistry-server[mcp] starlette uvicorn"
        ) from e

    logger.info(f"Starting MCP server with SSE transport on {host}:{port}{path}")

    # Create SSE transport
    sse = SseServerTransport(f"{path}/messages/")

    # SSE endpoint handler - must accept Request and return Response
    # See MCP SDK documentation for the correct pattern
    async def handle_sse(request: Request) -> Response:
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )
        # Return empty response to avoid NoneType error when client disconnects
        return Response()

    # Create Starlette app
    routes = [
        Route(path, endpoint=handle_sse, methods=["GET"]),
        Mount(f"{path}/messages/", app=sse.handle_post_message),
    ]
    app = Starlette(routes=routes, exception_handlers=_make_http_exception_handlers())

    # Run with uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uvicorn_server = uvicorn.Server(config)

    try:
        await uvicorn_server.serve()
    except KeyboardInterrupt:
        logger.info("MCP SSE server shutdown requested (KeyboardInterrupt)")
    except asyncio.CancelledError:
        logger.info("MCP SSE server shutdown requested (CancelledError)")


async def run_streamable_http(
    server: "Server",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
    valid_tokens: set[str] | None = None,
    server_url: str | None = None,
) -> None:
    """Run an MCP server over streamable HTTP transport.

    This is the recommended HTTP transport for production use,
    supporting bidirectional streaming over HTTP.

    When ``valid_tokens`` is provided, the server enables Bearer token
    authentication using the MCP SDK's auth infrastructure.  Clients
    must include ``Authorization: Bearer <token>`` in every request.
    The server publishes RFC 9728 Protected Resource Metadata at
    ``/.well-known/oauth-protected-resource<path>`` so that
    MCP clients (including Claude Code) can discover the auth
    requirements.

    Args:
        server: The MCP Server instance to run.
        host: Host address to bind to.
        port: Port number to bind to.
        path: URL path for the MCP endpoint.
        valid_tokens: Optional set of accepted Bearer tokens.
            When ``None``, authentication is disabled.
        server_url: Public URL of this server (e.g.
            ``https://example.com/mcp``).  Required when
            ``valid_tokens`` is set so that Protected Resource
            Metadata can be generated.  Defaults to
            ``http://{host}:{port}{path}``.
    """
    try:
        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.types import Receive, Scope, Send
    except ImportError as e:
        raise ImportError(
            "MCP SDK and Starlette are required for streamable HTTP transport. "
            "Install with: pip install toolregistry-server[mcp] starlette uvicorn"
        ) from e

    logger.info(
        f"Starting MCP server with streamable HTTP transport on {host}:{port}{path}"
    )

    # Create session manager
    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=False,
        stateless=False,
    )

    # Create ASGI application class for StreamableHTTP
    # This is necessary because Starlette Route treats ASGI apps differently
    # from regular endpoint functions - ASGI apps receive all HTTP methods
    class StreamableHTTPASGIApp:
        """ASGI application wrapper for StreamableHTTP session manager."""

        def __init__(self, manager: StreamableHTTPSessionManager):
            self.manager = manager

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await self.manager.handle_request(scope, receive, send)

    streamable_http_app = StreamableHTTPASGIApp(session_manager)

    # Build routes and middleware
    routes: list[Route] = []
    middleware: list = []

    if valid_tokens:
        from mcp.server.auth.middleware.bearer_auth import (
            BearerAuthBackend,
            RequireAuthMiddleware,
        )
        from mcp.server.auth.routes import (
            build_resource_metadata_url,
            create_protected_resource_routes,
        )
        from pydantic import AnyHttpUrl
        from starlette.middleware import Middleware
        from starlette.middleware.authentication import AuthenticationMiddleware

        from .token_verifier import StaticTokenVerifier

        token_verifier = StaticTokenVerifier(valid_tokens)

        # Resolve the public server URL
        resolved_url = server_url or f"http://{host}:{port}{path}"
        resource_url = AnyHttpUrl(resolved_url)

        # The issuer_url points to ourselves; we don't run a real AS,
        # but it's required by the Protected Resource Metadata spec.
        issuer_url = AnyHttpUrl(resolved_url.rsplit(path, 1)[0] or resolved_url)

        resource_metadata_url = build_resource_metadata_url(resource_url)

        # Starlette middleware: extract Bearer token from every request
        middleware = [
            Middleware(
                AuthenticationMiddleware, backend=BearerAuthBackend(token_verifier)
            ),
        ]

        # Wrap the MCP endpoint with RequireAuthMiddleware
        routes.append(
            Route(
                path,
                endpoint=RequireAuthMiddleware(
                    streamable_http_app, ["mcp"], resource_metadata_url
                ),
            )
        )

        # Publish Protected Resource Metadata (RFC 9728)
        routes.extend(
            create_protected_resource_routes(
                resource_url=resource_url,
                authorization_servers=[issuer_url],
                scopes_supported=["mcp"],
            )
        )

        logger.info(
            f"Bearer token authentication enabled ({len(valid_tokens)} token(s))"
        )
    else:
        routes.append(Route(path, endpoint=streamable_http_app))

    # Create Starlette app with lifespan
    app = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lambda app: session_manager.run(),
        exception_handlers=_make_http_exception_handlers(),
    )

    # Run with uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uvicorn_server = uvicorn.Server(config)

    try:
        await uvicorn_server.serve()
    except KeyboardInterrupt:
        logger.info("MCP streamable HTTP server shutdown requested (KeyboardInterrupt)")
    except asyncio.CancelledError:
        logger.info("MCP streamable HTTP server shutdown requested (CancelledError)")
