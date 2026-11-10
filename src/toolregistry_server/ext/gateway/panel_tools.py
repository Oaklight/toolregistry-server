"""Tool inspection and the guarded call tester (#251).

The tester is the operator's own invocation of one tool.  It runs the same
steps a client call runs — the shared argument validator, the process
permission policy, the handler itself, and the attributed execution-log
record — rather than calling the tool's callable directly, so a call the
panel makes is indistinguishable from a client's in the ways that matter: it
is validated against the served schema, it is refused by a policy that denies
it, and it appears in the log under the operator principal.

Nothing here is reachable without the panel's operator session: the host
answers 401 for every route before an extension handler runs.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...adapters.openapi.adapter import RequestValidationError, _validate_arguments
from ...adapters.openapi.serialisation import encode_tool_result
from ...isolation import policy_refusal_sync, record_tool_call
from ...source_manager import _redact_url_userinfo

#: Tags that make a call destructive enough to name in the confirmation.
DANGEROUS_TAGS = frozenset({"destructive", "privileged"})


class PanelRefusal(Exception):
    """A tool operation the panel refuses, with the wire shape to send.

    Args:
        status: HTTP status code.
        error: Short error name for the panel's error envelope.
        message: Operator-facing explanation.
        extra: Additional JSON fields merged into the response body.
    """

    def __init__(
        self,
        status: int,
        error: str,
        message: str,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message
        self.extra = dict(extra or {})


@dataclass(frozen=True)
class ToolView:
    """The route and tool a panel request addresses.

    Attributes:
        name: Full tool name.
        tool: The registry ``Tool``.
        route: The route table entry, or ``None`` without a route table.
        enabled: Whether the tool may be called.
        disable_reason: Why it is disabled, when it is.
    """

    name: str
    tool: Any
    route: Any
    enabled: bool
    disable_reason: str | None


def _route_for(registry: Any, route_table: Any, name: str) -> Any:
    """Return the route for *name*, or ``None`` when no table is attached."""
    if route_table is None:
        return None
    return route_table.get_route(name)


def resolve_tool(registry: Any, route_table: Any, name: str) -> ToolView:
    """Look up one tool and its route.

    Args:
        registry: The live registry.
        route_table: The gateway's route table, or ``None``.
        name: The full tool name.

    Returns:
        The tool view.

    Raises:
        KeyError: If no such tool is registered.
    """
    tool = registry.get_tool(name)
    if tool is None:
        raise KeyError(f"tool {name!r} not found")
    route = _route_for(registry, route_table, name)
    enabled = (
        bool(route.enabled) if route is not None else bool(registry.is_enabled(name))
    )
    reason = (
        route.disable_reason if route is not None else registry.get_disable_reason(name)
    )
    return ToolView(
        name=name, tool=tool, route=route, enabled=enabled, disable_reason=reason
    )


def dangerous_tags(tool: Any) -> list[str]:
    """Return the tags that make calling *tool* need a confirmation."""
    tags = getattr(getattr(tool, "metadata", None), "tags", ()) or ()
    return sorted(
        text for text in (_tag_text(tag) for tag in tags) if text in DANGEROUS_TAGS
    )


def _tag_text(tag: Any) -> str:
    """Return a tag's wire text.

    ``ToolTag`` is a ``(str, Enum)`` member, and ``str()`` of such a member is
    ``"ToolTag.DESTRUCTIVE"``, not ``"destructive"``.  The enum's value is
    the stable spelling both the config file and the schema use.
    """
    value = getattr(tag, "value", None)
    return value if isinstance(value, str) else str(tag)


def _tag_texts(metadata: Any) -> list[str]:
    """Return the metadata's predefined tags as their wire text."""
    return sorted(_tag_text(tag) for tag in getattr(metadata, "tags", ()) or ())


def _served_schema(view: ToolView) -> dict[str, Any]:
    """Return the parameter schema a client would be given."""
    if view.route is not None:
        return dict(view.route.served_parameters_schema)
    return dict(getattr(view.tool, "parameters", None) or {})


