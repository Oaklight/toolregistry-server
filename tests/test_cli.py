"""Tests for the CLI module."""

import argparse
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from toolregistry_server.cli import create_parser, main


class TestCreateParser:
    """Tests for create_parser function."""

    def test_parser_creation(self):
        """Test that parser is created successfully."""
        parser = create_parser()
        assert isinstance(parser, argparse.ArgumentParser)
        assert parser.prog == "toolregistry-server"

    def test_version_flag(self):
        """Test --version flag is recognized."""
        parser = create_parser()
        args = parser.parse_args(["--version"])
        assert args.version is True

    def test_version_short_flag(self):
        """Test -V flag is recognized."""
        parser = create_parser()
        args = parser.parse_args(["-V"])
        assert args.version is True

    def test_no_command(self):
        """Test parsing with no command."""
        parser = create_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_openapi_command(self):
        """Test openapi subcommand parsing."""
        parser = create_parser()
        args = parser.parse_args(["openapi"])
        assert args.command == "openapi"
        assert args.host == "0.0.0.0"
        assert args.port == 8000
        assert args.config is None
        assert args.tokens is None
        assert args.reload is False

    def test_openapi_with_options(self):
        """Test openapi subcommand with all options."""
        parser = create_parser()
        args = parser.parse_args(
            [
                "openapi",
                "--host",
                "127.0.0.1",
                "--port",
                "9000",
                "--config",
                "tools.yaml",
                "--tokens",
                "tokens.txt",
                "--reload",
            ]
        )
        assert args.command == "openapi"
        assert args.host == "127.0.0.1"
        assert args.port == 9000
        assert args.config == "tools.yaml"
        assert args.tokens == "tokens.txt"
        assert args.reload is True

    def test_mcp_command(self):
        """Test mcp subcommand parsing."""
        parser = create_parser()
        args = parser.parse_args(["mcp"])
        assert args.command == "mcp"
        assert args.transport == "stdio"
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.config is None

    def test_mcp_with_options(self):
        """Test mcp subcommand with all options."""
        parser = create_parser()
        args = parser.parse_args(
            [
                "mcp",
                "--transport",
                "sse",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--config",
                "tools.yaml",
            ]
        )
        assert args.command == "mcp"
        assert args.transport == "sse"
        assert args.host == "0.0.0.0"
        assert args.port == 9000
        assert args.config == "tools.yaml"

    def test_mcp_transport_choices(self):
        """Test mcp transport choices."""
        parser = create_parser()

        # Valid choices
        for transport in ["stdio", "sse", "streamable-http"]:
            args = parser.parse_args(["mcp", "--transport", transport])
            assert args.transport == transport

        # Invalid choice
        with pytest.raises(SystemExit):
            parser.parse_args(["mcp", "--transport", "invalid"])


class TestMain:
    """Tests for main function."""

    def test_version_output(self, capsys):
        """Test version output."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "toolregistry-server" in captured.out

    def test_no_command_shows_help(self, capsys):
        """Test that no command shows help."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0

    @patch("toolregistry_server.cli.openapi.run_openapi_server")
    def test_openapi_command_dispatch(self, mock_run):
        """Test openapi command dispatches correctly."""
        main(["openapi", "--port", "9000"])
        mock_run.assert_called_once_with(
            host="0.0.0.0",
            port=9000,
            config_path=None,
            tokens_path=None,
            reload=False,
            profile=None,
        )

    @patch("toolregistry_server.cli.openapi.run_openapi_server")
    def test_openapi_command_dispatch_with_profile(self, mock_run):
        """Test openapi command passes profile correctly."""
        main(["openapi", "--port", "9000", "--profile", "remote"])
        mock_run.assert_called_once_with(
            host="0.0.0.0",
            port=9000,
            config_path=None,
            tokens_path=None,
            reload=False,
            profile="remote",
        )

    @patch("toolregistry_server.cli.mcp.run_mcp_server")
    def test_mcp_command_dispatch(self, mock_run):
        """Test mcp command dispatches correctly."""
        main(["mcp", "--transport", "sse", "--port", "9000"])
        mock_run.assert_called_once_with(
            transport="sse",
            host="127.0.0.1",
            port=9000,
            config_path=None,
            profile=None,
        )

    @patch("toolregistry_server.cli.mcp.run_mcp_server")
    def test_mcp_command_dispatch_with_profile(self, mock_run):
        """Test mcp command passes profile correctly."""
        main(["mcp", "--profile", "remote"])
        mock_run.assert_called_once_with(
            transport="stdio",
            host="127.0.0.1",
            port=8000,
            config_path=None,
            profile="remote",
        )


