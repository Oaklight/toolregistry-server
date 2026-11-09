"""A resident local hub shared by several MCP sessions (#145).

``local-hub`` starts one gateway per ``--name``, publishes its address in the
endpoint file and holds an exclusive lock for its lifetime.  A stdio shim or a
direct HTTP client discovers the hub through that file.

Lifecycle
---------
``run`` serves in the foreground, ``ensure`` starts a detached hub when none
is running and prints the endpoint, ``stop`` signals the owner, and ``status``
reports the state.  The lock - not the pid in the endpoint document - is the
liveness authority: after a reboot the state directory survives while pids are
reused, so a document can name a process that has nothing to do with a hub.

Idle timeout
------------
``--idle-timeout 0`` (the default) keeps the hub resident.  A non-zero value
shuts the hub down after that many seconds without a request or an open MCP
transport session, so a connected but quiet client does not lose its hub.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .. import __version__
from .._vendor.structlog import get_logger
from .endpoint import (
    DEFAULT_NAME,
    EndpointError,
    HubEndpoint,
    ensure_hub_dir,
    hub_dir,
    lock_path,
    pid_is_alive,
    read_endpoint,
    read_live_endpoint,
    read_owner_pid,
    remove_endpoint,
    remove_owner_pid,
    write_endpoint,
    write_owner_pid,
)

logger = get_logger()

#: Exit code when no hub owns the requested name.
EXIT_NOT_RUNNING = 3
#: Seconds an ``ensure`` waits for a started hub to publish its endpoint.
DEFAULT_ENSURE_TIMEOUT = 30.0
#: Seconds a ``stop`` waits for a signalled hub to exit.
DEFAULT_STOP_TIMEOUT = 15.0
#: ``local-hub`` actions accepted on the command line.
ACTIONS = ("run", "ensure", "stop", "status")


class LocalHubUnsupported(RuntimeError):
    """The local hub cannot run on this platform yet."""


class _Termination:
    """Absorb uvicorn's SIGTERM re-raise and forward the request to the server.

    uvicorn restores the previous handler and re-raises a captured signal at
    the end of ``serve()``.  With the default handler the process would die
    before the hub retires its endpoint and pid file, so this handler is
    installed first.  A signal that arrives before the server exists is
    remembered and applied by :meth:`attach`.
    """

    def __init__(self) -> None:
        self.requested = False
        self._server: Any = None

    def __call__(self, signum: int, frame: Any) -> None:
        self.requested = True
        if self._server is not None:
            self._server.should_exit = True

    def attach(self, server: Any) -> None:
        """Remember *server* and obey a signal that arrived earlier."""
        self._server = server
        if self.requested:
            server.should_exit = True


def _require_posix(action: str) -> None:
    """Refuse the local-hub actions on Windows until they are tested there.

    Raises:
        LocalHubUnsupported: On Windows.
    """
    if os.name == "nt":
        raise LocalHubUnsupported(
            f"local-hub {action} is not supported on Windows yet: byte-range "
            "locks are mandatory there and a terminated hub cannot retire "
            "its endpoint, and no Windows CI verifies the lifecycle. Use "
            "`toolregistry-server gateway` on Windows."
        )


class HubLock:
    """Exclusive ownership of one hub name.

    The file is created ``0600``, never unlinked, and holds no data: the
    owner pid lives in ``hub/<name>.pid``, because a mandatory byte-range
    lock (Windows) would make the locked byte unreadable.

    Args:
        name: Hub name.
    """

    def __init__(self, name: str = DEFAULT_NAME) -> None:
        self.name = name
        self.path = lock_path(name)
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        """Take the lock without blocking.

        Returns:
            ``True`` when this process now owns the name.
        """
        if self._descriptor is not None:
            return True
        ensure_hub_dir()
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self._lock(descriptor)
        except OSError:
            os.close(descriptor)
            return False
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        """Release the lock, if this process holds it."""
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            os.close(descriptor)

    @staticmethod
    def _lock(descriptor: int) -> None:
        """Lock *descriptor*, raising when another process holds it."""
        if os.name == "nt":
            import msvcrt

            # msvcrt locks a byte range, so the file needs one byte to lock.
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\n")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def lock_is_held(name: str = DEFAULT_NAME) -> bool:
    """Return whether another process holds *name*'s lock.

    A probe leaves the lock file untouched: it never records a pid.

    Args:
        name: Hub name.

    Returns:
        ``True`` when a live hub owns the name.
    """
    probe = HubLock(name)
    if probe.acquire():
        probe.release()
        return False
    return True


class ActivityClock:
    """Measure idle time against a monotonic clock."""

    def __init__(self) -> None:
        self._last = time.monotonic()

    def touch(self) -> None:
        """Record activity now."""
        self._last = time.monotonic()

    def idle_for(self) -> float:
        """Return seconds since the last activity."""
        return time.monotonic() - self._last


def run_hub(
    app: Any,
    options: Mapping[str, Any],
    *,
    name: str = DEFAULT_NAME,
    idle_timeout: float = 0.0,
) -> int:
    """Run a hub in the foreground until it is stopped (blocking).

    Args:
        app: The server ``App`` that builds the gateway.
        options: Gateway construction options, as the ``gateway`` command
            builds them.
        name: Hub name.
        idle_timeout: Seconds without activity before the hub exits; ``0``
            keeps it resident.

    Returns:
        The process exit code.
    """
    _require_posix("run")
    if idle_timeout < 0:
        raise ValueError("--idle-timeout must be zero or positive")
    previous_sigterm: Any = None
    termination = _Termination()
    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.signal(signal.SIGTERM, termination)
    lock = HubLock(name)
    owned = False
    try:
        if not lock.acquire():
            existing = read_live_endpoint(name)
            if existing is not None:
                print(
                    f"hub {_describe_address(existing)} already serves {name!r}",
                    file=sys.stderr,
                )
            else:
                print(f"hub {name!r} is starting in another process", file=sys.stderr)
            return 1
        write_owner_pid(name, os.getpid())
        owned = True
        return _serve_hub(
            app,
            options,
            name=name,
            idle_timeout=idle_timeout,
            termination=termination,
        )
    except (EndpointError, OSError, ValueError) as exc:
        print(f"cannot start hub {name!r}: {exc}", file=sys.stderr)
        return 1
    finally:
        if owned:
            remove_owner_pid(name)
        lock.release()
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)


def ensure_hub(
    app: Any,
    options: Mapping[str, Any],
    *,
    name: str = DEFAULT_NAME,
    idle_timeout: float = 0.0,
    timeout: float = DEFAULT_ENSURE_TIMEOUT,
    argv: Sequence[str] | None = None,
) -> int:
    """Make sure a hub is running and print its endpoint.

    When a live hub owns *name*, its endpoint is printed and the call returns
    immediately.  Otherwise a detached ``local-hub run`` is started and the
    call waits for the endpoint to appear.

    Args:
        app: The server ``App`` (unused when a hub is already running).
        options: Gateway options; only used to validate the request.
        name: Hub name.
        idle_timeout: Idle timeout for the hub that may be started.
        timeout: Seconds to wait for the endpoint.
        argv: The invoking command line, so the child inherits every option.

    Returns:
        ``0`` when a hub is available, ``1`` when it could not be started.
    """
    _require_posix("ensure")
    running = _running_endpoint(name)
    if running is not None:
        _print_endpoint(running)
        return 0
    if lock_is_held(name):
        # Another process owns the name and is still publishing.
        return _wait_for_endpoint(name, None, timeout)

    process = _spawn_hub(options, name=name, idle_timeout=idle_timeout, argv=argv)
    return _wait_for_endpoint(name, process, timeout)


def stop_hub(name: str = DEFAULT_NAME, *, timeout: float = DEFAULT_STOP_TIMEOUT) -> int:
    """Stop the hub that owns *name*.

    Args:
        name: Hub name.
        timeout: Seconds to wait for the hub to exit after SIGTERM.

    Returns:
        ``0`` when the hub exited, ``EXIT_NOT_RUNNING`` when none was
        running, ``1`` on a failed stop.
    """
    _require_posix("stop")
    try:
        endpoint = read_endpoint(name)
    except EndpointError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not lock_is_held(name):
        if endpoint is not None:
            remove_endpoint(name)
        print(f"hub {name!r} is not running")
        return EXIT_NOT_RUNNING
    if endpoint is None:
        print(f"hub {name!r} holds its lock but published no endpoint", file=sys.stderr)
        return 1
    try:
        endpoint.check_protocol()
    except EndpointError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    owner = read_owner_pid(name)
    if owner is None:
        print(f"hub {name!r} has not recorded its pid yet", file=sys.stderr)
        return 1
    if owner != endpoint.pid:
        print(
            f"hub {name!r} is starting; its endpoint still names pid "
            f"{endpoint.pid} while the lock owner is {owner}",
            file=sys.stderr,
        )
        return 1
    if not endpoint.is_live():
        print(
            f"hub {name!r} holds its lock but pid {endpoint.pid} is gone",
            file=sys.stderr,
        )
        return 1

    os.kill(owner, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not lock_is_held(name) or not pid_is_alive(owner):
            break
        time.sleep(0.05)
    else:
        print(f"hub {name!r} did not exit within {timeout:.0f}s", file=sys.stderr)
        return 1

    # Remove the document only if it still belongs to the process we stopped.
    with contextlib.suppress(EndpointError):
        current = read_endpoint(name)
        if current is not None and current.pid == owner:
            remove_endpoint(name)
    print(f"hub {name!r} stopped")
    return 0


def status_hub(name: str = DEFAULT_NAME, *, as_json: bool = False) -> int:
    """Report whether a hub owns *name*.

    Args:
        name: Hub name.
        as_json: Print the endpoint document instead of one line.

    Returns:
        ``0`` when a hub is running, ``EXIT_NOT_RUNNING`` otherwise, ``1``
        when the endpoint document is malformed.
    """
    _require_posix("status")
    try:
        endpoint = read_endpoint(name)
    except EndpointError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    held = lock_is_held(name)
    owner = read_owner_pid(name) if held else None
    if not held:
        if endpoint is not None:
            remove_endpoint(name)
        state = "not-running"
        live = None
    elif endpoint is not None and endpoint.is_live() and owner == endpoint.pid:
        state = "running"
        live = endpoint
    else:
        state = "starting"
        live = None
    if as_json:
        payload = {
            "name": name,
            "state": state,
            "endpoint": live.to_dict() if live is not None else None,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif live is not None:
        print(f"hub {name!r} is running at {_describe_address(live)}")
    elif state == "starting":
        print(f"hub {name!r} is starting")
    else:
        print(f"hub {name!r} is not running")
    return 0 if state == "running" else EXIT_NOT_RUNNING


def _serve_hub(
    app: Any,
    options: Mapping[str, Any],
    *,
    name: str,
    idle_timeout: float,
    termination: _Termination | None = None,
) -> int:
    """Build and serve the gateway, publishing the endpoint once bound."""
    backend = options.get("backend", "fastapi")
    if backend == "httpserver":
        raise ValueError(
            "a local hub needs the ASGI backend; the httpserver backend "
            "cannot report the bound address or serve MCP transports"
        )
    gateway = app.create_gateway(**dict(options))
    clock = ActivityClock()
    gateway.set_activity_hook(clock.touch)
    state: dict[str, Any] = {"endpoint": None, "watchdog": None}

    def on_started(server: Any, bound: tuple[str, int] | None) -> None:
        transport = "unix" if options.get("socket_path") else "tcp"
        configured_host = str(options.get("host", "127.0.0.1"))
        endpoint = HubEndpoint.create(
            name=name,
            version=__version__,
            transport=transport,
            host=_advertised_host(bound, configured_host),
            port=bound[1] if bound is not None else None,
            socket=options.get("socket_path"),
            frontends=_frontend_paths(gateway),
        )
        write_endpoint(endpoint)
        state["endpoint"] = endpoint
        clock.touch()
        if termination is not None:
            # A SIGTERM that arrived before the listener existed stops the
            # server now, before it accepts a single request.
            termination.attach(server)
        if idle_timeout > 0:
            state["watchdog"] = asyncio.create_task(
                _idle_watchdog(server, gateway, clock, idle_timeout)
            )

    try:
        gateway.run(
            host=str(options.get("host", "127.0.0.1")),
            port=int(options.get("port") or 0),
            socket_path=options.get("socket_path"),
            on_started=on_started,
        )
    finally:
        watchdog = state.get("watchdog")
        if watchdog is not None:
            watchdog.cancel()
        _retire_endpoint(name, state.get("endpoint"))
    return 0


async def _idle_watchdog(
    server: Any,
    gateway: Any,
    clock: ActivityClock,
    idle_timeout: float,
    interval: float = 1.0,
) -> None:
    """Ask uvicorn to exit after *idle_timeout* seconds without activity."""
    while not server.should_exit:
        await asyncio.sleep(interval)
        if server.should_exit:
            return
        if gateway.open_session_count():
            clock.touch()
            continue
        if clock.idle_for() >= idle_timeout:
            logger.info(f"Local hub idle for {clock.idle_for():.0f}s; shutting down")
            server.should_exit = True
            return


def _frontend_paths(gateway: Any) -> dict[str, str]:
    """Return the enabled frontend mount paths."""
    return {
        record["name"]: record["path"]
        for record in gateway.list_frontends()
        if record.get("enabled")
    }


_WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::", "::0"})


def _advertised_host(bound: tuple[str, int] | None, configured: str) -> str:
    """Return a host a local client can connect to.

    A wildcard bind is not a connectable address, so it is advertised as
    loopback; a concrete bound address wins over the configured one.
    """
    host = bound[0] if bound is not None else configured
    return "127.0.0.1" if host in _WILDCARD_HOSTS else host


def _retire_endpoint(name: str, endpoint: HubEndpoint | None) -> None:
    """Remove this process's endpoint and socket, if it still owns them."""
    if endpoint is None:
        return
    current = None
    with contextlib.suppress(EndpointError):
        current = read_endpoint(name)
    if current is not None and current.pid == os.getpid():
        remove_endpoint(name)
    if endpoint.transport == "unix" and endpoint.socket:
        with contextlib.suppress(FileNotFoundError):
            Path(endpoint.socket).unlink()


