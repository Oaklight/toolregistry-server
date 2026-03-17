"""Tests for the RouteTable module."""

from unittest.mock import MagicMock

import pytest
from toolregistry.events import ChangeEvent, ChangeEventType

from toolregistry_server import RouteEntry, RouteTable


class TestRouteEntry:
    """Tests for the RouteEntry dataclass."""

    def test_route_entry_creation(self) -> None:
        """Test creating a RouteEntry with all fields."""
        handler = lambda x: x  # noqa: E731

        entry = RouteEntry(
            tool_name="calculator-evaluate",
            namespace="calculator",
            method_name="evaluate",
            path="/tools/calculator/evaluate",
            description="Evaluate a math expression",
            parameters_schema={"type": "object", "properties": {}},
            handler=handler,
            is_async=False,
            enabled=True,
            disable_reason=None,
        )

        assert entry.tool_name == "calculator-evaluate"
        assert entry.namespace == "calculator"
        assert entry.method_name == "evaluate"
        assert entry.path == "/tools/calculator/evaluate"
        assert entry.description == "Evaluate a math expression"
        assert entry.parameters_schema == {"type": "object", "properties": {}}
        assert entry.handler is handler
        assert entry.is_async is False
        assert entry.enabled is True
        assert entry.disable_reason is None

    def test_route_entry_disabled(self) -> None:
        """Test creating a disabled RouteEntry."""
        entry = RouteEntry(
            tool_name="test-tool",
            namespace="test",
            method_name="tool",
            path="/tools/test/tool",
            description="A test tool",
            parameters_schema={},
            handler=lambda: None,
            is_async=False,
            enabled=False,
            disable_reason="Under maintenance",
        )

        assert entry.enabled is False
        assert entry.disable_reason == "Under maintenance"


def _make_mock_registry() -> MagicMock:
    """Create a mock ToolRegistry that simulates on_change callback behavior.

    When enable() or disable() is called on the mock, registered on_change
    callbacks are invoked with the appropriate ChangeEvent, mimicking real
    ToolRegistry behavior.
    """
    registry = MagicMock()
    registry._tools = {}
    registry.is_enabled = MagicMock(return_value=True)
    registry.get_disable_reason = MagicMock(return_value=None)

    # Track registered callbacks so enable/disable can fire them
    callbacks: list = []

    def mock_on_change(cb):
        callbacks.append(cb)

    def mock_enable(tool_name):
        for cb in callbacks:
            cb(ChangeEvent(event_type=ChangeEventType.ENABLE, tool_name=tool_name))

    def mock_disable(tool_name, reason=""):
        for cb in callbacks:
            cb(
                ChangeEvent(
                    event_type=ChangeEventType.DISABLE,
                    tool_name=tool_name,
                    reason=reason,
                )
            )

    registry.on_change = mock_on_change
    registry.enable = mock_enable
    registry.disable = mock_disable

    return registry


