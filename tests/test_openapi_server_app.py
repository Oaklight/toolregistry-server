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
from io import StringIO

import pytest
from toolregistry import ToolRegistry
from toolregistry.utils import ToolResultJSONEncoder

from toolregistry_server import RouteTable
from toolregistry_server._vendor.structlog import (
    BoundLogger,
    ConsoleRenderer,
    PrintLogger,
    TimeStamper,
    add_log_level,
)
from toolregistry_server.adapters.openapi.server_app import (
    REDOC_BUNDLE_INTEGRITY,
    REDOC_VERSION,
    SWAGGER_UI_BUNDLE_INTEGRITY,
    SWAGGER_UI_CSS_INTEGRITY,
    SWAGGER_UI_VERSION,
    create_httpserver_app,
)


def _capture_logs(monkeypatch, module):
    """Route *module*'s logger into a StringIO and return the stream."""
    stream = StringIO()
    log = BoundLogger(
        logger=PrintLogger(file=stream),
        processors=[add_log_level, TimeStamper(), ConsoleRenderer(colors=False)],
        context={},
    )
    monkeypatch.setattr(module, "logger", log)
    return stream


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def noop() -> str:
    """Do nothing."""
    return "ok"


def echo_len(text: str) -> int:
    """Return the length of *text*."""
    return len(text)


@pytest.fixture
def route_table() -> RouteTable:
    registry = ToolRegistry()
    registry.register(add)
    registry.register(noop)
    table = RouteTable(registry)
    table.refresh_all()
    return table


@pytest.fixture
def body_limit_table() -> RouteTable:
    """A table with one tool that accepts an arbitrarily long string."""
    registry = ToolRegistry()
    registry.register(echo_len)
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


