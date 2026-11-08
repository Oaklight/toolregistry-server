"""One ASGI process serving OpenAPI, MCP streamable-HTTP and MCP SSE.

The gateway mounts three frontends on distinct, configurable paths.  Each
request is matched against the current frontend paths, and the frontend's
enable flag is read at that moment.  A disabled frontend returns ``404``
before its ASGI application runs, so it cannot reach the registry or execute
a tool.  Updating a flag takes effect on the next request; the MCP session
manager is not rebuilt.

The MCP streamable-HTTP transport owns one session manager.  It is created
when the ASGI lifespan starts and closed when the lifespan exits (or when
:meth:`GatewayAdapter.shutdown` runs after an interrupted serve call).

Example:
    ```python
    from toolregistry_server import App

    app = App()
    gateway = app.create_gateway(config_path="tools.yaml")
    gateway.update_frontend("sse", enabled=False)
    gateway.run(host="127.0.0.1", port=8000)
    ```
"""

from __future__ import annotations

import argparse
import asyncio
import threading
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Literal

from .._vendor.httpserver import DEFAULT_MAX_BODY_SIZE
from .._vendor.structlog import get_logger
from ..adapters import Adapter
from ..adapters.openapi.adapter import DEFAULT_MAX_SSE_SUBSCRIBERS
from ..auth import load_tokens, verify_token
from ..identity import ServerIdentity
from ..regex_guard import DEFAULT_REGEX_BUDGET, DEFAULT_REGEX_TIMEOUT
from .frontends import (
    DEFAULT_PATHS,
    FRONTEND_NAMES,
    TRANSPORTS,
    Frontend,
    FrontendRegistry,
    normalize_path,
)
from .persistence import load_frontend_settings, persist_frontend_settings

logger = get_logger()

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

    from ..route_table import RouteTable


