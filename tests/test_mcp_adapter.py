"""Tests for the MCP adapter that bridges RouteTable to MCP Server.

Uses the MCP SDK's in-memory transport (create_connected_server_and_client_session)
for end-to-end testing of list_tools and call_tool handlers.
"""

import json
from unittest.mock import MagicMock

import pytest
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_connected_server_and_client_session

from toolregistry_server import RouteEntry, RouteTable
from toolregistry_server.mcp import create_mcp_server, route_table_to_mcp_server

# ---------------------------------------------------------------------------
# Test helper functions
# ---------------------------------------------------------------------------


def add(a: int, b: int) -> int:
    """Add two integers.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        Sum of a and b.
    """
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two integers.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        Product of a and b.
    """
    return a * b


def greet(name: str) -> str:
    """Return a greeting string.

    Args:
        name: Name to greet.

    Returns:
        A greeting message.
    """
    return f"Hello, {name}!"


def get_info() -> dict:
    """Return a sample info dict.

    Returns:
        A dictionary with sample data.
    """
    return {"status": "ok", "count": 42}


def get_pi() -> float:
    """Return the value of pi.

    Returns:
        Pi approximation.
    """
    return 3.14159


def get_answer() -> int:
    """Return the answer to everything.

    Returns:
        The number 42.
    """
    return 42


def failing_tool() -> str:
    """A tool that always raises an exception.

    Returns:
        Never returns normally.

    Raises:
        ValueError: Always.
    """
    raise ValueError("intentional error for testing")


async def async_add(a: int, b: int) -> int:
    """Asynchronously add two integers.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        Sum of a and b.
    """
    return a + b


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_registry() -> MagicMock:
    """Create a mock ToolRegistry."""
    registry = MagicMock()
    registry._tools = {}
    registry.is_enabled = MagicMock(return_value=True)
    registry.get_disable_reason = MagicMock(return_value=None)
    return registry


@pytest.fixture
def route_table_with_tools(mock_registry: MagicMock) -> RouteTable:
    """Create a RouteTable with add and multiply tools."""
    # Create mock tools
    add_tool = MagicMock()
    add_tool.name = "add"
    add_tool.namespace = "default"
    add_tool.method_name = "add"
    add_tool.description = "Add two integers."
    add_tool.parameters = {
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "First operand."},
            "b": {"type": "integer", "description": "Second operand."},
        },
        "required": ["a", "b"],
    }
    add_tool.callable = add
    add_tool.is_async = False

    multiply_tool = MagicMock()
    multiply_tool.name = "multiply"
    multiply_tool.namespace = "default"
    multiply_tool.method_name = "multiply"
    multiply_tool.description = "Multiply two integers."
    multiply_tool.parameters = {
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "First operand."},
            "b": {"type": "integer", "description": "Second operand."},
        },
        "required": ["a", "b"],
    }
    multiply_tool.callable = multiply
    multiply_tool.is_async = False

    mock_registry._tools = {"add": add_tool, "multiply": multiply_tool}

    return RouteTable(mock_registry)


# ---------------------------------------------------------------------------
# 1. route_table_to_mcp_server() basic functionality
# ---------------------------------------------------------------------------


class TestRouteTableToMcpServer:
    """Tests for route_table_to_mcp_server() basic creation."""

    def test_returns_server_instance(self, route_table_with_tools: RouteTable) -> None:
        """Verify that route_table_to_mcp_server returns an mcp Server instance."""
        server = route_table_to_mcp_server(route_table_with_tools)
        assert isinstance(server, Server)

    def test_create_mcp_server_returns_server_instance(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Verify that create_mcp_server also returns a Server."""
        server = create_mcp_server(route_table_with_tools)
        assert isinstance(server, Server)

    def test_server_has_correct_name(self, route_table_with_tools: RouteTable) -> None:
        """Verify the server name is set correctly."""
        server = route_table_to_mcp_server(route_table_with_tools)
        assert server.name == "ToolRegistry-Server"

    def test_server_custom_name(self, route_table_with_tools: RouteTable) -> None:
        """Verify custom server name is used."""
        server = route_table_to_mcp_server(route_table_with_tools, name="Custom-Server")
        assert server.name == "Custom-Server"


# ---------------------------------------------------------------------------
# 2. list_tools handler
# ---------------------------------------------------------------------------