def _call(
    url: str,
    path: str,
    payload: object = None,
    headers: dict | None = None,
    timeout: float = 5,
):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url + path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
        detail = json.loads(body)["detail"]
        assert detail == "Tool 'boom' execution failed"
        assert "exploded" not in detail

    def test_tool_exception_traceback_is_logged(self, monkeypatch):
        """The server log keeps the traceback the client must not receive."""
        import toolregistry_server.adapters.openapi.server_app as server_app

        stream = _capture_logs(monkeypatch, server_app)

        def boom() -> str:
            """Raise."""
            raise RuntimeError("exploded")

        registry = ToolRegistry()
        registry.register(boom)
        table = RouteTable(registry)
        table.refresh_all()
        url = _serve(create_httpserver_app(table))

        status, _, _ = _call(url, table.get_route("boom").path, {})
        assert status == 500

        output = stream.getvalue()
        assert "Traceback (most recent call last)" in output
        assert "RuntimeError: exploded" in output

    def test_unserialisable_result_traceback_is_logged(self, monkeypatch):
        """A result JSON cannot encode still leaves a traceback in the log."""
        import toolregistry_server.adapters.openapi.server_app as server_app

        stream = _capture_logs(monkeypatch, server_app)

        def returns_a_function() -> object:
            """Return a value with no JSON projection."""
            return lambda: None

        registry = ToolRegistry()
        registry.register(returns_a_function)
        table = RouteTable(registry)
        table.refresh_all()
        url = _serve(create_httpserver_app(table))

        status, _, body = _call(url, table.get_route("returns_a_function").path, {})
        assert status == 500
        assert "not serialisable" in json.loads(body)["detail"]

        output = stream.getvalue()
        assert "Traceback (most recent call last)" in output
        assert "TypeError" in output

    def test_validation_runs_off_the_event_loop(self, route_table, monkeypatch):
        """Other requests are served while validation is still running."""
        import toolregistry_server.adapters.openapi.server_app as server_app

        entered = threading.Event()
        release = threading.Event()

        def blocking_validate(route, payload, **_kwargs):
            entered.set()
            assert release.wait(5)
            return payload

        monkeypatch.setattr(server_app, "_validate_arguments", blocking_validate)
        app = create_httpserver_app(route_table)
        url = _serve(app)
        responses: list[tuple[int, dict, bytes]] = []
        try:
            worker = threading.Thread(
                target=lambda: responses.append(
                    _call(url, _path(route_table), {"a": 1, "b": 2})
                )
            )
            worker.start()
            assert entered.wait(5)

            # If validation still ran on the loop, this request could not be
            # served until ``release`` was set below and would time out.
            status, _, body = _call(url, "/tools", timeout=1)
            assert status == 200
            assert "tools" in json.loads(body)

            release.set()
            worker.join(timeout=5)
            assert not worker.is_alive()
            assert responses[0][0] == 200
        finally:
            release.set()
            app.shutdown()

    def test_validation_http_exception_stays_a_422(self, route_table, monkeypatch):
        """A 422 raised in the worker thread keeps its detail envelope."""
        from fastapi import HTTPException

        import toolregistry_server.adapters.openapi.server_app as server_app

        def rejecting_validate(route, payload, **_kwargs):
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "loc": ["body", "a"],
                        "msg": "rejected in the worker thread",
                        "type": "value_error",
                    }
                ],
            )

        monkeypatch.setattr(server_app, "_validate_arguments", rejecting_validate)
        app = create_httpserver_app(route_table)
        url = _serve(app)
        try:
            status, _, body = _call(url, _path(route_table), {"a": 1, "b": 2})
        finally:
            app.shutdown()

        assert status == 422
        assert json.loads(body)["detail"][0]["msg"] == "rejected in the worker thread"


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

    def test_body_is_compact_like_fastapi(self):
        """Byte-level parity: FastAPI's JSONResponse uses compact separators."""
        status, body = _serve_result(Point(1, 2))
        assert status == 200
        assert body == b'{"x":1,"y":2}'

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

    def test_redoc_serves_the_reference_page(self, server):
        url, _, _ = server
        status, headers, body = _call(url, "/redoc")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        page = body.decode()
        assert 'spec-url="/openapi.json"' in page
        assert f"redoc@{REDOC_VERSION}/" in page
        assert REDOC_BUNDLE_INTEGRITY in page
        assert 'crossorigin="anonymous"' in page

    def test_docs_pins_swagger_ui_with_integrity(self, server):
        """The CDN assets are an exact release guarded by SRI (#149)."""
        url, _, _ = server
        _, _, body = _call(url, "/docs")
        page = body.decode()
        assert f"swagger-ui-dist@{SWAGGER_UI_VERSION}/" in page
        assert SWAGGER_UI_CSS_INTEGRITY in page
        assert SWAGGER_UI_BUNDLE_INTEGRITY in page
        assert page.count('integrity="sha384-') == 2
        assert page.count('crossorigin="anonymous"') == 2

    def test_docs_escapes_the_configured_title(self, route_table):
        """An operator-supplied title cannot inject markup (#149)."""
        title = "<script>alert(1)</script>"
        app = create_httpserver_app(route_table, title=title)
        url = _serve(app)
        try:
            _, _, body = _call(url, "/docs")
        finally:
            app.shutdown()
        assert title.encode() not in body
        assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in body

    def test_redoc_escapes_the_configured_title(self, route_table):
        """The ReDoc page takes the same title through the same escape."""
        title = "<script>alert(1)</script>"
        app = create_httpserver_app(route_table, title=title)
        url = _serve(app)
        try:
            _, _, body = _call(url, "/redoc")
        finally:
            app.shutdown()
        assert title.encode() not in body
        assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in body


class TestDynamicRoutes:
    def test_a_tool_registered_after_startup_is_callable(self, server):
        url, _, table = server
        table.registry.register(echo_len)

        path = table.get_route("echo_len").path
        status, _, body = _call(url, path, {"text": "abc"})
        assert status == 200
        assert json.loads(body) == 3

    def test_a_removed_tool_keeps_its_route_as_a_503(self, server):
        url, _, table = server
        table.registry.register(echo_len)
        path = table.get_route("echo_len").path
        assert _call(url, path, {"text": "abc"})[0] == 200

        table.registry._unregister("echo_len")  # noqa: SLF001 - test fixture
        assert table.get_route("echo_len") is None
        status, _, body = _call(url, path, {"text": "abc"})
        assert status == 503
        assert "disabled" in json.loads(body)["detail"]


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


class TestEventsLimit:
    """The ``/events`` subscriber set is bounded (#149)."""

    def test_extra_subscribers_are_rejected(self, route_table):
        app = create_httpserver_app(route_table, max_sse_subscribers=1)
        url = _serve(app)
        try:
            with urllib.request.urlopen(url + "/events", timeout=5) as response:
                assert "text/event-stream" in response.headers["Content-Type"]
                status, _, body = _call(url, "/events")
            assert status == 503
            assert json.loads(body)["detail"] == "Too many event subscribers"
        finally:
            app.shutdown()

    def test_zero_disables_the_endpoint(self, route_table):
        app = create_httpserver_app(route_table, max_sse_subscribers=0)
        url = _serve(app)
        try:
            status, _, body = _call(url, "/events")
        finally:
            app.shutdown()
        assert status == 503
        assert json.loads(body)["detail"] == "Too many event subscribers"

    def test_negative_limit_is_rejected(self, route_table):
        with pytest.raises(ValueError, match="max_sse_subscribers"):
            create_httpserver_app(route_table, max_sse_subscribers=-1)


