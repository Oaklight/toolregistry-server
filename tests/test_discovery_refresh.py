"""Discovery-mode changes must reach the transports.

The registry emits ``REGISTER`` when discovery turns on and ``UNREGISTER``
when it turns off.  The route table listens to those events and tells its own
listeners, which is what sends MCP ``tools/list_changed`` and the OpenAPI SSE
``tool_change`` event.  A silent removal would leave both advertising
``discover_tools``/``call_deferred`` after the registry dropped them, so these
tests pin the event trail rather than the registry's bookkeeping.
"""

from __future__ import annotations

from toolregistry import Tool, ToolMetadata, ToolRegistry

from toolregistry_server import RouteTable


def _hidden() -> Tool:
    def hidden(x: int = 1) -> int:
        """A deferred tool."""
        return x

    return Tool.from_function(hidden, name="hidden", metadata=ToolMetadata(defer=True))


def _deferred() -> Tool:
    def deferred(x: int = 1) -> int:
        """A deferred tool registered from the server."""
        return x

    return Tool.from_function(
        deferred, name="deferred", metadata=ToolMetadata(defer=True)
    )


class TestDiscoveryChangesReachTransports:
    """Auto mode and an explicit disable both refresh the route table."""

    def test_auto_mode_adds_the_discovery_routes(self) -> None:
        registry = ToolRegistry()
        table = RouteTable(registry)

        registry.register(_hidden())

        names = {route.tool_name for route in table.list_routes()}
        assert "discover_tools" in names
        assert "call_deferred" in names

    def test_disabling_discovery_removes_routes_and_notifies(self) -> None:
        registry = ToolRegistry()
        registry.register(_hidden())
        table = RouteTable(registry)
        assert table.get_route("discover_tools") is not None

        events: list[tuple[str, str]] = []
        table.add_listener(lambda name, event: events.append((name, event)))

        registry.set_tool_discovery_mode("off")

        assert table.get_route("discover_tools") is None
        assert table.get_route("call_deferred") is None
        assert events, "listeners must hear about the removal"

    def test_auto_mode_turns_discovery_off_when_the_last_deferral_ends(self) -> None:
        registry = ToolRegistry()
        registry.register(_deferred())
        table = RouteTable(registry)
        assert table.get_route("discover_tools") is not None

        events: list[tuple[str, str]] = []
        table.add_listener(lambda name, event: events.append((name, event)))

        registry.update_tool_metadata("deferred", defer=False)

        assert table.get_route("discover_tools") is None
        assert events
