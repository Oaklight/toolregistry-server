"""Scope-aware behaviour for the discovery proxies (#195).

``discover_tools`` and ``call_deferred`` speak for other tools: the first
returns their schemas, the second invokes one by name.  The route-level scope
check only sees the proxy, so both need an explicit decision about the tool
they act on.  This module makes that decision in one place, and the MCP and
OpenAPI surfaces call it with their own error envelope.

The resolver stays the only authority on scope: every decision here goes
through :meth:`PrincipalResolver.allows`.  This module only supplies the
target and filters the result set.

The target is looked up in the **registry**, not the route table: core's
``call_deferred`` resolves and invokes against the registry, and the route
table is a serving view that deliberately lags while
:meth:`RouteTable.batch_changes` defers a rebuild.  Judging by the route table
would miss a tool registered inside that window and let core invoke it.
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


def find_tool(registry: Any, tool_name: str) -> Any | None:
    """Return the registered tool core would invoke for *tool_name*.

    Mirrors ``ToolDiscoveryTool.call_deferred`` exactly: the exact name wins,
    then the first registry-order name whose normalised form matches.  The
    registry is the authority because that is what ``_invoke_raw`` uses; the
    route table can lag behind it while a batch change defers its rebuild.

    Args:
        registry: The live ``ToolRegistry`` behind the served routes.
        tool_name: A tool name as supplied by the caller.

    Returns:
        The registered ``Tool``, or ``None`` when nothing matches.
    """
    tool = registry.get_tool(tool_name)
    if tool is not None:
        return tool
    wanted = _normalise_tool_name(tool_name)
    for name in registry.list_tools(include_disabled=True):
        if _normalise_tool_name(name) == wanted:
            return registry.get_tool(name)
    return None


def refusing_target(
    tool_name: str,
    arguments: Any,
    registry: Any,
    principal_resolver: Any | None,
    principal: Any | None,
) -> str | None:
    """Return the target name when a ``call_deferred`` must be refused.

    Only ``call_deferred`` needs this: its arguments name another tool, and
    core invokes that tool with ``ToolRegistry._invoke_raw``, below the route
    the scope check saw.  The target is resolved against the registry, the
    same object core resolves and invokes against, so a tool registered while
    ``batch_changes`` defers the route rebuild cannot be missed.  A target
    that does not resolve is not refused here, so the existing not-found
    error shape is preserved.

    Args:
        tool_name: The tool the client called.
        arguments: The validated call arguments.
        registry: The live registry behind the served routes.
        principal_resolver: The configured resolver, or ``None``.
        principal: The caller's principal, or ``None``.

    Returns:
        The target's canonical name when the principal may not call it,
        otherwise ``None``.
    """
    if principal_resolver is None or tool_name != CALL_DEFERRED_TOOL_NAME:
        return None
    if not isinstance(arguments, Mapping):
        return None
    target = arguments.get(TARGET_ARGUMENT)
    if not isinstance(target, str) or not target:
        return None
    tool = find_tool(registry, target)
    if tool is None:
        return None
    if principal_resolver.allows(principal, tool.namespace, tool.name):
        return None
    # The registry's spelling, so the refusal reads the same as a direct call
    # to that tool after a client rewrote the name.
    return tool.name


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
    "find_tool",
    "refusing_target",
]
