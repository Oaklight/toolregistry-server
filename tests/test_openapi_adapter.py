"""Tests for the OpenAPI adapter module."""

import pytest
from toolregistry import ToolRegistry
from toolregistry.tool import Tool, ToolMetadata

from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import create_openapi_app
from toolregistry_server.adapters.openapi.adapter import (
    _validate_arguments,
    add_tools_endpoint,
    route_table_to_router,
    setup_dynamic_openapi,
)
from toolregistry_server.adapters.openapi.middleware import add_etag_middleware

# ============== Fixtures ==============


@pytest.fixture
def registry() -> ToolRegistry:
    """Create a ToolRegistry with sample tools."""
    reg = ToolRegistry()

    @reg.register
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    @reg.register
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @reg.register
    async def async_greet(name: str) -> str:
        """Async greeting."""
        return f"Hello async, {name}!"

    return reg


@pytest.fixture
def route_table(registry: ToolRegistry) -> RouteTable:
    """Create a RouteTable from the registry."""
    return RouteTable(registry)


# ============== Request Validation Tests ==============


class TestValidateArguments:
    """Bodies are validated against the tool's own schema, not a model."""

    def test_signature_tool_coerces_and_drops_unknown_keys(self, route_table):
        route = route_table.get_route("add")
        args = _validate_arguments(route, {"a": "2", "b": 3, "extra": True})
        assert args == {"a": 2, "b": 3}

    def test_signature_tool_rejects_a_bad_value(self, route_table):
        from fastapi import HTTPException

        route = route_table.get_route("add")
        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, {"a": "not-a-number", "b": 1})
        assert excinfo.value.status_code == 422
        detail = excinfo.value.detail
        assert isinstance(detail, list) and detail
        assert detail[0]["loc"][0] == "body"

    def test_proxy_tool_uses_json_schema(self, route_table):
        from fastapi import HTTPException
        from toolregistry.tool import Tool

        def proxy(**kwargs):
            return kwargs

        tool = Tool(
            name="proxy",
            description="proxy tool",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            callable=proxy,
        )
        route_table._registry.register(tool)  # noqa: SLF001 - test fixture
        route_table.refresh_all()
        route = route_table.get_route("proxy")

        assert _validate_arguments(route, {"q": "ok", "junk": 1}) == {"q": "ok"}
        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, {})
        assert excinfo.value.status_code == 422


# ============== Router Generation Tests ==============
# ============== Router Generation Tests ==============


class TestRouteTableToRouter:
    """Tests for route_table_to_router function."""

    def test_creates_router(self, route_table: RouteTable):
        """Test that router is created successfully."""
        from fastapi import APIRouter

        router = route_table_to_router(route_table)
        assert isinstance(router, APIRouter)

    def test_router_has_routes(self, route_table: RouteTable):
        """Test that router has routes for all tools."""
        router = route_table_to_router(route_table)
        # Should have routes for greet, add, and async_greet
        assert len(router.routes) >= 3

    def test_router_with_prefix(self, route_table: RouteTable):
        """Test router with custom prefix."""
        router = route_table_to_router(route_table, prefix="/api")
        assert router.prefix == "/api"


# ============== App Creation Tests ==============


class TestCreateOpenAPIApp:
    """Tests for create_openapi_app function."""

    def test_creates_app(self, route_table: RouteTable):
        """Test that app is created successfully."""
        from fastapi import FastAPI

        app = create_openapi_app(route_table)
        assert isinstance(app, FastAPI)

    def test_app_with_custom_metadata(self, route_table: RouteTable):
        """Test app with custom title and version."""
        app = create_openapi_app(
            route_table,
            title="Test API",
            version="2.0.0",
            description="Test description",
        )
        assert app.title == "Test API"
        assert app.version == "2.0.0"
        assert app.description == "Test description"


# ============== Integration Tests ==============


class TestOpenAPIIntegration:
    """Integration tests using TestClient."""

    def test_sync_endpoint(self, route_table: RouteTable):
        """Test calling a sync endpoint."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        response = client.post(
            "/tools/default/add",
            json={"a": 2, "b": 3},
        )
        assert response.status_code == 200
        assert response.json() == 5

    def test_async_endpoint(self, route_table: RouteTable):
        """Test calling an async endpoint."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        response = client.post(
            "/tools/default/async_greet",
            json={"name": "World"},
        )
        assert response.status_code == 200
        assert response.json() == "Hello async, World!"

    def test_disabled_tool_returns_503(self, route_table: RouteTable):
        """Test that disabled tools return 503."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        # Disable the tool
        route_table.disable("add", reason="Testing")

        response = client.post(
            "/tools/default/add",
            json={"a": 2, "b": 3},
        )
        assert response.status_code == 503
        assert "disabled" in response.json()["detail"].lower()

    def test_openapi_schema_excludes_disabled(self, route_table: RouteTable):
        """Test that OpenAPI schema excludes disabled tools."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        # Get schema before disabling
        response = client.get("/openapi.json")
        schema_before = response.json()
        assert "/tools/default/add" in schema_before["paths"]

        # Disable the tool
        route_table.disable("add", reason="Testing")

        # Get schema after disabling
        response = client.get("/openapi.json")
        schema_after = response.json()
        assert "/tools/default/add" not in schema_after["paths"]


