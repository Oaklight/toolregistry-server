"""Integration tests for the gateway ASGI application."""

import contextlib

import httpx
import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.gateway import GatewayAdapter


def _route_table():
    registry = ToolRegistry()

    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    registry.register(add)
    return RouteTable(registry)


def _gateway(**kwargs):
    kwargs.setdefault("tokens", [])
    return GatewayAdapter.create(_route_table(), **kwargs)


def _client(gateway):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app),
        base_url="http://test",
    )


class TestListFrontends:
    """The public record has a stable shape."""

    def test_defaults(self):
        gateway = _gateway()
        assert gateway.list_frontends() == [
            {
                "name": "openapi",
                "path": "/openapi",
                "enabled": True,
                "transport": "http",
            },
            {
                "name": "mcp",
                "path": "/mcp",
                "enabled": True,
                "transport": "streamable-http",
            },
            {
                "name": "sse",
                "path": "/sse",
                "enabled": True,
                "transport": "sse",
            },
            {
                "name": "admin",
                "path": "/admin",
                "enabled": False,
                "transport": "http",
            },
        ]

    def test_update_reflects_in_list(self):
        gateway = _gateway()
        gateway.update_frontend("mcp", enabled=False)
        record = next(
            item for item in gateway.list_frontends() if item["name"] == "mcp"
        )
        assert record["enabled"] is False


class TestGatewayRouting:
    """One process answers on each configured path."""

    async def test_openapi_frontend_is_served(self):
        gateway = _gateway()
        async with _client(gateway) as client:
            response = await client.get("/openapi/tools")
        assert response.status_code == 200
        body = response.json()
        assert "tools" in body
        assert "etag" in body

    async def test_openapi_tool_call_is_served(self):
        gateway = _gateway()
        route = gateway._route_table.list_routes()[0]
        async with _client(gateway) as client:
            response = await client.post(f"/openapi{route.path}", json={"a": 1, "b": 2})
        assert response.status_code == 200
        assert response.json() == 3

    async def test_unknown_path_returns_json_404(self):
        gateway = _gateway()
        async with _client(gateway) as client:
            response = await client.get("/nope")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}

    async def test_path_prefix_does_not_match_partial_segment(self):
        gateway = _gateway()
        async with _client(gateway) as client:
            response = await client.get("/openapix/tools")
        assert response.status_code == 404


class TestPerRequestEnableFlag:
    """A disabled frontend is a route-level 404."""

    async def test_disabled_frontend_returns_404(self):
        gateway = _gateway()
        gateway.update_frontend("mcp", enabled=False)
        async with _client(gateway) as client:
            response = await client.get("/mcp")
        assert response.status_code == 404
        assert "disabled" in response.json()["detail"]

    async def test_disabled_frontend_does_not_touch_the_registry(self, monkeypatch):
        gateway = _gateway()
        gateway.update_frontend("openapi", enabled=False)

        def _fail(*args, **kwargs):
            raise AssertionError("registry must not be read for a disabled frontend")

        monkeypatch.setattr(gateway._route_table, "list_routes", _fail)
        async with _client(gateway) as client:
            response = await client.get("/openapi/tools")
        assert response.status_code == 404

    async def test_enable_again_reaches_the_frontend(self):
        gateway = _gateway()
        gateway.update_frontend("mcp", enabled=False)
        async with _client(gateway) as client:
            disabled = await client.get("/mcp")
            gateway.update_frontend("mcp", enabled=True)
            enabled = await client.get("/mcp")
        assert disabled.status_code == 404
        # The session manager is not started in this test, so the request
        # reaches the frontend and it reports that fact.  A 404 here would
        # mean the enable flag was not re-evaluated.
        assert enabled.status_code == 503
        assert "not running" in enabled.json()["detail"]
        assert gateway._session_manager is None

    async def test_admin_is_disabled_by_default(self):
        gateway = _gateway()
        async with _client(gateway) as client:
            response = await client.get("/admin/")
        assert response.status_code == 404
        assert "disabled" in response.json()["detail"]

    def test_enabling_admin_without_a_token_fails(self):
        gateway = _gateway()
        with pytest.raises(ValueError, match="operator token"):
            gateway.update_frontend("admin", enabled=True)

    def test_enabled_admin_without_a_token_fails_at_construction(self):
        with pytest.raises(ValueError, match="operator token"):
            _gateway(admin_enabled=True)

    async def test_path_change_applies_without_restart(self):
        gateway = _gateway()
        gateway.update_frontend("openapi", path="/api")
        async with _client(gateway) as client:
            old = await client.get("/openapi/tools")
            new = await client.get("/api/tools")
        assert old.status_code == 404
        assert new.status_code == 200


