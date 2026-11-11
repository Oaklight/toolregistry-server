"""Tests for wiring upstream OAuth into the MCP source path (#254, stage 1)."""

from __future__ import annotations

import builtins
import dataclasses
import importlib
import sys
import textwrap
import types
from pathlib import Path

import pytest
from toolregistry import ToolRegistry
from toolregistry.config import MCPSource, ToolConfig, load_config

from toolregistry_server import source_manager as source_manager_module
from toolregistry_server._config_file import read_config_mapping
from toolregistry_server.oauth.upstream.config import load_oauth_clients
from toolregistry_server.oauth.upstream.provider import (
    OAuthClientError,
    OAuthClientProvider,
    ReauthorizationRequired,
    TransientOAuthError,
)
from toolregistry_server.oauth.upstream.store import TokenRecord, TokenStore
from toolregistry_server.source_manager import (
    _CORE_CONFIG_KEYS,
    SourceManager,
    SourceStatus,
)

MCP_SOURCE = """\
  - type: mcp
    transport: streamable-http
    url: https://mcp.example.com/mcp
    namespace: example
"""

MCP_SOURCE_DISABLED = """\
  - type: mcp
    transport: streamable-http
    url: https://mcp.example.com/mcp
    namespace: example
    enabled: false
"""

OAUTH_SECTION = """\
oauth:
  client_concurrency:
    reports-bot: 2
  clients:
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
        self.declarations: tuple = ()
        self.cleared: list[str] = []

    def headers_for(self, source):
        self.seen.append(source)
        if self.error is not None:
            raise self.error
        return {"Authorization": "Bearer AT"}

    def replace_declarations(self, declarations) -> None:
        self.declarations = declarations

    def clear_stored_token(self, selector: str) -> None:
        self.cleared.append(selector)


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


def _sdk_http_module():
    """Return the HTTP client module the installed MCP SDK raises from.

    mcp 2.x uses ``httpx2`` and 1.x uses ``httpx``; they are separate
    packages, so a fixture built from the wrong one would pass while the
    classifier missed every real rejection.
    """
    for name in ("mcp.client.streamable_http", "mcp.client.sse"):
        try:
            transport = importlib.import_module(name)
        except ImportError:  # pragma: no cover - mcp is an optional extra
            continue
        for attribute in ("httpx2", "httpx"):
            module = getattr(transport, attribute, None)
            if module is not None:
                return module
    pytest.skip("the installed MCP SDK exposes no HTTP client module")


def _status_error(status: int) -> Exception:
    module = _sdk_http_module()
    request = module.Request("POST", "https://mcp.example.com/mcp")
    response = module.Response(status, request=request)
    return module.HTTPStatusError("client error", request=request, response=response)


_EXCEPTION_GROUP = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="ExceptionGroup needs Python 3.11",
)


def _exception_group(*exceptions: BaseException) -> BaseException:
    """Build an ``ExceptionGroup`` on a runtime that has one."""
    group_cls = getattr(builtins, "ExceptionGroup", None)
    if group_cls is None:  # pragma: no cover - skipped below on 3.10
        pytest.skip("ExceptionGroup needs Python 3.11")
    return group_cls("unhandled errors", list(exceptions))


@pytest.mark.parametrize("status", [401, 403])
def test_mcp_auth_rejection_marks_the_source_for_reauth(monkeypatch, tmp_path, status):
    """A token the MCP server rejects must reach the re-authorize path."""
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )

    def refuse(registry, source, *, oauth_headers=None):
        raise _status_error(status)

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is True
    assert str(status) in entry.error


@_EXCEPTION_GROUP
def test_mcp_auth_rejection_wrapped_in_a_group_is_recognised(monkeypatch, tmp_path):
    """The MCP client raises inside a task group, so the 401 is nested."""
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )

    def refuse(registry, source, *, oauth_headers=None):
        raise _exception_group(_status_error(401))

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.reauth_required is True


def test_a_401_without_oauth_is_not_reported_as_reauth(monkeypatch, tmp_path):
    """Only a source whose credential came from the provider is affected."""
    path = _write(tmp_path, MCP_SOURCE)
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)

    def refuse(registry, source, *, oauth_headers=None):
        raise _status_error(401)

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is False


def _fake_http_module(module):
    """A transport-facing view of *module* that exposes only HTTP classes."""
    return types.SimpleNamespace(
        Request=module.Request,
        Response=module.Response,
        HTTPStatusError=module.HTTPStatusError,
    )


@pytest.mark.parametrize("attribute", ["httpx2", "httpx"])
def test_classification_uses_the_module_the_sdk_exposes(monkeypatch, attribute):
    """The resolver must follow the transport, whichever client it names.

    Patching a transport to expose one client and hide the other proves the
    classifier reads that module; a hard-coded ``httpx`` would fail the
    ``httpx2`` leg, which is the leg the default install runs.
    """
    try:
        available = importlib.import_module(attribute)
    except ImportError:
        pytest.skip(f"{attribute} is not installed")
    fake = _fake_http_module(available)

    for name in ("mcp.client.streamable_http", "mcp.client.sse"):
        try:
            transport = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(transport, attribute, fake, raising=False)
        for other in {"httpx2", "httpx"} - {attribute}:
            monkeypatch.delattr(transport, other, raising=False)

    from toolregistry_server import source_manager as module_under_test

    assert module_under_test._auth_rejection_status(_error_from(fake, 401)) == 401
    assert module_under_test._auth_rejection_status(_error_from(fake, 403)) == 403
    # A status that is not a credential verdict stays unclassified.
    assert module_under_test._auth_rejection_status(_error_from(fake, 400)) is None
    assert module_under_test._auth_rejection_status(_error_from(fake, 500)) is None


def _error_from(module, status: int) -> Exception:
    request = module.Request("POST", "https://mcp.example.com/mcp")
    response = module.Response(status, request=request)
    return module.HTTPStatusError("client error", request=request, response=response)


def test_a_non_auth_error_is_not_reported_as_reauth(monkeypatch, tmp_path):
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )

    def refuse(registry, source, *, oauth_headers=None):
        raise _status_error(500)

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.reauth_required is False


def _jsonrpc_error(code: int) -> BaseException:
    """Build the MCP SDK's protocol-level error for the installed version.

    The SDK renamed the class and moved the code: mcp 2.x has
    ``MCPError(code, message)`` with a ``code`` property, mcp 1.x has
    ``McpError(ErrorData(...))`` with no ``code`` attribute at all.  The
    regression only exists where ``code`` is exposed, so this builds whichever
    form is present rather than skipping the version the repo resolves to.
    """
    exceptions = pytest.importorskip("mcp.shared.exceptions")
    error_cls = getattr(exceptions, "MCPError", None)
    if error_cls is not None:
        return error_cls(code, "forbidden")
    error_cls = getattr(exceptions, "McpError", None)
    if error_cls is None:  # pragma: no cover - neither SDK name is present
        pytest.skip("no MCP protocol error class is available")
    types = pytest.importorskip("mcp.types")
    return error_cls(types.ErrorData(code=code, message="forbidden"))


@pytest.mark.parametrize("code", [401, 403])
def test_a_jsonrpc_error_code_is_not_an_http_status(monkeypatch, tmp_path, code):
    """A JSON-RPC error code must never be read as an HTTP status.

    mcp 2.x exposes ``MCPError.code``; a JSON-RPC 401/403 read as HTTP would
    mark the source for re-authorization on a protocol-level error and report
    a false "HTTP 403 from the MCP server".
    """
    error = _jsonrpc_error(code)
    # On an SDK that exposes ``code``, prove the fixture really carries one.
    assert getattr(error, "code", code) == code

    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )

    def refuse(registry, source, *, oauth_headers=None):
        raise error

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is False
    assert "HTTP" not in (entry.error or "")


def test_an_http_error_from_the_mcp_client_is_recognised(monkeypatch, tmp_path):
    """A genuine HTTP rejection still reaches the re-authorization path."""
    import urllib.error

    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )

    def refuse(registry, source, *, oauth_headers=None):
        raise urllib.error.HTTPError(
            "https://mcp.example.com/mcp", 403, "Forbidden", {}, None
        )

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.reauth_required is True
    assert "403" in (entry.error or "")


def test_an_exception_with_a_bare_code_attribute_is_ignored(monkeypatch, tmp_path):
    """Nothing but an HTTP-shaped error may supply the status."""

    class Coded(Exception):
        code = 403

    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )

    def refuse(registry, source, *, oauth_headers=None):
        raise Coded("boom")

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.reauth_required is False


def test_an_exception_with_a_bare_401_code_attribute_is_ignored(monkeypatch, tmp_path):
    """A local ``code = 401`` is not HTTP, so it must not clear a credential."""

    class Coded(Exception):
        code = 401

    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )

    def refuse(registry, source, *, oauth_headers=None):
        raise Coded("boom")

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.reauth_required is False
    assert provider.cleared == []


def test_a_context_only_401_is_not_a_credential_rejection(monkeypatch, tmp_path):
    """``__context__`` is incidental; only ``__cause__`` and groups count.

    Python sets ``__context__`` on anything raised while another exception is
    being handled, and ``raise ... from None`` does not clear it, so a parse
    error inside the client's 401 handler inherits the 401.  Following that
    link would destroy a good credential.
    """

    def refuse(registry, source, *, oauth_headers=None):
        try:
            raise _status_error(401)
        except Exception:
            raise ValueError("could not parse the response") from None

    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )
    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.reauth_required is False
    assert provider.cleared == []


def test_an_explicit_cause_is_still_followed(monkeypatch, tmp_path):
    """``raise ... from exc`` is a deliberate verdict, so it counts."""
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )

    def refuse(registry, source, *, oauth_headers=None):
        try:
            raise _status_error(403)
        except Exception as exc:
            raise RuntimeError("wrapped") from exc

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    entry = manager.apply_initial()[0]
    assert entry.reauth_required is True


def test_a_resource_server_rejection_clears_the_stored_token(monkeypatch, tmp_path):
    """A 401 from the MCP server invalidates the token it was given.

    Without the clear, a token whose response omitted ``expires_in`` never
    looks expired, so every retry presents the same rejected Bearer value and
    a ``client_credentials`` source has no way out.  Everything here is
    offline: the provider is built directly and its transport is stubbed, so
    the store is the only thing consulted until the token is gone.
    """
    store_path = tmp_path / "tokens.db"
    section = OAUTH_SECTION + f"      token_store: {store_path}\n"
    path = _write(tmp_path, MCP_SOURCE, section)
    config = _config(path)
    declarations = load_oauth_clients(path, config)
    provider = OAuthClientProvider(declarations)

    class Counting:
        def __init__(self):
            self.posts = 0

        def get_json(self, url, timeout):
            raise OAuthClientError("offline")

        def post_form(self, url, fields, headers, timeout):
            self.posts += 1
            raise OAuthClientError("offline")

    counting = Counting()
    provider._transport = counting
    store = provider._store_for(declarations[0])
    store.put(
        TokenRecord(
            source_id="namespace:example",
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="DEAD",
            # No expires_in, so is_expiring() can never retire it.
            expires_at=None,
        )
    )

    presented: list[object] = []

    def refuse(registry, source, *, oauth_headers=None):
        presented.append(oauth_headers)
        raise _status_error(401)

    monkeypatch.setattr(source_manager_module, "register_mcp_source", refuse)
    manager = SourceManager(
        ToolRegistry(), config, config_path=path, oauth_provider=provider
    )
    entry = manager.apply_initial()[0]

    # The dead token was presented, then cleared; the row itself survives so
    # the rejection can be counted and escalated later.
    assert presented == [{"Authorization": "Bearer DEAD"}]
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is True
    record = store.get("namespace:example")
    assert record is not None
    assert record.access_token is None
    assert record.rejections == 1

    # The next attempt reaches the token endpoint instead of replaying it.
    with pytest.raises(OAuthClientError, match="offline"):
        provider.headers_for(config.tools[0])
    assert counting.posts == 1


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
    assert data["oauth"]["clients"][0]["client_secret"] == "literal-secret"
    assert data["oauth"]["clients"][0]["source"] == "namespace:example"
    # The resource-server keys in the same section survive the rewrite.
    assert data["oauth"]["client_concurrency"]["reports-bot"] == 2


def test_core_config_keys_come_from_the_installed_dataclass():
    """A key the installed core does not declare must not be popped.

    The floor is core 0.20.1, which has no ``tool_parameters`` field.  A
    hard-coded list would delete that block from a file written by a newer
    core; taking the names from ``ToolConfig`` keeps them.
    """
    expected = {
        field.name for field in dataclasses.fields(ToolConfig) if field.name != "source"
    }
    assert expected == _CORE_CONFIG_KEYS
    assert "source" not in _CORE_CONFIG_KEYS


def test_persist_keeps_a_top_level_key_the_core_does_not_own(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(
        tmp_path,
        MCP_SOURCE,
        extra="future_core_section:\n  enabled: true\n",
    )
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    manager.apply_initial()
    persisted, error, _backup, _warnings = manager._persist()
    assert persisted is True
    assert error is None
    assert read_config_mapping(path)["future_core_section"] == {"enabled": True}


def test_persist_prunes_an_orphan_oauth_declaration(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    manager.apply_initial()
    manager.config = ToolConfig(mode="denylist", tools=(), source=path)
    persisted, error, _backup, _warnings = manager._persist()
    assert persisted is True
    assert error is None
    data = read_config_mapping(path)
    assert "clients" not in data["oauth"]
    # Pruning the last client must not delete the resource-server settings.
    assert data["oauth"]["client_concurrency"]["reports-bot"] == 2


def test_pruning_an_orphan_declaration_clears_its_stored_token(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )
    manager.apply_initial()
    manager.config = ToolConfig(mode="denylist", tools=(), source=path)
    persisted, _error, _backup, _warnings = manager._persist()
    assert persisted is True
    assert provider.cleared == ["namespace:example"]


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


def test_reload_drops_a_declaration_and_its_token(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE_DISABLED, OAUTH_SECTION)
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    # The first reload creates the provider, because the section is present.
    assert manager.reload_from_path() is True
    provider = FakeProvider()
    provider.declarations = manager.oauth_provider.declarations
    manager.oauth_provider = provider

    _write(tmp_path, MCP_SOURCE_DISABLED)
    assert manager.reload_from_path() is True
    assert provider.declarations == ()
    assert provider.cleared == ["namespace:example"]


def test_reload_clears_a_token_minted_for_the_previous_issuer(monkeypatch, tmp_path):
    """Re-pointing a source at another IdP must not keep the old token."""
    _patch_registration(monkeypatch)
    store_path = tmp_path / "tokens.db"
    section = OAUTH_SECTION + f"      token_store: {store_path}\n"
    path = _write(tmp_path, MCP_SOURCE_DISABLED, section)
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    # The first reload creates the provider, because the section is present.
    assert manager.reload_from_path() is True
    provider = manager.oauth_provider
    store = provider._store_for(provider.declarations[0])
    store.put(
        TokenRecord(
            source_id="namespace:example",
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
        )
    )

    _write(
        tmp_path,
        MCP_SOURCE_DISABLED,
        section.replace("https://idp.example.com", "https://new-idp.example.com"),
    )
    assert manager.reload_from_path() is True

    # The next read sees a token minted for another issuer, discards it, and
    # tries the new authorization server.  The transport is stubbed so the
    # test stays offline.
    class Unreachable:
        def get_json(self, url, timeout):
            raise OAuthClientError("offline")

        def post_form(self, url, fields, headers, timeout):
            raise OAuthClientError("offline")

    provider._transport = Unreachable()
    with pytest.raises(OAuthClientError, match="offline"):
        provider.access_token(provider.declarations[0])
    assert store.get("namespace:example") is None


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
    path = _write(
        tmp_path,
        MCP_SOURCE,
        OAUTH_SECTION + "      grant: magic\n",
    )
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    assert manager.reload_from_path() is False
    assert manager.last_reload_error is not None


def test_oauth_failure_message_is_redacted(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE)
    provider = FakeProvider(
        ReauthorizationRequired("https://user:pass@idp.example.com rejected")
    )
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=provider,
    )
    entry = manager.apply_initial()[0]
    assert "pass" not in (entry.error or "")


# -- panel mutations, guard layer, persistence ordering ---------------------

OAUTH_URL_SECTION = """\
oauth:
  clients:
    - source: "url:https://mcp.example.com/mcp"
      issuer: https://idp.example.com
      client_id: toolregistry
      client_secret: literal-secret
      token_endpoint: https://idp.example.com/token
