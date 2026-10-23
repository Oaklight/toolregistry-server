"""Tests for the httpserver-based OpenAPI app (epic #101, workstream 4).

The app is exercised over a real socket on an ephemeral port: the vendored
``httpserver`` has no in-process dispatch, and the point of this module is to
prove that the wire contract of the FastAPI app survives the port.
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
from toolregistry.utils import ToolResultJSONEncoder

from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def noop() -> str:
    """Do nothing."""
    return "ok"


@pytest.fixture
def route_table() -> RouteTable:
    registry = ToolRegistry()
    registry.register(add)
    registry.register(noop)
    table = RouteTable(registry)
    table.refresh_all()
    return table


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """``App.run`` installs signal handlers, which only work in the main thread."""
    loop_classes = [asyncio.BaseEventLoop]
    unix = getattr(asyncio, "unix_events", None)
    if unix is not None:
        loop_classes.append(unix._UnixSelectorEventLoop)  # noqa: SLF001 - test shim
    for loop_class in loop_classes:
        monkeypatch.setattr(loop_class, "add_signal_handler", lambda *a, **k: None)


def _serve(app) -> str:
    """Run *app* on an ephemeral port and return its base URL."""
    thread = threading.Thread(target=app.run, kwargs={"host": "127.0.0.1", "port": 0})
    thread.daemon = True
    thread.start()
    for _ in range(100):
        if getattr(app, "port", None):
            return f"http://127.0.0.1:{app.port}"
        time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture
def server(route_table):
    app = create_httpserver_app(route_table)
    url = _serve(app)
    yield url, app, route_table
    app.shutdown()


def _call(url: str, path: str, payload: object = None, headers: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url + path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _path(route_table: RouteTable) -> str:
    return route_table.get_route("add").path


class TestToolRoutes:
    def test_valid_call(self, server):
        url, _, table = server
        status, _, body = _call(url, _path(table), {"a": 2, "b": 3})
        assert status == 200
        assert json.loads(body) == 5

    def test_unknown_keys_are_dropped(self, server):
        url, _, table = server
        status, _, body = _call(url, _path(table), {"a": 1, "b": 2, "junk": True})
        assert status == 200
        assert json.loads(body) == 3

    def test_non_object_body_is_a_422(self, server):
        url, _, table = server
        for payload in ([], "str", 123):
            status, _, body = _call(url, _path(table), payload)
            assert status == 422, payload
            detail = json.loads(body)["detail"]
            assert detail[0]["loc"] == ["body"]
            assert detail[0]["type"] == "model_attributes_type"

    def test_malformed_json_is_a_422(self, server):
        url, _, table = server
        request = urllib.request.Request(
            url + _path(table),
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code == 422
        assert json.loads(excinfo.value.read())["detail"][0]["type"] == "json_invalid"

    def test_bad_argument_type_is_a_422(self, server):
        url, _, table = server
        status, _, body = _call(url, _path(table), {"a": "x", "b": 1})
        assert status == 422
        assert json.loads(body)["detail"][0]["loc"] == ["body", "a"]

    def test_disabled_tool_is_a_503(self, server):
        url, _, table = server
        table.disable("add", reason="test")
        status, _, body = _call(url, _path(table), {"a": 1, "b": 2})
        assert status == 503
        assert "disabled" in json.loads(body)["detail"]

    def test_tool_exception_is_a_500(self, route_table):
        def boom() -> str:
            """Raise."""
            raise RuntimeError("exploded")

        registry = ToolRegistry()
        registry.register(boom)
        table = RouteTable(registry)
        table.refresh_all()
        app = create_httpserver_app(table)
        url = _serve(app)
        status, _, body = _call(url, table.get_route("boom").path, {})
        assert status == 500
        assert "exploded" in json.loads(body)["detail"]


@dataclass
class Point:
    x: int
    y: int


def _serve_result(value):
    """Serve a tool that returns *value* and return ``(status, body bytes)``."""

    @dataclass
    class _Holder:
        value: object

    def tool() -> object:
        """Return a fixed value."""
        return value

    registry = ToolRegistry()
    registry.register(tool)
    table = RouteTable(registry)
    table.refresh_all()
    url = _serve(create_httpserver_app(table))
    status, _, body = _call(url, table.get_route("tool").path, {})
    return status, body


class TestResultEncoding:
    """Tool results must serialise exactly as the FastAPI JSON encoder did."""

    @pytest.mark.parametrize(
        "value",
        [
            Point(1, 2),
            [Point(1, 2)],
            {"p": Point(1, 2)},
            {1, 2},
            datetime(2026, 10, 23, 12, 0),
            None,
            42,
            "text",
            [1, "two", None],
        ],
        ids=[
            "dataclass",
            "list",
            "dict",
            "set",
            "datetime",
            "none",
            "int",
            "str",
            "list",
        ],
    )
    def test_result_matches_the_core_encoder(self, value):
        status, body = _serve_result(value)
        assert status == 200
        expected = json.dumps(value, cls=ToolResultJSONEncoder)
        assert json.loads(body) == json.loads(expected)

    def test_bytes_are_base64_encoded(self):
        """Core's deliberate choice: bytes become base64, not UTF-8 text."""
        status, body = _serve_result(b"hi")
        assert status == 200
        assert json.loads(body) == "aGk="