def _execution_hints(view: ToolView) -> dict[str, bool]:
    """Return the execution hints a client would be given."""
    if view.route is not None:
        return dict(view.route.served_execution_hints)
    metadata = getattr(view.tool, "metadata", None)
    return dict(getattr(metadata, "execution_hints", None) or {})


def tool_detail(registry: Any, route_table: Any, name: str) -> dict[str, Any]:
    """Return everything the detail view shows about one tool.

    Args:
        registry: The live registry.
        route_table: The gateway's route table, or ``None``.
        name: The full tool name.

    Returns:
        Description, input and output schema, tags, namespace, enabled state,
        source, execution hints and the dangerous tags to call out.

    Raises:
        KeyError: If no such tool is registered.
    """
    view = resolve_tool(registry, route_table, name)
    metadata = getattr(view.tool, "metadata", None)
    output_schema = getattr(view.route, "output_schema", None) if view.route else None
    return {
        "name": view.name,
        "namespace": getattr(view.tool, "namespace", None) or "default",
        "method_name": getattr(view.tool, "method_name", None) or view.name,
        "description": getattr(view.tool, "description", "") or "",
        "enabled": view.enabled,
        "disable_reason": view.disable_reason,
        "input_schema": _served_schema(view),
        "output_schema": output_schema,
        "tags": _tag_texts(metadata),
        "custom_tags": sorted(getattr(metadata, "custom_tags", ()) or ()),
        "source": getattr(metadata, "source", None),
        "source_detail": _redact_url_userinfo(
            getattr(metadata, "source_detail", "") or ""
        ),
        "execution_hints": _execution_hints(view),
        "dangerous_tags": dangerous_tags(view.tool),
        "defer": getattr(metadata, "defer", None),
        "search_hint": getattr(metadata, "search_hint", ""),
        "title": getattr(metadata, "title", None),
    }


def list_tools(registry: Any, route_table: Any) -> list[dict[str, Any]]:
    """Return a compact record per registered tool, disabled ones included.

    Args:
        registry: The live registry.
        route_table: The gateway's route table, or ``None``.

    Returns:
        ``name``, ``namespace``, ``description``, ``enabled``, ``source``,
        ``tags`` and ``dangerous_tags`` for each tool, sorted by name.
    """
    routes = route_table.list_routes(enabled_only=False) if route_table else None
    records: list[dict[str, Any]] = []
    if routes is None:
        for name in registry.list_tools(include_disabled=True):
            tool = registry.get_tool(name)
            if tool is None:
                continue
            metadata = tool.metadata
            records.append(
                _summary(
                    name,
                    tool,
                    registry.is_enabled(name),
                    getattr(metadata, "source", None),
                )
            )
        return sorted(records, key=lambda record: record["name"])
    for route in routes:
        records.append(
            _summary(
                route.tool_name,
                route.tool,
                bool(route.enabled),
                getattr(route.tool.metadata, "source", None),
            )
        )
    return sorted(records, key=lambda record: record["name"])


def _summary(name: str, tool: Any, enabled: bool, source: str | None) -> dict[str, Any]:
    """Build one listing record."""
    metadata = getattr(tool, "metadata", None)
    description = getattr(tool, "description", "") or ""
    return {
        "name": name,
        "namespace": getattr(tool, "namespace", None) or "default",
        "description": description[:200],
        "enabled": enabled,
        "source": source,
        "tags": _tag_texts(metadata),
        "dangerous_tags": dangerous_tags(tool),
    }


def _jsonable(result: Any) -> Any:
    """Project a handler result the way a client receives it."""
    try:
        return json.loads(encode_tool_result(result))
    except (TypeError, ValueError):
        # A value with no JSON projection: show its repr rather than failing
        # the whole response.  json.dumps here cannot raise for a str.
        return str(result)


def _arguments(payload: Mapping[str, Any], view: ToolView) -> dict[str, Any]:
    """Validate the tester's arguments against the served schema."""
    raw = payload.get("arguments", {})
    if not isinstance(raw, Mapping):
        raise PanelRefusal(400, "Bad Request", "'arguments' must be a JSON object")
    try:
        return _validate_arguments(view.route, dict(raw))
    except RequestValidationError as exc:
        raise PanelRefusal(
            exc.status_code,
            "Invalid Arguments",
            "The arguments do not match the tool's input schema",
            {"detail": exc.detail},
        ) from exc


