"""Command-line interface for ToolRegistry Server.

Provides :class:`CLI` — a class-based CLI framework that downstream
packages subclass to customize parser, banner, and dispatch.

Usage:
    toolregistry-server openapi --config tools.yaml [OPTIONS]
    toolregistry-server mcp --config tools.yaml [OPTIONS]

Subclassing (e.g. Hub)::

    class HubCLI(CLI):
        def __init__(self):
            super().__init__(app=HubApp(identity=hub_identity))

        def create_parser(self):
            parser = super().create_parser()
            # add hub-specific args to each subparser
            return parser

    HubCLI().main()
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from .app import App

from ._vendor.structlog import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Standalone utilities (used by CLI and downstream)
# ---------------------------------------------------------------------------


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
        logger.warning(f"Environment file not found: {path}")


def print_banner(
    version: str | None = None,
    banner_art: str | None = None,
    extra_lines: list[str] | None = None,
) -> None:
    """Print a bordered ASCII banner.

    Args:
        version: Version string to display.
        banner_art: ASCII art to display. If None, uses default.
        extra_lines: Additional lines after the version.
    """
    if version is None:
        from . import __version__

        version = __version__

    if banner_art is None:
        from .banner import BANNER_ART

        banner_art = BANNER_ART

    width = 80
    border_char = "·"
    art_lines = banner_art.split("\n")

    lines = []
    lines.append(border_char * width)
    lines.append(f": {' ' * (width - 4)} :")
    for line in art_lines:
        lines.append(f": {line.center(width - 4)} :")
    lines.append(f": {' ' * (width - 4)} :")
    lines.append(f": {f'Version {version}'.center(width - 4)} :")
    if extra_lines:
        for extra in extra_lines:
            lines.append(f": {extra.center(width - 4)} :")
    lines.append(f": {' ' * (width - 4)} :")
    lines.append(border_char * width)
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI class
# ---------------------------------------------------------------------------


class CLI:
    """Class-based CLI framework.

    Subclass and override methods to customize:

    - :meth:`create_parser` — add arguments
    - :meth:`get_version_string` — version output
    - :meth:`print_banner` — startup banner
    - :meth:`dispatch` — command routing

    The :class:`~toolregistry_server.app.App` instance used for dispatch
    is set via the constructor, enabling identity and registry
    customization.
    """

    def __init__(self, app: App | None = None) -> None:
        from .app import App

        self.app = app or App()

    def create_parser(self) -> argparse.ArgumentParser:
        """Create the argument parser.

        Override to add subcommands, change prog name, or add
        extra arguments.
        """
        from .adapters.mcp import MCPAdapter
        from .adapters.openapi import OpenAPIAdapter

        parser = argparse.ArgumentParser(
            prog=self.app.identity.prog,
            description=self.app.identity.description,
        )
        parser.add_argument("--version", "-V", action="store_true", help="Show version")
        parser.add_argument("--no-banner", action="store_true", help="Disable banner")

        subparsers = parser.add_subparsers(
            dest="command",
            metavar="{openapi,mcp}",
        )

        openapi_parser = subparsers.add_parser("openapi", help="Start OpenAPI server")
        OpenAPIAdapter.add_cli_arguments(openapi_parser)

        mcp_parser = subparsers.add_parser("mcp", help="Start MCP server")
        MCPAdapter.add_cli_arguments(mcp_parser)

        return parser

    def get_version_string(self) -> str:
        """Return version string for ``--version`` output."""
        identity = self.app.identity
        return f"{identity.name} {identity.version}"

    def print_banner(self) -> None:
        """Print the startup banner using identity."""
        identity = self.app.identity
        print_banner(
            version=identity.version,
            banner_art=identity.banner_art,
        )

    def dispatch(self, parsed: argparse.Namespace) -> None:
        """Route parsed args to app.serve_*.

        Override to add custom commands or pre-processing.
        """
        config_path = getattr(parsed, "config", None)
        if config_path is None:
            logger.error("No config file specified. Use --config to provide one.")
            sys.exit(1)

        if parsed.command == "openapi":
            self.app.serve_openapi(
                config_path=config_path,
                profile=getattr(parsed, "profile", None),
                host=parsed.host,
                port=parsed.port,
                tokens_path=getattr(parsed, "tokens", None),
                reload=getattr(parsed, "reload", False),
            )
        elif parsed.command == "mcp":
            self.app.serve_mcp(
                config_path=config_path,
                profile=getattr(parsed, "profile", None),
                host=parsed.host,
                port=parsed.port,
                transport=parsed.transport,
            )

    def main(self, args: list[str] | None = None) -> NoReturn | None:
        """Run the CLI."""
        parser = self.create_parser()
        parsed = parser.parse_args(args)

        # --version
        if parsed.version:
            print(self.get_version_string())
            sys.exit(0)

        # No command → help
        if parsed.command is None:
            parser.print_help()
            sys.exit(0)

        # Load .env
        load_env_file(
            env_path=getattr(parsed, "env", None),
            no_env=getattr(parsed, "no_env", False),
        )

        # Banner
        if not parsed.no_banner:
            self.print_banner()

        # Dispatch with error handling
        try:
            self.dispatch(parsed)
        except ImportError as e:
            logger.error(f"Server dependencies not installed: {e}")
            sys.exit(1)
        except FileNotFoundError as e:
            logger.error(str(e))
            sys.exit(1)
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)

        return None


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------


def main(args: list[str] | None = None) -> NoReturn | None:
    """Entry point for ``toolregistry-server`` CLI."""
    return CLI().main(args)


# Backward compat — kept for downstream that imports these
# (Hub currently uses print_banner and load_env_file directly)

__all__ = [
    "CLI",
    "load_env_file",
    "main",
    "print_banner",
]
