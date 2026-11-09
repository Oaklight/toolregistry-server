"""Tests for hosting the core admin panel as a gateway frontend.

The admin host needs the core admin extension contract, which reached core
master ahead of the 0.21.0 release.  On a released core that predates it
these tests skip, and the ``test-core-master`` CI job runs them against core
master.

The panel runs for real on a loopback port and is reached through the
gateway ASGI application, so these tests cover the proxy, the operator
authentication split and the extension wiring end to end.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

import pytest

pytest.importorskip("toolregistry.admin.extensions")

import httpx  # noqa: E402
from toolregistry import ToolRegistry  # noqa: E402

from toolregistry_server import RouteTable  # noqa: E402
from toolregistry_server.gateway import GatewayAdapter  # noqa: E402
from toolregistry_server.gateway.admin import (  # noqa: E402
    SourceManagerHook,
    parse_source_payload,
)


def _route_table() -> RouteTable:
    registry = ToolRegistry()

    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    registry.register(add)
    return RouteTable(registry)


def _namespaced_route_table() -> RouteTable:
    registry = ToolRegistry()

    def alpha(value: int) -> int:
        """Return the value."""
        return value

    def beta(value: int) -> int:
        """Return the value."""
        return value

    registry.register(alpha, namespace="alpha")
    registry.register(beta, namespace="beta")
    return RouteTable(registry)


def _gateway(route_table: RouteTable | None = None, **kwargs) -> GatewayAdapter:
    kwargs.setdefault("tokens", ["serving-token"])
    kwargs.setdefault("admin_enabled", True)
    kwargs.setdefault("admin_token", "operator-token")
    return GatewayAdapter.create(route_table or _route_table(), **kwargs)


def _client(gateway: GatewayAdapter) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app),
        base_url="http://test",
    )


_OPERATOR = {"Authorization": "Bearer operator-token"}


class TestHostedPanel:
    """The panel answers under its frontend path, and only with the token."""

    async def test_admin_mount_redirects_to_the_trailing_slash(self):
        gateway = _gateway()
        try:
            async with _client(gateway) as client:
                response = await client.get("/admin")
        finally:
            await gateway.shutdown()
        assert response.status_code == 308
        assert response.headers["location"] == "/admin/"

    async def test_panel_shell_and_assets_are_served(self):
        gateway = _gateway()
        try:
            async with _client(gateway) as client:
                shell = await client.get("/admin/")
                asset = await client.get("/admin/assets/js/core/state.js")
        finally:
            await gateway.shutdown()
        assert shell.status_code == 200
        assert "text/html" in shell.headers["content-type"]
        assert "assets/js/core/state.js" in shell.text
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]

    async def test_panel_requires_the_operator_token(self):
        gateway = _gateway()
        try:
            async with _client(gateway) as client:
                missing = await client.get("/admin/api/info")
                serving = await client.get(
                    "/admin/api/info",
                    headers={"Authorization": "Bearer serving-token"},
                )
                operator = await client.get("/admin/api/info", headers=_OPERATOR)
        finally:
            await gateway.shutdown()
        assert missing.status_code == 401
        assert serving.status_code == 401
        assert operator.status_code == 200

    async def test_tool_frontends_keep_their_serving_token(self):
        gateway = _gateway()
        try:
            async with _client(gateway) as client:
                response = await client.get(
                    "/openapi/tools",
                    headers={"Authorization": "Bearer serving-token"},
                )
        finally:
            await gateway.shutdown()
        assert response.status_code == 200

    async def test_disabling_admin_at_runtime_answers_404(self):
        gateway = _gateway()
        try:
            async with _client(gateway) as client:
                before = await client.get("/admin/", headers=_OPERATOR)
                gateway.update_frontend("admin", enabled=False)
                disabled = await client.get("/admin/", headers=_OPERATOR)
                gateway.update_frontend("admin", enabled=True)
                again = await client.get("/admin/", headers=_OPERATOR)
        finally:
            await gateway.shutdown()
        assert before.status_code == 200
        assert disabled.status_code == 404
        assert "disabled" in disabled.json()["detail"]
        assert again.status_code == 200

    async def test_admin_path_change_applies_at_runtime(self):
        gateway = _gateway()
        try:
            async with _client(gateway) as client:
                gateway.update_frontend("admin", path="/panel")
                old = await client.get("/admin/", headers=_OPERATOR)
                new = await client.get("/panel/", headers=_OPERATOR)
        finally:
            await gateway.shutdown()
        assert old.status_code == 404
        assert new.status_code == 200

    async def test_shutdown_stops_the_panel_listener(self):
        gateway = _gateway()
        async with _client(gateway) as client:
            assert (await client.get("/admin/")).status_code == 200
        assert gateway._admin_host.running is True
        await gateway.shutdown()
        assert gateway._admin_host.running is False


class TestGatewayExtensionPages:
    """The gateway tabs run against the live objects."""

    async def test_keys_tab_lists_and_creates_with_a_real_store(self, tmp_path):
        pytest.importorskip("toolregistry_server.keystore")
        gateway = _gateway(keys_db=str(tmp_path / "keys.db"))
        try:
            async with _client(gateway) as client:
                empty = await client.get("/admin/ext/gateway/keys", headers=_OPERATOR)
                created = await client.post(
                    "/admin/ext/gateway/keys",
                    headers=_OPERATOR,
                    json={"label": "ci", "scopes": ["calculator"]},
                )
                listed = await client.get("/admin/ext/gateway/keys", headers=_OPERATOR)
        finally:
            await gateway.shutdown()
        assert empty.json() == {"keys": []}
        assert created.status_code == 201
        assert created.headers["cache-control"] == "no-store"
        assert created.json()["label"] == "ci"
        assert created.json()["secret"]
        assert "secret" not in json.dumps(listed.json())
        assert listed.json()["keys"][0]["label"] == "ci"

    async def test_frontends_tab_updates_the_registry(self):
        gateway = _gateway()
        try:
            async with _client(gateway) as client:
                listed = await client.get(
                    "/admin/ext/gateway/frontends", headers=_OPERATOR
                )
                updated = await client.patch(
                    "/admin/ext/gateway/frontends/mcp",
                    headers=_OPERATOR,
                    json={"enabled": False},
                )
                disabled = await client.get("/mcp", headers=_OPERATOR)
        finally:
            await gateway.shutdown()
        names = [record["name"] for record in listed.json()["frontends"]]
        assert names == ["openapi", "mcp", "sse", "admin"]
        assert updated.status_code == 200
        assert updated.json()["frontend"]["enabled"] is False
        assert disabled.status_code == 404

    async def test_sources_page_is_503_without_a_source_manager(self):
        gateway = _gateway()
        try:
            async with _client(gateway) as client:
                response = await client.get(
                    "/admin/ext/gateway/sources", headers=_OPERATOR
                )
        finally:
            await gateway.shutdown()
        assert response.status_code == 503

    async def test_sources_page_reads_the_live_manager(self, tmp_path):
        from toolregistry_server import App

        config = tmp_path / "tools.yaml"
        config.write_text("tools: []\n", encoding="utf-8")
        app = App()
        gateway = app.create_gateway(
            config_path=str(config),
            tokens=["serving-token"],
            admin_enabled=True,
            admin_token="operator-token",
        )
        try:
            async with _client(gateway) as client:
                response = await client.get(
                    "/admin/ext/gateway/sources", headers=_OPERATOR
                )
        finally:
            await gateway.shutdown()
        assert response.status_code == 200
        assert response.json() == {"sources": []}


class TestKeyEnforcement:
    """Scoped keys work through the gateway's own resolver wiring."""

    async def test_static_operator_token_lists_and_calls_tools(self, tmp_path):
        pytest.importorskip("toolregistry_server.keystore")
        gateway = _gateway(keys_db=str(tmp_path / "keys.db"))
        route = gateway._route_table.list_routes()[0]
        try:
            static = {"Authorization": "Bearer serving-token"}
            async with _client(gateway) as client:
                listed = await client.get("/openapi/tools", headers=static)
                called = await client.post(
                    f"/openapi{route.path}",
                    headers=static,
                    json={"a": 1, "b": 2},
                )
        finally:
            await gateway.shutdown()
        assert listed.status_code == 200
        assert listed.json()["tools"]
        assert called.status_code == 200
        assert called.json() == 3

    async def test_scoped_key_sees_only_its_namespace(self, tmp_path):
        pytest.importorskip("toolregistry_server.keystore")
        gateway = _gateway(_namespaced_route_table(), keys_db=str(tmp_path / "keys.db"))
        routes = {
            route.namespace: route for route in gateway._route_table.list_routes()
        }
        try:
            async with _client(gateway) as client:
                created = await client.post(
                    "/admin/ext/gateway/keys",
                    headers=_OPERATOR,
                    json={"label": "alpha-bot", "scopes": ["alpha"]},
                )
                secret = created.json()["secret"]
                headers = {"Authorization": f"Bearer {secret}"}
                listed = await client.get("/openapi/tools", headers=headers)
                allowed = await client.post(
                    f"/openapi{routes['alpha'].path}",
                    headers=headers,
                    json={"value": 7},
                )
                denied = await client.post(
                    f"/openapi{routes['beta'].path}",
                    headers=headers,
                    json={"value": 7},
                )
        finally:
            await gateway.shutdown()
        assert created.status_code == 201
        names = {tool["name"] for tool in listed.json()["tools"]}
        assert names == {routes["alpha"].tool_name}
        assert allowed.status_code == 200
        assert allowed.json() == 7
        assert denied.status_code == 403

    async def test_created_key_authenticates_and_revoke_applies(self, tmp_path):
        pytest.importorskip("toolregistry_server.keystore")
        gateway = _gateway(keys_db=str(tmp_path / "keys.db"))
        try:
            async with _client(gateway) as client:
                created = await client.post(
                    "/admin/ext/gateway/keys",
                    headers=_OPERATOR,
                    json={"label": "bot", "scopes": ["*"]},
                )
                secret = created.json()["secret"]
                headers = {"Authorization": f"Bearer {secret}"}
                accepted = await client.get("/openapi/tools", headers=headers)
                revoked = await client.post(
                    f"/admin/ext/gateway/keys/{created.json()['id']}/revoke",
                    headers=_OPERATOR,
                    json={},
                )
                refused = await client.get("/openapi/tools", headers=headers)
        finally:
            await gateway.shutdown()
        assert created.status_code == 201
        assert accepted.status_code == 200
        assert revoked.status_code == 200
        assert refused.status_code == 401

    async def test_metadata_is_served_in_keys_only_mode(self, tmp_path):
        pytest.importorskip("toolregistry_server.keystore")
        gateway = _gateway(tokens=[], keys_db=str(tmp_path / "keys.db"))
        try:
            async with _client(gateway) as client:
                response = await client.get("/.well-known/oauth-protected-resource/mcp")
                mcp_operator = await client.get(
                    "/mcp", headers={"Authorization": "Bearer serving-token"}
                )
        finally:
            await gateway.shutdown()
        assert response.status_code == 200
        assert response.json()["resource"].endswith("/mcp")
        # No static token is configured in this mode; the key resolver
        # refuses the credential before the MCP frontend runs.
        assert mcp_operator.status_code == 401

    async def test_static_token_reaches_mcp_when_keys_are_enabled(self, tmp_path):
        pytest.importorskip("toolregistry_server.keystore")
        gateway = _gateway(keys_db=str(tmp_path / "keys.db"))
        try:
            async with _client(gateway) as client:
                authorized = await client.get(
                    "/mcp", headers={"Authorization": "Bearer serving-token"}
                )
                anonymous = await client.get("/mcp")
        finally:
            await gateway.shutdown()
        # The frontend is reached (and reports the missing session manager);
        # 404 would mean the operator token was not accepted.
        assert authorized.status_code == 503
        assert anonymous.status_code == 401


