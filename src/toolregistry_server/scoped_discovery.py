"""Scope-aware behaviour for the discovery proxies (#195).

``discover_tools`` and ``call_deferred`` speak for other tools: the first
returns their schemas, the second invokes one by name.  The route-level scope
check only sees the proxy, so both need an explicit decision about the tool
they act on.  This module makes that decision in one place, and the MCP and
OpenAPI surfaces call it with their own error envelope.

The resolver stays the only authority on scope: every decision here goes
through :meth:`PrincipalResolver.allows` / ``allows_route``.  This module only
supplies the target and filters the result set.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The discovery tool.  Matches the name core registers
#: (``toolregistry.llm.discovery.TOOL_DISCOVERY_NAME``).
DISCOVERY_TOOL_NAME = "discover_tools"

#: The deferred-call proxy.  Matches core's ``TOOL_CALL_DEFERRED_NAME``.
CALL_DEFERRED_TOOL_NAME = "call_deferred"

#: Argument that names the tool ``call_deferred`` will invoke.
TARGET_ARGUMENT = "_target_tool"


def _normalise_tool_name(name: str) -> str:
    """Return the lookup form core uses for a requested tool name.

    Core's ``call_deferred`` resolves ``/`` and ``-`` to ``_`` and compares
    case-insensitively, so the scope check must resolve the same spelling;
    otherwise a rewritten name would pass the check as unknown and then be
    invoked by core.  Kept byte-identical to
    ``toolregistry.llm.discovery._normalize_tool_name``.
    """
    return name.replace("/", "_").replace("-", "_").lower()


def find_route(route_table: Any, tool_name: str) -> Any | None:
    """Return the route *tool_name* names, or ``None``.

    The exact name wins.  Otherwise the first route whose normalised name
    matches is returned, which mirrors the registry-order first match core
    applies before invoking a deferred target.

    Args:
        route_table: The route table being served.
        tool_name: A tool name as supplied by the caller.

    Returns:
        The matching route entry, or ``None`` when nothing matches.
    """
    route = route_table.get_route(tool_name)
    if route is not None:
        return route
    wanted = _normalise_tool_name(tool_name)
    for candidate in route_table.list_routes(enabled_only=False):
        if _normalise_tool_name(candidate.tool_name) == wanted:
            return candidate
    return None


def refusing_target(
    tool_name: str,
    arguments: Any,
    route_table: Any,
    principal_resolver: Any | None,
    principal: Any | None,
) -> str | None:
    """Return the target name when a ``call_deferred`` must be refused.

    Only ``call_deferred`` needs this: its arguments name another tool, and
    core invokes that tool with ``ToolRegistry._invoke_raw``, below the route
    the scope check saw.  A target that does not resolve is not refused here,
    so the existing not-found error shape is preserved.

    Args:
        tool_name: The tool the client called.
        arguments: The validated call arguments.
        route_table: The route table being served.
        principal_resolver: The configured resolver, or ``None``.
        principal: The caller's principal, or ``None``.

    Returns:
        The target's name when the principal may not call it, otherwise
        ``None``.
    """
    if principal_resolver is None or tool_name != CALL_DEFERRED_TOOL_NAME:
        return None
    if not isinstance(arguments, Mapping):
        return None
    target = arguments.get(TARGET_ARGUMENT)
    if not isinstance(target, str) or not target:
        return None
    route = find_route(route_table, target)
    if route is None:
        return None
    if principal_resolver.allows_route(principal, route):
        return None
    # The canonical spelling, so the refusal reads the same as a direct call
    # to that tool after a client rewrote the name.
    return getattr(route, "tool_name", None) or target


def filter_discovery_result(
    result: Any,
    principal_resolver: Any | None,
    principal: Any | None,
) -> Any:
    """Drop discovered tools the principal may not call.

    Filtering happens on the resolved result set, not on the query: each hit
    carries the tool's name and namespace, which is exactly what the resolver
    judges.  A result that is not a list of mappings is returned unchanged.

    Args:
        result: The value ``discover_tools`` returned.
        principal_resolver: The configured resolver, or ``None``.
        principal: The caller's principal, or ``None``.

    Returns:
        The result, with out-of-scope hits removed.
    """
    if principal_resolver is None or not isinstance(result, list):
        return result
    kept: list[Any] = []
    for hit in result:
        if isinstance(hit, Mapping):
            name = hit.get("name")
            namespace = hit.get("namespace")
            if isinstance(name, str) and not principal_resolver.allows(
                principal,
                namespace if isinstance(namespace, str) else None,
                name,
            ):
                continue
        kept.append(hit)
    return kept


__all__ = [
    "CALL_DEFERRED_TOOL_NAME",
    "DISCOVERY_TOOL_NAME",
    "TARGET_ARGUMENT",
    "filter_discovery_result",
    "find_route",
    "refusing_target",
]
