"""Tests for the FastAPI-free OpenAPI document builder."""

from __future__ import annotations

import json

import pytest
from toolregistry import ToolRegistry
from toolregistry.tool import Tool, ToolMetadata

from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi.spec import build_openapi_schema


@pytest.fixture
def route_table() -> RouteTable:
    registry = ToolRegistry()

    @registry.register
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    @registry.register
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    return RouteTable(registry)


def _operation(document: dict, tool_name: str) -> dict:
    return document["paths"][f"/tools/default/{tool_name}"]["post"]


class TestDocumentShape:
    def test_document_is_openapi_31(self, route_table: RouteTable):
        document = build_openapi_schema(route_table)

        assert document["openapi"] == "3.1.0"
        assert document["info"]["title"] == "ToolRegistry Server"
        assert document["info"]["version"] == "1.0.0"
        assert json.dumps(document)  # JSON-serializable

    def test_info_is_configurable(self, route_table: RouteTable):
        document = build_openapi_schema(
            route_table,
            title="Tools",
            version="9.9.9",
            description="desc",
            servers=[{"url": "https://example.test"}],
        )

        assert document["info"] == {
            "title": "Tools",
            "version": "9.9.9",
            "description": "desc",
        }
        assert document["servers"] == [{"url": "https://example.test"}]

    def test_one_post_per_enabled_route(self, route_table: RouteTable):
        document = build_openapi_schema(route_table, include_meta=False)

        assert set(document["paths"]) == {
            "/tools/default/greet",
            "/tools/default/add",
        }
        for path_item in document["paths"].values():
            assert set(path_item) == {"post"}

    def test_meta_endpoints_are_included_by_default(self, route_table: RouteTable):
        document = build_openapi_schema(route_table)

        assert "get" in document["paths"]["/tools"]
        assert "get" in document["paths"]["/events"]
        assert document["paths"]["/tools"]["get"]["tags"] == ["meta"]

    def test_disabled_route_is_not_emitted(self, route_table: RouteTable):
        route_table.disable("add", reason="test")

        document = build_openapi_schema(route_table)

        assert "/tools/default/add" not in document["paths"]
        assert "/tools/default/greet" in document["paths"]

    def test_reenabled_route_returns(self, route_table: RouteTable):
        route_table.disable("add")
        route_table.enable("add")

        document = build_openapi_schema(route_table)

        assert "/tools/default/add" in document["paths"]


class TestOperationIdentity:
    def test_operation_id_is_the_tool_name(self, route_table: RouteTable):
        document = build_openapi_schema(route_table)

        assert _operation(document, "add")["operationId"] == "add"

    def test_summary_is_the_description(self, route_table: RouteTable):
        document = build_openapi_schema(route_table)

        assert _operation(document, "add")["summary"] == "Add two numbers."

    def test_summary_is_truncated_at_120(self):
        registry = ToolRegistry()

        def long_tool(x: int) -> int:
            return x

        long_tool.__doc__ = "y" * 200
        registry.register(Tool.from_function(long_tool))

        document = build_openapi_schema(RouteTable(registry))

        assert len(_operation(document, "long_tool")["summary"]) == 120

    def test_tag_is_the_top_level_namespace(self, route_table: RouteTable):
        document = build_openapi_schema(route_table)

        assert _operation(document, "add")["tags"] == ["default"]

    def test_request_body_carries_the_parameter_schema(self, route_table: RouteTable):
        document = build_openapi_schema(route_table)
        schema = _operation(document, "add")["requestBody"]["content"][
            "application/json"
        ]["schema"]

        assert schema["type"] == "object"
        assert set(schema["properties"]) == {"a", "b"}

    def test_responses_cover_success_and_validation(self, route_table: RouteTable):
        document = build_openapi_schema(route_table)
        responses = _operation(document, "add")["responses"]

        assert set(responses) == {"200", "422"}


def _core_has_execution_hints() -> bool:
    """Whether the installed toolregistry defines the hint metadata fields."""
    from dataclasses import fields

    return "read_only_hint" in {f.name for f in fields(ToolMetadata)}


@pytest.mark.skipif(
    not _core_has_execution_hints(),
    reason="installed toolregistry predates execution hints",
)
class TestExtensions:
    @staticmethod
    def _table() -> RouteTable:
        registry = ToolRegistry()

        def current_time() -> str:
            """Read the clock."""
            return "now"

        def remove_file(path: str) -> str:
            """Delete a file."""
            return path

        registry.register(
            Tool.from_function(
                current_time,
                metadata=ToolMetadata(
                    title="Current Time",
                    read_only_hint=True,
                    destructive_hint=False,
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
        return RouteTable(registry)

    def test_read_only_extensions(self):
        document = build_openapi_schema(self._table())
        operation = _operation(document, "current_time")

        assert operation["x-read-only"] is True
        assert operation["x-destructive"] is False
        assert operation["x-idempotent"] is True
        assert operation["x-open-world"] is False
        assert operation["x-title"] == "Current Time"

    def test_unset_hints_are_absent(self):
        document = build_openapi_schema(self._table())
        operation = _operation(document, "remove_file")

        assert operation["x-destructive"] is True
        assert "x-read-only" not in operation


class TestParityWithFastAPI:
    """The builder must describe the same API FastAPI published."""

    def test_paths_and_operation_identity_match(self, route_table: RouteTable):
        fastapi = pytest.importorskip("fastapi")
        assert fastapi  # imported for the side effect of skipping
        from fastapi.testclient import TestClient

        from toolregistry_server.adapters.openapi import create_openapi_app

        legacy = TestClient(create_openapi_app(route_table)).get("/openapi.json").json()
        document = build_openapi_schema(route_table)

        assert set(document["paths"]) == set(legacy["paths"])
        for path, operations in legacy["paths"].items():
            for method, legacy_op in operations.items():
                built = document["paths"][path][method]
                assert built["operationId"] == legacy_op["operationId"]
                assert built["summary"] == legacy_op["summary"]
                assert built["tags"] == legacy_op["tags"]
                for key, value in legacy_op.items():
                    if key.startswith("x-"):
                        assert built[key] == value

    def test_disabled_routes_are_omitted_like_the_filtered_document(
        self, route_table: RouteTable
    ):
        route_table.disable("add", reason="test")
        document = build_openapi_schema(route_table)

        assert "/tools/default/add" not in document["paths"]
        assert "/tools/default/greet" in document["paths"]