class TestRunOpenAPIServerWithRegistry:
    """Tests for run_openapi_server with a pre-built registry."""

    @patch("uvicorn.run")
    @patch("toolregistry_server.openapi.create_openapi_app")
    @patch("toolregistry_server.RouteTable")
    def test_run_with_registry_skips_config(
        self, mock_route_table_cls, mock_create_app, mock_uvicorn_run
    ):
        """When registry is provided, config loading is bypassed."""
        from unittest.mock import MagicMock

        from toolregistry_server.cli.openapi import run_openapi_server

        registry = MagicMock()
        mock_route_table = MagicMock()
        mock_route_table.list_routes.return_value = []
        mock_route_table_cls.return_value = mock_route_table
        mock_create_app.return_value = MagicMock()

        with (
            patch("toolregistry_server.cli.openapi.load_config") as mock_load_config,
            patch(
                "toolregistry_server.cli.openapi.create_registry_from_config"
            ) as mock_create_reg,
        ):
            run_openapi_server(host="127.0.0.1", port=9001, registry=registry)
            mock_load_config.assert_not_called()
            mock_create_reg.assert_not_called()

        mock_route_table_cls.assert_called_once_with(registry)
        mock_uvicorn_run.assert_called_once()

    @patch("uvicorn.run")
    @patch("toolregistry_server.openapi.create_openapi_app")
    @patch("toolregistry_server.RouteTable")
    def test_run_without_registry_uses_config(
        self, mock_route_table_cls, mock_create_app, mock_uvicorn_run
    ):
        """When registry is None, config path is used to build registry."""
        from unittest.mock import MagicMock

        from toolregistry_server.cli.openapi import run_openapi_server

        mock_route_table = MagicMock()
        mock_route_table.list_routes.return_value = []
        mock_route_table_cls.return_value = mock_route_table
        mock_create_app.return_value = MagicMock()
        mock_registry = MagicMock()

        with (
            patch("toolregistry_server.cli.openapi.load_config") as mock_load_config,
            patch(
                "toolregistry_server.cli.openapi.create_registry_from_config"
            ) as mock_create_reg,
        ):
            mock_load_config.return_value = None
            mock_create_reg.return_value = mock_registry

            run_openapi_server(host="127.0.0.1", port=9001)

            mock_load_config.assert_called_once_with(None)
            mock_create_reg.assert_called_once_with(None)


