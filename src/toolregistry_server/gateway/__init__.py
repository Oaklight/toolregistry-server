"""Single-process gateway for the OpenAPI and MCP frontends.

The gateway builds one ASGI application that serves the OpenAPI adapter and
both MCP HTTP transports from the same registry and auth configuration.

Main components:

* :class:`GatewayAdapter` — builds and runs the gateway and exposes
  :meth:`GatewayAdapter.list_frontends` /
  :meth:`GatewayAdapter.update_frontend` for in-process administration.
* :class:`FrontendRegistry` — per-request path and enable-flag state.
* :func:`create_gateway_app` — build the ASGI application without running
  a listener.

Example:
    ```python
    from toolregistry_server import App

    gateway = App().create_gateway(config_path="tools.yaml")
    print(gateway.list_frontends())
    gateway.update_frontend("sse", enabled=False)
    gateway.run(port=8000)
    ```
"""

from .app import GatewayAdapter, create_gateway_app, resolve_frontends
from .frontends import (
    DEFAULT_PATHS,
    FRONTEND_NAMES,
    TRANSPORTS,
    Frontend,
    FrontendRegistry,
    default_frontends,
    normalize_path,
)
from .persistence import load_frontend_settings, persist_frontend_settings

__all__ = [
    "DEFAULT_PATHS",
    "FRONTEND_NAMES",
    "TRANSPORTS",
    "Frontend",
    "FrontendRegistry",
    "GatewayAdapter",
    "create_gateway_app",
    "default_frontends",
    "load_frontend_settings",
    "normalize_path",
    "persist_frontend_settings",
    "resolve_frontends",
]