class TestBodyLimit:
    """The request-body cap is configurable and fails as a JSON 413 (#134)."""

    @staticmethod
    def _call_with_limit(table: RouteTable, payload: object, max_body_size: int):
        app = create_httpserver_app(table, max_body_size=max_body_size)
        url = _serve(app)
        try:
            return _call(url, table.get_route("echo_len").path, payload)
        finally:
            app.shutdown()

    def test_default_limit_is_one_mib(self, route_table):
        assert create_httpserver_app(route_table).max_body_size == 1_048_576

    def test_body_under_the_limit_is_served(self, body_limit_table):
        status, _, body = self._call_with_limit(
            body_limit_table, {"text": "x" * 100}, max_body_size=1024
        )
        assert status == 200
        assert json.loads(body) == 100

    def test_body_over_the_limit_is_a_413(self, body_limit_table):
        status, headers, body = self._call_with_limit(
            body_limit_table, {"text": "x" * 2000}, max_body_size=1024
        )
        assert status == 413
        assert headers["Content-Type"].startswith("application/json")
        assert "too large" in json.loads(body)["detail"].lower()
        assert b"Traceback" not in body

    def test_raising_the_limit_accepts_the_same_body(self, body_limit_table):
        payload = {"text": "x" * 2000}
        status, _, body = self._call_with_limit(
            body_limit_table, payload, max_body_size=8192
        )
        assert status == 200
        assert json.loads(body) == 2000

    def test_non_positive_limit_is_rejected(self, route_table):
        with pytest.raises(ValueError, match="max_body_size"):
            create_httpserver_app(route_table, max_body_size=0)

    def test_eight_megabytes_is_a_413_not_a_reset(self, body_limit_table):
        """The 0.7.2 drain turns the old connection reset into a 413 (#134)."""
        app = create_httpserver_app(body_limit_table)
        url = _serve(app)
        try:
            status, _, body = _call(
                url,
                body_limit_table.get_route("echo_len").path,
                {"text": "x" * 8_000_000},
                timeout=30,
            )
        finally:
            app.shutdown()
        assert status == 413
        assert b"Request body too large" in body
        assert b"Traceback" not in body

    def test_transport_and_framework_errors_share_the_envelope(self, body_limit_table):
        """413, 404 and 405 all carry the same top-level key (#146)."""
        app = create_httpserver_app(body_limit_table, max_body_size=1024)
        url = _serve(app)
        try:
            status, _, body = _call(
                url,
                body_limit_table.get_route("echo_len").path,
                {"text": "x" * 2000},
            )
            assert status == 413
            assert set(json.loads(body)) == {"detail"}

            status, _, body = _call(url, "/nope")
            assert status == 404
            assert set(json.loads(body)) == {"detail"}

            # GET on a POST-only tool route.
            status, _, body = _call(url, body_limit_table.get_route("echo_len").path)
            assert status == 405
            assert set(json.loads(body)) == {"detail"}
        finally:
            app.shutdown()


class TestShutdown:
    def test_shutdown_drains_an_in_flight_request(self, route_table):
        """SIGTERM-equivalent shutdown completes the request already running."""
        started = threading.Event()
        release = threading.Event()

        def hold() -> str:
            """Block until the test releases it."""
            started.set()
            release.wait(timeout=10)
            return "done"

        route_table.registry.register(hold)
        route_table.refresh_all()

        app = create_httpserver_app(route_table)
        url = _serve(app)
        path = route_table.get_route("hold").path
        response: dict[str, object] = {}

        def call() -> None:
            response["result"] = _call(url, path, {})

        client = threading.Thread(target=call, daemon=True)
        client.start()
        assert started.wait(timeout=5), "the handler never started"
        threading.Timer(0.2, release.set).start()

        app.shutdown()
        client.join(timeout=5)

        assert not client.is_alive()
        status, _, body = response["result"]  # type: ignore[misc]
        assert status == 200
        assert json.loads(body) == "done"

        # Shutdown is signalled from another thread, so give the loop a
        # bounded window to close the listener before asserting it is gone.
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                _call(url, "/tools")
            except (urllib.error.URLError, ConnectionError):
                break
            time.sleep(0.05)
        else:
            pytest.fail("the listener stayed open after shutdown")
