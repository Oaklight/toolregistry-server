"""Frontend records and per-request routing state for the gateway.

The gateway serves three frontends from one process: the OpenAPI adapter at
``/openapi``, the MCP streamable-HTTP transport at ``/mcp`` and the MCP SSE
transport at ``/sse``.  Each frontend has a configurable path and an enable
flag.  The flag is read on every request, so disabling a frontend at runtime
takes effect without a restart and without rebuilding the MCP session
manager.

A disabled frontend never reaches its ASGI application, so a request to a
disabled path cannot touch the registry or run a tool.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

#: Frontends the gateway can serve, in their stable display order.
FRONTEND_NAMES: tuple[str, ...] = ("openapi", "mcp", "sse")

#: Default path for each frontend.
DEFAULT_PATHS: dict[str, str] = {
    "openapi": "/openapi",
    "mcp": "/mcp",
    "sse": "/sse",
}

#: Protocol served by each frontend.
TRANSPORTS: dict[str, str] = {
    "openapi": "http",
    "mcp": "streamable-http",
    "sse": "sse",
}


def normalize_path(path: str) -> str:
    """Validate and normalise a frontend mount path.

    Args:
        path: The path to normalise.

    Returns:
        The path with a leading slash and no trailing slash.

    Raises:
        ValueError: If *path* is not a non-root absolute path without
            whitespace, query string or fragment.
    """
    if not isinstance(path, str):
        raise ValueError(f"frontend path must be a string, got {type(path).__name__}")
    if path != path.strip():
        raise ValueError(
            f"frontend path must not have surrounding whitespace: {path!r}"
        )
    if not path.startswith("/"):
        raise ValueError(f"frontend path must start with '/': {path!r}")
    if path == "/":
        raise ValueError("frontend path must not be the root path '/'")
    if "?" in path or "#" in path:
        raise ValueError(f"frontend path must not contain '?' or '#': {path!r}")
    if any(character.isspace() for character in path):
        raise ValueError(f"frontend path must not contain whitespace: {path!r}")
    normalized = path.rstrip("/")
    if "//" in normalized:
        raise ValueError(f"frontend path must not contain '//': {path!r}")
    if any(segment in {".", ".."} for segment in normalized.split("/")):
        raise ValueError(
            f"frontend path must not contain '.' or '..' segments: {path!r}"
        )
    return normalized


@dataclass(frozen=True)
class Frontend:
    """One gateway frontend.

    Attributes:
        name: Stable frontend name, one of :data:`FRONTEND_NAMES`.
        path: Mount path, without a trailing slash.
        enabled: Whether requests to *path* are served.  Checked per request.
        transport: Protocol served by the frontend.
    """

    name: str
    path: str
    enabled: bool
    transport: str

    def as_dict(self) -> dict[str, Any]:
        """Return the public record exposed by :meth:`list_frontends`.

        Returns:
            A dict with ``name``, ``path``, ``enabled`` and ``transport``.
        """
        return {
            "name": self.name,
            "path": self.path,
            "enabled": self.enabled,
            "transport": self.transport,
        }


def default_frontends() -> list[Frontend]:
    """Return the default frontend records.

    All three frontends are enabled on their default paths.

    Returns:
        One enabled :class:`Frontend` per entry in :data:`FRONTEND_NAMES`.
    """
    return [
        Frontend(name, DEFAULT_PATHS[name], True, TRANSPORTS[name])
        for name in FRONTEND_NAMES
    ]


class FrontendRegistry:
    """Thread-safe collection of gateway frontends.

    The registry owns the mutable path and enable flag of each frontend.
    The ASGI dispatcher reads it on every request, so an update applies to
    the next request without a restart.

    Args:
        frontends: Frontend records to serve.  Defaults to
            :func:`default_frontends`.
        locked_names: Names whose path may not change.  The gateway uses this
            for a frontend served by a non-ASGI backend.

    Raises:
        ValueError: If a frontend name or path is unknown, duplicated, or
            invalid.
    """

    def __init__(
        self,
        frontends: list[Frontend] | None = None,
        *,
        locked_names: frozenset[str] = frozenset(),
    ) -> None:
        records = frontends if frontends is not None else default_frontends()
        self._lock = threading.RLock()
        self._locked_names = locked_names
        self._frontends: dict[str, Frontend] = {}
        for record in records:
            if record.name not in FRONTEND_NAMES:
                raise ValueError(
                    f"unknown frontend {record.name!r}; "
                    f"expected one of {', '.join(FRONTEND_NAMES)}"
                )
            if record.name in self._frontends:
                raise ValueError(f"duplicate frontend {record.name!r}")
            self._frontends[record.name] = Frontend(
                name=record.name,
                path=normalize_path(record.path),
                enabled=bool(record.enabled),
                transport=record.transport,
            )
        self._check_unique_paths()

    def _check_unique_paths(self) -> None:
        """Reject two frontends that share a path.

        Raises:
            ValueError: If two frontends have the same path.  Nested paths are
                allowed because the dispatcher uses the longest prefix.
        """
        seen: dict[str, str] = {}
        for record in self._frontends.values():
            other = seen.get(record.path)
            if other is not None:
                raise ValueError(
                    f"frontends {other!r} and {record.name!r} share path "
                    f"{record.path!r}"
                )
            seen[record.path] = record.name

    def get(self, name: str) -> Frontend:
        """Return the record for *name*.

        Args:
            name: One of :data:`FRONTEND_NAMES`.

        Returns:
            The current, immutable record.

        Raises:
            ValueError: If *name* is not a known frontend.
        """
        with self._lock:
            return self._get_locked(name)

    def _get_locked(self, name: str) -> Frontend:
        try:
            return self._frontends[name]
        except KeyError:
            raise ValueError(
                f"unknown frontend {name!r}; "
                f"expected one of {', '.join(FRONTEND_NAMES)}"
            ) from None

    def snapshot(self) -> list[Frontend]:
        """Return every frontend record in stable order.

        Returns:
            A list ordered like :data:`FRONTEND_NAMES`.
        """
        with self._lock:
            return [self._frontends[name] for name in FRONTEND_NAMES]

    def enabled_snapshot(self) -> list[Frontend]:
        """Return the enabled frontends in stable order.

        Returns:
            A list of enabled records.
        """
        return [record for record in self.snapshot() if record.enabled]

    def update(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        path: str | None = None,
    ) -> Frontend:
        """Change one frontend's enable flag, path, or both.

        Args:
            name: One of :data:`FRONTEND_NAMES`.
            enabled: New enable flag, or ``None`` to keep the current value.
            path: New mount path, or ``None`` to keep the current value.

        Returns:
            The updated, immutable record.

        Raises:
            ValueError: If *name* is unknown, *enabled* is not a boolean,
                *path* is invalid or already used, or the path is locked.
        """
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("'enabled' must be a boolean")
        with self._lock:
            current = self._get_locked(name)
            new_path = current.path if path is None else normalize_path(path)
            new_enabled = current.enabled if enabled is None else enabled

            if new_path != current.path and name in self._locked_names:
                raise ValueError(
                    f"frontend {name!r} is served by a non-ASGI backend and "
                    "its path cannot change at runtime"
                )
            for other in self._frontends.values():
                if other.name != name and other.path == new_path:
                    raise ValueError(
                        f"frontends {other.name!r} and {name!r} share path {new_path!r}"
                    )

            updated = Frontend(
                name=current.name,
                path=new_path,
                enabled=new_enabled,
                transport=current.transport,
            )
            self._frontends[name] = updated
            return updated

    def match(self, path: str) -> Frontend | None:
        """Return the frontend whose mount path matches *path*.

        A frontend matches its exact path and any path below it.  When paths
        are nested, the longest match wins.

        Args:
            path: Request path, without a query string.

        Returns:
            The matching record, or ``None`` when no frontend owns the path.
        """
        with self._lock:
            matches = [
                record
                for record in self._frontends.values()
                if path == record.path or path.startswith(record.path + "/")
            ]
        if not matches:
            return None
        return max(matches, key=lambda record: len(record.path))


__all__ = [
    "DEFAULT_PATHS",
    "FRONTEND_NAMES",
    "TRANSPORTS",
    "Frontend",
    "FrontendRegistry",
    "default_frontends",
    "normalize_path",
]
