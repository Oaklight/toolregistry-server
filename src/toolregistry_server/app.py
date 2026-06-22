"""Application-level server orchestration.

Provides :class:`App` — the programmatic entry point for running
servers.  Downstream packages subclass it to customize registry
construction.

Design
------

``App`` is intentionally adapter-agnostic.  The core method is
``serve(adapter_cls, **kwargs)`` which:

1. Calls ``self.prepare_registry(**kwargs)`` to build a registry
2. Wraps it in a ``RouteTable``
3. Delegates to ``adapter_cls.create_and_run(route_table, **kwargs)``

``serve_openapi`` / ``serve_mcp`` are convenience wrappers that
bind a specific adapter class.  They exist so that end users don't
need to import adapter classes for the common case::

    # Quick start — no adapter import needed:
    from toolregistry_server import serve_openapi
    serve_openapi(config_path="tools.yaml")

    # Equivalent explicit form:
    from toolregistry_server.app import App
    from toolregistry_server.adapters.openapi import OpenAPIAdapter
    App().serve(OpenAPIAdapter, config_path="tools.yaml")

Adding a new adapter
~~~~~~~~~~~~~~~~~~~~

When adding a new adapter (e.g. gRPC), no changes are needed in
``App`` — just implement the adapter with ``create_and_run`` and
call ``app.serve(GRPCAdapter, ...)``.  Optionally add a
``serve_grpc`` convenience wrapper on ``App`` and a module-level
shortcut.

Subclassing
~~~~~~~~~~~

Override ``prepare_registry`` to customize how the registry is
built (e.g. built-in tools, hooks, admin panel)::

    class HubApp(App):
        def prepare_registry(self, **kwargs):
            registry = build_hub_registry(...)
            if kwargs.get("admin_port"):
                registry.enable_admin(port=kwargs["admin_port"])
            return registry

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