class TestListTools:
    """Tests for the list_tools MCP handler."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_registered_tools(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Verify list_tools returns all enabled tools from the route table."""
        server = route_table_to_mcp_server(route_table_with_tools)
        async with create_connected_server_and_client_session(server) as client:
            result = await client.list_tools()
            tool_names = {t.name for t in result.tools}
            assert tool_names == {"add", "multiply"}

    @pytest.mark.asyncio
    async def test_list_tools_name_and_description(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Verify tool name and description are correctly mapped."""
        server = route_table_to_mcp_server(route_table_with_tools)
        async with create_connected_server_and_client_session(server) as client:
            result = await client.list_tools()
            tools_by_name = {t.name: t for t in result.tools}

            assert "add" in tools_by_name
            assert tools_by_name["add"].description == "Add two integers."

            assert "multiply" in tools_by_name
            assert tools_by_name["multiply"].description == "Multiply two integers."

    @pytest.mark.asyncio
    async def test_list_tools_input_schema(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Verify inputSchema contains correct parameter definitions."""
        server = route_table_to_mcp_server(route_table_with_tools)
        async with create_connected_server_and_client_session(server) as client:
            result = await client.list_tools()
            tools_by_name = {t.name: t for t in result.tools}

            schema = tools_by_name["add"].inputSchema
            assert schema["type"] == "object"
            assert "a" in schema["properties"]
            assert "b" in schema["properties"]
            assert schema["properties"]["a"]["type"] == "integer"
            assert schema["properties"]["b"]["type"] == "integer"
            assert set(schema["required"]) == {"a", "b"}


# ---------------------------------------------------------------------------
# 3. enable/disable dynamic reflection (key test)
# ---------------------------------------------------------------------------


class TestEnableDisable:
    """Tests for dynamic enable/disable reflection in list_tools."""

    @pytest.mark.asyncio
    async def test_disable_removes_tool_from_list(
        self, mock_registry: MagicMock
    ) -> None:
        """Disabling a tool should remove it from list_tools results."""
        # Create tools
        add_tool = MagicMock()
        add_tool.name = "add"
        add_tool.namespace = "default"
        add_tool.method_name = "add"
        add_tool.description = "Add two integers."
        add_tool.parameters = {"type": "object", "properties": {}}
        add_tool.callable = add
        add_tool.is_async = False

        multiply_tool = MagicMock()
        multiply_tool.name = "multiply"
        multiply_tool.namespace = "default"
        multiply_tool.method_name = "multiply"
        multiply_tool.description = "Multiply two integers."
        multiply_tool.parameters = {"type": "object", "properties": {}}
        multiply_tool.callable = multiply
        multiply_tool.is_async = False

        mock_registry._tools = {"add": add_tool, "multiply": multiply_tool}
        mock_registry.get_tool = MagicMock(
            side_effect=lambda n: mock_registry._tools.get(n)
        )

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            # Initially both tools are listed
            result = await client.list_tools()
            assert {t.name for t in result.tools} == {"add", "multiply"}

            # Disable 'add' by modifying the route entry
            route = route_table.get_route("add")
            assert route is not None
            # Manually update the route entry to simulate disable
            route_table._routes["add"] = RouteEntry(
                tool_name=route.tool_name,
                namespace=route.namespace,
                method_name=route.method_name,
                path=route.path,
                description=route.description,
                parameters_schema=route.parameters_schema,
                handler=route.handler,
                is_async=route.is_async,
                enabled=False,
                disable_reason="maintenance",
            )

            result = await client.list_tools()
            assert {t.name for t in result.tools} == {"multiply"}

    @pytest.mark.asyncio
    async def test_enable_restores_tool_to_list(self, mock_registry: MagicMock) -> None:
        """Re-enabling a tool should restore it in list_tools results."""
        add_tool = MagicMock()
        add_tool.name = "add"
        add_tool.namespace = "default"
        add_tool.method_name = "add"
        add_tool.description = "Add two integers."
        add_tool.parameters = {"type": "object", "properties": {}}
        add_tool.callable = add
        add_tool.is_async = False

        mock_registry._tools = {"add": add_tool}
        mock_registry.get_tool = MagicMock(return_value=add_tool)

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            # Disable then re-enable
            route = route_table.get_route("add")
            assert route is not None

            # Disable
            route_table._routes["add"] = RouteEntry(
                tool_name=route.tool_name,
                namespace=route.namespace,
                method_name=route.method_name,
                path=route.path,
                description=route.description,
                parameters_schema=route.parameters_schema,
                handler=route.handler,
                is_async=route.is_async,
                enabled=False,
                disable_reason="maintenance",
            )

            result = await client.list_tools()
            assert {t.name for t in result.tools} == set()

            # Re-enable
            route_table._routes["add"] = RouteEntry(
                tool_name=route.tool_name,
                namespace=route.namespace,
                method_name=route.method_name,
                path=route.path,
                description=route.description,
                parameters_schema=route.parameters_schema,
                handler=route.handler,
                is_async=route.is_async,
                enabled=True,
                disable_reason=None,
            )

            result = await client.list_tools()
            assert {t.name for t in result.tools} == {"add"}


# ---------------------------------------------------------------------------
# 4. call_tool handler
# ---------------------------------------------------------------------------


class TestCallTool:
    """Tests for the call_tool MCP handler."""

    @pytest.mark.asyncio
    async def test_call_enabled_tool(self, route_table_with_tools: RouteTable) -> None:
        """Calling an enabled tool should return the correct result."""
        server = route_table_to_mcp_server(route_table_with_tools)
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("add", {"a": 3, "b": 4})
            assert result.isError is False
            assert len(result.content) == 1
            assert result.content[0].text == "7"

    @pytest.mark.asyncio
    async def test_call_disabled_tool_returns_error(
        self, mock_registry: MagicMock
    ) -> None:
        """Calling a disabled tool should return isError=True with reason."""
        add_tool = MagicMock()
        add_tool.name = "add"
        add_tool.namespace = "default"
        add_tool.method_name = "add"
        add_tool.description = "Add two integers."
        add_tool.parameters = {"type": "object", "properties": {}}
        add_tool.callable = add
        add_tool.is_async = False

        mock_registry._tools = {"add": add_tool}
        mock_registry.is_enabled = MagicMock(return_value=False)
        mock_registry.get_disable_reason = MagicMock(return_value="maintenance")

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("add", {"a": 1, "b": 2})
            assert result.isError is True
            assert "disabled" in result.content[0].text.lower()
            assert "maintenance" in result.content[0].text

    @pytest.mark.asyncio
    async def test_call_nonexistent_tool_returns_error(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Calling a non-existent tool should return isError=True."""
        server = route_table_to_mcp_server(route_table_with_tools)
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("nonexistent", {})
            assert result.isError is True
            assert "not found" in result.content[0].text.lower()


# ---------------------------------------------------------------------------
# 5. sync/async tool tests
# ---------------------------------------------------------------------------


class TestSyncAsyncTools:
    """Tests for both synchronous and asynchronous tool execution."""

    @pytest.mark.asyncio
    async def test_sync_tool_execution(self, mock_registry: MagicMock) -> None:
        """A synchronous tool should execute correctly via call_tool."""
        add_tool = MagicMock()
        add_tool.name = "add"
        add_tool.namespace = "default"
        add_tool.method_name = "add"
        add_tool.description = "Add two integers."
        add_tool.parameters = {"type": "object", "properties": {}}
        add_tool.callable = add
        add_tool.is_async = False

        mock_registry._tools = {"add": add_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("add", {"a": 10, "b": 20})
            assert result.isError is False
            assert result.content[0].text == "30"

    @pytest.mark.asyncio
    async def test_async_tool_execution(self, mock_registry: MagicMock) -> None:
        """An asynchronous tool should execute correctly via call_tool."""
        async_add_tool = MagicMock()
        async_add_tool.name = "async_add"
        async_add_tool.namespace = "default"
        async_add_tool.method_name = "async_add"
        async_add_tool.description = "Asynchronously add two integers."
        async_add_tool.parameters = {"type": "object", "properties": {}}
        async_add_tool.callable = async_add
        async_add_tool.is_async = True

        mock_registry._tools = {"async_add": async_add_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("async_add", {"a": 5, "b": 7})
            assert result.isError is False
            assert result.content[0].text == "12"

    @pytest.mark.asyncio
    async def test_mixed_sync_async_tools(self, mock_registry: MagicMock) -> None:
        """Both sync and async tools should coexist and work correctly."""
        add_tool = MagicMock()
        add_tool.name = "add"
        add_tool.namespace = "default"
        add_tool.method_name = "add"
        add_tool.description = "Add two integers."
        add_tool.parameters = {"type": "object", "properties": {}}
        add_tool.callable = add
        add_tool.is_async = False

        async_add_tool = MagicMock()
        async_add_tool.name = "async_add"
        async_add_tool.namespace = "default"
        async_add_tool.method_name = "async_add"
        async_add_tool.description = "Asynchronously add two integers."
        async_add_tool.parameters = {"type": "object", "properties": {}}
        async_add_tool.callable = async_add
        async_add_tool.is_async = True

        mock_registry._tools = {"add": add_tool, "async_add": async_add_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            # Verify both are listed
            tools_result = await client.list_tools()
            tool_names = {t.name for t in tools_result.tools}
            assert tool_names == {"add", "async_add"}

            # Call sync tool
            r1 = await client.call_tool("add", {"a": 1, "b": 2})
            assert r1.content[0].text == "3"

            # Call async tool
            r2 = await client.call_tool("async_add", {"a": 3, "b": 4})
            assert r2.content[0].text == "7"


# ---------------------------------------------------------------------------
# 6. Result serialization tests
# ---------------------------------------------------------------------------


class TestResultSerialization:
    """Tests for result serialization in call_tool responses."""

    @pytest.mark.asyncio
    async def test_dict_result_json_serialized(self, mock_registry: MagicMock) -> None:
        """A dict result should be JSON-serialized."""
        info_tool = MagicMock()
        info_tool.name = "get_info"
        info_tool.namespace = "default"
        info_tool.method_name = "get_info"
        info_tool.description = "Return a sample info dict."
        info_tool.parameters = {"type": "object", "properties": {}}
        info_tool.callable = get_info
        info_tool.is_async = False

        mock_registry._tools = {"get_info": info_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("get_info", {})
            assert result.isError is False
            parsed = json.loads(result.content[0].text)
            assert parsed == {"status": "ok", "count": 42}

    @pytest.mark.asyncio
    async def test_str_result_direct_string(self, mock_registry: MagicMock) -> None:
        """A str result should be returned as-is."""
        greet_tool = MagicMock()
        greet_tool.name = "greet"
        greet_tool.namespace = "default"
        greet_tool.method_name = "greet"
        greet_tool.description = "Return a greeting string."
        greet_tool.parameters = {"type": "object", "properties": {}}
        greet_tool.callable = greet
        greet_tool.is_async = False

        mock_registry._tools = {"greet": greet_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("greet", {"name": "World"})
            assert result.isError is False
            assert result.content[0].text == "Hello, World!"

    @pytest.mark.asyncio
    async def test_int_result_str_conversion(self, mock_registry: MagicMock) -> None:
        """An int result should be converted via str()."""
        answer_tool = MagicMock()
        answer_tool.name = "get_answer"
        answer_tool.namespace = "default"
        answer_tool.method_name = "get_answer"
        answer_tool.description = "Return the answer to everything."
        answer_tool.parameters = {"type": "object", "properties": {}}
        answer_tool.callable = get_answer
        answer_tool.is_async = False

        mock_registry._tools = {"get_answer": answer_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("get_answer", {})
            assert result.isError is False
            assert result.content[0].text == "42"

    @pytest.mark.asyncio
    async def test_float_result_str_conversion(self, mock_registry: MagicMock) -> None:
        """A float result should be converted via str()."""
        pi_tool = MagicMock()
        pi_tool.name = "get_pi"
        pi_tool.namespace = "default"
        pi_tool.method_name = "get_pi"
        pi_tool.description = "Return the value of pi."
        pi_tool.parameters = {"type": "object", "properties": {}}
        pi_tool.callable = get_pi
        pi_tool.is_async = False

        mock_registry._tools = {"get_pi": pi_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("get_pi", {})
            assert result.isError is False
            assert result.content[0].text == "3.14159"

    @pytest.mark.asyncio
    async def test_list_result_json_serialized(self, mock_registry: MagicMock) -> None:
        """A list result should be JSON-serialized."""

        def get_items() -> list:
            """Return a sample list."""
            return [1, "two", 3.0]

        items_tool = MagicMock()
        items_tool.name = "get_items"
        items_tool.namespace = "default"
        items_tool.method_name = "get_items"
        items_tool.description = "Return a sample list."
        items_tool.parameters = {"type": "object", "properties": {}}
        items_tool.callable = get_items
        items_tool.is_async = False

        mock_registry._tools = {"get_items": items_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("get_items", {})
            assert result.isError is False
            parsed = json.loads(result.content[0].text)
            assert parsed == [1, "two", 3.0]


# ---------------------------------------------------------------------------
# 7. Exception handling tests
# ---------------------------------------------------------------------------


class TestExceptionHandling:
    """Tests for exception handling in call_tool."""

    @pytest.mark.asyncio
    async def test_tool_execution_error_returns_error(
        self, mock_registry: MagicMock
    ) -> None:
        """When a tool raises an exception, it should return isError=True."""
        failing = MagicMock()
        failing.name = "failing_tool"
        failing.namespace = "default"
        failing.method_name = "failing_tool"
        failing.description = "A tool that always raises an exception."
        failing.parameters = {"type": "object", "properties": {}}
        failing.callable = failing_tool
        failing.is_async = False

        mock_registry._tools = {"failing_tool": failing}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("failing_tool", {})
            assert result.isError is True
            assert "intentional error for testing" in result.content[0].text