"""


class _NoNetwork:
    """A transport that fails the test if anything reaches it."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    def get_json(self, url, timeout):
        raise AssertionError(f"unexpected discovery request to {url}")

    def post_form(self, url, fields, headers, timeout):
        self.posts.append(url)
        raise AssertionError(f"unexpected token request to {url}")


def _seeded_provider(path, config, store, transport):
    """Build a real provider whose one declaration is already satisfied."""
    declarations = load_oauth_clients(path, config)
    return OAuthClientProvider(declarations, store=store, transport=transport)


def test_a_url_selected_source_cannot_be_moved_without_the_declaration(
    monkeypatch, tmp_path
):
    """Reject, not warn: the alternative is a silently unauthenticated source."""
    from toolregistry.config import ConfigError

    _patch_registration(monkeypatch)
    path = Path(_write(tmp_path, MCP_SOURCE, OAUTH_URL_SECTION))
    config = _config(path)
    manager = SourceManager(
        ToolRegistry(), config, config_path=path, oauth_provider=FakeProvider()
    )
    manager.apply_initial()
    key = manager.list()[0].key
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ConfigError):
        manager.replace(
            key,
            MCPSource(
                transport="streamable-http",
                url="https://other.example.com/mcp",
                namespace="example",
            ),
        )

    # Nothing moved: the registry, the config and the file are unchanged.
    assert manager.list()[0].source.url == "https://mcp.example.com/mcp"
    assert manager.config.tools[0].url == "https://mcp.example.com/mcp"
    assert path.read_text(encoding="utf-8") == before


