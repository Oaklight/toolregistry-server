"""Pick the tools the demo exposes.

``GATEWAY_TOOL_SOURCE`` selects the source:

* ``auto`` (default): use toolregistry-hub tools when the package is
  importable, otherwise the three local tools in :mod:`gateway_demo.tools`;
* ``hub``: require toolregistry-hub;
* ``local``: always use the local tools.

Installing hub tools here stands in for the runtime source manager (#185).
"""

from __future__ import annotations

import logging
import os

from toolregistry import ToolRegistry

from .tools import add, echo, server_time

logger = logging.getLogger("gateway_demo")

_MODES = ("auto", "hub", "local")


def _local_registry() -> ToolRegistry:
    registry = ToolRegistry(name="gateway-demo")
    for tool in (echo, add, server_time):
        registry.register(tool)
    return registry


def build_registry() -> tuple[ToolRegistry, str]:
    """Return ``(registry, source)`` with ``source`` being ``"hub"`` or ``"local"``.

    Raises:
        ValueError: If ``GATEWAY_TOOL_SOURCE`` is not one of the modes.
        ImportError: If the mode is ``hub`` and toolregistry-hub is missing.
    """
    mode = os.environ.get("GATEWAY_TOOL_SOURCE", "auto").strip().lower() or "auto"
    if mode not in _MODES:
        raise ValueError(
            f"GATEWAY_TOOL_SOURCE must be one of {', '.join(_MODES)}, got {mode!r}"
        )
    if mode != "local":
        try:
            from toolregistry_hub.server.registry import build_registry as build_hub

            return build_hub(enable_discovery=False, enable_think=False), "hub"
        except Exception as exc:  # noqa: BLE001 - fall back to the local tools
            if mode == "hub":
                raise
            logger.warning(
                "toolregistry-hub is not usable (%s); using the local demo tools", exc
            )
    return _local_registry(), "local"
