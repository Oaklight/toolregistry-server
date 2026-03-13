"""
OpenAPI adapter for ToolRegistry.

This module provides functionality to expose ToolRegistry tools as
RESTful HTTP endpoints using FastAPI.

Main Components:
    - create_openapi_app: Create a FastAPI application from a RouteTable
    - route_table_to_router: Convert a RouteTable to a FastAPI router

Example:
    >>> from toolregistry import ToolRegistry
    >>> from toolregistry_server import RouteTable
    >>> from toolregistry_server.openapi import create_openapi_app
    >>>
    >>> registry = ToolRegistry()
    >>> route_table = RouteTable(registry)
    >>> app = create_openapi_app(route_table)

Note:
    This module requires the 'openapi' extra to be installed:
    pip install toolregistry-server[openapi]
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from ..route_table import RouteTable


def create_openapi_app(
    route_table: "RouteTable",
    title: str = "ToolRegistry Server",
    version: str = "1.0.0",
    description: str = "OpenAPI server for ToolRegistry tools",
) -> "FastAPI":
    """Create a FastAPI application from a RouteTable.

    Args:
        route_table: The RouteTable to expose.
        title: API title for OpenAPI schema.
        version: API version for OpenAPI schema.
        description: API description for OpenAPI schema.

    Returns:
        A configured FastAPI application.

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

    app = FastAPI(title=title, version=version, description=description)

    # Add routes from route table
    router = route_table_to_router(route_table)
    app.include_router(router)

    return app


def route_table_to_router(
    route_table: "RouteTable",
    prefix: str = "/tools",
) -> "APIRouter":
    """Convert a RouteTable into a FastAPI router.

    Args:
        route_table: The RouteTable to convert.
        prefix: URL prefix for all routes.

    Returns:
        A FastAPI APIRouter with all tool routes.

    Raises:
        ImportError: If FastAPI is not installed.
    """
    try:
        from fastapi import APIRouter
    except ImportError as e:
        raise ImportError(
            "FastAPI is required for OpenAPI support. "
            "Install with: pip install toolregistry-server[openapi]"
        ) from e

    router = APIRouter(prefix=prefix)

    # TODO: Implement route generation from RouteTable
    # This is a skeleton implementation - full implementation will be
    # migrated from toolregistry-hub's autoroute.py

    return router


__all__ = [
    "create_openapi_app",
    "route_table_to_router",
]
