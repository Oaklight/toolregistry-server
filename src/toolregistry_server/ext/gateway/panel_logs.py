"""Principal-aware filtering over the registry execution log (#252).

The core panel exposes ``GET /api/logs`` with a tool, status and warnings
filter only.  Its handler lives in the installed ``toolregistry`` package, so
the gateway extension cannot add query parameters to it.  This module is the
server-side half of the extension's own Logs view: the same entries, with the
principal, outcome and time-range filters the issue asks for, and a matching
statistics view so a count always agrees with the filtered table.

The execution log records calls that reached a handler.  A refused call
(``denied``) or one that never got a concurrency slot (``busy``) is written to
the structured log, not to the execution log, so those two outcome values are
accepted and match nothing.  The answer says so, rather than silently
returning every entry.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Outcome spelling the panel uses to ``ExecutionStatus`` value.
_OUTCOME_STATUSES = {
    "ok": "success",
    "success": "success",
    "error": "error",
    "timeout": "timeout",
    "disabled": "disabled",
}

#: Outcomes that never appear in the execution log.
_UNRECORDED_OUTCOMES = frozenset({"busy", "denied"})

#: Query value that selects the unscoped operator principal.
_OPERATOR = "operator"

#: Most entries a single request may return.
MAX_LIMIT = 1000


class LogFilterError(ValueError):
    """A query parameter cannot be used as a filter."""


@dataclass(frozen=True)
class LogFilters:
    """One parsed set of log query filters.

    Attributes:
        limit: Largest number of entries to return.
        tool_name: Exact tool name, or ``None``.
        status: ``ExecutionStatus`` value, or ``None``.
        has_warnings: Warnings filter, or ``None``.
        key_id: Stored key id, ``"operator"`` for an unattributed-to-a-key
            principal, or ``None``.
        key_label: Case-insensitive key label, or ``None.
        since: Inclusive lower bound on the entry timestamp, or ``None``.
        until: Inclusive upper bound on the entry timestamp, or ``None``.
        match_nothing: Set when the requested outcome is never recorded.
    """

    limit: int = 100
    tool_name: str | None = None
    status: str | None = None
    has_warnings: bool | None = None
    key_id: str | None = None
    key_label: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    match_nothing: bool = False


def _param(params: Mapping[str, Sequence[str]], name: str) -> str | None:
    """Return the first value of *name*, or ``None`` when absent or empty."""
    values = params.get(name)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _parse_bool(value: str) -> bool | None:
    """Parse a tri-state boolean query value.

    Args:
        value: ``true``/``1``/``yes``, ``false``/``0``/``no``, or empty.

    Returns:
        The boolean, or ``None`` for an unrecognised value.
    """
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    return None


def _parse_time(value: str, name: str) -> datetime:
    """Parse an ISO 8601 timestamp into a naive local datetime.

    Log entries carry ``datetime.now()`` values, which are naive local time.
    An aware value from the query is converted to local time and stripped of
    its offset, so the comparison is between comparable values.

    Args:
        value: ISO 8601 text; a trailing ``Z`` is accepted.
        name: Query parameter name, used in the error message.

    Returns:
        The naive local datetime.

    Raises:
        LogFilterError: If the text is not an ISO 8601 timestamp.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LogFilterError(f"'{name}' must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _parse_limit(value: str) -> int:
    """Validate the ``limit`` query value."""
    try:
        limit = int(value)
    except ValueError as exc:
        raise LogFilterError("'limit' must be a positive integer") from exc
    if limit < 1 or limit > MAX_LIMIT:
        raise LogFilterError(f"'limit' must be between 1 and {MAX_LIMIT}")
    return limit


def _parse_outcome(value: str) -> tuple[str | None, bool]:
    """Translate an ``outcome`` value into a status and a match-nothing flag."""
    lowered = value.strip().lower()
    if lowered in _UNRECORDED_OUTCOMES:
        return None, True
    status = _OUTCOME_STATUSES.get(lowered)
    if status is None:
        accepted = sorted({*_OUTCOME_STATUSES, *_UNRECORDED_OUTCOMES})
        raise LogFilterError(f"'outcome' must be one of: {', '.join(accepted)}")
    return status, False


def parse_filters(params: Mapping[str, Sequence[str]]) -> LogFilters:
    """Parse the extension's log query parameters.

    Args:
        params: The request's ``query_params`` mapping.

    Returns:
        The parsed filters.

    Raises:
        LogFilterError: If a value is malformed.  The extension reports a
            ``ValueError`` as a 400.
    """
    limit_text = _param(params, "limit")
    limit = _parse_limit(limit_text) if limit_text is not None else 100

    status = _param(params, "status")
    match_nothing = False
    outcome = _param(params, "outcome")
    if outcome is not None:
        status, match_nothing = _parse_outcome(outcome)

    has_warnings: bool | None = None
    warnings_text = _param(params, "has_warnings")
    if warnings_text is not None:
        has_warnings = _parse_bool(warnings_text)
        if has_warnings is None:
            raise LogFilterError("'has_warnings' must be true or false")

    since_text = _param(params, "since")
    until_text = _param(params, "until")

    return LogFilters(
        limit=limit,
        tool_name=_param(params, "tool_name"),
        status=status,
        has_warnings=has_warnings,
        key_id=_param(params, "key_id"),
        key_label=_param(params, "key_label"),
        since=_parse_time(since_text, "since") if since_text else None,
        until=_parse_time(until_text, "until") if until_text else None,
        match_nothing=match_nothing,
    )


def principal_view(entry: Any) -> dict[str, Any]:
    """Return the principal recorded with *entry*.

    ``record_tool_call`` stores ``key_id`` and the key label under
    ``metadata``.  A static operator token has no key id, and its label is
    ``"operator"``; an entry with no attribution at all is reported as
    ``"anonymous"`` so it cannot be mistaken for an operator call.

    Args:
        entry: One :class:`~toolregistry.admin.ExecutionLogEntry`.

    Returns:
        ``{"key_id": ..., "label": ...}``.
    """
    metadata = getattr(entry, "metadata", None) or {}
    key_id = metadata.get("key_id")
    label = metadata.get("client_id")
    if not label:
        attributed = "key_id" in metadata
        label = _OPERATOR if attributed and key_id is None else "anonymous"
    return {"key_id": key_id, "label": str(label)}


def _matches(entry: Any, filters: LogFilters, principal: Mapping[str, Any]) -> bool:
    """Return whether *entry* passes every filter."""
    if filters.tool_name is not None and entry.tool_name != filters.tool_name:
        return False
    if filters.status is not None and getattr(entry.status, "value", entry.status) != (
        filters.status
    ):
        return False
    if (
        filters.has_warnings is not None
        and bool(entry.warnings) != filters.has_warnings
    ):
        return False
    if filters.key_id is not None:
        key_id = principal["key_id"]
        if filters.key_id.lower() == _OPERATOR:
            # The operator principal is an attributed entry with no key row;
            # an unattributed entry is anonymous, not the operator.
            if key_id is not None or principal["label"] != _OPERATOR:
                return False
        elif key_id != filters.key_id:
            return False
    if (
        filters.key_label is not None
        and principal["label"].lower() != filters.key_label.lower()
    ):
        return False
    if filters.since is not None and entry.timestamp < filters.since:
        return False
    return filters.until is None or entry.timestamp <= filters.until


def select_entries(entries: Iterable[Any], filters: LogFilters) -> list[Any]:
    """Return the entries that pass *filters*, newest first, at most ``limit``.

    Args:
        entries: The log's entries.
        filters: The parsed filters.

    Returns:
        The matching entries in reverse chronological order.
    """
    if filters.match_nothing:
        return []
    selected = [
        entry for entry in entries if _matches(entry, filters, principal_view(entry))
    ]
    selected.sort(key=lambda entry: entry.timestamp, reverse=True)
    return selected[: filters.limit]


def summarise(entries: Sequence[Any]) -> dict[str, Any]:
    """Return the statistics of an already filtered entry list.

    The counts are computed from the same entries the table shows, so the
    two always agree.

    Args:
        entries: The filtered entries.

    Returns:
        A JSON-ready mapping with the same counters the core view reports
        plus ``by_key``.
    """
    by_status: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    by_key: dict[str, int] = {}
    total_duration = 0.0
    for entry in entries:
        status = getattr(entry.status, "value", str(entry.status))
        by_status[status] = by_status.get(status, 0) + 1
        by_tool[entry.tool_name] = by_tool.get(entry.tool_name, 0) + 1
        label = principal_view(entry)["label"]
        by_key[label] = by_key.get(label, 0) + 1
        total_duration += entry.duration_ms

    oldest = min((entry.timestamp for entry in entries), default=None)
    newest = max((entry.timestamp for entry in entries), default=None)
    return {
        "total_entries": len(entries),
        "by_status": by_status,
        "by_tool": by_tool,
        "by_key": by_key,
        "avg_duration_ms": total_duration / len(entries) if entries else 0.0,
        "oldest_entry": oldest.isoformat() if oldest else None,
        "newest_entry": newest.isoformat() if newest else None,
    }


__all__ = [
    "MAX_LIMIT",
    "LogFilterError",
    "LogFilters",
    "parse_filters",
    "principal_view",
    "select_entries",
    "summarise",
]
