"""Application-level server orchestration.

This module provides high-level functions for building a registry from
config and serving it via any adapter.  It is the programmatic entry
point — use this instead of the CLI when embedding a server in your
own application.

Example — OpenAPI server::

    from toolregistry_server.app import serve_openapi
    serve_openapi(config_path="tools.yaml", host="0.0.0.0", port=8000)

Example — MCP server::

    from toolregistry_server.app import serve_mcp
    serve_mcp(config_path="tools.yaml", transport="stdio")

Example — bring your own registry::

    from toolregistry import ToolRegistry
    from toolregistry_server.app import serve_openapi
    registry = ToolRegistry()
    # ... register tools ...
    serve_openapi(registry=registry, port=9000)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._vendor.structlog import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from toolregistry import ToolRegistry
    from toolregistry.config import ToolConfig


def _resolve_registry(
    config_path: str | None = None,
    registry: ToolRegistry | None = None,
    profile: str | None = None,
) -> tuple[ToolRegistry, ToolConfig | None]:
    """Resolve a registry from arguments.

    If *registry* is provided it is used directly.  Otherwise a new one
    is built from *config_path*.  Profile filtering is applied when
    *profile* is set.

    Returns:
        ``(registry, config)`` — *config* is ``None`` when *registry*
        was provided directly.
    """
    from .registry_builder import apply_profile, load_config, registry_from_config

    config: ToolConfig | None = None
    if registry is not None:
        pass  # use provided registry as-is
    elif config_path is not None:
        config = load_config(config_path)
        registry = registry_from_config(config)
    else:
        raise ValueError(
            "Either 'config_path' or 'registry' must be provided. "
            "Pass a config file path or a pre-built ToolRegistry."
        )

    if profile is not None:
        apply_profile(registry, profile, config=config)

    return registry, config


def serve_openapi(
    *,
    config_path: str | None = None,
    registry: ToolRegistry | None = None,
    profile: str | None = None,
    host: str = "0.0.0.0",
    port: int = 8000,
    tokens_path: str | None = None,
    reload: bool = False,
) -> None:
    """Build a registry (if needed) and start an OpenAPI server.

    Args:
        config_path: Path to a JSONC/YAML config file.
        registry: Pre-built registry (skips config loading).
        profile: Deployment profile for tag-based filtering.
        host: Host to bind to.
        port: Port to bind to.
        tokens_path: Path to Bearer token file.
        reload: Enable uvicorn auto-reload.
    """
    from .adapters.openapi import OpenAPIAdapter, load_tokens
    from .route_table import RouteTable

    registry, _ = _resolve_registry(config_path, registry, profile)
    route_table = RouteTable(registry)
    tokens = load_tokens(tokens_path)
    adapter = OpenAPIAdapter(route_table, tokens=tokens or None)
    adapter(host=host, port=port, reload=reload)


def serve_mcp(
    *,
    config_path: str | None = None,
    registry: ToolRegistry | None = None,
    profile: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    transport: str = "stdio",
    tokens_path: str | None = None,
    server_url: str | None = None,
) -> None:
    """Build a registry (if needed) and start an MCP server.

    Args:
        config_path: Path to a JSONC/YAML config file.
        registry: Pre-built registry (skips config loading).
        profile: Deployment profile for tag-based filtering.
        host: Host to bind to.
        port: Port to bind to.
        transport: MCP transport (``"stdio"``, ``"sse"``, ``"http"``,
            or ``"streamable-http"``).
        tokens_path: Path to Bearer token file (streamable-http only).
        server_url: Public server URL (streamable-http only).
    """
    from .adapters.mcp import MCPAdapter
    from .route_table import RouteTable

    registry, _ = _resolve_registry(config_path, registry, profile)
    route_table = RouteTable(registry)
    adapter = MCPAdapter(route_table)
    adapter(
        host=host,
        port=port,
        transport=transport,
        tokens_path=tokens_path,
        server_url=server_url,
    )
