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
    hub_dir,
    lock_path,
    read_endpoint,
    read_live_endpoint,
    remove_endpoint,
    socket_path,
    write_endpoint,
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


def test_malformed_json_raises():
    path = endpoint_path()
    hub_dir().mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(EndpointError, match="not valid JSON"):
        read_endpoint()


def test_a_document_for_another_name_is_refused():
    hub_dir().mkdir(parents=True)
    endpoint_path().write_text(
        json.dumps(_tcp(name="other").to_dict()), encoding="utf-8"
    )

    with pytest.raises(EndpointError, match="declares hub 'other'"):
        read_endpoint()


@pytest.mark.parametrize("name", ["", "../escape", "a/b", ".hidden", "x" * 65])
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
