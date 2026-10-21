"""Tests for MCP and OpenAPI source registration in registry_builder.

Covers the test gaps identified in toolregistry-server#24: register_mcp_source,
register_openapi_source, _should_load_source with non-Python types,
apply_config with MCP/OpenAPI sources, and multi-source config loading.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from toolregistry.config import (
    AuthConfig,
    MCPSource,
    OpenAPISource,
    ProfileConfig,
    PythonSource,
    ToolConfig,
)

from toolregistry_server.registry_builder import (
    _should_load_source,
    apply_config,
    register_mcp_source,
    register_openapi_source,
)

MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test", "version": "1.0"},
    "servers": [{"url": "http://api.example.com"}],
    "paths": {},
}


class TestRegisterMCPSource:
    def test_stdio_transport(self):
        registry = MagicMock()
        source = MCPSource(
            transport="stdio",
            command=("python", "-m", "my_mcp_server", "--flag"),
            namespace="mcp_ns",
        )
        register_mcp_source(registry, source)

        registry.register_from_mcp.assert_called_once_with(
            {"command": "python", "args": ["-m", "my_mcp_server", "--flag"], "env": {}},
            namespace="mcp_ns",
            persistent=True,
            headers=None,
        )

    def test_stdio_transport_with_env(self):
        registry = MagicMock()
        source = MCPSource(
            transport="stdio",
            command=("node", "server.js"),
            env={"API_KEY": "secret"},
            namespace="mcp_ns",
        )
        register_mcp_source(registry, source)

        call_args = registry.register_from_mcp.call_args
        assert call_args[0][0] == {
            "command": "node",
            "args": ["server.js"],
            "env": {"API_KEY": "secret"},
        }

    def test_sse_transport(self):
        registry = MagicMock()
        source = MCPSource(
            transport="sse",
            url="http://localhost:8080/sse",
            namespace="sse_ns",
        )
        register_mcp_source(registry, source)

        registry.register_from_mcp.assert_called_once_with(
            "http://localhost:8080/sse",
            namespace="sse_ns",
            persistent=True,
            headers=None,
        )

    def test_streamable_http_transport(self):
        registry = MagicMock()
        source = MCPSource(
            transport="streamable-http",
            url="http://localhost:9000/mcp",
            namespace="http_ns",
        )
        register_mcp_source(registry, source)

        registry.register_from_mcp.assert_called_once_with(
            "http://localhost:9000/mcp",
            namespace="http_ns",
            persistent=True,
            headers=None,
        )

    def test_with_headers_and_non_persistent(self):
        registry = MagicMock()
        source = MCPSource(
            transport="sse",
            url="http://localhost:8080/sse",
            namespace="mcp_ns",
            headers={"X-Custom": "value"},
            persistent=False,
        )
        register_mcp_source(registry, source)

        registry.register_from_mcp.assert_called_once_with(
            "http://localhost:8080/sse",
            namespace="mcp_ns",
            persistent=False,
            headers={"X-Custom": "value"},
        )

    def test_no_namespace(self):
        """When namespace is None, register_mcp_source passes False (not None)
        to signal 'no namespace' to the registry's register_from_mcp."""
        registry = MagicMock()
        source = MCPSource(
            transport="sse",
            url="http://localhost:8080/sse",
        )
        register_mcp_source(registry, source)

        call_args = registry.register_from_mcp.call_args
        assert call_args[1]["namespace"] is False


