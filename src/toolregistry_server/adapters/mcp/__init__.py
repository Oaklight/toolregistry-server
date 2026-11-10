"""MCP (Model Context Protocol) adapter for ToolRegistry.

This module provides functionality to expose ToolRegistry tools via
the Model Context Protocol for LLM integration.

Main Components:
    - MCPAdapter: Adapter class for serving tools via MCP
    - route_table_to_mcp_server: Create an MCP server from a RouteTable
    - run_stdio / run_sse / run_streamable_http: Transport runners

Example:
    ```python
    from toolregistry import ToolRegistry
    from toolregistry_server import RouteTable
    from toolregistry_server.adapters.mcp import MCPAdapter

    registry = ToolRegistry()
    route_table = RouteTable(registry)
    adapter = MCPAdapter(route_table)
    adapter.run(transport="stdio")
    ```

Note:
    This module requires the 'mcp' extra to be installed:
    pip install toolregistry-server[mcp]
"""

import argparse
import asyncio
from typing import TYPE_CHECKING, Any, Literal

from ..._vendor.structlog import get_logger
from ...isolation import (
    DEFAULT_CLIENT_CONCURRENCY,
    DEFAULT_CONCURRENCY_TIMEOUT,
    ClientLimiter,
    configure_execution_log,
)
from .. import Adapter

logger = get_logger()

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

    from ...keystore import PrincipalResolver
    from ...oauth import OAuthSettings
    from ...route_table import RouteTable


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def route_table_to_mcp_server(
    route_table: "RouteTable",
    name: str = "ToolRegistry-Server",
    *,
    list_tools_ttl_ms: int | None = None,
    list_tools_cache_scope: Literal["public", "private"] | None = None,
    version: str | None = None,
    instructions: str | None = None,
    principal_resolver: "PrincipalResolver | None" = None,
    refuse_operator_token: bool = False,
    client_limiter: Any | None = None,
) -> "Server":
    """Create an MCP low-level server from a RouteTable.

    Registers list_tools and call_tool handlers that read directly
    from the route table, ensuring enable/disable state is always
    in sync (no drift).

    Args:
        route_table: The RouteTable to convert.
        name: Server name for MCP identification.
        list_tools_ttl_ms: Optional ``ttlMs`` cache hint for ``tools/list``.
        list_tools_cache_scope: Optional ``cacheScope`` hint for
            ``tools/list``.
        version: Implementation version for ``serverInfo``.  ``None`` uses
            the installed ``toolregistry-server`` version.
        instructions: Usage guidance for ``initialize``.  ``None`` uses the
            deferred-tool hint when discovery is served.
        principal_resolver: Optional API key resolver, forwarded to the
            adapter.  ``None`` keeps the historical unscoped behaviour.
        refuse_operator_token: Refuse static-token principals on MCP.
        client_limiter: Per-principal call budget, forwarded to the adapter.

    Returns:
        A configured MCP Server instance.

    Raises:
        ImportError: If MCP SDK is not installed.
    """
    from .adapter import route_table_to_mcp_server as _route_table_to_mcp_server

    return _route_table_to_mcp_server(
        route_table,
        name,
        list_tools_ttl_ms=list_tools_ttl_ms,
        list_tools_cache_scope=list_tools_cache_scope,
        version=version,
        instructions=instructions,
        principal_resolver=principal_resolver,
        refuse_operator_token=refuse_operator_token,
        client_limiter=client_limiter,
    )


# ---------------------------------------------------------------------------
# Transport runners
# ---------------------------------------------------------------------------


async def run_stdio(server: "Server") -> None:
    """Run an MCP server over stdio transport.

    Args:
        server: The MCP Server instance to run.
    """
    from .server import run_stdio as _run_stdio

    await _run_stdio(server)


async def run_sse(
    server: "Server",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/sse",
) -> None:
    """Run an MCP server over SSE (Server-Sent Events) transport.

    Args:
        server: The MCP Server instance to run.
        host: Host address to bind to.
        port: Port number to bind to.
        path: URL path for the SSE endpoint.
    """
    from .server import run_sse as _run_sse

    await _run_sse(server, host, port, path)


