"""stdio shim tests (#145).

The shim is the client-side half of the local hub: it answers ``initialize``
locally, proxies calls to the hub, treats a document with nothing answering
as "no hub", reconnects with backoff and refreshes the client's tool list when
the hub's tools change.  The integration tests start a real gateway process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import time
import weakref
from types import SimpleNamespace

import pytest

from toolregistry_server.adapters.mcp._compat import (
    _get_mcp_error_code,
    create_test_client,
)
from toolregistry_server.gateway import local_hub, shim
from toolregistry_server.gateway.endpoint import (
    HUB_PROTOCOL_MAJOR,
    HubEndpoint,
    read_live_endpoint,
    read_owner_pid,
    write_endpoint,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep every test out of the real state directory."""
    monkeypatch.setenv("TOOLREGISTRY_HOME", str(tmp_path))
    return tmp_path


def _config(**kwargs) -> shim.ShimConfig:
    kwargs.setdefault("spawn", False)
    return shim.ShimConfig(**kwargs)


def _tool(name: str = "add", description: str = "Add two integers", schema=None):
    return SimpleNamespace(
        name=name,
        description=description,
        input_schema=schema or {"type": "object"},
    )


def _tcp_endpoint(
    name: str = "default",
    port: int = 4321,
    frontends: dict[str, str] | None = None,
    protocol: int = HUB_PROTOCOL_MAJOR,
) -> HubEndpoint:
    return HubEndpoint.create(
        name=name,
        pid=os.getpid(),
        version="0.9.0",
        transport="tcp",
        host="127.0.0.1",
        port=port,
        frontends=frontends if frontends is not None else {"mcp": "/mcp"},
        protocol=protocol,
    )


def _unused_port() -> int:
    """Return a loopback port that was free a moment ago."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


class TestToolFingerprint:
    """The fingerprint tracks exactly what changes the client's list."""

    def test_an_identical_list_hashes_the_same(self):
        assert shim.tool_fingerprint([_tool()]) == shim.tool_fingerprint([_tool()])

    @pytest.mark.parametrize(
        "changed",
        [
            _tool(name="plus"),
            _tool(description="Add integers"),
            _tool(schema={"type": "object", "properties": {"a": {"type": "integer"}}}),
        ],
    )
    def test_a_changed_name_description_or_schema_changes_the_digest(self, changed):
        assert shim.tool_fingerprint([_tool()]) != shim.tool_fingerprint([changed])

    def test_camel_case_schemas_are_read_too(self):
        """SDK v1 spells the schema ``inputSchema``."""
        legacy = SimpleNamespace(name="add", description="Add", inputSchema={"a": 1})
        camel = shim.tool_fingerprint([legacy])
        probe = SimpleNamespace(name="add", description="Add", input_schema={"a": 1})
        assert camel == shim.tool_fingerprint([probe])


class TestEndpointUrl:
    """The shim connects to the MCP frontend as the document describes it."""

    def test_tcp_uses_the_published_address_and_mcp_path(self):
        endpoint = _tcp_endpoint(frontends={"mcp": "/rpc", "openapi": "/api"})
        assert shim.endpoint_url(endpoint) == "http://127.0.0.1:4321/rpc"

    def test_a_document_without_the_frontend_falls_back_to_mcp(self):
        endpoint = _tcp_endpoint(frontends={"openapi": "/api"})
        assert shim.endpoint_url(endpoint) == "http://127.0.0.1:4321/mcp"

    def test_unix_uses_a_placeholder_host(self):
        endpoint = HubEndpoint.create(
            name="default",
            pid=os.getpid(),
            version="0.9.0",
            transport="unix",
            socket="/tmp/hub.sock",
            frontends={"mcp": "/mcp"},
        )
        assert shim.endpoint_url(endpoint) == "http://localhost/mcp"


class _FakeHttp:
    """Stands in for the SDK's HTTP client module."""

    def __init__(self) -> None:
        self.transports: list[SimpleNamespace] = []
        self.clients: list[SimpleNamespace] = []

    def HTTPTransport(self, *, uds: str) -> SimpleNamespace:
        transport = SimpleNamespace(uds=uds)
        self.transports.append(transport)
        return transport

    def AsyncClient(self, **kwargs) -> SimpleNamespace:
        client = SimpleNamespace(**kwargs)
        self.clients.append(client)
        return client


