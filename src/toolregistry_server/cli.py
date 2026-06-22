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

    # With configuration file (JSONC or YAML)
    $ toolregistry-server openapi --config tools.yaml

    # With custom .env file
    $ toolregistry-server openapi --env /path/to/.env

    # Skip loading .env file
    $ toolregistry-server openapi --no-env
"""

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from collections.abc import Callable

from ._vendor.structlog import get_logger

logger = get_logger()

# Default ASCII art banner for ToolRegistry Server
DEFAULT_BANNER_ART = """
░▀█▀░█▀█░█▀█░█░░░█▀▄░█▀▀░█▀▀░▀█▀░█▀▀░▀█▀░█▀▄░█░█░░░░░█▀▀░█▀▀░█▀▄░█░█░█▀▀░█▀▄
░░█░░█░█░█░█░█░░░█▀▄░█▀▀░█░█░░█░░▀▀█░░█░░█▀▄░░█░░▄▄▄░▀▀█░█▀▀░█▀▄░▀▄▀░█▀▀░█▀▄
░░▀░░▀▀▀░▀▀▀░▀▀▀░▀░▀░▀▀▀░▀▀▀░▀▀▀░▀▀▀░░▀░░▀░▀░░▀░░░░░░▀▀▀░▀▀▀░▀░▀░░▀░░▀▀▀░▀░▀
""".strip()


def load_env_file(env_path: str | None = None, no_env: bool = False) -> None:
    """Load environment variables from .env file.

    Args:
        env_path: Path to .env file. If None, uses current directory's .env
        no_env: If True, skip loading .env file
    """
    if no_env:
        return

    from ._vendor.dotenv import load_dotenv

    path = Path(env_path) if env_path else Path.cwd() / ".env"

    if path.exists():
        load_dotenv(path)
        logger.info(f"Loaded environment from {path}")
    elif env_path:
        # User explicitly specified a path but file doesn't exist
        logger.warning(f"Environment file not found: {path}")


def print_banner(
    version: str | None = None,
    banner_art: str | None = None,
    extra_lines: list[str] | None = None,
) -> None:
    """Print the ToolRegistry Server banner with centered content and border.

    This function can be used by downstream packages (e.g., toolregistry-hub)
    to display a customized banner with their own version and art.

    Args:
        version: Version string to display. If None, uses toolregistry-server version.
        banner_art: Custom ASCII art to display. If None, uses default banner.
        extra_lines: Additional lines to display after the version (e.g., update info).
    """
    if version is None:
        from toolregistry_server import __version__

        version = __version__

    if banner_art is None:
        banner_art = DEFAULT_BANNER_ART

    width = 80
    border_char = "·"

    # Split banner art into lines
    art_lines = banner_art.split("\n")

    # Build the banner
    lines = []

    # Top border
    lines.append(border_char * width)

    # Empty line
    lines.append(f": {' ' * (width - 4)} :")

    # Art lines - center each line
    for line in art_lines:
        centered = line.center(width - 4)
        lines.append(f": {centered} :")

    # Empty line
    lines.append(f": {' ' * (width - 4)} :")

    # Version information
    version_line = f"Version {version}"
    centered_version = version_line.center(width - 4)
    lines.append(f": {centered_version} :")

    # Extra lines (e.g., update available info)
    if extra_lines:
        for extra in extra_lines:
            centered_extra = extra.center(width - 4)
            lines.append(f": {centered_extra} :")

    # Empty line
    lines.append(f": {' ' * (width - 4)} :")

    # Bottom border
    lines.append(border_char * width)

    # Print the banner
    print("\n".join(lines))


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the CLI.

    Returns:
        Configured ArgumentParser instance with subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="toolregistry-server",
        description="Define custom tools and serve them via OpenAPI or MCP interfaces",
    )

    parser.add_argument(
        "--version",
        "-V",
        action="store_true",
        help="Show version and exit",
    )

    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Disable the startup banner",
    )

    # Create subparsers — each adapter registers its own arguments
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available server modes",
        metavar="{openapi,mcp}",
    )

    from .adapters.mcp import MCPAdapter
    from .adapters.openapi import OpenAPIAdapter

    openapi_parser = subparsers.add_parser(
        "openapi", help="Start OpenAPI (REST) server"
    )
    OpenAPIAdapter.add_cli_arguments(openapi_parser)

    mcp_parser = subparsers.add_parser("mcp", help="Start MCP server")
    MCPAdapter.add_cli_arguments(mcp_parser)

    return parser


def run_cli(
    parsed: argparse.Namespace,
    *,
    version_string: str | None = None,
    banner_fn: "Callable[[], None] | None" = None,
    dispatch_fn: "Callable[[argparse.Namespace], None] | None" = None,
) -> NoReturn | None:
    """Reusable CLI main loop.

    Handles version flag, no-command help, .env loading, banner,
    and dispatch.  Downstream packages (e.g. Hub) can override
    specific steps via keyword arguments.

    Args:
        parsed: Parsed argparse namespace.
        version_string: Version string for ``--version`` output.
            Defaults to ``"toolregistry-server <version>"``.
        banner_fn: Callable to print the banner.  Defaults to
            :func:`print_banner`.  Pass ``None`` to skip.
        dispatch_fn: Callable that receives *parsed* and dispatches
            to the appropriate serve function.  Defaults to the
            built-in config-based dispatch.
    """
    # Handle version flag
    if parsed.version:
        if version_string is None:
            from toolregistry_server import __version__

            version_string = f"toolregistry-server {__version__}"
        print(version_string)
        sys.exit(0)

    # If no command specified, show help
    if parsed.command is None:
        # Re-create parser to print help (parsed doesn't carry it)
        create_parser().print_help()
        sys.exit(0)

    # Load environment variables from .env file
    load_env_file(
        env_path=getattr(parsed, "env", None),
        no_env=getattr(parsed, "no_env", False),
    )

    # Print banner
    if not parsed.no_banner:
        if banner_fn is not None:
            banner_fn()
        else:
            print_banner()

    # Dispatch
    try:
        if dispatch_fn is not None:
            dispatch_fn(parsed)
        else:
            _default_dispatch(parsed)
    except ImportError as e:
        logger.error(f"Server dependencies not installed: {e}")
        logger.info(
            "Install with: pip install toolregistry-server[openapi] "
            "or toolregistry-server[mcp]"
        )
        sys.exit(1)
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    return None


def _default_dispatch(parsed: argparse.Namespace) -> None:
    """Default dispatch for standalone toolregistry-server CLI."""
    config_path = getattr(parsed, "config", None)
    if config_path is None:
        logger.error("No config file specified. Use --config to provide one.")
        sys.exit(1)

    if parsed.command == "openapi":
        from toolregistry_server.app import serve_openapi

        serve_openapi(
            config_path=config_path,
            profile=getattr(parsed, "profile", None),
            host=parsed.host,
            port=parsed.port,
            tokens_path=getattr(parsed, "tokens", None),
            reload=getattr(parsed, "reload", False),
        )
    elif parsed.command == "mcp":
        from toolregistry_server.app import serve_mcp

        serve_mcp(
            config_path=config_path,
            profile=getattr(parsed, "profile", None),
            host=parsed.host,
            port=parsed.port,
            transport=parsed.transport,
        )


def main(args: list[str] | None = None) -> NoReturn | None:
    """Main entry point for the standalone toolregistry-server CLI."""
    parser = create_parser()
    parsed = parser.parse_args(args)
    return run_cli(parsed)


__all__ = [
    "DEFAULT_BANNER_ART",
    "create_parser",
    "load_env_file",
    "main",
    "print_banner",
    "run_cli",
]