class TestRunMCPServerWithRegistry:
    """Tests for run_mcp_server with a pre-built registry."""

    @patch("asyncio.run")
    @patch("toolregistry_server.mcp.route_table_to_mcp_server")
    @patch("toolregistry_server.RouteTable")
    def test_run_with_registry_skips_config(
        self, mock_route_table_cls, mock_to_mcp, mock_asyncio_run
    ):
        """When registry is provided, config loading is bypassed."""
        from unittest.mock import MagicMock

        from toolregistry_server.cli.mcp import run_mcp_server

        registry = MagicMock()
        mock_route_table = MagicMock()
        mock_route_table.list_routes.return_value = []
        mock_route_table_cls.return_value = mock_route_table
        mock_to_mcp.return_value = MagicMock()

        with (
            patch("toolregistry_server.cli.mcp.load_config") as mock_load_config,
            patch(
                "toolregistry_server.cli.mcp.create_registry_from_config"
            ) as mock_create_reg,
        ):
            run_mcp_server(transport="stdio", registry=registry)
            mock_load_config.assert_not_called()
            mock_create_reg.assert_not_called()

        mock_route_table_cls.assert_called_once_with(registry)

    @patch("asyncio.run")
    @patch("toolregistry_server.mcp.route_table_to_mcp_server")
    @patch("toolregistry_server.RouteTable")
    def test_run_without_registry_uses_config(
        self, mock_route_table_cls, mock_to_mcp, mock_asyncio_run
    ):
        """When registry is None, config path is used to build registry."""
        from unittest.mock import MagicMock

        from toolregistry_server.cli.mcp import run_mcp_server

        mock_route_table = MagicMock()
        mock_route_table.list_routes.return_value = []
        mock_route_table_cls.return_value = mock_route_table
        mock_to_mcp.return_value = MagicMock()
        mock_registry = MagicMock()

        with (
            patch("toolregistry_server.cli.mcp.load_config") as mock_load_config,
            patch(
                "toolregistry_server.cli.mcp.create_registry_from_config"
            ) as mock_create_reg,
        ):
            mock_load_config.return_value = None
            mock_create_reg.return_value = mock_registry

            run_mcp_server(transport="stdio")

            mock_load_config.assert_called_once_with(None)
            mock_create_reg.assert_called_once_with(None)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_none(self):
        """Test load_config with None path."""
        from toolregistry_server.cli.openapi import load_config

        result = load_config(None)
        assert result is None

    def test_load_config_not_found(self):
        """Test load_config with non-existent file."""
        from toolregistry_server.cli.openapi import load_config

        with pytest.raises(SystemExit):
            load_config("/nonexistent/path/config.json")

    def test_load_config_valid_jsonc(self, tmp_path):
        """Test load_config with valid JSONC file."""
        from toolregistry.config import ToolConfig

        from toolregistry_server.cli.openapi import load_config

        config_file = tmp_path / "config.jsonc"
        config_file.write_text('{\n  // comment\n  "tools": []\n}', encoding="utf-8")

        result = load_config(str(config_file))
        assert isinstance(result, ToolConfig)
        assert result.tools == ()

    def test_load_config_valid_yaml(self, tmp_path):
        """Test load_config with valid YAML file."""
        from toolregistry.config import ToolConfig

        from toolregistry_server.cli.openapi import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("tools: []\n", encoding="utf-8")

        result = load_config(str(config_file))
        assert isinstance(result, ToolConfig)
        assert result.tools == ()

    def test_load_config_invalid_json(self, tmp_path):
        """Test load_config with invalid JSON."""
        from toolregistry_server.cli.openapi import load_config

        config_file = tmp_path / "bad.json"
        config_file.write_text("not valid json", encoding="utf-8")

        with pytest.raises(SystemExit):
            load_config(str(config_file))

    def test_load_config_invalid_mode(self, tmp_path):
        """Test load_config with invalid mode raises SystemExit."""
        from toolregistry_server.cli.openapi import load_config

        config_file = tmp_path / "bad.yaml"
        config_file.write_text("mode: invalid\ntools: []\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            load_config(str(config_file))


class TestLoadTokens:
    """Tests for load_tokens function."""

    def test_load_tokens_none(self):
        """Test load_tokens with None path."""
        from toolregistry_server.cli.openapi import load_tokens

        result = load_tokens(None)
        assert result == []

    def test_load_tokens_not_found(self):
        """Test load_tokens with non-existent file."""
        from toolregistry_server.cli.openapi import load_tokens

        with pytest.raises(SystemExit):
            load_tokens("/nonexistent/path/tokens.txt")

    def test_load_tokens_valid_file(self):
        """Test load_tokens with valid file."""
        from toolregistry_server.cli.openapi import load_tokens

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("token1\ntoken2\n# comment\n\ntoken3")
            f.flush()

            result = load_tokens(f.name)
            assert result == ["token1", "token2", "token3"]

            Path(f.name).unlink()


class TestCreateRegistryFromConfig:
    """Tests for create_registry_from_config function."""

    def test_create_registry_no_config(self):
        """Test creating registry with no config."""
        from toolregistry_server.cli.openapi import create_registry_from_config

        registry = create_registry_from_config(None)
        assert len(registry._tools) == 0

    def test_create_registry_empty_tools(self):
        """Test creating registry with empty tools list."""
        from toolregistry.config import ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        config = ToolConfig(tools=())
        registry = create_registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_invalid_class(self):
        """Test creating registry with a non-existent Python class logs warning."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        config = ToolConfig(
            tools=(
                PythonSource(
                    class_path="nonexistent_module_xyz.NoClass",
                    namespace="test",
                ),
            ),
        )
        # Should not raise — logs a warning instead
        registry = create_registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_python_module(self, tmp_path, monkeypatch):
        """Test creating registry with a Python module source."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        # Create a temporary module
        mod_file = tmp_path / "test_tools.py"
        mod_file.write_text(
            "def hello(name: str) -> str:\n"
            '    """Greet someone."""\n'
            '    return f"Hello, {name}!"\n',
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        config = ToolConfig(
            tools=(PythonSource(module_path="test_tools", namespace="test"),),
        )
        registry = create_registry_from_config(config)
        tool_names = [t.name for t in registry._tools.values()]
        assert any("hello" in name for name in tool_names)

    def test_create_registry_disabled_source(self, tmp_path, monkeypatch):
        """Test that disabled sources are skipped."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        mod_file = tmp_path / "skip_tools.py"
        mod_file.write_text(
            "def skipped() -> str:\n    return 'nope'\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        config = ToolConfig(
            tools=(
                PythonSource(
                    module_path="skip_tools",
                    namespace="skip",
                    enabled=False,
                ),
            ),
        )
        registry = create_registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_denylist_filtering(self, tmp_path, monkeypatch):
        """Test denylist mode filters by namespace."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        mod_file = tmp_path / "deny_tools.py"
        mod_file.write_text(
            "def denied() -> str:\n    return 'denied'\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        config = ToolConfig(
            mode="denylist",
            disabled=("blocked",),
            tools=(
                PythonSource(
                    module_path="deny_tools",
                    namespace="blocked",
                ),
            ),
        )
        registry = create_registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_allowlist_filtering(self, tmp_path, monkeypatch):
        """Test allowlist mode only loads matching namespaces."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        mod_file = tmp_path / "allow_tools.py"
        mod_file.write_text(
            "def allowed() -> str:\n    return 'yes'\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        config = ToolConfig(
            mode="allowlist",
            enabled=("allowed_ns",),
            tools=(
                PythonSource(
                    module_path="allow_tools",
                    namespace="not_allowed",
                ),
            ),
        )
        registry = create_registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_invalid_module(self):
        """Test that invalid module logs warning and continues."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        config = ToolConfig(
            tools=(
                PythonSource(
                    module_path="nonexistent_module_xyz",
                    namespace="bad",
                ),
            ),
        )
        # Should not raise — logs a warning instead
        registry = create_registry_from_config(config)
        assert len(registry._tools) == 0


class TestShouldLoadSource:
    """Tests for _should_load_source helper."""

    def test_no_namespace_always_loads(self):
        """Sources without namespace are always loaded."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import _should_load_source

        source = PythonSource(module_path="mod")
        config = ToolConfig(mode="allowlist", enabled=("other",))
        assert _should_load_source(source, config) is True

    def test_denylist_allows_unmatched(self):
        """Denylist mode allows sources not in disabled list."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import _should_load_source

        source = PythonSource(module_path="mod", namespace="safe")
        config = ToolConfig(mode="denylist", disabled=("blocked",))
        assert _should_load_source(source, config) is True

    def test_denylist_blocks_matched(self):
        """Denylist mode blocks sources in disabled list."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import _should_load_source

        source = PythonSource(module_path="mod", namespace="blocked")
        config = ToolConfig(mode="denylist", disabled=("blocked",))
        assert _should_load_source(source, config) is False

    def test_denylist_blocks_prefix(self):
        """Denylist mode blocks child namespaces."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import _should_load_source

        source = PythonSource(module_path="mod", namespace="web/search")
        config = ToolConfig(mode="denylist", disabled=("web",))
        assert _should_load_source(source, config) is False

    def test_allowlist_allows_matched(self):
        """Allowlist mode allows sources in enabled list."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import _should_load_source

        source = PythonSource(module_path="mod", namespace="calc")
        config = ToolConfig(mode="allowlist", enabled=("calc",))
        assert _should_load_source(source, config) is True

    def test_allowlist_blocks_unmatched(self):
        """Allowlist mode blocks sources not in enabled list."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import _should_load_source

        source = PythonSource(module_path="mod", namespace="other")
        config = ToolConfig(mode="allowlist", enabled=("calc",))
        assert _should_load_source(source, config) is False


class TestNsMatches:
    """Tests for _ns_matches helper."""

    def test_exact_match(self):
        from toolregistry_server.cli.openapi import _ns_matches

        assert _ns_matches("web", "web") is True

    def test_prefix_match(self):
        from toolregistry_server.cli.openapi import _ns_matches

        assert _ns_matches("web/search", "web") is True

    def test_no_match(self):
        from toolregistry_server.cli.openapi import _ns_matches

        assert _ns_matches("calculator", "web") is False

    def test_partial_no_match(self):
        """'webhook' should NOT match pattern 'web'."""
        from toolregistry_server.cli.openapi import _ns_matches

        assert _ns_matches("webhook", "web") is False


class TestCreateRegistryFromConfigWithHooks:
    """Tests for create_registry_from_config with post_register_hooks."""

    def test_hook_called_for_each_tool(self, tmp_path, monkeypatch):
        """Hook is invoked once per registered tool."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        mod_file = tmp_path / "hook_tools.py"
        mod_file.write_text(
            "def alpha() -> str:\n    return 'a'\ndef beta() -> str:\n    return 'b'\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        called: list[str] = []

        def my_hook(name, tool, registry):
            called.append(name)
            return None

        config = ToolConfig(
            tools=(PythonSource(module_path="hook_tools", namespace="h"),)
        )
        registry = create_registry_from_config(config, post_register_hooks=[my_hook])
        assert len(called) == 2
        assert len(registry._tools) == 2

    def test_hook_returning_string_disables_tool(self, tmp_path, monkeypatch):
        """Hook returning a non-empty string auto-disables the tool."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.cli.openapi import create_registry_from_config

        mod_file = tmp_path / "disable_tools.py"
        mod_file.write_text(
            "def bad_tool() -> str:\n    return 'bad'\n"
            "def good_tool() -> str:\n    return 'good'\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        def selective_hook(name, tool, registry):
            if "bad" in name:
                return "blocked by hook"
            return None

        config = ToolConfig(
            tools=(PythonSource(module_path="disable_tools", namespace="d"),)
        )
        registry = create_registry_from_config(
            config, post_register_hooks=[selective_hook]
        )
        assert len(registry._tools) == 2
        enabled = [n for n in registry._tools if registry.is_enabled(n)]
        assert all("good" in n for n in enabled)

    def test_no_hooks_behaviour_unchanged(self):
        """Passing no hooks leaves existing behaviour intact."""
        from toolregistry_server.cli.openapi import create_registry_from_config

        registry = create_registry_from_config(None)
        assert len(registry._tools) == 0

    def test_parser_has_profile_openapi(self):
        """Parser exposes --profile for openapi subcommand."""
        from toolregistry_server.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["openapi", "--profile", "remote"])
        assert args.profile == "remote"

    def test_parser_has_profile_mcp(self):
        """Parser exposes --profile for mcp subcommand."""
        from toolregistry_server.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["mcp", "--profile", "local"])
        assert args.profile == "local"

    def test_parser_profile_default_none(self):
        """--profile defaults to None when not provided."""
        from toolregistry_server.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["openapi"])
        assert args.profile is None