def test_adding_a_source_that_shadows_a_namespace_selector_is_rejected(
    monkeypatch, tmp_path
):
    from toolregistry.config import ConfigError

    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    config = _config(path)
    manager = SourceManager(
        ToolRegistry(), config, config_path=path, oauth_provider=FakeProvider()
    )
    manager.apply_initial()

    with pytest.raises(ConfigError):
        manager.add(
            MCPSource(
                transport="streamable-http",
                url="https://second.example.com/mcp",
                namespace="example",
            )
        )
    assert len(manager.config.tools) == 1


def test_removing_and_re_adding_a_source_mints_nothing(monkeypatch, tmp_path):
    """A removed declaration must leave the provider, or a re-add reuses it."""
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    config = _config(path)
    store = TokenStore(":memory:")
    store.put(
        TokenRecord(
            source_id="namespace:example",
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="AT",
            expires_at=None,
        )
    )
    transport = _NoNetwork()
    provider = _seeded_provider(path, config, store, transport)
    manager = SourceManager(
        ToolRegistry(), config, config_path=path, oauth_provider=provider
    )
    manager.apply_initial()
    assert len(provider.declarations) == 1

    key = manager.list()[0].key
    manager.remove(key)
    assert provider.declarations == ()
    assert store.get("namespace:example") is None

    manager.add(
        MCPSource(
            transport="streamable-http",
            url="https://mcp.example.com/mcp",
            namespace="example",
        )
    )
    assert provider.declarations == ()
    assert transport.posts == []


