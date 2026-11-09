"""stdio shim that connects one MCP client to a local hub (#145).

A hub is one resident gateway; each MCP client gets its own stdio shim that
proxies to it.  The shim owns the stdio side and keeps the client usable
while the hub is down:

* ``initialize`` is answered locally, and ``tools/list`` returns an empty
  list until a hub answers;
* a tool call made while the hub is unavailable fails once with a clear MCP
  error, and a background reconnect (0.25 s to 5 s backoff) restores service;
* after a reconnect, a changed tool fingerprint notifies the client with
  ``notifications/tools/list_changed``.

The endpoint document is discovery, not proof of life: the shim cannot hold
the hub's lock, so "a document exists but nothing answers on its address" is
treated as "no hub" and the shim starts one through the ``ensure`` path.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import io
import json
import time
import weakref
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from .. import __version__
from .._vendor.structlog import get_logger
from ..adapters.mcp._compat import (
    create_mcp_server,
    get_field,
    make_mcp_error,
    tools_changed_notification_options,
)
from . import local_hub
from .endpoint import DEFAULT_NAME, EndpointError, HubEndpoint, read_live_endpoint

logger = get_logger()

#: Connect + initialize budget for one hub attempt.
DEFAULT_CONNECT_TIMEOUT = 5.0
#: First reconnect delay and the ceiling it doubles towards.
INITIAL_BACKOFF = 0.25
MAX_BACKOFF = 5.0
#: JSON-RPC server-error code used for "hub unavailable".
HUB_ERROR_CODE = -32000
#: Minimum seconds between two spawned hubs from one shim.
SPAWN_COOLDOWN = 60.0
#: Seconds a session cleanup may take before its task is abandoned.
CLEANUP_TIMEOUT = 10.0

Notifier = Callable[[], Awaitable[None]]


class ShimError(RuntimeError):
    """The shim cannot serve this hub, and a retry will not fix it."""


@dataclass(frozen=True)
class ShimConfig:
    """How the shim finds and (optionally) starts its hub.

    Attributes:
        name: Hub name; keeps hubs apart.
        config_path: Config the shim hands to a hub it starts.
        socket_path: Unix socket the hub listens on, when opting out of TCP.
        connect_timeout: Seconds allowed for one connect + initialize.
        spawn: Whether the shim may start a hub that is not running.
    """

    name: str = DEFAULT_NAME
    config_path: str | None = None
    socket_path: str | None = None
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    spawn: bool = True


def _streamable_http_module() -> Any:
    return importlib.import_module("mcp.client.streamable_http")


def _sdk_http_module(transport_module: Any) -> Any:
    """Return the HTTP client module the installed MCP SDK uses.

    SDK 2.x talks to ``httpx2``; 1.x talks to ``httpx``.  Reading the module
    the transport imported keeps the shim's UDS client on the same library as
    the transport, instead of adding a second one.
    """
    for name in ("httpx2", "httpx"):
        module = getattr(transport_module, name, None)
        if module is not None:
            return module
    return importlib.import_module("httpx")


def _uds_client(transport_module: Any, socket_path: str) -> Any:
    http = _sdk_http_module(transport_module)
    # The streamable-HTTP transport keeps a GET stream open, so the client
    # needs the SDK's read timeout (300 s); the library default (5 s) would
    # tear the transport down while the hub is simply quiet.
    timeout = http.Timeout(30.0, read=300.0)
    return http.AsyncClient(
        transport=http.AsyncHTTPTransport(uds=socket_path), timeout=timeout
    )


def open_hub_transport(url: str, socket_path: str | None = None) -> Any:
    """Open the streamable-HTTP transport to a hub.

    Args:
        url: MCP endpoint URL (``http://host:port/mcp`` or a placeholder for
            a Unix socket).
        socket_path: Unix socket to connect through, or ``None`` for TCP.

    Returns:
        An async context manager yielding ``(read_stream, write_stream)``.

    Raises:
        ShimError: When the installed SDK has no streamable-HTTP client.
    """
    module = _streamable_http_module()
    new_style = getattr(module, "streamable_http_client", None)
    if new_style is not None:
        client = _uds_client(module, socket_path) if socket_path else None
        return new_style(url, http_client=client)

    old_style = getattr(module, "streamablehttp_client", None)
    if old_style is None:  # pragma: no cover - both names cover the range
        raise ShimError("the installed MCP SDK has no streamable-HTTP client")
    if socket_path is None:
        return old_style(url)

    def factory(*_args: Any, **kwargs: Any) -> Any:
        allowed = {
            key: kwargs[key] for key in ("headers", "timeout", "auth") if key in kwargs
        }
        http = _sdk_http_module(module)
        return http.AsyncClient(
            transport=http.AsyncHTTPTransport(uds=socket_path), **allowed
        )

    return old_style(url, httpx_client_factory=factory)


def endpoint_url(endpoint: HubEndpoint) -> str:
    """Return the MCP endpoint URL for *endpoint*.

    Args:
        endpoint: The hub endpoint document.

    Returns:
        The URL to hand to the streamable-HTTP client.
    """
    path = dict(endpoint.frontends).get("mcp", "/mcp")
    if endpoint.transport == "unix":
        return f"http://localhost{path}"
    return f"{endpoint.url or 'http://127.0.0.1'}{path}"


def tool_fingerprint(tools: list[Any]) -> str:
    """Return a stable digest of the tool names, descriptions and schemas."""
    signature = [
        {
            "name": get_field(tool, "name", "name"),
            "description": get_field(tool, "description", "description"),
            "schema": get_field(tool, "input_schema", "inputSchema"),
        }
        for tool in tools
    ]
    payload = json.dumps(signature, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unavailable(config: ShimConfig, detail: str | None = None) -> Exception:
    message = f"hub {config.name!r} is unavailable"
    if detail:
        message = f"{message}: {detail}"
    return make_mcp_error(HUB_ERROR_CODE, message)


def _describe_exception(exc: BaseException) -> str:
    """Return *exc* and the exceptions nested inside it, in one line."""
    children = getattr(exc, "exceptions", None)
    text = _as_exception_text(exc)
    if not children:
        return text
    detail = "; ".join(_describe_exception(child) for child in children)
    return f"{text}: {detail}" if text else detail


class HubConnection:
    """The shim's side of the hub: discovery, connection and reconnection.

    Args:
        config: Hub discovery and spawn options.
    """

    def __init__(self, config: ShimConfig) -> None:
        self._config = config
        self._session: Any = None
        self._owner: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._tools: list[Any] = []
        self._fingerprint: str | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task | None = None
        self._notify_tasks: set[asyncio.Task] = set()
        self._notifier: Notifier | None = None
        self._last_spawn = float("-inf")

    def set_notifier(self, notifier: Notifier | None) -> None:
        """Register the callback that tells clients the tool list moved."""
        self._notifier = notifier

    @property
    def connected(self) -> bool:
        """Whether a hub session is open right now."""
        return self._session is not None

    @property
    def tools(self) -> list[Any]:
        """The last tool list fetched from the hub."""
        return self._tools

    async def start(self) -> None:
        """Make the first connection attempt and retry in the background.

        Raises:
            ShimError: When the hub speaks another endpoint protocol; that
                cannot be fixed by reconnecting.
        """
        self._closed = False
        await self.ensure(spawn=False)
        if not self.connected:
            self._schedule_reconnect()

    async def close(self) -> None:
        """Close the session and stop reconnecting."""
        self._closed = True
        task = self._reconnect_task
        self._reconnect_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._drop()

    async def ensure(self, *, spawn: bool = True) -> bool:
        """Connect if needed; return whether a hub session is open.

        Args:
            spawn: Whether a missing hub may be started.  Startup uses
                ``False`` so ``initialize`` never waits for a spawn.

        Returns:
            ``True`` when the connection is ready.

        Raises:
            ShimError: On a protocol-major mismatch.
        """
        async with self._lock:
            if self._session is not None:
                if self._owner is not None and not self._owner.done():
                    return True
                # The hub session ended while idle; reconnect before the call.
                await self._drop()
            try:
                endpoint = await asyncio.to_thread(self._discover)
            except EndpointError as exc:
                raise ShimError(str(exc)) from exc
            if endpoint is None and spawn:
                await asyncio.to_thread(self._ensure_hub)
                try:
                    endpoint = await asyncio.to_thread(self._discover)
                except EndpointError as exc:
                    raise ShimError(str(exc)) from exc
            if endpoint is None:
                return False
            return await self._connect(endpoint)

    async def list_tools(self) -> list[Any]:
        """Return the hub's tools, or an empty list while it is down."""
        if not await self.ensure():
            return []
        session = self._session
        try:
            result = await asyncio.wait_for(
                session.list_tools(), self._config.connect_timeout
            )
        except Exception as exc:
            logger.warning(f"hub tools/list failed: {_describe_exception(exc)}")
            await self._drop()
            self._schedule_reconnect()
            return []
        self._update_tools(list(result.tools))
        return self._tools

    async def call_tool(self, name: str, arguments: Mapping[str, Any] | None) -> Any:
        """Call one tool on the hub.

        Args:
            name: Tool name.
            arguments: Tool arguments.

        Returns:
            The hub's ``CallToolResult``.

        Raises:
            Exception: An MCP error carrying :data:`HUB_ERROR_CODE` when the
                hub cannot serve this call.  Both SDK generations turn it
                into an ``isError`` tool result with the message; the next
                request may succeed once the background reconnect finishes.
        """
        if not await self.ensure():
            self._schedule_reconnect()
            raise _unavailable(self._config)
        session = self._session
        try:
            return await self._call(session, name, dict(arguments or {}))
        except Exception as exc:
            logger.warning(f"hub call {name!r} failed: {_describe_exception(exc)}")
            await self._drop()
            self._schedule_reconnect()
            raise _unavailable(self._config, str(exc)) from exc

    async def _call(self, session: Any, name: str, arguments: dict[str, Any]) -> Any:
        """Run one call, failing as soon as the owning task dies.

        SDK v1 leaves a request waiting when the transport closes without
        waking it, so the owner task is the signal that the hub is gone.
        """
        task = asyncio.ensure_future(session.call_tool(name, arguments))
        try:
            owner = self._owner
            if owner is None:
                return await task
            await asyncio.wait({task, owner}, return_when=asyncio.FIRST_COMPLETED)
            if not task.done():
                raise ShimError("the connection to the hub closed")
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def call_tool_pair(
        self, name: str, arguments: Mapping[str, Any] | None
    ) -> tuple[list[Any], Any]:
        """Adapt :meth:`call_tool` to ``create_mcp_server``'s handler shape."""
        result = await self.call_tool(name, arguments)
        return list(result.content), get_field(
            result, "structured_content", "structuredContent"
        )

    def _discover(self) -> HubEndpoint | None:
        """Return a live, answerable endpoint, or ``None`` for "no hub"."""
        try:
            endpoint = read_live_endpoint(self._config.name)
        except EndpointError as exc:
            # A malformed or unsafe document is not proof of a hub either.
            logger.warning(f"ignoring the hub endpoint: {exc}")
            return None
        if endpoint is None:
            return None
        if not local_hub._endpoint_accepts(endpoint):
            # A document with nothing answering on its address is stale; a
            # stale document must not block a start with a protocol refusal.
            return None
        endpoint.check_protocol()
        return endpoint

    def _ensure_hub(self) -> None:
        """Start a hub through ``local-hub ensure``, at most once a minute."""
        if not self._config.spawn:
            return
        if self._config.config_path is None:
            # A hub needs a config file to build its tool registry; without
            # one the shim can only wait for an operator-started hub.
            return
        now = time.monotonic()
        if now - self._last_spawn < SPAWN_COOLDOWN:
            return
        self._last_spawn = now
        argv = ["local-hub", "run", "--name", self._config.name]
        if self._config.config_path is not None:
            argv += ["--config", self._config.config_path]
        if self._config.socket_path is not None:
            argv += ["--socket", self._config.socket_path]
        options = {
            "config_path": self._config.config_path,
            "socket_path": self._config.socket_path,
        }
        # ensure_hub prints the endpoint to stdout; stdout is the MCP channel.
        with contextlib.redirect_stdout(io.StringIO()):
            local_hub.ensure_hub(
                None,
                options,
                name=self._config.name,
                timeout=local_hub.DEFAULT_ENSURE_TIMEOUT,
                argv=argv,
            )

    async def _connect(self, endpoint: HubEndpoint) -> bool:
        """Open and initialize one hub session, bounded by the timeout.

        The transport and session are entered in their own long-lived task:
        the streamable-HTTP transport binds its task group to the task that
        entered it, so a hub dying would otherwise cancel the caller -- at
        startup, the whole stdio server.
        """
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        ready: asyncio.Future = loop.create_future()
        owner = loop.create_task(self._own_session(endpoint, ready, stop))
        try:
            session, tools = await asyncio.wait_for(
                self._owner_ready(owner, ready), self._config.connect_timeout
            )
        except asyncio.CancelledError:
            stop.set()
            owner.cancel()
            raise
        except Exception as exc:
            stop.set()
            owner.cancel()
            await self._settle(owner)
            self._consume_ready(ready)
            logger.warning(f"hub connect failed: {_describe_exception(exc)}")
            return False
        self._owner = owner
        self._stop = stop
        self._session = session
        previous = self._fingerprint
        self._update_tools(tools)
        if (
            previous is not None
            and previous != self._fingerprint
            and self._notifier is not None
        ):
            self._schedule_notify()
        return True

    @staticmethod
    async def _owner_ready(
        owner: asyncio.Task, ready: asyncio.Future
    ) -> tuple[Any, list[Any]]:
        """Wait for the owning task to publish its session, or to fail."""
        done, _ = await asyncio.wait(
            {owner, ready}, return_when=asyncio.FIRST_COMPLETED
        )
        if ready in done:
            return ready.result()
        if owner.cancelled():
            raise ShimError("the hub session was cancelled")
        error = owner.exception()
        if error is not None:
            raise error
        raise ShimError("the hub session ended before it was ready")

    @staticmethod
    def _consume_ready(ready: asyncio.Future) -> None:
        """Retrieve a result nobody will read, so no warning is logged."""
        if ready.done() and not ready.cancelled():
            ready.exception()

    async def _own_session(
        self, endpoint: HubEndpoint, ready: asyncio.Future, stop: asyncio.Event
    ) -> None:
        """Own the transport and session until *stop* is set.

        Entering and leaving the stack happens in this task, as the SDK's
        context managers require.
        """
        try:
            stack, session, tools = await self._open(endpoint)
        except BaseException as exc:
            if not ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    ready.set_exception(ShimError("the hub session was cancelled"))
                else:
                    ready.set_exception(exc)
            return
        if not ready.done():
            ready.set_result((session, tools))
        try:
            await stop.wait()
        finally:
            with contextlib.suppress(BaseException):
                await stack.aclose()

    async def _open(
        self, endpoint: HubEndpoint
    ) -> tuple[AsyncExitStack, Any, list[Any]]:
        from mcp.client.session import ClientSession

        socket = endpoint.socket if endpoint.transport == "unix" else None
        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(
                open_hub_transport(endpoint_url(endpoint), socket)
            )
            session = await stack.enter_async_context(
                ClientSession(streams[0], streams[1])
            )
            await session.initialize()
            tools = list((await session.list_tools()).tools)
        except BaseException:
            await stack.aclose()
            raise
        return stack, session, tools

    def _update_tools(self, tools: list[Any]) -> None:
        self._tools = tools
        self._fingerprint = tool_fingerprint(tools)

    async def _drop(self) -> None:
        owner, self._owner = self._owner, None
        stop, self._stop = self._stop, None
        self._session = None
        if owner is None:
            return
        if stop is not None:
            stop.set()
        # The owner closes the transport stack in its own task; wait for it
        # so a reconnect starts from a clean state.
        await self._settle(owner)

    @staticmethod
    async def _settle(task: asyncio.Task) -> None:
        """Wait for *task*, abandoning it after :data:`CLEANUP_TIMEOUT`."""
        done, _ = await asyncio.wait({task}, timeout=CLEANUP_TIMEOUT)
        if not done:
            task.cancel()
            await asyncio.wait({task}, timeout=CLEANUP_TIMEOUT)

    def _schedule_reconnect(self) -> None:
        if self._closed:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._reconnect_task = loop.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        delay = INITIAL_BACKOFF
        while not self._closed:
            try:
                if await self.ensure():
                    return
            except ShimError as exc:
                logger.warning(f"hub reconnect refused: {exc}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF)

    def _schedule_notify(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - always called from a loop
            return
        task = loop.create_task(self._notify())
        self._notify_tasks.add(task)
        task.add_done_callback(self._notify_tasks.discard)

    async def _notify(self) -> None:
        if self._notifier is not None:
            await self._notifier()


def _send_tools_changed(sessions: weakref.WeakSet) -> Notifier:
    """Build the notifier that tells every connected client the list moved."""

    async def notify() -> None:
        for session in list(sessions):
            try:
                await session.send_tool_list_changed()
            except Exception as exc:
                logger.warning(f"tools/list_changed failed: {_as_exception_text(exc)}")
                sessions.discard(session)

    return notify


def _as_exception_text(exc: BaseException) -> str:
    return str(exc) or exc.__class__.__name__


def build_server(connection: HubConnection, sessions: weakref.WeakSet) -> Any:
    """Build the stdio-side MCP server that proxies to the hub."""
    return create_mcp_server(
        name="toolregistry-hub-shim",
        list_tools_handler=connection.list_tools,
        call_tool_handler=connection.call_tool_pair,
        session_tracker=sessions,
        version=__version__,
        instructions=(
            "Tools are served by a local ToolRegistry hub; the shim keeps "
            "this connection alive while the hub restarts."
        ),
    )


async def run_shim(config: ShimConfig) -> int:
    """Serve one MCP client over stdio, proxying to the configured hub.

    Args:
        config: Hub discovery and spawn options.

    Returns:
        ``0`` when the client closes the connection.

    Raises:
        ShimError: On a protocol-major mismatch, before serving.
    """
    local_hub._require_posix("hub-shim")
    sessions: weakref.WeakSet = weakref.WeakSet()
    connection = HubConnection(config)
    connection.set_notifier(_send_tools_changed(sessions))
    server = build_server(connection, sessions)

    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await connection.start()
        try:
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(
                    tools_changed_notification_options()
                ),
            )
        finally:
            await connection.close()
    return 0


def run_stdio(config: ShimConfig) -> int:
    """Run the shim to completion in a fresh event loop."""
    return asyncio.run(run_shim(config))


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "HubConnection",
    "ShimConfig",
    "ShimError",
    "build_server",
    "endpoint_url",
    "open_hub_transport",
    "run_shim",
    "run_stdio",
    "tool_fingerprint",
]
