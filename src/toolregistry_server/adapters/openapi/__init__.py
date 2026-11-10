"""OpenAPI adapter for ToolRegistry.

This module exposes ToolRegistry tools as RESTful HTTP endpoints on the
vendored zero-dependency ``httpserver``; no third-party packages are
required.  The FastAPI assembly moved to
:mod:`toolregistry_server.gateway.openapi_asgi` and is only loaded by the
gateway, which needs the ``gateway`` extra.

Main Components:
    - OpenAPIAdapter: Adapter class for serving tools via OpenAPI

Example:
    ```python
    from toolregistry import ToolRegistry
    from toolregistry_server import RouteTable
    from toolregistry_server.adapters.openapi import OpenAPIAdapter

    registry = ToolRegistry()
    route_table = RouteTable(registry)
    adapter = OpenAPIAdapter(route_table)
    adapter.serve(host="0.0.0.0", port=8000)
    ```
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from ..._vendor.httpserver import DEFAULT_MAX_BODY_SIZE
from ..._vendor.structlog import get_logger
from ...isolation import configure_execution_log
from ...regex_guard import DEFAULT_REGEX_BUDGET, DEFAULT_REGEX_TIMEOUT
from .. import Adapter

logger = get_logger()

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...route_table import RouteTable


def _build_key_resolver(
    keys_db: str | None,
    operator_tokens: Sequence[str],
    oauth: Any | None = None,
) -> Any | None:
    """Build the resolver named by ``--keys-db`` and the OAuth options.

    Args:
        keys_db: SQLite database path, or ``None`` when key authentication is
            not configured.
        operator_tokens: Static tokens that keep full, unscoped access.
        oauth: OAuth settings, or ``None`` when OAuth is not configured.

    Returns:
        The resolver, or ``None`` when authentication is off.
    """
    if not keys_db and oauth is None:
        return None

    from ...oauth import build_resolver

    return build_resolver(keys_db, operator_tokens, oauth)


def _positive_int(text: str) -> int:
    """Argparse type for a byte count that must be greater than zero.

    The factory and the adapter reject a non-positive cap at construction;
    rejecting it at the parser too gives one error message.
    """
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {text!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of bytes")
    return value


def _non_negative_float(text: str) -> float:
    """Argparse type for a time bound in seconds; ``0`` disables it."""
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid float value: {text!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("must be a finite number of seconds")
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class OpenAPIAdapter(Adapter):
    """Serve tools as RESTful HTTP endpoints.

    The adapter runs on the vendored zero-dependency ``httpserver``.  The
    FastAPI/uvicorn assembly is part of the gateway
    (:mod:`toolregistry_server.gateway.openapi_asgi`) and is not reachable
    from this standalone command.

    Args:
        route_table: The RouteTable to expose.
        tokens: Optional list of Bearer tokens for authentication.
        title: API title for OpenAPI schema.
        version: API version string.
        description: API description for OpenAPI schema.
        max_body_size: Largest request body accepted, in bytes. Defaults to
            1 MiB.
        regex_timeout: Per-match ceiling for spec-supplied regexes, in
            seconds. Requires the optional ``regex`` extra; ``0`` disables
            execution and keeps spec patterns out of validation.
        regex_budget: Per-request ceiling for all spec-supplied regex
            matches, in seconds. ``0`` disables execution.
        principal_resolver: Optional API key resolver.  When set, the
            caller's key scope filters tool routes, ``/tools`` and
            ``/openapi.json``.  Static *tokens* keep full, unscoped access
            when both are supplied.
        execution_log: Record tool calls in the registry execution log.
            ``None`` enables it when a key store is configured, ``True``
            forces it on and ``False`` leaves it off.  The app calls route
            handlers directly, so core writes nothing without this switch.
        oauth: OAuth resource-server settings.  When set, the served OpenAPI
            document describes the authorization-code flow and, if keys or
            static tokens are also accepted, the OR of both credentials.
    """

    def __init__(
        self,
        route_table: RouteTable,
        *,
        tokens: list[str] | None = None,
        title: str = "ToolRegistry Server",
        version: str = "1.0.0",
        description: str = "OpenAPI server for ToolRegistry tools",
        max_body_size: int = DEFAULT_MAX_BODY_SIZE,
        regex_timeout: float = DEFAULT_REGEX_TIMEOUT,
        regex_budget: float = DEFAULT_REGEX_BUDGET,
        principal_resolver: Any | None = None,
        execution_log: bool | None = None,
        oauth: Any | None = None,
    ) -> None:
        super().__init__(route_table)

        from .server_app import create_httpserver_app

        has_keys = bool(
            principal_resolver is not None
            and getattr(principal_resolver, "has_keys", False)
        )
        configure_execution_log(
            route_table.registry,
            enabled=execution_log,
            has_keys=has_keys or oauth is not None,
        )
        openapi_auth = None
        if oauth is not None:
            from ...oauth import OpenAPIAuth

            openapi_auth = OpenAPIAuth(oauth, bearer_accepted=bool(tokens) or has_keys)
        self._app = create_httpserver_app(
            route_table,
            title=title,
            version=version,
            description=description,
            tokens=tokens,
            max_body_size=max_body_size,
            regex_timeout=regex_timeout,
            regex_budget=regex_budget,
            principal_resolver=principal_resolver,
            openapi_auth=openapi_auth,
        )

    @property
    def app(self) -> Any:
        """The application instance for the configured backend."""
        return self._app

    def run(self, *, host: str = "0.0.0.0", port: int = 8000, **kwargs) -> None:
        """Start the OpenAPI server.

        Args:
            host: Host to bind to.
            port: Port to bind to.
            **kwargs: Accepted and ignored: the standalone command has no
                options left for the runner, and the httpserver must not
                reject callers that still pass adapter-wide settings.
        """
        if kwargs.get("reload"):
            logger.warning(
                "--reload is a uvicorn feature; the standalone OpenAPI "
                "server runs on httpserver and ignores it. Use the gateway "
                "with --backend fastapi for auto-reload."
            )
        logger.info(f"Starting OpenAPI server on {host}:{port}")
        logger.info(f"Registered {len(self._route_table.list_routes())} tool(s)")
        self._app.run(host=host, port=port)

    @staticmethod
    def add_cli_arguments(parser) -> None:
        """Add OpenAPI-specific CLI arguments.

        The standalone command always serves the vendored httpserver.  The
        FastAPI/uvicorn assembly is available through the ``gateway``
        command, which keeps its own ``--backend`` flag.
        """
        Adapter.add_cli_arguments(parser)
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
            "--tokens",
            type=str,
            default=None,
            help=(
                "Path to a file containing authentication tokens (one per "
                "line). These are unscoped bootstrap credentials"
            ),
        )
        parser.add_argument(
            "--keys-db",
            type=str,
            default=None,
            metavar="PATH",
            help=(
                "Path to the SQLite API key database. When set, keys from "
                "this database are authenticated and their namespace scopes "
                "filter tool routes, /tools and /openapi.json. Static "
                "--tokens keep full, unscoped access. Omit for the previous "
                "behaviour"
            ),
        )
        parser.add_argument(
            "--max-body-size",
            type=_positive_int,
            default=DEFAULT_MAX_BODY_SIZE,
            metavar="BYTES",
            help=(
                "Largest request body accepted, in bytes "
                f"(default: {DEFAULT_MAX_BODY_SIZE}); larger bodies return 413"
            ),
        )
        parser.add_argument(
            "--regex-timeout",
            type=_non_negative_float,
            default=DEFAULT_REGEX_TIMEOUT,
            metavar="SECONDS",
            help=(
                "Per-match ceiling for spec-supplied regexes when the regex "
                f"extra is installed (default: {DEFAULT_REGEX_TIMEOUT:g}); "
                "0 disables execution and ignores spec patterns"
            ),
        )
        parser.add_argument(
            "--regex-budget",
            type=_non_negative_float,
            default=DEFAULT_REGEX_BUDGET,
            metavar="SECONDS",
            help=(
                "Per-request ceiling for all spec-supplied regex matches "
                f"(default: {DEFAULT_REGEX_BUDGET:g}); 0 disables execution"
            ),
        )
        parser.add_argument(
            "--strict-spec-regex",
            action="store_true",
            help=(
                "Abort startup when an OpenAPI source contains an invalid "
                "regex; by default that source is skipped and logged"
            ),
        )
        parser.add_argument(
            "--execution-log",
            action=argparse.BooleanOptionalAction,
            default=None,
            help=(
                "Record every tool call in the registry execution log "
                "(default: on when --keys-db is set; the admin Logs page "
                "shows the entries)"
            ),
        )

    @classmethod
    def create_and_run(cls, route_table: RouteTable, **kwargs) -> None:
        """Construct and run an OpenAPI server in one step.

        Reads ``identity`` from kwargs for title/version/description
        defaults.  Explicit ``title``/``version``/``description``
        kwargs take precedence over identity.
        """
        from ...auth import load_tokens
        from ...identity import ServerIdentity

        identity: ServerIdentity = kwargs.pop("identity", ServerIdentity())
        tokens = load_tokens(kwargs.pop("tokens_path", None))
        oauth = kwargs.pop("oauth", None)
        principal_resolver = _build_key_resolver(
            kwargs.pop("keys_db", None), tokens, oauth
        )
        adapter = cls(
            route_table,
            tokens=tokens or None,
            title=kwargs.pop("title", identity.name),
            version=kwargs.pop("version", identity.version),
            description=kwargs.pop("description", identity.description),
            max_body_size=kwargs.pop("max_body_size", DEFAULT_MAX_BODY_SIZE),
            regex_timeout=kwargs.pop("regex_timeout", DEFAULT_REGEX_TIMEOUT),
            regex_budget=kwargs.pop("regex_budget", DEFAULT_REGEX_BUDGET),
            principal_resolver=principal_resolver,
            execution_log=kwargs.pop("execution_log", None),
            oauth=oauth,
        )
        adapter.run(**kwargs)


__all__ = [
    "OpenAPIAdapter",
]