def test_a_failed_write_keeps_the_token_row(monkeypatch, tmp_path):
    """A failed panel edit must not destroy a credential the file declares."""
    import toolregistry_server._config_file as config_file

    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    config = _config(path)
    store = TokenStore(":memory:")
    store.put(
        TokenRecord(
            source_id="namespace:example",
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="AT",
            expires_at=None,
        )
    )
    provider = _seeded_provider(path, config, store, _NoNetwork())
    manager = SourceManager(
        ToolRegistry(), config, config_path=path, oauth_provider=provider
    )
    manager.apply_initial()

    def refuse(*_args, **_kwargs):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(config_file, "write_config_mapping", refuse)
    result = manager.remove(manager.list()[0].key)

    assert result.persisted is False
    record = store.get("namespace:example")
    assert record is not None
    assert record.access_token == "AT"


def test_a_headers_for_failure_cannot_desynchronise_the_manager(monkeypatch, tmp_path):
    """One source must never abort a mutation or leave stale tool names."""
    from toolregistry.tool import Tool

    def fake_register(registry, source, *, oauth_headers=None):
        registry.register(
            Tool(
                name="example-one",
                description="One example tool.",
                parameters={"type": "object", "properties": {}},
                callable=lambda: "one",
            )
        )

    monkeypatch.setattr(source_manager_module, "register_mcp_source", fake_register)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    config = _config(path)
    provider = FakeProvider()
    manager = SourceManager(
        ToolRegistry(), config, config_path=path, oauth_provider=provider
    )
    manager.apply_initial()
    first = manager.list()[0]
    assert first.status is SourceStatus.APPLIED
    assert "example-one" in manager.registry._tools

    provider.error = RuntimeError("boom")
    result = manager.replace(
        first.key,
        MCPSource(
            transport="streamable-http",
            url="https://other.example.com/mcp",
            namespace="example",
        ),
    )

    assert result.entry.status is SourceStatus.FAILED
    assert result.entry.reauth_required is False
    assert "RuntimeError" in (result.entry.error or "")
    for entry in manager.list():
        for name in entry.names:
            assert name in manager.registry._tools
    assert [entry.source for entry in manager.list()] == list(manager.config.tools)