class TestSharedAuth:
    """One token set protects every frontend."""

    async def test_openapi_requires_a_token(self):
        gateway = _gateway(tokens=["secret"])
        async with _client(gateway) as client:
            missing = await client.get("/openapi/tools")
            invalid = await client.get(
                "/openapi/tools",
                headers={"Authorization": "Bearer wrong"},
            )
            valid = await client.get(
                "/openapi/tools",
                headers={"Authorization": "Bearer secret"},
            )
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert invalid.status_code == 401
        assert valid.status_code == 200

    async def test_mcp_and_sse_require_the_same_token(self):
        gateway = _gateway(tokens=["secret"])
        async with _client(gateway) as client:
            mcp = await client.get("/mcp")
            sse = await client.get("/sse")
        assert mcp.status_code == 401
        assert sse.status_code == 401

    async def test_metadata_is_served_without_a_token(self):
        gateway = _gateway(tokens=["secret"], host="127.0.0.1", port=9000)
        async with _client(gateway) as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 200
        body = response.json()
        assert body["resource"] == "http://127.0.0.1:9000/mcp"
        assert body["authorization_servers"] == ["http://127.0.0.1:9000"]
        assert body["scopes_supported"] == ["mcp"]

    async def test_metadata_follows_the_current_mcp_path(self):
        gateway = _gateway(tokens=["secret"], host="127.0.0.1", port=9000)
        gateway.update_frontend("mcp", path="/rpc")
        async with _client(gateway) as client:
            old = await client.get("/.well-known/oauth-protected-resource/mcp")
            new = await client.get("/.well-known/oauth-protected-resource/rpc")
        assert old.status_code == 404
        assert new.status_code == 200
        assert new.json()["resource"] == "http://127.0.0.1:9000/rpc"

    async def test_metadata_is_absent_without_tokens(self):
        gateway = _gateway()
        async with _client(gateway) as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 404

    async def test_metadata_404_when_mcp_is_disabled(self):
        gateway = _gateway(tokens=["secret"])
        gateway.update_frontend("mcp", enabled=False)
        async with _client(gateway) as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 404

    async def test_metadata_is_omitted_on_a_unix_socket_without_a_server_url(self):
        """A UDS hub has no TCP address to advertise (#145)."""
        gateway = _gateway(tokens=["secret"], host="127.0.0.1", port=9000)
        gateway._socket_path = "/tmp/hub.sock"
        async with _client(gateway) as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 404

    async def test_a_server_url_restores_metadata_on_a_unix_socket(self):
        gateway = _gateway(
            tokens=["secret"],
            host="127.0.0.1",
            port=9000,
            server_url="http://127.0.0.1:9000/mcp",
        )
        gateway._socket_path = "/tmp/hub.sock"
        async with _client(gateway) as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 200
        assert response.json()["resource"] == "http://127.0.0.1:9000/mcp"


