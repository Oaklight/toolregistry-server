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
    from .gateway import GatewayAdapter
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
        #: Upstream OAuth client provider built from the config, when the
        #: file declares one.  Kept so the stores can be closed and so an
        #: embedder can reach the panel-facing interface.
        self.oauth_client_provider = None
        #: The running config watcher, when ``config_reload`` is enabled.
        self.config_watcher = None
        #: The gateway adapter created by :meth:`create_gateway`, if any.
        self._gateway: GatewayAdapter | None = None

    @property
    def gateway(self) -> GatewayAdapter | None:
        """The gateway adapter created by :meth:`create_gateway`, if any."""
        return self._gateway

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

            from .oauth.upstream.config import load_oauth_clients
            from .oauth.upstream.provider import OAuthClientProvider

            config = load_config(config_path)
            registry = _ToolRegistry()
            declarations = load_oauth_clients(config_path, config)
            self.oauth_client_provider = (
                OAuthClientProvider(declarations) if declarations else None
            )
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
                oauth_provider=self.oauth_client_provider,
            )
            manager.apply_initial()
        else:
            raise ValueError(
                "Either 'config_path' or 'registry' must be provided. "
                "Pass a config file path or a pre-built ToolRegistry."
            )

        if manager is not None:
            import atexit

            # The token stores are SQLite connections; release them when the
            # process ends.  A provider created by a config reload lives on
            # the manager, so registration cannot depend on a startup
            # provider existing.  A test or embedder calls
            # close_oauth_clients itself.
            atexit.register(self.close_oauth_clients)

        if profile is not None:
            apply_profile(registry, profile)

        self.source_manager = manager
        return registry

    def _make_route_table(self, registry: ToolRegistry) -> RouteTable:
        """Create a RouteTable from a registry and apply config overrides.

        ``tool_parameters`` entries are applied here, after registration and
        at the single point the table is built, so every adapter sees the
        same served schemas.
        """
        from .registry_builder import apply_parameter_overrides
        from .route_table import RouteTable

        route_table = RouteTable(registry)
        manager = self.source_manager
        if manager is not None:
            apply_parameter_overrides(route_table, manager.config)
        return route_table

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
        self._attach_source_manager(route_table, **kwargs)
        adapter_cls.create_and_run(route_table, **kwargs)

    def _attach_source_manager(self, route_table: RouteTable, **kwargs) -> None:
        """Attach the runtime source manager to *route_table*, if present.

        The gateway builds its own route table instead of calling
        :meth:`serve`, so both paths use this helper to keep runtime source
        changes and the config watcher working.

        Args:
            route_table: The route table that will serve the registry.
            **kwargs: Startup options; ``config_reload`` enables the watcher.
        """
        manager = getattr(self, "source_manager", None)
        if manager is None:
            return
        # One source diff produces one route rebuild instead of one per
        # registry event.
        manager.attach_route_table(route_table)
        if kwargs.get("config_reload"):
            self._start_config_watcher(manager)

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

    def close_oauth_clients(self) -> None:
        """Close the upstream OAuth token stores, if any.

        :meth:`prepare_registry` registers this with :mod:`atexit` whenever it
        builds a source manager, because a config reload can create the
        provider later; an embedder or a test can also call this to release
        the stores sooner.  Both references are dropped, so a later reload
        creates a new provider instead of using a closed connection.  A
        provider created by a config reload lives on the source manager, so
        both are closed.  Calling it twice is harmless.
        """
        manager = self.source_manager
        for provider in (
            self.oauth_client_provider,
            getattr(manager, "oauth_provider", None),
        ):
            if provider is not None:
                provider.close()
        self.oauth_client_provider = None
        if manager is not None:
            manager.oauth_provider = None

    def serve_openapi(self, **kwargs) -> None:
        """Build registry and start an OpenAPI server."""
        from .adapters.openapi import OpenAPIAdapter

        self.serve(OpenAPIAdapter, **kwargs)

    def serve_mcp(self, **kwargs) -> None:
        """Build registry and start an MCP server."""
        from .adapters.mcp import MCPAdapter

        self.serve(MCPAdapter, **kwargs)

    def create_gateway(self, **kwargs) -> GatewayAdapter:
        """Build a gateway adapter without starting its listener.

        The returned object exposes :meth:`GatewayAdapter.list_frontends`
        and :meth:`GatewayAdapter.update_frontend`, so an in-process admin
        extension can change a frontend without an HTTP round trip.

        Args:
            **kwargs: Split between :meth:`prepare_registry` and gateway
                construction options.  ``config_path`` or ``registry`` is
                required, as for :meth:`serve`.

        Returns:
            The configured gateway adapter.
        """
        from .gateway import GatewayAdapter

        kwargs.setdefault("identity", self.identity)
        registry = self.prepare_registry(**kwargs)
        route_table = self._make_route_table(registry)
        self._attach_source_manager(route_table, **kwargs)
        options = dict(kwargs)
        options["registry"] = registry
        options["source_manager"] = self.source_manager
        gateway = GatewayAdapter.create(route_table, **options)
        self._gateway = gateway
        return gateway

    def serve_gateway(self, **kwargs) -> None:
        """Build registry and serve all enabled frontends from one process."""
        gateway = self.create_gateway(**kwargs)
        gateway.run(
            host=kwargs.get("host", "0.0.0.0"),
            port=kwargs.get("port", 8000),
            reload=kwargs.get("reload", False),
        )


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


def serve_gateway(**kwargs) -> None:
    """Build registry and serve all enabled gateway frontends."""
    _get_default_app().serve_gateway(**kwargs)
