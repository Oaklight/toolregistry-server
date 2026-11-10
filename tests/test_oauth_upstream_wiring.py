"""Tests for wiring upstream OAuth into the MCP source path (#254, stage 1)."""

from __future__ import annotations

import builtins
import dataclasses
import importlib
import sys
import textwrap
import types

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
from toolregistry_server.oauth.upstream.store import TokenRecord
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