class TestTransportSelection:
    """Both SDK generations are supported, with and without a socket."""

    def test_new_style_client_receives_a_unix_socket_transport(self, monkeypatch):
        http = _FakeHttp()
        calls = []

        class FakeModule:
            httpx = http

            @staticmethod
            def streamable_http_client(url, http_client=None):
                calls.append((url, http_client))
                return "new-transport"

        monkeypatch.setattr(shim, "_streamable_http_module", lambda: FakeModule)

        assert shim.open_hub_transport("http://x/mcp", "/tmp/h.sock") == "new-transport"
        url, client = calls[0]
        assert url == "http://x/mcp"
        assert client.transport.uds == "/tmp/h.sock"

    def test_new_style_client_over_tcp_leaves_the_transport_to_the_sdk(
        self, monkeypatch
    ):
        http = _FakeHttp()
        calls = []

        class FakeModule:
            httpx = http

            @staticmethod
            def streamable_http_client(url, http_client=None):
                calls.append((url, http_client))
                return "new-transport"

        monkeypatch.setattr(shim, "_streamable_http_module", lambda: FakeModule)

        assert shim.open_hub_transport("http://x/mcp") == "new-transport"
        assert calls[0][1] is None

    def test_old_style_client_takes_a_unix_socket_factory(self, monkeypatch):
        http = _FakeHttp()

        class FakeModule:
            httpx = http

            @staticmethod
            def streamablehttp_client(url, httpx_client_factory=None):
                return url, httpx_client_factory

        monkeypatch.setattr(shim, "_streamable_http_module", lambda: FakeModule)

        url, factory = shim.open_hub_transport("http://x/mcp", "/tmp/h.sock")
        assert url == "http://x/mcp"

        client = factory(headers={"x": "1"}, timeout=5.0, auth=None)
        assert client.transport.uds == "/tmp/h.sock"
        assert client.headers == {"x": "1"}
        assert client.timeout == 5.0
        assert "auth" in client.__dict__

    def test_an_sdk_without_a_streamable_client_is_refused(self, monkeypatch):
        class FakeModule:
            pass

        monkeypatch.setattr(shim, "_streamable_http_module", lambda: FakeModule)

        with pytest.raises(shim.ShimError, match="streamable-HTTP"):
            shim.open_hub_transport("http://x/mcp")

    def test_the_http_module_falls_back_to_httpx(self):
        import httpx

        assert shim._sdk_http_module(SimpleNamespace()) is httpx


class TestHubConnectionWithoutAHub:
    """The client stays usable while the hub is down."""

    async def test_initialize_and_list_succeed_while_downed(self):
        connection = shim.HubConnection(_config())
        try:
            await connection.start()

            assert connection.connected is False
            assert connection.tools == []
            assert await connection.list_tools() == []
        finally:
            await connection.close()

    async def test_a_call_fails_once_with_a_clear_mcp_error(self):
        connection = shim.HubConnection(_config())
        try:
            with pytest.raises(Exception) as excinfo:
                await connection.call_tool("add", {"a": 1, "b": 2})

            assert _get_mcp_error_code(excinfo.value) == shim.HUB_ERROR_CODE
            assert "hub 'default' is unavailable" in str(excinfo.value)
        finally:
            await connection.close()

    async def test_a_document_with_nothing_answering_is_no_hub(self):
        """A stale document must not make the shim block or give up."""
        write_endpoint(_tcp_endpoint(port=_unused_port()))

        connection = shim.HubConnection(_config())
        assert await connection.ensure() is False
        assert await connection.list_tools() == []

    async def test_another_names_hub_is_not_used(self):
        write_endpoint(_tcp_endpoint(name="other", port=_unused_port()))

        connection = shim.HubConnection(_config(name="default"))
        assert await connection.ensure() is False

    async def test_a_protocol_major_mismatch_is_refused(self):
        """A hub that answers but speaks another generation is not used."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        try:
            endpoint = _tcp_endpoint(
                port=int(listener.getsockname()[1]),
                protocol=HUB_PROTOCOL_MAJOR + 1,
            )
            write_endpoint(endpoint)

            connection = shim.HubConnection(_config())
            with pytest.raises(shim.ShimError, match="protocol"):
                await connection.start()
        finally:
            listener.close()

    async def test_a_stale_foreign_document_is_no_hub(self):
        """A mismatched document with nothing listening is stale, not a hub."""
        write_endpoint(
            _tcp_endpoint(port=_unused_port(), protocol=HUB_PROTOCOL_MAJOR + 1)
        )

        connection = shim.HubConnection(_config())
        assert await connection.ensure() is False

    async def test_the_stdio_server_serves_an_empty_list(self):
        sessions: weakref.WeakSet = weakref.WeakSet()
        connection = shim.HubConnection(_config())
        server = shim.build_server(connection, sessions)
        try:
            async with create_test_client(server) as client:
                result = await client.list_tools()
                assert list(result.tools) == []
        finally:
            await connection.close()


class TestReconnect:
    """Reconnect is bounded, and a changed fingerprint reaches the client."""

    async def test_backoff_starts_at_a_quarter_second_and_doubles(self, monkeypatch):
        connection = shim.HubConnection(_config())
        delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            delays.append(delay)

        async def fake_ensure(*, spawn: bool = True) -> bool:
            return len(delays) >= 4

        monkeypatch.setattr(connection, "ensure", fake_ensure)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        await connection._reconnect()

        assert delays == [0.25, 0.5, 1.0, 2.0]
        assert shim.MAX_BACKOFF == 5.0

    async def test_a_changed_tool_list_notifies_the_client(self, monkeypatch):
        connection = shim.HubConnection(_config())
        notified = asyncio.Event()

        async def notifier() -> None:
            notified.set()

        connection.set_notifier(notifier)
        endpoint = _tcp_endpoint()

        async def open_first(_endpoint):
            return contextlib.AsyncExitStack(), SimpleNamespace(), [_tool()]

        monkeypatch.setattr(connection, "_open", open_first)
        assert await connection._connect(endpoint) is True
        await asyncio.sleep(0.05)
        assert not notified.is_set()

        await connection._drop()

        async def open_changed(_endpoint):
            return (
                contextlib.AsyncExitStack(),
                SimpleNamespace(),
                [_tool(description="Add integers")],
            )

        monkeypatch.setattr(connection, "_open", open_changed)
        assert await connection._connect(endpoint) is True
        await asyncio.sleep(0.05)
        assert notified.is_set()

        await connection._drop()

    async def test_an_identical_tool_list_stays_quiet(self, monkeypatch):
        connection = shim.HubConnection(_config())
        notifications = []

        async def notifier() -> None:
            notifications.append(True)

        connection.set_notifier(notifier)
        endpoint = _tcp_endpoint()

        async def open_same(_endpoint):
            return contextlib.AsyncExitStack(), SimpleNamespace(), [_tool()]

        monkeypatch.setattr(connection, "_open", open_same)
        assert await connection._connect(endpoint) is True
        await connection._drop()
        assert await connection._connect(endpoint) is True
        await asyncio.sleep(0.05)

        assert notifications == []
        await connection._drop()


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
}
if len(sys.argv) > 2:
    options["socket_path"] = sys.argv[2]
sys.exit(local_hub.run_hub(App(), options, name=sys.argv[1]))
"""


