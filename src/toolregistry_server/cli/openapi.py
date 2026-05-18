"""
OpenAPI server startup module.

This module provides functions to start an OpenAPI server from the CLI.
"""

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .._vendor.structlog import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from toolregistry import ToolRegistry
    from toolregistry.config import (
        MCPSource,
        OpenAPISource,
        PythonSource,
        ToolConfig,
        ToolSource,
    )


def load_config(config_path: str | None) -> "ToolConfig | None":
    """Load configuration from a JSONC or YAML file.

    Delegates to ``toolregistry.config.load_config()`` for parsing and
    validation.

    Args:
        config_path: Path to the configuration file, or None.

    Returns:
        Parsed ``ToolConfig``, or None if no config specified.

    Raises:
        SystemExit: If the config file cannot be loaded.
    """
    if config_path is None:
        return None

    try:
        from toolregistry.config import ConfigError
        from toolregistry.config import load_config as _load_config

        return _load_config(config_path)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    except ConfigError as e:
        logger.error(f"Invalid configuration: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load configuration file: {e}")
        sys.exit(1)


def load_tokens(tokens_path: str | None) -> list[str]:
    """Load authentication tokens from a file.

    Args:
        tokens_path: Path to the tokens file, or None.

    Returns:
        List of tokens, or empty list if no file specified.

    Raises:
        SystemExit: If the tokens file cannot be loaded.
    """
    if tokens_path is None:
        return []

    path = Path(tokens_path)
    if not path.exists():
        logger.error(f"Tokens file not found: {tokens_path}")
        sys.exit(1)

    try:
        content = path.read_text(encoding="utf-8")
        tokens = []
        for line in content.splitlines():
            line = line.strip()
            # Skip empty lines and comments
            if line and not line.startswith("#"):
                tokens.append(line)
        return tokens
    except Exception as e:
        logger.error(f"Failed to load tokens file: {e}")
        sys.exit(1)


def _ns_matches(tool_namespace: str, pattern: str) -> bool:
    """Check if a tool namespace matches a config pattern.

    Supports exact match and prefix match for hierarchical namespaces.
    For example, pattern ``"web"`` matches ``"web/brave_search"``.

    Args:
        tool_namespace: The tool's namespace (e.g. ``"web/brave_search"``).
        pattern: The config pattern (e.g. ``"web"`` or ``"web/brave_search"``).

    Returns:
        True if the namespace matches the pattern.
    """
    return tool_namespace == pattern or tool_namespace.startswith(pattern + "/")


def _should_load_source(source: "ToolSource", config: "ToolConfig") -> bool:
    """Determine if a tool source should be loaded based on mode and namespace.

    Args:
        source: The tool source to check.
        config: The parsed tool configuration.

    Returns:
        True if the source should be loaded, False otherwise.
    """
    ns = source.namespace
    if ns is None:
        return True
    if config.mode == "denylist":
        return not any(_ns_matches(ns, p) for p in config.disabled)
    return any(_ns_matches(ns, p) for p in config.enabled)


def _register_python_source(
    registry: "ToolRegistry",
    source: "PythonSource",
) -> None:
    """Register tools from a Python class or module source.

    Args:
        registry: The registry to register tools into.
        source: The Python source configuration.
    """
    ns: bool | str = source.namespace if source.namespace else False

    if source.class_path:
        module_path, class_name = source.class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        instance = cls()
        registry.register_from_class(instance, namespace=ns)
        logger.info(f"Loaded class tools from {source.class_path}")
    elif source.module_path:
        module = importlib.import_module(source.module_path)
        for name in dir(module):
            if not name.startswith("_"):
                obj = getattr(module, name)
                if callable(obj) and not isinstance(obj, type):
                    registry.register(obj, namespace=source.namespace)
        logger.info(f"Loaded module tools from {source.module_path}")


def _register_mcp_source(
    registry: "ToolRegistry",
    source: "MCPSource",
) -> None:
    """Register tools from an MCP server source.

    Args:
        registry: The registry to register tools into.
        source: The MCP source configuration.
    """
    ns: bool | str = source.namespace if source.namespace else False

    if source.transport == "stdio":
        assert source.command is not None
        transport: str | dict = {
            "command": source.command[0],
            "args": list(source.command[1:]),
            "env": dict(source.env) if source.env else {},
        }
    else:
        assert source.url is not None
        transport = source.url

    registry.register_from_mcp(
        transport,
        namespace=ns,
        persistent=source.persistent,
    )
    logger.info(f"Loaded MCP tools from {source.url or ' '.join(source.command or [])}")


