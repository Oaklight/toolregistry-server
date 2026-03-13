"""
Command-line interface for ToolRegistry Server.

This module provides the CLI entry point for running ToolRegistry servers.

Usage:
    toolregistry-server --mode openapi --port 8000
    toolregistry-server --mode mcp
    toolregistry-server --help

Example:
    # Start OpenAPI server on port 8000
    $ toolregistry-server --mode openapi --port 8000

    # Start MCP server (stdio mode)
    $ toolregistry-server --mode mcp

    # With authentication
    $ toolregistry-server --mode openapi --auth-token "secret"
"""

import argparse
import sys
from typing import NoReturn


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the CLI.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="toolregistry-server",
        description="ToolRegistry Server - Expose tools via OpenAPI or MCP",
    )

    parser.add_argument(
        "--mode",
        choices=["openapi", "mcp"],
        default="openapi",
        help="Server mode: 'openapi' for REST API, 'mcp' for Model Context Protocol",
    )

    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (OpenAPI mode only, default: 0.0.0.0)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (OpenAPI mode only, default: 8000)",
    )

    parser.add_argument(
        "--auth-token",
        dest="auth_token",
        help="Bearer token for authentication (optional)",
    )

    parser.add_argument(
        "--auth-file",
        dest="auth_file",
        help="File containing authentication tokens, one per line (optional)",
    )

    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version and exit",
    )

    return parser


def main(args: list[str] | None = None) -> NoReturn | None:
    """Main entry point for the CLI.

    Args:
        args: Command-line arguments. If None, uses sys.argv.

    Returns:
        None on success, or exits with error code.
    """
    parser = create_parser()
    parsed = parser.parse_args(args)

    if parsed.version:
        from toolregistry_server import __version__

        print(f"toolregistry-server {__version__}")
        sys.exit(0)

    # TODO: Implement server startup logic
    # This is a skeleton implementation - full implementation will be
    # migrated from toolregistry-hub's cli.py

    if parsed.mode == "openapi":
        print(f"Starting OpenAPI server on {parsed.host}:{parsed.port}")
        print("Note: This is a skeleton implementation.")
        print("Full implementation will be available after migration.")
    elif parsed.mode == "mcp":
        print("Starting MCP server (stdio mode)")
        print("Note: This is a skeleton implementation.")
        print("Full implementation will be available after migration.")

    return None


__all__ = [
    "main",
    "create_parser",
]