def _running_endpoint(name: str) -> HubEndpoint | None:
    """Return *name*'s endpoint only when the lock owner wrote it.

    The lock owner check matters: a stale document can name a pid that is
    alive again after a reboot, so the pid alone does not identify a hub.
    """
    if not lock_is_held(name):
        return None
    endpoint = read_live_endpoint(name)
    if endpoint is None or endpoint.pid != read_owner_pid(name):
        return None
    return endpoint


def _wait_for_endpoint(
    name: str, process: subprocess.Popen | None, timeout: float
) -> int:
    """Poll for a published endpoint, failing when the child dies."""
    deadline = time.monotonic() + timeout
    while True:
        endpoint = _running_endpoint(name)
        if endpoint is not None:
            _print_endpoint(endpoint)
            return 0
        if process is not None and process.poll() is not None:
            # The child may have lost a start race; check once more.
            endpoint = _running_endpoint(name)
            if endpoint is not None:
                _print_endpoint(endpoint)
                return 0
            _report_child_failure(name, process)
            return 1
        if time.monotonic() >= deadline:
            if process is not None:
                process.terminate()
            print(
                f"hub {name!r} did not publish an endpoint within {timeout:.0f}s",
                file=sys.stderr,
            )
            return 1
        time.sleep(0.05)