class TestStartupFailure:
    """A contract violation fails startup before requests are served."""

    async def test_extension_contract_violation_fails_startup(self, monkeypatch):
        gateway = _gateway()

        def _fail():
            raise RuntimeError("extension contract violation")

        monkeypatch.setattr(gateway._admin_host, "_build_server", _fail)
        with pytest.raises(RuntimeError, match="contract violation"):
            await gateway.startup()
        assert gateway._admin_host.running is False

    def test_disabled_admin_is_not_loaded(self):
        gateway = _gateway(admin_enabled=False)
        assert gateway._admin_host.running is False


class TestParseSourcePayload:
    """The panel declaration becomes a core source object."""

    def test_openapi(self):
        source = parse_source_payload(
            {"type": "openapi", "url": "http://example.test/spec.json"}
        )
        assert source.url == "http://example.test/spec.json"

    def test_openapi_core_panel_kind_and_spec_url(self):
        source = parse_source_payload(
            {"kind": "openapi", "spec_url": "http://example.test/spec.json"}
        )
        assert source.url == "http://example.test/spec.json"

    def test_mcp_defaults_to_streamable_http(self):
        source = parse_source_payload({"type": "mcp", "url": "http://example.test/mcp"})
        assert source.transport == "streamable-http"

    def test_mcp_command_defaults_to_stdio(self):
        source = parse_source_payload({"type": "mcp", "command": "server --flag"})
        assert source.transport == "stdio"
        assert source.command == ("server", "--flag")

    def test_python_module_and_object(self):
        source = parse_source_payload(
            {"type": "python", "module": "pkg.tools", "object": "Tools"}
        )
        assert source.class_path == "pkg.tools.Tools"

    @pytest.mark.parametrize(
        "payload",
        [
            {"type": "grpc"},
            {"type": "openapi"},
            {"type": "openapi", "url": "http://x", "unknown": 1},
            {"type": "mcp"},
            {"type": "python"},
        ],
    )
    def test_rejects_invalid_declarations(self, payload):
        with pytest.raises(ValueError):
            parse_source_payload(payload)

    def test_rejects_basic_auth_that_cannot_be_persisted(self):
        with pytest.raises(ValueError, match="cannot be stored"):
            parse_source_payload(
                {
                    "type": "openapi",
                    "url": "http://example.test/spec.json",
                    "auth": {"type": "basic"},
                }
            )


