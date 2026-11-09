"""Command-line interface for ToolRegistry Server.

Provides :class:`CLI` — a class-based CLI framework that downstream
packages subclass to customize parser, banner, and dispatch.

Usage:
    toolregistry-server openapi --config tools.yaml [OPTIONS]
    toolregistry-server mcp --config tools.yaml [OPTIONS]
    toolregistry-server gateway --config tools.yaml [OPTIONS]

Subclassing (e.g. Hub)::

    class HubCLI(CLI):
        def __init__(self):
            super().__init__(app=HubApp(identity=hub_identity))

        def configure_subparsers(self, subparsers):
            for sp in subparsers.values():
                sp.add_argument("--admin-port", type=int)

    HubCLI().main()
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TextIO

if TYPE_CHECKING:
    from .app import App

from ._vendor.httpserver import DEFAULT_MAX_BODY_SIZE
from ._vendor.structlog import get_logger
from .regex_guard import DEFAULT_REGEX_BUDGET, DEFAULT_REGEX_TIMEOUT

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
    file: TextIO | None = None,
) -> None:
    """Print a bordered ASCII banner.

    The banner goes to *stderr* by default, alongside the log output.
    Over the MCP stdio transport stdout is the JSON-RPC channel, and the
    spec forbids writing anything else there; a client reading stdout
    would otherwise see banner art between its messages.

    Args:
        version: Version string to display.
        banner_art: ASCII art to display. If None, uses default.
        extra_lines: Additional lines after the version.
        file: Stream to write to. Defaults to ``sys.stderr``.
    """
    if version is None:
        from . import __version__

        version = __version__

    if banner_art is None:
        from .banner import BANNER_ART

        banner_art = BANNER_ART

    stream = file if file is not None else sys.stderr

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
    print("\n".join(lines), file=stream)


# ---------------------------------------------------------------------------
# CLI class
# ---------------------------------------------------------------------------


class CLI:
    """Class-based CLI framework.

    Subclass and override methods to customize:

    - :meth:`configure_subparsers` — add arguments to subparsers
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
        self._argv: list[str] = []

    def create_parser(self) -> argparse.ArgumentParser:
        """Create the argument parser.

        Builds the top-level parser and ``openapi`` / ``mcp``
        subparsers, then calls :meth:`configure_subparsers` so
        subclasses can add extra arguments without touching
        argparse internals.
        """
        from .adapters.mcp import MCPAdapter
        from .adapters.openapi import OpenAPIAdapter
        from .gateway import GatewayAdapter
        from .keys_cli import add_keys_subparser

        parser = argparse.ArgumentParser(
            prog=self.app.identity.prog,
            description=self.app.identity.description,
        )
        parser.add_argument("--version", "-V", action="store_true", help="Show version")
        parser.add_argument("--no-banner", action="store_true", help="Disable banner")

        sub = parser.add_subparsers(
            dest="command",
            metavar="{openapi,mcp,gateway,local-hub,hub-shim,keys}",
        )

        openapi_parser = sub.add_parser("openapi", help="Start OpenAPI server")
        OpenAPIAdapter.add_cli_arguments(openapi_parser)

        mcp_parser = sub.add_parser("mcp", help="Start MCP server")
        MCPAdapter.add_cli_arguments(mcp_parser)

        for source_parser in (openapi_parser, mcp_parser):
            self._add_source_runtime_arguments(source_parser)

        gateway_parser = sub.add_parser(
            "gateway", help="Serve OpenAPI and MCP from one process"
        )
        GatewayAdapter.add_cli_arguments(gateway_parser)
        self._add_source_runtime_arguments(gateway_parser)

        keys_parser = sub.add_parser("keys", help="Manage inbound API keys")
        add_keys_subparser(keys_parser)

        from .gateway.endpoint import DEFAULT_NAME

        local_hub_parser = sub.add_parser(
            "local-hub",
            help="Run a resident gateway shared by local clients (POSIX only)",
        )
        GatewayAdapter.add_cli_arguments(local_hub_parser)
        self._add_source_runtime_arguments(local_hub_parser)
        local_hub_parser.add_argument(
            "action",
            nargs="?",
            choices=("run", "ensure", "stop", "status"),
            default="run",
            help=(
                "run serves in the foreground (default); ensure starts a "
                "detached hub and prints its endpoint; stop signals the hub; "
                "status reports it"
            ),
        )
        local_hub_parser.add_argument(
            "--name",
            default=DEFAULT_NAME,
            metavar="NAME",
            help="Hub name (default: default)",
        )
        local_hub_parser.add_argument(
            "--idle-timeout",
            type=float,
            default=0.0,
            metavar="SECONDS",
            help=(
                "Exit after SECONDS without a request or an open MCP session "
                "(default: 0, stay resident)"
            ),
        )
        local_hub_parser.add_argument(
            "--timeout",
            type=float,
            default=None,
            metavar="SECONDS",
            help="Wait limit for ensure and stop",
        )
        local_hub_parser.add_argument(
            "--json",
            dest="json_output",
            action="store_true",
            help="Print status as JSON",
        )
        local_hub_parser.set_defaults(host="127.0.0.1", port=0)

        hub_shim_parser = sub.add_parser(
            "hub-shim",
            help="Expose one local hub to a stdio MCP client (POSIX only)",
        )
        hub_shim_parser.add_argument(
            "--name",
            default=DEFAULT_NAME,
            metavar="NAME",
            help="Hub to attach to (default: default)",
        )
        hub_shim_parser.add_argument(
            "--config",
            default=None,
            metavar="PATH",
            help="Config file used when the shim has to start the hub",
        )
        hub_shim_parser.add_argument(
            "--socket",
            dest="socket_path",
            default=None,
            metavar="PATH",
            help="Unix socket for a hub the shim starts",
        )
        hub_shim_parser.add_argument(
            "--connect-timeout",
            type=float,
            default=5.0,
            metavar="SECONDS",
            help="Limit for one connection attempt (default: 5)",
        )
        hub_shim_parser.add_argument(
            "--no-spawn",
            dest="spawn",
            action="store_false",
            default=True,
            help="Never start a hub; report it as unavailable instead",
        )

        self.configure_subparsers(
            {
                "openapi": openapi_parser,
                "mcp": mcp_parser,
                "gateway": gateway_parser,
                "local-hub": local_hub_parser,
                "hub-shim": hub_shim_parser,
            }
        )

        return parser

    @staticmethod
    def _add_source_runtime_arguments(parser: argparse.ArgumentParser) -> None:
        """Add the runtime source-management flags shared by both commands."""
        parser.add_argument(
            "--config-reload",
            action=argparse.BooleanOptionalAction,
            default=False,
            help=(
                "Watch the config file and apply a source-level diff when it "
                "changes (default: disabled)"
            ),
        )
        parser.add_argument(
            "--no-config-write",
            dest="config_write",
            action="store_false",
            default=True,
            help=(
                "Keep runtime source changes in memory; never rewrite the config file"
            ),
        )

    def configure_subparsers(
        self, subparsers: dict[str, argparse.ArgumentParser]
    ) -> None:
        """Hook for subclasses to add arguments to subparsers.

        Called by :meth:`create_parser` after all subparsers are
        created.  Override to add extra CLI flags without accessing
        argparse private API::

            def configure_subparsers(self, subparsers):
                for sp in subparsers.values():
                    sp.add_argument("--admin-port", type=int)

        Args:
            subparsers: Mapping of command name to its sub-parser.
        """
        pass

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
        if parsed.command == "keys":
            from .keys_cli import dispatch_keys

            exit_code = dispatch_keys(parsed)
            if exit_code:
                sys.exit(exit_code)
            return

        if parsed.command == "local-hub":
            self._dispatch_local_hub(parsed)
            return

        if parsed.command == "hub-shim":
            self._dispatch_hub_shim(parsed)
            return

        config_path = getattr(parsed, "config", None)
        if config_path is None:
            logger.error("No config file specified. Use --config to provide one.")
            sys.exit(1)

        keys_db = getattr(parsed, "keys_db", None)

        if parsed.command == "openapi":
            options: dict[str, Any] = {
                "config_path": config_path,
                "profile": getattr(parsed, "profile", None),
                "host": parsed.host,
                "port": parsed.port,
                "tokens_path": getattr(parsed, "tokens", None),
                "max_body_size": getattr(
                    parsed, "max_body_size", DEFAULT_MAX_BODY_SIZE
                ),
                "regex_timeout": getattr(
                    parsed, "regex_timeout", DEFAULT_REGEX_TIMEOUT
                ),
                "regex_budget": getattr(parsed, "regex_budget", DEFAULT_REGEX_BUDGET),
                "strict_spec_regex": getattr(parsed, "strict_spec_regex", False),
                "config_reload": getattr(parsed, "config_reload", False),
                "write_config": getattr(parsed, "config_write", True),
            }
            if keys_db is not None:
                options["keys_db"] = keys_db
            self.app.serve_openapi(**options)
        elif parsed.command == "mcp":
            options = {
                "config_path": config_path,
                "profile": getattr(parsed, "profile", None),
                "host": parsed.host,
                "port": parsed.port,
                "transport": parsed.transport,
                "tokens_path": getattr(parsed, "tokens", None),
                "server_url": getattr(parsed, "server_url", None),
                "tools_list_ttl_ms": getattr(parsed, "tools_list_ttl_ms", None),
                "tools_list_cache_scope": getattr(
                    parsed, "tools_list_cache_scope", None
                ),
                "config_reload": getattr(parsed, "config_reload", False),
                "write_config": getattr(parsed, "config_write", True),
            }
            if keys_db is not None:
                options["keys_db"] = keys_db
            self.app.serve_mcp(**options)
        elif parsed.command == "gateway":
            self.app.serve_gateway(**self._gateway_options(config_path, parsed))

    @staticmethod
    def _gateway_options(
        config_path: str | None, parsed: argparse.Namespace
    ) -> dict[str, Any]:
        """Build the gateway construction options from parsed arguments."""
        options: dict[str, Any] = {
            "config_path": config_path,
            "profile": getattr(parsed, "profile", None),
            "host": parsed.host,
            "port": parsed.port,
            "socket_path": getattr(parsed, "socket_path", None),
            "tokens_path": getattr(parsed, "tokens", None),
            "server_url": getattr(parsed, "server_url", None),
            "backend": getattr(parsed, "backend", "fastapi"),
            "max_body_size": getattr(parsed, "max_body_size", DEFAULT_MAX_BODY_SIZE),
            "regex_timeout": getattr(parsed, "regex_timeout", DEFAULT_REGEX_TIMEOUT),
            "regex_budget": getattr(parsed, "regex_budget", DEFAULT_REGEX_BUDGET),
            "strict_spec_regex": getattr(parsed, "strict_spec_regex", False),
            "tools_list_ttl_ms": getattr(parsed, "tools_list_ttl_ms", None),
            "tools_list_cache_scope": getattr(parsed, "tools_list_cache_scope", None),
            "openapi_path": getattr(parsed, "openapi_path", None),
            "mcp_path": getattr(parsed, "mcp_path", None),
            "sse_path": getattr(parsed, "sse_path", None),
            "admin_path": getattr(parsed, "admin_path", None),
            "openapi_enabled": getattr(parsed, "openapi", None),
            "mcp_enabled": getattr(parsed, "mcp", None),
            "sse_enabled": getattr(parsed, "sse", None),
            "admin_enabled": getattr(parsed, "admin", None),
            "admin_token": getattr(parsed, "admin_token", None),
            "admin_token_file": getattr(parsed, "admin_token_file", None),
            "admin_allow_private_hosts": getattr(
                parsed, "admin_allow_private_hosts", False
            ),
            "config_reload": getattr(parsed, "config_reload", False),
            "write_config": getattr(parsed, "config_write", True),
        }
        keys_db = getattr(parsed, "keys_db", None)
        if keys_db is not None:
            options["keys_db"] = keys_db
        return options

    def _dispatch_local_hub(self, parsed: argparse.Namespace) -> None:
        """Run one ``local-hub`` action."""
        from .gateway import local_hub

        action = parsed.action
        name = parsed.name
        try:
            if action == "status":
                sys.exit(local_hub.status_hub(name, as_json=parsed.json_output))
            if action == "stop":
                timeout = parsed.timeout or local_hub.DEFAULT_STOP_TIMEOUT
                sys.exit(local_hub.stop_hub(name, timeout=timeout))

            options = self._gateway_options(getattr(parsed, "config", None), parsed)
            if action == "run":
                code = local_hub.run_hub(
                    self.app, options, name=name, idle_timeout=parsed.idle_timeout
                )
            else:
                code = local_hub.ensure_hub(
                    self.app,
                    options,
                    name=name,
                    idle_timeout=parsed.idle_timeout,
                    timeout=parsed.timeout or local_hub.DEFAULT_ENSURE_TIMEOUT,
                    argv=self._argv,
                )
        except local_hub.LocalHubUnsupported as exc:
            logger.error(str(exc))
            sys.exit(1)
        if code:
            sys.exit(code)

    def _dispatch_hub_shim(self, parsed: argparse.Namespace) -> None:
        """Serve one stdio MCP client until it disconnects."""
        from .gateway import local_hub, shim

        config = shim.ShimConfig(
            name=parsed.name,
            config_path=parsed.config,
            socket_path=parsed.socket_path,
            connect_timeout=parsed.connect_timeout,
            spawn=parsed.spawn,
        )
        try:
            code = shim.run_stdio(config)
        except (local_hub.LocalHubUnsupported, shim.ShimError) as exc:
            logger.error(str(exc))
            sys.exit(1)
        if code:
            sys.exit(code)

    def main(self, args: list[str] | None = None) -> NoReturn | None:
        """Run the CLI."""
        parser = self.create_parser()
        self._argv = list(sys.argv[1:] if args is None else args)
        parsed = parser.parse_args(self._argv)

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
