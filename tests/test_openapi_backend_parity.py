"""Differential tests between the FastAPI and httpserver OpenAPI backends.

Both apps are served on ephemeral ports over real sockets and the same
request matrix is sent to each, so a contract drift shows up as a diff and
not as a rewritten expectation. The FastAPI app is the reference until the
switchover lands (epic #101); both sides encode tool results through
``adapters.openapi.serialisation``, which uses core's
``ToolResultJSONEncoder``, so non-finite floats and Decimals, ``bytes`` and
sets reach the wire identically.
"""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import create_openapi_app
from toolregistry_server.adapters.openapi.serialisation import encode_tool_result
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app

TOKENS = ["secret"]


@dataclass
class Point:
    x: int
    y: int


@dataclass
class Metric:
    """A dataclass whose Decimal field can be non-finite."""

    label: str
    value: Decimal


class Reading(BaseModel):
    """A Pydantic model whose float field can be non-finite.

    The field is ``float`` because pydantic 2.14 rejects a non-finite
    ``Decimal`` at validation time; a model must accept the value before the
    encoder can be asked to project it.
    """

    label: str
    value: float


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


def nan_dict() -> dict:
    """Return a mapping with a NaN value."""
    return {"x": float("nan")}


def inf_value() -> float:
    """Return a positive infinity."""
    return float("inf")


def nan_in_list() -> dict:
    """Return a nested list with a NaN value."""
    return {"a": [1.0, float("nan")]}


def decimal_nan() -> Decimal:
    """Return a non-finite Decimal."""
    return Decimal("NaN")


def decimal_finite() -> Decimal:
    """Return a finite Decimal, which both backends encode as a string."""
    return Decimal("1.5")


def decimal_neg_inf() -> Decimal:
    """Return a negative infinity Decimal."""
    return Decimal("-Infinity")


def dataclass_neg_inf() -> Metric:
    """Return a dataclass carrying a negative-infinity Decimal."""
    return Metric("temp", Decimal("-Infinity"))


def model_nan() -> Reading:
    """Return a Pydantic model carrying a NaN float."""
    return Reading(label="temp", value=float("nan"))


def blob() -> bytes:
    """Return bytes, which core encodes as base64."""
    return b"hello"


def unordered() -> set:
    """Return a set, which core orders by ``repr``."""
    return {"b", "a"}


def boom() -> str:
    """Always raise."""
    raise RuntimeError("exploded")


def unserialisable():
    """Return a value neither encoder can project to JSON."""
    return object()


def _reject_json_constant(value: str) -> None:
    """Fail when ``json.loads`` meets a non-standard constant."""
    raise AssertionError(f"non-standard JSON constant: {value}")


