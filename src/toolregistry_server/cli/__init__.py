"""
Command-line interface for ToolRegistry Server.

This module provides the CLI entry point for running ToolRegistry servers
with support for both OpenAPI and MCP protocols.

Usage:
    toolregistry-server openapi [OPTIONS]
    toolregistry-server mcp [OPTIONS]
    toolregistry-server --help

Example:
    # Start OpenAPI server on port 8000
    $ toolregistry-server openapi --port 8000

    # Start MCP server with stdio transport
    $ toolregistry-server mcp --transport stdio

    # Start MCP server with SSE transport
    $ toolregistry-server mcp --transport sse --port 8000

    # With configuration file
    $ toolregistry-server openapi --config tools.jsonc
"""

import argparse
import sys
from typing import NoReturn


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the CLI.

    Returns:
        Configured ArgumentParser instance with subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="toolregistry-server",
        description="ToolRegistry Server - Expose tools via OpenAPI or MCP",
    )

    parser.add_argument(
        "--version",
        "-V",
        action="store_true",
        help="Show version and exit",
    )

    # Create subparsers for openapi and mcp commands
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available server modes",
        metavar="{openapi,mcp}",
    )

    # OpenAPI subcommand
    openapi_parser = subparsers.add_parser(
        "openapi",
        help="Start OpenAPI (REST) server",
        description="Start an OpenAPI server exposing tools as REST endpoints",
    )
    _add_openapi_arguments(openapi_parser)

    # MCP subcommand
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Start MCP server",
        description="Start an MCP server for LLM tool integration",
    )
    _add_mcp_arguments(mcp_parser)

    return parser


def _add_openapi_arguments(parser: argparse.ArgumentParser) -> None:
    """Add OpenAPI-specific arguments to the parser.

    Args:
        parser: The ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the server to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the server to (default: 8000)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a JSON/JSONC configuration file for tools",
    )
    parser.add_argument(
        "--tokens",
        type=str,
        default=None,
        help="Path to a file containing authentication tokens (one per line)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development mode",
    )


def _add_mcp_arguments(parser: argparse.ArgumentParser) -> None:
    """Add MCP-specific arguments to the parser.

    Args:
        parser: The ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport type: stdio, sse, or streamable-http (default: stdio)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host for SSE/HTTP transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE/HTTP transport (default: 8000)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a JSON/JSONC configuration file for tools",
    )


def main(args: list[str] | None = None) -> NoReturn | None:
    """Main entry point for the CLI.

    Args:
        args: Command-line arguments. If None, uses sys.argv.

    Returns:
        None on success, or exits with error code.
    """
    parser = create_parser()
    parsed = parser.parse_args(args)

    # Handle version flag
    if parsed.version:
        from toolregistry_server import __version__

        print(f"toolregistry-server {__version__}")
        sys.exit(0)

    # If no command specified, show help
    if parsed.command is None:
        parser.print_help()
        sys.exit(0)

    # Dispatch to appropriate command handler
    if parsed.command == "openapi":
        from .openapi import run_openapi_server

        run_openapi_server(
            host=parsed.host,
            port=parsed.port,
            config_path=parsed.config,
            tokens_path=parsed.tokens,
            reload=parsed.reload,
        )
    elif parsed.command == "mcp":
        from .mcp import run_mcp_server

        run_mcp_server(
            transport=parsed.transport,
            host=parsed.host,
            port=parsed.port,
            config_path=parsed.config,
        )

    return None


__all__ = [
    "main",
    "create_parser",
]
