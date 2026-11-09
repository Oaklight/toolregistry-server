"""Local-hub lifecycle tests (#145).

The hub is a resident gateway with an endpoint document, an exclusive lock
and an optional idle timeout.  These tests pin the lock-as-liveness rule,
the endpoint contents, the stop/status exit codes and the idle logic.  The
subprocess tests start a real gateway; nothing is mocked there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from toolregistry import ToolRegistry

from toolregistry_server import App
from toolregistry_server.gateway import local_hub
from toolregistry_server.gateway.app import _SseSessionCounter
from toolregistry_server.gateway.endpoint import (
    HubEndpoint,
    endpoint_path,
    lock_path,
    read_endpoint,
    read_live_endpoint,
    read_owner_pid,
    remove_owner_pid,
    write_endpoint,
    write_owner_pid,
)
from toolregistry_server.gateway.local_hub import (
    ActivityClock,
    HubLock,
    lock_is_held,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep every test out of the real state directory."""
    monkeypatch.setenv("TOOLREGISTRY_HOME", str(tmp_path))
    return tmp_path


def _tcp(name: str = "default", port: int = 4321) -> HubEndpoint:
    return HubEndpoint.create(
        name=name,
        pid=os.getpid(),
        version="0.9.0",
        transport="tcp",
        host="127.0.0.1",
        port=port,
        frontends={"openapi": "/openapi"},
    )


class TestHubLock:
    """One process owns a name; the lock file is private."""

    def test_a_second_lock_is_refused_until_release(self):
        first = HubLock("work")
        second = HubLock("work")
        assert first.acquire() is True
        try:
            assert second.acquire() is False
            assert lock_is_held("work") is True
            # The lock itself holds no data; the owner pid is a separate file.
            assert read_owner_pid("work") is None
            assert lock_path("work").read_bytes() == b""
        finally:
            first.release()
        assert lock_is_held("work") is False
        assert second.acquire() is True
        second.release()

    def test_the_lock_file_is_private(self):
        lock = HubLock("work")
        assert lock.acquire() is True
        try:
            assert lock_path("work").stat().st_mode & 0o777 == 0o600
        finally:
            lock.release()


class TestStatusAndStopAuthority:
    """The lock decides liveness; a live pid in the document does not."""

    def test_status_reports_not_running_for_a_live_pid_without_a_lock(self, capsys):
        write_endpoint(_tcp())

        assert local_hub.status_hub() == local_hub.EXIT_NOT_RUNNING
        assert "not running" in capsys.readouterr().out
        assert not endpoint_path().exists()

    def test_stop_does_not_signal_without_a_lock(self, monkeypatch):
        write_endpoint(_tcp())
        signals = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))

        assert local_hub.stop_hub() == local_hub.EXIT_NOT_RUNNING
        assert signals == []
        assert not endpoint_path().exists()

    def test_status_reports_a_running_hub(self, capsys):
        lock = HubLock("default")
        assert lock.acquire() is True
        try:
            write_owner_pid("default", os.getpid())
            write_endpoint(_tcp(port=4321))
            assert local_hub.status_hub(as_json=True) == 0
        finally:
            remove_owner_pid("default")
            lock.release()

        payload = json.loads(capsys.readouterr().out)
        assert payload["state"] == "running"
        assert payload["endpoint"]["port"] == 4321

    def test_status_reports_starting_when_the_pid_is_not_the_owner(self, capsys):
        write_endpoint(_tcp())
        # Another process owns the name; the document still names this pid.
        holder = _hold_lock_in_child("default")
        try:
            assert local_hub.status_hub() == local_hub.EXIT_NOT_RUNNING
        finally:
            holder.terminate()
            holder.wait(timeout=10)
        assert "starting" in capsys.readouterr().out


