"""
OpenAPI server startup module.

This module provides functions to start an OpenAPI server from the CLI.
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from toolregistry import ToolRegistry


def load_config(config_path: str | None) -> dict | None:
    """Load configuration from a JSON/JSONC file.

    Args:
        config_path: Path to the configuration file, or None.

    Returns:
        Parsed configuration dictionary, or None if no config specified.

    Raises:
        SystemExit: If the config file cannot be loaded.
    """
    if config_path is None:
        return None

    path = Path(config_path)
    if not path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)

    try:
        import json

        content = path.read_text(encoding="utf-8")

        # Handle JSONC (JSON with comments) by stripping comments
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            # Skip full-line comments
            if stripped.startswith("//"):
                continue
            # Remove inline comments (simple approach)
            if "//" in line:
                # Be careful not to remove // inside strings
                # This is a simple heuristic that works for most cases
                in_string = False
                result = []
                i = 0
                while i < len(line):
                    if line[i] == '"' and (i == 0 or line[i - 1] != "\\"):
                        in_string = not in_string
                    if not in_string and line[i : i + 2] == "//":
                        break
                    result.append(line[i])
                    i += 1
                lines.append("".join(result))
            else:
                lines.append(line)

        clean_content = "\n".join(lines)
        return json.loads(clean_content)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
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


def create_registry_from_config(config: dict | None) -> "ToolRegistry":
    """Create a ToolRegistry from configuration.

    Args:
        config: Configuration dictionary, or None for empty registry.

    Returns:
        Configured ToolRegistry instance.
    """
    from toolregistry import ToolRegistry

    registry = ToolRegistry()

    if config is None:
        logger.info("No configuration provided, starting with empty registry")
        return registry

    # Process tools from config
    tools = config.get("tools", [])
    for tool_config in tools:
        # Tool configuration format:
        # Option 1 - Full class path:
        # {
        #     "class": "module.path.ClassName",
        #     "enabled": true,       # optional, default true
        #     "namespace": "ns"      # optional
        # }
        # Option 2 - Separate module and class:
        # {
        #     "module": "module.path",
        #     "class": "ClassName",  # optional
        #     "enabled": true,       # optional, default true
        #     "namespace": "ns"      # optional
        # }
        module_path = tool_config.get("module")
        class_name = tool_config.get("class")
        enabled = tool_config.get("enabled", True)
        namespace = tool_config.get("namespace")

        # Skip disabled tools entirely - don't register them
        if not enabled:
            logger.info(f"Skipping disabled tool: {class_name or module_path}")
            continue

        # Support full class path in "class" field (e.g., "module.path.ClassName")
        if not module_path and class_name and "." in class_name:
            # Split the full class path into module and class name
            parts = class_name.rsplit(".", 1)
            module_path = parts[0]
            class_name = parts[1]

        if not module_path:
            logger.warning(f"Skipping tool config without module: {tool_config}")
            continue

        try:
            import importlib

            module = importlib.import_module(module_path)

            if class_name:
                # Register a class
                cls = getattr(module, class_name)
                instance = cls()
                # register_from_class uses with_namespace parameter
                # If namespace is provided, use it; otherwise use False (no namespace)
                registry.register_from_class(
                    instance, with_namespace=namespace if namespace else False
                )
            else:
                # Register all public functions from module
                for name in dir(module):
                    if not name.startswith("_"):
                        obj = getattr(module, name)
                        if callable(obj) and not isinstance(obj, type):
                            # register uses namespace parameter
                            registry.register(obj, namespace=namespace)

            logger.info(f"Loaded tools from {module_path}")
        except Exception as e:
            logger.warning(f"Failed to load tools from {module_path}: {e}")

    return registry


def run_openapi_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    config_path: str | None = None,
    tokens_path: str | None = None,
    reload: bool = False,
) -> None:
    """Start the OpenAPI server.

    Args:
        host: Host to bind the server to.
        port: Port to bind the server to.
        config_path: Path to configuration file.
        tokens_path: Path to tokens file.
        reload: Enable auto-reload for development.
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

    # Load configuration
    config = load_config(config_path)

    # Create registry from config
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
