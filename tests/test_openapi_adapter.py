"""Tests for the OpenAPI adapter module."""

import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app
from toolregistry_server.openapi.adapter import (
    _resolve_type,
    _schema_to_pydantic,
    add_tools_endpoint,
    route_table_to_router,
    setup_dynamic_openapi,
)
from toolregistry_server.openapi.middleware import add_etag_middleware

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


# ============== Type Resolution Tests ==============


class TestResolveType:
    """Tests for _resolve_type function."""

    def test_string_type(self):
        """Test resolving string type."""
        result = _resolve_type({"type": "string"})
        assert result is str

    def test_integer_type(self):
        """Test resolving integer type."""
        result = _resolve_type({"type": "integer"})
        assert result is int

    def test_number_type(self):
        """Test resolving number type."""
        result = _resolve_type({"type": "number"})
        assert result is float

    def test_boolean_type(self):
        """Test resolving boolean type."""
        result = _resolve_type({"type": "boolean"})
        assert result is bool

    def test_array_type_simple(self):
        """Test resolving simple array type."""
        result = _resolve_type({"type": "array"})
        assert result is list

    def test_array_type_with_items(self):
        """Test resolving array type with items."""
        result = _resolve_type({"type": "array", "items": {"type": "string"}})
        assert result == list[str]

    def test_enum_type(self):
        """Test resolving enum type to Literal."""
        from typing import get_args

        result = _resolve_type({"enum": ["a", "b", "c"]})
        # Check it's a Literal type with correct values
        assert get_args(result) == ("a", "b", "c")

    def test_anyof_optional(self):
        """Test resolving anyOf with null (Optional pattern)."""
        result = _resolve_type({"anyOf": [{"type": "string"}, {"type": "null"}]})
        assert result is str


class TestSchemaToPydantic:
    """Tests for _schema_to_pydantic function."""

    def test_empty_schema(self):
        """Test creating model from empty schema."""
        from pydantic import BaseModel

        model = _schema_to_pydantic("EmptyModel", {})
        assert issubclass(model, BaseModel)

    def test_simple_schema(self):
        """Test creating model from simple schema."""
        schema = {
            "properties": {
                "name": {"type": "string", "description": "The name"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        model = _schema_to_pydantic("PersonModel", schema)

        # Check fields exist
        assert "name" in model.model_fields
        assert "age" in model.model_fields

        # Check required field
        assert model.model_fields["name"].is_required()
        assert not model.model_fields["age"].is_required()

    def test_schema_with_defaults(self):
        """Test creating model with default values."""
        schema = {
            "properties": {
                "count": {"type": "integer", "default": 10},
            },
        }
        model = _schema_to_pydantic("CountModel", schema)

        # Create instance without providing count
        instance = model()
        assert instance.count == 10


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

        from toolregistry_server.openapi.adapter import route_table_to_router

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