def test_a_store_failure_at_startup_is_a_failed_source(monkeypatch, tmp_path):
    """A typed store error is recorded; startup continues."""
    from toolregistry_server.oauth.upstream.provider import OAuthStoreError

    class BrokenProvider(FakeProvider):
        def headers_for(self, source):
            raise OAuthStoreError("token store unavailable")

    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        oauth_provider=BrokenProvider(),
    )

    entry = manager.apply_initial()[0]

    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is False
    assert "token store unavailable" in (entry.error or "")


def test_close_oauth_clients_drops_a_reload_created_provider():
    """Registration cannot depend on a startup provider existing."""
    from toolregistry_server.app import App

    closed: list[bool] = []

    class Provider:
        def close(self):
            closed.append(True)

    class Manager:
        oauth_provider: Provider | None = Provider()

    app = App()
    app.source_manager = Manager()
    app.oauth_client_provider = None

    app.close_oauth_clients()

    assert closed == [True]
    assert app.source_manager.oauth_provider is None


# -- declaration swap ordering and the excluded-source flag ------------------


def test_a_reload_swaps_declarations_under_the_apply_lock(tmp_path, monkeypatch):
    """#274: a reload must not interleave with a panel mutation.

    The mutation persists and then syncs declarations while holding the
    manager lock; a reload that swapped them outside that lock could leave
    the provider holding a mix of the file and the in-memory config.
    """
    _patch_registration(monkeypatch)
    path = Path(_write(tmp_path, MCP_SOURCE, OAUTH_URL_SECTION))
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)

    seen: list[bool] = []

    def recording(target, config):
        seen.append(manager._apply_lock._is_owned())

    monkeypatch.setattr(manager, "_reload_oauth_declarations", recording)

    assert manager.reload_from_path(path) is True
    assert seen == [True]


def test_excluding_a_source_clears_its_reauth_flag(tmp_path, monkeypatch):
    """#276: the panel must not offer Re-authorize for an excluded source.

    The flag is a verdict about a credential in use.  The store keeps the
    latch, so re-enabling re-derives it; the entry does not keep it.
    """
    _patch_registration(monkeypatch)
    path = Path(_write(tmp_path, MCP_SOURCE, OAUTH_URL_SECTION))
    config = _config(path)
    manager = SourceManager(
        ToolRegistry(), config, config_path=path, oauth_provider=FakeProvider()
    )
    entry = manager.apply_initial()[0]
    assert entry.status is SourceStatus.APPLIED

    # The declaration is unchanged; only its scope moves.  A credential
    # verdict may already be latched on the entry.
    flagged = dataclasses.replace(entry, reauth_required=True)
    excluded = dataclasses.replace(entry.source, enabled=False)

    updated, added = manager._apply_unchanged(
        entry.key,
        excluded,
        flagged,
        dataclasses.replace(config, tools=(excluded,)),
        {"registered": 0, "disabled": 0},
    )

    assert updated.status is SourceStatus.EXCLUDED
    assert updated.reauth_required is False
    assert added == set()


