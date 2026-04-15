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
        )


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
