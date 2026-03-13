"""Tests for the OpenAPI adapter module."""

import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app
from toolregistry_server.openapi.adapter import (
    _resolve_type,
    _schema_to_pydantic,
    route_table_to_router,
    setup_dynamic_openapi,
)

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