def test_apply_config_refuses_oauth_clients_without_a_provider(tmp_path):
    """A one-shot apply must not connect a protected source unauthenticated."""
    from toolregistry.config import ConfigError

    from toolregistry_server.registry_builder import apply_config

    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    with pytest.raises(ConfigError, match="oauth_provider"):
        apply_config(ToolRegistry(), _config(path))


def test_apply_config_passes_the_provider_header(monkeypatch, tmp_path):
    """With a provider, the one-shot path presents the resolved header."""
    from toolregistry_server import registry_builder
    from toolregistry_server.registry_builder import apply_config

    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    calls: list[object] = []

    def fake_register(registry, source, *, oauth_headers=None):
        calls.append(oauth_headers)

    monkeypatch.setattr(registry_builder, "register_mcp_source", fake_register)
    provider = FakeProvider()
    apply_config(ToolRegistry(), _config(path), oauth_provider=provider)
    assert calls == [{"Authorization": "Bearer AT"}]


def test_apply_config_skips_a_source_whose_token_fails(monkeypatch, tmp_path):
    """A provider failure skips the source instead of connecting without auth."""
    from toolregistry_server import registry_builder
    from toolregistry_server.registry_builder import apply_config

    path = _write(tmp_path, MCP_SOURCE, OAUTH_SECTION)
    calls: list[object] = []

    def fake_register(registry, source, *, oauth_headers=None):
        calls.append(oauth_headers)

    monkeypatch.setattr(registry_builder, "register_mcp_source", fake_register)
    provider = FakeProvider(error=OAuthClientError("offline"))
    apply_config(ToolRegistry(), _config(path), oauth_provider=provider)
    assert calls == []


def test_apply_config_without_oauth_needs_no_provider(monkeypatch, tmp_path):
    """A config with no oauth.clients keeps the old one-shot behaviour."""
    from toolregistry_server import registry_builder
    from toolregistry_server.registry_builder import apply_config

    path = _write(tmp_path, MCP_SOURCE)
    calls: list[object] = []

    def fake_register(registry, source, *, oauth_headers=None):
        calls.append(oauth_headers)

    monkeypatch.setattr(registry_builder, "register_mcp_source", fake_register)
    apply_config(ToolRegistry(), _config(path))
    assert calls == [None]


TWO_MCP_SOURCES = """\
  - type: mcp
    transport: streamable-http
    url: https://a.example.com/mcp
    namespace: alpha
  - type: mcp
    transport: streamable-http
    url: https://b.example.com/mcp
    namespace: beta
"""


def test_startup_resolves_oauth_headers_concurrently(monkeypatch, tmp_path):
    """#277: two slow token requests overlap instead of running in turn."""
    import threading

    barrier = threading.Barrier(2, timeout=5)

    class Rendezvous(FakeProvider):
        def headers_for(self, source):
            # Both calls must be in flight at once, or the barrier breaks.
            barrier.wait()
            return {"Authorization": f"Bearer {source.namespace}"}

    seen: list[str] = []

    def fake_register(registry, source, *, oauth_headers=None):
        seen.append(oauth_headers["Authorization"])

    monkeypatch.setattr(source_manager_module, "register_mcp_source", fake_register)
    path = _write(tmp_path, TWO_MCP_SOURCES)
    manager = SourceManager(
        ToolRegistry(), _config(path), config_path=path, oauth_provider=Rendezvous()
    )
    entries = manager.apply_initial()
    assert [entry.status for entry in entries] == [SourceStatus.APPLIED] * 2
    # Registration keeps declaration order.
    assert seen == ["Bearer alpha", "Bearer beta"]


def test_a_prefetch_failure_fails_only_its_source(monkeypatch, tmp_path):
    class OneFails(FakeProvider):
        def headers_for(self, source):
            if source.namespace == "alpha":
                raise TransientOAuthError("idp down")
            return {"Authorization": "Bearer AT"}

    _patch_registration(monkeypatch)
    path = _write(tmp_path, TWO_MCP_SOURCES)
    manager = SourceManager(
        ToolRegistry(), _config(path), config_path=path, oauth_provider=OneFails()
    )
    alpha, beta = manager.apply_initial()
    assert alpha.status is SourceStatus.FAILED
    assert "idp down" in (alpha.error or "")
    assert beta.status is SourceStatus.APPLIED


