"""
Central Router Table for ToolRegistry.

This module provides the RouteTable class that bridges ToolRegistry and
protocol adapters (OpenAPI, MCP, etc.).
"""

import copy
import functools
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from toolregistry import ToolRegistry
from toolregistry.events import ChangeEvent, ChangeEventType
from toolregistry.tool import Tool
from toolregistry.utils import compute_schema_hash


def _extract_output_schema(tool: Tool) -> dict[str, Any] | None:
    """Return a tool's declared output schema, if any.

    Tool authors declare an output schema via ``metadata.extra['output_schema']``.
    """
    metadata = getattr(tool, "metadata", None)
    extra = getattr(metadata, "extra", None)
    if not isinstance(extra, dict):
        return None
    schema = extra.get("output_schema")
    if not isinstance(schema, dict) or not schema:
        return None
    return schema


def normalize_parameters_schema(schema: Any) -> dict[str, Any]:
    """Return a canonical object schema for tool parameters."""
    if not isinstance(schema, dict) or schema.get("type") not in (None, "object"):
        return {"type": "object", "properties": {}}

    normalized = dict(schema)
    normalized["type"] = "object"
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    return normalized


#: The hint names a route override may set.  Keys are the transport-neutral
#: ``ToolMetadata`` field names; the adapters map them to their wire spelling.
_EXECUTION_HINT_NAMES: frozenset[str] = frozenset(
    {
        "read_only_hint",
        "destructive_hint",
        "idempotent_hint",
        "open_world_hint",
    }
)


def _validate_parameter_override(parameters: Any) -> dict[str, Any]:
    """Return a deep-copied object schema, rejecting a wrong shape.

    An override that is not an object schema used to be silently coerced to
    ``{"type": "object", "properties": {}}``, which then dropped every
    argument.  Raising here keeps a malformed override from changing what
    the server accepts without saying so.

    An omitted ``additionalProperties`` is filled with ``False`` so the
    published schema says exactly what request validation enforces.
    """
    if not isinstance(parameters, dict):
        raise TypeError("parameter override must be a JSON Schema object")
    schema_type = parameters.get("type")
    if isinstance(schema_type, list):
        if "object" not in schema_type:
            raise ValueError("parameter override must describe an object schema")
    elif schema_type not in (None, "object"):
        raise ValueError("parameter override must describe an object schema")
    properties = parameters.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise ValueError("parameter override 'properties' must be a mapping")

    copied = copy.deepcopy(parameters)
    if isinstance(copied.get("type"), list):
        # ``["object", "null"]`` is legal JSON Schema; the request path only
        # produces an object, so the served schema is normalized to object.
        copied["type"] = "object"
    normalized = normalize_parameters_schema(copied)
    normalized.setdefault("additionalProperties", False)
    return normalized


def _allows_additional_properties(schema: dict[str, Any]) -> bool:
    """Whether the served schema permits keys outside ``properties``.

    ``additionalProperties`` is a JSON Schema, not a boolean: ``{}`` and any
    other non-``False`` value allow extra keys, and only an explicit
    ``False`` forbids them.
    """
    return (
        "additionalProperties" in schema and schema["additionalProperties"] is not False
    )


def _validate_execution_hints(hints: Any) -> dict[str, bool]:
    """Return a copied hint mapping, rejecting unknown names and non-bools."""
    if not isinstance(hints, dict):
        raise TypeError("execution hint override must be a mapping")
    unknown = sorted(set(hints) - _EXECUTION_HINT_NAMES)
    if unknown:
        raise ValueError(
            f"unknown execution hint(s) {unknown}; "
            f"valid names are {sorted(_EXECUTION_HINT_NAMES)}"
        )
    invalid = sorted(
        name for name, value in hints.items() if not isinstance(value, bool)
    )
    if invalid:
        raise ValueError(f"execution hint(s) {invalid} must be booleans")
    return dict(hints)


