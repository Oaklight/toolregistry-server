"""Endpoint-file tests for the local hub (#145).

The endpoint document is the contract between a resident hub and its
clients.  These tests pin its atomic replacement, permissions, liveness and
protocol checks so the shim and the lock can depend on it.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import pytest

from toolregistry_server.gateway import endpoint
from toolregistry_server.gateway.endpoint import (
    DEFAULT_NAME,
    HUB_PROTOCOL_MAJOR,
    EndpointError,
    HubEndpoint,
    endpoint_path,
    ensure_hub_dir,
    hub_dir,
    lock_path,
    pid_path,
    read_endpoint,
    read_live_endpoint,
    read_owner_pid,
    remove_endpoint,
    remove_owner_pid,
    socket_path,
    write_endpoint,
    write_owner_pid,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep every test out of the real state directory."""
    monkeypatch.setenv("TOOLREGISTRY_HOME", str(tmp_path))
    return tmp_path


def _tcp(name: str = DEFAULT_NAME, **kwargs) -> HubEndpoint:
    values = {
        "name": name,
        "pid": os.getpid(),
        "version": "0.9.0",
        "transport": "tcp",
        "host": "127.0.0.1",
        "port": 8765,
        "frontends": {"mcp": "/mcp"},
    }
    values.update(kwargs)
    return HubEndpoint.create(**values)


def test_write_creates_a_private_file_and_directory(isolated_home):
    endpoint_file = write_endpoint(_tcp())

    assert endpoint_file == endpoint_path()
    assert stat.S_IMODE(endpoint_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(hub_dir().stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
def test_an_existing_directory_is_tightened(isolated_home):
    directory = hub_dir()
    directory.mkdir(parents=True)
    os.chmod(directory, 0o755)

    ensure_hub_dir()

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_round_trip():
    written = _tcp()
    write_endpoint(written)

    assert read_endpoint() == written


def test_write_leaves_no_temporary_file():
    write_endpoint(_tcp())

    assert sorted(path.name for path in hub_dir().iterdir()) == ["default.json"]


def test_missing_endpoint_returns_none():
    assert read_endpoint() is None
    assert read_live_endpoint() is None


def _write_raw(path, text):
    """Write an endpoint file the way the writer does: mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


def test_malformed_json_raises():
    path = endpoint_path()
    _write_raw(path, "{not json")

    with pytest.raises(EndpointError, match="not valid JSON"):
        read_endpoint()


def test_a_document_for_another_name_is_refused():
    _write_raw(endpoint_path(), json.dumps(_tcp(name="other").to_dict()))

    with pytest.raises(EndpointError, match="declares hub 'other'"):
        read_endpoint()


def test_a_readable_endpoint_is_refused():
    path = endpoint_path()
    _write_raw(path, json.dumps(_tcp().to_dict()))
    os.chmod(path, 0o644)

    with pytest.raises(EndpointError, match="accessible to other users"):
        read_endpoint()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_a_symlinked_endpoint_is_refused():
    isolated_target = hub_dir().parent / "elsewhere.json"
    _write_raw(isolated_target, json.dumps(_tcp().to_dict()))
    hub_dir().mkdir(parents=True, exist_ok=True)
    os.symlink(isolated_target, endpoint_path())

    with pytest.raises(EndpointError, match="cannot read"):
        read_endpoint()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFOs")
def test_a_fifo_endpoint_is_refused_not_blocking():
    """A planted FIFO must not block the open; it is refused instead."""
    hub_dir().mkdir(parents=True, exist_ok=True)
    os.mkfifo(endpoint_path())

    with pytest.raises(EndpointError, match="not a regular file"):
        read_endpoint()


@pytest.mark.parametrize("name", ["", "../escape", "a/b", ".hidden", "x" * 65, "ok\n"])
def test_invalid_names_are_refused(name):
    with pytest.raises(EndpointError, match="must match"):
        endpoint_path(name)


def test_paths_stay_under_the_state_root(isolated_home):
    assert endpoint_path("work") == isolated_home / "hub" / "work.json"
    assert lock_path("work") == isolated_home / "hub" / "work.lock"
    assert socket_path("work") == isolated_home / "hub" / "work.sock"


def test_current_pid_is_alive():
    assert endpoint.pid_is_alive(os.getpid()) is True


def test_a_finished_child_is_not_alive():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)

    assert endpoint.pid_is_alive(child.pid) is False


def test_read_live_endpoint_ignores_a_dead_writer():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    write_endpoint(_tcp(pid=child.pid))

    assert read_endpoint() is not None
    assert read_live_endpoint() is None


def test_protocol_mismatch_is_refused():
    written = _tcp()
    written.check_protocol(HUB_PROTOCOL_MAJOR)

    with pytest.raises(EndpointError, match="speaks endpoint protocol"):
        written.check_protocol(HUB_PROTOCOL_MAJOR + 1)


def test_tcp_requires_host_and_port():
    with pytest.raises(EndpointError, match="requires 'host' and 'port'"):
        HubEndpoint.create(name="default", version="0.9.0", transport="tcp", port=None)


def test_unix_requires_a_socket():
    with pytest.raises(EndpointError, match="requires 'socket'"):
        HubEndpoint.create(name="default", version="0.9.0", transport="unix")


def test_unknown_transport_is_refused():
    with pytest.raises(EndpointError, match="transport must be one of"):
        HubEndpoint.from_dict(
            {
                "name": "default",
                "pid": os.getpid(),
                "version": "0.9.0",
                "protocol": HUB_PROTOCOL_MAJOR,
                "transport": "carrier-pigeon",
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        )


def test_tcp_url_is_built_from_the_bind_address():
    assert _tcp(host="127.0.0.1", port=8765).url == "http://127.0.0.1:8765"
    assert _tcp(host="::1", port=8765).url == "http://[::1]:8765"


def test_unix_has_no_url():
    written = HubEndpoint.create(
        name="default",
        version="0.9.0",
        transport="unix",
        socket="/tmp/hub.sock",
    )

    assert written.url is None
    assert written.socket == "/tmp/hub.sock"


def test_owner_pid_is_private_and_round_trips():
    write_owner_pid("work", 4242)

    assert read_owner_pid("work") == 4242
    assert stat.S_IMODE(pid_path("work").stat().st_mode) == 0o600
    assert list(hub_dir().glob(".*.tmp")) == []

    remove_owner_pid("work")
    assert read_owner_pid("work") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_a_widened_pid_file_is_refused():
    write_owner_pid("work", 4242)
    os.chmod(pid_path("work"), 0o644)

    with pytest.raises(EndpointError, match="accessible to other users"):
        read_owner_pid("work")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_a_symlinked_pid_file_is_refused():
    target = hub_dir().parent / "pid-target"
    _write_raw(target, "4242\n")
    hub_dir().mkdir(parents=True, exist_ok=True)
    os.symlink(target, pid_path("work"))

    with pytest.raises(EndpointError, match="cannot read"):
        read_owner_pid("work")


def test_remove_endpoint_is_idempotent():
    write_endpoint(_tcp())

    remove_endpoint()
    remove_endpoint()

    assert endpoint_path().exists() is False


def test_document_is_stable_json():
    write_endpoint(_tcp())

    payload = json.loads(endpoint_path().read_text(encoding="utf-8"))

    assert payload["protocol"] == HUB_PROTOCOL_MAJOR
    assert payload["transport"] == "tcp"
    assert payload["frontends"] == {"mcp": "/mcp"}