def _hold_lock_in_child(name: str) -> subprocess.Popen:
    """Hold *name*'s lock from another process until it is terminated."""
    script = (
        "import os, sys, time\n"
        "from toolregistry_server.gateway.endpoint import write_owner_pid\n"
        "from toolregistry_server.gateway.local_hub import HubLock\n"
        "lock = HubLock(sys.argv[1])\n"
        "assert lock.acquire()\n"
        "write_owner_pid(sys.argv[1], os.getpid())\n"
        "sys.stdout.write('locked\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, name],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "locked"
    return process


class TestEnsure:
    """ensure returns the endpoint of a running hub without spawning."""

    def test_ensure_returns_the_running_endpoint(self, capsys):
        lock = HubLock("default")
        assert lock.acquire() is True
        try:
            write_owner_pid("default", os.getpid())
            write_endpoint(_tcp(port=4321))
            assert local_hub.ensure_hub(None, {}, timeout=1.0) == 0
        finally:
            remove_owner_pid("default")
            lock.release()

        payload = json.loads(capsys.readouterr().out)
        assert payload["port"] == 4321

    def test_ensure_waits_for_a_starting_hub_then_times_out(self):
        lock = HubLock("default")
        assert lock.acquire() is True
        try:
            assert local_hub.ensure_hub(None, {}, timeout=0.2) == 1
        finally:
            lock.release()


class TestRunArguments:
    """The spawned child inherits the invoking command line."""

    def test_ensure_is_rewritten_to_run(self):
        arguments = local_hub._run_arguments(
            ["local-hub", "ensure", "--name", "work"], "work", {}, 0.0
        )
        assert arguments == ["local-hub", "run", "--name", "work"]

    def test_run_is_inserted_when_no_action_is_given(self):
        assert local_hub._run_arguments(["local-hub"], "work", {}, 0.0) == [
            "local-hub",
            "run",
        ]

    def test_idle_timeout_is_appended(self):
        assert local_hub._run_arguments(["local-hub"], "work", {}, 30.0) == [
            "local-hub",
            "run",
            "--idle-timeout",
            "30",
        ]


class TestPlatformAndAddress:
    """The CLI refuses Windows and advertises a connectable host."""

    def test_windows_refuses_every_action(self, monkeypatch):
        monkeypatch.setattr(local_hub, "os", SimpleNamespace(name="nt"))
        calls = (
            lambda: local_hub.run_hub(None, {}),
            lambda: local_hub.ensure_hub(None, {}),
            lambda: local_hub.stop_hub(),
            lambda: local_hub.status_hub(),
        )
        for call in calls:
            with pytest.raises(local_hub.LocalHubUnsupported, match="Windows"):
                call()

    @pytest.mark.parametrize(
        ("bound", "configured", "expected"),
        [
            (("127.0.0.1", 5000), "0.0.0.0", "127.0.0.1"),
            (("0.0.0.0", 5000), "localhost", "127.0.0.1"),
            (("::1", 5000), "::1", "::1"),
            (None, "0.0.0.0", "127.0.0.1"),
            (None, "127.0.0.1", "127.0.0.1"),
        ],
    )
    def test_a_wildcard_bind_is_advertised_as_loopback(
        self, bound, configured, expected
    ):
        assert local_hub._advertised_host(bound, configured) == expected


@pytest.mark.asyncio
async def test_sse_counter_brackets_the_request():
    class _FakeGateway:
        _sse_connections = 0

    observed = []

    async def app(scope, receive, send):
        observed.append(gateway._sse_connections)

    gateway = _FakeGateway()
    counter = _SseSessionCounter(app, gateway)
    await counter({"type": "http"}, None, None)

    assert observed == [1]
    assert gateway._sse_connections == 0


@pytest.mark.asyncio
async def test_idle_watchdog_skips_while_a_session_is_open():
    server = SimpleNamespace(should_exit=False)
    clock = ActivityClock()
    clock._last -= 100

    gateway = SimpleNamespace(open_session_count=lambda: 1)
    task = asyncio.create_task(
        local_hub._idle_watchdog(server, gateway, clock, 0.1, interval=0.01)
    )
    await asyncio.sleep(0.05)
    assert server.should_exit is False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_idle_watchdog_exits_when_idle():
    server = SimpleNamespace(should_exit=False)
    clock = ActivityClock()
    clock._last -= 100

    gateway = SimpleNamespace(open_session_count=lambda: 0)
    await local_hub._idle_watchdog(server, gateway, clock, 0.1, interval=0.01)
    assert server.should_exit is True


_HUB_SCRIPT = """
import sys

from toolregistry import ToolRegistry

from toolregistry_server import App
from toolregistry_server.gateway import local_hub


def add(a: int, b: int) -> int:
    '''Add two integers.'''
    return a + b


registry = ToolRegistry()
registry.register(add)
options = {
    "registry": registry,
    "host": "127.0.0.1",
    "port": 0,
    "mcp_enabled": False,
    "sse_enabled": False,
}
sys.exit(
    local_hub.run_hub(
        App(), options, name=sys.argv[1], idle_timeout=float(sys.argv[2])
    )
)
"""


def _start_hub(name: str = "default", idle: float = 0.0) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _HUB_SCRIPT, name, str(idle)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for_endpoint(
    name: str = "default", process: subprocess.Popen | None = None, timeout: float = 30
) -> HubEndpoint:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"hub exited before publishing: {output!r}")
        endpoint = read_live_endpoint(name)
        if (
            endpoint is not None
            and lock_is_held(name)
            and read_owner_pid(name) == endpoint.pid
        ):
            return endpoint
        time.sleep(0.05)
    raise AssertionError(f"hub {name!r} did not publish an endpoint")


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


