"""OpenAPI adapter for ToolRegistry.

This module provides functionality to expose ToolRegistry tools as
RESTful HTTP endpoints using FastAPI.

Main Components:
    - OpenAPIAdapter: Adapter class for serving tools via OpenAPI
    - create_openapi_app: Create a FastAPI application from a RouteTable

Example:
    ```python
    from toolregistry import ToolRegistry
    from toolregistry_server import RouteTable
    from toolregistry_server.adapters.openapi import OpenAPIAdapter

    registry = ToolRegistry()
    route_table = RouteTable(registry)
    adapter = OpenAPIAdapter(route_table)
    adapter.serve(host="0.0.0.0", port=8000)
    ```

Note:
    This module requires the 'openapi' extra to be installed:
    pip install toolregistry-server[openapi]
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from ..._vendor.httpserver import DEFAULT_MAX_BODY_SIZE
from ..._vendor.structlog import get_logger
from ...regex_guard import (
    DEFAULT_REGEX_BUDGET,
    DEFAULT_REGEX_TIMEOUT,
    build_matcher_factory,
    log_status,
)
from .. import Adapter

logger = get_logger()

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI

    from ...route_table import RouteTable


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
) -> FastAPI:
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
            "FastAPI is required for OpenAPI support. "
            "Install with: pip install toolregistry-server[openapi]"
        ) from e

    from .adapter import (
        add_events_endpoint,
        add_tools_endpoint,
        route_table_to_router,
        setup_dynamic_openapi,
    )
    from .middleware import add_body_size_middleware, add_etag_middleware

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
    setup_dynamic_openapi(app, route_table, principal_resolver)

    # Added last so it wraps every other middleware: an oversized body is
    # rejected before routing, auth or ETag short-circuits see it.
    add_body_size_middleware(app, max_body_size=max_body_size)

    return app


#: HTTP backends :class:`OpenAPIAdapter` can serve on.
_BACKENDS = ("fastapi", "httpserver")


def _build_key_resolver(
    keys_db: str | None, operator_tokens: Sequence[str]
) -> Any | None:
    """Open the key store named by ``--keys-db`` and build its resolver.

    Args:
        keys_db: SQLite database path, or ``None`` when key authentication is
            not configured.
        operator_tokens: Static tokens that keep full, unscoped access.

    Returns:
        The resolver, or ``None`` when key authentication is off.
    """
    if not keys_db:
        return None

    from ...keystore import ApiKeyStore, PrincipalResolver

    return PrincipalResolver(ApiKeyStore(keys_db), operator_tokens=operator_tokens)


def _positive_int(text: str) -> int:
    """Argparse type for a byte count that must be greater than zero.

    The factory and the adapter reject a non-positive cap at construction;
    rejecting it at the parser too gives one error message for both backends.
    """
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {text!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of bytes")
    return value


def _non_negative_float(text: str) -> float:
    """Argparse type for a time bound in seconds; ``0`` disables it."""
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid float value: {text!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("must be a finite number of seconds")
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class OpenAPIAdapter(Adapter):
    """Serve tools as RESTful HTTP endpoints.

    Two backends produce the same wire contract. ``fastapi`` (the default)
    keeps the existing uvicorn stack; ``httpserver`` runs on the vendored
    zero-dependency server from epic #101 and will become the default once
    the test suite is ported.

    Args:
        route_table: The RouteTable to expose.
        tokens: Optional list of Bearer tokens for authentication.
        title: API title for OpenAPI schema.
        version: API version string.
        description: API description.
        backend: ``"fastapi"`` or ``"httpserver"``.
        max_body_size: Largest request body accepted, in bytes. Both backends
            enforce it and return the same ``413`` envelope. Defaults to
            1 MiB.
        regex_timeout: Per-match ceiling for spec-supplied regexes, in
            seconds. Requires the optional ``regex`` extra; ``0`` disables
            execution and keeps spec patterns out of validation.
        regex_budget: Per-request ceiling for all spec-supplied regex
            matches, in seconds. ``0`` disables execution.
        principal_resolver: Optional API key resolver.  When set, the
            caller's key scope filters tool routes, ``/tools`` and
            ``/openapi.json``.  Static *tokens* keep full, unscoped access
            when both are supplied.

    Raises:
        ValueError: If *backend* is unknown.
    """

    def __init__(
        self,
        route_table: RouteTable,
        *,
        tokens: list[str] | None = None,
        title: str = "ToolRegistry Server",
        version: str = "1.0.0",
        description: str = "OpenAPI server for ToolRegistry tools",
        backend: str = "fastapi",
        max_body_size: int = DEFAULT_MAX_BODY_SIZE,
        regex_timeout: float = DEFAULT_REGEX_TIMEOUT,
        regex_budget: float = DEFAULT_REGEX_BUDGET,
        principal_resolver: Any | None = None,
    ) -> None:
        super().__init__(route_table)

        if backend not in _BACKENDS:
            raise ValueError(
                f"Unknown OpenAPI backend {backend!r}; choose from {_BACKENDS}"
            )
        self._backend = backend
        # Two backends produce different app types; the annotation keeps
        # type checkers from narrowing run() to a union of both.
        self._app: Any = None

        if backend == "httpserver":
            from .server_app import create_httpserver_app

            self._app = create_httpserver_app(
                route_table,
                title=title,
                version=version,
                description=description,
                tokens=tokens,
                max_body_size=max_body_size,
                regex_timeout=regex_timeout,
                regex_budget=regex_budget,
                principal_resolver=principal_resolver,
            )
            return

        dependencies = None
        if principal_resolver is not None:
            from fastapi import Depends

            from ...auth import create_scoped_bearer_dependency

            dependencies = [
                Depends(create_scoped_bearer_dependency(principal_resolver))
            ]
            logger.info("API key scoping enabled; static tokens keep full access")
        elif tokens:
            from fastapi import Depends

            from ...auth import BearerTokenAuth, create_bearer_dependency

            auth = BearerTokenAuth(tokens=tokens)
            dependencies = [Depends(create_bearer_dependency(auth))]
            logger.info(f"Authentication enabled with {len(tokens)} token(s)")

        self._app = create_openapi_app(
            route_table,
            title=title,
            version=version,
            description=description,
            dependencies=dependencies,
            max_body_size=max_body_size,
            regex_timeout=regex_timeout,
            regex_budget=regex_budget,
            principal_resolver=principal_resolver,
        )

    @property
    def app(self) -> Any:
        """The application instance for the configured backend."""
        return self._app

    def run(self, *, host: str = "0.0.0.0", port: int = 8000, **kwargs) -> None:
        """Start the OpenAPI server.

        Args:
            host: Host to bind to.
            port: Port to bind to.
            reload: Enable auto-reload for development (default: False).
                Only supported by the ``fastapi`` backend.
        """
        if self._backend == "httpserver":
            if kwargs.get("reload"):
                logger.warning(
                    "--reload is a uvicorn feature; it is ignored on the "
                    "httpserver backend"
                )
            logger.info(f"Starting OpenAPI server on {host}:{port}")
            logger.info(f"Registered {len(self._route_table.list_routes())} tool(s)")
            self._app.run(host=host, port=port)
            return

        import uvicorn

        reload = kwargs.get("reload", False)
        if reload:
            logger.warning(
                "Reload mode is not fully supported with dynamic configuration"
            )

        logger.info(f"Starting OpenAPI server on {host}:{port}")
        logger.info(f"Registered {len(self._route_table.list_routes())} tool(s)")
        uvicorn.run(self._app, host=host, port=port, reload=reload)

    @staticmethod
    def add_cli_arguments(parser) -> None:
        """Add OpenAPI-specific CLI arguments."""
        Adapter.add_cli_arguments(parser)
        parser.add_argument(
            "--host",
            type=str,
            default="0.0.0.0",
            help="Host to bind the server to (default: 0.0.0.0)",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8000,
            help="Port to bind the server to (default: 8000)",
        )
        parser.add_argument(
            "--tokens",
            type=str,
            default=None,
            help=(
                "Path to a file containing authentication tokens (one per "
                "line). These are unscoped bootstrap credentials"
            ),
        )
        parser.add_argument(
            "--keys-db",
            type=str,
            default=None,
            metavar="PATH",
            help=(
                "Path to the SQLite API key database. When set, keys from "
                "this database are authenticated and their namespace scopes "
                "filter tool routes, /tools and /openapi.json. Static "
                "--tokens keep full, unscoped access. Omit for the previous "
                "behaviour"
            ),
        )
        parser.add_argument(
            "--reload",
            action="store_true",
            help="Enable auto-reload for development mode (fastapi backend only)",
        )
        parser.add_argument(
            "--backend",
            choices=_BACKENDS,
            default="fastapi",
            help=(
                "HTTP backend: fastapi (uvicorn, default) or httpserver "
                "(vendored, zero-dependency)"
            ),
        )
        parser.add_argument(
            "--max-body-size",
            type=_positive_int,
            default=DEFAULT_MAX_BODY_SIZE,
            metavar="BYTES",
            help=(
                "Largest request body accepted by either backend, in bytes "
                f"(default: {DEFAULT_MAX_BODY_SIZE}); larger bodies return 413"
            ),
        )
        parser.add_argument(
            "--regex-timeout",
            type=_non_negative_float,
            default=DEFAULT_REGEX_TIMEOUT,
            metavar="SECONDS",
            help=(
                "Per-match ceiling for spec-supplied regexes when the regex "
                f"extra is installed (default: {DEFAULT_REGEX_TIMEOUT:g}); "
                "0 disables execution and ignores spec patterns"
            ),
        )
        parser.add_argument(
            "--regex-budget",
            type=_non_negative_float,
            default=DEFAULT_REGEX_BUDGET,
            metavar="SECONDS",
            help=(
                "Per-request ceiling for all spec-supplied regex matches "
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

    @classmethod
    def create_and_run(cls, route_table: RouteTable, **kwargs) -> None:
        """Construct and run an OpenAPI server in one step.

        Reads ``identity`` from kwargs for title/version/description
        defaults.  Explicit ``title``/``version``/``description``
        kwargs take precedence over identity.
        """
        from ...auth import load_tokens
        from ...identity import ServerIdentity

        identity: ServerIdentity = kwargs.pop("identity", ServerIdentity())
        tokens = load_tokens(kwargs.pop("tokens_path", None))
        principal_resolver = _build_key_resolver(kwargs.pop("keys_db", None), tokens)
        adapter = cls(
            route_table,
            tokens=tokens or None,
            title=kwargs.pop("title", identity.name),
            version=kwargs.pop("version", identity.version),
            description=kwargs.pop("description", identity.description),
            backend=kwargs.pop("backend", "fastapi"),
            max_body_size=kwargs.pop("max_body_size", DEFAULT_MAX_BODY_SIZE),
            regex_timeout=kwargs.pop("regex_timeout", DEFAULT_REGEX_TIMEOUT),
            regex_budget=kwargs.pop("regex_budget", DEFAULT_REGEX_BUDGET),
            principal_resolver=principal_resolver,
        )
        adapter.run(**kwargs)


__all__ = [
    "OpenAPIAdapter",
    "create_openapi_app",
]
