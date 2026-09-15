"""Tests for MCP and OpenAPI source registration in registry_builder.

Covers the gaps identified in toolregistry-server#24: no tests previously
existed for register_mcp_source, register_openapi_source, or apply_config
with non-Python source types.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestRegisterMCPSource:
    def test_stdio_transport(self):
        from toolregistry.config import MCPSource

        from toolregistry_server.registry_builder import register_mcp_source

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
        from toolregistry.config import MCPSource

        from toolregistry_server.registry_builder import register_mcp_source

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
        from toolregistry.config import MCPSource

        from toolregistry_server.registry_builder import register_mcp_source

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
        from toolregistry.config import MCPSource

        from toolregistry_server.registry_builder import register_mcp_source

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
        from toolregistry.config import MCPSource

        from toolregistry_server.registry_builder import register_mcp_source

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
        from toolregistry.config import MCPSource

        from toolregistry_server.registry_builder import register_mcp_source

        registry = MagicMock()
        source = MCPSource(
            transport="sse",
            url="http://localhost:8080/sse",
        )
        register_mcp_source(registry, source)

        call_args = registry.register_from_mcp.call_args
        assert call_args[1]["namespace"] is False


class TestRegisterOpenAPISource:
    MINIMAL_SPEC = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "servers": [{"url": "http://api.example.com"}],
        "paths": {},
    }

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_basic(self, mock_load):
        from toolregistry.config import OpenAPISource

        from toolregistry_server.registry_builder import register_openapi_source

        mock_load.return_value = self.MINIMAL_SPEC
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
        assert call_args[0][1] is self.MINIMAL_SPEC
        assert call_args[1]["namespace"] == "api_ns"
        assert call_args[1]["persistent"] is True

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_bearer_auth(self, mock_load):
        from toolregistry.config import AuthConfig, OpenAPISource

        from toolregistry_server.registry_builder import register_openapi_source

        mock_load.return_value = self.MINIMAL_SPEC
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
        from toolregistry.config import AuthConfig, OpenAPISource

        from toolregistry_server.registry_builder import register_openapi_source

        mock_load.return_value = self.MINIMAL_SPEC
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
        from toolregistry.config import OpenAPISource

        from toolregistry_server.registry_builder import register_openapi_source

        mock_load.return_value = self.MINIMAL_SPEC
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
        from toolregistry.config import OpenAPISource

        from toolregistry_server.registry_builder import register_openapi_source

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
        from toolregistry.config import OpenAPISource

        from toolregistry_server.registry_builder import register_openapi_source

        mock_load.return_value = self.MINIMAL_SPEC
        registry = MagicMock()
        source = OpenAPISource(url="http://example.com/openapi.json")
        register_openapi_source(registry, source)

        call_args = registry.register_from_openapi.call_args
        assert call_args[1]["namespace"] is False


class TestShouldLoadSourceMCPOpenAPI:
    def test_mcp_source_denylist_blocked(self):
        from toolregistry.config import MCPSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = MCPSource(
            transport="stdio",
            command=("python", "-m", "server"),
            namespace="blocked_ns",
        )
        config = ToolConfig(mode="denylist", disabled=("blocked_ns",))
        assert _should_load_source(source, config) is False

    def test_mcp_source_denylist_allowed(self):
        from toolregistry.config import MCPSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = MCPSource(
            transport="sse",
            url="http://localhost:8080/sse",
            namespace="allowed_ns",
        )
        config = ToolConfig(mode="denylist", disabled=("other_ns",))
        assert _should_load_source(source, config) is True

    def test_openapi_source_allowlist_included(self):
        from toolregistry.config import OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = OpenAPISource(
            url="http://example.com/openapi.json",
            namespace="included_ns",
        )
        config = ToolConfig(mode="allowlist", enabled=("included_ns",))
        assert _should_load_source(source, config) is True

    def test_openapi_source_allowlist_excluded(self):
        from toolregistry.config import OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = OpenAPISource(
            url="http://example.com/openapi.json",
            namespace="excluded_ns",
        )
        config = ToolConfig(mode="allowlist", enabled=("other_ns",))
        assert _should_load_source(source, config) is False

    def test_mcp_source_no_namespace_always_loaded(self):
        from toolregistry.config import MCPSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = MCPSource(transport="sse", url="http://localhost/sse")
        config = ToolConfig(mode="allowlist", enabled=("some_ns",))
        assert _should_load_source(source, config) is True


class TestApplyConfigMCPOpenAPI:
    MINIMAL_SPEC = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "servers": [{"url": "http://api.example.com"}],
        "paths": {},
    }

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_mcp_source_registered(self, mock_load):
        from toolregistry.config import MCPSource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

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
        from toolregistry.config import OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

        mock_load.return_value = self.MINIMAL_SPEC
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
        from toolregistry.config import MCPSource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

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
        from toolregistry.config import OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

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
        from toolregistry.config import MCPSource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

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
        from toolregistry.config import OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

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
        from toolregistry.config import MCPSource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

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

    def test_mcp_error_logged_and_continues(self):
        from toolregistry.config import MCPSource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

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

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_openapi_load_error_logged_and_continues(self, mock_load):
        from toolregistry.config import OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

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


class TestApplyConfigMultiSource:
    MINIMAL_SPEC = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "servers": [{"url": "http://api.example.com"}],
        "paths": {},
    }

    @patch("toolregistry.integrations.openapi.load_openapi_spec")
    def test_mcp_and_openapi_both_registered(self, mock_load):
        from toolregistry.config import MCPSource, OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

        mock_load.return_value = self.MINIMAL_SPEC
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
        from toolregistry.config import (
            MCPSource,
            OpenAPISource,
            PythonSource,
            ToolConfig,
        )

        from toolregistry_server.registry_builder import apply_config

        mock_load.return_value = self.MINIMAL_SPEC

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
        from toolregistry.config import MCPSource, OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

        mock_load.return_value = self.MINIMAL_SPEC
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
        from toolregistry.config import MCPSource, OpenAPISource, ToolConfig

        from toolregistry_server.registry_builder import apply_config

        mock_load.return_value = self.MINIMAL_SPEC
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
