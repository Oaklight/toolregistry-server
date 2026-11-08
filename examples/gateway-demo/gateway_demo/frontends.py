"""Enable flags for the two gateway frontends.

The gateway process (#184) serves OpenAPI, MCP and the admin extension from
one origin and treats a stopped frontend as a route-level 404.  The demo
keeps that contract in memory; persistence arrives with the gateway process.
"""

import threading

FRONTENDS = ("openapi", "mcp")


class FrontendState:
    """Thread-safe on/off state for each frontend."""

    def __init__(self) -> None:
        self._enabled = dict.fromkeys(FRONTENDS, True)
        self._lock = threading.Lock()

    def enabled(self, name: str) -> bool:
        """Return whether *name* is enabled.

        Args:
            name: One of :data:`FRONTENDS`.

        Raises:
            KeyError: If *name* is not a known frontend.
        """
        with self._lock:
            return self._enabled[name]

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or disable *name*.

        Args:
            name: One of :data:`FRONTENDS`.
            enabled: The new state.

        Raises:
            KeyError: If *name* is not a known frontend.
        """
        with self._lock:
            self._enabled[name] = enabled

    def snapshot(self) -> dict[str, bool]:
        """Return a copy of every frontend's state."""
        with self._lock:
            return dict(self._enabled)
