"""Tests for wiring upstream OAuth into the MCP source path (#254, stage 1)."""

from __future__ import annotations

import textwrap

from toolregistry import ToolRegistry
from toolregistry.config import MCPSource, ToolConfig, load_config

from toolregistry_server import source_manager as source_manager_module
from toolregistry_server._config_file import read_config_mapping
from toolregistry_server.mcp_oauth.provider import (
    ReauthorizationRequired,
    TransientOAuthError,
)
from toolregistry_server.source_manager import SourceManager, SourceStatus

MCP_SOURCE = """\
  - type: mcp
    transport: streamable-http
    url: https://mcp.example.com/mcp
    namespace: example
"""

OAUTH_SECTION = """\
mcp_oauth:
  - source: "namespace:example"
    issuer: https://idp.example.com
    client_id: toolregistry
    client_secret: literal-secret
    token_endpoint: https://idp.example.com/token
"""


class FakeProvider:
    """Minimal provider: returns a header or raises a scripted error."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.seen: list[object] = []

    def headers_for(self, source):
        self.seen.append(source)
        if self.error is not None:
            raise self.error
        return {"Authorization": "Bearer AT"}

    def replace_declarations(self, declarations) -> None:
        self.declarations = declarations


def _config(path):
    return load_config(str(path))


def _write(tmp_path, tools: str, oauth: str = "", extra: str = "") -> str:
    path = tmp_path / "tools.yaml"
    path.write_text(
        textwrap.dedent("mode: denylist\ntools:\n" + tools + oauth + extra),
        encoding="utf-8",
    )
    return str(path)


def _patch_registration(monkeypatch):
    calls: list[dict[str, str] | None] = []

    def fake_register(registry, source, *, oauth_headers=None):
        calls.append(oauth_headers)

    monkeypatch.setattr(source_manager_module, "register_mcp_source", fake_register)
    return calls


def test_register_mcp_source_merges_oauth_headers(monkeypatch):
    registry = ToolRegistry()
    seen: dict[str, object] = {}

    def fake_register(transport, **kwargs):
        seen["transport"] = transport
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(registry, "register_from_mcp", fake_register)
    source = MCPSource(
        transport="streamable-http",
        namespace="example",
        url="https://mcp.example.com/mcp",
        headers={"X-Static": "1"},
    )
    source_manager_module.register_mcp_source(
        registry, source, oauth_headers={"Authorization": "Bearer AT"}
    )
    assert seen["headers"] == {
        "X-Static": "1",
        "Authorization": "Bearer AT",
    }


def test_register_mcp_source_keeps_declared_headers_without_oauth(monkeypatch):
    registry = ToolRegistry()
    seen: dict[str, object] = {}

    def fake_register(transport, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(registry, "register_from_mcp", fake_register)
    source = MCPSource(
        transport="streamable-http",
        namespace="example",
        url="https://mcp.example.com/mcp",
        headers={"X-Static": "1"},
    )
    source_manager_module.register_mcp_source(registry, source)
    assert seen["headers"] == {"X-Static": "1"}


def test_source_manager_passes_the_oauth_header(monkeypatch, tmp_path):
    calls = _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )
    entries = manager.apply_initial()
    assert entries[0].status is SourceStatus.APPLIED
    assert calls == [{"Authorization": "Bearer AT"}]
    assert provider.seen


def test_reauth_failure_marks_the_source(monkeypatch, tmp_path):
    calls = _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider(ReauthorizationRequired("invalid_grant: revoked"))
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )
    entry = manager.apply_initial()[0]
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is True
    assert entry.error == "invalid_grant: revoked"
    assert entry.to_dict()["reauth_required"] is True
    assert calls == []  # registration was never attempted


def test_transient_failure_is_not_marked_for_reauth(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider(TransientOAuthError("connection reset"))
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )
    entry = manager.apply_initial()[0]
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is False
    assert entry.error == "connection reset"


def test_persist_preserves_server_sections(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(
        tmp_path,
        MCP_SOURCE,
        OAUTH_SECTION,
        "gateway:\n  frontends:\n    mcp:\n      enabled: true\n",
    )
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    manager.apply_initial()
    persisted, error, backup, _warnings = manager._persist()
    assert persisted is True
    assert error is None
    assert backup == f"{path}.bak"

    data = read_config_mapping(path)
    assert data["gateway"]["frontends"]["mcp"]["enabled"] is True
    assert data["mcp_oauth"][0]["client_secret"] == "literal-secret"
    assert data["mcp_oauth"][0]["source"] == "namespace:example"


def test_persist_prunes_an_orphan_oauth_declaration(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    manager.apply_initial()
    manager.config = ToolConfig(mode="denylist", tools=(), source=path)
    persisted, error, _backup, _warnings = manager._persist()
    assert persisted is True
    assert error is None
    assert "mcp_oauth" not in read_config_mapping(path)


MCP_SOURCE_DISABLED = """\
  - type: mcp
    transport: streamable-http
    url: https://mcp.example.com/mcp
    namespace: example
    enabled: false
"""


def test_reload_enables_oauth_without_a_restart(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    # The source is disabled so the reload does not try to reach the fake
    # issuer; the declaration is still parsed and the provider is created.
    path = _write(tmp_path, MCP_SOURCE_DISABLED, OAUTH_SECTION)
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    assert manager.oauth_provider is None
    assert manager.reload_from_path() is True
    assert manager.oauth_provider is not None
    assert len(manager.oauth_provider.declarations) == 1


def test_reload_updates_existing_declarations(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )
    assert manager.reload_from_path() is True
    assert len(provider.declarations) == 1

    _write(tmp_path, MCP_SOURCE)
    assert manager.reload_from_path() is True
    assert provider.declarations == ()


def test_reload_rejects_a_bad_oauth_section(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE)
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    _write(
        tmp_path,
        MCP_SOURCE,
        "mcp_oauth:\n"
        '  - source: "namespace:example"\n'
        "    issuer: https://idp.example.com\n"
        "    client_id: toolregistry\n"
        "    client_secret: secret\n"
        "    token_stroe: /tmp/wrong.db\n",
    )
    assert manager.reload_from_path() is False
    assert "unrecognised key" in (manager.last_reload_error or "")
    assert manager.oauth_provider is None


def test_oauth_failure_message_is_redacted(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider(
        ReauthorizationRequired("https://user:pass@idp.example.com/token")
    )
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )
    entry = manager.apply_initial()[0]
    assert "user:pass" not in (entry.error or "")
    assert entry.error == "https://idp.example.com/token"
