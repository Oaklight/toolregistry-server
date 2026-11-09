"""Tests for the CLI module."""

import argparse
import dataclasses
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from toolregistry_server.cli import CLI, main, print_banner


class TestCreateParser:
    """Tests for create_parser function."""

    def test_parser_creation(self):
        """Test that parser is created successfully."""
        parser = CLI().create_parser()
        assert isinstance(parser, argparse.ArgumentParser)
        assert parser.prog == "toolregistry-server"

    def test_version_flag(self):
        """Test --version flag is recognized."""
        parser = CLI().create_parser()
        args = parser.parse_args(["--version"])
        assert args.version is True

    def test_version_short_flag(self):
        """Test -V flag is recognized."""
        parser = CLI().create_parser()
        args = parser.parse_args(["-V"])
        assert args.version is True

    def test_no_command(self):
        """Test parsing with no command."""
        parser = CLI().create_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_openapi_command(self):
        """Test openapi subcommand parsing."""
        parser = CLI().create_parser()
        args = parser.parse_args(["openapi"])
        assert args.command == "openapi"
        assert args.host == "0.0.0.0"
        assert args.port == 8000
        assert args.config is None
        assert args.tokens is None

    def test_openapi_with_options(self):
        """Test openapi subcommand with all options."""
        parser = CLI().create_parser()
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
            ]
        )
        assert args.command == "openapi"
        assert args.host == "127.0.0.1"
        assert args.port == 9000
        assert args.config == "tools.yaml"
        assert args.tokens == "tokens.txt"

    def test_openapi_max_body_size_defaults_to_one_mib(self):
        """The httpserver body cap defaults to 1 MiB (#134)."""
        parser = CLI().create_parser()
        args = parser.parse_args(["openapi"])
        assert args.max_body_size == 1_048_576

    def test_openapi_max_body_size_override(self):
        """--max-body-size reaches the parsed namespace (#134)."""
        parser = CLI().create_parser()
        args = parser.parse_args(["openapi", "--max-body-size", "2097152"])
        assert args.max_body_size == 2_097_152

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_openapi_max_body_size_must_be_positive(self, value):
        """A non-positive cap is a parser error, not a silent no-op (#134)."""
        parser = CLI().create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["openapi", "--max-body-size", value])

    def test_mcp_command(self):
        """Test mcp subcommand parsing."""
        parser = CLI().create_parser()
        args = parser.parse_args(["mcp"])
        assert args.command == "mcp"
        assert args.transport == "stdio"
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.config is None

    def test_mcp_with_options(self):
        """Test mcp subcommand with all options."""
        parser = CLI().create_parser()
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
        parser = CLI().create_parser()

        # Valid choices
        for transport in ["stdio", "sse", "streamable-http"]:
            args = parser.parse_args(["mcp", "--transport", transport])
            assert args.transport == transport

        # Invalid choice
        with pytest.raises(SystemExit):
            parser.parse_args(["mcp", "--transport", "invalid"])


class TestConfigureSubparsers:
    """Tests for configure_subparsers hook."""

    def test_hook_receives_subparsers(self):
        """configure_subparsers is called with the subparser dict."""
        received = {}

        class CustomCLI(CLI):
            def configure_subparsers(self, subparsers):
                received.update(subparsers)

        CustomCLI().create_parser()
        assert "openapi" in received
        assert "mcp" in received
        assert all(isinstance(sp, argparse.ArgumentParser) for sp in received.values())

    def test_hook_adds_arguments(self):
        """Arguments added in configure_subparsers are available."""

        class CustomCLI(CLI):
            def configure_subparsers(self, subparsers):
                for sp in subparsers.values():
                    sp.add_argument("--custom-flag", type=int, default=42)

        parser = CustomCLI().create_parser()
        args = parser.parse_args(["openapi", "--custom-flag", "99"])
        assert args.custom_flag == 99

    def test_base_hook_is_noop(self):
        """Base CLI.configure_subparsers does nothing (no error)."""
        parser = CLI().create_parser()
        args = parser.parse_args(["openapi"])
        assert args.command == "openapi"