class TestMetaRoutes:
    def test_tools_listing_and_etag(self, server):
        url, _, _ = server
        status, headers, body = _call(url, "/tools")
        assert status == 200
        payload = json.loads(body)
        assert {tool["name"] for tool in payload["tools"]} == {"add", "noop"}
        etag = headers["ETag"]
        assert payload["etag"] == etag

        status, headers, body = _call(url, "/tools", headers={"If-None-Match": etag})
        assert status == 304
        assert body == b""

    def test_openapi_document(self, server):
        url, _, table = server
        status, headers, body = _call(url, "/openapi.json")
        assert status == 200
        assert headers["ETag"] == table.etag
        document = json.loads(body)
        assert _path(table) in document["paths"]
        assert document["info"]["title"] == "ToolRegistry Server"

    def test_openapi_document_supports_conditional_get(self, server):
        url, _, table = server
        status, headers, body = _call(url, "/openapi.json")
        assert status == 200
        status, headers, body = _call(
            url, "/openapi.json", headers={"If-None-Match": headers["ETag"]}
        )
        assert status == 304
        assert body == b""

    def test_etag_only_on_cacheable_gets(self, server):
        url, _, table = server
        status, headers, _ = _call(url, _path(table), {"a": 1, "b": 2})
        assert status == 200
        assert "ETag" not in headers
        status, headers, _ = _call(url, "/docs")
        assert status == 200
        assert "ETag" not in headers

    def test_unknown_path_uses_the_detail_envelope(self, server):
        url, _, _ = server
        status, _, body = _call(url, "/nope")
        assert status == 404
        assert json.loads(body) == {"detail": "Not Found"}

    def test_docs_serves_swagger(self, server):
        url, _, _ = server
        status, headers, body = _call(url, "/docs")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert b"swagger" in body.lower()


class TestAuth:
    def test_missing_token_is_a_401(self, route_table):
        app = create_httpserver_app(route_table, tokens=["secret"])
        url = _serve(app)
        status, headers, body = _call(url, _path(route_table), {"a": 1, "b": 2})
        assert status == 401
        assert json.loads(body)["detail"] == "Not authenticated"
        assert headers["WWW-Authenticate"] == "Bearer"

    def test_valid_token_is_accepted(self, route_table):
        app = create_httpserver_app(route_table, tokens=["secret"])
        url = _serve(app)
        status, _, body = _call(
            url,
            _path(route_table),
            {"a": 1, "b": 2},
            headers={"Authorization": "Bearer secret"},
        )
        assert status == 200
        assert json.loads(body) == 3


class TestEvents:
    def test_events_streams_a_change(self, server):
        url, _, table = server
        request = urllib.request.Request(url + "/events")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert "text/event-stream" in response.headers["Content-Type"]

            def emit():
                time.sleep(0.05)
                table.disable("noop", reason="test")

            threading.Thread(target=emit, daemon=True).start()
            line = response.readline()
            assert line.startswith(b"event: tool_change")