def _spawn_hub(
    options: Mapping[str, Any],
    *,
    name: str,
    idle_timeout: float,
    argv: Sequence[str] | None,
) -> subprocess.Popen:
    """Start ``local-hub run`` detached from the caller."""
    command = _hub_command() + _run_arguments(argv, name, options, idle_timeout)
    log_path = hub_dir() / f"{name}.log"
    ensure_hub_dir()
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as log:
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            return subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=creationflags,
            )
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )


def _hub_command() -> list[str]:
    """Return the command that starts this package's CLI."""
    executable = shutil.which("toolregistry-server")
    if executable:
        return [executable]
    bootstrap = "from toolregistry_server.cli import main; main()"
    return [sys.executable, "-c", bootstrap]


def _run_arguments(
    argv: Sequence[str] | None,
    name: str,
    options: Mapping[str, Any],
    idle_timeout: float,
) -> list[str]:
    """Rewrite the invoking argv so the child runs the hub."""
    if argv:
        arguments = list(argv)
        try:
            position = arguments.index("local-hub")
        except ValueError:
            arguments = ["local-hub", "run", *arguments]
        else:
            following = position + 1
            if following < len(arguments) and arguments[following] in ACTIONS:
                arguments[following] = "run"
            else:
                arguments.insert(following, "run")
    else:
        arguments = ["local-hub", "run", "--name", name]
        if options.get("config_path"):
            arguments += ["--config", str(options["config_path"])]
    if idle_timeout > 0:
        arguments += ["--idle-timeout", f"{idle_timeout:g}"]
    return arguments


