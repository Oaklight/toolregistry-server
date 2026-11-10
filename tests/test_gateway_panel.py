"""Panel routes added by #250, #251 and #252.

Three groups, one per issue:

* source editing, connection tests and per-source tools (#250);
* tool detail and the guarded call tester (#251);
* execution-log filters by principal, outcome, tool and time (#252).

Every test drives a real :class:`AdminServer` with the extension mounted.
Network access is patched out: a connection test must be exercisable without
leaving the test process.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

pytest.importorskip("toolregistry.admin.extensions")

from toolregistry import AdminServer, ToolRegistry, ToolTag
from toolregistry.config import MCPSource

from toolregistry_server.ext.gateway import (
    GatewayAdminExtension,
    panel_sources,
)
from toolregistry_server.gateway.admin import parse_source_payload
from toolregistry_server.route_table import RouteTable

# ---------------------------------------------------------------- sources


def _declaration_dict(source: Any) -> dict[str, Any]:
    """Return a source declaration in its config-file form."""
    if hasattr(source, "to_dict"):
        return dict(source.to_dict())
    return dict(source)


@dataclasses.dataclass
class FakeEntry:
    """Stand-in for ``SourceEntry`` with an editable declaration."""

    source_id: str
    source: Any
    status: str = "applied"

    @property
    def names(self) -> Sequence[str]:
        return ("calc-add",)

    @property
    def allow_private_hosts(self) -> bool:
        return bool(getattr(self.source, "allow_private_hosts", False))

    def to_dict(self) -> dict[str, Any]:
        declaration = _declaration_dict(self.source)
        return {
            "id": self.source_id,
            "kind": declaration.get("type"),
            "detail": declaration.get("url") or declaration.get("class", ""),
            "namespace": declaration.get("namespace"),
            "enabled": declaration.get("enabled", True),
            "status": self.status,
            "last_error": None,
            "names": list(self.names),
            "tool_count": {"enabled": 1, "total": 1},
        }


@dataclasses.dataclass
class FakeMutation:
    """Stand-in for ``MutationResult``."""

    entry: FakeEntry
    counts: dict[str, int]
    persisted: bool = True
    persist_error: str | None = None
    warnings: tuple[str, ...] = ()
    removed: bool = False


class FakeSources:
    """The source surface the panel needs, with ``get`` for the edit form."""

    def __init__(self) -> None:
        self._entries: dict[str, FakeEntry] = {}
        self._next = 0

    def to_dicts(self) -> Sequence[Mapping[str, Any]]:
        return [entry.to_dict() for entry in self._entries.values()]

    def get(self, ref: str) -> FakeEntry:
        return self._entries[ref]

    def add(self, source: Any) -> FakeMutation:
        self._next += 1
        entry = FakeEntry(f"s{self._next}", source)
        self._entries[entry.source_id] = entry
        return FakeMutation(entry, {"registered": 1})

    def remove(self, ref: str) -> FakeMutation:
        return FakeMutation(self._entries.pop(ref), {"unregistered": 1}, removed=True)

    def replace(self, ref: str, source: Any) -> FakeMutation:
        entry = FakeEntry(ref, source)
        self._entries[ref] = entry
        return FakeMutation(entry, {"registered": 1, "unregistered": 1})

    def retry(self, ref: str) -> FakeEntry:
        return self._entries[ref]

    def refresh(self, ref: str) -> Any:
        self._entries[ref]
        return object()


# ---------------------------------------------------------------- harness


def _request(
    url: str, method: str = "GET", data: dict | None = None
) -> tuple[int, bytes]:
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _json(body: Any) -> dict[str, Any]:
    return json.loads(body)


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def subtract(a: int, b: int) -> int:
    """Subtract two integers."""
    return a - b


def remove_all(path: str) -> str:
    """Delete a path."""
    return f"removed {path}"


@pytest.fixture()
def registry() -> ToolRegistry:
    """A registry with a normal, a tagged and a disabled tool."""
    reg = ToolRegistry()
    reg.register(add, namespace="calc")
    reg.register(remove_all, namespace="files")
    reg.update_tool_metadata("files-remove_all", tags={ToolTag.DESTRUCTIVE})
    reg.register(subtract, namespace="calc")
    reg.disable("calc-subtract", "maintenance")
    return reg


@pytest.fixture()
def sources() -> FakeSources:
    """A source manager with one MCP source that stores a bearer header."""
    manager = FakeSources()
    manager._next = 1
    manager._entries["s1"] = FakeEntry(
        "s1",
        MCPSource(
            transport="streamable-http",
            url="https://mcp.example.test/mcp",
            namespace="remote",
            headers={"Authorization": "Bearer top-secret"},
            env={"TOKEN": "also-secret"},
        ),
    )
    return manager


def _start(registry: ToolRegistry, sources: FakeSources):
    extension = GatewayAdminExtension(
        sources=sources,
        parse_source=parse_source_payload,
        route_table=RouteTable(registry),
    )
    admin = AdminServer(registry, port=0, extensions=[extension])
    return admin, admin.start()


@pytest.fixture()
def panel(registry, sources):
    """A running panel with the extension and a route table wired."""
    admin, info = _start(registry, sources)
    try:
        yield info
    finally:
        admin.stop()


# ---------------------------------------------------------------- #252 logs


def _seed_log(registry: ToolRegistry) -> None:
    """Write one entry per principal and outcome, oldest first."""
    registry.enable_logging()
    log = registry.get_execution_log()
    assert log is not None
    base = datetime(2026, 11, 10, 12, 0, 0)
    rows = [
        ("calc-add", "success", {"key_id": "key_a", "client_id": "build-bot"}),
        ("calc-add", "error", {"key_id": "key_a", "client_id": "build-bot"}),
        ("files-remove_all", "success", {"key_id": None, "client_id": "operator"}),
        ("calc-subtract", "success", {}),
    ]
    from toolregistry.admin import ExecutionLogEntry, ExecutionStatus

    for index, (tool, status, metadata) in enumerate(rows):
        entry = ExecutionLogEntry.create(
            tool_name=tool,
            status=ExecutionStatus(status),
            duration_ms=1.0 + index,
            arguments={"a": index},
            metadata=metadata,
        )
        entry = dataclasses.replace(entry, timestamp=base + timedelta(minutes=index))
        log.add(entry)


def _logs(info: Any, query: str = "") -> tuple[int, dict[str, Any]]:
    status, body = _request(f"{info.url}/ext/gateway/logs{query}")
    return status, _json(body)


def _stats(info: Any, query: str = "") -> dict[str, Any]:
    status, body = _request(f"{info.url}/ext/gateway/logs/stats{query}")
    assert status == 200
    return _json(body)


class TestLogFilters:
    def test_logging_disabled_is_a_400(self, panel) -> None:
        status, payload = _logs(panel)
        assert status == 400
        assert "not enabled" in payload["message"]

    def test_each_entry_carries_its_principal(self, registry, panel) -> None:
        _seed_log(registry)
        status, payload = _logs(panel)
        assert status == 200
        principals = {
            entry["tool_name"]: entry["principal"] for entry in payload["entries"]
        }
        assert principals["files-remove_all"] == {
            "key_id": None,
            "label": "operator",
        }
        assert principals["calc-subtract"] == {"key_id": None, "label": "anonymous"}
        assert payload["entries"][0]["timestamp"]

    def test_key_id_filter(self, registry, panel) -> None:
        _seed_log(registry)
        _, payload = _logs(panel, "?key_id=key_a")
        assert [entry["status"] for entry in payload["entries"]] == [
            "error",
            "success",
        ]
        assert all(
            entry["principal"]["key_id"] == "key_a" for entry in payload["entries"]
        )

    def test_operator_key_filter_selects_the_unkeyed_principal(
        self, registry, panel
    ) -> None:
        _seed_log(registry)
        _, payload = _logs(panel, "?key_id=operator")
        assert [entry["tool_name"] for entry in payload["entries"]] == [
            "files-remove_all"
        ]

    def test_key_label_filter_is_case_insensitive(self, registry, panel) -> None:
        _seed_log(registry)
        _, payload = _logs(panel, "?key_label=BUILD-BOT")
        assert len(payload["entries"]) == 2

    def test_outcome_ok_and_error(self, registry, panel) -> None:
        _seed_log(registry)
        _, ok = _logs(panel, "?outcome=ok")
        _, error = _logs(panel, "?outcome=error")
        assert {entry["status"] for entry in ok["entries"]} == {"success"}
        assert [entry["tool_name"] for entry in error["entries"]] == ["calc-add"]

    def test_busy_and_denied_match_nothing(self, registry, panel) -> None:
        _seed_log(registry)
        for outcome in ("busy", "denied"):
            _, payload = _logs(panel, f"?outcome={outcome}")
            assert payload["entries"] == []
            assert payload["count"] == 0

    def test_unknown_outcome_is_a_400(self, registry, panel) -> None:
        _seed_log(registry)
        status, payload = _logs(panel, "?outcome=maybe")
        assert status == 400
        assert "outcome" in payload["message"]

    def test_tool_and_time_range_combined(self, registry, panel) -> None:
        _seed_log(registry)
        query = (
            "?tool_name=calc-add&since=2026-11-10T12:00:30&until=2026-11-10T12:02:00"
        )
        _, payload = _logs(panel, query)
        assert [entry["status"] for entry in payload["entries"]] == ["error"]

    def test_time_range_accepts_a_z_suffix(self, registry, panel) -> None:
        _seed_log(registry)
        # A Z timestamp is UTC.  Derive it from the local instant the seed
        # used, so the expectation holds in every timezone.
        since = datetime(2026, 11, 10, 12, 3).astimezone(timezone.utc)
        query = "?since=" + since.strftime("%Y-%m-%dT%H:%M:%SZ")
        _, payload = _logs(panel, query)
        assert len(payload["entries"]) == 1

    def test_bad_time_is_a_400(self, registry, panel) -> None:
        _seed_log(registry)
        status, payload = _logs(panel, "?since=yesterday")
        assert status == 400
        assert "since" in payload["message"]

    def test_bad_limit_is_a_400(self, registry, panel) -> None:
        _seed_log(registry)
        status, _ = _logs(panel, "?limit=0")
        assert status == 400

    def test_combined_filters(self, registry, panel) -> None:
        _seed_log(registry)
        query = "?key_label=build-bot&outcome=error&tool_name=calc-add"
        _, payload = _logs(panel, query)
        assert len(payload["entries"]) == 1
        assert payload["entries"][0]["principal"]["key_id"] == "key_a"

    def test_stats_agree_with_the_table(self, registry, panel) -> None:
        _seed_log(registry)
        query = "?key_id=key_a"
        _, table = _logs(panel, query)
        stats = _stats(panel, query)
        assert stats["total_entries"] == table["count"]
        assert stats["by_status"] == {"success": 1, "error": 1}
        assert stats["by_key"] == {"build-bot": 2}
        assert stats["by_tool"] == {"calc-add": 2}

    def test_warnings_filter_still_applies(self, registry, panel) -> None:
        _seed_log(registry)
        _, payload = _logs(panel, "?has_warnings=false")
        assert len(payload["entries"]) == 4
        _, none = _logs(panel, "?has_warnings=true")
        assert none["entries"] == []


# ---------------------------------------------------------------- #250 sources


class TestSourceEditing:
    def test_detail_hides_every_secret(self, panel) -> None:
        status, body = _request(f"{panel.url}/ext/gateway/sources/s1")
        assert status == 200
        declaration = _json(body)["source"]["declaration"]
        assert declaration["url"] == "https://mcp.example.test/mcp"
        assert "headers" not in declaration
        assert "env" not in declaration
        assert sorted(declaration["secret_fields"]) == [
            "env.TOKEN",
            "headers.Authorization",
        ]
        assert "top-secret" not in body.decode()
        assert "also-secret" not in body.decode()

    def test_unknown_source_is_a_404(self, panel) -> None:
        status, _ = _request(f"{panel.url}/ext/gateway/sources/nope")
        assert status == 404

    def test_edit_keeps_a_blank_secret(self, panel, sources) -> None:
        status, _ = _request(
            f"{panel.url}/ext/gateway/sources/s1",
            method="PUT",
            data={
                "type": "mcp",
                "transport": "streamable-http",
                "url": "https://mcp.example.test/mcp",
                "namespace": "renamed",
            },
        )
        assert status == 200
        stored = sources.get("s1").source
        assert stored.namespace == "renamed"
        assert stored.headers == {"Authorization": "Bearer top-secret"}
        assert stored.env == {"TOKEN": "also-secret"}

    def test_clear_secrets_drops_them(self, panel, sources) -> None:
        status, _ = _request(
            f"{panel.url}/ext/gateway/sources/s1",
            method="PUT",
            data={
                "type": "mcp",
                "transport": "streamable-http",
                "url": "https://mcp.example.test/mcp",
                "clear_secrets": True,
            },
        )
        assert status == 200
        stored = sources.get("s1").source
        assert stored.headers is None
        assert stored.env is None

    def test_mcp_auth_is_stored_as_a_header(self, panel, sources) -> None:
        status, _ = _request(
            f"{panel.url}/ext/gateway/sources",
            method="POST",
            data={
                "type": "mcp",
                "transport": "sse",
                "url": "https://mcp.example.test/sse",
                "auth": {"type": "bearer", "token": "abc123"},
            },
        )
        assert status == 201
        assert sources.get("s2").source.headers == {"Authorization": "Bearer abc123"}

    def test_custom_header_auth(self, panel, sources) -> None:
        status, _ = _request(
            f"{panel.url}/ext/gateway/sources",
            method="POST",
            data={
                "type": "mcp",
                "transport": "streamable-http",
                "url": "https://mcp.example.test/mcp",
                "auth": {
                    "type": "header",
                    "header_name": "X-API-Key",
                    "token": "abc123",
                },
            },
        )
        assert status == 201
        assert sources.get("s2").source.headers == {"X-API-Key": "abc123"}

    def test_auth_without_a_token_is_refused(self, panel) -> None:
        status, body = _request(
            f"{panel.url}/ext/gateway/sources",
            method="POST",
            data={
                "type": "mcp",
                "transport": "sse",
                "url": "https://mcp.example.test/sse",
                "auth": {"type": "bearer"},
            },
        )
        assert status == 400
        assert "auth.token" in _json(body)["message"]

    def test_per_source_tools(self, panel) -> None:
        status, body = _request(f"{panel.url}/ext/gateway/sources/s1/tools")
        assert status == 200
        [tool] = _json(body)["tools"]
        assert tool["name"] == "calc-add"
        assert tool["description"] == "Add two integers."
        assert tool["enabled"] is True

    def test_openapi_token_and_python_kwargs_are_write_only(self) -> None:
        from toolregistry.config import AuthConfig, OpenAPISource, PythonSource

        from toolregistry_server.ext.gateway.panel_sources import public_declaration

        openapi = public_declaration(
            OpenAPISource(
                url="https://api.example.test/spec.json",
                auth=AuthConfig(token="s3cret-token"),
            )
        )
        assert "s3cret-token" not in json.dumps(openapi)
        assert openapi["secret_fields"] == ["auth.token"]

        python = public_declaration(
            PythonSource(class_path="pkg.Tools", kwargs={"api_key": "s3cret"})
        )
        assert "s3cret" not in json.dumps(python)
        assert python["secret_fields"] == ["kwargs.api_key"]


class TestConnectionTest:
    def test_openapi_success(self, panel, monkeypatch) -> None:
        monkeypatch.setattr(
            panel_sources,
            "_probe_openapi",
            lambda source, **kwargs: {
                "ok": True,
                "kind": "openapi",
                "tool_count": 3,
                "server_name": "Demo",
                "server_version": "1.0",
                "tools": [],
                "error": None,
                "error_type": None,
            },
        )
        status, body = _request(
            f"{panel.url}/ext/gateway/sources/test",
            method="POST",
            data={"type": "openapi", "url": "https://api.example.test/openapi.json"},
        )
        assert status == 200
        result = _json(body)["test"]
        assert result["ok"] is True
        assert result["tool_count"] == 3
        assert result["server_name"] == "Demo"

    def test_failure_reports_the_error_verbatim(self, panel, monkeypatch) -> None:
        def _fail(source, **kwargs):
            raise ValueError("spec fetch failed: HTTP 500")

        monkeypatch.setattr(panel_sources, "_probe_openapi", _fail)
        status, body = _request(
            f"{panel.url}/ext/gateway/sources/test",
            method="POST",
            data={"type": "openapi", "url": "https://api.example.test/openapi.json"},
        )
        assert status == 200
        result = _json(body)["test"]
        assert result["ok"] is False
        assert result["error"] == "spec fetch failed: HTTP 500"
        assert result["error_type"] == "ValueError"

    def test_failure_redacts_url_credentials(self, panel, monkeypatch) -> None:
        def _fail(source, **kwargs):
            raise ValueError("cannot fetch https://user:pass@example.test/spec")

        monkeypatch.setattr(panel_sources, "_probe_openapi", _fail)
        _, body = _request(
            f"{panel.url}/ext/gateway/sources/test",
            method="POST",
            data={"type": "openapi", "url": "https://example.test/spec"},
        )
        assert "user:pass" not in body.decode()

    def test_mcp_success_reports_the_server(self, panel, monkeypatch) -> None:
        async def _probe(source, **kwargs):
            return {
                "ok": True,
                "kind": "mcp",
                "tool_count": 2,
                "server_name": "remote",
                "server_version": "2.1",
                "tools": [{"name": "one", "description": "First"}],
                "error": None,
                "error_type": None,
            }

        monkeypatch.setattr(panel_sources, "_probe_mcp", _probe)
        status, body = _request(
            f"{panel.url}/ext/gateway/sources/s1/test", method="POST"
        )
        assert status == 200
        result = _json(body)["test"]
        assert result["ok"] is True
        assert result["server_name"] == "remote"
        assert result["tool_count"] == 2

    def test_python_source_has_no_test(self, panel) -> None:
        status, body = _request(
            f"{panel.url}/ext/gateway/sources/test",
            method="POST",
            data={"type": "python", "class": "demo.Tools"},
        )
        assert status == 400
        assert "no connection test" in _json(body)["message"]


# ---------------------------------------------------------------- #251 tools


def _call(info: Any, name: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, body = _request(
        f"{info.url}/ext/gateway/tools/{name}/call", method="POST", data=data
    )
    return status, _json(body)


class TestToolDetail:
    def test_detail_includes_both_schemas(self, panel) -> None:
        status, body = _request(f"{panel.url}/ext/gateway/tools/calc-add")
        assert status == 200
        tool = _json(body)["tool"]
        assert tool["description"] == "Add two integers."
        assert tool["namespace"] == "calc"
        assert tool["enabled"] is True
        assert set(tool["input_schema"]["properties"]) >= {"a", "b"}
        assert tool["execution_hints"] is not None

    def test_disabled_tool_reports_the_reason(self, panel) -> None:
        _, body = _request(f"{panel.url}/ext/gateway/tools/calc-subtract")
        tool = _json(body)["tool"]
        assert tool["enabled"] is False
        assert tool["disable_reason"] == "maintenance"

    def test_unknown_tool_is_a_404(self, panel) -> None:
        status, _ = _request(f"{panel.url}/ext/gateway/tools/missing")
        assert status == 404

    def test_listing_includes_disabled_tools(self, panel) -> None:
        status, body = _request(f"{panel.url}/ext/gateway/tools")
        assert status == 200
        tools = {tool["name"]: tool for tool in _json(body)["tools"]}
        assert tools["calc-add"]["enabled"] is True
        assert tools["calc-subtract"]["enabled"] is False
        assert tools["files-remove_all"]["dangerous_tags"] == ["destructive"]


class TestCallTester:
    def test_call_succeeds_and_is_logged(self, registry, panel) -> None:
        registry.enable_logging()
        status, body = _call(
            panel, "calc-add", {"arguments": {"a": 2, "b": 3}, "confirm": True}
        )
        assert status == 200
        result = body["call"]
        assert result["ok"] is True
        assert result["result"] == 5
        assert result["duration_ms"] >= 0

        log = registry.get_execution_log()
        assert log is not None
        [entry] = log.get_entries()
        assert entry.tool_name == "calc-add"
        assert entry.metadata["client_id"] == "operator"
        assert entry.metadata["key_id"] is None

    def test_the_call_appears_in_the_filtered_log(self, registry, panel) -> None:
        registry.enable_logging()
        _call(panel, "calc-add", {"arguments": {"a": 1, "b": 1}, "confirm": True})
        _, payload = _logs(panel, "?key_id=operator")
        assert [entry["tool_name"] for entry in payload["entries"]] == ["calc-add"]

    def test_confirmation_is_required(self, panel) -> None:
        status, body = _call(panel, "files-remove_all", {"arguments": {"path": "/tmp"}})
        assert status == 409
        payload = body
        assert payload["error"] == "Confirmation Required"
        assert payload["dangerous_tags"] == ["destructive"]

    def test_a_disabled_tool_is_refused(self, registry, panel) -> None:
        registry.enable_logging()
        status, body = _call(
            panel, "calc-subtract", {"arguments": {"a": 2, "b": 1}, "confirm": True}
        )
        assert status == 409
        assert body["error"] == "Tool Disabled"
        log = registry.get_execution_log()
        assert log is not None
        assert log.get_entries() == []

    def test_invalid_arguments_are_refused(self, panel) -> None:
        status, body = _call(
            panel, "calc-add", {"arguments": {"a": "x"}, "confirm": True}
        )
        assert status == 422
        assert body["error"] == "Invalid Arguments"

    def test_a_failing_tool_reports_the_error_and_logs_it(
        self, registry, panel
    ) -> None:
        def boom() -> str:
            raise RuntimeError("tool exploded")

        registry.register(boom, namespace="danger")
        route_table = RouteTable(registry)
        extension = GatewayAdminExtension(route_table=route_table)
        admin = AdminServer(registry, port=0, extensions=[extension])
        info = admin.start()
        try:
            status, body = _call(
                info, "danger-boom", {"arguments": {}, "confirm": True}
            )
        finally:
            admin.stop()
        assert status == 200
        result = body["call"]
        assert result["ok"] is False
        assert result["error"] == "tool exploded"
        assert result["error_type"] == "RuntimeError"

    def test_unknown_tool_is_a_404(self, panel) -> None:
        status, _ = _call(panel, "missing", {"arguments": {}, "confirm": True})
        assert status == 404

    def test_a_call_needs_an_operator_session(self, registry) -> None:
        extension = GatewayAdminExtension(route_table=RouteTable(registry))
        admin = AdminServer(
            registry, port=0, auth_token="operator-secret", extensions=[extension]
        )
        info = admin.start()
        try:
            status, _ = _request(
                f"{info.url}/ext/gateway/tools/calc-add/call",
                method="POST",
                data={"arguments": {"a": 1, "b": 2}, "confirm": True},
            )
        finally:
            admin.stop()
        assert status == 401
