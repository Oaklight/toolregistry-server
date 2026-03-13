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
                "tools.jsonc",
                "--tokens",
                "tokens.txt",
                "--reload",
            ]
        )
        assert args.command == "openapi"
        assert args.host == "127.0.0.1"
        assert args.port == 9000
        assert args.config == "tools.jsonc"
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
                "tools.jsonc",
            ]
        )
        assert args.command == "mcp"
        assert args.transport == "sse"
        assert args.host == "0.0.0.0"
        assert args.port == 9000
        assert args.config == "tools.jsonc"

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

    def test_load_config_valid_json(self):
        """Test load_config with valid JSON file."""
        from toolregistry_server.cli.openapi import load_config

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"tools": []}')
            f.flush()

            result = load_config(f.name)
            assert result == {"tools": []}

            Path(f.name).unlink()

    def test_load_config_jsonc_with_comments(self):
        """Test load_config with JSONC file containing comments."""
        from toolregistry_server.cli.openapi import load_config

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonc", delete=False) as f:
            f.write("""
            // This is a comment
            {
                "tools": []  // inline comment
            }
            """)
            f.flush()

            result = load_config(f.name)
            assert result == {"tools": []}

            Path(f.name).unlink()

    def test_load_config_invalid_json(self):
        """Test load_config with invalid JSON."""
        from toolregistry_server.cli.openapi import load_config

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json")
            f.flush()

            with pytest.raises(SystemExit):
                load_config(f.name)

            Path(f.name).unlink()


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
        from toolregistry_server.cli.openapi import create_registry_from_config

        config = {"tools": []}
        registry = create_registry_from_config(config)
        assert len(registry._tools) == 0

    def test_create_registry_invalid_tool_config(self):
        """Test creating registry with invalid tool config (no module)."""
        from toolregistry_server.cli.openapi import create_registry_from_config

        config = {"tools": [{"class": "SomeClass"}]}
        registry = create_registry_from_config(config)
        # Should skip invalid config and continue
        assert len(registry._tools) == 0
