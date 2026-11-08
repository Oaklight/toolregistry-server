"""Bounded spec-regex validation on both OpenAPI backends.

The optional ``regex`` package is replaced with a fake, so these tests run on
the default matrix as well and the budget/exception paths are deterministic.
"""

import asyncio
import json
import re
import threading
import time
import urllib.error
import urllib.request
from io import StringIO

import pytest
from toolregistry import ToolRegistry
from toolregistry.config import OpenAPISource
from toolregistry.tool import Tool

from toolregistry_server import RouteTable, regex_guard
from toolregistry_server._vendor.structlog import (
    BoundLogger,
    ConsoleRenderer,
    PrintLogger,
    TimeStamper,
    add_log_level,
)
from toolregistry_server.adapters.openapi import create_openapi_app
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app
from toolregistry_server.regex_guard import SpecPatternError
from toolregistry_server.registry_builder import register_openapi_source


class _FakeRegex:
    """Stand-in for the optional ``regex`` package using ``re`` to match."""

    class error(Exception):
        """Matches the shape of ``regex.error``."""

    def __init__(self, *, timeout: bool = False, invalid: bool = False) -> None:
        self.timeout = timeout
        self.invalid = invalid
        self.calls: list[tuple[str, str, float | None]] = []

    def search(self, pattern: str, text: str, timeout: float | None = None):
        self.calls.append((pattern, text, timeout))
        if self.timeout:
            raise TimeoutError("regex timed out")
        if self.invalid:
            raise self.error(f"bad pattern {pattern!r}")
        return re.search(pattern, text)

    def compile(self, pattern: str):
        if pattern in {"(", "[", "{"}:
            raise self.error(f"missing ) at offset 0 in {pattern!r}")
        return pattern


@pytest.fixture
def fake_regex(monkeypatch):
    def install(**kwargs) -> _FakeRegex:
        module = _FakeRegex(**kwargs)
        monkeypatch.setattr(regex_guard, "_regex", module)
        monkeypatch.setattr(regex_guard, "HAS_REGEX", True)
        return module

    return install


@pytest.fixture(scope="module", autouse=True)
def _no_signal_handlers():
    """The httpserver installs signal handlers, which need the main thread."""
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


def build_table(parameters: dict, name: str = "regex_tool") -> RouteTable:
    registry = ToolRegistry()

    def proxy(**kwargs):
        return kwargs

    tool = Tool(
        name=name,
        description="proxy tool",
        parameters=parameters,
        callable=proxy,
    )
    registry.register(tool)
    table = RouteTable(registry)
    table.refresh_all()
    return table


def call_fastapi(table: RouteTable, path: str, payload: dict, **kwargs):
    from fastapi.testclient import TestClient

    client = TestClient(create_openapi_app(table, **kwargs))
    response = client.post(path, json=payload)
    return response.status_code, response.headers.get("content-type"), response.content


def call_httpserver(table: RouteTable, path: str, payload: dict, **kwargs):
    app = create_httpserver_app(table, **kwargs)
    threading.Thread(
        target=app.run, kwargs={"host": "127.0.0.1", "port": 0}, daemon=True
    ).start()
    deadline = time.time() + 5
    while not app.port and time.time() < deadline:
        time.sleep(0.05)
    request = urllib.request.Request(
        f"http://127.0.0.1:{app.port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return (
                response.status,
                response.headers.get("content-type"),
                response.read(),
            )
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("content-type"), error.read()
    finally:
        app.shutdown()


def _capture_adapter_logs(monkeypatch) -> StringIO:
    """Route the adapter module's logger into a stream."""
    import toolregistry_server.adapters.openapi.adapter as adapter

    stream = StringIO()
    log = BoundLogger(
        logger=PrintLogger(file=stream),
        processors=[add_log_level, TimeStamper(), ConsoleRenderer(colors=False)],
        context={},
    )
    monkeypatch.setattr(adapter, "logger", log)
    return stream


_BACKENDS = (call_fastapi, call_httpserver)

_PATTERN_SCHEMA = {
    "type": "object",
    "properties": {"q": {"type": "string", "pattern": "^[a-z]+$"}},
    "required": ["q"],
}

_PATTERN_PROPERTIES_SCHEMA = {
    "type": "object",
    "patternProperties": {"^x-": {"type": "integer"}},
    "additionalProperties": False,
}