class TestApplyProfile:
    """Tests for apply_profile()."""

    def test_remote_profile_disables_tagged_tools(self, tmp_path, monkeypatch):
        """remote profile disables file_system/destructive/privileged tools."""
        from toolregistry import ToolRegistry
        from toolregistry.tool import Tool, ToolMetadata, ToolTag

        from toolregistry_server.cli.openapi import apply_profile

        registry = ToolRegistry()

        def fs_tool() -> str:
            """A filesystem tool."""
            return "fs"

        def safe_tool() -> str:
            """A safe network tool."""
            return "safe"

        t1 = Tool.from_function(fs_tool)
        t1.metadata = ToolMetadata(tags={ToolTag.FILE_SYSTEM})
        t2 = Tool.from_function(safe_tool)
        t2.metadata = ToolMetadata(tags={ToolTag.NETWORK})

        registry._tools["fs_tool"] = t1
        registry._tools["safe_tool"] = t2

        apply_profile(registry, "remote")

        assert not registry.is_enabled("fs_tool")
        assert registry.is_enabled("safe_tool")

    def test_local_profile_no_change(self, tmp_path):
        """local profile applies no tag filter."""
        from toolregistry import ToolRegistry
        from toolregistry.tool import Tool, ToolMetadata, ToolTag

        from toolregistry_server.cli.openapi import apply_profile

        registry = ToolRegistry()

        def any_tool() -> str:
            """Any tool."""
            return "x"

        t = Tool.from_function(any_tool)
        t.metadata = ToolMetadata(tags={ToolTag.FILE_SYSTEM})
        registry._tools["any_tool"] = t

        apply_profile(registry, "local")
        assert registry.is_enabled("any_tool")

    def test_unknown_profile_does_not_raise(self):
        """Unknown profile name does not raise an exception."""
        from toolregistry import ToolRegistry

        from toolregistry_server.cli.openapi import apply_profile

        registry = ToolRegistry()
        # Should complete without raising regardless of unknown profile
        apply_profile(registry, "nonexistent_profile")
        # Registry should be unchanged (no tools to disable anyway)
        assert len(registry._tools) == 0