def test_status_reads_do_not_wait_for_a_connecting_source(monkeypatch, tmp_path):
    """#265: list() answers while a mutation is blocked on network I/O."""
    import threading

    started = threading.Event()
    release = threading.Event()

    def slow_register(registry, source, *, oauth_headers=None):
        started.set()
        assert release.wait(timeout=10)

    path = _write(tmp_path, MCP_SOURCE)
    manager = SourceManager(
        ToolRegistry(), _config(path), config_path=path, write_config=False
    )
    _patch_registration(monkeypatch)
    manager.apply_initial()
    monkeypatch.setattr(source_manager_module, "register_mcp_source", slow_register)

    new_source = dataclasses.replace(
        manager.config.tools[0], namespace="other", url="https://o.example.com/mcp"
    )
    adder = threading.Thread(target=manager.add, args=(new_source,), daemon=True)
    adder.start()
    try:
        assert started.wait(timeout=5)
        answers: list[int] = []
        reader = threading.Thread(
            target=lambda: answers.append(len(manager.list())), daemon=True
        )
        reader.start()
        reader.join(timeout=5)
        assert answers == [1]
    finally:
        release.set()
        adder.join(timeout=10)
    assert len(manager.list()) == 2


class _Scripted(FakeProvider):
    """Provider whose next headers_for outcome a test sets explicitly."""

    def __init__(self) -> None:
        super().__init__()
        self.outcome: object = {"Authorization": "Bearer AT"}

    def headers_for(self, source):
        self.seen.append(source)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _live_manager(monkeypatch, tmp_path):
    calls = _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE)
    provider = _Scripted()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        write_config=False,
        oauth_provider=provider,
    )
    (entry,) = manager.apply_initial()
    assert entry.status is SourceStatus.APPLIED
    return manager, provider, calls


def _no_probe(source, headers):
    return None


def test_revalidation_fails_a_source_whose_grant_was_revoked(monkeypatch, tmp_path):
    """#267: a definitive rejection turns a live APPLIED source FAILED."""
    manager, provider, _ = _live_manager(monkeypatch, tmp_path)
    provider.outcome = ReauthorizationRequired("invalid_grant")
    (entry,) = manager.revalidate_oauth(probe=_no_probe)
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is True


def test_revalidation_reconnects_after_a_token_rotation(monkeypatch, tmp_path):
    manager, provider, calls = _live_manager(monkeypatch, tmp_path)
    provider.outcome = {"Authorization": "Bearer NEW"}
    probed: list[object] = []
    (entry,) = manager.revalidate_oauth(probe=lambda s, h: probed.append(h))
    assert entry.status is SourceStatus.APPLIED
    assert calls[-1] == {"Authorization": "Bearer NEW"}
    # The new header is probed before the working source is torn down.
    assert probed == [{"Authorization": "Bearer NEW"}]


def test_revalidation_probe_rejection_requires_reauth(monkeypatch, tmp_path):
    manager, _, _ = _live_manager(monkeypatch, tmp_path)

    def rejected(source, headers):
        raise _status_error(401)

    (entry,) = manager.revalidate_oauth(probe=rejected)
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is True
    assert "401" in (entry.error or "")


def test_revalidation_keeps_a_source_on_a_transient_failure(monkeypatch, tmp_path):
    manager, provider, _ = _live_manager(monkeypatch, tmp_path)
    provider.outcome = TransientOAuthError("idp down")
    (entry,) = manager.revalidate_oauth(probe=_no_probe)
    assert entry.status is SourceStatus.APPLIED
    assert entry.reauth_required is False


def test_revalidation_without_oauth_does_nothing(monkeypatch, tmp_path):
    _patch_registration(monkeypatch)
    path = _write(tmp_path, MCP_SOURCE)
    manager = SourceManager(ToolRegistry(), _config(path), config_path=path)
    manager.apply_initial()

    def boom(source, headers):  # pragma: no cover - must not run
        raise AssertionError("probed a source without OAuth")

    (entry,) = manager.revalidate_oauth(probe=boom)
    assert entry.status is SourceStatus.APPLIED


def _registering_manager(monkeypatch, tmp_path, outcomes):
    """A live manager whose fake registration adds one tool per connect."""
    registered: list[object] = []

    def ping() -> str:
        """Ping."""
        return "pong"

    class _Integration:
        def __init__(self) -> None:
            self.closed = False

        def close_sync(self) -> None:
            self.closed = True

    def fake_register(registry, source, *, oauth_headers=None):
        outcome = outcomes.pop(0) if outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        registry.register(ping, namespace=source.namespace)
        integration = _Integration()
        registry._mcp_integrations.append(integration)
        registered.append(integration)

    monkeypatch.setattr(source_manager_module, "register_mcp_source", fake_register)
    path = _write(tmp_path, MCP_SOURCE)
    provider = _Scripted()
    manager = SourceManager(
        ToolRegistry(),
        _config(path),
        config_path=path,
        write_config=False,
        oauth_provider=provider,
    )
    (entry,) = manager.apply_initial()
    assert entry.status is SourceStatus.APPLIED
    return manager, provider, registered


def test_a_rotation_keeps_a_disabled_tool_disabled(monkeypatch, tmp_path):
    manager, provider, _ = _registering_manager(monkeypatch, tmp_path, [])
    (name,) = manager.list()[0].names
    manager.registry.disable(name, "operator")
    provider.outcome = {"Authorization": "Bearer NEW"}
    (entry,) = manager.revalidate_oauth(probe=_no_probe)
    assert entry.status is SourceStatus.APPLIED
    assert name in entry.names
    assert manager.registry.is_enabled(name) is False
    assert manager.registry.get_disable_reason(name) == "operator"