def _invoke(view: ToolView, arguments: Mapping[str, Any]) -> Any:
    """Run the route handler, awaiting a coroutine result.

    A sync handler returning a coroutine is awaited too, matching the
    OpenAPI adapter's execution step.
    """
    result = view.route.handler(**arguments)
    if asyncio.iscoroutine(result):
        result = _run_coroutine(result)
    return result


def _run_coroutine(coroutine: Any) -> Any:
    """Run *coroutine* on core's shared event loop."""
    from toolregistry._async_runtime import AsyncRuntime

    return AsyncRuntime.run_sync(coroutine)


def call_tool(
    registry: Any,
    route_table: Any,
    name: str,
    payload: Mapping[str, Any],
    *,
    principal: Any = None,
) -> dict[str, Any]:
    """Call one tool as the operator, through the client execution path.

    Args:
        registry: The live registry.
        route_table: The gateway's route table, or ``None``.
        name: The full tool name.
        payload: The request body; ``confirm`` must be true and
            ``arguments`` holds the call arguments.
        principal: The operator principal to attribute, or ``None`` for the
            default ``Principal.operator()``.

    Returns:
        ``ok``, the encoded ``result`` or the verbatim ``error`` with its
        ``error_type``, ``duration_ms`` and the ``dangerous_tags`` the caller
        confirmed.

    Raises:
        KeyError: If no such tool is registered.
        PanelRefusal: If the tool is disabled, the call is unconfirmed, the
            arguments are invalid, or the process policy denies it.
    """
    view = resolve_tool(registry, route_table, name)
    tags = dangerous_tags(view.tool)
    if not view.enabled:
        raise PanelRefusal(
            409,
            "Tool Disabled",
            f"Tool {name!r} is disabled; enable it before calling it from the panel",
            {"disable_reason": view.disable_reason},
        )
    if not payload.get("confirm"):
        raise PanelRefusal(
            409,
            "Confirmation Required",
            f"Confirm the call to {name!r}",
            {"dangerous_tags": tags},
        )
    if view.route is None:
        raise PanelRefusal(
            503,
            "Service Unavailable",
            "the gateway extension has no route table; start the server in "
            "gateway mode with this page wired",
        )
    arguments = _arguments(payload, view)
    refusal = policy_refusal_sync(registry, view.route, arguments)
    if refusal is not None:
        raise PanelRefusal(403, "Forbidden", refusal)

    if principal is None:
        from ...keystore import Principal

        principal = Principal.operator()
    return _execute(registry, view, arguments, principal, tags)


def _execute(
    registry: Any,
    view: ToolView,
    arguments: Mapping[str, Any],
    principal: Any,
    tags: list[str],
) -> dict[str, Any]:
    """Run the handler and record the call under *principal*."""
    started = time.monotonic()
    try:
        result = _invoke(view, arguments)
    except Exception as exc:  # noqa: BLE001 - reported to the operator
        duration = (time.monotonic() - started) * 1000
        record_tool_call(
            registry,
            view.name,
            arguments,
            principal=principal,
            error=exc,
            duration_ms=duration,
        )
        return {
            "ok": False,
            "error": _redact_url_userinfo(str(exc)),
            "error_type": type(exc).__qualname__,
            "duration_ms": round(duration, 3),
            "dangerous_tags": tags,
        }
    duration = (time.monotonic() - started) * 1000
    record_tool_call(
        registry,
        view.name,
        arguments,
        principal=principal,
        result=result,
        duration_ms=duration,
    )
    return {
        "ok": True,
        "result": _jsonable(result),
        "duration_ms": round(duration, 3),
        "dangerous_tags": tags,
    }


__all__ = [
    "DANGEROUS_TAGS",
    "PanelRefusal",
    "call_tool",
    "dangerous_tags",
    "list_tools",
    "resolve_tool",
    "tool_detail",
]
