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
from pathlib import Path
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


class _FakeSession:
    """A session whose ``tools/list`` returns a fixed list."""

    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)


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


class TestErrorText:
    """A diagnostic names the cause, not only the wrapper."""

    class _Group(Exception):
        def __init__(self, children: list[BaseException]) -> None:
            super().__init__("unhandled errors in a TaskGroup")
            self.exceptions = children

    def test_a_nested_exception_text_names_the_cause(self):
        text = shim._describe_exception(
            self._Group([ConnectionResetError("peer closed")])
        )

        assert "TaskGroup" in text
        assert "peer closed" in text

    def test_a_plain_exception_text_is_unchanged(self):
        assert shim._describe_exception(ValueError("boom")) == "boom"


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

    @staticmethod
    def Timeout(connect: float, *, read: float) -> SimpleNamespace:
        return SimpleNamespace(connect=connect, read=read)

    def AsyncHTTPTransport(self, *, uds: str) -> SimpleNamespace:
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
        assert client.timeout.read == 300.0

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
            def streamablehttp_client(url, headers=None, httpx_client_factory=None):
                return url, httpx_client_factory

        monkeypatch.setattr(shim, "_streamable_http_module", lambda: FakeModule)

        url, factory = shim.open_hub_transport("http://x/mcp", "/tmp/h.sock")
        assert url == "http://x/mcp"

        client = factory(headers={"x": "1"}, timeout=5.0, auth=None)
        assert client.transport.uds == "/tmp/h.sock"
        assert client.headers == {"x": "1"}
        assert client.timeout == 5.0
        assert "auth" in client.__dict__

    def test_new_style_client_sends_the_hub_credential(self, monkeypatch):
        http = _FakeHttp()
        calls = []

        class FakeModule:
            httpx = http

            @staticmethod
            def streamable_http_client(url, http_client=None):
                calls.append((url, http_client))
                return "new-transport"

        monkeypatch.setattr(shim, "_streamable_http_module", lambda: FakeModule)

        assert (
            shim.open_hub_transport("http://x/mcp", None, "trk_hub") == "new-transport"
        )
        assert calls[0][1].headers == {"Authorization": "Bearer trk_hub"}

    def test_old_style_client_forwards_the_hub_credential(self, monkeypatch):
        http = _FakeHttp()
        captured = {}

        class FakeModule:
            httpx = http

            @staticmethod
            def streamablehttp_client(url, headers=None, httpx_client_factory=None):
                captured["headers"] = headers
                return "old-transport"

        monkeypatch.setattr(shim, "_streamable_http_module", lambda: FakeModule)

        assert (
            shim.open_hub_transport("http://x/mcp", None, "trk_hub") == "old-transport"
        )
        assert captured["headers"] == {"Authorization": "Bearer trk_hub"}

    def test_an_sdk_without_a_streamable_client_is_refused(self, monkeypatch):
        class FakeModule:
            pass

        monkeypatch.setattr(shim, "_streamable_http_module", lambda: FakeModule)

        with pytest.raises(shim.ShimError, match="streamable-HTTP"):
            shim.open_hub_transport("http://x/mcp")

    def test_the_http_module_falls_back_to_httpx(self):
        import httpx

        assert shim._sdk_http_module(SimpleNamespace()) is httpx