@pytest.mark.parametrize("call", _BACKENDS, ids=["fastapi", "httpserver"])
def test_valid_pattern_is_enforced(fake_regex, call):
    fake_regex()
    table = build_table(_PATTERN_SCHEMA)
    path = table.get_route("regex_tool").path

    status, _, _ = call(table, path, {"q": "abc"})
    assert status == 200

    status, _, body = call(table, path, {"q": "ABC"})
    assert status == 422
    assert json.loads(body)["detail"][0]["loc"] == ["body", "q"]


@pytest.mark.parametrize("call", _BACKENDS, ids=["fastapi", "httpserver"])
def test_zero_timeout_restores_ignoring_spec_patterns(fake_regex, call):
    fake_regex()
    table = build_table(_PATTERN_SCHEMA)
    path = table.get_route("regex_tool").path

    status, _, _ = call(table, path, {"q": "ABC"}, regex_timeout=0)
    assert status == 200


@pytest.mark.parametrize("call", _BACKENDS, ids=["fastapi", "httpserver"])
def test_timeout_is_a_generic_500(fake_regex, call, monkeypatch):
    stream = _capture_adapter_logs(monkeypatch)
    fake_regex(timeout=True)
    table = build_table(_PATTERN_SCHEMA)
    path = table.get_route("regex_tool").path

    status, _, body = call(table, path, {"q": "abc"})

    assert status == 500
    assert json.loads(body) == {"detail": "Tool 'regex_tool' validation timed out"}
    assert b"^[a-z]+$" not in body
    output = stream.getvalue()
    assert "Spec regex timed out" in output
    assert "regex_tool" in output