@dataclasses.dataclass
class _FakeEntry:
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "src_1",
            "kind": "openapi",
            "detail": "http://example.test/spec.json",
            "namespace": None,
            "status": "applied",
            "last_error": None,
        }


@dataclasses.dataclass
class _FakeMutation:
    entry: _FakeEntry
    persisted: bool = True
    persist_error: str | None = None
    removed: bool = False


class _FakeSourceManager:
    """The subset of ``SourceManager`` the source hook uses."""

    config_path = "/tmp/tools.yaml"
    write_config = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.persisted = True

    def add(self, source):
        self.calls.append(("add", source))
        return _FakeMutation(_FakeEntry(), persisted=self.persisted)

    def remove(self, ref):
        self.calls.append(("remove", ref))
        return _FakeMutation(_FakeEntry(), persisted=self.persisted, removed=True)

    def replace(self, ref, source):
        self.calls.append(("replace", (ref, source)))
        return _FakeMutation(_FakeEntry(), persisted=self.persisted)

    def to_dicts(self):
        return ({"id": "src_1", "status": "applied"},)


@dataclasses.dataclass
class _Change:
    action: str
    payload: Mapping[str, Any]
    namespace: str | None = None
    selected: tuple[str, ...] | None = None
    source_id: str | None = None
    kind: str = "openapi"


class TestSourceManagerHook:
    """The core panel's source hook goes through the manager."""

    def test_add_persists_through_the_manager(self):
        manager = _FakeSourceManager()
        hook = SourceManagerHook(manager)
        record = hook.persist(
            _Change(
                action="add",
                payload={
                    "kind": "openapi",
                    "spec_url": "http://example.test/spec.json",
                },
            )
        )
        assert record["id"] == "src_1"
        assert manager.calls[0][0] == "add"
        assert manager.calls[0][1].url == "http://example.test/spec.json"

    def test_failed_write_is_reported(self):
        from toolregistry.admin.source_hook import SourcePersistenceError

        manager = _FakeSourceManager()
        manager.persisted = False
        hook = SourceManagerHook(manager)
        with pytest.raises(SourcePersistenceError):
            hook.persist(
                _Change(
                    action="add",
                    payload={"kind": "openapi", "spec_url": "http://x"},
                )
            )

    def test_writable_and_statuses_reflect_the_manager(self):
        manager = _FakeSourceManager()
        hook = SourceManagerHook(manager)
        assert hook.writable() is True
        assert hook.statuses() == ({"id": "src_1", "status": "applied"},)
        manager.write_config = False
        assert hook.writable() is False
