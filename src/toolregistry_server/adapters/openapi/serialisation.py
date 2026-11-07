"""Shared JSON encoding for both OpenAPI backends.

Core's ``ToolResultJSONEncoder`` projects tool results to JSON-native
values.  0.19.0, the current floor, passes a non-finite float to ``json``
unchanged and renders a non-finite ``Decimal`` as the string ``"NaN"``.
Core ``master`` maps both to ``null``, but the server cannot require it yet.
Both backends must agree on the wire today, so this module detects the
installed encoder and applies the same projection before encoding when the
core predates it.  The fallback is a no-op on a core that already has the
projection and is removed once the server floor moves past the release that
ships it (#159).
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

from toolregistry.utils import ToolResultJSONEncoder


def _core_projects_non_finite() -> bool:
    """Return whether the installed core maps a NaN float to ``null``."""
    return "null" in ToolResultJSONEncoder().encode({"x": float("nan")})


#: Whether the installed core already owns the non-finite projection.  The
#: fallback encoder below is used only when this is ``False``.
CORE_PROJECTS_NON_FINITE = _core_projects_non_finite()


def _normalise_non_finite(value: Any, _stack: set[int] | None = None) -> Any:
    """Replace non-finite floats and Decimals with ``None``, recursively.

    Walks the same JSON containers ``json`` traverses itself.  Values that
    ``ToolResultJSONEncoder.default()`` projects (models, dataclasses, sets)
    are normalised again there, so a non-finite Decimal inside one of them
    is covered too.

    Args:
        value: A value returned by a tool.
        _stack: Object ids on the current path, used to detect cycles.

    Returns:
        The value with every non-finite float or Decimal replaced by ``None``.

    Raises:
        ValueError: If *value* contains a reference cycle.  The message
            matches ``json``'s own, so callers that catch ``ValueError`` to
            reject an unencodable result keep working.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Decimal) and not value.is_finite():
        return None
    if isinstance(value, dict):
        stack = _stack if _stack is not None else set()
        marker = id(value)
        if marker in stack:
            raise ValueError("Circular reference detected")
        stack.add(marker)
        try:
            return {
                key: _normalise_non_finite(item, stack) for key, item in value.items()
            }
        finally:
            stack.discard(marker)
    if isinstance(value, (list, tuple)):
        stack = _stack if _stack is not None else set()
        marker = id(value)
        if marker in stack:
            raise ValueError("Circular reference detected")
        stack.add(marker)
        try:
            return [_normalise_non_finite(item, stack) for item in value]
        finally:
            stack.discard(marker)
    return value


class _NonFiniteFallbackEncoder(ToolResultJSONEncoder):
    """Encoder that adds the non-finite projection missing from core 0.19.0."""

    def iterencode(self, o: Any, _one_shot: bool = False):
        return super().iterencode(_normalise_non_finite(o), _one_shot)

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            if not o.is_finite():
                return None
            return str(o)
        return _normalise_non_finite(super().default(o))


#: Encoder used for tool results.  On a core with the projection this is the
#: core class itself, so the fallback is not in the path.
_ENCODER = (
    ToolResultJSONEncoder if CORE_PROJECTS_NON_FINITE else _NonFiniteFallbackEncoder
)


def encode_tool_result(result: Any) -> str:
    """Encode *result* as compact strict JSON for both backends.

    Args:
        result: The value returned by a tool.

    Returns:
        The JSON body both OpenAPI backends send.
    """
    return json.dumps(
        result,
        cls=_ENCODER,
        ensure_ascii=False,
        separators=(",", ":"),
    )