class TestShimCredential:
    """The key the shim presents comes from the config, never a header."""

    def test_an_explicit_key_wins(self):
        assert _config(key="trk_inline").credential() == "trk_inline"

    def test_a_key_file_is_read_and_stripped(self, tmp_path):
        path = tmp_path / "hub.key"
        path.write_text("trk_file\n", encoding="utf-8")
        assert _config(key_file=str(path)).credential() == "trk_file"

    def test_a_missing_key_file_is_a_config_error(self, tmp_path):
        with pytest.raises(shim.ShimError, match="key file"):
            _config(key_file=str(tmp_path / "missing")).credential()

    def test_an_empty_key_file_is_a_config_error(self, tmp_path):
        path = tmp_path / "hub.key"
        path.write_text("\n", encoding="utf-8")
        with pytest.raises(shim.ShimError, match="empty"):
            _config(key_file=str(path)).credential()

    def test_without_a_key_nothing_is_sent(self):
        assert _config().credential() is None


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

    async def test_a_call_without_a_hub_is_an_error_result(self):
        """The error reaches the client as a result, not as a crash."""
        sessions: weakref.WeakSet = weakref.WeakSet()
        connection = shim.HubConnection(_config())
        server = shim.build_server(connection, sessions)
        try:
            async with create_test_client(server) as client:
                try:
                    result = await client.call_tool("add", {"a": 1, "b": 2})
                except Exception as exc:  # a client may raise instead
                    # A raising client must still carry our code, not a
                    # generic protocol error.
                    assert _get_mcp_error_code(exc) == shim.HUB_ERROR_CODE
                    assert "unavailable" in str(exc)
                else:
                    assert result.is_error is True
                    assert "unavailable" in "".join(
                        block.text for block in result.content
                    )
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

    async def test_a_list_served_while_down_is_announced_after_connect(
        self, monkeypatch
    ):
        """With no --config or --no-spawn, the first list must still be told."""
        connection = shim.HubConnection(_config())
        notified = asyncio.Event()

        async def notifier() -> None:
            notified.set()

        connection.set_notifier(notifier)
        assert await connection.list_tools() == []
        assert connection._served == shim.tool_fingerprint([])

        async def open_one(_endpoint):
            return (
                contextlib.AsyncExitStack(),
                _FakeSession([_tool()]),
                [_tool()],
            )

        monkeypatch.setattr(connection, "_open", open_one)
        try:
            assert await connection._connect(_tcp_endpoint()) is True
            await asyncio.sleep(0.05)
            assert notified.is_set()
        finally:
            await connection._drop()

    async def test_a_changed_tool_list_notifies_the_client(self, monkeypatch):
        connection = shim.HubConnection(_config())
        notified = asyncio.Event()

        async def notifier() -> None:
            notified.set()

        connection.set_notifier(notifier)
        endpoint = _tcp_endpoint()

        async def open_first(_endpoint):
            return (
                contextlib.AsyncExitStack(),
                _FakeSession([_tool()]),
                [_tool()],
            )

        monkeypatch.setattr(connection, "_open", open_first)
        assert await connection._connect(endpoint) is True
        assert len(await connection.list_tools()) == 1
        await connection._drop()

        async def open_changed(_endpoint):
            changed = _tool(description="Add integers")
            return contextlib.AsyncExitStack(), _FakeSession([changed]), [changed]

        monkeypatch.setattr(connection, "_open", open_changed)
        assert await connection._connect(endpoint) is True
        await asyncio.sleep(0.05)
        assert notified.is_set()

        await connection._drop()

    async def test_an_identical_tool_list_stays_quiet(self, monkeypatch):
        connection = shim.HubConnection(_config())
        notifications: list[bool] = []

        async def notifier() -> None:
            notifications.append(True)

        connection.set_notifier(notifier)
        endpoint = _tcp_endpoint()

        async def open_same(_endpoint):
            return (
                contextlib.AsyncExitStack(),
                _FakeSession([_tool()]),
                [_tool()],
            )

        monkeypatch.setattr(connection, "_open", open_same)
        assert await connection._connect(endpoint) is True
        assert len(await connection.list_tools()) == 1
        await connection._drop()
        assert await connection._connect(endpoint) is True
        await asyncio.sleep(0.05)

        assert notifications == []
        await connection._drop()

    async def test_the_same_list_after_an_outage_is_announced(self, monkeypatch):
        """A hub that returns with the same tools must still be announced.

        The client was served ``[]`` during the outage, so comparing against
        the previous connection would stay silent.
        """
        connection = shim.HubConnection(_config())
        notifications: list[bool] = []

        async def notifier() -> None:
            notifications.append(True)

        connection.set_notifier(notifier)
        endpoint = _tcp_endpoint()

        async def open_same(_endpoint):
            return (
                contextlib.AsyncExitStack(),
                _FakeSession([_tool()]),
                [_tool()],
            )

        monkeypatch.setattr(connection, "_open", open_same)
        assert await connection._connect(endpoint) is True
        assert len(await connection.list_tools()) == 1
        await connection._drop()

        # The outage: the client lists and is served the empty answer.
        assert await connection.list_tools() == []

        assert await connection._connect(endpoint) is True
        await asyncio.sleep(0.05)
        assert notifications == [True]

        # The announced fingerprint is now the served one: an unchanged
        # reconnect stays quiet until the client lists again.
        await connection._drop()
        assert await connection._connect(endpoint) is True
        await asyncio.sleep(0.05)
        assert notifications == [True]

        await connection._drop()