def _report_child_failure(name: str, process: subprocess.Popen) -> None:
    """Print why a spawned hub died, with the tail of its log."""
    tail = _log_tail(name)
    print(
        f"hub {name!r} exited with status {process.returncode} before publishing "
        "an endpoint",
        file=sys.stderr,
    )
    if tail:
        print(tail, file=sys.stderr)


def _log_tail(name: str, limit: int = 20) -> str:
    """Return the last *limit* lines of the hub log, if any."""
    try:
        lines = (hub_dir() / f"{name}.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-limit:])


def _print_endpoint(endpoint: HubEndpoint) -> None:
    """Print the endpoint document as JSON."""
    print(json.dumps(endpoint.to_dict(), indent=2, sort_keys=True))


def _describe_address(endpoint: HubEndpoint) -> str:
    """Return the URL or socket path of *endpoint*."""
    return endpoint.url or endpoint.socket or "<unknown>"


__all__ = [
    "ACTIONS",
    "DEFAULT_ENSURE_TIMEOUT",
    "DEFAULT_STOP_TIMEOUT",
    "EXIT_NOT_RUNNING",
    "ActivityClock",
    "HubLock",
    "LocalHubUnsupported",
    "ensure_hub",
    "lock_is_held",
    "run_hub",
    "status_hub",
    "stop_hub",
]