class TestRegisterOpenAPISource:
    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_basic(self, mock_load):
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        source = OpenAPISource(
            url="http://example.com/openapi.json",
            namespace="api_ns",
        )
        register_openapi_source(registry, source)

        mock_load.assert_called_once_with("http://example.com/openapi.json")
        registry.register_from_openapi.assert_called_once()
        call_args = registry.register_from_openapi.call_args
        client = call_args[0][0]
        assert client.base_url == "http://api.example.com"
        assert call_args[0][1] is MINIMAL_SPEC
        assert call_args[1]["namespace"] == "api_ns"
        assert call_args[1]["persistent"] is True

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_bearer_auth(self, mock_load):
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        source = OpenAPISource(
            url="http://example.com/openapi.json",
            namespace="api_ns",
            auth=AuthConfig(type="bearer", token="my-secret-token"),
        )
        register_openapi_source(registry, source)

        client = registry.register_from_openapi.call_args[0][0]
        assert client.headers["Authorization"] == "Bearer my-secret-token"

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_header_auth(self, mock_load):
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        source = OpenAPISource(
            url="http://example.com/openapi.json",
            auth=AuthConfig(type="header", token="key123", header_name="X-Api-Key"),
        )
        register_openapi_source(registry, source)

        client = registry.register_from_openapi.call_args[0][0]
        assert client.headers["X-Api-Key"] == "key123"

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_explicit_base_url_override(self, mock_load):
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        source = OpenAPISource(
            url="http://example.com/openapi.json",
            base_url="http://custom-host:9000",
        )
        register_openapi_source(registry, source)

        client = registry.register_from_openapi.call_args[0][0]
        assert client.base_url == "http://custom-host:9000"

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_no_servers_in_spec(self, mock_load):
        spec_no_servers = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {},
        }
        mock_load.return_value = spec_no_servers
        registry = MagicMock()
        source = OpenAPISource(url="http://example.com/openapi.json")
        register_openapi_source(registry, source)

        client = registry.register_from_openapi.call_args[0][0]
        assert client.base_url == ""

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_no_namespace(self, mock_load):
        """When namespace is None, register_openapi_source passes False (not
        None) to signal 'no namespace' to the registry's register_from_openapi."""
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        source = OpenAPISource(url="http://example.com/openapi.json")
        register_openapi_source(registry, source)

        call_args = registry.register_from_openapi.call_args
        assert call_args[1]["namespace"] is False


class TestShouldLoadSourceMCPOpenAPI:
    def test_mcp_source_denylist_blocked(self):
        source = MCPSource(
            transport="stdio",
            command=("python", "-m", "server"),
            namespace="blocked_ns",
        )
        config = ToolConfig(mode="denylist", disabled=("blocked_ns",))
        assert _should_load_source(source, config) is False

    def test_mcp_source_denylist_allowed(self):
        source = MCPSource(
            transport="sse",
            url="http://localhost:8080/sse",
            namespace="allowed_ns",
        )
        config = ToolConfig(mode="denylist", disabled=("other_ns",))
        assert _should_load_source(source, config) is True

    def test_openapi_source_allowlist_included(self):
        source = OpenAPISource(
            url="http://example.com/openapi.json",
            namespace="included_ns",
        )
        config = ToolConfig(mode="allowlist", enabled=("included_ns",))
        assert _should_load_source(source, config) is True

    def test_openapi_source_allowlist_excluded(self):
        source = OpenAPISource(
            url="http://example.com/openapi.json",
            namespace="excluded_ns",
        )
        config = ToolConfig(mode="allowlist", enabled=("other_ns",))
        assert _should_load_source(source, config) is False

    def test_mcp_source_no_namespace_always_loaded_allowlist(self):
        source = MCPSource(transport="sse", url="http://localhost/sse")
        config = ToolConfig(mode="allowlist", enabled=("some_ns",))
        assert _should_load_source(source, config) is True

    def test_mcp_source_no_namespace_always_loaded_denylist(self):
        source = MCPSource(transport="sse", url="http://localhost/sse")
        config = ToolConfig(mode="denylist", disabled=("some_ns",))
        assert _should_load_source(source, config) is True