class TestBackendSelection:
    """Only the ASGI backend can mount multiple frontends."""

    def test_httpserver_rejects_multiple_frontends(self):
        with pytest.raises(ValueError, match="ASGI backend") as exc_info:
            _gateway(backend="httpserver")
        assert "fastapi" in str(exc_info.value)

    def test_httpserver_rejects_an_openapi_path_override(self):
        """httpserver serves at the listener root, so a mount path is a lie."""
        with pytest.raises(ValueError, match="listener root") as exc_info:
            _gateway(
                backend="httpserver",
                openapi_path="/api",
                mcp_enabled=False,
                sse_enabled=False,
            )
        assert "/api" in str(exc_info.value)

    def test_httpserver_allows_openapi_only(self):
        gateway = _gateway(
            backend="httpserver",
            openapi_enabled=True,
            mcp_enabled=False,
            sse_enabled=False,
        )
        assert gateway.list_frontends()[0]["enabled"] is True
        assert gateway.list_frontends()[1]["enabled"] is False
        assert gateway.list_frontends()[2]["enabled"] is False

    def test_httpserver_rejects_enabling_mcp_at_runtime(self):
        gateway = _gateway(
            backend="httpserver",
            openapi_enabled=True,
            mcp_enabled=False,
            sse_enabled=False,
        )
        with pytest.raises(ValueError, match="only the openapi"):
            gateway.update_frontend("mcp", enabled=True)

    def test_httpserver_cannot_disable_openapi_at_runtime(self):
        gateway = _gateway(
            backend="httpserver",
            openapi_enabled=True,
            mcp_enabled=False,
            sse_enabled=False,
        )
        with pytest.raises(ValueError, match="cannot disable"):
            gateway.update_frontend("openapi", enabled=False)


class TestHttpserverFrontendRecord:
    """The reported path must be one the listener actually serves."""

    def _gateway(self):
        return _gateway(
            backend="httpserver",
            openapi_enabled=True,
            mcp_enabled=False,
            sse_enabled=False,
        )

    def _serves(self, app, path):
        # The vendored App has no public route listing, so match the
        # compiled patterns the request handler dispatches against.
        return any(entry.pattern.fullmatch(path) for entry in app._routes)

    def test_reports_the_listener_root(self):
        record = self._gateway().list_frontends()[0]
        assert record["name"] == "openapi"
        assert record["path"] == "/"
        assert "listener root" in record["note"]

    def test_the_reported_path_prefixes_the_served_routes(self):
        gateway = self._gateway()
        prefix = gateway.list_frontends()[0]["path"].rstrip("/")
        for route in ("/tools", "/openapi.json", "/docs", "/events"):
            assert self._serves(gateway.app, f"{prefix}{route}"), route

    def test_the_configured_mount_path_is_not_served(self):
        gateway = self._gateway()
        assert not self._serves(gateway.app, "/openapi/tools")

    def test_saving_the_reported_record_is_accepted(self):
        gateway = self._gateway()
        result = gateway.update_frontend("openapi", path="/", enabled=True)
        assert result["path"] == "/"
        assert result["note"] == gateway.list_frontends()[0]["note"]
        assert result["enabled"] is True


class TestSourceManagerCoexistence:
    """The gateway keeps runtime source management wired up."""

    def test_create_gateway_attaches_the_source_manager(self, tmp_path, monkeypatch):
        from toolregistry_server import App

        config = tmp_path / "tools.yaml"
        config.write_text("tools: []\n", encoding="utf-8")
        monkeypatch.setattr(GatewayAdapter, "_build_mcp_server", lambda self: object())

        app = App()
        gateway = app.create_gateway(config_path=str(config), tokens=[])

        assert app.gateway is gateway
        assert app.source_manager is not None
        assert app.source_manager.route_table is gateway._route_table


class _FakeSessionManager:
    """Records the lifecycle calls the gateway makes."""

    instances: list["_FakeSessionManager"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        type(self).instances.append(self)

    @contextlib.asynccontextmanager
    async def run(self):
        self.started = True
        try:
            yield
        finally:
            self.closed = True


class TestSessionManagerLifecycle:
    """One manager per MCP mount, created at startup and closed at shutdown."""

    async def test_startup_creates_one_manager_and_shutdown_closes_it(
        self, monkeypatch
    ):
        _FakeSessionManager.instances = []
        monkeypatch.setattr(
            "mcp.server.streamable_http_manager.StreamableHTTPSessionManager",
            _FakeSessionManager,
        )
        gateway = _gateway()

        await gateway.startup()
        assert len(_FakeSessionManager.instances) == 1
        manager = _FakeSessionManager.instances[0]
        assert manager.started is True

        # A second startup in the same lifespan must not create another
        # manager for the one MCP mount.
        await gateway.startup()
        assert len(_FakeSessionManager.instances) == 1

        await gateway.shutdown()
        assert manager.closed is True

        # Shutdown is idempotent.
        await gateway.shutdown()
        assert manager.closed is True