def _start_hub(name: str, socket_path: str | None = None) -> subprocess.Popen:
    arguments = [sys.executable, "-c", _HUB_SCRIPT, name]
    if socket_path is not None:
        arguments.append(socket_path)
    return subprocess.Popen(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for_endpoint(
    name: str, process: subprocess.Popen, timeout: float = 30
) -> HubEndpoint:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"hub exited before publishing: {output!r}")
        endpoint = read_live_endpoint(name)
        if (
            endpoint is not None
            and local_hub.lock_is_held(name)
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


def _text_of(result) -> str:
    return "".join(block.text for block in result.content)


class TestShimAgainstARealHub:
    """A real gateway serves the shim; stopping it degrades cleanly."""

    async def test_a_stale_document_starts_a_hub_through_ensure(self, tmp_path):
        """A document naming a dead address is "no hub"; the shim starts one."""
        config = tmp_path / "tools.yaml"
        config.write_text("tools: []\n", encoding="utf-8")
        write_endpoint(_tcp_endpoint(name="shim-spawn", port=_unused_port()))
        connection = shim.HubConnection(
            shim.ShimConfig(name="shim-spawn", config_path=str(config), spawn=True)
        )
        try:
            assert await connection.list_tools() == []
            assert connection.connected is True
        finally:
            await connection.close()
            local_hub.stop_hub("shim-spawn", timeout=10)

    async def test_a_tool_call_goes_through_the_hub(self):
        process = _start_hub("shim-tcp")
        connection = shim.HubConnection(_config(name="shim-tcp"))
        try:
            await asyncio.to_thread(_wait_for_endpoint, "shim-tcp", process)
            await connection.start()

            assert connection.connected is True
            assert [tool.name for tool in connection.tools] == ["add"]

            result = await connection.call_tool("add", {"a": 2, "b": 3})
            assert "5" in _text_of(result)
        finally:
            await connection.close()
            _terminate(process)

    async def test_stopping_the_hub_makes_calls_fail_once(self):
        process = _start_hub("shim-stop")
        connection = shim.HubConnection(_config(name="shim-stop"))
        try:
            await asyncio.to_thread(_wait_for_endpoint, "shim-stop", process)
            await connection.start()
            assert connection.connected is True

            assert local_hub.stop_hub("shim-stop", timeout=10) == 0
            process.wait(timeout=10)

            with pytest.raises(Exception) as excinfo:
                await connection.call_tool("add", {"a": 1, "b": 1})
            assert "unavailable" in str(excinfo.value)
            assert await connection.list_tools() == []
        finally:
            await connection.close()
            _terminate(process)

    @pytest.mark.skipif(os.name == "nt", reason="local hub is POSIX-only")
    async def test_the_hub_is_reachable_over_a_unix_socket(self, tmp_path):
        sock = str(tmp_path / "hub.sock")
        process = _start_hub("shim-uds", sock)
        connection = shim.HubConnection(_config(name="shim-uds"))
        try:
            endpoint = await asyncio.to_thread(_wait_for_endpoint, "shim-uds", process)
            assert endpoint.transport == "unix"
            assert endpoint.socket == sock

            await connection.start()
            assert connection.connected is True

            result = await connection.call_tool("add", {"a": 4, "b": 1})
            assert "5" in _text_of(result)
        finally:
            await connection.close()
            _terminate(process)