def _register_openapi_source(
    registry: "ToolRegistry",
    source: "OpenAPISource",
) -> None:
    """Register tools from an OpenAPI endpoint source.

    Args:
        registry: The registry to register tools into.
        source: The OpenAPI source configuration.
    """
    from toolregistry.integrations.openapi import HttpClientConfig, load_openapi_spec

    ns: bool | str = source.namespace if source.namespace else False

    # Build auth headers
    headers: dict[str, str] = {}
    if source.auth and source.auth.token:
        if source.auth.type == "bearer":
            headers["Authorization"] = f"Bearer {source.auth.token}"
        elif source.auth.type == "header":
            headers[source.auth.header_name] = source.auth.token

    # Load spec and determine base URL
    spec = load_openapi_spec(source.url)
    base_url = source.base_url
    if not base_url:
        servers = spec.get("servers", [])
        base_url = servers[0].get("url", "") if servers else ""

    client = HttpClientConfig(base_url=base_url, headers=headers)
    registry.register_from_openapi(client, spec, namespace=ns, persistent=True)
    logger.info(f"Loaded OpenAPI tools from {source.url}")


def create_registry_from_config(config: "ToolConfig | None") -> "ToolRegistry":
    """Create a ToolRegistry from configuration.

    Supports three tool source types:

    - **python**: Python classes or modules
    - **mcp**: MCP servers (stdio, SSE, streamable-http)
    - **openapi**: OpenAPI endpoints

    And two filtering modes:

    - **denylist** (default): Load all tools except those with namespaces
      listed in the "disabled" array.
    - **allowlist**: Only load tools with namespaces listed in the "enabled"
      array.

    Args:
        config: Parsed ``ToolConfig``, or None for empty registry.

    Returns:
        Configured ToolRegistry instance.
    """
    from toolregistry import ToolRegistry
    from toolregistry.config import MCPSource, OpenAPISource, PythonSource

    registry = ToolRegistry()

    if config is None:
        logger.info("No configuration provided, starting with empty registry")
        return registry

    loaded_count = 0
    skipped_count = 0

    for source in config.tools:
        if not source.enabled:
            logger.info(f"Skipping disabled source: {source}")
            skipped_count += 1
            continue

        if not _should_load_source(source, config):
            reason = (
                "in disabled list"
                if config.mode == "denylist"
                else "not in enabled list"
            )
            logger.info(
                f"Config {config.mode}: skipping namespace "
                f"'{source.namespace}' ({reason})"
            )
            skipped_count += 1
            continue

        try:
            if isinstance(source, PythonSource):
                _register_python_source(registry, source)
            elif isinstance(source, MCPSource):
                _register_mcp_source(registry, source)
            elif isinstance(source, OpenAPISource):
                _register_openapi_source(registry, source)
            loaded_count += 1
        except Exception as e:
            source_desc = _describe_source(source)
            logger.warning(f"Failed to load tools from {source_desc}: {e}")

    logger.info(
        f"Applied tool config (mode={config.mode}): "
        f"loaded {loaded_count}, skipped {skipped_count}"
    )

    return registry


def _describe_source(source: "ToolSource") -> str:
    """Return a human-readable description of a tool source for logging."""
    from toolregistry.config import MCPSource, OpenAPISource, PythonSource

    if isinstance(source, PythonSource):
        return source.class_path or source.module_path or "unknown python source"
    if isinstance(source, MCPSource):
        return source.url or " ".join(source.command or [])
    if isinstance(source, OpenAPISource):
        return source.url
    return str(source)


def run_openapi_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    config_path: str | None = None,
    tokens_path: str | None = None,
    reload: bool = False,
    registry: "ToolRegistry | None" = None,
) -> None:
    """Start the OpenAPI server.

    Args:
        host: Host to bind the server to.
        port: Port to bind the server to.
        config_path: Path to configuration file.
        tokens_path: Path to tokens file.
        reload: Enable auto-reload for development.
        registry: Pre-built ToolRegistry to use directly. When provided,
            ``config_path`` is ignored and ``create_registry_from_config``
            is skipped.
    """
    try:
        import uvicorn
    except ImportError as e:
        logger.error(f"OpenAPI server dependencies not installed: {e}")
        logger.info("Install with: pip install toolregistry-server[openapi]")
        sys.exit(1)

    try:
        from toolregistry_server import RouteTable
        from toolregistry_server.openapi import create_openapi_app
    except ImportError as e:
        logger.error(f"Failed to import server components: {e}")
        sys.exit(1)

    if registry is None:
        # Load configuration and build registry from config
        config = load_config(config_path)
        registry = create_registry_from_config(config)

    # Create route table
    route_table = RouteTable(registry)

    # Load tokens for authentication
    tokens = load_tokens(tokens_path)

    # Create dependencies for authentication if tokens are provided
    dependencies = None
    if tokens:
        try:
            from fastapi import Depends

            from toolregistry_server.auth import (
                BearerTokenAuth,
                create_bearer_dependency,
            )

            auth = BearerTokenAuth(tokens=tokens)
            dependencies = [Depends(create_bearer_dependency(auth))]
            logger.info(f"Authentication enabled with {len(tokens)} token(s)")
        except ImportError as e:
            logger.warning(f"Failed to setup authentication: {e}")

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
