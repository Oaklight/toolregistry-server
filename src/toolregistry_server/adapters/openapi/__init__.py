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

from typing import TYPE_CHECKING, Any

from ..._vendor.structlog import get_logger
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

    Returns:
        A configured FastAPI application with:
        - Tool routes from the RouteTable
        - /tools endpoint for listing available tools
        - ETag middleware for cache validation (if enabled)
        - Dynamic OpenAPI schema that filters disabled tools

    Raises:
        ImportError: If FastAPI is not installed.
    """
    try:
        from fastapi import FastAPI
    except ImportError as e:
        raise ImportError(
            "FastAPI is required for OpenAPI support. "
            "Install with: pip install toolregistry-server[openapi]"
        ) from e

    from .adapter import (
        add_tools_endpoint,
        route_table_to_router,
        setup_dynamic_openapi,
    )
    from .middleware import add_etag_middleware

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

    # Add ETag middleware for cache validation
    if enable_etag:
        add_etag_middleware(app, route_table)

    # Add /tools endpoint for listing available tools
    add_tools_endpoint(app, route_table)

    # Add routes from route table
    router = route_table_to_router(route_table)
    app.include_router(router)

    # Setup dynamic OpenAPI schema that filters disabled tools
    setup_dynamic_openapi(app, route_table)

    return app


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class OpenAPIAdapter(Adapter):
    """Serve tools as RESTful HTTP endpoints via FastAPI.

    Args:
        route_table: The RouteTable to expose.
        tokens: Optional list of Bearer tokens for authentication.
        title: API title for OpenAPI schema.
        version: API version string.
        description: API description.
    """

    def __init__(
        self,
        route_table: RouteTable,
        *,
        tokens: list[str] | None = None,
        title: str = "ToolRegistry Server",
        version: str = "1.0.0",
        description: str = "OpenAPI server for ToolRegistry tools",
    ) -> None:
        super().__init__(route_table)

        dependencies = None
        if tokens:
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
        )

    @property
    def app(self) -> FastAPI:
        """The FastAPI application instance."""
        return self._app

    def run(self, *, host: str = "0.0.0.0", port: int = 8000, **kwargs) -> None:
        """Start the OpenAPI server.

        Args:
            host: Host to bind to.
            port: Port to bind to.
            reload: Enable auto-reload for development (default: False).
        """
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
            help="Path to a file containing authentication tokens (one per line)",
        )
        parser.add_argument(
            "--reload",
            action="store_true",
            help="Enable auto-reload for development mode",
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
        adapter = cls(
            route_table,
            tokens=tokens or None,
            title=kwargs.pop("title", identity.name),
            version=kwargs.pop("version", identity.version),
            description=kwargs.pop("description", identity.description),
        )
        adapter.run(**kwargs)


__all__ = [
    "OpenAPIAdapter",
    "create_openapi_app",
]
