"""Unix-domain-socket listener tests for the gateway (#145).

TCP on 127.0.0.1 stays the default; a UDS is opt-in on POSIX.  These tests
cover stale-socket recovery and one end-to-end serve over the socket.
"""

from __future__ import annotations

import os
import socket
import stat
import subprocess
import sys
import time

import httpx
import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.gateway import GatewayAdapter
from toolregistry_server.gateway.unix import prepare_unix_socket

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix domain sockets are POSIX-only"
)


def _bind_stale(path):
    """Create a socket file with no listener behind it."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.close()


def _wait_until_accepting(path, process, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"gateway exited before binding {path}: {output!r}")
        if path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.2)
            try:
                probe.connect(str(path))
                return
            except OSError:
                pass
            finally:
                probe.close()
        time.sleep(0.05)
    raise AssertionError(f"gateway did not bind {path} within {timeout}s")


def test_prepare_removes_a_stale_socket(tmp_path):
    path = tmp_path / "hub.sock"
    _bind_stale(path)

    prepare_unix_socket(path)

    assert path.exists() is False


def test_prepare_refuses_a_live_listener(tmp_path):
    path = tmp_path / "hub.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen(1)
    try:
        with pytest.raises(ValueError, match="already listening"):
            prepare_unix_socket(path)
    finally:
        sock.close()


def test_prepare_refuses_a_regular_file(tmp_path):
    path = tmp_path / "hub.sock"
    path.write_text("not a socket", encoding="utf-8")

    with pytest.raises(ValueError, match="not a socket"):
        prepare_unix_socket(path)


def test_prepare_refuses_an_overlong_path(tmp_path):
    path = tmp_path / ("a" * 200)

    with pytest.raises(ValueError, match="limit"):
        prepare_unix_socket(path)


def test_prepare_creates_a_private_parent_directory(tmp_path):
    parent = tmp_path / "state"
    path = parent / "hub.sock"

    prepare_unix_socket(path)

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


def _wait_for_mode(path, expected, process, timeout=5):
    """Wait until *path* reports *expected* permission bits.

    uvicorn binds the listener and chmods it to ``0o666`` inside its startup
    hook; the gateway tightens it to ``0o600`` before that hook returns.  A
    probe that connects between the two calls must not fail the test.
    """
    deadline = time.monotonic() + timeout
    mode = 0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(
                f"gateway exited before the socket was private: {output!r}"
            )
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"socket mode {mode:#o}, expected {expected:#o}")


def test_httpserver_backend_refuses_a_live_listener(tmp_path):
    """The httpserver backend must not unlink and take over a live socket."""
    path = tmp_path / "hub.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    try:
        registry = ToolRegistry()
        gateway = GatewayAdapter.create(
            RouteTable(registry),
            backend="httpserver",
            mcp_enabled=False,
            sse_enabled=False,
            admin_enabled=False,
            socket_path=str(path),
        )
        with pytest.raises(ValueError, match="already listening"):
            gateway.run(socket_path=str(path))

        # The live listener is still there and still accepting.
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(path))
        finally:
            probe.close()
    finally:
        listener.close()


_SERVER_SCRIPT = """
import sys

from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.gateway import GatewayAdapter


def add(a: int, b: int) -> int:
    '''Add two integers.'''
    return a + b


registry = ToolRegistry()
registry.register(add)
gateway = GatewayAdapter.create(
    RouteTable(registry),
    tokens=[],
    mcp_enabled=False,
    sse_enabled=False,
)
gateway.run(host="127.0.0.1", port=0, socket_path=sys.argv[1])
"""


def test_gateway_serves_over_a_unix_socket(tmp_path):
    path = tmp_path / "hub.sock"
    # A file left by a crashed server must not block the bind.
    _bind_stale(path)

    process = subprocess.Popen(
        [sys.executable, "-c", _SERVER_SCRIPT, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_until_accepting(path, process)

        _wait_for_mode(path, 0o600, process)

        with httpx.Client(
            transport=httpx.HTTPTransport(uds=str(path)),
            base_url="http://localhost",
        ) as client:
            response = client.get("/openapi/tools")

        assert response.status_code == 200
        assert '"add"' in response.text
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