@dataclass
class RouteEntry:
    """A single route entry in the central router table.

    Holds a direct reference to the underlying :class:`~toolregistry.tool.Tool`
    instead of copying its fields.  Backward-compatible properties expose
    commonly accessed tool attributes (``tool_name``, ``description``, etc.)
    so that existing adapter code continues to work unchanged.

    Attributes:
        tool: Direct reference to the Tool instance.
        path: HTTP path for the route (e.g., "/tools/calculator/evaluate").
        handler: The callable used to execute the tool (may differ from
            ``tool.callable`` for session-scoped handlers).
        is_async: Whether the handler is async (cached from tool).
        handler_factory: Optional factory for session-scoped handlers.
        output_schema: Optional JSON Schema for the tool result
            (MCP ``outputSchema``).
        enabled: Whether the tool is currently enabled.
        disable_reason: Reason for disabling, if disabled.
        parameter_schema_override: Schema served instead of the tool's own,
            or ``None`` to serve the tool's schema.
        execution_hints_override: Hints served instead of the resolved ones,
            or ``None`` to serve the resolved mapping.  An empty mapping
            suppresses every annotation/extension for this route.
        include_derived_hints: Whether to publish hints derived from policy
            tags.  ``False`` (the default) publishes only explicit
            declarations; a tag alone makes no claim on the wire.
    """

    # Tool reference
    tool: Tool

    # Route metadata
    path: str

    # Execution
    handler: Callable[..., Any]
    is_async: bool

    # Session-scoped handler factory (optional)
    handler_factory: Callable[..., Callable[..., Any]] | None = None

    # Optional JSON Schema for the tool result (MCP ``outputSchema``)
    output_schema: dict[str, Any] | None = None

    # State
    enabled: bool = True
    disable_reason: str | None = None

    # Route-level overrides.  The registry Tool is never modified; these only
    # change what the adapters publish for this route.
    parameter_schema_override: dict[str, Any] | None = None
    execution_hints_override: dict[str, bool] | None = None
    include_derived_hints: bool = False
    #: Back-reference to the owning table, so ``served_execution_hints``
    #: reads the live policy flag instead of a value copied at build time.
    _table_ref: Any = field(default=None, repr=False, compare=False)

    # --- Backward-compatible properties -----------------------------------

    @property
    def tool_name(self) -> str:
        """Full tool name (e.g. ``"calculator-evaluate"``)."""
        return self.tool.name

    @property
    def namespace(self) -> str:
        """Tool namespace, defaulting to ``"default"``."""
        return getattr(self.tool, "namespace", None) or "default"

    @property
    def method_name(self) -> str:
        """Method name within namespace."""
        return getattr(self.tool, "method_name", None) or self.tool.name

    @property
    def description(self) -> str:
        """Tool description."""
        return self.tool.description or ""

    @functools.cached_property
    def parameters_schema(self) -> dict[str, Any]:
        """Canonical JSON Schema for tool parameters.

        Cached because the underlying Tool is frozen and its parameters
        never change after construction.
        """
        return normalize_parameters_schema(self.tool.parameters)

    @property
    def parameters_model(self) -> Any | None:
        """Parameters model from the tool, if available."""
        return getattr(self.tool, "parameters_model", None)

    @property
    def deferred(self) -> bool:
        """Whether the tool is deferred (discovered via ``discover_tools``)."""
        metadata = getattr(self.tool, "metadata", None)
        return bool(getattr(metadata, "defer", False))

    @property
    def title(self) -> str | None:
        """Human-readable display title, or ``None`` when none is declared."""
        metadata = getattr(self.tool, "metadata", None)
        title = getattr(metadata, "title", None)
        return title if isinstance(title, str) and title else None

    @property
    def execution_hints(self) -> dict[str, bool]:
        """Resolved execution hints for the tool.

        Returns the transport-neutral mapping exposed by
        ``Tool.execution_hints`` (snake_case keys, unset hints absent).
        Empty when the tool declares none or predates the metadata fields,
        so adapters can serialize it without a version check.
        """
        hints = getattr(self.tool, "execution_hints", None)
        return hints if isinstance(hints, dict) else {}

    @property
    def served_parameters_schema(self) -> dict[str, Any]:
        """Parameter schema to publish, honoring the route override."""
        if self.parameter_schema_override is not None:
            # Copy on read as well as on write: an adapter that mutates the
            # schema it was handed must not change what the route serves.
            return normalize_parameters_schema(
                copy.deepcopy(self.parameter_schema_override)
            )
        return self.parameters_schema

    @property
    def declared_execution_hints(self) -> dict[str, bool]:
        """Explicitly declared execution hints, excluding tag-derived values.

        Prefers ``Tool.declared_execution_hints`` when the installed core
        provides it.  Otherwise reconstructs the same mapping from the
        ``ToolMetadata`` hint fields, which exist on the 0.19.x floor; a core
        that predates those fields yields an empty mapping, which is the
        safe "no claim".  The server therefore does not depend on the core
        release that adds the property.
        """
        hints = getattr(self.tool, "declared_execution_hints", None)
        if isinstance(hints, dict):
            return hints
        metadata = getattr(self.tool, "metadata", None)
        if metadata is None:
            return {}
        declared: dict[str, bool] = {}
        for name in _EXECUTION_HINT_NAMES:
            value = getattr(metadata, name, None)
            if value is not None:
                declared[name] = value
        if (
            "destructive_hint" not in declared
            and declared.get("read_only_hint") is True
        ):
            declared["destructive_hint"] = False
        return declared

    @property
    def served_execution_hints(self) -> dict[str, bool]:
        """Execution hints to publish.

        An explicit route override wins.  Otherwise a tool publishes only
        what it declared; tag-derived hints are included only when the table
        opted in.  The flag is read from the owning table when the entry is
        attached to one, so assigning ``RouteTable.emit_derived_execution_hints``
        takes effect immediately instead of silently doing nothing.  An
        override of ``{}`` suppresses every annotation and extension, which
        is how a consumer says "no claim" for a tool it has not rated.
        """
        if self.execution_hints_override is not None:
            return dict(self.execution_hints_override)
        include_derived = (
            self._table_ref.emit_derived_execution_hints
            if self._table_ref is not None
            else self.include_derived_hints
        )
        if include_derived:
            return self.execution_hints
        return self.declared_execution_hints

    @property
    def schema_hash(self) -> str:
        """Content hash of the parameter schema this route serves.

        Equal to the tool's own ``metadata.schema_hash`` until an override
        is active; then it describes the override, because that is the
        schema the payload publishes.  The registry tool's hash is never
        modified.
        """
        if self.parameter_schema_override is not None:
            return compute_schema_hash(self.served_parameters_schema)
        metadata = getattr(self.tool, "metadata", None)
        return getattr(metadata, "schema_hash", "") or ""

    @property
    def last_refreshed_at(self) -> str:
        """ISO 8601 timestamp of the last schema refresh."""
        metadata = getattr(self.tool, "metadata", None)
        return getattr(metadata, "last_refreshed_at", "") or ""