# ============== Dynamic OpenAPI Tests ==============


class TestDynamicOpenAPI:
    """Tests for setup_dynamic_openapi function."""

    def test_dynamic_schema_updates(self, route_table: RouteTable):
        """Test that schema updates dynamically."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from toolregistry_server.adapters.openapi.adapter import route_table_to_router

        app = FastAPI()
        router = route_table_to_router(route_table)
        app.include_router(router)
        setup_dynamic_openapi(app, route_table)

        client = TestClient(app)

        # Initially all tools visible
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        assert "/tools/default/greet" in paths
        assert "/tools/default/add" in paths

        # Disable one tool
        route_table.disable("greet")

        # Schema should update
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        assert "/tools/default/greet" not in paths
        assert "/tools/default/add" in paths

        # Re-enable
        route_table.enable("greet")

        # Schema should update again
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        assert "/tools/default/greet" in paths


# ============== Tools Endpoint Tests ==============


class TestToolsEndpoint:
    """Tests for the /tools endpoint."""

    def test_tools_endpoint_returns_list(self, route_table: RouteTable):
        """Test that /tools endpoint returns a list of tools."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        response = client.get("/tools")
        assert response.status_code == 200

        data = response.json()
        assert "tools" in data
        assert "etag" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) == 3  # greet, add, async_greet

    def test_tools_endpoint_tool_structure(self, route_table: RouteTable):
        """Test that each tool has the expected structure."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        response = client.get("/tools")
        data = response.json()

        for tool in data["tools"]:
            assert "name" in tool
            assert "namespace" in tool
            assert "method" in tool
            assert "path" in tool
            assert "description" in tool

    def test_tools_endpoint_etag_header(self, route_table: RouteTable):
        """Test that /tools endpoint returns ETag header."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        response = client.get("/tools")
        assert response.status_code == 200
        assert "ETag" in response.headers

        # ETag in header should match etag in body
        data = response.json()
        assert response.headers["ETag"] == data["etag"]

    def test_tools_endpoint_conditional_request(self, route_table: RouteTable):
        """Test that /tools endpoint supports If-None-Match."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        # First request to get ETag
        response1 = client.get("/tools")
        assert response1.status_code == 200
        etag = response1.headers["ETag"]

        # Second request with If-None-Match
        response2 = client.get("/tools", headers={"If-None-Match": etag})
        assert response2.status_code == 304

    def test_tools_endpoint_etag_changes_on_disable(self, route_table: RouteTable):
        """Test that ETag changes when a tool is disabled."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        # Get initial ETag
        response1 = client.get("/tools")
        etag1 = response1.headers["ETag"]

        # Disable a tool
        route_table.disable("add")

        # Get new ETag
        response2 = client.get("/tools")
        etag2 = response2.headers["ETag"]

        # ETags should be different
        assert etag1 != etag2

    def test_tools_endpoint_excludes_disabled(self, route_table: RouteTable):
        """Test that /tools endpoint excludes disabled tools."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        # Initially all tools visible
        response1 = client.get("/tools")
        tools1 = response1.json()["tools"]
        tool_names1 = {t["name"] for t in tools1}
        assert "add" in tool_names1

        # Disable a tool
        route_table.disable("add")

        # Tool should be excluded
        response2 = client.get("/tools")
        tools2 = response2.json()["tools"]
        tool_names2 = {t["name"] for t in tools2}
        assert "add" not in tool_names2


# ============== ETag Middleware Tests ==============


class TestETagMiddleware:
    """Tests for ETag middleware."""

    def test_etag_middleware_adds_header(self, route_table: RouteTable):
        """Test that ETag middleware adds ETag header to responses."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table, enable_etag=True)
        client = TestClient(app)

        response = client.get("/tools")
        assert "ETag" in response.headers

    def test_etag_middleware_304_on_match(self, route_table: RouteTable):
        """Test that middleware returns 304 when ETag matches."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table, enable_etag=True)
        client = TestClient(app)

        # Get ETag
        response1 = client.get("/tools")
        etag = response1.headers["ETag"]

        # Request with matching If-None-Match
        response2 = client.get("/tools", headers={"If-None-Match": etag})
        assert response2.status_code == 304

    def test_etag_middleware_200_on_mismatch(self, route_table: RouteTable):
        """Test that middleware returns 200 when ETag doesn't match."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table, enable_etag=True)
        client = TestClient(app)

        # Request with non-matching If-None-Match
        response = client.get("/tools", headers={"If-None-Match": '"invalid"'})
        assert response.status_code == 200

    def test_etag_middleware_disabled(self, route_table: RouteTable):
        """Test that ETag middleware can be disabled."""
        from fastapi.testclient import TestClient

        # Create app without ETag middleware
        app = create_openapi_app(route_table, enable_etag=False)
        client = TestClient(app)

        # /tools endpoint still adds ETag header (it's built into the endpoint)
        response = client.get("/tools")
        assert response.status_code == 200
        # The endpoint itself adds ETag, but middleware doesn't intercept

    def test_etag_middleware_only_on_get(self, route_table: RouteTable):
        """Test that ETag middleware only applies to GET requests."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table, enable_etag=True)
        client = TestClient(app)

        # POST request should not be affected by ETag middleware
        response = client.post(
            "/tools/default/add",
            json={"a": 1, "b": 2},
            headers={"If-None-Match": route_table.etag},
        )
        # Should execute normally, not return 304
        assert response.status_code == 200
        assert response.json() == 3


class TestAddToolsEndpoint:
    """Tests for add_tools_endpoint function."""

    def test_add_tools_endpoint_to_app(self, route_table: RouteTable):
        """Test adding /tools endpoint to an existing app."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        add_tools_endpoint(app, route_table)

        client = TestClient(app)
        response = client.get("/tools")
        assert response.status_code == 200
        assert "tools" in response.json()


