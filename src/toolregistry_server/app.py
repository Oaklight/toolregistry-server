"""Application-level server orchestration.

Provides :class:`App` — the programmatic entry point for running
servers.  Downstream packages subclass it to customize registry
construction and identity.

Design
------

``App`` is intentionally adapter-agnostic.  The core method is
``serve(adapter_cls, **kwargs)`` which:

1. Calls ``self.prepare_registry(**kwargs)`` to build a registry
2. Wraps it in a ``RouteTable``
3. Delegates to ``adapter_cls.create_and_run(route_table, **kwargs)``

Identity (product name, version, banner art) is set once on the
``App`` and automatically flows to adapters — OpenAPI title, MCP
server name, CLI banner.

``serve_openapi`` / ``serve_mcp`` are convenience wrappers that
bind a specific adapter class.  They exist so that end users don't
need to import adapter classes for the common case::

    # Quick start — no adapter import needed:
    from toolregistry_server import serve_openapi
    serve_openapi(config_path="tools.yaml")

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
        def __init__(self):
            super().__init__(identity=ServerIdentity(
                name="ToolRegistry Hub",
                version=hub_version,
                banner_art=HUB_BANNER,
            ))

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
    from .identity import ServerIdentity
    from .route_table import RouteTable
    from .source_manager import SourceManager


class App:
    """Server application — builds registry and dispatches to adapters.

    Args:
        identity: Server identity (name, version, description, banner art).
            Defaults to ``ServerIdentity()`` (toolregistry-server branding).

    Override :meth:`prepare_registry` to customize how the registry
    is constructed (e.g. built-in tools, hooks, metadata overrides).
    """

    def __init__(self, identity: ServerIdentity | None = None) -> None:
        from .identity import ServerIdentity

        self.identity = identity or ServerIdentity()
        #: Populated by :meth:`prepare_registry` when a config file was
        #: loaded.  Downstream code (admin extensions) uses it to change the
        #: live source list.
        self.source_manager = None
        #: The running config watcher, when ``config_reload`` is enabled.
        self.config_watcher = None

    def prepare_registry(self, **kwargs) -> ToolRegistry:
        """Build or resolve a ``ToolRegistry``.

        Default implementation builds from ``config_path`` or uses a
        pre-provided ``registry``.  Override in subclasses to add
        custom tools, hooks, or metadata.

        Keyword Args:
            config_path: Path to a JSONC/YAML config file.
            registry: Pre-built registry (returned as-is).
            profile: Deployment profile for tag-based filtering.
            strict_spec_regex: Abort startup on an invalid regex in an
                OpenAPI source instead of skipping that source.
            write_config: Persist runtime source changes back to
                ``config_path``.  ``False`` keeps them in memory only.

        Returns:
            A configured ``ToolRegistry`` instance.
        """
        from .registry_builder import apply_profile, load_config
        from .source_manager import SourceManager

        registry: ToolRegistry | None = kwargs.get("registry")
        config_path: str | None = kwargs.get("config_path")
        profile: str | None = kwargs.get("profile")

        manager: SourceManager | None = None
        if registry is not None:
            pass
        elif config_path is not None:
            from toolregistry import ToolRegistry as _ToolRegistry

            config = load_config(config_path)
            registry = _ToolRegistry()
            # The manager owns the source list from startup: it registers the
            # sources, records which names each produced, and is the object a
            # runtime add/remove/replace mutates.
            manager = SourceManager(
                registry,
                config,
                config_path=config_path,
                profile=profile,
                strict_spec_regex=kwargs.get("strict_spec_regex", False),
                write_config=kwargs.get("write_config", True),
            )
            manager.apply_initial()
        else:
            raise ValueError(
                "Either 'config_path' or 'registry' must be provided. "
                "Pass a config file path or a pre-built ToolRegistry."
            )

        if profile is not None:
            apply_profile(registry, profile)

        self.source_manager = manager
        return registry

    def _make_route_table(self, registry: ToolRegistry) -> RouteTable:
        """Create a RouteTable from a registry."""
        from .route_table import RouteTable

        return RouteTable(registry)

    def serve(self, adapter_cls: type[Adapter], **kwargs) -> None:
        """Build registry and serve via any adapter.

        Identity is injected into kwargs so adapters can use it
        (e.g. OpenAPI title, MCP server name).

        Args:
            adapter_cls: The adapter class to use (e.g. OpenAPIAdapter).
            **kwargs: Split between :meth:`prepare_registry` and
                ``adapter_cls.create_and_run``.
        """
        # Inject identity so adapters can read it
        kwargs.setdefault("identity", self.identity)

        registry = self.prepare_registry(**kwargs)
        route_table = self._make_route_table(registry)
        manager = getattr(self, "source_manager", None)
        if manager is not None:
            # One source diff produces one route rebuild instead of one per
            # registry event.
            manager.attach_route_table(route_table)
            if kwargs.get("config_reload"):
                self._start_config_watcher(manager)
        adapter_cls.create_and_run(route_table, **kwargs)

    def _start_config_watcher(self, manager: SourceManager) -> None:
        """Start the config watcher, or explain why it cannot run."""
        import atexit

        from .source_manager import ConfigWatcher

        config_path = getattr(manager, "config_path", None)
        if not config_path:
            logger.warning(
                "--config-reload was requested, but no config file was loaded; "
                "nothing to watch"
            )
            return
        watcher = ConfigWatcher(manager)
        watcher.start()
        self.config_watcher = watcher
        atexit.register(self.stop_config_watcher)
        logger.info(f"Config hot reload watching {config_path}")

    def stop_config_watcher(self, timeout: float | None = 2.0) -> None:
        """Stop the config watcher, if one is running.

        Args:
            timeout: Seconds to wait for the thread to exit, or ``None``.
        """
        watcher = self.config_watcher
        if watcher is None:
            return
        watcher.stop(timeout=timeout)
        self.config_watcher = None

    def serve_openapi(self, **kwargs) -> None:
        """Build registry and start an OpenAPI server."""
        from .adapters.openapi import OpenAPIAdapter

        self.serve(OpenAPIAdapter, **kwargs)

    def serve_mcp(self, **kwargs) -> None:
        """Build registry and start an MCP server."""
        from .adapters.mcp import MCPAdapter

        self.serve(MCPAdapter, **kwargs)


# ---------------------------------------------------------------------------
# Module-level convenience functions (lazy-init default App)
# ---------------------------------------------------------------------------

_default_app: App | None = None


def _get_default_app() -> App:
    """Return the shared default App, creating it on first use."""
    global _default_app
    if _default_app is None:
        _default_app = App()
    return _default_app


def serve_openapi(**kwargs) -> None:
    """Build registry and start an OpenAPI server."""
    _get_default_app().serve_openapi(**kwargs)


def serve_mcp(**kwargs) -> None:
    """Build registry and start an MCP server."""
    _get_default_app().serve_mcp(**kwargs)
