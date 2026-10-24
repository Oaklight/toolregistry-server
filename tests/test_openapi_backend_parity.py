"""Differential tests between the FastAPI and httpserver OpenAPI backends.

Both apps are served on ephemeral ports over real sockets and the same
request matrix is sent to each, so a contract drift shows up as a diff and
not as a rewritten expectation. The FastAPI app is the reference until the
switchover lands (epic #101).

Known, deliberate divergence: ``bytes`` tool results. FastAPI decodes them as
UTF-8 text, core's encoder base64-encodes them; the httpserver backend keeps
core's behaviour.
"""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import create_openapi_app
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app

TOKENS = ["secret"]


@dataclass
class Point:
    x: int
    y: int


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def greet(name: str = "world") -> str:
    """Greet someone."""
    return f"hello {name}"


def point() -> Point:
    """Return a dataclass."""
    return Point(1, 2)


def points() -> list[Point]:
    """Return a list of dataclasses."""
    return [Point(1, 2), Point(3, 4)]


def moment() -> datetime:
    """Return a datetime."""
    return datetime(2026, 10, 23, 12, 0)


def boom() -> str:
    """Always raise."""
    raise RuntimeError("exploded")


def build_table() -> RouteTable:
    registry = ToolRegistry()
    for tool in (add, greet, point, points, moment, boom):
        registry.register(tool)
    table = RouteTable(registry)
    table.refresh_all()
    return table


