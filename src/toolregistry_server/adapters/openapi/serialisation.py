"""Shared JSON encoding for both OpenAPI backends.

Both backends send tool results through core's ``ToolResultJSONEncoder``, so
non-finite floats and ``Decimal`` values reach the wire as ``null``, and
``bytes``, sets, dataclasses and Pydantic models use the same projection on
both transports.
"""

from __future__ import annotations

import json
from typing import Any

from toolregistry.utils import ToolResultJSONEncoder


def encode_tool_result(result: Any) -> str:
    """Encode *result* as compact strict JSON for both backends.

    Args:
        result: The value returned by a tool.

    Returns:
        The JSON body both OpenAPI backends send.
    """
    return json.dumps(
        result,
        cls=ToolResultJSONEncoder,
        ensure_ascii=False,
        separators=(",", ":"),
    )