class TestApplyConfigMCPOpenAPI:
    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_mcp_source_registered(self, mock_load):
        registry = MagicMock()
        config = ToolConfig(
            tools=(
                MCPSource(
                    transport="sse",
                    url="http://localhost:8080/sse",
                    namespace="mcp_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_mcp.assert_called_once()
        mock_load.assert_not_called()

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_openapi_source_registered(self, mock_load):
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        config = ToolConfig(
            tools=(
                OpenAPISource(
                    url="http://example.com/openapi.json",
                    namespace="api_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_openapi.assert_called_once()

    def test_denylist_filters_mcp_source(self):
        registry = MagicMock()
        config = ToolConfig(
            mode="denylist",
            disabled=("blocked_ns",),
            tools=(
                MCPSource(
                    transport="sse",
                    url="http://localhost:8080/sse",
                    namespace="blocked_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_mcp.assert_not_called()

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_denylist_filters_openapi_source(self, mock_load):
        registry = MagicMock()
        config = ToolConfig(
            mode="denylist",
            disabled=("blocked_api",),
            tools=(
                OpenAPISource(
                    url="http://example.com/openapi.json",
                    namespace="blocked_api",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_openapi.assert_not_called()
        mock_load.assert_not_called()

    def test_allowlist_allows_mcp_source(self):
        registry = MagicMock()
        config = ToolConfig(
            mode="allowlist",
            enabled=("mcp_ns",),
            tools=(
                MCPSource(
                    transport="sse",
                    url="http://localhost:8080/sse",
                    namespace="mcp_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_mcp.assert_called_once()

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_allowlist_filters_openapi_not_in_list(self, mock_load):
        registry = MagicMock()
        config = ToolConfig(
            mode="allowlist",
            enabled=("other_ns",),
            tools=(
                OpenAPISource(
                    url="http://example.com/openapi.json",
                    namespace="api_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_openapi.assert_not_called()

    def test_disabled_mcp_source_skipped(self):
        registry = MagicMock()
        config = ToolConfig(
            tools=(
                MCPSource(
                    transport="sse",
                    url="http://localhost:8080/sse",
                    namespace="mcp_ns",
                    enabled=False,
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_mcp.assert_not_called()

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_disabled_openapi_source_skipped(self, mock_load):
        registry = MagicMock()
        config = ToolConfig(
            tools=(
                OpenAPISource(
                    url="http://example.com/openapi.json",
                    namespace="api_ns",
                    enabled=False,
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_openapi.assert_not_called()
        mock_load.assert_not_called()

    @patch("toolregistry_server.registry_builder.logger")
    def test_mcp_error_swallowed_and_logged(self, mock_logger):
        registry = MagicMock()
        registry.register_from_mcp.side_effect = ConnectionError("unreachable")
        config = ToolConfig(
            tools=(
                MCPSource(
                    transport="sse",
                    url="http://localhost:8080/sse",
                    namespace="mcp_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_mcp.assert_called_once()
        mock_logger.warning.assert_called()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "Failed to load tools" in warning_msg

    @patch("toolregistry_server.registry_builder.logger")
    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_openapi_load_error_swallowed_and_logged(self, mock_load, mock_logger):
        mock_load.side_effect = ConnectionError("cannot fetch spec")
        registry = MagicMock()
        config = ToolConfig(
            tools=(
                OpenAPISource(
                    url="http://unreachable.example.com/openapi.json",
                    namespace="api_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_openapi.assert_not_called()
        mock_logger.warning.assert_called()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "Failed to load tools" in warning_msg


class TestApplyConfigMultiSource:
    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_mcp_and_openapi_both_registered(self, mock_load):
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        config = ToolConfig(
            tools=(
                MCPSource(
                    transport="sse",
                    url="http://localhost:8080/sse",
                    namespace="mcp_ns",
                ),
                OpenAPISource(
                    url="http://example.com/openapi.json",
                    namespace="api_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_mcp.assert_called_once()
        registry.register_from_openapi.assert_called_once()

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_all_three_source_types(self, mock_load, tmp_path, monkeypatch):
        # @patch injects mock_load before pytest appends tmp_path, monkeypatch
        mock_load.return_value = MINIMAL_SPEC

        mod_file = tmp_path / "test_tools.py"
        mod_file.write_text(
            "def greet(name: str) -> str:\n"
            '    """Say hello."""\n'
            '    return f"Hello, {name}!"\n',
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        registry = MagicMock()
        config = ToolConfig(
            tools=(
                PythonSource(module_path="test_tools", namespace="py_ns"),
                MCPSource(
                    transport="stdio",
                    command=("python", "-m", "server"),
                    namespace="mcp_ns",
                ),
                OpenAPISource(
                    url="http://example.com/openapi.json",
                    namespace="api_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register.assert_called()
        registry.register_from_mcp.assert_called_once()
        registry.register_from_openapi.assert_called_once()

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_one_source_fails_others_still_register(self, mock_load):
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        registry.register_from_mcp.side_effect = RuntimeError("MCP server down")

        config = ToolConfig(
            tools=(
                MCPSource(
                    transport="sse",
                    url="http://localhost:8080/sse",
                    namespace="mcp_ns",
                ),
                OpenAPISource(
                    url="http://example.com/openapi.json",
                    namespace="api_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_mcp.assert_called_once()
        registry.register_from_openapi.assert_called_once()

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_denylist_filters_selectively(self, mock_load):
        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()

        config = ToolConfig(
            mode="denylist",
            disabled=("mcp_ns",),
            tools=(
                MCPSource(
                    transport="sse",
                    url="http://localhost:8080/sse",
                    namespace="mcp_ns",
                ),
                OpenAPISource(
                    url="http://example.com/openapi.json",
                    namespace="api_ns",
                ),
            ),
        )
        apply_config(registry, config)

        registry.register_from_mcp.assert_not_called()
        registry.register_from_openapi.assert_called_once()


# ---------------------------------------------------------------------------
# Profile filtering: what external callers are told, and what discovery sees
# ---------------------------------------------------------------------------


def _tagged_registry(tags, name: str = "fs_tool"):
    """Return a registry holding one tool carrying *tags*."""
    from toolregistry import ToolRegistry
    from toolregistry.tool import Tool, ToolMetadata

    def fs_tool() -> str:
        """Read a file from disk."""
        return "fs"

    registry = ToolRegistry()
    registry._tools[name] = Tool.from_function(
        fs_tool, name=name, metadata=ToolMetadata(tags=set(tags))
    )
    return registry


def _error_text(exc: BaseException) -> str:
    """Return the text of *exc*, unwrapping nested exception groups.

    An MCP client session runs inside an anyio task group, so an error
    raised by the server arrives as an ``ExceptionGroup`` whose own
    ``str()`` is only ``"unhandled errors in a TaskGroup"`` — the message
    the caller actually sees lives on the sub-exceptions.
    """
    parts = [str(exc)]
    for sub in getattr(exc, "exceptions", ()):
        parts.append(_error_text(sub))
    return " ".join(parts)


def _discovery_skips_disabled_tools() -> bool:
    """Probe whether the installed core drops disabled tools from discovery.

    Core only started invalidating the search index on disable events in
    the 0.19 line, so on older cores a profile-filtered tool is still
    discoverable.  Server CI installs the released core, hence the probe.
    """
    from toolregistry.tool import ToolTag

    registry = _tagged_registry({ToolTag.FILE_SYSTEM})
    registry.enable_tool_discovery()
    try:
        registry.disable("fs_tool", reason="probe")
        result = registry._tool_discovery.discover("read a file", top_k=5)
        if isinstance(result, dict):  # core < 0.19 shape
            result = result.get("results", [])
        return "fs_tool" not in {item["name"] for item in result}
    finally:
        registry.disable_tool_discovery()


class TestProfileDisableReason:
    """The disable reason is client-visible, so it must stay neutral (#84)."""

    def test_builtin_profile_reason_does_not_name_the_profile(self):
        from toolregistry.tool import ToolTag

        from toolregistry_server.registry_builder import (
            PROFILE_DISABLE_REASON,
            apply_profile,
        )

        registry = _tagged_registry({ToolTag.FILE_SYSTEM})
        apply_profile(registry, "remote")

        reason = registry.get_disable_reason("fs_tool")
        assert reason == PROFILE_DISABLE_REASON
        assert "remote" not in reason
        assert "profile" not in reason.lower()

    def test_config_profile_reason_does_not_name_the_profile(self):
        from toolregistry.tool import ToolTag

        from toolregistry_server.registry_builder import (
            PROFILE_DISABLE_REASON,
            apply_profile,
        )

        registry = _tagged_registry({ToolTag.NETWORK})
        config = ToolConfig(profiles={"edge": ProfileConfig(disable_tags=("network",))})
        apply_profile(registry, "edge", config)

        reason = registry.get_disable_reason("fs_tool")
        assert reason == PROFILE_DISABLE_REASON
        assert "edge" not in reason

    def test_config_profile_force_disable_reason_is_neutral(self):
        from toolregistry.tool import ToolTag

        from toolregistry_server.registry_builder import (
            PROFILE_DISABLE_REASON,
            apply_profile,
        )

        registry = _tagged_registry({ToolTag.NETWORK})
        config = ToolConfig(profiles={"edge": ProfileConfig(disable=("fs_tool",))})
        apply_profile(registry, "edge", config)

        assert registry.get_disable_reason("fs_tool") == PROFILE_DISABLE_REASON

    def test_mcp_client_error_does_not_leak_the_profile(self):
        """The message an MCP client sees stays free of the profile name."""
        from toolregistry.tool import ToolTag

        pytest.importorskip("mcp")

        from toolregistry_server.adapters.mcp import route_table_to_mcp_server
        from toolregistry_server.adapters.mcp._compat import create_test_client
        from toolregistry_server.registry_builder import apply_profile
        from toolregistry_server.route_table import RouteTable

        registry = _tagged_registry({ToolTag.FILE_SYSTEM})
        apply_profile(registry, "remote")
        server = route_table_to_mcp_server(RouteTable(registry))

        async def _call():
            async with create_test_client(server) as client:
                return await client.call_tool("fs_tool", {})

        # A disabled tool is a protocol error, so the client raises.  Which
        # exception arrives depends on the SDK: v1 propagates ``McpError``
        # directly, v2 runs the session in an anyio task group and the error
        # comes back wrapped in an ``ExceptionGroup``.  Either way the text
        # the caller sees is what must stay free of the profile name.
        with pytest.raises(Exception) as exc_info:
            asyncio.run(_call())

        message = _error_text(exc_info.value)
        assert "remote" not in message
        assert "disabled" in message.lower()

    @pytest.mark.skipif(
        not _discovery_skips_disabled_tools(),
        reason="needs a core that invalidates the discovery index on disable",
    )
    def test_profile_disabled_tool_is_not_discoverable(self):
        """A tool the profile disables must not be discoverable (#84)."""
        from toolregistry.tool import ToolTag

        from toolregistry_server.registry_builder import apply_profile

        registry = _tagged_registry({ToolTag.FILE_SYSTEM})
        registry.enable_tool_discovery()
        try:
            apply_profile(registry, "remote")

            result = registry._tool_discovery.discover("read a file", top_k=5)
            if isinstance(result, dict):  # core < 0.19 shape
                result = result.get("results", [])
            assert "fs_tool" not in {item["name"] for item in result}
        finally:
            registry.disable_tool_discovery()


# ---------------------------------------------------------------------------
# Source-level include/exclude forwarding (#88)
# ---------------------------------------------------------------------------


def _supports_source_filters(source_cls) -> bool:
    """Whether the installed core's source type declares include/exclude."""
    import dataclasses

    names = {f.name for f in dataclasses.fields(source_cls)}
    return {"include", "exclude"} <= names


class TestSourceFilterForwarding:
    """Config-level filters must reach the core registration call (#88)."""

    def test_unset_filters_produce_no_kwargs(self):
        from types import SimpleNamespace

        from toolregistry_server.registry_builder import _source_filter_kwargs

        assert _source_filter_kwargs(SimpleNamespace()) == {}

    def test_explicit_empty_include_is_forwarded(self):
        """An empty tuple means "match nothing", not "no filter"."""
        from types import SimpleNamespace

        from toolregistry_server.registry_builder import _source_filter_kwargs

        assert _source_filter_kwargs(SimpleNamespace(include=())) == {"include": ()}

    def test_exclude_is_forwarded(self):
        from types import SimpleNamespace

        from toolregistry_server.registry_builder import _source_filter_kwargs

        assert _source_filter_kwargs(SimpleNamespace(exclude=("drop",))) == {
            "exclude": ("drop",)
        }

    def test_mcp_source_forwards_include(self):
        if not _supports_source_filters(MCPSource):
            pytest.skip("installed toolregistry predates source filters")

        registry = MagicMock()
        source = MCPSource(
            transport="sse",
            url="http://mcp.example.com/sse",
            namespace="hub",
            include=("hub-bash_execute",),
        )

        register_mcp_source(registry, source)

        kwargs = registry.register_from_mcp.call_args.kwargs
        assert kwargs["include"] == ("hub-bash_execute",)
        assert "exclude" not in kwargs

    def test_mcp_source_forwards_exclude(self):
        if not _supports_source_filters(MCPSource):
            pytest.skip("installed toolregistry predates source filters")

        registry = MagicMock()
        source = MCPSource(
            transport="sse",
            url="http://mcp.example.com/sse",
            exclude=("dangerous",),
        )

        register_mcp_source(registry, source)

        kwargs = registry.register_from_mcp.call_args.kwargs
        assert kwargs["exclude"] == ("dangerous",)
        assert "include" not in kwargs

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_openapi_source_forwards_include(self, mock_load):
        from toolregistry.config import OpenAPISource

        if not _supports_source_filters(OpenAPISource):
            pytest.skip("installed toolregistry predates source filters")

        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        source = OpenAPISource(
            url="http://example.com/openapi.json",
            include=("listPets",),
        )

        register_openapi_source(registry, source)

        kwargs = registry.register_from_openapi.call_args.kwargs
        assert kwargs["include"] == ("listPets",)
        assert "exclude" not in kwargs

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_openapi_source_forwards_exclude(self, mock_load):
        from toolregistry.config import OpenAPISource

        if not _supports_source_filters(OpenAPISource):
            pytest.skip("installed toolregistry predates source filters")

        mock_load.return_value = MINIMAL_SPEC
        registry = MagicMock()
        source = OpenAPISource(
            url="http://example.com/openapi.json",
            exclude=("deletePet",),
        )

        register_openapi_source(registry, source)

        kwargs = registry.register_from_openapi.call_args.kwargs
        assert kwargs["exclude"] == ("deletePet",)
