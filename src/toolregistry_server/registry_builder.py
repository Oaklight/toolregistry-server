"""Registry construction and profile filtering.

This module provides the core infrastructure for building a
:class:`~toolregistry.ToolRegistry` from configuration files and applying
deployment profile filters.  It is protocol-agnostic — both OpenAPI and MCP
server startup paths use the same functions.

Two-phase usage (recommended for downstream projects like Hub)::

    from toolregistry import ToolRegistry
    from toolregistry_server.registry_builder import apply_config, load_config

    registry = ToolRegistry(name="my-app")
    registry.add_post_register_hook(my_hook)

    # Register built-in tools first ...
    # Then apply user config overrides:
    config = load_config("tools.yaml")
    if config:
        apply_config(registry, config)

One-shot usage (standalone server)::

    from toolregistry_server.registry_builder import registry_from_config, load_config

    config = load_config("tools.yaml")
    registry = registry_from_config(config)
"""

import importlib
from typing import TYPE_CHECKING

from ._vendor.structlog import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from toolregistry import PostRegisterHook, ToolRegistry
    from toolregistry.config import (
        MCPSource,
        OpenAPISource,
        ProfileConfig,
        PythonSource,
        ToolConfig,
        ToolSource,
    )


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def load_config(config_path: str) -> "ToolConfig":
    """Load configuration from a JSONC or YAML file.

    Delegates to ``toolregistry.config.load_config()`` for parsing and
    validation.

    Args:
        config_path: Path to the configuration file.

    Returns:
        Parsed ``ToolConfig``.

    Raises:
        FileNotFoundError: If the config file does not exist.
        toolregistry.config.ConfigError: If the config is invalid.
    """
    from toolregistry.config import load_config as _load_config

    return _load_config(config_path)


# ---------------------------------------------------------------------------
# Source registration helpers
# ---------------------------------------------------------------------------


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


def register_python_source(
    registry: "ToolRegistry",
    source: "PythonSource",
) -> None:
    """Register tools from a Python class or module source.

    For ``class_path`` sources, delegates to
    ``registry.register_from_class(cls, constructor_kwargs=source.kwargs)``,
    which handles static-method-only classes (registered without
    instantiation) and constructor kwargs automatically.

    Args:
        registry: The registry to register tools into.
        source: The Python source configuration.
    """
    ns: bool | str = source.namespace if source.namespace else False

    if source.class_path:
        module_path, class_name = source.class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        registry.register_from_class(
            cls,
            namespace=ns,
            constructor_kwargs=source.kwargs or None,
        )
        logger.info(f"Loaded class tools from {source.class_path}")
    elif source.module_path:
        module = importlib.import_module(source.module_path)
        for name in dir(module):
            if not name.startswith("_"):
                obj = getattr(module, name)
                if callable(obj) and not isinstance(obj, type):
                    registry.register(obj, namespace=source.namespace)
        logger.info(f"Loaded module tools from {source.module_path}")