class _StepClock:
    """Clock that advances half a second on every read."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.5
        return self.now


def test_budget_exhaustion_logs_the_bounds(fake_regex, monkeypatch):
    """The budget path is driven by the clock, so it cannot flake."""
    stream = _capture_adapter_logs(monkeypatch)
    fake_regex()
    monkeypatch.setattr(regex_guard, "_clock", _StepClock())
    table = build_table(_PATTERN_SCHEMA)
    path = table.get_route("regex_tool").path

    status, _, body = call_fastapi(table, path, {"q": "abc"})

    assert status == 500
    assert json.loads(body)["detail"] == "Tool 'regex_tool' validation timed out"
    assert "250 ms" in stream.getvalue()


@pytest.mark.parametrize("call", _BACKENDS, ids=["fastapi", "httpserver"])
def test_invalid_pattern_is_a_generic_500(fake_regex, call):
    fake_regex(invalid=True)
    table = build_table(_PATTERN_SCHEMA)
    path = table.get_route("regex_tool").path

    status, _, body = call(table, path, {"q": "abc"})

    assert status == 500
    assert json.loads(body) == {"detail": "Tool 'regex_tool' validation failed"}
    assert b"^[a-z]+$" not in body


def test_pattern_properties_calls_are_probe_plus_pairs(fake_regex):
    module = fake_regex()
    table = build_table(_PATTERN_PROPERTIES_SCHEMA)
    path = table.get_route("regex_tool").path

    status, _, _ = call_fastapi(table, path, {"x-a": 1, "x-b": 2})

    assert status == 200
    # One probe for the pattern itself, then one call per key.
    assert [text for _, text, _ in module.calls] == ["", "x-a", "x-b"]


@pytest.mark.parametrize("call", _BACKENDS, ids=["fastapi", "httpserver"])
def test_installed_regex_does_not_relax_additional_properties(fake_regex, call):
    """The fallback relaxes ``additionalProperties``; bounded validation does not."""
    fake_regex()
    table = build_table(_PATTERN_PROPERTIES_SCHEMA)
    path = table.get_route("regex_tool").path

    # Step 1 accepted both payloads: patterns were stripped and the schema
    # was relaxed, so no constraint was left to fail.
    for payload in ({"x-a": "not an int"}, {"y": 1}):
        status, _, _ = call(table, path, payload, regex_timeout=0)
        assert status == 200

    # With bounded validation the pattern matches and the type is enforced;
    # an uncovered key is rejected by the unrelaxed additionalProperties.
    for payload in ({"x-a": "not an int"}, {"y": 1}):
        status, _, _ = call(table, path, payload)
        assert status == 422


def test_both_backends_agree_on_the_timeout_envelope(fake_regex):
    fake_regex(timeout=True)
    table = build_table(_PATTERN_SCHEMA)
    path = table.get_route("regex_tool").path

    fastapi = call_fastapi(table, path, {"q": "abc"})
    httpserver = call_httpserver(table, path, {"q": "abc"})

    assert fastapi[0] == httpserver[0] == 500
    assert fastapi[2] == httpserver[2]
    assert fastapi[1].startswith("application/json")
    assert httpserver[1].startswith("application/json")


def test_negative_timeout_is_rejected_at_construction(fake_regex):
    fake_regex()
    table = build_table(_PATTERN_SCHEMA)

    with pytest.raises(ValueError, match="regex_timeout"):
        create_openapi_app(table, regex_timeout=-0.1)
    with pytest.raises(ValueError, match="regex_timeout"):
        create_httpserver_app(table, regex_timeout=-0.1)


def test_cli_float_type_rejects_negative_and_non_numeric():
    import argparse

    from toolregistry_server.adapters.openapi import _non_negative_float

    assert _non_negative_float("0") == 0
    assert _non_negative_float("0.05") == 0.05
    with pytest.raises(argparse.ArgumentTypeError):
        _non_negative_float("-1")
    with pytest.raises(argparse.ArgumentTypeError):
        _non_negative_float("fast")


# ---------------------------------------------------------------------------
# Load time: a bad pattern drops the tool, the source, or aborts
# ---------------------------------------------------------------------------

_BAD = {"type": "object", "pattern": "("}
_GOOD = {"type": "object", "properties": {"q": {"type": "string"}}}


def _patch_openapi_loading(monkeypatch, tools: dict[str, dict]) -> None:
    import toolregistry.integrations.openapi as integration

    monkeypatch.setattr(integration, "load_openapi_spec", lambda *a, **k: {})
    monkeypatch.setattr(integration, "HttpClientConfig", lambda **k: object())

    def register(self, client, spec, **kwargs):
        for name, parameters in tools.items():
            self.register(
                Tool(
                    name=name,
                    description="from spec",
                    parameters=parameters,
                    callable=lambda **k: k,
                )
            )

    monkeypatch.setattr(ToolRegistry, "register_from_openapi", register, raising=False)


def _capture_builder_logs(monkeypatch) -> str:
    import toolregistry_server.registry_builder as builder

    stream = StringIO()
    log = BoundLogger(
        logger=PrintLogger(file=stream),
        processors=[add_log_level, TimeStamper(), ConsoleRenderer(colors=False)],
        context={},
    )
    monkeypatch.setattr(builder, "logger", log)
    return stream


def test_only_the_tool_with_the_bad_pattern_is_dropped(fake_regex, monkeypatch):
    fake_regex()
    _patch_openapi_loading(monkeypatch, {"bad": _BAD, "good": _GOOD})
    stream = _capture_builder_logs(monkeypatch)
    registry = ToolRegistry()

    registered = register_openapi_source(
        registry, OpenAPISource(url="https://example.test/openapi.json")
    )

    assert registered is True
    assert registry.list_tools(include_disabled=True) == ["good"]
    output = stream.getvalue()
    assert "'bad'" in output
    assert "pattern" in output
    assert "'('" in output


def test_source_is_failed_when_no_tool_survives(fake_regex, monkeypatch):
    fake_regex()
    _patch_openapi_loading(monkeypatch, {"bad": _BAD})
    registry = ToolRegistry()

    registered = register_openapi_source(
        registry, OpenAPISource(url="https://example.test/openapi.json")
    )

    assert registered is False
    assert registry.list_tools(include_disabled=True) == []


def test_strict_mode_removes_the_whole_source(fake_regex, monkeypatch):
    fake_regex()
    _patch_openapi_loading(monkeypatch, {"bad": _BAD, "good": _GOOD})
    registry = ToolRegistry()

    with pytest.raises(SpecPatternError):
        register_openapi_source(
            registry,
            OpenAPISource(url="https://example.test/openapi.json"),
            strict=True,
        )
    # Even the healthy tool is gone: strict is all-or-nothing.
    assert registry.list_tools(include_disabled=True) == []
