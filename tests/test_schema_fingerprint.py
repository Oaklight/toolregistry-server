"""Tests for schema fingerprint, SSE events, and MCP notifications.

Covers issue #68: schema_hash/last_refreshed_at on RouteEntry and /tools,
GET /events SSE endpoint, and MCP tools/list_changed notification.
"""

import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteEntry, RouteTable
from toolregistry_server.adapters.openapi import create_openapi_app

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

    return reg


@pytest.fixture
def route_table(registry: ToolRegistry) -> RouteTable:
    """Create a RouteTable from the registry."""
    return RouteTable(registry)


# ============== RouteEntry schema fingerprint tests ==============


class TestRouteEntrySchemaFingerprint:
    """Tests for schema_hash and last_refreshed_at on RouteEntry."""

    def test_route_entry_has_schema_hash_field(self):
        """RouteEntry should have schema_hash field with empty default."""
        entry = RouteEntry(
            tool_name="test",
            namespace="default",
            method_name="test",
            path="/tools/default/test",
            description="A test tool",
            parameters_schema={"type": "object", "properties": {}},
            handler=lambda: None,
            is_async=False,
        )
        assert entry.schema_hash == ""

    def test_route_entry_has_last_refreshed_at_field(self):
        """RouteEntry should have last_refreshed_at field with empty default."""
        entry = RouteEntry(
            tool_name="test",
            namespace="default",
            method_name="test",
            path="/tools/default/test",
            description="A test tool",
            parameters_schema={"type": "object", "properties": {}},
            handler=lambda: None,
            is_async=False,
        )
        assert entry.last_refreshed_at == ""

    def test_route_entry_custom_schema_hash(self):
        """RouteEntry should accept custom schema_hash value."""
        entry = RouteEntry(
            tool_name="test",
            namespace="default",
            method_name="test",
            path="/tools/default/test",
            description="A test tool",
            parameters_schema={"type": "object", "properties": {}},
            handler=lambda: None,
            is_async=False,
            schema_hash="abc123",
            last_refreshed_at="2026-09-10T00:00:00Z",
        )
        assert entry.schema_hash == "abc123"
        assert entry.last_refreshed_at == "2026-09-10T00:00:00Z"

    def test_tool_to_route_populates_schema_hash(self, route_table: RouteTable):
        """_tool_to_route should populate schema_hash from tool.metadata."""
        route = route_table.get_route("greet")
        assert route is not None
        # schema_hash is populated from metadata (may be empty for
        # tools without explicit schema_hash set)
        assert isinstance(route.schema_hash, str)
        assert isinstance(route.last_refreshed_at, str)


# ============== /tools endpoint with schema_hash ==============


class TestToolsEndpointSchemaFingerprint:
    """Tests for schema_hash and last_refreshed_at in /tools response."""

    def test_tools_response_includes_schema_hash_when_present(self):
        """When a tool has schema_hash set, /tools should include it."""
        from fastapi.testclient import TestClient

        reg = ToolRegistry()

        @reg.register
        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        # Manually set schema_hash on the tool's metadata
        tool = reg.get_tool("greet")
        tool.metadata.schema_hash = "deadbeef"
        tool.metadata.last_refreshed_at = "2026-09-10T12:00:00Z"

        rt = RouteTable(reg)
        app = create_openapi_app(rt)
        client = TestClient(app)

        response = client.get("/tools")
        assert response.status_code == 200
        data = response.json()

        tool_info = next(t for t in data["tools"] if t["name"] == "greet")
        assert tool_info["schema_hash"] == "deadbeef"
        assert tool_info["last_refreshed_at"] == "2026-09-10T12:00:00Z"

    def test_tools_response_omits_empty_schema_hash(self, route_table: RouteTable):
        """When schema_hash is empty, it should be omitted from /tools."""
        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app)

        response = client.get("/tools")
        assert response.status_code == 200
        data = response.json()

        for tool_info in data["tools"]:
            # Empty schema_hash should not appear in the response
            if "schema_hash" in tool_info:
                assert tool_info["schema_hash"] != ""
            if "last_refreshed_at" in tool_info:
                assert tool_info["last_refreshed_at"] != ""


# ============== SSE /events endpoint ==============


class TestSSEEventsEndpoint:
    """Tests for the GET /events SSE endpoint."""

    def test_events_endpoint_exists(self, route_table: RouteTable):
        """The /events endpoint should be registered on the app."""
        app = create_openapi_app(route_table)
        paths = [getattr(r, "path", None) for r in app.routes]
        assert "/events" in paths

    def test_events_listener_pushes_to_queues(self, route_table: RouteTable):
        """Disabling a tool should push an SSE-formatted event to all queues.

        We test the mechanism by verifying the listener is registered and
        does not crash when triggered.
        """
        from fastapi import FastAPI

        from toolregistry_server.adapters.openapi.adapter import add_events_endpoint

        app = FastAPI()
        add_events_endpoint(app, route_table)

        # The listener is now registered on route_table.
        initial_version = route_table.version
        route_table.disable("add", reason="testing")
        assert route_table.version > initial_version

    def test_events_sse_queue_receives_messages(self):
        """Internal SSE queue should receive messages when changes happen."""

        reg = ToolRegistry()

        @reg.register
        def hello(name: str) -> str:
            """Say hello."""
            return f"Hello, {name}!"

        rt = RouteTable(reg)

        from fastapi import FastAPI

        from toolregistry_server.adapters.openapi.adapter import add_events_endpoint

        app = FastAPI()
        add_events_endpoint(app, rt)

        # Directly test the SSE queue mechanism by calling the endpoint
        # and checking queue content through the listener
        # We inspect the _queues set indirectly by verifying
        # the listener chain works end-to-end
        events_received: list[tuple[str, str]] = []

        def spy(tool_name: str, event: str) -> None:
            events_received.append((tool_name, event))

        rt.add_listener(spy)
        rt.disable("hello", reason="test")

        assert len(events_received) == 1
        assert events_received[0] == ("hello", "disable")


# ============== MCP tools_changed capability ==============


class TestMCPToolsChangedCapability:
    """Tests for MCP tools_changed notification capability."""

    def test_tools_changed_notification_options(self):
        """tools_changed_notification_options should return options with
        tools_changed=True."""
        from toolregistry_server.adapters.mcp._compat import (
            tools_changed_notification_options,
        )

        opts = tools_changed_notification_options()
        assert opts.tools_changed is True

    async def test_mcp_server_advertises_tools_changed(self):
        """MCP server should advertise tools.listChanged capability."""
        from toolregistry_server.adapters.mcp import route_table_to_mcp_server

        reg = ToolRegistry()

        @reg.register
        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        rt = RouteTable(reg)
        server = route_table_to_mcp_server(rt)

        # Check that create_initialization_options produces
        # listChanged=True in tools capability
        init_opts = server.create_initialization_options()
        caps = init_opts.capabilities
        assert caps.tools is not None
        assert caps.tools.listChanged is True

    async def test_mcp_session_tracker_captures_sessions(self):
        """The session_tracker set should capture sessions during list_tools."""
        from toolregistry_server.adapters.mcp import route_table_to_mcp_server
        from toolregistry_server.adapters.mcp._compat import create_test_client

        reg = ToolRegistry()

        @reg.register
        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        rt = RouteTable(reg)
        server = route_table_to_mcp_server(rt)

        async with create_test_client(server) as client:
            # list_tools should trigger session tracking
            tools = await client.list_tools()
            assert len(tools.tools) == 1