class TestRouteTable:
    """Tests for the RouteTable class."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create a mock ToolRegistry."""
        return _make_mock_registry()

    @pytest.fixture
    def mock_tool(self) -> MagicMock:
        """Create a mock Tool."""
        tool = MagicMock()
        tool.name = "greet"
        tool.namespace = "default"
        tool.method_name = "greet"
        tool.description = "Greet someone"
        tool.parameters = {"type": "object", "properties": {"name": {"type": "string"}}}
        tool.callable = lambda name: f"Hello, {name}!"
        tool.is_async = False
        return tool

    def test_route_table_initialization(self, mock_registry: MagicMock) -> None:
        """Test RouteTable initialization with empty registry."""
        route_table = RouteTable(mock_registry)

        assert route_table.list_routes() == []
        assert route_table.version == 0
        assert route_table.etag == '"0"'

    def test_route_table_with_tools(
        self, mock_registry: MagicMock, mock_tool: MagicMock
    ) -> None:
        """Test RouteTable initialization with tools."""
        mock_registry._tools = {"greet": mock_tool}

        route_table = RouteTable(mock_registry)
        routes = route_table.list_routes()

        assert len(routes) == 1
        assert routes[0].tool_name == "greet"
        assert routes[0].path == "/tools/default/greet"
        assert routes[0].description == "Greet someone"

    def test_get_route(self, mock_registry: MagicMock, mock_tool: MagicMock) -> None:
        """Test getting a specific route by name."""
        mock_registry._tools = {"greet": mock_tool}

        route_table = RouteTable(mock_registry)

        route = route_table.get_route("greet")
        assert route is not None
        assert route.tool_name == "greet"

        # Non-existent route
        assert route_table.get_route("nonexistent") is None

    def test_list_routes_enabled_only(
        self, mock_registry: MagicMock, mock_tool: MagicMock
    ) -> None:
        """Test listing routes with enabled_only filter."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.is_enabled = MagicMock(return_value=False)
        mock_registry.get_disable_reason = MagicMock(
            return_value="Disabled for testing"
        )

        route_table = RouteTable(mock_registry)

        # enabled_only=True (default) should return empty list
        assert route_table.list_routes(enabled_only=True) == []

        # enabled_only=False should return all routes
        routes = route_table.list_routes(enabled_only=False)
        assert len(routes) == 1
        assert routes[0].enabled is False

    def test_enable_tool(self, mock_registry: MagicMock, mock_tool: MagicMock) -> None:
        """Test enabling a tool."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.get_tool = MagicMock(return_value=mock_tool)

        route_table = RouteTable(mock_registry)
        initial_version = route_table.version

        route_table.enable("greet")

        assert route_table.version == initial_version + 1

    def test_disable_tool(self, mock_registry: MagicMock, mock_tool: MagicMock) -> None:
        """Test disabling a tool."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.get_tool = MagicMock(return_value=mock_tool)

        route_table = RouteTable(mock_registry)
        initial_version = route_table.version

        route_table.disable("greet", reason="Under maintenance")

        assert route_table.version == initial_version + 1

    def test_refresh_single_route(
        self, mock_registry: MagicMock, mock_tool: MagicMock
    ) -> None:
        """Test refreshing a single route."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.get_tool = MagicMock(return_value=mock_tool)

        route_table = RouteTable(mock_registry)

        # Modify the tool
        mock_tool.description = "Updated description"

        route_table.refresh("greet")

        route = route_table.get_route("greet")
        assert route is not None
        assert route.description == "Updated description"

    def test_refresh_all_routes(
        self, mock_registry: MagicMock, mock_tool: MagicMock
    ) -> None:
        """Test refreshing all routes."""
        mock_registry._tools = {"greet": mock_tool}

        route_table = RouteTable(mock_registry)
        initial_version = route_table.version

        route_table.refresh_all()

        assert route_table.version == initial_version + 1

    def test_add_listener(self, mock_registry: MagicMock, mock_tool: MagicMock) -> None:
        """Test adding a listener for route changes."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.get_tool = MagicMock(return_value=mock_tool)

        route_table = RouteTable(mock_registry)

        events: list[tuple[str, str]] = []

        def listener(tool_name: str, event: str) -> None:
            events.append((tool_name, event))

        route_table.add_listener(listener)
        route_table.enable("greet")

        assert len(events) == 1
        assert events[0] == ("greet", "enable")

    def test_remove_listener(
        self, mock_registry: MagicMock, mock_tool: MagicMock
    ) -> None:
        """Test removing a listener."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.get_tool = MagicMock(return_value=mock_tool)

        route_table = RouteTable(mock_registry)

        events: list[tuple[str, str]] = []

        def listener(tool_name: str, event: str) -> None:
            events.append((tool_name, event))

        route_table.add_listener(listener)
        route_table.remove_listener(listener)
        route_table.enable("greet")

        # Listener should not be called after removal
        assert len(events) == 0

    def test_remove_nonexistent_listener(self, mock_registry: MagicMock) -> None:
        """Test removing a listener that doesn't exist."""
        route_table = RouteTable(mock_registry)

        def listener(tool_name: str, event: str) -> None:
            pass

        with pytest.raises(ValueError):
            route_table.remove_listener(listener)

    def test_etag_changes_on_modification(
        self, mock_registry: MagicMock, mock_tool: MagicMock
    ) -> None:
        """Test that ETag changes when routes are modified."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.get_tool = MagicMock(return_value=mock_tool)

        route_table = RouteTable(mock_registry)
        initial_etag = route_table.etag

        route_table.enable("greet")

        assert route_table.etag != initial_etag

    def test_namespace_handling(self, mock_registry: MagicMock) -> None:
        """Test handling of tools with different namespaces."""
        tool1 = MagicMock()
        tool1.name = "calculator-add"
        tool1.namespace = "calculator"
        tool1.method_name = "add"
        tool1.description = "Add numbers"
        tool1.parameters = {}
        tool1.callable = lambda a, b: a + b
        tool1.is_async = False

        tool2 = MagicMock()
        tool2.name = "datetime-now"
        tool2.namespace = "datetime"
        tool2.method_name = "now"
        tool2.description = "Get current time"
        tool2.parameters = {}
        tool2.callable = lambda: "now"
        tool2.is_async = False

        mock_registry._tools = {
            "calculator-add": tool1,
            "datetime-now": tool2,
        }

        route_table = RouteTable(mock_registry)
        routes = route_table.list_routes()

        assert len(routes) == 2

        calc_route = route_table.get_route("calculator-add")
        assert calc_route is not None
        assert calc_route.namespace == "calculator"
        assert calc_route.path == "/tools/calculator/add"

        dt_route = route_table.get_route("datetime-now")
        assert dt_route is not None
        assert dt_route.namespace == "datetime"
        assert dt_route.path == "/tools/datetime/now"

    def test_default_namespace(self, mock_registry: MagicMock) -> None:
        """Test that tools without namespace get 'default' namespace."""
        tool = MagicMock()
        tool.name = "simple_tool"
        tool.namespace = None  # No namespace
        tool.method_name = None  # No method name
        tool.description = "A simple tool"
        tool.parameters = {}
        tool.callable = lambda: "result"
        tool.is_async = False

        mock_registry._tools = {"simple_tool": tool}

        route_table = RouteTable(mock_registry)
        route = route_table.get_route("simple_tool")

        assert route is not None
        assert route.namespace == "default"
        assert route.method_name == "simple_tool"
        assert route.path == "/tools/default/simple_tool"

    def test_async_handler_detection(self, mock_registry: MagicMock) -> None:
        """Test that async handlers are correctly detected."""
        async_tool = MagicMock()
        async_tool.name = "async_tool"
        async_tool.namespace = "test"
        async_tool.method_name = "async_tool"
        async_tool.description = "An async tool"
        async_tool.parameters = {}
        async_tool.callable = lambda: "result"
        async_tool.is_async = True

        mock_registry._tools = {"async_tool": async_tool}

        route_table = RouteTable(mock_registry)
        route = route_table.get_route("async_tool")

        assert route is not None
        assert route.is_async is True

    def test_multiple_listeners(
        self, mock_registry: MagicMock, mock_tool: MagicMock
    ) -> None:
        """Test that multiple listeners are all notified."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.get_tool = MagicMock(return_value=mock_tool)

        route_table = RouteTable(mock_registry)

        events1: list[tuple[str, str]] = []
        events2: list[tuple[str, str]] = []

        def listener1(tool_name: str, event: str) -> None:
            events1.append((tool_name, event))

        def listener2(tool_name: str, event: str) -> None:
            events2.append((tool_name, event))

        route_table.add_listener(listener1)
        route_table.add_listener(listener2)
        route_table.enable("greet")

        assert len(events1) == 1
        assert len(events2) == 1
        assert events1[0] == ("greet", "enable")
        assert events2[0] == ("greet", "enable")

    def test_refresh_all_notifies_listeners(self, mock_registry: MagicMock) -> None:
        """Test that refresh_all notifies listeners with '*' tool name."""
        route_table = RouteTable(mock_registry)

        events: list[tuple[str, str]] = []

        def listener(tool_name: str, event: str) -> None:
            events.append((tool_name, event))

        route_table.add_listener(listener)
        route_table.refresh_all()

        assert len(events) == 1
        assert events[0] == ("*", "refresh_all")

    def test_external_registry_change_syncs(
        self, mock_registry: MagicMock, mock_tool: MagicMock
    ) -> None:
        """Test that changes made directly on registry (e.g. via admin panel)
        are reflected in the RouteTable via on_change callback."""
        mock_registry._tools = {"greet": mock_tool}
        mock_registry.get_tool = MagicMock(return_value=mock_tool)

        route_table = RouteTable(mock_registry)
        initial_version = route_table.version

        events: list[tuple[str, str]] = []

        def listener(tool_name: str, event: str) -> None:
            events.append((tool_name, event))

        route_table.add_listener(listener)

        # Simulate admin panel calling registry.disable() directly
        # (bypassing route_table.disable())
        route_table._on_registry_change(
            ChangeEvent(
                event_type=ChangeEventType.DISABLE,
                tool_name="greet",
                reason="admin disabled",
            )
        )

        assert route_table.version == initial_version + 1
        assert len(events) == 1
        assert events[0] == ("greet", "disable")

    def test_namespace_disable_syncs_all_tools(self, mock_registry: MagicMock) -> None:
        """Test that disabling a namespace refreshes all tools in that namespace."""
        tool1 = MagicMock()
        tool1.name = "calculator-add"
        tool1.namespace = "calculator"
        tool1.method_name = "add"
        tool1.description = "Add numbers"
        tool1.parameters = {}
        tool1.callable = lambda a, b: a + b
        tool1.is_async = False

        tool2 = MagicMock()
        tool2.name = "calculator-sub"
        tool2.namespace = "calculator"
        tool2.method_name = "sub"
        tool2.description = "Subtract numbers"
        tool2.parameters = {}
        tool2.callable = lambda a, b: a - b
        tool2.is_async = False

        tool3 = MagicMock()
        tool3.name = "datetime-now"
        tool3.namespace = "datetime"
        tool3.method_name = "now"
        tool3.description = "Get current time"
        tool3.parameters = {}
        tool3.callable = lambda: "now"
        tool3.is_async = False

        mock_registry._tools = {
            "calculator-add": tool1,
            "calculator-sub": tool2,
            "datetime-now": tool3,
        }
        mock_registry.get_tool = MagicMock(
            side_effect=lambda n: mock_registry._tools.get(n)
        )

        # After namespace disable, is_enabled should return False for calculator tools
        def mock_is_enabled(name):
            return name not in ("calculator-add", "calculator-sub")

        mock_registry.is_enabled = MagicMock(side_effect=mock_is_enabled)
        mock_registry.get_disable_reason = MagicMock(return_value=None)

        route_table = RouteTable(mock_registry)

        events: list[tuple[str, str]] = []

        def listener(tool_name: str, event: str) -> None:
            events.append((tool_name, event))

        route_table.add_listener(listener)

        # Simulate admin panel disabling the "calculator" namespace
        route_table._on_registry_change(
            ChangeEvent(
                event_type=ChangeEventType.DISABLE,
                tool_name="calculator",
                reason="namespace disabled",
            )
        )

        # Both calculator tools should now be disabled
        add_route = route_table.get_route("calculator-add")
        sub_route = route_table.get_route("calculator-sub")
        dt_route = route_table.get_route("datetime-now")

        assert add_route is not None and add_route.enabled is False
        assert sub_route is not None and sub_route.enabled is False
        assert dt_route is not None and dt_route.enabled is True

        # Listener should have been notified
        assert len(events) == 1
        assert events[0] == ("calculator", "disable")
