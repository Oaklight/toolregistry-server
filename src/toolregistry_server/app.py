"""Application-level server orchestration.

Provides :class:`App` — the programmatic entry point for running
servers.  Downstream packages subclass it to customize registry
construction.

Example — standalone::

    from toolregistry_server import serve_openapi
    serve_openapi(config_path="tools.yaml", host="0.0.0.0", port=8000)

Example — subclass (e.g. Hub)::

    from toolregistry_server.app import App

    class HubApp(App):
        def prepare_registry(self, **kwargs):
            return build_hub_registry(...)

    app = HubApp()
    app.serve_mcp(transport="stdio")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._vendor.structlog import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from toolregistry import ToolRegistry

    from .adapters import Adapter
    from .route_table import RouteTable


class App:
    """Server application — builds registry and dispatches to adapters.

    Override :meth:`prepare_registry` to customize how the registry
    is constructed (e.g. built-in tools, hooks, metadata overrides).
    """

    def prepare_registry(self, **kwargs) -> ToolRegistry:
        """Build or resolve a ``ToolRegistry``.

        Default implementation builds from ``config_path`` or uses a
        pre-provided ``registry``.  Override in subclasses to add
        custom tools, hooks, or metadata.

        Keyword Args:
            config_path: Path to a JSONC/YAML config file.
            registry: Pre-built registry (returned as-is).
            profile: Deployment profile for tag-based filtering.

        Returns:
            A configured ``ToolRegistry`` instance.
        """
        from .registry_builder import apply_profile, load_config, registry_from_config

        registry: ToolRegistry | None = kwargs.get("registry")
        config_path: str | None = kwargs.get("config_path")
        profile: str | None = kwargs.get("profile")

        if registry is not None:
            pass
        elif config_path is not None:
            config = load_config(config_path)
            registry = registry_from_config(config)
        else:
            raise ValueError(
                "Either 'config_path' or 'registry' must be provided. "
                "Pass a config file path or a pre-built ToolRegistry."
            )

        if profile is not None:
            apply_profile(registry, profile)

        return registry

    def _make_route_table(self, registry: ToolRegistry) -> RouteTable:
        """Create a RouteTable from a registry."""
        from .route_table import RouteTable

        return RouteTable(registry)

    def serve(self, adapter_cls: type[Adapter], **kwargs) -> None:
        """Build registry and serve via any adapter.

        Args:
            adapter_cls: The adapter class to use (e.g. OpenAPIAdapter).
            **kwargs: Split between :meth:`prepare_registry` and
                ``adapter_cls.create_and_run``.
        """
        registry = self.prepare_registry(**kwargs)
        route_table = self._make_route_table(registry)
        adapter_cls.create_and_run(route_table, **kwargs)

    def serve_openapi(self, **kwargs) -> None:
        """Build registry and start an OpenAPI server.

        Convenience wrapper for ``serve(OpenAPIAdapter, ...)``.
        """
        from .adapters.openapi import OpenAPIAdapter

        self.serve(OpenAPIAdapter, **kwargs)

    def serve_mcp(self, **kwargs) -> None:
        """Build registry and start an MCP server.

        Convenience wrapper for ``serve(MCPAdapter, ...)``.
        """
        from .adapters.mcp import MCPAdapter

        self.serve(MCPAdapter, **kwargs)


# ---------------------------------------------------------------------------
# Module-level convenience functions (default App instance)
# ---------------------------------------------------------------------------

_default_app = App()

serve_openapi = _default_app.serve_openapi
serve_mcp = _default_app.serve_mcp