class TestToolExecutionErrorHandling:
    """Tests for tool execution error handling in OpenAPI endpoints."""

    def test_sync_tool_exception_returns_500(self, route_table: RouteTable):
        """Sync tool that raises should return structured 500."""
        from fastapi.testclient import TestClient

        def fail_sync() -> str:
            raise RuntimeError("boom")

        route_table._registry.register(fail_sync, name="fail_sync")
        route_table._rebuild()

        app = create_openapi_app(route_table)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/tools/default/fail_sync", json={})
        assert response.status_code == 500
        assert "boom" in response.json()["detail"]

    def test_async_tool_exception_returns_500(self, route_table: RouteTable):
        """Async tool that raises should return structured 500."""
        from fastapi.testclient import TestClient

        async def fail_async() -> str:
            raise ValueError("async boom")

        route_table._registry.register(fail_async, name="fail_async")
        route_table._rebuild()

        app = create_openapi_app(route_table)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/tools/default/fail_async", json={})
        assert response.status_code == 500
        assert "async boom" in response.json()["detail"]


class TestAddETagMiddleware:
    """Tests for add_etag_middleware function."""

    def test_add_etag_middleware_to_app(self, route_table: RouteTable):
        """Test adding ETag middleware to an existing app."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        add_tools_endpoint(app, route_table)
        add_etag_middleware(app, route_table)

        client = TestClient(app)

        # Get ETag
        response1 = client.get("/tools")
        etag = response1.headers.get("ETag")
        assert etag is not None

        # Conditional request
        response2 = client.get("/tools", headers={"If-None-Match": etag})
        assert response2.status_code == 304


# ============== Execution hint extensions (#280) ==============


def _core_has_execution_hints() -> bool:
    """Whether the installed toolregistry defines the hint metadata fields."""
    from dataclasses import fields

    return "read_only_hint" in {f.name for f in fields(ToolMetadata)}


@pytest.mark.skipif(
    not _core_has_execution_hints(),
    reason="installed toolregistry predates execution hints",
)
class TestOperationExtensions:
    """Resolved hints are exposed as ``x-`` extensions on operations."""

    @pytest.fixture
    def annotated_table(self) -> RouteTable:
        registry = ToolRegistry()

        def current_time() -> str:
            """Return the current time."""
            return "now"

        def remove_file(path: str) -> str:
            """Remove a file."""
            return path

        def plain(value: int) -> int:
            """Echo a value."""
            return value

        registry.register(
            Tool.from_function(
                current_time,
                metadata=ToolMetadata(
                    title="Current Time",
                    read_only_hint=True,
                    idempotent_hint=True,
                    open_world_hint=False,
                ),
            )
        )
        registry.register(
            Tool.from_function(
                remove_file,
                metadata=ToolMetadata(
                    destructive_hint=True,
                    idempotent_hint=False,
                ),
            )
        )
        registry.register(Tool.from_function(plain))
        return RouteTable(registry)

    @staticmethod
    def _operation(table: RouteTable, tool_name: str) -> dict:
        from fastapi.testclient import TestClient

        app = create_openapi_app(table)
        schema = TestClient(app).get("/openapi.json").json()
        return schema["paths"][f"/tools/default/{tool_name}"]["post"]

    def test_read_only_extensions(self, annotated_table: RouteTable) -> None:
        op = self._operation(annotated_table, "current_time")
        assert op["x-read-only"] is True
        assert op["x-destructive"] is False
        assert op["x-idempotent"] is True
        assert op["x-open-world"] is False
        assert op["x-title"] == "Current Time"

    def test_destructive_extensions(self, annotated_table: RouteTable) -> None:
        op = self._operation(annotated_table, "remove_file")
        assert op["x-destructive"] is True
        assert op["x-idempotent"] is False
        # Unset stays unset; the extension is absent, not fabricated.
        assert "x-read-only" not in op

    def test_unannotated_operation_has_no_extensions(
        self, annotated_table: RouteTable
    ) -> None:
        op = self._operation(annotated_table, "plain")
        assert not [key for key in op if key.startswith("x-")]
