"""Tests for the MCP adapter that bridges RouteTable to MCP Server.

Uses the MCP SDK's in-memory transport (create_test_client)
for end-to-end testing of list_tools and call_tool handlers.
"""

import json
from unittest.mock import MagicMock

import pytest
from mcp.server.lowlevel import Server
from toolregistry.tool import Tool

from toolregistry_server import RouteEntry, RouteTable
from toolregistry_server.adapters.mcp import route_table_to_mcp_server
from toolregistry_server.adapters.mcp._compat import create_test_client, get_field

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
    add_tool.metadata.defer = False

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
    multiply_tool.metadata.defer = False

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
        async with create_test_client(server) as client:
            result = await client.list_tools()
            tool_names = {t.name for t in result.tools}
            assert tool_names == {"add", "multiply"}

    @pytest.mark.asyncio
    async def test_list_tools_name_and_description(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Verify tool name and description are correctly mapped."""
        server = route_table_to_mcp_server(route_table_with_tools)
        async with create_test_client(server) as client:
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
        async with create_test_client(server) as client:
            result = await client.list_tools()
            tools_by_name = {t.name: t for t in result.tools}

            schema = get_field(tools_by_name["add"], "input_schema", "inputSchema")
            assert schema["type"] == "object"
            assert "a" in schema["properties"]
            assert "b" in schema["properties"]
            assert schema["properties"]["a"]["type"] == "integer"
            assert schema["properties"]["b"]["type"] == "integer"
            assert set(schema["required"]) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_list_tools_normalizes_invalid_input_schema(
        self, mock_registry: MagicMock
    ) -> None:
        """Invalid route schemas are normalized at the MCP boundary."""
        bad_tool = MagicMock()
        bad_tool.name = "bad"
        bad_tool.namespace = "default"
        bad_tool.method_name = "bad"
        bad_tool.description = "Bad schema."
        bad_tool.parameters = {}
        bad_tool.callable = get_info
        bad_tool.is_async = False
        bad_tool.metadata.defer = False

        mock_registry._tools = {"bad": bad_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)
        async with create_test_client(server) as client:
            result = await client.list_tools()
            assert get_field(result.tools[0], "input_schema", "inputSchema") == {
                "type": "object",
                "properties": {},
            }


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
        add_tool.metadata.defer = False

        multiply_tool = MagicMock()
        multiply_tool.name = "multiply"
        multiply_tool.namespace = "default"
        multiply_tool.method_name = "multiply"
        multiply_tool.description = "Multiply two integers."
        multiply_tool.parameters = {"type": "object", "properties": {}}
        multiply_tool.callable = multiply
        multiply_tool.is_async = False
        multiply_tool.metadata.defer = False

        mock_registry._tools = {"add": add_tool, "multiply": multiply_tool}
        mock_registry.get_tool = MagicMock(
            side_effect=lambda n: mock_registry._tools.get(n)
        )

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_test_client(server) as client:
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

        async with create_test_client(server) as client:
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
        async with create_test_client(server) as client:
            result = await client.call_tool("add", {"a": 3, "b": 4})
            assert get_field(result, "is_error", "isError") is False
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

        async with create_test_client(server) as client:
            result = await client.call_tool("add", {"a": 1, "b": 2})
            assert get_field(result, "is_error", "isError") is True
            assert "disabled" in result.content[0].text.lower()
            assert "maintenance" in result.content[0].text

    @pytest.mark.asyncio
    async def test_call_nonexistent_tool_returns_error(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Calling a non-existent tool should return isError=True."""
        server = route_table_to_mcp_server(route_table_with_tools)
        async with create_test_client(server) as client:
            result = await client.call_tool("nonexistent", {})
            assert get_field(result, "is_error", "isError") is True
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

        async with create_test_client(server) as client:
            result = await client.call_tool("add", {"a": 10, "b": 20})
            assert get_field(result, "is_error", "isError") is False
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

        async with create_test_client(server) as client:
            result = await client.call_tool("async_add", {"a": 5, "b": 7})
            assert get_field(result, "is_error", "isError") is False
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
        add_tool.metadata.defer = False

        async_add_tool = MagicMock()
        async_add_tool.name = "async_add"
        async_add_tool.namespace = "default"
        async_add_tool.method_name = "async_add"
        async_add_tool.description = "Asynchronously add two integers."
        async_add_tool.parameters = {"type": "object", "properties": {}}
        async_add_tool.callable = async_add
        async_add_tool.is_async = True
        async_add_tool.metadata.defer = False

        mock_registry._tools = {"add": add_tool, "async_add": async_add_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_test_client(server) as client:
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

        async with create_test_client(server) as client:
            result = await client.call_tool("get_info", {})
            assert get_field(result, "is_error", "isError") is False
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

        async with create_test_client(server) as client:
            result = await client.call_tool("greet", {"name": "World"})
            assert get_field(result, "is_error", "isError") is False
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

        async with create_test_client(server) as client:
            result = await client.call_tool("get_answer", {})
            assert get_field(result, "is_error", "isError") is False
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

        async with create_test_client(server) as client:
            result = await client.call_tool("get_pi", {})
            assert get_field(result, "is_error", "isError") is False
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

        async with create_test_client(server) as client:
            result = await client.call_tool("get_items", {})
            assert get_field(result, "is_error", "isError") is False
            parsed = json.loads(result.content[0].text)
            assert parsed == [1, "two", 3.0]

    @pytest.mark.asyncio
    async def test_content_block_list_returns_image_content(
        self, mock_registry: MagicMock
    ) -> None:
        """A content block list with an image should return MCP ImageContent."""
        from mcp.types import ImageContent

        def read_image() -> list:
            """Return a mock content block list with text and image."""
            return [
                {"type": "text", "text": "[Image: test.png (image/png, 100 bytes)]"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "iVBORw0KGgoAAAANSUhEUg==",
                    },
                },
            ]

        img_tool = MagicMock()
        img_tool.name = "read_image"
        img_tool.namespace = "default"
        img_tool.method_name = "read_image"
        img_tool.description = "Read an image file."
        img_tool.parameters = {"type": "object", "properties": {}}
        img_tool.callable = read_image
        img_tool.is_async = False

        mock_registry._tools = {"read_image": img_tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_test_client(server) as client:
            result = await client.call_tool("read_image", {})
            assert get_field(result, "is_error", "isError") is False
            assert len(result.content) == 2
            assert result.content[0].type == "text"
            assert "test.png" in result.content[0].text
            assert isinstance(result.content[1], ImageContent)
            assert result.content[1].type == "image"
            assert result.content[1].data == "iVBORw0KGgoAAAANSUhEUg=="
            assert get_field(result.content[1], "mime_type", "mimeType") == "image/png"

    @pytest.mark.asyncio
    async def test_text_only_content_blocks_returns_text_content(
        self, mock_registry: MagicMock
    ) -> None:
        """A content block list with only text blocks should return TextContent."""
        from mcp.types import TextContent

        def text_blocks() -> list:
            """Return text-only content blocks."""
            return [
                {"type": "text", "text": "Line one"},
                {"type": "text", "text": "Line two"},
            ]

        tool = MagicMock()
        tool.name = "text_blocks"
        tool.namespace = "default"
        tool.method_name = "text_blocks"
        tool.description = "Return text blocks."
        tool.parameters = {"type": "object", "properties": {}}
        tool.callable = text_blocks
        tool.is_async = False

        mock_registry._tools = {"text_blocks": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_test_client(server) as client:
            result = await client.call_tool("text_blocks", {})
            assert get_field(result, "is_error", "isError") is False
            assert len(result.content) == 2
            assert all(isinstance(c, TextContent) for c in result.content)
            assert result.content[0].text == "Line one"
            assert result.content[1].text == "Line two"

    @pytest.mark.asyncio
    async def test_unknown_block_type_degrades_to_text(
        self, mock_registry: MagicMock
    ) -> None:
        """Unknown content block types should degrade to JSON TextContent."""
        from unittest.mock import patch

        from mcp.types import ImageContent, TextContent

        def mixed_blocks() -> list:
            """Return content blocks including a future unknown type."""
            return [
                {"type": "text", "text": "Known text"},
                {"type": "audio", "data": "base64audio", "format": "wav"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "iVBORw0KGgo=",
                    },
                },
            ]

        tool = MagicMock()
        tool.name = "mixed_blocks"
        tool.namespace = "default"
        tool.method_name = "mixed_blocks"
        tool.description = "Return mixed blocks."
        tool.parameters = {"type": "object", "properties": {}}
        tool.callable = mixed_blocks
        tool.is_async = False

        mock_registry._tools = {"mixed_blocks": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        # Simulate core library recognizing "audio" before adapter does.
        # Patch on the adapter module where it was imported at module level.
        with patch(
            "toolregistry_server.adapters.mcp.adapter.is_content_block_list",
            return_value=True,
        ):
            async with create_test_client(server) as client:
                result = await client.call_tool("mixed_blocks", {})
                assert get_field(result, "is_error", "isError") is False
                assert len(result.content) == 3
                # Known text block
                assert isinstance(result.content[0], TextContent)
                assert result.content[0].text == "Known text"
                # Unknown "audio" block degraded to TextContent (JSON)
                assert isinstance(result.content[1], TextContent)
                degraded = json.loads(result.content[1].text)
                assert degraded["type"] == "audio"
                # Known image block
                assert isinstance(result.content[2], ImageContent)

    @pytest.mark.asyncio
    async def test_plain_list_not_content_blocks_json_serialized(
        self, mock_registry: MagicMock
    ) -> None:
        """A plain list (not content blocks) should still be JSON-serialized."""

        def get_numbers() -> list:
            """Return a plain list of numbers."""
            return [1, 2, 3]

        tool = MagicMock()
        tool.name = "get_numbers"
        tool.namespace = "default"
        tool.method_name = "get_numbers"
        tool.description = "Return numbers."
        tool.parameters = {"type": "object", "properties": {}}
        tool.callable = get_numbers
        tool.is_async = False

        mock_registry._tools = {"get_numbers": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_test_client(server) as client:
            result = await client.call_tool("get_numbers", {})
            assert get_field(result, "is_error", "isError") is False
            assert len(result.content) == 1
            parsed = json.loads(result.content[0].text)
            assert parsed == [1, 2, 3]


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

        async with create_test_client(server) as client:
            result = await client.call_tool("failing_tool", {})
            assert get_field(result, "is_error", "isError") is True
            assert "intentional error for testing" in result.content[0].text


# ---------------------------------------------------------------------------
# 8. Parameter validation / type coercion tests
# ---------------------------------------------------------------------------


def search(query: str, *, max_results: int = 5, timeout: float = 10.0) -> str:
    """Search with typed parameters.

    Args:
        query: Search query.
        max_results: Maximum results.
        timeout: Request timeout.

    Returns:
        A summary string.
    """
    return f"query={query} max_results={max_results}({type(max_results).__name__}) timeout={timeout}({type(timeout).__name__})"


class TestParameterValidation:
    """Tests for parameter validation and type coercion in call_tool."""

    @pytest.fixture
    def route_table_with_validated_tool(self, mock_registry: MagicMock) -> RouteTable:
        """Create a RouteTable with a tool that has a parameters_model."""
        tool = Tool.from_function(add)
        mock_registry._tools = {"add": tool}
        return RouteTable(mock_registry)

    @pytest.fixture
    def route_table_with_search_tool(self, mock_registry: MagicMock) -> RouteTable:
        """Create a RouteTable with a search tool that has optional typed params."""
        tool = Tool.from_function(search)
        mock_registry._tools = {"search": tool}
        return RouteTable(mock_registry)

    @pytest.mark.asyncio
    async def test_string_args_coerced_to_int(
        self, route_table_with_validated_tool: RouteTable
    ) -> None:
        """String arguments should be coerced to int when parameters_model is present."""
        server = route_table_to_mcp_server(route_table_with_validated_tool)
        async with create_test_client(server) as client:
            result = await client.call_tool("add", {"a": "3", "b": "4"})
            assert get_field(result, "is_error", "isError") is False
            assert result.content[0].text == "7"

    @pytest.mark.asyncio
    async def test_string_args_coerced_for_optional_params(
        self, route_table_with_search_tool: RouteTable
    ) -> None:
        """String values for optional int/float params should be coerced."""
        server = route_table_to_mcp_server(route_table_with_search_tool)
        async with create_test_client(server) as client:
            result = await client.call_tool(
                "search",
                {"query": "test", "max_results": "8", "timeout": "15.5"},
            )
            assert get_field(result, "is_error", "isError") is False
            text = result.content[0].text
            assert "max_results=8(int)" in text
            assert "timeout=15.5(float)" in text

    @pytest.mark.asyncio
    async def test_default_params_used_when_omitted(
        self, route_table_with_search_tool: RouteTable
    ) -> None:
        """Default values should be used when optional params are omitted."""
        server = route_table_to_mcp_server(route_table_with_search_tool)
        async with create_test_client(server) as client:
            result = await client.call_tool("search", {"query": "hello"})
            assert get_field(result, "is_error", "isError") is False
            text = result.content[0].text
            assert "max_results=5(int)" in text
            assert "timeout=10.0(float)" in text

    @pytest.mark.asyncio
    async def test_correct_types_pass_through(
        self, route_table_with_validated_tool: RouteTable
    ) -> None:
        """Correctly typed arguments should pass through without issue."""
        server = route_table_to_mcp_server(route_table_with_validated_tool)
        async with create_test_client(server) as client:
            result = await client.call_tool("add", {"a": 10, "b": 20})
            assert get_field(result, "is_error", "isError") is False
            assert result.content[0].text == "30"

    @pytest.mark.asyncio
    async def test_all_params_as_strings(
        self, route_table_with_search_tool: RouteTable
    ) -> None:
        """All params as strings (Codex-like behavior) should be coerced."""
        server = route_table_to_mcp_server(route_table_with_search_tool)
        async with create_test_client(server) as client:
            result = await client.call_tool(
                "search",
                {"query": "test query", "max_results": "3", "timeout": "5.0"},
            )
            assert get_field(result, "is_error", "isError") is False
            text = result.content[0].text
            assert "query=test query" in text
            assert "max_results=3(int)" in text
            assert "timeout=5.0(float)" in text

    @pytest.mark.asyncio
    async def test_invalid_string_coercion_returns_error(
        self, route_table_with_validated_tool: RouteTable
    ) -> None:
        """Non-numeric string for int param should return an error."""
        server = route_table_to_mcp_server(route_table_with_validated_tool)
        async with create_test_client(server) as client:
            result = await client.call_tool("add", {"a": "abc", "b": "4"})
            assert get_field(result, "is_error", "isError") is True

    @pytest.mark.asyncio
    async def test_bool_param_coerced_from_string(
        self, mock_registry: MagicMock
    ) -> None:
        """String 'true'/'false' should be coerced to bool."""

        def check(query: str, *, verbose: bool = False) -> str:
            """Check with bool param.

            Args:
                query: Query string.
                verbose: Verbose flag.

            Returns:
                Result string.
            """
            return f"verbose={verbose}({type(verbose).__name__})"

        tool = Tool.from_function(check)
        mock_registry._tools = {"check": tool}
        route_table = RouteTable(mock_registry)

        server = route_table_to_mcp_server(route_table)
        async with create_test_client(server) as client:
            result = await client.call_tool(
                "check", {"query": "test", "verbose": "true"}
            )
            assert get_field(result, "is_error", "isError") is False
            assert "verbose=True(bool)" in result.content[0].text


# ---------------------------------------------------------------------------
# 9. tools/list cache hints (MCP spec 2026-07-28)
# ---------------------------------------------------------------------------


class TestListToolsCacheHints:
    """Tests for ttlMs / cacheScope hints on tools/list responses."""

    @pytest.mark.asyncio
    async def test_cache_hints_forwarded_when_supported(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Cache hints should appear in list_tools response on supported SDKs."""
        from toolregistry_server.adapters.mcp._compat import supports_list_tools_cache

        server = route_table_to_mcp_server(
            route_table_with_tools,
            list_tools_ttl_ms=60000,
            list_tools_cache_scope="public",
        )
        async with create_test_client(server) as client:
            result = await client.list_tools()
            assert len(result.tools) > 0
            if supports_list_tools_cache():
                assert get_field(result, "ttl_ms", "ttlMs") == 60000
                assert get_field(result, "cache_scope", "cacheScope") == "public"

    @pytest.mark.asyncio
    async def test_no_cache_hints_by_default(
        self, route_table_with_tools: RouteTable
    ) -> None:
        """Without cache hint params, defaults should be unchanged."""
        server = route_table_to_mcp_server(route_table_with_tools)
        async with create_test_client(server) as client:
            result = await client.list_tools()
            assert len(result.tools) > 0
            assert get_field(result, "ttl_ms", "ttlMs") in (None, 0)


# ---------------------------------------------------------------------------
# 10. outputSchema / structuredContent (MCP spec 2026-07-28)
# ---------------------------------------------------------------------------

_STATS_SCHEMA = {
    "type": "object",
    "properties": {"count": {"type": "integer"}},
    "required": ["count"],
}


def _route_table_with_output_schema(
    func, tool_name: str, output_schema: dict | None = None
) -> RouteTable:
    """Build a RouteTable from a real Tool, optionally with output_schema."""
    tool = Tool.from_function(func, name=tool_name)
    if output_schema is not None:
        tool.metadata.extra["output_schema"] = output_schema
    registry = MagicMock()
    registry._tools = {tool_name: tool}
    registry.is_enabled = MagicMock(return_value=True)
    registry.get_disable_reason = MagicMock(return_value=None)
    return RouteTable(registry)


def stats() -> dict:
    """Return a small stats object.

    Returns:
        A dict with a count.
    """
    return {"count": 2}


class TestOutputSchema:
    """outputSchema advertisement and structuredContent emission."""

    def test_route_entry_picks_up_output_schema(self) -> None:
        """metadata.extra['output_schema'] should populate RouteEntry."""
        rt = _route_table_with_output_schema(stats, "stats", _STATS_SCHEMA)
        assert rt.get_route("stats").output_schema == _STATS_SCHEMA

    def test_route_entry_defaults_to_none(self) -> None:
        """No output_schema in extra should default to None."""
        rt = _route_table_with_output_schema(stats, "stats")
        assert rt.get_route("stats").output_schema is None

    @pytest.mark.asyncio
    async def test_list_tools_advertises_output_schema(self) -> None:
        """tools/list should include outputSchema when declared."""
        rt = _route_table_with_output_schema(stats, "stats", _STATS_SCHEMA)
        server = route_table_to_mcp_server(rt)
        async with create_test_client(server) as client:
            result = await client.list_tools()
            tool = result.tools[0]
            assert get_field(tool, "output_schema", "outputSchema") == _STATS_SCHEMA

    @pytest.mark.asyncio
    async def test_list_tools_omits_output_schema_when_absent(self) -> None:
        """tools/list should not include outputSchema when not declared."""
        rt = _route_table_with_output_schema(stats, "stats")
        server = route_table_to_mcp_server(rt)
        async with create_test_client(server) as client:
            result = await client.list_tools()
            tool = result.tools[0]
            assert get_field(tool, "output_schema", "outputSchema") is None

    @pytest.mark.asyncio
    async def test_call_tool_returns_structured_content(self) -> None:
        """tools/call should return structuredContent when outputSchema is declared."""
        rt = _route_table_with_output_schema(stats, "stats", _STATS_SCHEMA)
        server = route_table_to_mcp_server(rt)
        async with create_test_client(server) as client:
            result = await client.call_tool("stats", {})
            assert get_field(result, "is_error", "isError") is False
            structured = get_field(result, "structured_content", "structuredContent")
            assert structured == {"count": 2}
            assert json.loads(result.content[0].text) == {"count": 2}

    @pytest.mark.asyncio
    async def test_no_structured_content_without_output_schema(self) -> None:
        """tools/call should not return structuredContent without outputSchema."""
        rt = _route_table_with_output_schema(stats, "stats")
        server = route_table_to_mcp_server(rt)
        async with create_test_client(server) as client:
            result = await client.call_tool("stats", {})
            assert get_field(result, "structured_content", "structuredContent") is None
            assert json.loads(result.content[0].text) == {"count": 2}


# ---------------------------------------------------------------------------
# 11. resource_link and content-block passthrough
# ---------------------------------------------------------------------------


def with_links() -> dict:
    """Return a result carrying a resource link.

    Returns:
        A dict with a _resource_links key.
    """
    return {
        "ok": True,
        "_resource_links": [
            {
                "uri": "file:///tmp/report.json",
                "name": "report.json",
                "mime_type": "application/json",
                "description": "Full run record.",
            }
        ],
    }


def bad_links() -> dict:
    """Return malformed resource links that must be skipped.

    Returns:
        A dict with an invalid _resource_links payload.
    """
    return {"ok": True, "_resource_links": [{"name": "missing-uri"}, "nonsense"]}


def image_and_text() -> list:
    """Return content blocks directly, as an image tool does.

    Returns:
        A list of MCP content blocks.
    """
    from mcp.types import ImageContent, TextContent

    return [
        ImageContent(type="image", data="aGk=", mimeType="image/png"),
        TextContent(type="text", text='{"n_face": 8}'),
    ]


def single_block() -> object:
    """Return one content block, not wrapped in a list.

    Returns:
        A single MCP content block.
    """
    from mcp.types import TextContent

    return TextContent(type="text", text="hello")


class TestContentBlockPassthrough:
    """A tool may return content blocks; they must survive untouched."""

    @pytest.mark.asyncio
    async def test_image_block_is_not_stringified(self) -> None:
        # Serializing these would emit a JSON array of Pydantic reprs, so
        # the caller receives text like "type='image' data='...'" and never
        # an image it can render.
        server = route_table_to_mcp_server(
            _route_table_with_output_schema(image_and_text, "img")
        )
        async with create_test_client(server) as client:
            result = await client.call_tool("img", {})
            assert get_field(result, "is_error", "isError") is False
            assert [c.type for c in result.content] == ["image", "text"]
            assert result.content[0].data == "aGk="
            assert json.loads(result.content[1].text) == {"n_face": 8}

    @pytest.mark.asyncio
    async def test_single_block_is_accepted(self) -> None:
        server = route_table_to_mcp_server(
            _route_table_with_output_schema(single_block, "one")
        )
        async with create_test_client(server) as client:
            result = await client.call_tool("one", {})
            assert [c.type for c in result.content] == ["text"]
            assert result.content[0].text == "hello"

    @pytest.mark.asyncio
    async def test_ordinary_results_still_serialized(self) -> None:
        # The passthrough must not swallow plain data results.
        server = route_table_to_mcp_server(
            _route_table_with_output_schema(stats, "stats")
        )
        async with create_test_client(server) as client:
            result = await client.call_tool("stats", {})
            assert [c.type for c in result.content] == ["text"]
            assert json.loads(result.content[0].text)


class TestResourceLinks:
    """_resource_links promotion into resource_link content blocks."""

    @pytest.mark.asyncio
    async def test_resource_link_emitted_as_content_block(self) -> None:
        server = route_table_to_mcp_server(
            _route_table_with_output_schema(with_links, "with_links")
        )
        async with create_test_client(server) as client:
            result = await client.call_tool("with_links", {})
            assert get_field(result, "is_error", "isError") is False
            links = [c for c in result.content if c.type == "resource_link"]
            assert len(links) == 1
            assert str(links[0].uri) == "file:///tmp/report.json"
            assert links[0].name == "report.json"
            assert get_field(links[0], "mime_type", "mimeType") == "application/json"

    @pytest.mark.asyncio
    async def test_marker_key_stripped_from_text_payload(self) -> None:
        server = route_table_to_mcp_server(
            _route_table_with_output_schema(with_links, "with_links")
        )
        async with create_test_client(server) as client:
            result = await client.call_tool("with_links", {})
            payload = json.loads(result.content[0].text)
            assert payload == {"ok": True}
            assert "_resource_links" not in payload

    @pytest.mark.asyncio
    async def test_malformed_links_are_skipped(self) -> None:
        server = route_table_to_mcp_server(
            _route_table_with_output_schema(bad_links, "bad_links")
        )
        async with create_test_client(server) as client:
            result = await client.call_tool("bad_links", {})
            assert get_field(result, "is_error", "isError") is False
            assert [c for c in result.content if c.type == "resource_link"] == []
            assert json.loads(result.content[0].text) == {"ok": True}

    @pytest.mark.asyncio
    async def test_plain_results_are_untouched(self) -> None:
        server = route_table_to_mcp_server(
            _route_table_with_output_schema(stats, "stats")
        )
        async with create_test_client(server) as client:
            result = await client.call_tool("stats", {})
            assert len(result.content) == 1
            assert result.content[0].type == "text"