class TestMain:
    """Tests for main function."""

    def test_version_output(self, capsys):
        """Test version output."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "ToolRegistry Server" in captured.out

    def test_no_command_shows_help(self, capsys):
        """Test that no command shows help."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0

    @patch.object(
        __import__("toolregistry_server.app", fromlist=["App"]).App, "serve_openapi"
    )
    def test_openapi_command_dispatch(self, mock_serve):
        """Test openapi command dispatches correctly."""
        main(["openapi", "--config", "tools.yaml", "--port", "9000"])
        mock_serve.assert_called_once_with(
            config_path="tools.yaml",
            profile=None,
            host="0.0.0.0",
            port=9000,
            tokens_path=None,
            max_body_size=1_048_576,
            regex_timeout=0.05,
            regex_budget=0.25,
            strict_spec_regex=False,
            execution_log=None,
            config_reload=False,
            write_config=True,
        )

    @patch.object(
        __import__("toolregistry_server.app", fromlist=["App"]).App, "serve_openapi"
    )
    def test_openapi_command_dispatch_with_profile(self, mock_serve):
        """Test openapi command passes profile correctly."""
        main(
            [
                "openapi",
                "--config",
                "tools.yaml",
                "--port",
                "9000",
                "--profile",
                "remote",
            ]
        )
        mock_serve.assert_called_once_with(
            config_path="tools.yaml",
            profile="remote",
            host="0.0.0.0",
            port=9000,
            tokens_path=None,
            max_body_size=1_048_576,
            regex_timeout=0.05,
            regex_budget=0.25,
            strict_spec_regex=False,
            execution_log=None,
            config_reload=False,
            write_config=True,
        )

    @patch.object(
        __import__("toolregistry_server.app", fromlist=["App"]).App, "serve_openapi"
    )
    def test_openapi_command_passes_keys_db(self, mock_serve):
        """--keys-db reaches the adapter (#186)."""
        main(["openapi", "--config", "tools.yaml", "--keys-db", "/tmp/keys.db"])
        assert mock_serve.call_args.kwargs["keys_db"] == "/tmp/keys.db"

    @patch.object(
        __import__("toolregistry_server.app", fromlist=["App"]).App, "serve_openapi"
    )
    def test_openapi_command_passes_max_body_size(self, mock_serve):
        """--max-body-size reaches the adapter (#134)."""
        main(["openapi", "--config", "tools.yaml", "--max-body-size", "2048"])
        assert mock_serve.call_args.kwargs["max_body_size"] == 2048

    @patch("toolregistry_server.app.App.serve_mcp")
    def test_mcp_command_dispatch(self, mock_serve):
        """Test mcp command dispatches correctly."""
        main(["mcp", "--config", "tools.yaml", "--transport", "sse", "--port", "9000"])
        mock_serve.assert_called_once_with(
            config_path="tools.yaml",
            profile=None,
            host="127.0.0.1",
            port=9000,
            transport="sse",
            tokens_path=None,
            server_url=None,
            tools_list_ttl_ms=None,
            tools_list_cache_scope=None,
            allow_operator_token=False,
            client_concurrency=8,
            concurrency_timeout=30.0,
            execution_log=None,
            config_reload=False,
            write_config=True,
        )

    @patch("toolregistry_server.app.App.serve_mcp")
    def test_mcp_command_passes_keys_db(self, mock_serve):
        """--keys-db reaches the adapter (#186)."""
        main(["mcp", "--config", "tools.yaml", "--keys-db", "/tmp/keys.db"])
        assert mock_serve.call_args.kwargs["keys_db"] == "/tmp/keys.db"

    @patch("toolregistry_server.app.App.serve_gateway")
    def test_gateway_command_passes_keys_db(self, mock_serve):
        """--keys-db reaches the gateway adapter (#200)."""
        main(["gateway", "--config", "tools.yaml", "--keys-db", "/tmp/keys.db"])
        assert mock_serve.call_args.kwargs["keys_db"] == "/tmp/keys.db"

    @patch("toolregistry_server.app.App.serve_mcp")
    def test_mcp_command_passes_tokens_path(self, mock_serve):
        """--tokens reaches the adapter, which already knew how to load it (#99)."""
        main(
            [
                "mcp",
                "--config",
                "tools.yaml",
                "--transport",
                "streamable-http",
                "--tokens",
                "/etc/app/tokens.txt",
            ]
        )
        assert mock_serve.call_args.kwargs["tokens_path"] == "/etc/app/tokens.txt"

    @patch("toolregistry_server.app.App.serve_mcp")
    def test_mcp_command_passes_cache_hint_flags(self, mock_serve):
        """tools/list cache hints are configurable from the CLI (#83)."""
        main(
            [
                "mcp",
                "--config",
                "tools.yaml",
                "--tools-list-ttl-ms",
                "0",
                "--tools-list-cache-scope",
                "public",
            ]
        )
        kwargs = mock_serve.call_args.kwargs
        assert kwargs["tools_list_ttl_ms"] == 0
        assert kwargs["tools_list_cache_scope"] == "public"

    @patch("toolregistry_server.app.App.serve_mcp")
    def test_mcp_command_passes_server_url(self, mock_serve):
        """--server-url reaches the adapter so metadata can name the proxy (#78)."""
        main(
            [
                "mcp",
                "--config",
                "tools.yaml",
                "--transport",
                "streamable-http",
                "--server-url",
                "https://tools.example.com/mcp",
            ]
        )
        assert (
            mock_serve.call_args.kwargs["server_url"] == "https://tools.example.com/mcp"
        )

    @patch("toolregistry_server.app.App.serve_mcp")
    def test_mcp_command_dispatch_with_profile(self, mock_serve):
        """Test mcp command passes profile correctly."""
        main(["mcp", "--config", "tools.yaml", "--profile", "remote"])
        mock_serve.assert_called_once_with(
            config_path="tools.yaml",
            profile="remote",
            host="127.0.0.1",
            port=8000,
            transport="stdio",
            tokens_path=None,
            server_url=None,
            tools_list_ttl_ms=None,
            tools_list_cache_scope=None,
            allow_operator_token=False,
            client_concurrency=8,
            concurrency_timeout=30.0,
            execution_log=None,
            config_reload=False,
            write_config=True,
        )

    def test_no_config_exits(self):
        """Test that missing --config exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["openapi", "--port", "9000"])
        assert exc_info.value.code == 1


class TestBannerStream:
    """The startup banner must never reach stdout (#80).

    Over the MCP stdio transport stdout is the JSON-RPC channel; a client
    reading it strictly would see banner art between its messages.
    """

    def test_print_banner_writes_to_stderr(self, capsys):
        """print_banner() keeps stdout clean and uses stderr."""
        print_banner(version="1.2.3", banner_art="X")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Version 1.2.3" in captured.err

    def test_print_banner_accepts_explicit_stream(self, capsys):
        """Callers can redirect the banner to another stream."""
        buffer = io.StringIO()
        print_banner(version="1.2.3", banner_art="X", file=buffer)

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        assert "Version 1.2.3" in buffer.getvalue()

    @patch("toolregistry_server.app.App.serve_mcp")
    def test_stdio_mcp_startup_leaves_stdout_empty(self, mock_serve, capsys):
        """A stdio MCP launch writes nothing but JSON-RPC to stdout."""
        main(["mcp", "--config", "tools.yaml", "--transport", "stdio"])

        mock_serve.assert_called_once()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Version" in captured.err

    @patch("toolregistry_server.app.App.serve_mcp")
    def test_no_banner_silences_both_streams(self, mock_serve, capsys):
        """--no-banner still suppresses the banner entirely."""
        main(["--no-banner", "mcp", "--config", "tools.yaml"])

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Version" not in captured.err


class TestAppServe:
    """Tests for App.serve / serve_openapi / serve_mcp."""

    def test_serve_openapi_calls_adapter(self):
        """serve_openapi calls OpenAPIAdapter.create_and_run."""
        from unittest.mock import MagicMock

        from toolregistry_server.app import App

        app = App()
        mock_registry = MagicMock()
        app.prepare_registry = MagicMock(return_value=mock_registry)

        with patch(
            "toolregistry_server.adapters.openapi.OpenAPIAdapter.create_and_run"
        ) as mock_run:
            app.serve_openapi(host="127.0.0.1", port=9001, registry=mock_registry)
            mock_run.assert_called_once()

    def test_serve_mcp_calls_adapter(self):
        """serve_mcp calls MCPAdapter.create_and_run."""
        from unittest.mock import MagicMock

        from toolregistry_server.app import App

        app = App()
        mock_registry = MagicMock()
        app.prepare_registry = MagicMock(return_value=mock_registry)

        with patch(
            "toolregistry_server.adapters.mcp.MCPAdapter.create_and_run"
        ) as mock_run:
            app.serve_mcp(transport="stdio", registry=mock_registry)
            mock_run.assert_called_once()

    def test_prepare_registry_requires_config_or_registry(self):
        """prepare_registry raises ValueError without config_path or registry."""
        from toolregistry_server.app import App

        app = App()
        with pytest.raises(ValueError, match="config_path.*registry"):
            app.prepare_registry()


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_not_found(self):
        """Test load_config with non-existent file."""
        from toolregistry_server.registry_builder import load_config

        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.json")

    def test_load_config_valid_jsonc(self, tmp_path):
        """Test load_config with valid JSONC file."""
        from toolregistry.config import ToolConfig

        from toolregistry_server.registry_builder import load_config

        config_file = tmp_path / "config.jsonc"
        config_file.write_text('{\n  // comment\n  "tools": []\n}', encoding="utf-8")

        result = load_config(str(config_file))
        assert isinstance(result, ToolConfig)
        assert result.tools == ()

    def test_load_config_valid_yaml(self, tmp_path):
        """Test load_config with valid YAML file."""
        from toolregistry.config import ToolConfig

        from toolregistry_server.registry_builder import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("tools: []\n", encoding="utf-8")

        result = load_config(str(config_file))
        assert isinstance(result, ToolConfig)
        assert result.tools == ()

    def test_load_config_invalid_json(self, tmp_path):
        """Test load_config with invalid JSON."""
        from toolregistry_server.registry_builder import load_config

        config_file = tmp_path / "bad.json"
        config_file.write_text("not valid json", encoding="utf-8")

        with pytest.raises((ValueError, KeyError, FileNotFoundError)):
            load_config(str(config_file))

    def test_load_config_invalid_mode(self, tmp_path):
        """Test load_config with invalid mode raises error."""
        from toolregistry.config import ConfigError

        from toolregistry_server.registry_builder import load_config

        config_file = tmp_path / "bad.yaml"
        config_file.write_text("mode: invalid\ntools: []\n", encoding="utf-8")

        with pytest.raises(ConfigError):
            load_config(str(config_file))


class TestLoadTokens:
    """Tests for load_tokens function."""

    def test_load_tokens_none(self):
        """Test load_tokens with None path."""
        from toolregistry_server.auth import load_tokens

        result = load_tokens(None)
        assert result == []

    def test_load_tokens_not_found(self):
        """Test load_tokens with non-existent file."""
        from toolregistry_server.auth import load_tokens

        with pytest.raises(FileNotFoundError):
            load_tokens("/nonexistent/path/tokens.txt")

    def test_load_tokens_valid_file(self):
        """Test load_tokens with valid file."""
        from toolregistry_server.auth import load_tokens

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("token1\ntoken2\n# comment\n\ntoken3")
            f.flush()

            result = load_tokens(f.name)
            assert result == ["token1", "token2", "token3"]

            Path(f.name).unlink()


class TestCreateRegistryFromConfig:
    """Tests for registry_from_config function."""

    def test_create_registry_empty_config(self):
        """Test creating registry with empty config."""
        from toolregistry.config import ToolConfig

        from toolregistry_server.registry_builder import registry_from_config

        config = ToolConfig(tools=())
        registry = registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_empty_tools(self):
        """Test creating registry with empty tools list."""
        from toolregistry.config import ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

        config = ToolConfig(tools=())
        registry = registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_invalid_class(self):
        """Test creating registry with a non-existent Python class logs warning."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

        config = ToolConfig(
            tools=(
                PythonSource(
                    class_path="nonexistent_module_xyz.NoClass",
                    namespace="test",
                ),
            ),
        )
        # Should not raise — logs a warning instead
        registry = registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_python_module(self, tmp_path, monkeypatch):
        """Test creating registry with a Python module source."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

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
        registry = registry_from_config(config)
        tool_names = [t.name for t in registry._tools.values()]
        assert any("hello" in name for name in tool_names)

    def test_create_registry_disabled_source(self, tmp_path, monkeypatch):
        """Test that disabled sources are skipped."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

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
        registry = registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_denylist_filtering(self, tmp_path, monkeypatch):
        """Test denylist mode filters by namespace."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

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
        registry = registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_allowlist_filtering(self, tmp_path, monkeypatch):
        """Test allowlist mode only loads matching namespaces."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

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
        registry = registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_invalid_module(self):
        """Test that invalid module logs warning and continues."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

        config = ToolConfig(
            tools=(
                PythonSource(
                    module_path="nonexistent_module_xyz",
                    namespace="bad",
                ),
            ),
        )
        # Should not raise — logs a warning instead
        registry = registry_from_config(config)
        assert len(registry._tools) == 0


class TestShouldLoadSource:
    """Tests for _should_load_source helper."""

    def test_no_namespace_always_loads(self):
        """Sources without namespace are always loaded."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = PythonSource(module_path="mod")
        config = ToolConfig(mode="allowlist", enabled=("other",))
        assert _should_load_source(source, config) is True

    def test_denylist_allows_unmatched(self):
        """Denylist mode allows sources not in disabled list."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = PythonSource(module_path="mod", namespace="safe")
        config = ToolConfig(mode="denylist", disabled=("blocked",))
        assert _should_load_source(source, config) is True

    def test_denylist_blocks_matched(self):
        """Denylist mode blocks sources in disabled list."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = PythonSource(module_path="mod", namespace="blocked")
        config = ToolConfig(mode="denylist", disabled=("blocked",))
        assert _should_load_source(source, config) is False

    def test_denylist_blocks_prefix(self):
        """Denylist mode blocks child namespaces."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = PythonSource(module_path="mod", namespace="web/search")
        config = ToolConfig(mode="denylist", disabled=("web",))
        assert _should_load_source(source, config) is False

    def test_allowlist_allows_matched(self):
        """Allowlist mode allows sources in enabled list."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = PythonSource(module_path="mod", namespace="calc")
        config = ToolConfig(mode="allowlist", enabled=("calc",))
        assert _should_load_source(source, config) is True

    def test_allowlist_blocks_unmatched(self):
        """Allowlist mode blocks sources not in enabled list."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import _should_load_source

        source = PythonSource(module_path="mod", namespace="other")
        config = ToolConfig(mode="allowlist", enabled=("calc",))
        assert _should_load_source(source, config) is False


class TestNsMatches:
    """Tests for _ns_matches helper."""

    def test_exact_match(self):
        from toolregistry_server.registry_builder import _ns_matches

        assert _ns_matches("web", "web") is True

    def test_prefix_match(self):
        from toolregistry_server.registry_builder import _ns_matches

        assert _ns_matches("web/search", "web") is True

    def test_no_match(self):
        from toolregistry_server.registry_builder import _ns_matches

        assert _ns_matches("calculator", "web") is False

    def test_partial_no_match(self):
        """'webhook' should NOT match pattern 'web'."""
        from toolregistry_server.registry_builder import _ns_matches

        assert _ns_matches("webhook", "web") is False


class TestCreateRegistryFromConfigWithHooks:
    """Tests for registry_from_config with post_register_hooks."""

    def test_hook_called_for_each_tool(self, tmp_path, monkeypatch):
        """Hook is invoked once per registered tool."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

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
        registry = registry_from_config(config, post_register_hooks=[my_hook])
        assert len(called) == 2
        assert len(registry._tools) == 2

    def test_hook_returning_string_disables_tool(self, tmp_path, monkeypatch):
        """Hook returning a non-empty string auto-disables the tool."""
        from toolregistry.config import PythonSource, ToolConfig

        from toolregistry_server.registry_builder import (
            registry_from_config,
        )

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
        registry = registry_from_config(config, post_register_hooks=[selective_hook])
        assert len(registry._tools) == 2
        enabled = [n for n in registry._tools if registry.is_enabled(n)]
        assert all("good" in n for n in enabled)

    def test_no_hooks_behaviour_unchanged(self):
        """Passing no hooks leaves existing behaviour intact."""
        from toolregistry.config import ToolConfig

        from toolregistry_server.registry_builder import registry_from_config

        registry = registry_from_config(ToolConfig(tools=()))
        assert len(registry._tools) == 0

    def test_parser_has_profile_openapi(self):
        """Parser exposes --profile for openapi subcommand."""
        from toolregistry_server.cli import CLI

        parser = CLI().create_parser()
        args = parser.parse_args(["openapi", "--profile", "remote"])
        assert args.profile == "remote"

    def test_parser_has_profile_mcp(self):
        """Parser exposes --profile for mcp subcommand."""
        from toolregistry_server.cli import CLI

        parser = CLI().create_parser()
        args = parser.parse_args(["mcp", "--profile", "local"])
        assert args.profile == "local"

    def test_parser_profile_default_none(self):
        """--profile defaults to None when not provided."""
        from toolregistry_server.cli import CLI

        parser = CLI().create_parser()
        args = parser.parse_args(["openapi"])
        assert args.profile is None


class TestApplyProfile:
    """Tests for apply_profile()."""

    def test_remote_profile_disables_tagged_tools(self, tmp_path, monkeypatch):
        """remote profile disables file_system/destructive/privileged tools."""
        from toolregistry import ToolRegistry
        from toolregistry.tool import Tool, ToolMetadata, ToolTag

        from toolregistry_server.registry_builder import apply_profile

        registry = ToolRegistry()

        def fs_tool() -> str:
            """A filesystem tool."""
            return "fs"

        def safe_tool() -> str:
            """A safe network tool."""
            return "safe"

        t1 = Tool.from_function(fs_tool)
        t1 = dataclasses.replace(t1, metadata=ToolMetadata(tags={ToolTag.FILE_SYSTEM}))
        t2 = Tool.from_function(safe_tool)
        t2 = dataclasses.replace(t2, metadata=ToolMetadata(tags={ToolTag.NETWORK}))

        registry._tools["fs_tool"] = t1
        registry._tools["safe_tool"] = t2

        apply_profile(registry, "remote")

        assert not registry.is_enabled("fs_tool")
        assert registry.is_enabled("safe_tool")

    def test_local_profile_disables_network(self, tmp_path):
        """local profile disables network-tagged tools, keeps others."""
        from toolregistry import ToolRegistry
        from toolregistry.tool import Tool, ToolMetadata, ToolTag

        from toolregistry_server.registry_builder import apply_profile

        registry = ToolRegistry()

        def fs_tool() -> str:
            """File system tool."""
            return "x"

        def net_tool() -> str:
            """Network tool."""
            return "y"

        t_fs = Tool.from_function(fs_tool)
        t_fs = dataclasses.replace(
            t_fs, metadata=ToolMetadata(tags={ToolTag.FILE_SYSTEM})
        )
        registry._tools["fs_tool"] = t_fs

        t_net = Tool.from_function(net_tool)
        t_net = dataclasses.replace(
            t_net, metadata=ToolMetadata(tags={ToolTag.NETWORK})
        )
        registry._tools["net_tool"] = t_net

        apply_profile(registry, "local")
        assert registry.is_enabled("fs_tool")
        assert not registry.is_enabled("net_tool")

    def test_unknown_profile_does_not_raise(self):
        """Unknown profile name does not raise an exception."""
        from toolregistry import ToolRegistry

        from toolregistry_server.registry_builder import apply_profile

        registry = ToolRegistry()
        # Should complete without raising regardless of unknown profile
        apply_profile(registry, "nonexistent_profile")
        # Registry should be unchanged (no tools to disable anyway)
        assert len(registry._tools) == 0


class TestApplyNsTags:
    """Tests for _apply_ns_tags with frozen Tool/ToolMetadata."""

    def test_apply_ns_tags_with_frozen_tool(self):
        """_apply_ns_tags works with frozen Tool and ToolMetadata."""
        from toolregistry import ToolRegistry
        from toolregistry.tool import ToolTag

        from toolregistry_server.registry_builder import _apply_ns_tags

        registry = ToolRegistry()

        def my_tool() -> str:
            """A tool."""
            return "ok"

        registry.register(my_tool, namespace="test_ns")

        ns_tags = {"test_ns": ("file_system",)}
        _apply_ns_tags(registry, ns_tags)

        tool = registry.get_tool("test_ns-my_tool")
        assert ToolTag.FILE_SYSTEM in tool.metadata.tags

    def test_apply_ns_tags_unknown_tag_skipped(self):
        """Unknown tag strings are skipped with a warning."""
        from toolregistry import ToolRegistry
        from toolregistry.tool import ToolTag

        from toolregistry_server.registry_builder import _apply_ns_tags

        registry = ToolRegistry()

        def my_tool() -> str:
            """A tool."""
            return "ok"

        registry.register(my_tool, namespace="ns")

        ns_tags = {"ns": ("file_system", "nonexistent_tag")}
        _apply_ns_tags(registry, ns_tags)

        tool = registry.get_tool("ns-my_tool")
        assert ToolTag.FILE_SYSTEM in tool.metadata.tags
        assert len(tool.metadata.tags) == 1

    def test_apply_ns_tags_no_matching_namespace(self):
        """Tools with non-matching namespace are untouched."""
        from toolregistry import ToolRegistry

        from toolregistry_server.registry_builder import _apply_ns_tags

        registry = ToolRegistry()

        def my_tool() -> str:
            """A tool."""
            return "ok"

        registry.register(my_tool, namespace="other")

        ns_tags = {"unrelated": ("file_system",)}
        _apply_ns_tags(registry, ns_tags)

        tool = registry.get_tool("other-my_tool")
        assert not tool.metadata.tags


class TestGatewayCommand:
    """The gateway subcommand composes the three frontends."""

    def test_gateway_command_defaults(self):
        """Parsing gateway with no options uses the documented defaults."""
        parser = CLI().create_parser()
        args = parser.parse_args(["gateway"])

        assert args.command == "gateway"
        assert args.host == "0.0.0.0"
        assert args.port == 8000
        assert args.config is None
        assert args.tokens is None
        assert args.server_url is None
        assert args.backend == "fastapi"
        assert args.openapi_path is None
        assert args.mcp_path is None
        assert args.sse_path is None
        assert args.admin_path is None
        assert args.openapi is None
        assert args.mcp is None
        assert args.sse is None
        assert args.admin is None
        assert args.admin_token is None
        assert args.admin_token_file is None
        assert args.keys_db is None
        assert args.admin_allow_private_hosts is False
        assert args.config_reload is False
        assert args.config_write is True

    def test_gateway_command_with_options(self):
        """Paths, switches and the config-write flag are parsed."""
        parser = CLI().create_parser()
        args = parser.parse_args(
            [
                "gateway",
                "--config",
                "tools.yaml",
                "--port",
                "9100",
                "--openapi-path",
                "/api",
                "--mcp-path",
                "/rpc",
                "--sse-path",
                "/events",
                "--admin-path",
                "/panel",
                "--no-mcp",
                "--admin",
                "--admin-token-file",
                "operator.txt",
                "--keys-db",
                "keys.db",
                "--admin-allow-private-hosts",
                "--config-reload",
                "--no-config-write",
            ]
        )

        assert args.port == 9100
        assert args.openapi_path == "/api"
        assert args.mcp_path == "/rpc"
        assert args.sse_path == "/events"
        assert args.admin_path == "/panel"
        assert args.openapi is None
        assert args.mcp is False
        assert args.sse is None
        assert args.admin is True
        assert args.admin_token_file == "operator.txt"
        assert args.keys_db == "keys.db"
        assert args.admin_allow_private_hosts is True
        assert args.config_reload is True
        assert args.config_write is False

    def test_gateway_enable_switch_can_be_explicit(self):
        """A persisted disabled frontend can be force-enabled from the CLI."""
        parser = CLI().create_parser()
        args = parser.parse_args(["gateway", "--mcp"])
        assert args.mcp is True

    @patch("toolregistry_server.app.App.serve_gateway")
    def test_gateway_command_dispatch(self, mock_serve):
        """gateway dispatch forwards every option to App.serve_gateway."""
        main(
            [
                "gateway",
                "--config",
                "tools.yaml",
                "--port",
                "9000",
                "--no-mcp",
                "--openapi-path",
                "/api",
            ]
        )

        mock_serve.assert_called_once_with(
            config_path="tools.yaml",
            profile=None,
            host="0.0.0.0",
            port=9000,
            socket_path=None,
            tokens_path=None,
            server_url=None,
            backend="fastapi",
            max_body_size=1_048_576,
            regex_timeout=0.05,
            regex_budget=0.25,
            strict_spec_regex=False,
            tools_list_ttl_ms=None,
            tools_list_cache_scope=None,
            openapi_path="/api",
            mcp_path=None,
            sse_path=None,
            openapi_enabled=None,
            mcp_enabled=False,
            sse_enabled=None,
            admin_path=None,
            admin_enabled=None,
            admin_token=None,
            admin_token_file=None,
            admin_allow_private_hosts=False,
            client_concurrency=8,
            concurrency_timeout=30.0,
            allow_operator_token=False,
            execution_log=None,
            config_reload=False,
            write_config=True,
        )

    @patch("toolregistry_server.app.App.serve_gateway")
    def test_gateway_command_passes_socket(self, mock_serve):
        """--socket reaches the gateway adapter (#145)."""
        main(["gateway", "--config", "tools.yaml", "--socket", "/tmp/hub.sock"])

        assert mock_serve.call_args.kwargs["socket_path"] == "/tmp/hub.sock"


class TestLocalHubCommand:
    """The local-hub subcommand and its actions (#145)."""

    def test_defaults_match_a_local_resident_hub(self):
        args = CLI().create_parser().parse_args(["local-hub"])

        assert args.command == "local-hub"
        assert args.action == "run"
        assert args.name == "default"
        assert args.host == "127.0.0.1"
        assert args.port == 0
        assert args.idle_timeout == 0.0
        assert args.socket_path is None
        assert args.json_output is False
        assert args.keys_db is None
        assert args.client_concurrency == 8
        assert args.concurrency_timeout == 30.0
        assert args.allow_operator_token is False

    def test_status_accepts_name_and_json(self):
        args = (
            CLI()
            .create_parser()
            .parse_args(["local-hub", "status", "--name", "work", "--json"])
        )

        assert args.action == "status"
        assert args.name == "work"
        assert args.json_output is True

    @patch("toolregistry_server.gateway.local_hub.run_hub", return_value=0)
    def test_run_dispatch_forwards_options(self, mock_run):
        main(
            [
                "local-hub",
                "run",
                "--config",
                "tools.yaml",
                "--name",
                "work",
                "--idle-timeout",
                "30",
            ]
        )

        assert mock_run.call_args.kwargs["name"] == "work"
        assert mock_run.call_args.kwargs["idle_timeout"] == 30.0
        options = mock_run.call_args.args[1]
        assert options["config_path"] == "tools.yaml"
        assert options["host"] == "127.0.0.1"
        assert options["port"] == 0

    @patch("toolregistry_server.gateway.local_hub.ensure_hub", return_value=0)
    def test_ensure_dispatch_passes_the_invoking_argv(self, mock_ensure):
        main(["local-hub", "ensure", "--config", "tools.yaml", "--name", "work"])

        assert mock_ensure.call_args.kwargs["argv"] == [
            "local-hub",
            "ensure",
            "--config",
            "tools.yaml",
            "--name",
            "work",
        ]

    @patch("toolregistry_server.gateway.local_hub.status_hub", return_value=3)
    def test_status_dispatch_exits_with_the_code(self, mock_status):
        with pytest.raises(SystemExit) as exc:
            main(["local-hub", "status", "--name", "work", "--json"])

        assert exc.value.code == 3
        mock_status.assert_called_once_with("work", as_json=True)

    @patch("toolregistry_server.gateway.local_hub.stop_hub", return_value=0)
    def test_stop_dispatch_does_not_need_a_config(self, mock_stop):
        with pytest.raises(SystemExit) as exc:
            main(["local-hub", "stop", "--name", "work"])

        assert exc.value.code == 0
        assert mock_stop.call_args.kwargs["timeout"] == 15.0

    def test_a_platform_refusal_exits_one(self):
        from toolregistry_server.gateway import local_hub

        with (
            patch.object(
                local_hub,
                "run_hub",
                side_effect=local_hub.LocalHubUnsupported("no Windows hub"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            main(["local-hub", "run", "--config", "tools.yaml"])

        assert exc.value.code == 1


class TestHubShimCommand:
    """The hub-shim subcommand proxies one stdio client to a hub (#145)."""

    def test_defaults(self):
        args = CLI().create_parser().parse_args(["hub-shim"])

        assert args.command == "hub-shim"
        assert args.name == "default"
        assert args.config is None
        assert args.socket_path is None
        assert args.connect_timeout == 5.0
        assert args.spawn is True
        assert args.keys_db is None
        assert args.key_file is None

    def test_options(self):
        args = (
            CLI()
            .create_parser()
            .parse_args(
                [
                    "hub-shim",
                    "--name",
                    "work",
                    "--config",
                    "tools.yaml",
                    "--socket",
                    "/tmp/hub.sock",
                    "--connect-timeout",
                    "2",
                    "--no-spawn",
                ]
            )
        )

        assert args.name == "work"
        assert args.config == "tools.yaml"
        assert args.socket_path == "/tmp/hub.sock"
        assert args.connect_timeout == 2.0
        assert args.spawn is False

    @patch("toolregistry_server.gateway.shim.run_stdio", return_value=0)
    def test_dispatch_builds_the_shim_config(self, mock_run):
        main(
            [
                "hub-shim",
                "--name",
                "work",
                "--config",
                "tools.yaml",
                "--socket",
                "/tmp/hub.sock",
                "--connect-timeout",
                "2",
                "--no-spawn",
            ]
        )

        config = mock_run.call_args.args[0]
        assert config.name == "work"
        assert config.config_path == "tools.yaml"
        assert config.socket_path == "/tmp/hub.sock"
        assert config.connect_timeout == 2.0
        assert config.spawn is False

    def test_dispatch_does_not_need_a_config(self):
        """An operator-started hub is found through the endpoint document."""
        with patch("toolregistry_server.gateway.shim.run_stdio", return_value=0) as run:
            main(["hub-shim", "--name", "work"])

        assert run.call_args.args[0].config_path is None

    def test_a_shim_error_exits_one(self):
        from toolregistry_server.gateway.shim import ShimError

        with (
            patch(
                "toolregistry_server.gateway.shim.run_stdio",
                side_effect=ShimError("hub speaks another protocol"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            main(["hub-shim"])

        assert exc.value.code == 1


class TestClientIsolationFlags:
    """Per-client isolation knobs (#145)."""

    @patch("toolregistry_server.app.App.serve_gateway")
    def test_gateway_forwards_the_quota_flags(self, mock_serve):
        main(
            [
                "gateway",
                "--config",
                "tools.yaml",
                "--client-concurrency",
                "2",
                "--concurrency-timeout",
                "5",
                "--allow-operator-token",
            ]
        )

        kwargs = mock_serve.call_args.kwargs
        assert kwargs["client_concurrency"] == 2
        assert kwargs["concurrency_timeout"] == 5.0
        assert kwargs["allow_operator_token"] is True

    @pytest.mark.parametrize(
        "flags",
        [
            ["--client-concurrency", "0"],
            ["--concurrency-timeout", "0"],
        ],
    )
    def test_nonsense_knobs_are_rejected_by_the_parser(self, flags):
        with pytest.raises(SystemExit) as exc:
            main(["gateway", "--config", "tools.yaml", *flags])

        assert exc.value.code == 2

    @patch("toolregistry_server.gateway.local_hub.ensure_hub", return_value=0)
    def test_local_hub_forwards_the_key_store(self, mock_ensure):
        main(
            [
                "local-hub",
                "ensure",
                "--config",
                "tools.yaml",
                "--keys-db",
                "/tmp/keys.db",
            ]
        )

        options = mock_ensure.call_args.args[1]
        assert options["keys_db"] == "/tmp/keys.db"

    @patch("toolregistry_server.gateway.shim.run_stdio", return_value=0)
    def test_hub_shim_passes_its_key_and_store(self, mock_run, monkeypatch):
        monkeypatch.delenv("TOOLREGISTRY_HUB_KEY", raising=False)
        main(
            [
                "hub-shim",
                "--keys-db",
                "/tmp/keys.db",
                "--key-file",
                "/tmp/hub.key",
            ]
        )

        config = mock_run.call_args.args[0]
        assert config.keys_db == "/tmp/keys.db"
        assert config.key_file == "/tmp/hub.key"
        assert config.key is None

    @patch("toolregistry_server.gateway.shim.run_stdio", return_value=0)
    def test_hub_shim_reads_the_key_from_the_environment(self, mock_run, monkeypatch):
        monkeypatch.setenv("TOOLREGISTRY_HUB_KEY", "trk_from_env")
        main(["hub-shim"])

        assert mock_run.call_args.args[0].key == "trk_from_env"

    @patch("toolregistry_server.gateway.shim.run_stdio", return_value=0)
    def test_an_explicit_key_file_wins_over_the_environment(
        self, mock_run, monkeypatch
    ):
        monkeypatch.setenv("TOOLREGISTRY_HUB_KEY", "trk_from_env")
        main(["hub-shim", "--key-file", "/tmp/hub.key"])

        config = mock_run.call_args.args[0]
        assert config.key_file == "/tmp/hub.key"
        assert config.key is None


class TestMCPQuotaFlags:
    """The standalone mcp server throttles a network transport (#145)."""

    def test_defaults(self):
        args = CLI().create_parser().parse_args(["mcp"])

        assert args.client_concurrency == 8
        assert args.concurrency_timeout == 30.0

    def test_values_are_forwarded(self):
        with patch("toolregistry_server.app.App.serve_mcp") as mock_serve:
            main(
                [
                    "mcp",
                    "--config",
                    "tools.yaml",
                    "--transport",
                    "streamable-http",
                    "--client-concurrency",
                    "2",
                    "--concurrency-timeout",
                    "5",
                ]
            )

        kwargs = mock_serve.call_args.kwargs
        assert kwargs["client_concurrency"] == 2
        assert kwargs["concurrency_timeout"] == 5.0

    @pytest.mark.parametrize(
        "flags",
        [
            ["--client-concurrency", "0"],
            ["--concurrency-timeout", "0"],
        ],
    )
    def test_nonsense_knobs_are_rejected_by_the_parser(self, flags):
        with pytest.raises(SystemExit) as exc:
            main(["mcp", "--config", "tools.yaml", *flags])

        assert exc.value.code == 2


class TestExecutionLogFlag:
    """--execution-log is forwarded by every serving command (#224)."""

    @pytest.mark.parametrize(
        ("command", "target"),
        [
            ("gateway", "toolregistry_server.app.App.serve_gateway"),
            ("mcp", "toolregistry_server.app.App.serve_mcp"),
            ("openapi", "toolregistry_server.app.App.serve_openapi"),
        ],
    )
    @pytest.mark.parametrize(
        ("flag", "expected"),
        [("--execution-log", True), ("--no-execution-log", False)],
    )
    def test_the_flag_reaches_the_adapter(self, command, target, flag, expected):
        with patch(target) as serve:
            main([command, "--config", "tools.yaml", flag])

        assert serve.call_args.kwargs["execution_log"] is expected

    @pytest.mark.parametrize("command", ["gateway", "mcp", "openapi"])
    def test_the_default_is_left_to_the_adapter(self, command):
        args = CLI().create_parser().parse_args([command])

        assert args.execution_log is None