def build_table() -> RouteTable:
    registry = ToolRegistry()
    for tool in (
        add,
        greet,
        point,
        points,
        moment,
        nan_dict,
        inf_value,
        nan_in_list,
        decimal_nan,
        decimal_finite,
        decimal_neg_inf,
        dataclass_neg_inf,
        model_nan,
        blob,
        unordered,
        boom,
        unserialisable,
    ):
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
        detail = json.loads(body)["detail"]
        assert detail == "Tool 'boom' execution failed"
        assert "exploded" not in detail

    def test_unserialisable_result(self, both, table):
        """Both backends answer with the same JSON envelope and stable reason."""
        status, body = compare(both, table.get_route("unserialisable").path, {})
        assert status == 500
        assert json.loads(body) == {
            "detail": "Tool 'unserialisable' result is not serialisable"
        }

    @pytest.mark.parametrize(
        ("tool_name", "expected"),
        [
            ("nan_dict", {"x": None}),
            ("inf_value", None),
            ("nan_in_list", {"a": [1.0, None]}),
            ("decimal_nan", None),
            ("decimal_finite", "1.5"),
            ("decimal_neg_inf", None),
            ("dataclass_neg_inf", {"label": "temp", "value": None}),
            ("model_nan", {"label": "temp", "value": None}),
        ],
    )
    def test_non_finite_results_are_strict_json(self, both, table, tool_name, expected):
        """Both backends project non-finite values to null (#130, #135).

        ``jsonable_encoder`` is not the reference here: it hands a float NaN
        to ``json`` unchanged and keeps a non-finite ``Decimal`` as a value.
        Both backends go through ``encode_tool_result``, which uses core's
        ``ToolResultJSONEncoder``, so this holds on the default matrix and on
        core master.
        """
        status, body = compare(both, table.get_route(tool_name).path, {})
        assert status == 200
        text = body.decode("utf-8")
        assert "NaN" not in text
        assert "Infinity" not in text
        assert json.loads(text, parse_constant=_reject_json_constant) == expected

    @pytest.mark.parametrize(
        ("tool_name", "expected"),
        [("blob", "aGVsbG8="), ("unordered", ["a", "b"])],
    )
    def test_core_projections_agree(self, both, table, tool_name, expected):
        """``bytes`` and sets use core's projection on both backends."""
        status, body = compare(both, table.get_route(tool_name).path, {})
        assert status == 200
        assert json.loads(body) == expected

    def test_tool_response_content_type_matches(self, both, table):
        """Both backends send the same Content-Type for tool results."""
        path = table.get_route("add").path
        fastapi = request(both[0], path, {"a": 1, "b": 2})
        httpserver = request(both[1], path, {"a": 1, "b": 2})
        assert fastapi[1]["content-type"] == httpserver[1]["content-type"]

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


class TestToolResultEncoding:
    """The shared encoder keeps the wire contract strict."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ({"x": float("nan")}, {"x": None}),
            (float("inf"), None),
            ([1.0, float("-inf")], [1.0, None]),
            (Decimal("NaN"), None),
            (Decimal("sNaN"), None),
            (Decimal("-Infinity"), None),
            (Decimal("1.5"), "1.5"),
            (b"hello", "aGVsbG8="),
            ({"b", "a"}, ["a", "b"]),
        ],
    )
    def test_non_finite_and_core_projections(self, value, expected):
        """Non-finite values become ``null``; other projections are unchanged."""
        text = encode_tool_result(value)
        assert "NaN" not in text
        assert "Infinity" not in text
        assert json.loads(text, parse_constant=_reject_json_constant) == expected

    def test_nested_containers_are_walked(self):
        """A non-finite value inside a nested container is projected."""
        text = encode_tool_result(
            {
                "a": [float("nan"), {"b": Decimal("Infinity")}],
                "c": (float("-inf"),),
            }
        )
        assert json.loads(text, parse_constant=_reject_json_constant) == {
            "a": [None, {"b": None}],
            "c": [None],
        }

    def test_non_finite_inside_dataclass_and_model(self):
        """Projections are sanitised inside dataclasses and Pydantic models."""
        text = encode_tool_result(
            {
                "metric": Metric("temp", Decimal("-Infinity")),
                "reading": Reading(label="temp", value=float("nan")),
            }
        )
        assert "NaN" not in text
        assert "Infinity" not in text
        assert json.loads(text, parse_constant=_reject_json_constant) == {
            "metric": {"label": "temp", "value": None},
            "reading": {"label": "temp", "value": None},
        }

    def test_shared_values_that_are_not_cyclic_survive(self):
        shared = [float("nan")]
        text = encode_tool_result({"a": shared, "b": shared})
        assert json.loads(text) == {"a": [None], "b": [None]}

    def test_cycles_raise_value_error(self):
        """A circular result still fails with json's own error (#135)."""
        cyclic: dict = {}
        cyclic["self"] = cyclic
        with pytest.raises(ValueError, match="Circular reference detected"):
            encode_tool_result(cyclic)


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

    def test_redoc_page(self, both):
        status, _ = compare(both, "/redoc", compare_body=False)
        assert status == 200
        _, headers, body = request(both[1], "/redoc")
        assert "text/html" in headers["content-type"]
        assert b'spec-url="/openapi.json"' in body


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
