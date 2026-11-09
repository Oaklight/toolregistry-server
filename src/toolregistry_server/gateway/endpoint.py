"""Discover a resident local hub from another process.

A local hub (``toolregistry-server local-hub``) writes one JSON endpoint file
per ``--name``.  The file is the contract between the hub and its clients, in
particular the stdio shim:

* it is replaced atomically, so a reader never sees a partial document;
* it is mode ``0600`` inside a ``0700`` directory created by the hub;
* it carries the writer's pid, so a file left by a crashed hub is
  recognisable without a lock;
* it carries a protocol major, so a client built for another hub generation
  refuses it instead of guessing at a changed shape.

State directory
---------------
``$TOOLREGISTRY_HOME/hub`` when ``TOOLREGISTRY_HOME`` is set; otherwise
``$XDG_RUNTIME_DIR/toolregistry/hub`` on POSIX when available; otherwise
``~/.local/state/toolregistry/hub``; on Windows ``%LOCALAPPDATA%\\toolregistry\\hub``.
``TOOLREGISTRY_HOME`` names the state root, not the ``hub`` directory.

The file is not a lock.  A writer that needs exclusive ownership also takes
:func:`lock_path` (the local-hub command does); a reader only needs
:func:`read_live_endpoint` and :meth:`HubEndpoint.check_protocol`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

#: Major version of the endpoint document and the hub protocol it describes.
#: A client refuses a hub whose major differs (see
#: :meth:`HubEndpoint.check_protocol`); additive changes keep the major.
HUB_PROTOCOL_MAJOR = 1

#: Name used when the caller does not select one.
DEFAULT_NAME = "default"

#: Accepted ``--name`` values.  A name is a file stem, never a path.
_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

#: Transport values the endpoint document may declare.
TRANSPORTS = ("tcp", "unix")


class EndpointError(RuntimeError):
    """A hub endpoint file is unreadable, malformed or incompatible."""


@dataclass(frozen=True)
class HubEndpoint:
    """One resident hub, as published in its endpoint file.

    Attributes:
        name: The hub name selected with ``--name``.
        pid: Process id of the hub that wrote the file.
        version: ``toolregistry-server`` version of the hub.
        protocol: Major protocol version; compare with
            :data:`HUB_PROTOCOL_MAJOR`.
        transport: ``"tcp"`` or ``"unix"``.
        started_at: ISO 8601 UTC timestamp from the hub's startup.
        host: Bind host for ``transport="tcp"``.
        port: Bind port for ``transport="tcp"``.
        socket: Unix socket path for ``transport="unix"``.
        frontends: Mount paths keyed by frontend name, for diagnostics and
            for clients that want the document path.
    """

    name: str
    pid: int
    version: str
    protocol: int
    transport: Literal["tcp", "unix"]
    started_at: str
    host: str | None = None
    port: int | None = None
    socket: str | None = None
    frontends: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        name: str = DEFAULT_NAME,
        pid: int | None = None,
        version: str,
        transport: Literal["tcp", "unix"] = "tcp",
        host: str = "127.0.0.1",
        port: int | None = None,
        socket: str | None = None,
        frontends: Mapping[str, str] | None = None,
        started_at: str | None = None,
        protocol: int = HUB_PROTOCOL_MAJOR,
    ) -> HubEndpoint:
        """Build a validated endpoint record.

        Args:
            name: Hub name; validated as a file stem.
            pid: Writer pid; defaults to the current process.
            version: Server version string.
            transport: ``"tcp"`` or ``"unix"``.
            host: Bind host for TCP.
            port: Bind port for TCP.
            socket: Socket path for Unix.
            frontends: Optional frontend path mapping.
            started_at: Timestamp; defaults to now in UTC.
            protocol: Protocol major; defaults to :data:`HUB_PROTOCOL_MAJOR`.

        Returns:
            The record.

        Raises:
            EndpointError: If a field is inconsistent with the transport.
        """
        return HubEndpoint.from_dict(
            {
                "name": name,
                "pid": os.getpid() if pid is None else pid,
                "version": version,
                "protocol": protocol,
                "transport": transport,
                "started_at": started_at
                or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "host": host if transport == "tcp" else None,
                "port": port if transport == "tcp" else None,
                "socket": socket if transport == "unix" else None,
                "frontends": dict(frontends or {}),
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> HubEndpoint:
        """Validate a decoded endpoint document.

        Args:
            payload: Decoded JSON object.

        Returns:
            The record.

        Raises:
            EndpointError: If a required field is missing, has the wrong
                type, or the transport fields are inconsistent.
        """
        name = _require_str(payload, "name")
        _validate_name(name)
        version = _require_str(payload, "version")
        started_at = _require_str(payload, "started_at")
        pid = _require_int(payload, "pid")
        protocol = _require_int(payload, "protocol")
        transport = _require_str(payload, "transport")
        if transport not in TRANSPORTS:
            raise EndpointError(
                f"endpoint transport must be one of {TRANSPORTS}, got {transport!r}"
            )
        host = _optional_str(payload, "host")
        socket_path = _optional_str(payload, "socket")
        port = payload.get("port")
        if port is not None and (not isinstance(port, int) or port <= 0):
            raise EndpointError(
                f"endpoint port must be a positive integer, got {port!r}"
            )
        if transport == "tcp" and (host is None or port is None):
            raise EndpointError("a tcp endpoint requires 'host' and 'port'")
        if transport == "unix" and socket_path is None:
            raise EndpointError("a unix endpoint requires 'socket'")
        frontends = payload.get("frontends", {})
        if not isinstance(frontends, Mapping):
            raise EndpointError("endpoint 'frontends' must be an object")
        clean_frontends = {}
        for key, value in frontends.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise EndpointError("endpoint 'frontends' must map strings to strings")
            clean_frontends[key] = value
        return cls(
            name=name,
            pid=pid,
            version=version,
            protocol=protocol,
            transport=cast(Literal["tcp", "unix"], transport),
            started_at=started_at,
            host=host,
            port=port,
            socket=socket_path,
            frontends=clean_frontends,
        )

    @property
    def url(self) -> str | None:
        """Base URL for a TCP endpoint, or ``None`` for a Unix socket."""
        if self.transport != "tcp" or self.host is None or self.port is None:
            return None
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready document, with a stable key order."""
        return {
            "name": self.name,
            "protocol": self.protocol,
            "pid": self.pid,
            "version": self.version,
            "transport": self.transport,
            "host": self.host,
            "port": self.port,
            "socket": self.socket,
            "started_at": self.started_at,
            "frontends": dict(self.frontends),
        }

    def is_live(self) -> bool:
        """Whether the writer pid is still alive on this machine."""
        return pid_is_alive(self.pid)

    def check_protocol(self, expected: int = HUB_PROTOCOL_MAJOR) -> None:
        """Refuse a hub built for another endpoint-protocol major.

        Args:
            expected: The major the caller implements.

        Raises:
            EndpointError: If the hub's major differs.
        """
        if self.protocol != expected:
            raise EndpointError(
                f"hub {self.name!r} speaks endpoint protocol {self.protocol}, "
                f"this client speaks {expected}; start or install a matching "
                "hub, or select another --name"
            )


