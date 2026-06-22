"""OpenAPI adapter for ToolRegistry.

This module provides functionality to expose ToolRegistry tools as
RESTful HTTP endpoints using FastAPI.

Main Components:
    - create_openapi_app: Create a FastAPI application from a RouteTable
    - run_openapi_server: Start an OpenAPI server (registry + transport)

Example:
    ```python
    from toolregistry import ToolRegistry
    from toolregistry_server import RouteTable
    from toolregistry_server.adapters.openapi import create_openapi_app

    registry = ToolRegistry()
    route_table = RouteTable(registry)
    app = create_openapi_app(route_table)
    ```

Note:
    This module requires the 'openapi' extra to be installed:
    pip install toolregistry-server[openapi]
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..._vendor.structlog import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI
    from toolregistry import ToolRegistry

    from ...route_table import RouteTable


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_openapi_app(
    route_table: "RouteTable",
    title: str = "ToolRegistry Server",
    version: str = "1.0.0",
    description: str = "OpenAPI server for ToolRegistry tools",
    dependencies: "Sequence[Any] | None" = None,
    enable_etag: bool = True,
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
# Token loading
# ---------------------------------------------------------------------------


def load_tokens(tokens_path: str | None) -> list[str]:
    """Load authentication tokens from a file.

    Args:
        tokens_path: Path to the tokens file, or None.

    Returns:
        List of tokens, or empty list if no file specified.

    Raises:
        FileNotFoundError: If the tokens file does not exist.
        OSError: If the tokens file cannot be read.
    """
    if tokens_path is None:
        return []

    path = Path(tokens_path)
    if not path.exists():
        raise FileNotFoundError(f"Tokens file not found: {tokens_path}")

    content = path.read_text(encoding="utf-8")
    tokens = []
    for line in content.splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if line and not line.startswith("#"):
            tokens.append(line)
    return tokens


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------


def run_openapi_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    config_path: str | None = None,
    tokens_path: str | None = None,
    reload: bool = False,
    registry: "ToolRegistry | None" = None,
    profile: str | None = None,
) -> None:
    """Start the OpenAPI server.

    Args:
        host: Host to bind the server to.
        port: Port to bind the server to.
        config_path: Path to configuration file.
        tokens_path: Path to tokens file.
        reload: Enable auto-reload for development.
        registry: Pre-built ToolRegistry to use directly. When provided,
            ``config_path`` is ignored and ``registry_from_config``
            is skipped.
        profile: Deployment profile for tag-based tool filtering.
            ``"remote"`` disables tools tagged ``file_system``, ``destructive``,
            or ``privileged``. ``"local"`` disables tools tagged ``network``.
            ``None`` (default) skips profile filtering entirely.

    Raises:
        ImportError: If uvicorn or FastAPI is not installed.
    """
    import uvicorn

    from ...registry_builder import apply_profile, load_config, registry_from_config
    from ...route_table import RouteTable

    config = None
    if registry is None:
        # Load configuration and build registry from config
        config = load_config(config_path)
        if config:
            registry = registry_from_config(config)
        else:
            from toolregistry import ToolRegistry

            registry = ToolRegistry()

    if profile is not None:
        apply_profile(registry, profile, config=config)

    # Create route table
    route_table = RouteTable(registry)

    # Load tokens for authentication
    tokens = load_tokens(tokens_path)

    # Create dependencies for authentication if tokens are provided
    dependencies = None
    if tokens:
        from fastapi import Depends

        from ...auth import (
            BearerTokenAuth,
            create_bearer_dependency,
        )

        auth = BearerTokenAuth(tokens=tokens)
        dependencies = [Depends(create_bearer_dependency(auth))]
        logger.info(f"Authentication enabled with {len(tokens)} token(s)")

    # Create the FastAPI app
    app = create_openapi_app(
        route_table,
        title="ToolRegistry Server",
        version="1.0.0",
        description="OpenAPI server for ToolRegistry tools",
        dependencies=dependencies,
    )

    # Log startup info
    logger.info(f"Starting OpenAPI server on {host}:{port}")
    logger.info(f"Registered {len(route_table.list_routes())} tool(s)")

    # Run the server
    if reload:
        logger.warning("Reload mode is not fully supported with dynamic configuration")

    uvicorn.run(app, host=host, port=port, reload=reload)


__all__ = [
    "create_openapi_app",
    "load_tokens",
    "run_openapi_server",
]