def _positive_int(text: str) -> int:
    """Parse a byte count that must be greater than zero.

    Args:
        text: Command-line value.

    Returns:
        The parsed integer.

    Raises:
        argparse.ArgumentTypeError: If the value is not a positive integer.
    """
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {text!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of bytes")
    return value


def _non_negative_float(text: str) -> float:
    """Parse a finite, non-negative number of seconds.

    Args:
        text: Command-line value.

    Returns:
        The parsed float.  ``0`` disables the bound.

    Raises:
        argparse.ArgumentTypeError: If the value is negative or not finite.
    """
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid float value: {text!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("must be a finite number of seconds")
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def resolve_frontends(
    stored: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
) -> list[Frontend]:
    """Merge stored frontend settings with explicit CLI overrides.

    Args:
        stored: Settings read by :func:`load_frontend_settings`.
        overrides: Keyword arguments that may contain ``<name>_path`` and
            ``<name>_enabled`` for each frontend.  ``None`` means "no
            override".

    Returns:
        One resolved :class:`Frontend` per name, in stable order.

    Raises:
        ValueError: If a path or enable override is invalid.
    """
    records: list[Frontend] = []
    for name in FRONTEND_NAMES:
        entry = stored.get(name, {})
        path_override = overrides.get(f"{name}_path")
        enabled_override = overrides.get(f"{name}_enabled")

        path = (
            path_override
            if path_override is not None
            else entry.get("path", DEFAULT_PATHS[name])
        )
        if enabled_override is None:
            enabled = entry.get("enabled", True)
        else:
            if not isinstance(enabled_override, bool):
                raise ValueError(
                    f"{name} enable override must be a boolean, "
                    f"got {type(enabled_override).__name__}"
                )
            enabled = enabled_override

        records.append(
            Frontend(
                name=name,
                path=normalize_path(path),
                enabled=bool(enabled),
                transport=TRANSPORTS[name],
            )
        )
    return records


def _sub_scope(scope: Scope, prefix: str) -> Scope:
    """Return a copy of *scope* with *prefix* removed from the path.

    The mounted application sees the path below its mount point, the same
    way Starlette's ``Mount`` rewrites the scope.  ``root_path`` keeps the
    prefix so generated URLs still point at the public path.

    Args:
        scope: Incoming ASGI scope.
        prefix: Matched frontend path.

    Returns:
        A new scope mapping.
    """
    rewritten = dict(scope)
    path = scope.get("path", "")
    rewritten["path"] = path[len(prefix) :] or "/"
    raw_path = scope.get("raw_path")
    if raw_path is not None:
        rewritten["raw_path"] = raw_path[len(prefix.encode("utf-8")) :] or b"/"
    root_path = scope.get("root_path", "")
    rewritten["root_path"] = root_path + prefix
    return rewritten


async def _send_json(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    payload: Any,
    headers: dict[str, str] | None = None,
) -> None:
    """Send a JSON response.

    Args:
        scope: ASGI scope.
        receive: ASGI receive callable.
        send: ASGI send callable.
        status: HTTP status code.
        payload: JSON-serialisable response body.
        headers: Extra response headers.
    """
    from starlette.responses import JSONResponse

    response = JSONResponse(payload, status_code=status, headers=headers)
    await response(scope, receive, send)


class _BearerAuthASGI:
    """ASGI wrapper that enforces one bearer-token set.

    The same wrapper protects every frontend, which is what makes the
    gateway's auth configuration shared.

    Args:
        app: The wrapped ASGI application.
        tokens: Accepted bearer tokens.
    """

    def __init__(self, app: Any, tokens: tuple[str, ...]) -> None:
        self._app = app
        self._tokens = tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        header = ""
        for key, value in scope.get("headers", []):
            if key.lower() == b"authorization":
                header = value.decode("latin-1")
                break
        scheme, _, credential = header.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            await _send_json(
                scope,
                receive,
                send,
                401,
                {"detail": "Not authenticated"},
                {"WWW-Authenticate": "Bearer"},
            )
            return
        if not verify_token(credential, self._tokens):
            await _send_json(
                scope,
                receive,
                send,
                401,
                {"detail": "Invalid authentication token"},
                {"WWW-Authenticate": "Bearer"},
            )
            return
        await self._app(scope, receive, send)


class _McpStreamableASGI:
    """ASGI wrapper around the current streamable-HTTP session manager.

    The manager is created at lifespan startup, so the wrapper looks it up
    per request instead of holding a reference from construction time.

    Args:
        gateway: The owning gateway adapter.
    """

    def __init__(self, gateway: GatewayAdapter) -> None:
        self._gateway = gateway

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        manager = self._gateway._session_manager
        if manager is None:
            await _send_json(
                scope,
                receive,
                send,
                503,
                {"detail": "MCP session manager is not running"},
            )
            return
        await manager.handle_request(scope, receive, send)


class _GatewayASGI:
    """ASGI application that dispatches requests to gateway frontends.

    Args:
        gateway: The owning gateway adapter.
    """

    def __init__(self, gateway: GatewayAdapter) -> None:
        self._gateway = gateway

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(scope, receive, send)
        elif scope["type"] == "http":
            await self._http(scope, receive, send)
        else:
            # No websocket frontend is hosted.
            await send({"type": "websocket.close", "code": 1000})

    async def _lifespan(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the gateway startup and shutdown hooks."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self._gateway.startup()
                except Exception as exc:
                    logger.error(
                        "Gateway startup failed",
                        exception=str(exc),
                    )
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": str(exc),
                        }
                    )
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    await self._gateway.shutdown()
                except Exception as exc:
                    logger.error(
                        "Gateway shutdown failed",
                        exception=str(exc),
                    )
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Dispatch one HTTP request to its frontend."""
        gateway = self._gateway
        path = scope.get("path", "")

        if await gateway._serve_metadata(scope, receive, send):
            return

        frontend = gateway._registry.match(path)
        if frontend is None:
            await _send_json(scope, receive, send, 404, {"detail": "Not Found"})
            return
        if not frontend.enabled:
            await _send_json(
                scope,
                receive,
                send,
                404,
                {"detail": f"frontend {frontend.name!r} is disabled"},
            )
            return

        app = gateway._frontend_app(frontend.name)
        await app(_sub_scope(scope, frontend.path), receive, send)


class GatewayAdapter(Adapter):
    """Serve the OpenAPI and MCP frontends from one ASGI application.

    Args:
        route_table: The RouteTable shared by every frontend.
        identity: Server identity used for the OpenAPI title and MCP name.
        frontends: Resolved frontend records.
        tokens: Bearer tokens accepted by every frontend.  ``None`` or an
            empty sequence disables authentication.
        backend: ``"fastapi"`` (ASGI) or ``"httpserver"``.  The httpserver
            backend can serve only the openapi frontend.
        config_path: Configuration file used to persist frontend settings.
        write_config: Whether :meth:`update_frontend` may write the config
            file.
        server_url: Public URL of the MCP endpoint, used in protected
            resource metadata.
        host: Bind host, also used to derive metadata URLs.
        port: Bind port, also used to derive metadata URLs.
        max_body_size: Largest request body accepted by the OpenAPI
            frontend.
        max_sse_subscribers: Largest number of concurrent OpenAPI
            ``/events`` streams.
        regex_timeout: Per-match ceiling for spec-supplied regexes.
        regex_budget: Per-request ceiling for spec-supplied regexes.
        tools_list_ttl_ms: ``ttlMs`` hint for MCP ``tools/list``.
        tools_list_cache_scope: ``cacheScope`` hint for MCP ``tools/list``.
        instructions: MCP ``initialize`` instructions.

    Raises:
        ValueError: If a frontend path is invalid, two frontends share a
            path, or the backend cannot serve the enabled frontend set.
    """

    def __init__(
        self,
        route_table: RouteTable,
        *,
        identity: ServerIdentity | None = None,
        frontends: list[Frontend] | None = None,
        tokens: list[str] | tuple[str, ...] | None = None,
        backend: str = "fastapi",
        config_path: str | None = None,
        write_config: bool = True,
        server_url: str | None = None,
        host: str = "0.0.0.0",
        port: int = 8000,
        max_body_size: int = DEFAULT_MAX_BODY_SIZE,
        max_sse_subscribers: int = DEFAULT_MAX_SSE_SUBSCRIBERS,
        regex_timeout: float = DEFAULT_REGEX_TIMEOUT,
        regex_budget: float = DEFAULT_REGEX_BUDGET,
        tools_list_ttl_ms: int | None = None,
        tools_list_cache_scope: Literal["public", "private"] | None = None,
        instructions: str | None = None,
    ) -> None:
        super().__init__(route_table)
        if backend not in ("fastapi", "httpserver"):
            raise ValueError(
                f"Unknown gateway backend {backend!r}; "
                "choose from ('fastapi', 'httpserver')"
            )
        self._identity = identity or ServerIdentity()
        self._backend = backend
        self._config_path = config_path
        self._write_config = write_config
        self._tokens = tuple(tokens or ())
        self._server_url = server_url
        self._host = host
        self._port = port
        self._max_body_size = max_body_size
        self._max_sse_subscribers = max_sse_subscribers
        self._regex_timeout = regex_timeout
        self._regex_budget = regex_budget
        self._tools_list_ttl_ms = tools_list_ttl_ms
        self._tools_list_cache_scope = tools_list_cache_scope
        self._instructions = instructions

        locked = frozenset({"openapi"}) if backend == "httpserver" else frozenset()
        self._registry = FrontendRegistry(frontends, locked_names=locked)
        self._update_lock = threading.RLock()
        self._validate_backend()

        self._openapi_app = self._build_openapi_app()
        self._mcp_server: Any = (
            None if backend == "httpserver" else self._build_mcp_server()
        )

        self._session_manager: Any = None
        self._exit_stack: AsyncExitStack | None = None
        self._http_openapi_app = self._wrap_auth(self._openapi_app)
        self._mcp_asgi = _McpStreamableASGI(self)
        self._http_mcp_app = self._wrap_auth(self._mcp_asgi)
        self._sse_app: Any = None
        self._sse_path: str | None = None
        self._sse_wrapped: Any = None
        self._asgi = _GatewayASGI(self)

        logger.info(
            "Gateway frontends: "
            + ", ".join(
                f"{record.name}={record.path} ({'on' if record.enabled else 'off'})"
                for record in self._registry.snapshot()
            )
        )

    def _validate_backend(self) -> None:
        """Reject a backend that cannot serve the enabled frontends.

        Raises:
            ValueError: If the httpserver backend is asked to serve anything
                other than the openapi frontend alone.
        """
        if self._backend != "httpserver":
            return
        enabled = [record.name for record in self._registry.enabled_snapshot()]
        if len(enabled) > 1:
            raise ValueError(
                "gateway mode requires the ASGI backend when more than one "
                "frontend is enabled; the httpserver backend cannot mount "
                "multiple frontends. Use --backend fastapi."
            )
        if enabled != ["openapi"]:
            raise ValueError(
                "the httpserver backend can serve only the openapi frontend; "
                f"enabled frontend(s): {enabled or ['<none>']}. "
                "Use --backend fastapi to serve MCP transports."
            )

    def _build_openapi_app(self) -> Any:
        """Build the OpenAPI application for the configured backend."""
        identity = self._identity
        if self._backend == "httpserver":
            from ..adapters.openapi.server_app import create_httpserver_app

            return create_httpserver_app(
                self._route_table,
                title=identity.name,
                version=identity.version,
                description=identity.description,
                tokens=self._tokens or None,
                max_body_size=self._max_body_size,
                max_sse_subscribers=self._max_sse_subscribers,
                regex_timeout=self._regex_timeout,
                regex_budget=self._regex_budget,
            )

        from ..adapters.openapi import create_openapi_app

        return create_openapi_app(
            self._route_table,
            title=identity.name,
            version=identity.version,
            description=identity.description,
            max_sse_subscribers=self._max_sse_subscribers,
            max_body_size=self._max_body_size,
            regex_timeout=self._regex_timeout,
            regex_budget=self._regex_budget,
        )

    def _build_mcp_server(self) -> Any:
        """Build the MCP low-level server shared by both MCP frontends."""
        from ..adapters.mcp.adapter import route_table_to_mcp_server

        return route_table_to_mcp_server(
            self._route_table,
            self._identity.name,
            list_tools_ttl_ms=self._tools_list_ttl_ms,
            list_tools_cache_scope=self._tools_list_cache_scope,
            version=self._identity.version,
            instructions=self._instructions,
        )

    def _wrap_auth(self, app: Any) -> Any:
        """Wrap *app* with the shared bearer-token check when configured."""
        if not self._tokens:
            return app
        return _BearerAuthASGI(app, self._tokens)

    @property
    def app(self) -> Any:
        """The application instance for the configured backend.

        For the ASGI backend this is the gateway dispatcher; for the
        httpserver backend it is the vendored OpenAPI application.
        """
        if self._backend == "httpserver":
            return self._openapi_app
        return self._asgi

    def list_frontends(self) -> list[dict[str, Any]]:
        """Return the current frontend configuration.

        Returns:
            A list of records with ``name``, ``path``, ``enabled`` and
            ``transport`` keys, in stable order.
        """
        return [record.as_dict() for record in self._registry.snapshot()]

    def update_frontend(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        """Change one frontend and optionally persist the change.

        The change applies to the next request.  The MCP session manager is
        never rebuilt.  When the configuration file cannot be written, the
        in-memory change still applies and the result carries a warning.

        Args:
            name: Frontend name.
            enabled: New enable flag, or ``None`` to keep the current value.
            path: New mount path, or ``None`` to keep the current value.

        Returns:
            The updated record plus ``persisted`` and ``warning``.

        Raises:
            ValueError: If *name* is unknown, no change is requested, a value
                is invalid, or the backend cannot apply the change.
        """
        if enabled is None and path is None:
            raise ValueError("update_frontend requires 'enabled' or 'path'")
        if self._backend == "httpserver":
            if name != "openapi":
                raise ValueError(
                    "the httpserver backend serves only the openapi frontend"
                )
            if enabled is False:
                raise ValueError(
                    "the httpserver backend cannot disable the openapi "
                    "frontend at runtime"
                )

        with self._update_lock:
            record = self._registry.update(name, enabled=enabled, path=path)
            persisted, warning = persist_frontend_settings(
                self._config_path,
                name=record.name,
                path=record.path,
                enabled=record.enabled,
                write_config=self._write_config,
            )

        result = record.as_dict()
        result["persisted"] = persisted
        result["warning"] = warning
        return result

    def _frontend_app(self, name: str) -> Any:
        """Return the ASGI application for *name*.

        Args:
            name: Frontend name.

        Returns:
            The application, wrapped with the shared auth check when tokens
            are configured.
        """
        if name == "openapi":
            return self._http_openapi_app
        if name == "mcp":
            return self._http_mcp_app
        if name == "sse":
            return self._sse_frontend_app()
        raise ValueError(f"unknown frontend {name!r}")

    def _sse_frontend_app(self) -> Any:
        """Return the SSE application for the current SSE path.

        The SSE transport bakes the advertised message endpoint into its
        transport object, so a path change rebuilds this frontend only.  The
        streamable-HTTP session manager is unaffected.
        """
        from ..adapters.mcp.server import build_sse_app

        sse_path = self._registry.get("sse").path
        if self._sse_app is None or self._sse_path != sse_path:
            self._sse_app = build_sse_app(
                self._mcp_server,
                path=sse_path,
                route_path="/",
            )
            self._sse_wrapped = self._wrap_auth(self._sse_app)
            self._sse_path = sse_path
        return self._sse_wrapped

    async def _serve_metadata(self, scope: Scope, receive: Receive, send: Send) -> bool:
        """Serve RFC 9728 protected-resource metadata when it applies.

        Clients discover bearer auth at
        ``/.well-known/oauth-protected-resource<mcp path>``.  The route is
        derived from the current MCP path, so a path change is reflected
        without a restart.

        Args:
            scope: ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable.

        Returns:
            ``True`` when the request was answered here.
        """
        if not self._tokens:
            return False
        mcp = self._registry.get("mcp")
        expected = "/.well-known/oauth-protected-resource" + mcp.path
        if scope.get("path", "") != expected:
            return False
        if not mcp.enabled:
            await _send_json(
                scope,
                receive,
                send,
                404,
                {"detail": "frontend 'mcp' is disabled"},
            )
            return True

        from ..adapters.mcp.server import resolve_public_url

        resource = resolve_public_url(
            self._server_url, self._host, self._port, mcp.path
        )
        issuer = resource.rsplit(mcp.path, 1)[0] or resource
        await _send_json(
            scope,
            receive,
            send,
            200,
            {
                "resource": resource,
                "authorization_servers": [issuer],
                "scopes_supported": ["mcp"],
                "bearer_methods_supported": ["header"],
            },
            {"Cache-Control": "public, max-age=3600"},
        )
        return True

    async def startup(self) -> None:
        """Create and start the MCP streamable-HTTP session manager.

        The manager is created once per lifespan.  It is not rebuilt when a
        frontend's enable flag or path changes.
        """
        if self._backend == "httpserver" or self._exit_stack is not None:
            return
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        stack = AsyncExitStack()
        manager = StreamableHTTPSessionManager(
            app=self._mcp_server,
            json_response=False,
            stateless=False,
        )
        try:
            await stack.enter_async_context(manager.run())
        except BaseException:
            await stack.aclose()
            raise
        self._session_manager = manager
        self._exit_stack = stack
        logger.info("Gateway MCP streamable-HTTP session manager started")

    async def shutdown(self) -> None:
        """Close the MCP session manager opened by :meth:`startup`."""
        stack = self._exit_stack
        self._exit_stack = None
        if stack is None:
            return
        try:
            await stack.aclose()
        finally:
            self._session_manager = None
        logger.info("Gateway MCP streamable-HTTP session manager closed")

    def run(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8000,
        reload: bool = False,
        **kwargs,
    ) -> None:
        """Start the gateway server (blocking).

        Args:
            host: Host to bind to.
            port: Port to bind to.
            reload: Accepted for CLI parity; ignored in gateway mode.
        """
        if reload:
            logger.warning("--reload is not supported in gateway mode; it is ignored")
        self._host = host
        self._port = port

        if self._backend == "httpserver":
            logger.info(f"Starting gateway (openapi only, httpserver) on {host}:{port}")
            self._openapi_app.run(host=host, port=port)
            return

        logger.info(f"Starting gateway on {host}:{port}")
        asyncio.run(self.run_async(host=host, port=port))

    async def run_async(self, **kwargs) -> None:
        """Start the gateway server in the current event loop.

        Args:
            **kwargs: ``host`` and ``port`` bind options.  Other keywords are
                accepted for parity with :meth:`Adapter.run_async`.
        """
        import uvicorn

        host = kwargs.get("host", "0.0.0.0")
        port = kwargs.get("port", 8000)
        self._host = host
        self._port = port
        server = uvicorn.Server(
            uvicorn.Config(self._asgi, host=host, port=port, log_level="info")
        )
        try:
            await server.serve()
        except KeyboardInterrupt:
            logger.info("Gateway shutdown requested (KeyboardInterrupt)")
        except asyncio.CancelledError:
            logger.info("Gateway shutdown requested (CancelledError)")
        finally:
            # uvicorn closes the listener and the lifespan on its own graceful
            # path; this covers an interrupted serve(), where it may not.
            await self.shutdown()

    @staticmethod
    def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
        """Add gateway-specific CLI arguments.

        Args:
            parser: The ``gateway`` subparser.
        """
        Adapter.add_cli_arguments(parser)
        parser.add_argument(
            "--host",
            type=str,
            default="0.0.0.0",
            help="Host to bind the gateway to (default: 0.0.0.0)",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8000,
            help="Port to bind the gateway to (default: 8000)",
        )
        parser.add_argument(
            "--tokens",
            type=str,
            default=None,
            metavar="PATH",
            help=(
                "Path to a file of Bearer tokens (one per line). "
                "The same tokens protect every frontend"
            ),
        )
        parser.add_argument(
            "--server-url",
            type=str,
            default=None,
            metavar="URL",
            help=(
                "Public URL of the MCP endpoint, used for protected-resource "
                "metadata (e.g. https://tools.example.com/mcp)"
            ),
        )
        parser.add_argument(
            "--backend",
            choices=("fastapi", "httpserver"),
            default="fastapi",
            help=(
                "HTTP backend. The httpserver backend can serve only the "
                "openapi frontend; multiple frontends need fastapi"
            ),
        )
        parser.add_argument(
            "--max-body-size",
            type=_positive_int,
            default=DEFAULT_MAX_BODY_SIZE,
            metavar="BYTES",
            help=(
                "Largest request body accepted by the OpenAPI frontend, in "
                f"bytes (default: {DEFAULT_MAX_BODY_SIZE})"
            ),
        )
        parser.add_argument(
            "--regex-timeout",
            type=_non_negative_float,
            default=DEFAULT_REGEX_TIMEOUT,
            metavar="SECONDS",
            help=(
                "Per-match ceiling for spec-supplied regexes "
                f"(default: {DEFAULT_REGEX_TIMEOUT:g}); 0 disables execution"
            ),
        )
        parser.add_argument(
            "--regex-budget",
            type=_non_negative_float,
            default=DEFAULT_REGEX_BUDGET,
            metavar="SECONDS",
            help=(
                "Per-request ceiling for spec-supplied regex matches "
                f"(default: {DEFAULT_REGEX_BUDGET:g}); 0 disables execution"
            ),
        )
        parser.add_argument(
            "--strict-spec-regex",
            action="store_true",
            help=(
                "Abort startup when an OpenAPI source contains an invalid "
                "regex; by default that source is skipped and logged"
            ),
        )
        parser.add_argument(
            "--tools-list-ttl-ms",
            type=int,
            default=None,
            metavar="MS",
            help="ttlMs cache hint for MCP tools/list (default: 300000)",
        )
        parser.add_argument(
            "--tools-list-cache-scope",
            choices=("public", "private"),
            default=None,
            help="cacheScope hint for MCP tools/list (default: private)",
        )

        paths = parser.add_argument_group("frontend paths")
        paths.add_argument(
            "--openapi-path",
            default=None,
            metavar="PATH",
            help="Mount path for the OpenAPI frontend (default: /openapi)",
        )
        paths.add_argument(
            "--mcp-path",
            default=None,
            metavar="PATH",
            help="Mount path for MCP streamable-HTTP (default: /mcp)",
        )
        paths.add_argument(
            "--sse-path",
            default=None,
            metavar="PATH",
            help="Mount path for MCP SSE (default: /sse)",
        )

        switches = parser.add_argument_group("frontend switches")
        switches.add_argument(
            "--openapi",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Enable or disable the OpenAPI frontend (default: enabled)",
        )
        switches.add_argument(
            "--mcp",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Enable or disable MCP streamable-HTTP (default: enabled)",
        )
        switches.add_argument(
            "--sse",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Enable or disable MCP SSE (default: enabled)",
        )

    @classmethod
    def create(cls, route_table: RouteTable, **kwargs) -> GatewayAdapter:
        """Build a gateway adapter without starting the listener.

        Args:
            route_table: The shared RouteTable.
            **kwargs: Adapter construction and transport options.

        Returns:
            A configured adapter.
        """
        identity = kwargs.get("identity") or ServerIdentity()
        tokens = kwargs.get("tokens")
        if tokens is None:
            tokens = load_tokens(kwargs.get("tokens_path"))
        stored = load_frontend_settings(kwargs.get("config_path"))
        frontends = resolve_frontends(stored, kwargs)
        return cls(
            route_table,
            identity=identity,
            frontends=frontends,
            tokens=tokens or None,
            backend=kwargs.get("backend", "fastapi"),
            config_path=kwargs.get("config_path"),
            write_config=kwargs.get("write_config", True),
            server_url=kwargs.get("server_url"),
            host=kwargs.get("host", "0.0.0.0"),
            port=kwargs.get("port", 8000),
            max_body_size=kwargs.get("max_body_size", DEFAULT_MAX_BODY_SIZE),
            max_sse_subscribers=kwargs.get(
                "max_sse_subscribers", DEFAULT_MAX_SSE_SUBSCRIBERS
            ),
            regex_timeout=kwargs.get("regex_timeout", DEFAULT_REGEX_TIMEOUT),
            regex_budget=kwargs.get("regex_budget", DEFAULT_REGEX_BUDGET),
            tools_list_ttl_ms=kwargs.get("tools_list_ttl_ms"),
            tools_list_cache_scope=kwargs.get("tools_list_cache_scope"),
            instructions=kwargs.get("instructions"),
        )

    @classmethod
    def create_and_run(cls, route_table: RouteTable, **kwargs) -> None:
        """Construct and run a gateway in one step.

        Args:
            route_table: The shared RouteTable.
            **kwargs: Adapter construction and transport options.
        """
        gateway = cls.create(route_table, **kwargs)
        gateway.run(
            host=kwargs.get("host", "0.0.0.0"),
            port=kwargs.get("port", 8000),
            reload=kwargs.get("reload", False),
        )


def create_gateway_app(route_table: RouteTable, **kwargs) -> Any:
    """Build the gateway application without starting a listener.

    Args:
        route_table: The shared RouteTable.
        **kwargs: Gateway construction options.

    Returns:
        The ASGI dispatcher, or the vendored OpenAPI application when the
        httpserver backend serves the sole frontend.
    """
    return GatewayAdapter.create(route_table, **kwargs).app


__all__ = [
    "GatewayAdapter",
    "create_gateway_app",
    "resolve_frontends",
]