def _wait_for_port(get_port, timeout: float = 5.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = get_port()
        if port:
            return port
        time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture(scope="module", autouse=True)
def _no_signal_handlers():
    """``httpserver.App.run`` installs signal handlers, which need the main thread.

    Patched by hand rather than with ``monkeypatch`` because the server
    fixtures are module-scoped and ``monkeypatch`` is function-scoped.
    """
    loop_classes = [asyncio.BaseEventLoop]
    unix = getattr(asyncio, "unix_events", None)
    if unix is not None:
        loop_classes.append(unix._UnixSelectorEventLoop)  # noqa: SLF001 - test shim
    originals = [(cls, cls.add_signal_handler) for cls in loop_classes]
    for cls, _ in originals:
        cls.add_signal_handler = lambda *a, **k: None
    yield
    for cls, original in originals:
        cls.add_signal_handler = original


@pytest.fixture(scope="module")
def table() -> RouteTable:
    return build_table()


@pytest.fixture(scope="module")
def fastapi_base(table):
    import socket

    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    app = create_openapi_app(table)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(scope="module")
def httpserver_base(table):
    app = create_httpserver_app(table)
    thread = threading.Thread(
        target=app.run, kwargs={"host": "127.0.0.1", "port": 0}, daemon=True
    )
    thread.start()
    port = _wait_for_port(lambda: app.port)
    yield f"http://127.0.0.1:{port}"
    app.shutdown()


@pytest.fixture(scope="module")
def both(fastapi_base, httpserver_base):
    return fastapi_base, httpserver_base


def request(base: str, path: str, payload=..., headers=None):
    """Return ``(status, headers, body)`` for a GET or POST."""
    if payload is ...:
        req = urllib.request.Request(base + path, headers=headers or {})
    else:
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, _lower(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, _lower(error.headers), error.read()


def _lower(headers) -> dict:
    """Header names are case-insensitive; uvicorn sends them lowercase."""
    return {key.lower(): value for key, value in headers.items()}


def compare(both, path, payload=..., headers=None, compare_body=True):
    """Send the same request to both backends and assert they agree."""
    fastapi_status, fastapi_headers, fastapi_body = request(
        both[0], path, payload, headers
    )
    http_status, http_headers, http_body = request(both[1], path, payload, headers)
    assert (fastapi_status, fastapi_headers.get("etag")) == (
        http_status,
        http_headers.get("etag"),
    ), f"{path}: status/etag differ"
    if compare_body:
        assert fastapi_body == http_body, f"{path}: body differs"
    return fastapi_status, fastapi_body


class TestToolRoutes:
    def test_valid_call(self, both, table):
        _, body = compare(both, table.get_route("add").path, {"a": 2, "b": 3})
        assert json.loads(body) == 5

    def test_default_argument(self, both, table):
        compare(both, table.get_route("greet").path, {})

    def test_unknown_keys_are_dropped(self, both, table):
        compare(both, table.get_route("add").path, {"a": 1, "b": 2, "junk": True})

    def test_missing_required_field(self, both, table):
        status, _ = compare(both, table.get_route("add").path, {"a": 1})
        assert status == 422

    @pytest.mark.parametrize("payload", [[], "text", 123, True], ids=str)
    def test_non_object_body(self, both, table, payload):
        status, _ = compare(both, table.get_route("add").path, payload)
        assert status == 422

    def test_bad_argument_type(self, both, table):
        status, _ = compare(both, table.get_route("add").path, {"a": "x", "b": 1})
        assert status == 422

    def test_tool_exception(self, both, table):
        status, body = compare(both, table.get_route("boom").path, {})
        assert status == 500
        assert "exploded" in json.loads(body)["detail"]

    def test_unknown_path(self, both):
        status, body = compare(both, "/nope")
        assert status == 404
        assert json.loads(body) == {"detail": "Not Found"}

    def test_wrong_method(self, both):
        status, body = compare(both, "/tools", {"unexpected": True})
        assert status == 405
        assert json.loads(body) == {"detail": "Method Not Allowed"}


class TestResultShapes:
    @pytest.mark.parametrize("tool", ["point", "points", "moment"])
    def test_results_match(self, both, table, tool):
        compare(both, table.get_route(tool).path, {})


class TestMetaRoutes:
    def test_tools_listing(self, both):
        status, body = compare(both, "/tools")
        assert status == 200
        assert {tool["name"] for tool in json.loads(body)["tools"]} >= {"add", "greet"}

    def test_tools_conditional_get(self, both):
        status, headers, _ = request(both[0], "/tools")
        etag = headers["etag"]
        compare(both, "/tools", headers={"If-None-Match": etag})

    def test_openapi_document(self, both):
        compare(both, "/openapi.json")

    def test_openapi_conditional_get(self, both):
        _, headers, _ = request(both[0], "/openapi.json")
        etag = headers["etag"]
        assert etag
        compare(both, "/openapi.json", headers={"If-None-Match": etag})

    def test_docs_page(self, both):
        status, _ = compare(both, "/docs", compare_body=False)
        assert status == 200
        _, headers, _ = request(both[1], "/docs")
        assert "text/html" in headers["content-type"]


class TestDisabledTool:
    def test_disabled_tool_is_a_503(self, both, table):
        route = table.get_route("greet")
        table.disable("greet", reason="parity test")
        try:
            status, body = compare(both, route.path, {})
            assert status == 503
            assert "disabled" in json.loads(body)["detail"]
        finally:
            table.enable("greet")


class TestAuth:
    @pytest.fixture(scope="class")
    def secured(self, table):
        import socket

        import uvicorn
        from fastapi import Depends

        from toolregistry_server.auth import BearerTokenAuth, create_bearer_dependency

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        auth = BearerTokenAuth(tokens=TOKENS)
        fastapi_app = create_openapi_app(
            table, dependencies=[Depends(create_bearer_dependency(auth))]
        )
        server = uvicorn.Server(
            uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="error")
        )
        threading.Thread(target=server.run, daemon=True).start()
        deadline = time.time() + 10
        while time.time() < deadline and not server.started:
            time.sleep(0.05)

        http_app = create_httpserver_app(table, tokens=TOKENS)
        threading.Thread(
            target=http_app.run, kwargs={"host": "127.0.0.1", "port": 0}, daemon=True
        ).start()
        http_port = _wait_for_port(lambda: http_app.port)

        yield f"http://127.0.0.1:{port}", f"http://127.0.0.1:{http_port}"
        server.should_exit = True
        http_app.shutdown()

    def test_missing_token(self, secured):
        fastapi_status, _, fastapi_body = request(secured[0], "/tools")
        http_status, http_headers, http_body = request(secured[1], "/tools")
        assert fastapi_status == http_status == 401
        assert json.loads(fastapi_body) == json.loads(http_body)
        assert http_headers["www-authenticate"] == "Bearer"

    def test_wrong_token(self, secured):
        fastapi_status, _, fastapi_body = request(
            secured[0], "/tools", headers={"Authorization": "Bearer nope"}
        )
        http_status, _, http_body = request(
            secured[1], "/tools", headers={"Authorization": "Bearer nope"}
        )
        assert fastapi_status == http_status == 401
        assert json.loads(fastapi_body) == json.loads(http_body)

    def test_valid_token(self, secured):
        for base in secured:
            status, _, _ = request(
                base, "/tools", headers={"Authorization": "Bearer secret"}
            )
            assert status == 200