def register_mcp_source(
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


def register_openapi_source(
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


def _build_ns_tags(config: "ToolConfig") -> dict[str, tuple[str, ...]]:
    """Build a namespace → tags mapping from source declarations in *config*.

    Args:
        config: Parsed ``ToolConfig``.

    Returns:
        Dict mapping namespace strings to their declared tag tuples.
        Only includes sources that have both a namespace and non-empty tags.
    """
    return {
        source.namespace: source.tags
        for source in config.tools
        if source.namespace and source.tags
    }


def _apply_ns_tags(
    registry: "ToolRegistry",
    ns_tags: dict[str, tuple[str, ...]],
) -> None:
    """Apply namespace-based tag declarations to already-registered tools.

    For each tool in *registry* whose namespace appears in *ns_tags*, sets
    ``tool.metadata.tags`` to the corresponding ``ToolTag`` enum set.
    Unknown tag strings are skipped with a warning.

    Args:
        registry: The registry whose tools will be updated.
        ns_tags: Mapping of namespace → tag name tuples.
    """
    from toolregistry.tool import ToolTag

    for tool in registry._tools.values():
        if not tool.namespace or tool.namespace not in ns_tags:
            continue
        tag_enums: set[ToolTag] = set()
        for t in ns_tags[tool.namespace]:
            try:
                tag_enums.add(ToolTag(t))
            except ValueError:
                logger.warning(
                    f"Unknown tag '{t}' for namespace '{tool.namespace}', skipping"
                )
        if tag_enums:
            tool.metadata.tags = tag_enums


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


# ---------------------------------------------------------------------------
# Config application (mutates an existing registry)
# ---------------------------------------------------------------------------


def apply_config(registry: "ToolRegistry", config: "ToolConfig") -> None:
    """Apply a ``ToolConfig`` to an existing registry.

    Registers tool sources from *config* into *registry*, respecting
    allowlist/denylist mode, and applies namespace-level tags and
    ``tool_metadata`` overrides afterwards.

    Individual source registration failures are caught and logged as
    warnings; successfully registered tools remain in the registry.

    Args:
        registry: The registry to mutate.
        config: Parsed ``ToolConfig`` describing sources to register.
    """
    from toolregistry.config import MCPSource, OpenAPISource, PythonSource

    loaded_count = 0
    skipped_count = 0

    # Build namespace → tags mapping from config before registration
    ns_tags = _build_ns_tags(config)

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
                register_python_source(registry, source)
            elif isinstance(source, MCPSource):
                register_mcp_source(registry, source)
            elif isinstance(source, OpenAPISource):
                register_openapi_source(registry, source)
            loaded_count += 1
        except Exception as e:
            source_desc = _describe_source(source)
            logger.warning(f"Failed to load tools from {source_desc}: {e}")

    logger.info(
        f"Applied tool config (mode={config.mode}): "
        f"loaded {loaded_count}, skipped {skipped_count}"
    )

    # Apply tags from config to registered tools
    if ns_tags:
        _apply_ns_tags(registry, ns_tags)

    # Apply tool_metadata overrides (defer, search_hint, etc.)
    if config.tool_metadata:
        registry.apply_metadata_config(config.tool_metadata)


def registry_from_config(
    config: "ToolConfig",
    post_register_hooks: "list[PostRegisterHook] | None" = None,
) -> "ToolRegistry":
    """Create a new ToolRegistry and populate it from configuration.

    Convenience wrapper around :func:`apply_config` for the common
    case of building a registry from scratch::

        config = load_config("tools.yaml")
        registry = registry_from_config(config)

    Args:
        config: Parsed ``ToolConfig`` describing sources to register.
        post_register_hooks: Optional list of hooks invoked after each tool
            is registered.

    Returns:
        A new, fully configured ToolRegistry instance.
    """
    from toolregistry import ToolRegistry

    registry = ToolRegistry()

    if post_register_hooks:
        for hook in post_register_hooks:
            registry.add_post_register_hook(hook)

    apply_config(registry, config)
    return registry


# ---------------------------------------------------------------------------
# Profile-based tag filtering
# ---------------------------------------------------------------------------

#: Tags that represent local-machine-sensitive operations.
_LOCAL_TAGS: frozenset[str] = frozenset({"file_system", "destructive", "privileged"})

#: Tags that represent network-dependent operations.
_NETWORK_TAGS: frozenset[str] = frozenset({"network"})

#: Fallback mapping from profile name → tags to *disable* when no per-profile
#: override is declared in the config file.
PROFILE_DISABLE_TAGS: dict[str, frozenset[str]] = {
    "remote": _LOCAL_TAGS,
    "local": _NETWORK_TAGS,
}


def apply_profile(
    registry: "ToolRegistry",
    profile: str,
    config: "ToolConfig | None" = None,
) -> None:
    """Apply a deployment profile filter to a registry.

    When *config* contains a ``profiles`` entry for *profile*, its settings
    take precedence over the built-in defaults:

    - ``disable_tags``: replaces the built-in tag set used to call
      ``registry.disable_by_tags()``.
    - ``enable``: namespace patterns force-enabled after tag filtering.
    - ``disable``: namespace patterns force-disabled after tag filtering.

    When no matching entry exists in *config*, the function falls back to the
    built-in ``PROFILE_DISABLE_TAGS`` mapping.  Unknown profile names (absent
    from both *config* and the built-in mapping) are logged as a warning and
    ignored.

    Supported built-in profiles (used as fallback defaults):

    - ``remote``: disables tools tagged ``file_system``, ``destructive``, or
      ``privileged`` — tools that operate on the server's own machine and
      have no value to remote end-users.
    - ``local``: disables tools tagged ``network`` — keeps only
      filesystem, shell, and other local-only tools.

    Args:
        registry: The registry to filter in-place.
        profile: Profile name (e.g. ``"remote"`` or ``"local"``).
        config: Optional parsed ``ToolConfig``.  When provided, its
            ``profiles`` dict is consulted first for per-profile overrides.
    """
    from toolregistry.tool import ToolTag

    # Resolve profile config: prefer config-file declaration, fall back to built-ins.
    profile_cfg: ProfileConfig | None = None
    if config is not None and profile in config.profiles:
        profile_cfg = config.profiles[profile]

    if profile_cfg is not None:
        # --- Config-file defined profile ---
        tags_to_disable: frozenset[str] = frozenset(profile_cfg.disable_tags)

        if tags_to_disable:
            tag_enums: set[ToolTag] = {ToolTag(t) for t in tags_to_disable}
            disabled = registry.disable_by_tags(
                tag_enums,
                match="any",
                reason=f"Disabled by profile '{profile}'",
            )
            logger.info(
                f"Profile '{profile}': disabled {len(disabled)} tool(s) "
                f"with tags {sorted(tags_to_disable)}"
            )
        else:
            logger.info(f"Profile '{profile}': no tag filter applied")

        # Apply name-based enable overrides (highest priority)
        for name in profile_cfg.enable:
            registry.enable(name)
            logger.info(f"Profile '{profile}': force-enabled '{name}'")

        # Apply name-based disable overrides
        for name in profile_cfg.disable:
            registry.disable(name, reason=f"Disabled by profile '{profile}'")
            logger.info(f"Profile '{profile}': force-disabled '{name}'")

    elif profile in PROFILE_DISABLE_TAGS:
        # --- Built-in fallback profile ---
        builtin_tags = PROFILE_DISABLE_TAGS[profile]
        if not builtin_tags:
            logger.info(f"Profile '{profile}': no tag filter applied")
            return

        tag_enums = {ToolTag(t) for t in builtin_tags}
        disabled = registry.disable_by_tags(
            tag_enums,
            match="any",
            reason=f"Disabled by profile '{profile}'",
        )
        logger.info(
            f"Profile '{profile}': disabled {len(disabled)} tool(s) "
            f"with tags {sorted(t for t in builtin_tags)}"
        )

    else:
        known = sorted(
            set(PROFILE_DISABLE_TAGS)
            | (set(config.profiles) if config is not None else set())
        )
        logger.warning(f"Unknown profile '{profile}'. Valid profiles: {known}")
        return