class TestRunningHub:
    """A real gateway process publishes, serves and stops."""

    def test_endpoint_names_the_bound_port(self):
        process = _start_hub()
        try:
            endpoint = _wait_for_endpoint(process=process)

            assert endpoint.transport == "tcp"
            assert endpoint.host == "127.0.0.1"
            assert endpoint.port > 0
            assert endpoint.pid == process.pid
            assert endpoint.frontends == {"openapi": "/openapi"}
            assert endpoint.url == f"http://127.0.0.1:{endpoint.port}"
            assert read_owner_pid("default") == process.pid
            assert read_endpoint() == endpoint
        finally:
            _terminate(process)

    def test_stop_ends_the_hub_and_removes_the_endpoint(self):
        process = _start_hub(name="work")
        try:
            _wait_for_endpoint("work", process)

            assert local_hub.stop_hub("work", timeout=10) == 0
            process.wait(timeout=10)

            assert not endpoint_path("work").exists()
            assert read_owner_pid("work") is None
            assert lock_is_held("work") is False
            assert local_hub.status_hub("work") == local_hub.EXIT_NOT_RUNNING
        finally:
            _terminate(process)

    def test_a_second_hub_for_the_same_name_is_refused(self):
        process = _start_hub(name="taken")
        try:
            _wait_for_endpoint("taken", process)
            second = _start_hub(name="taken")
            output, _ = second.communicate(timeout=30)

            assert second.returncode == 1
            assert "already serves" in output
        finally:
            _terminate(process)

    def test_idle_timeout_ends_a_quiet_hub(self):
        process = _start_hub(name="idle", idle=1.0)
        try:
            _wait_for_endpoint("idle", process)
            process.wait(timeout=20)

            assert process.returncode == 0
            assert not endpoint_path("idle").exists()
        finally:
            _terminate(process)

    def test_a_request_resets_the_idle_clock(self):
        process = _start_hub(name="busy", idle=3.0)
        try:
            endpoint = _wait_for_endpoint("busy", process)
            time.sleep(1.5)
            with httpx.Client(base_url=endpoint.url or "") as client:
                assert client.get("/openapi/tools").status_code == 200
            time.sleep(1.5)
            assert process.poll() is None

            process.wait(timeout=20)
        finally:
            _terminate(process)


def test_ensure_starts_a_hub_and_stop_ends_it(isolated_home, tmp_path):
    """End to end through the CLI: ensure spawns, stop reaps."""
    config = tmp_path / "tools.yaml"
    config.write_text("tools: []\n", encoding="utf-8")
    name = "ensure-test"
    env = {**os.environ, "TOOLREGISTRY_HOME": str(isolated_home)}
    command = local_hub._hub_command()

    ensured = subprocess.run(
        command
        + [
            "local-hub",
            "ensure",
            "--config",
            str(config),
            "--name",
            name,
            "--timeout",
            "30",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
    )
    try:
        assert ensured.returncode == 0, ensured.stderr
        payload = json.loads(ensured.stdout)
        assert payload["name"] == name
        assert payload["port"] > 0

        endpoint = read_endpoint(name)
        assert endpoint is not None
        assert endpoint.pid != os.getpid()
        assert local_hub.status_hub(name) == 0
        with httpx.Client(base_url=endpoint.url or "") as client:
            assert client.get("/openapi/tools").status_code == 200
    finally:
        stopped = subprocess.run(
            command + ["local-hub", "stop", "--name", name],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
    assert stopped.returncode == 0, stopped.stderr
    assert not endpoint_path(name).exists()


@pytest.mark.asyncio
async def test_open_session_count_sees_a_streamable_http_session():
    """A real MCP session is counted while the client holds it open."""
    streamable_http = pytest.importorskip("mcp.client.streamable_http")
    session_module = pytest.importorskip("mcp.client.session")
    streamablehttp_client = streamable_http.streamablehttp_client
    client_session = session_module.ClientSession

    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    registry = ToolRegistry()
    registry.register(add)
    gateway = App().create_gateway(registry=registry, host="127.0.0.1", port=0)

    started: queue.Queue = queue.Queue()
    thread = threading.Thread(
        target=asyncio.run,
        args=(
            gateway.run_async(
                host="127.0.0.1",
                port=0,
                on_started=lambda server, bound: started.put((server, bound)),
            ),
        ),
        daemon=True,
    )
    thread.start()
    server, bound = await asyncio.to_thread(started.get, True, 60)
    try:
        url = f"http://{bound[0]}:{bound[1]}/mcp"
        async with (
            streamablehttp_client(url) as (read, write, _),
            client_session(read, write) as session,
        ):
            await session.initialize()

            assert gateway.open_session_count() >= 1
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 30)

    assert gateway.open_session_count() == 0