@dataclass
class RouteTable:
    """Central router table that bridges ToolRegistry and protocol adapters.

    The RouteTable provides a unified view of all registered tools and their
    routing information. It supports:

    - Querying routes by name or listing all routes
    - Enabling/disabling tools dynamically
    - Observer pattern for change notifications
    - ETag generation for cache validation

    Example:
        ```python
        from toolregistry import ToolRegistry
        from toolregistry_server import RouteTable

        registry = ToolRegistry()

        @registry.register
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        route_table = RouteTable(registry)
        routes = route_table.list_routes()
        print(routes[0].path)
        # /tools/default/greet
        ```
    """

    _registry: ToolRegistry

    #: Whether adapters publish hints derived from policy tags.  ``False``
    #: (the default) publishes only explicit declarations: a tag chosen for
    #: local permission policy must not become a client auto-approve claim.
    emit_derived_execution_hints: bool = False

    _routes: dict[str, RouteEntry] = field(default_factory=dict, init=False)
    _listeners: list[Callable[[str, str], None]] = field(
        default_factory=list, init=False
    )
    _version: int = field(default=0, init=False)
    _parameter_overrides: dict[str, dict[str, Any]] = field(
        default_factory=dict, init=False
    )
    _execution_hint_overrides: dict[str, dict[str, bool]] = field(
        default_factory=dict, init=False
    )

    def __post_init__(self) -> None:
        """Initialize the route table from the registry."""
        self._rebuild()
        self._registry.on_change(self._on_registry_change)

    def _on_registry_change(self, event: ChangeEvent) -> None:
        """Handle change events from the underlying ToolRegistry.

        Keeps the RouteTable in sync when tools are enabled/disabled/registered
        externally (e.g. via the admin panel).  Handles both tool-level and
        namespace-level enable/disable events.
        """
        if event.event_type in (
            ChangeEventType.ENABLE,
            ChangeEventType.DISABLE,
            ChangeEventType.METADATA_UPDATE,
        ):
            if event.tool_name:
                if event.tool_name in self._routes:
                    # Direct tool-level change
                    self.refresh(event.tool_name)
                    self._notify_listeners(event.tool_name, event.event_type.value)
                else:
                    # Namespace-level change - refresh all tools in that namespace
                    for route in self._routes.values():
                        if route.namespace == event.tool_name:
                            self.refresh(route.tool_name)
                    self._notify_listeners(event.tool_name, event.event_type.value)
        elif event.event_type in (
            ChangeEventType.REGISTER,
            ChangeEventType.UNREGISTER,
            ChangeEventType.REFRESH_ALL,
        ):
            self.refresh_all()

    def _rebuild(self) -> None:
        """Rebuild route table from registry."""
        self._routes.clear()
        for tool_name, tool in self._registry._tools.items():
            self._routes[tool_name] = self._tool_to_route(tool)

    def _tool_to_route(self, tool: Tool) -> RouteEntry:
        """Convert a Tool to a RouteEntry.

        Args:
            tool: The Tool instance to convert.

        Returns:
            A RouteEntry representing the tool.
        """
        namespace = getattr(tool, "namespace", None) or "default"
        method_name = getattr(tool, "method_name", None) or tool.name

        return RouteEntry(
            tool=tool,
            path=f"/tools/{namespace}/{method_name}",
            handler=tool.callable,
            is_async=tool.is_async,
            output_schema=_extract_output_schema(tool),
            enabled=self._registry.is_enabled(tool.name),
            disable_reason=self._registry.get_disable_reason(tool.name),
            parameter_schema_override=self._parameter_overrides.get(tool.name),
            execution_hints_override=self._execution_hint_overrides.get(tool.name),
            include_derived_hints=self.emit_derived_execution_hints,
            _table_ref=self,
        )

    # ============== Query API ==============

    def list_routes(
        self,
        enabled_only: bool = True,
        include_deferred: bool = True,
    ) -> list[RouteEntry]:
        """List all routes, optionally filtering by enabled state or defer flag.

        Args:
            enabled_only: If True, only return enabled routes.
            include_deferred: If False, exclude routes whose tool has
                ``metadata.defer == True``.  Use ``False`` when building
                the initial tool list presented to an LLM so that deferred
                tools are only surfaced via ``discover_tools``.

        Returns:
            List of RouteEntry objects.
        """
        routes = self._routes.values()
        if enabled_only:
            routes = (r for r in routes if r.enabled)
        if not include_deferred:
            routes = (r for r in routes if not r.deferred)
        return list(routes)

    def get_route(self, tool_name: str) -> RouteEntry | None:
        """Get a specific route by tool name.

        Args:
            tool_name: The name of the tool.

        Returns:
            The RouteEntry if found, None otherwise.
        """
        return self._routes.get(tool_name)

    @property
    def etag(self) -> str:
        """Get ETag for cache validation.

        Returns:
            An ETag string based on the current version.
        """
        return f'"{self._version}"'

    @property
    def version(self) -> int:
        """Get the current version number.

        Returns:
            The version number, incremented on each change.
        """
        return self._version

    # ============== State Change API ==============

    def enable(self, tool_name: str) -> None:
        """Enable a tool.

        The route table is automatically refreshed via the registry change
        callback.

        Args:
            tool_name: The name of the tool to enable.

        Raises:
            KeyError: If the tool is not found.
        """
        self._registry.enable(tool_name)

    def disable(self, tool_name: str, reason: str = "") -> None:
        """Disable a tool.

        The route table is automatically refreshed via the registry change
        callback.

        Args:
            tool_name: The name of the tool to disable.
            reason: Optional reason for disabling.

        Raises:
            KeyError: If the tool is not found.
        """
        self._registry.disable(tool_name, reason)

    def refresh(self, tool_name: str) -> None:
        """Refresh a single route's state from registry.

        Args:
            tool_name: The name of the tool to refresh.
        """
        tool = self._registry.get_tool(tool_name)
        if tool:
            self._routes[tool_name] = self._tool_to_route(tool)

    def refresh_all(self) -> None:
        """Refresh all routes from registry."""
        self._rebuild()
        self._notify_listeners("*", "refresh_all")

    @staticmethod
    def _reconcile_override_with_model(
        tool_name: str, route: RouteEntry, schema: dict[str, Any]
    ) -> None:
        """Make the served schema agree with the tool's validation model.

        An override built from ``properties`` alone carries no ``required``
        list, so the served schema would advertise the model's required
        fields as optional.  Fill them in from the model, then warn only when
        a model-required field is genuinely absent from the override.
        """
        model = getattr(route.tool, "parameters_model", None)
        if model is None:
            return

        additional = schema.get("additionalProperties")
        if isinstance(additional, dict) and additional:
            raise ValueError(
                f"parameter override for '{tool_name}' uses a typed "
                "additionalProperties, which a model-backed tool cannot "
                "enforce; use true, false, or {}"
            )

        required = getattr(model, "__required_keys__", None)
        if not required:
            return
        declared = set(schema.get("properties", {}))
        if "required" not in schema:
            schema["required"] = sorted(set(required) & declared)
        missing = sorted(set(required) - set(schema.get("required", ())))
        if missing:
            warnings.warn(
                f"parameter override for '{tool_name}' omits required field(s) "
                f"{missing}; the tool's validation model still requires them.",
                UserWarning,
                stacklevel=3,
            )

    def override_parameters(
        self, tool_name: str, parameters: dict[str, Any] | None
    ) -> None:
        """Override the parameter schema published for one route.

        The registry ``Tool`` and its ``metadata.schema_hash`` are untouched;
        only the payload built by the adapters changes.  The route's
        :attr:`RouteEntry.schema_hash` describes the served schema, so it
        changes with the override.  The override is remembered across
        ``refresh()`` and ``refresh_all()``.  Passing ``None`` clears it and
        restores the tool's own schema.  A route override takes precedence
        over ``ToolRegistry.update_tool_metadata()`` for this route.

        Args:
            tool_name: Registered tool name.
            parameters: Replacement JSON Schema, or ``None`` to clear.

        Raises:
            KeyError: If the route does not exist.
            TypeError: If *parameters* is not a mapping.
            ValueError: If *parameters* is not an object schema, or its
                ``properties`` is not a mapping.
        """
        route = self._routes.get(tool_name)
        if route is None:
            raise KeyError(f"Route not found: {tool_name}")
        if parameters is None:
            if self._parameter_overrides.pop(tool_name, None) is None:
                route.parameter_schema_override = None
                return
        else:
            normalized = _validate_parameter_override(parameters)
            self._reconcile_override_with_model(tool_name, route, normalized)
            if self._parameter_overrides.get(tool_name) == normalized:
                route.parameter_schema_override = normalized
                return
            self._parameter_overrides[tool_name] = normalized
        route.parameter_schema_override = self._parameter_overrides.get(tool_name)
        self._notify_listeners(tool_name, ChangeEventType.METADATA_UPDATE.value)

    def override_execution_hints(
        self, tool_name: str, hints: dict[str, bool] | None
    ) -> None:
        """Override the execution hints published for one route.

        The registry ``Tool`` is untouched.  Passing ``{}`` suppresses every
        annotation and extension for the route -- the transport equivalent of
        "no claim" -- while passing ``None`` clears the override and restores
        the resolved hints.

        Args:
            tool_name: Registered tool name.
            hints: Replacement mapping, or ``None`` to clear.

        Raises:
            KeyError: If the route does not exist.
            TypeError: If *hints* is not a mapping.
            ValueError: If *hints* contains an unknown name or a non-boolean
                value.
        """
        route = self._routes.get(tool_name)
        if route is None:
            raise KeyError(f"Route not found: {tool_name}")
        if hints is None:
            if self._execution_hint_overrides.pop(tool_name, None) is None:
                route.execution_hints_override = None
                return
        else:
            normalized = _validate_execution_hints(hints)
            if self._execution_hint_overrides.get(tool_name) == normalized:
                route.execution_hints_override = normalized
                return
            self._execution_hint_overrides[tool_name] = normalized
        route.execution_hints_override = self._execution_hint_overrides.get(tool_name)
        self._notify_listeners(tool_name, ChangeEventType.METADATA_UPDATE.value)

    def set_emit_derived_execution_hints(self, enabled: bool) -> None:
        """Enable or disable publication of tag-derived execution hints.

        The default is ``False``: only explicitly declared hints reach the
        wire, because a ``READ_ONLY`` tag may have been chosen for local
        permission policy rather than as a client-facing safety claim.
        Enabling this restores the pre-0.8 behavior for deployments that
        deliberately want tag-derived annotations.

        Args:
            enabled: ``True`` to publish resolved hints, ``False`` to
                publish explicit declarations only.
        """
        if self.emit_derived_execution_hints == enabled:
            return
        self.emit_derived_execution_hints = enabled
        # Entries read the flag live through their table reference, so only
        # the notification is needed; a full rebuild would be wasted work.
        self._notify_listeners("*", ChangeEventType.METADATA_UPDATE.value)

    # ============== Observer Pattern ==============

    def add_listener(self, callback: Callable[[str, str], None]) -> None:
        """Add a listener for route changes.

        The callback will be invoked with (tool_name, event) whenever
        a route changes. Events include: "enable", "disable", "refresh",
        "refresh_all".

        Args:
            callback: Function to call on changes.
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str, str], None]) -> None:
        """Remove a listener.

        Args:
            callback: The callback to remove.

        Raises:
            ValueError: If the callback is not found.
        """
        self._listeners.remove(callback)

    def _notify_listeners(self, tool_name: str, event: str) -> None:
        """Notify all listeners of a change.

        Args:
            tool_name: The tool that changed, or "*" for all.
            event: The type of event.
        """
        self._version += 1
        for listener in self._listeners:
            listener(tool_name, event)
