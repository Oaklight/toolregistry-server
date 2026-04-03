"""Integration tests for MCP session context passthrough.

Uses the MCP SDK's in-memory transport to verify that SessionContext
is correctly created, injected, and reused across tool calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from toolregistry.tool import Tool

from toolregistry_server import RouteTable
from toolregistry_server.mcp.adapter import route_table_to_mcp_server
from toolregistry_server.session import SessionContext

# ---------------------------------------------------------------------------
# Test tool functions
# ---------------------------------------------------------------------------


def plain_add(a: int, b: int) -> int:
    """Add two integers without session.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        Sum of a and b.
    """
    return a + b


def session_echo(x: int, _session: SessionContext) -> str:
    """Echo the input along with session info.

    Args:
        x: A value.
        _session: Injected session context.

    Returns:
        String with value and session id.
    """
    return f"x={x} session={_session.session_id}"


def session_counter(_session: SessionContext) -> str:
    """Increment and return a per-session counter.

    Args:
        _session: Injected session context.

    Returns:
        The current call count for this session.
    """
    count = _session.get("count", 0) + 1
    _session.set("count", count)
    return f"count={count}"


async def async_session_echo(x: int, _session: SessionContext) -> str:
    """Async version of session_echo.

    Args:
        x: A value.
        _session: Injected session context.

    Returns:
        String with value and session id.
    """
    return f"async x={x} session={_session.session_id}"


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


# ---------------------------------------------------------------------------
# 1. Backward compatibility – tool without _session
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Tools without _session should work unchanged."""

    @pytest.mark.asyncio
    async def test_plain_tool_works(self, mock_registry: MagicMock) -> None:
        """A tool without _session should execute normally."""
        tool = Tool.from_function(plain_add)
        mock_registry._tools = {"plain_add": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("plain_add", {"a": 3, "b": 4})
            assert result.isError is False
            assert result.content[0].text == "7"


# ---------------------------------------------------------------------------
# 2. Session injection
# ---------------------------------------------------------------------------


class TestSessionInjection:
    """Tools with _session should receive a SessionContext."""

    @pytest.mark.asyncio
    async def test_session_injected(self, mock_registry: MagicMock) -> None:
        """A tool with _session: SessionContext should receive session info."""
        tool = Tool.from_function(session_echo)
        mock_registry._tools = {"session_echo": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("session_echo", {"x": 42})
            assert result.isError is False
            text = result.content[0].text
            assert "x=42" in text
            assert "session=" in text
            # Session ID should be a non-empty hex string
            sid = text.split("session=")[1]
            assert len(sid) == 32  # uuid4().hex

    @pytest.mark.asyncio
    async def test_async_session_injected(self, mock_registry: MagicMock) -> None:
        """An async tool with _session should also receive session info."""
        tool = Tool.from_function(async_session_echo)
        mock_registry._tools = {"async_session_echo": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("async_session_echo", {"x": 7})
            assert result.isError is False
            text = result.content[0].text
            assert "async x=7" in text
            assert "session=" in text


# ---------------------------------------------------------------------------
# 3. Session stability within a connection
# ---------------------------------------------------------------------------


class TestSessionStability:
    """Multiple calls in the same session should share the same SessionContext."""

    @pytest.mark.asyncio
    async def test_same_session_id_across_calls(self, mock_registry: MagicMock) -> None:
        """Two calls in the same session should have the same session_id."""
        tool = Tool.from_function(session_echo)
        mock_registry._tools = {"session_echo": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            r1 = await client.call_tool("session_echo", {"x": 1})
            r2 = await client.call_tool("session_echo", {"x": 2})

            sid1 = r1.content[0].text.split("session=")[1]
            sid2 = r2.content[0].text.split("session=")[1]
            assert sid1 == sid2

    @pytest.mark.asyncio
    async def test_session_state_persists(self, mock_registry: MagicMock) -> None:
        """Session data should persist across multiple calls."""
        tool = Tool.from_function(session_counter)
        mock_registry._tools = {"session_counter": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            r1 = await client.call_tool("session_counter", {})
            assert r1.content[0].text == "count=1"

            r2 = await client.call_tool("session_counter", {})
            assert r2.content[0].text == "count=2"

            r3 = await client.call_tool("session_counter", {})
            assert r3.content[0].text == "count=3"


# ---------------------------------------------------------------------------
# 4. Different sessions get different contexts
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    """Different MCP sessions should get independent SessionContexts."""

    @pytest.mark.asyncio
    async def test_different_sessions_different_ids(
        self, mock_registry: MagicMock
    ) -> None:
        """Two separate sessions should have different session_ids."""
        tool = Tool.from_function(session_echo)
        mock_registry._tools = {"session_echo": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client1:
            r1 = await client1.call_tool("session_echo", {"x": 1})
            sid1 = r1.content[0].text.split("session=")[1]

        async with create_connected_server_and_client_session(server) as client2:
            r2 = await client2.call_tool("session_echo", {"x": 2})
            sid2 = r2.content[0].text.split("session=")[1]

        assert sid1 != sid2

    @pytest.mark.asyncio
    async def test_session_state_isolated(self, mock_registry: MagicMock) -> None:
        """Session state should not leak between sessions."""
        tool = Tool.from_function(session_counter)
        mock_registry._tools = {"session_counter": tool}

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        # First session counts to 2
        async with create_connected_server_and_client_session(server) as client1:
            await client1.call_tool("session_counter", {})
            r = await client1.call_tool("session_counter", {})
            assert r.content[0].text == "count=2"

        # Second session starts fresh at 1
        async with create_connected_server_and_client_session(server) as client2:
            r = await client2.call_tool("session_counter", {})
            assert r.content[0].text == "count=1"


# ---------------------------------------------------------------------------
# 5. Mixed tools (with and without _session)
# ---------------------------------------------------------------------------


class TestMixedTools:
    """Both plain and session-aware tools should coexist."""

    @pytest.mark.asyncio
    async def test_mixed_tools_coexist(self, mock_registry: MagicMock) -> None:
        """Plain and session-aware tools should both work in the same server."""
        plain_tool = Tool.from_function(plain_add)
        session_tool = Tool.from_function(session_echo)
        mock_registry._tools = {
            "plain_add": plain_tool,
            "session_echo": session_tool,
        }

        route_table = RouteTable(mock_registry)
        server = route_table_to_mcp_server(route_table)

        async with create_connected_server_and_client_session(server) as client:
            # Plain tool works
            r1 = await client.call_tool("plain_add", {"a": 5, "b": 3})
            assert r1.isError is False
            assert r1.content[0].text == "8"

            # Session tool works
            r2 = await client.call_tool("session_echo", {"x": 10})
            assert r2.isError is False
            assert "x=10" in r2.content[0].text
            assert "session=" in r2.content[0].text