class TestALostConnection:
    """A call in flight ends when the session owner does."""

    async def test_the_call_fails_when_the_owner_task_dies(self):
        connection = shim.HubConnection(_config())

        async def never_returns(_name, _arguments):
            await asyncio.sleep(3600)

        session = SimpleNamespace(call_tool=never_returns)
        stop = asyncio.Event()
        connection._owner = asyncio.get_running_loop().create_task(stop.wait())
        try:
            stop.set()
            with pytest.raises(shim.ShimError, match="closed"):
                await connection._call(session, "add", {})
        finally:
            await connection._drop()

    async def test_a_call_without_an_owner_is_awaited(self):
        connection = shim.HubConnection(_config())

        async def returns(name, arguments):
            return ("done", name, arguments)

        session = SimpleNamespace(call_tool=returns)
        result = await connection._call(session, "add", {"a": 1})

        assert result == ("done", "add", {"a": 1})


_HUB_SCRIPT = """
import os
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
    "keys_db": os.path.join(os.environ["TOOLREGISTRY_HOME"], "keys.db"),
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


def _create_hub_key(home) -> str:
    """Create the key a test shim presents to its hub."""
    from toolregistry_server.keystore import ApiKeyStore

    store = ApiKeyStore(Path(home) / "keys.db")
    try:
        created = store.create("shim-test", {"namespaces": ["*"]})
        return created.secret
    finally:
        store.close()


def _text_of(result) -> str:
    return "".join(block.text for block in result.content)


class TestShimAgainstARealHub:
    """A real gateway serves the shim; stopping it degrades cleanly."""

    async def test_a_stale_document_starts_a_hub_through_ensure(
        self, tmp_path, isolated_home
    ):
        """A document naming a dead address is "no hub"; the shim starts one."""
        config = tmp_path / "tools.yaml"
        config.write_text("tools: []\n", encoding="utf-8")
        write_endpoint(_tcp_endpoint(name="shim-spawn", port=_unused_port()))
        connection = shim.HubConnection(
            shim.ShimConfig(
                name="shim-spawn",
                config_path=str(config),
                spawn=True,
                keys_db=str(Path(isolated_home) / "keys.db"),
                key=_create_hub_key(isolated_home),
            )
        )
        try:
            assert await connection.list_tools() == []
            assert connection.connected is True
        finally:
            await connection.close()
            local_hub.stop_hub("shim-spawn", timeout=10)

    async def test_a_tool_call_goes_through_the_hub(self, isolated_home):
        process = _start_hub("shim-tcp")
        connection = shim.HubConnection(
            _config(name="shim-tcp", key=_create_hub_key(isolated_home))
        )
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

    async def test_stopping_the_hub_makes_calls_fail_once(self, isolated_home):
        process = _start_hub("shim-stop")
        connection = shim.HubConnection(
            _config(name="shim-stop", key=_create_hub_key(isolated_home))
        )
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
    async def test_the_hub_is_reachable_over_a_unix_socket(
        self, tmp_path, isolated_home
    ):
        sock = str(tmp_path / "hub.sock")
        process = _start_hub("shim-uds", sock)
        connection = shim.HubConnection(
            _config(name="shim-uds", key=_create_hub_key(isolated_home))
        )
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
