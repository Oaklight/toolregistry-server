"""Unix-domain-socket helpers for the gateway listener.

The gateway binds TCP by default on every platform.  A Unix socket is an
opt-in on POSIX (``--socket PATH``): it keeps the listener off the loopback
port, at the cost of clients needing a UDS-capable HTTP transport (the stdio
shim has one; a direct streamable-HTTP client usually does not).
"""

from __future__ import annotations

import os
import socket
import stat
import sys
from pathlib import Path

#: Longest usable ``sun_path`` in bytes.  Linux allows 108 bytes including
#: the terminator, macOS 104; the platform constant is not exposed, so the
#: conservative value is used.
_MAX_UNIX_PATH = 103 if sys.platform == "darwin" else 107


def prepare_unix_socket(path: str | os.PathLike[str]) -> None:
    """Validate *path* and remove a stale socket file before binding.

    Args:
        path: Socket path the gateway is about to bind.

    Raises:
        ValueError: On a platform without Unix sockets, when the path is
            too long, when the existing entry is not a socket, or when
            another process is already accepting on it.
    """
    if sys.platform == "win32":
        raise ValueError(
            "Unix domain sockets are not supported by the gateway on Windows; "
            "use --host/--port"
        )
    target = Path(path)
    encoded = os.fsencode(str(target))
    if len(encoded) > _MAX_UNIX_PATH:
        raise ValueError(
            f"Unix socket path {str(target)!r} is {len(encoded)} bytes; the "
            f"limit on this platform is {_MAX_UNIX_PATH}"
        )
    parent = target.parent
    if not parent.exists():
        parent.mkdir(mode=0o700, parents=True)
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise ValueError(f"{str(target)!r} exists and is not a socket")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.2)
    try:
        try:
            probe.connect(str(target))
        except (ConnectionRefusedError, FileNotFoundError):
            # Nobody is listening: the file is left over from a crashed
            # server, and bind would fail with EADDRINUSE.
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise ValueError(
                f"cannot inspect the existing socket {str(target)!r}: {exc}"
            ) from exc
        else:
            raise ValueError(f"another server is already listening on {str(target)!r}")
    finally:
        probe.close()