async def run_streamable_http(
    server: "Server",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
    valid_tokens: set[str] | None = None,
    server_url: str | None = None,
    principal_resolver: "PrincipalResolver | None" = None,
    authorization_servers: list[str] | None = None,
    scopes_supported: "list[str] | tuple[str, ...] | None" = None,
) -> None:
    """Run an MCP server over streamable HTTP transport.

    Args:
        server: The MCP Server instance to run.
        host: Host address to bind to.
        port: Port number to bind to.
        path: URL path for the MCP endpoint.
        valid_tokens: Optional set of accepted Bearer tokens.
        server_url: Public URL of this server for metadata generation.
        principal_resolver: Optional API key resolver.  When set, the
            transport verifies keys through it instead of the static token
            set, and tool handlers enforce the resolved scope.
        authorization_servers: Issuers to advertise in the
            protected-resource metadata.
        scopes_supported: Scopes to advertise in the
            protected-resource metadata.
    """
    from .server import run_streamable_http as _run_streamable_http

    await _run_streamable_http(
        server,
        host,
        port,
        path,
        valid_tokens=valid_tokens,
        server_url=server_url,
        principal_resolver=principal_resolver,
        authorization_servers=authorization_servers,
        scopes_supported=scopes_supported,
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


def _build_principal_resolver(
    keys_db: str | None,
    tokens_path: str | None,
    *,
    transport: str,
    oauth: "OAuthSettings | None" = None,
) -> "PrincipalResolver | None":
    """Open the key store named by ``--keys-db`` and build its resolver.

    Args:
        keys_db: SQLite database path, or ``None`` when key authentication is
            not configured.
        tokens_path: Static token file.  Its tokens become unscoped operator
            credentials inside the resolver.
        transport: The selected MCP transport.  stdio carries no request
            credential, so a key store cannot authenticate anything there;
            the option is reported and ignored instead of silently emptying
            the tool list.
        oauth: OAuth settings, or ``None`` when OAuth is not configured.

    Returns:
        The resolver, or ``None`` when authentication is off.
    """
    if transport == "stdio":
        if keys_db:
            logger.warning(
                "--keys-db is ignored on the stdio transport: there is no "
                "request credential to scope"
            )
        if oauth is not None:
            logger.warning(
                "--oauth-issuer is ignored on the stdio transport: there is "
                "no request credential to verify"
            )
        return None
    if not keys_db and oauth is None:
        return None

    from ...auth import load_tokens
    from ...oauth import build_resolver

    return build_resolver(keys_db, load_tokens(tokens_path), oauth)


def _positive_count(text: str) -> int:
    """Parse an integer that must be at least one."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {text!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _positive_seconds(text: str) -> float:
    """Parse a finite number of seconds that must be greater than zero."""
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid float value: {text!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("must be a finite number of seconds")
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


class MCPAdapter(Adapter):
    """Serve tools via the Model Context Protocol.

    Args:
        route_table: The RouteTable to expose.
        name: Server name for MCP identification.
        list_tools_ttl_ms: Optional ``ttlMs`` cache hint for ``tools/list``.
        list_tools_cache_scope: Optional ``cacheScope`` hint for
            ``tools/list``.
        version: Implementation version for ``serverInfo``.  Defaults to the
            installed ``toolregistry-server`` version.
        instructions: Usage guidance for ``initialize``.  Defaults to the
            deferred-tool hint when discovery is served.
        principal_resolver: Optional resolver for inbound API keys.  When
            set, ``tools/list`` is filtered and ``tools/call`` is scoped;
            ``None`` keeps static-token behaviour unchanged.
        oauth_issuer: Authorization-server issuer to advertise in the
            protected-resource metadata.  ``None`` advertises this server's
            origin, as before.
        allow_operator_token: Accept static ``--tokens`` credentials when a
            key store is configured.  Without it a key-scoped MCP endpoint
            refuses them, because every client would share one principal.
        client_limiter: Per-principal concurrency budget.  ``None`` runs
            calls without one; the CLI sets it for network transports.
        execution_log: Record tool calls in the registry execution log.
            ``None`` enables it when a key store is configured, ``True``
            forces it on and ``False`` leaves it off.  The adapter calls
            route handlers directly, so core writes nothing without this.
    """

    def __init__(
        self,
        route_table: "RouteTable",
        *,
        name: str = "ToolRegistry-Server",
        list_tools_ttl_ms: int | None = None,
        list_tools_cache_scope: Literal["public", "private"] | None = None,
        version: str | None = None,
        instructions: str | None = None,
        principal_resolver: "PrincipalResolver | None" = None,
        oauth_issuer: str | None = None,
        oauth_scopes: "tuple[str, ...] | None" = None,
        allow_operator_token: bool = False,
        client_limiter: ClientLimiter | None = None,
        execution_log: bool | None = None,
    ) -> None:
        super().__init__(route_table)
        self._principal_resolver = principal_resolver
        self._oauth_issuer = oauth_issuer
        self._oauth_scopes = oauth_scopes or None
        self._client_limiter = client_limiter
        has_keys = bool(
            principal_resolver is not None
            and getattr(principal_resolver, "has_keys", False)
        )
        configure_execution_log(
            route_table.registry,
            enabled=execution_log,
            has_keys=has_keys or oauth_issuer is not None,
        )
        self._server = route_table_to_mcp_server(
            route_table,
            name,
            list_tools_ttl_ms=list_tools_ttl_ms,
            list_tools_cache_scope=list_tools_cache_scope,
            version=version,
            instructions=instructions,
            principal_resolver=principal_resolver,
            refuse_operator_token=bool(not allow_operator_token and has_keys),
            client_limiter=client_limiter,
        )

    @property
    def server(self) -> "Server":
        """The MCP Server instance."""
        return self._server

    def run(
        self,
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
        **kwargs,
    ) -> None:
        """Start the MCP server (blocking).

        Calls :meth:`run_async` via ``asyncio.run()``.  Use
        :meth:`run_async` directly when already inside an event loop.

        Args:
            transport: MCP transport type.  One of ``"stdio"``, ``"sse"``,
                ``"streamable-http"``, or ``"http"`` (alias).
            host: Host address to bind to (SSE/HTTP only).
            port: Port number to bind to (SSE/HTTP only).
            tokens_path: Path to Bearer token file (streamable-http only).
            server_url: Public server URL (streamable-http only).
        """
        asyncio.run(self.run_async(transport=transport, host=host, port=port, **kwargs))

    async def run_async(
        self,
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
        **kwargs,
    ) -> None:
        """Start the MCP server (async).

        Use this when already inside an event loop.  Otherwise use
        :meth:`run` for the blocking variant.

        Args:
            transport: MCP transport type.  One of ``"stdio"``, ``"sse"``,
                ``"streamable-http"``, or ``"http"`` (alias).
            host: Host address to bind to (SSE/HTTP only).
            port: Port number to bind to (SSE/HTTP only).
            tokens_path: Path to Bearer token file (streamable-http only).
            server_url: Public server URL (streamable-http only).
        """
        if transport == "http":
            transport = "streamable-http"
        tokens_path = kwargs.get("tokens_path")
        server_url = kwargs.get("server_url")

        logger.info(f"Starting MCP server with {transport} transport")
        logger.info(f"Registered {len(self._route_table.list_routes())} tool(s)")

        if transport == "stdio":
            await run_stdio(self._server)
        elif transport == "sse":
            logger.info(f"SSE endpoint: http://{host}:{port}/sse")
            await run_sse(self._server, host=host, port=port)
        elif transport == "streamable-http":
            logger.info(f"HTTP endpoint: http://{host}:{port}/mcp")
            from ...auth import load_tokens

            tokens_list = load_tokens(tokens_path)
            valid_tokens = set(tokens_list) if tokens_list else None
            await run_streamable_http(
                self._server,
                host=host,
                port=port,
                valid_tokens=valid_tokens,
                server_url=server_url,
                principal_resolver=self._principal_resolver,
                authorization_servers=(
                    [self._oauth_issuer] if self._oauth_issuer else None
                ),
                scopes_supported=self._oauth_scopes,
            )
        else:
            raise ValueError(f"Unknown transport type: {transport}")

    @staticmethod
    def add_cli_arguments(parser) -> None:
        """Add MCP-specific CLI arguments."""
        Adapter.add_cli_arguments(parser)
        parser.add_argument(
            "--transport",
            type=str,
            choices=["stdio", "sse", "streamable-http", "http"],
            default="stdio",
            help="Transport type: stdio, sse, streamable-http (or http) (default: stdio)",
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
            "--tokens",
            type=str,
            default=None,
            metavar="PATH",
            help=(
                "Path to a file of Bearer tokens (one per line) for "
                "streamable-http. Falls back to the API_BEARER_TOKEN "
                "environment variable. These tokens are unscoped bootstrap "
                "credentials"
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
                "are enforced on tools/list and tools/call. Static --tokens "
                "keep full, unscoped access. Omit for the previous behaviour"
            ),
        )
        parser.add_argument(
            "--allow-operator-token",
            action="store_true",
            help=(
                "Accept static --tokens credentials on MCP when --keys-db is "
                "set. Without this flag a key-scoped MCP endpoint refuses them"
            ),
        )
        parser.add_argument(
            "--client-concurrency",
            type=_positive_count,
            default=DEFAULT_CLIENT_CONCURRENCY,
            metavar="N",
            help=(
                "In-flight calls one client may run at once on a network "
                "transport, unless its key sets a lower concurrency "
                f"(default: {DEFAULT_CLIENT_CONCURRENCY})"
            ),
        )
        parser.add_argument(
            "--concurrency-timeout",
            type=_positive_seconds,
            default=DEFAULT_CONCURRENCY_TIMEOUT,
            metavar="SECONDS",
            help=(
                "How long a call waits for its client's slot before it is "
                f"refused (default: {DEFAULT_CONCURRENCY_TIMEOUT:g})"
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
        parser.add_argument(
            "--server-url",
            type=str,
            default=None,
            metavar="URL",
            help=(
                "Public URL of this server, used for OAuth protected-resource "
                "metadata (e.g. https://tools.example.com/mcp). Required behind "
                "a reverse proxy, where the bind address is not reachable "
                "(default: derived from --host/--port)"
            ),
        )

        parser.add_argument(
            "--tools-list-ttl-ms",
            type=int,
            default=None,
            metavar="MS",
            help=(
                "ttlMs cache hint for tools/list, in milliseconds "
                "(default: 300000 — five minutes). Clients that open a "
                "subscriptions/listen stream have their cache invalidated on "
                "every change; clients that do not will keep the list for up "
                "to the TTL. Set 0 to disable caching"
            ),
        )
        parser.add_argument(
            "--tools-list-cache-scope",
            choices=["public", "private"],
            default=None,
            help=(
                "cacheScope hint for tools/list (default: private, since the "
                "served set depends on profile and authorization)"
            ),
        )

    @classmethod
    def create_and_run(cls, route_table: "RouteTable", **kwargs) -> None:
        """Construct and run an MCP server in one step.

        Reads ``identity`` from kwargs for server name default.
        Explicit ``name`` kwarg takes precedence over identity.

        When ``keys_db`` is given, an :class:`~toolregistry_server.keystore.
        ApiKeyStore` is opened and its resolver is attached to the server.
        Static ``tokens_path`` credentials are passed to the resolver as
        unscoped operator tokens, so they keep full access.  ``keys_db`` is
        ignored on the stdio transport, which carries no request credential.

        A network transport also gets a per-principal concurrency limiter
        built from ``client_concurrency`` and ``concurrency_timeout``; stdio
        validates them but runs unthrottled, since it has one client.
        """
        from ...identity import ServerIdentity

        identity: ServerIdentity = kwargs.pop("identity", ServerIdentity())
        name = kwargs.pop("name", identity.name)
        keys_db = kwargs.pop("keys_db", None)
        oauth = kwargs.pop("oauth", None)
        transport = kwargs.get("transport", "stdio")
        principal_resolver = _build_principal_resolver(
            keys_db,
            kwargs.get("tokens_path"),
            transport=transport,
            oauth=oauth,
        )
        # Build the limiter even for stdio so an invalid knob fails at
        # startup; a single stdio client has no per-client identity to
        # throttle, so it runs without one.
        limiter = ClientLimiter(
            kwargs.pop("client_concurrency", DEFAULT_CLIENT_CONCURRENCY),
            kwargs.pop("concurrency_timeout", DEFAULT_CONCURRENCY_TIMEOUT),
        )
        adapter = cls(
            route_table,
            name=name,
            list_tools_ttl_ms=kwargs.pop("tools_list_ttl_ms", None),
            list_tools_cache_scope=kwargs.pop("tools_list_cache_scope", None),
            version=identity.version,
            instructions=kwargs.pop("instructions", None),
            principal_resolver=principal_resolver,
            oauth_issuer=(
                oauth.issuer if oauth is not None and transport != "stdio" else None
            ),
            oauth_scopes=(
                oauth.scopes_supported
                if oauth is not None and transport != "stdio"
                else None
            ),
            allow_operator_token=kwargs.pop("allow_operator_token", False),
            client_limiter=None if transport == "stdio" else limiter,
            execution_log=kwargs.pop("execution_log", None),
        )
        adapter.run(**kwargs)


__all__ = [
    "MCPAdapter",
    "route_table_to_mcp_server",
    "run_stdio",
    "run_sse",
    "run_streamable_http",
]