def _require_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise EndpointError(f"endpoint field {key!r} must be a non-empty string")
    return value


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EndpointError(f"endpoint field {key!r} must be a non-empty string")
    return value


def _require_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EndpointError(f"endpoint field {key!r} must be a positive integer")
    return value


def _validate_name(name: str) -> None:
    if _NAME_PATTERN.fullmatch(name) is None:
        raise EndpointError(f"hub name {name!r} must match {_NAME_PATTERN.pattern!r}")


def state_root() -> Path:
    """Return the state root that holds the ``hub`` directory."""
    override = os.environ.get("TOOLREGISTRY_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "toolregistry"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "toolregistry"
    return Path.home() / ".local" / "state" / "toolregistry"


def hub_dir() -> Path:
    """Return the directory that holds endpoint, lock and socket files."""
    return state_root() / "hub"


def endpoint_path(name: str = DEFAULT_NAME) -> Path:
    """Return the endpoint document path for *name*."""
    _validate_name(name)
    return hub_dir() / f"{name}.json"


def lock_path(name: str = DEFAULT_NAME) -> Path:
    """Return the exclusive-ownership lock path for *name*."""
    _validate_name(name)
    return hub_dir() / f"{name}.lock"


def socket_path(name: str = DEFAULT_NAME) -> Path:
    """Return the default Unix socket path for *name*."""
    _validate_name(name)
    return hub_dir() / f"{name}.sock"


def _ensure_hub_dir() -> Path:
    directory = hub_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def write_endpoint(endpoint: HubEndpoint) -> Path:
    """Atomically publish *endpoint* and return the file path.

    The document is written to a temporary file in the same directory,
    fsynced, chmodded to ``0600`` and moved into place with
    :func:`os.replace`, so a concurrent reader sees either the old document
    or the new one.

    Args:
        endpoint: The record to publish.

    Returns:
        The endpoint file path.

    Raises:
        OSError: If the directory or file cannot be written.
    """
    directory = _ensure_hub_dir()
    destination = endpoint_path(endpoint.name)
    payload = json.dumps(endpoint.to_dict(), indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{endpoint.name}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    return destination


def read_endpoint(name: str = DEFAULT_NAME) -> HubEndpoint | None:
    """Read the endpoint document for *name*, or ``None`` when absent.

    Args:
        name: Hub name.

    Returns:
        The record, or ``None`` when no file exists.

    Raises:
        EndpointError: If the document is malformed or declares another name.
    """
    endpoint = read_endpoint_file(endpoint_path(name))
    if endpoint is not None and endpoint.name != name:
        raise EndpointError(
            f"endpoint file for {name!r} declares hub {endpoint.name!r}"
        )
    return endpoint


def read_endpoint_file(path: Path | str) -> HubEndpoint | None:
    """Read one endpoint document, or ``None`` when the file is absent.

    On POSIX the file must be owned by the current user and must not be
    accessible to group or other, because a client trusts the document to
    choose where it connects.  The file is opened without following a
    symlink, so a swapped path cannot redirect the read.

    Args:
        path: Endpoint file path.

    Returns:
        The record, or ``None``.

    Raises:
        EndpointError: If the file is unreadable, unsafe, or not a valid
            document.
    """
    source = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise EndpointError(f"cannot read hub endpoint {source}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if os.name != "nt":
            if info.st_uid != os.getuid():
                raise EndpointError(f"hub endpoint {source} is owned by another user")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise EndpointError(
                    f"hub endpoint {source} is accessible to other users"
                )
        handle = os.fdopen(descriptor, "r", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
    try:
        with handle:
            raw = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise EndpointError(f"cannot read hub endpoint {source}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise EndpointError(f"hub endpoint {source} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise EndpointError(f"hub endpoint {source} must be a JSON object")
    return HubEndpoint.from_dict(payload)


def read_live_endpoint(name: str = DEFAULT_NAME) -> HubEndpoint | None:
    """Read *name*'s endpoint and return it only when its writer is alive.

    A stale file is left in place: removing it is the owner's decision, and
    a writer that is starting up may have just replaced it.
    """
    endpoint = read_endpoint(name)
    if endpoint is None or not endpoint.is_live():
        return None
    return endpoint


def remove_endpoint(name: str = DEFAULT_NAME) -> None:
    """Remove *name*'s endpoint document, if present."""
    with contextlib.suppress(FileNotFoundError):
        endpoint_path(name).unlink()


def pid_is_alive(pid: int) -> bool:
    """Return whether *pid* names a live process on this machine.

    A permission error means the process exists but belongs to another user.
    On Windows ``os.kill(pid, 0)`` is not a probe (it terminates the
    process), so the process handle is opened instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    """Open the process handle to test liveness without signalling it."""
    import ctypes

    process_query_limited_information = 0x1000
    error_access_denied = 5
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return True
    kernel32 = windll.kernel32
    # Handle as pointer-sized: the default c_int restype truncates a 64-bit
    # handle and makes a live process look dead.
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        # Another user's process is alive, matching the POSIX PermissionError
        # branch in :func:`pid_is_alive`.
        return kernel32.GetLastError() == error_access_denied
    kernel32.CloseHandle(handle)
    return True