def test_a_rotation_closes_the_previous_connection(monkeypatch, tmp_path):
    manager, provider, registered = _registering_manager(monkeypatch, tmp_path, [])
    provider.outcome = {"Authorization": "Bearer NEW"}
    manager.revalidate_oauth(probe=_no_probe)
    first, second = registered
    assert first.closed is True
    assert first not in manager.registry._mcp_integrations
    assert second in manager.registry._mcp_integrations


def test_a_rejected_new_header_keeps_the_working_source(monkeypatch, tmp_path):
    manager, provider, registered = _registering_manager(monkeypatch, tmp_path, [])
    provider.outcome = {"Authorization": "Bearer NEW"}

    def unreachable(source, headers):
        raise OSError("connection refused")

    (entry,) = manager.revalidate_oauth(probe=unreachable)
    # An inconclusive probe never tears the live source down.
    assert entry.status is SourceStatus.APPLIED
    assert len(registered) == 1


def test_a_transient_reconnect_failure_is_retried(monkeypatch, tmp_path):
    outcomes: list[object] = [None, OSError("blip")]
    manager, provider, _ = _registering_manager(monkeypatch, tmp_path, outcomes)
    provider.outcome = {"Authorization": "Bearer NEW"}
    (entry,) = manager.revalidate_oauth(probe=_no_probe)
    assert entry.status is SourceStatus.FAILED
    assert entry.reauth_required is False
    # The next pass retries it instead of skipping a FAILED entry forever.
    (entry,) = manager.revalidate_oauth(probe=_no_probe)
    assert entry.status is SourceStatus.APPLIED


def test_a_rotation_keeps_a_runtime_override(monkeypatch, tmp_path):
    """The operator's runtime override survives a token rotation."""
    manager, provider, _ = _registering_manager(monkeypatch, tmp_path, [])

    (name,) = manager.list()[0].names

    def replacement() -> str:
        """Operator replacement."""
        return "overridden"

    manager.registry.override(name, replacement)
    provider.outcome = {"Authorization": "Bearer NEW"}
    (entry,) = manager.revalidate_oauth(probe=_no_probe)
    assert entry.status is SourceStatus.APPLIED
    assert name in entry.names
    assert manager.registry._tools[name].description == "Operator replacement."
    assert name in manager.registry.list_overrides()


def test_removing_a_source_closes_its_connection(monkeypatch, tmp_path):
    """A diff that drops a declaration closes the integration it opened."""
    manager, _, registered = _registering_manager(monkeypatch, tmp_path, [])
    (key,) = manager._mcp_integrations
    manager.remove(key)
    assert registered[0].closed is True
    assert key not in manager._mcp_integrations
    assert registered[0] not in manager.registry._mcp_integrations


def test_the_revalidator_thread_runs_a_pass_then_stops(monkeypatch, tmp_path):
    """The background thread drives revalidate_oauth and exits on stop."""
    import threading

    from toolregistry_server.source_manager import OAuthRevalidator

    manager, _, _ = _registering_manager(monkeypatch, tmp_path, [])
    ran = threading.Event()

    def pass_once(probe=None):
        ran.set()
        return manager.list()

    monkeypatch.setattr(manager, "revalidate_oauth", pass_once)
    thread = OAuthRevalidator(manager, interval=0.01)
    thread.start()
    try:
        assert ran.wait(timeout=5)
    finally:
        thread.stop(timeout=5)
    assert thread.is_alive() is False


def test_the_default_probe_presents_the_resolved_header(monkeypatch):
    """The probe opens a throwaway session carrying the token and static headers."""
    from toolregistry.integrations.mcp import client as client_module

    from toolregistry_server.source_manager import _probe_mcp_credential

    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, target, headers=None, allow_private_hosts=False):
            seen["target"] = target
            seen["headers"] = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def list_tools(self):
            return []

    monkeypatch.setattr(client_module, "MCPClient", FakeClient)
    source = MCPSource(
        transport="streamable-http",
        url="https://mcp.example.com/mcp",
        namespace="example",
        headers={"X-Static": "1"},
    )
    _probe_mcp_credential(source, {"Authorization": "Bearer AT"})
    assert seen["target"] == "https://mcp.example.com/mcp"
    assert seen["headers"] == {"X-Static": "1", "Authorization": "Bearer AT"}


def test_a_repeated_initial_apply_resets_the_bookkeeping(monkeypatch, tmp_path):
    """A second apply must not keep a key from a source that no longer registers."""
    manager, _, _ = _registering_manager(monkeypatch, tmp_path, [])
    manager._oauth_headers[("stale",)] = {"Authorization": "Bearer OLD"}
    manager._reconnect_pending.add(("stale",))
    manager._mcp_integrations[("stale",)] = object()
    manager.apply_initial()
    assert ("stale",) not in manager._oauth_headers
    assert ("stale",) not in manager._reconnect_pending
    assert ("stale",) not in manager._mcp_integrations
