"""Host the core admin panel as the gateway's ``admin`` frontend.

The core :class:`toolregistry.admin.AdminServer` is a threaded HTTP server,
not an ASGI application.  The gateway therefore starts it on a loopback
ephemeral port and reverse-proxies the admin frontend to it.  Externally
there is still one process and one port: the panel is reachable only through
the gateway path (``/admin`` by default), and the internal listener never
leaves ``127.0.0.1``.

Authentication is the panel's own token/session mechanism.  The gateway
bearer tokens protect the tool frontends and are never accepted by the
panel: the admin frontend is not wrapped with the shared gateway auth, so a
valid serving token is just an unknown panel credential.

The panel loads the gateway extension (:class:`GatewayAdminExtension`) with
the process's live key store, frontend registry and source manager, so its
Keys, Frontends and Sources pages mutate the same objects the frontends
serve.  The extension contract is validated by ``AdminServer.start()``
before the listener opens; a violation propagates out of gateway startup.

Example:
    ```python
    from toolregistry_server import App

    gateway = App().create_gateway(
        config_path="tools.yaml",
        admin_token="operator-secret",
        keys_db="keys.db",
    )
    gateway.run(port=8000)
    ```
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shlex
import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, cast

from toolregistry.config import (
    AuthConfig,
    ConfigError,
    MCPSource,
    OpenAPISource,
    PythonSource,
    ToolSource,
)

from .._vendor.structlog import get_logger
from ..ext.gateway import GatewayAdminExtension

if TYPE_CHECKING:
    from toolregistry import ToolRegistry

    from ..source_manager import SourceManager
    from .app import GatewayAdapter

logger = get_logger()

#: Largest body the gateway proxy accepts in either direction.  The panel
#: renders tool schemas, so a few megabytes are expected; an unbounded read
#: would let a client exhaust memory.
MAX_PROXY_BYTES = 64 * 1024 * 1024

#: Seconds allowed for the panel to answer one request.
PROXY_TIMEOUT = 30.0

#: Request and response headers that describe one connection and must not be
#: forwarded by a proxy.
_HOP_BY_HOP = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
        b"content-length",
    }
)

_MCP_TRANSPORT_ALIASES = {"http": "streamable-http"}

#: Headers that state where a request came from.  The gateway is the edge,
#: so it replaces a client-supplied value with what the ASGI scope saw
#: instead of forwarding the client's claim.  RFC 7239 ``Forwarded`` is
#: stripped too, but not replaced: the panel does not read it.
_FORWARDED_HEADERS = frozenset(
    {b"forwarded", b"x-forwarded-for", b"x-forwarded-proto", b"x-forwarded-host"}
)


def _as_mapping(value: Any, name: str) -> dict[str, Any]:
    """Return *value* as a plain mapping.

    Args:
        value: Value to validate.
        name: Field name used in the error message.

    Returns:
        A shallow copy of the mapping.

    Raises:
        ConfigError: If *value* is not a mapping.
    """
    if not isinstance(value, Mapping):
        raise ConfigError(f"'{name}' must be a mapping")
    return dict(value)


def _as_bool(value: Any, name: str) -> bool:
    """Validate a boolean field.

    Args:
        value: Value to validate.
        name: Field name used in the error message.

    Returns:
        The boolean value.

    Raises:
        ConfigError: If *value* is not a boolean.
    """
    if not isinstance(value, bool):
        raise ConfigError(f"'{name}' must be a boolean")
    return value


def _as_str(value: Any, name: str) -> str:
    """Validate a non-empty string field.

    Args:
        value: Value to validate.
        name: Field name used in the error message.

    Returns:
        The string value.

    Raises:
        ConfigError: If *value* is not a non-empty string.
    """
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{name}' must be a non-empty string")
    return value.strip()


def _optional_str(value: Any, name: str) -> str | None:
    """Validate an optional string field.

    Args:
        value: Value to validate.
        name: Field name used in the error message.

    Returns:
        The string, or ``None`` when *value* is ``None``.

    Raises:
        ConfigError: If *value* is neither ``None`` nor a string.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"'{name}' must be a non-empty string or null")
    return value


def _patterns(value: Any, name: str) -> tuple[str, ...] | None:
    """Validate an optional include/exclude list.

    Args:
        value: Value to validate.
        name: Field name used in the error message.

    Returns:
        A tuple of strings, or ``None`` when *value* is ``None``.

    Raises:
        ConfigError: If *value* is not a list of strings.
    """
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ConfigError(f"'{name}' must be a list of non-empty strings")
    return tuple(value)


def _tags(value: Any) -> tuple[str, ...]:
    """Validate the optional ``tags`` list."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ConfigError("'tags' must be a list of non-empty strings")
    return tuple(value)


def _reject_unknown(payload: Mapping[str, Any], allowed: set[str], kind: str) -> None:
    """Reject declaration keys this parser does not understand.

    Args:
        payload: The decoded declaration.
        allowed: Keys the declaration kind accepts.
        kind: Source kind used in the error message.

    Raises:
        ConfigError: If an unknown key is present.
    """
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(
            f"unknown {kind} source field(s): {', '.join(map(str, unknown))}"
        )


def _auth_config(value: Any) -> AuthConfig | None:
    """Build an :class:`AuthConfig` from a declaration ``auth`` mapping.

    The config file can represent bearer and header authentication.  A basic
    credential is supported by the panel's preview but has no config-file
    representation, so it is refused here rather than silently dropped.
    """
    if value is None:
        return None
    data = _as_mapping(value, "auth")
    unknown = sorted(set(data) - {"type", "token", "token_env", "header_name"})
    if unknown:
        raise ConfigError(f"unknown auth field(s): {', '.join(map(str, unknown))}")
    auth_type = data.get("type", "bearer")
    if auth_type not in ("bearer", "header"):
        raise ConfigError(
            f"auth type {auth_type!r} cannot be stored in the configuration file; "
            "use 'bearer' or 'header'"
        )
    token = data.get("token")
    if token is not None and not isinstance(token, str):
        raise ConfigError("'auth.token' must be a string")
    token_env = data.get("token_env")
    if token_env is not None and not isinstance(token_env, str):
        raise ConfigError("'auth.token_env' must be a string")
    header_name = data.get("header_name", "Authorization")
    if not isinstance(header_name, str) or not header_name:
        raise ConfigError("'auth.header_name' must be a non-empty string")
    return AuthConfig(
        type=cast(Literal["bearer", "header"], auth_type),
        token=token,
        token_env=token_env,
        header_name=header_name,
    )


def _parse_python(payload: dict[str, Any]) -> PythonSource:
    """Build a :class:`PythonSource` declaration."""
    _reject_unknown(
        payload,
        {
            "type",
            "class",
            "object",
            "module",
            "namespace",
            "enabled",
            "kwargs",
            "tags",
        },
        "python",
    )
    module = _optional_str(payload.get("module"), "module")
    object_name = _optional_str(payload.get("object"), "object")
    class_path = _optional_str(payload.get("class"), "class")

    if module is not None and object_name is not None:
        if class_path is not None:
            raise ConfigError("use either 'class' or 'module' plus 'object'")
        class_path = f"{module}.{object_name}"
        module = None
    if class_path is not None and module is not None:
        raise ConfigError("use either 'class' or 'module', not both")
    if class_path is None and module is None:
        raise ConfigError("a python source requires 'class' or 'module'")

    return PythonSource(
        class_path=class_path,
        module_path=module,
        namespace=_optional_str(payload.get("namespace"), "namespace"),
        enabled=_as_bool(payload.get("enabled", True), "enabled"),
        kwargs=_as_mapping(payload.get("kwargs", {}), "kwargs"),
        tags=_tags(payload.get("tags")),
    )


def _parse_mcp(payload: dict[str, Any]) -> MCPSource:
    """Build an :class:`MCPSource` declaration."""
    _reject_unknown(
        payload,
        {
            "type",
            "transport",
            "url",
            "command",
            "env",
            "headers",
            "namespace",
            "enabled",
            "persistent",
            "allow_private_hosts",
            "tags",
            "include",
            "exclude",
        },
        "mcp",
    )
    transport = payload.get("transport")
    if transport is None:
        command = payload.get("command")
        transport = "stdio" if command else "streamable-http"
    if not isinstance(transport, str):
        raise ConfigError("'transport' must be a string")
    transport = _MCP_TRANSPORT_ALIASES.get(transport, transport)
    if transport not in ("stdio", "sse", "streamable-http"):
        raise ConfigError("'transport' must be 'stdio', 'sse' or 'streamable-http'")

    command_value = payload.get("command")
    if command_value is not None and not isinstance(command_value, str):
        if not isinstance(command_value, (list, tuple)) or not all(
            isinstance(item, str) and item for item in command_value
        ):
            raise ConfigError("'command' must be a string or a list of strings")
        command = tuple(command_value)
    elif command_value is not None:
        command = tuple(shlex.split(command_value))
    else:
        command = None

    url = _optional_str(payload.get("url"), "url")
    if transport == "stdio" and not command:
        raise ConfigError("an mcp source with transport 'stdio' requires 'command'")
    if transport != "stdio" and url is None:
        raise ConfigError(f"an mcp source with transport {transport!r} requires 'url'")

    include = _patterns(payload.get("include"), "include")
    exclude = _patterns(payload.get("exclude"), "exclude")
    if include is not None and exclude is not None:
        raise ConfigError("'include' and 'exclude' are mutually exclusive")

    return MCPSource(
        transport=cast(Literal["stdio", "sse", "streamable-http"], transport),
        namespace=_optional_str(payload.get("namespace"), "namespace"),
        enabled=_as_bool(payload.get("enabled", True), "enabled"),
        command=command,
        env=(
            {str(k): str(v) for k, v in _as_mapping(payload["env"], "env").items()}
            if payload.get("env") is not None
            else None
        ),
        url=url,
        headers=(
            {
                str(k): str(v)
                for k, v in _as_mapping(payload["headers"], "headers").items()
            }
            if payload.get("headers") is not None
            else None
        ),
        persistent=_as_bool(payload.get("persistent", True), "persistent"),
        allow_private_hosts=_as_bool(
            payload.get("allow_private_hosts", False), "allow_private_hosts"
        ),
        tags=_tags(payload.get("tags")),
        include=include,
        exclude=exclude,
    )


def _parse_openapi(payload: dict[str, Any]) -> OpenAPISource:
    """Build an :class:`OpenAPISource` declaration."""
    _reject_unknown(
        payload,
        {
            "type",
            "url",
            "spec_url",
            "namespace",
            "enabled",
            "auth",
            "base_url",
            "tags",
            "include",
            "exclude",
            "allow_private_hosts",
        },
        "openapi",
    )
    url = payload.get("url") or payload.get("spec_url")
    include = _patterns(payload.get("include"), "include")
    exclude = _patterns(payload.get("exclude"), "exclude")
    if include is not None and exclude is not None:
        raise ConfigError("'include' and 'exclude' are mutually exclusive")
    return OpenAPISource(
        url=_as_str(url, "url"),
        namespace=_optional_str(payload.get("namespace"), "namespace"),
        enabled=_as_bool(payload.get("enabled", True), "enabled"),
        auth=_auth_config(payload.get("auth")),
        base_url=_optional_str(payload.get("base_url"), "base_url"),
        tags=_tags(payload.get("tags")),
        include=include,
        exclude=exclude,
        allow_private_hosts=_as_bool(
            payload.get("allow_private_hosts", False), "allow_private_hosts"
        ),
    )


def parse_source_payload(payload: Mapping[str, Any]) -> ToolSource:
    """Turn an operator-supplied declaration into a core source object.

    Two payload shapes are accepted: the gateway extension's ``type`` form
    and the core panel's ``kind`` form.  The latter also uses ``spec_url``
    and ``object`` for the fields the config calls ``url`` and ``class``;
    both spellings are normalised here.

    The returned object is a :class:`toolregistry.config.ToolSource`
    (``OpenAPISource``, ``MCPSource`` or ``PythonSource``), the type
    :meth:`toolregistry_server.source_manager.SourceManager.add` accepts.

    Args:
        payload: Decoded JSON object from a panel request.

    Returns:
        The typed source declaration.

    Raises:
        ConfigError: If the payload is not a supported declaration.  The
            error is a ``ValueError``, which the extension reports as 400.
    """
    data = _as_mapping(payload, "source")
    kind = data.pop("type", data.pop("kind", None))
    if not isinstance(kind, str):
        raise ConfigError("'type' is required")
    if kind == "python":
        return _parse_python(data)
    if kind == "mcp":
        return _parse_mcp(data)
    if kind == "openapi":
        return _parse_openapi(data)
    raise ConfigError(f"unknown source type {kind!r}")


def _declaration_from_change(payload: Mapping[str, Any]) -> ToolSource:
    """Normalise a core panel source change into a declaration.

    The panel sends ``kind`` and, for OpenAPI, ``spec_url``; it also sends
    ``selected``, which describes the registration, not the declaration.
    """
    data = dict(payload)
    data.pop("selected", None)
    data.setdefault("type", data.pop("kind", None))
    return parse_source_payload(data)


class SourceManagerHook:
    """Adapt :class:`SourceManager` to the core panel's source hook.

    The core panel registers a source's tools itself and then hands the
    declaration to this hook for persistence.  The manager owns the live
    declaration list, so the hook adds, replaces or removes the declaration
    through it; the resulting status record is what the panel exposes under
    ``source_status``.

    A failed config write is reported as :class:`SourcePersistenceError`:
    the tools stay live, and the panel answers ``persisted: false`` rather
    than claiming a restart-safe change.

    Args:
        manager: The gateway's runtime source manager.
    """

    def __init__(self, manager: SourceManager) -> None:
        self._manager = manager

    def persist(self, change: Any) -> Mapping[str, Any]:
        """Apply *change* through the manager and return its status record.

        Args:
            change: A ``toolregistry.admin.source_hook.SourceChange``.

        Returns:
            The affected source's status record.

        Raises:
            SourcePersistenceError: If the declaration is invalid or the
                configuration file could not be written.
        """
        from toolregistry.admin.source_hook import SourcePersistenceError

        try:
            if change.action == "remove":
                if change.source_id is None:
                    raise SourcePersistenceError("a remove change requires a source id")
                result = self._manager.remove(change.source_id)
            else:
                source = _declaration_from_change(change.payload)
                if change.action == "replace":
                    if change.source_id is None:
                        raise SourcePersistenceError(
                            "a replace change requires a source id"
                        )
                    result = self._manager.replace(change.source_id, source)
                else:
                    result = self._manager.add(source)
        except (KeyError, ConfigError) as exc:
            raise SourcePersistenceError(str(exc)) from exc

        if not result.persisted:
            raise SourcePersistenceError(
                result.persist_error or "the configuration file was not written"
            )
        return result.entry.to_dict()

    def writable(self) -> bool:
        """Whether runtime source changes reach the configuration file."""
        return bool(self._manager.config_path) and self._manager.write_config

    def statuses(self) -> Sequence[Mapping[str, Any]]:
        """Return every source's status record."""
        return self._manager.to_dicts()


class GatewayAdminHost:
    """Start, stop and proxy the core admin panel for the gateway.

    Args:
        registry: The live tool registry the panel manages.
        frontends: The object that answers ``list_frontends`` and
            ``update_frontend``; the gateway adapter supplies itself.
        source_manager: Runtime source manager, or ``None``.
        admin_token: Operator token accepted by the panel.  Required.
        key_store: Inbound key store, or ``None`` for a 503 Keys page.
            The gateway passes the store its resolver already uses, so the
            panel and the tool frontends share one database.
        allow_private_hosts: Permit the panel to fetch a private-network
            source URL.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        frontends: GatewayAdapter,
        source_manager: SourceManager | None,
        admin_token: str,
        key_store: Any = None,
        allow_private_hosts: bool = False,
    ) -> None:
        self._registry = registry
        self._frontends = frontends
        self._source_manager = source_manager
        self._admin_token = admin_token
        self._key_store = key_store
        self._allow_private_hosts = allow_private_hosts
        self._server: Any = None
        self._port: int | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        """Whether the panel listener has been started."""
        return self._server is not None

    @property
    def port(self) -> int | None:
        """The loopback port the panel listens on, or ``None``."""
        return self._port

    def _build_server(self) -> Any:
        """Build a core :class:`AdminServer` with the gateway extension."""
        from toolregistry.admin.server import AdminServer

        # The installed core may predate the extension contract, so its type
        # stub does not list ``extensions`` or ``source_hook``.  Runtime
        # support is checked in ``ensure_started``; call through ``Any`` so
        # the older stub does not reject the newer keyword arguments.
        server_factory: Any = AdminServer

        extension = GatewayAdminExtension(
            key_store=self._key_store,
            frontends=self._frontends,
            sources=self._source_manager,
            parse_source=parse_source_payload,
        )
        hook = (
            SourceManagerHook(self._source_manager)
            if self._source_manager is not None
            else None
        )
        return server_factory(
            self._registry,
            host="127.0.0.1",
            port=0,
            serve_ui=True,
            auth_token=self._admin_token,
            extensions=[extension],
            source_hook=hook,
            # A loopback bind normally accepts only loopback Host headers.
            # The gateway forwards the public Host and requires an operator
            # token for every panel request, which is the documented
            # condition for disabling the allowlist.
            allowed_hosts=(),
            allow_private_hosts=self._allow_private_hosts,
        )

    def ensure_started(self) -> int:
        """Start the panel if it is not running.

        Returns:
            The loopback port the panel listens on.

        Raises:
            RuntimeError: If the core admin extension contract is missing or
                the panel fails to start.  A contract violation raised by
                ``AdminServer.start()`` propagates unchanged.
        """
        with self._lock:
            if self._port is not None:
                return self._port
            try:
                import toolregistry.admin.extensions  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "the admin frontend requires the core admin extension "
                    "contract (toolregistry 0.21.0 or newer)"
                ) from exc
            try:
                server = self._build_server()
                info = server.start()
            except BaseException:
                raise
            self._server = server
            self._port = info.port
            logger.info(f"Gateway admin panel listening on 127.0.0.1:{info.port}")
            return info.port

    def stop(self) -> None:
        """Stop the panel listener.

        The key store belongs to the gateway adapter, which closes it after
        the panel stops.
        """
        with self._lock:
            server = self._server
            self._server = None
            self._port = None
            if server is not None:
                server.stop()


class GatewayAdminProxyASGI:
    """Reverse-proxy the admin frontend to the loopback panel.

    Args:
        host: The panel host that owns the listener lifecycle.
    """

    def __init__(self, host: GatewayAdminHost) -> None:
        self._host = host

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Forward one HTTP request and stream the panel's response back."""
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1000})
            return

        try:
            port = await asyncio.to_thread(self._host.ensure_started)
        except Exception as exc:
            logger.error("Gateway admin panel is unavailable", exception=str(exc))
            await _json(send, 502, {"detail": f"admin panel unavailable: {exc}"})
            return

        body = await _read_body(receive)
        if body is None:
            await _json(send, 413, {"detail": "request body too large"})
            return

        try:
            status, headers, response_body = await _forward(port, scope, body)
        except Exception as exc:
            logger.error("Gateway admin proxy failed", exception=str(exc))
            await _json(send, 502, {"detail": f"admin panel proxy failed: {exc}"})
            return

        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": response_body})


async def _read_body(receive: Any) -> bytes | None:
    """Read a complete ASGI request body.

    Args:
        receive: ASGI receive callable.

    Returns:
        The body, or ``None`` when it exceeds :data:`MAX_PROXY_BYTES`.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > MAX_PROXY_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks)


def _request_target(scope: Any) -> bytes:
    """Build the origin-form request target for the panel."""
    raw_path = scope.get("raw_path")
    target = raw_path if raw_path else scope.get("path", "/").encode("latin-1")
    query = scope.get("query_string", b"")
    if query:
        target = target + b"?" + query
    return target


def _request_headers(scope: Any, body: bytes) -> list[tuple[bytes, bytes]]:
    """Return the headers to send to the panel.

    Any inbound ``Forwarded``, ``X-Forwarded-For``, ``X-Forwarded-Proto``
    or ``X-Forwarded-Host`` is dropped: only this proxy may say where the
    request came from, and what it says is what the ASGI scope saw.  The
    value is replaced, never appended to, so a client cannot choose its own
    place in the chain; RFC 7239 ``Forwarded`` is not replaced because the
    panel does not read it.  A scope without a client (for example a test
    harness) forwards no ``X-Forwarded-For`` at all rather than trusting
    what the client wrote.
    """
    incoming = scope.get("headers", [])
    headers = [
        (name, value)
        for name, value in incoming
        if name.lower() not in _HOP_BY_HOP and name.lower() not in _FORWARDED_HEADERS
    ]
    client = scope.get("client")
    if client and client[0]:
        headers.append((b"x-forwarded-for", str(client[0]).encode("latin-1")))
    scheme = scope.get("scheme")
    if scheme:
        headers.append((b"x-forwarded-proto", scheme.encode("latin-1")))
    host = _incoming_host(incoming)
    if host is not None:
        headers.append((b"x-forwarded-host", host))
    headers.append((b"connection", b"close"))
    if body:
        headers.append((b"content-length", str(len(body)).encode("latin-1")))
    return headers


def _incoming_host(headers: Sequence[tuple[bytes, bytes]]) -> bytes | None:
    """Return the first ``Host`` header value, or ``None``."""
    for name, value in headers:
        if name.lower() == b"host":
            return value
    return None


async def _forward(
    port: int, scope: Any, body: bytes
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Send one request to the panel and read its complete response.

    Args:
        port: Loopback port of the panel.
        scope: ASGI HTTP scope.
        body: Request body.

    Returns:
        ``(status, headers, body)`` ready for an ASGI response.

    Raises:
        TimeoutError: If the panel does not answer within the timeout.
        ValueError: If the panel response is malformed.
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", port), timeout=PROXY_TIMEOUT
    )
    try:
        head = (
            scope["method"].encode("latin-1")
            + b" "
            + _request_target(scope)
            + b" HTTP/1.1\r\n"
        )
        lines = b"".join(
            name + b": " + value + b"\r\n"
            for name, value in _request_headers(scope, body)
        )
        writer.write(head + lines + b"\r\n" + body)
        await asyncio.wait_for(writer.drain(), timeout=PROXY_TIMEOUT)

        status_line = await asyncio.wait_for(reader.readline(), timeout=PROXY_TIMEOUT)
        parts = status_line.split()
        if len(parts) < 2:
            raise ValueError(f"malformed status line: {status_line!r}")
        status = int(parts[1])

        raw_headers: list[tuple[bytes, bytes]] = []
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=PROXY_TIMEOUT)
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.partition(b":")
            raw_headers.append((name.strip().lower(), value.strip()))

        response_body = await _read_response_body(
            reader, raw_headers, scope.get("method", "GET")
        )
    finally:
        writer.close()
        with contextlib.suppress(Exception):  # pragma: no cover - best effort
            await writer.wait_closed()

    headers = [(name, value) for name, value in raw_headers if name not in _HOP_BY_HOP]
    # A 204/304 response carries no body, and HEAD answers only headers.
    if status in (204, 304) or scope.get("method", "GET").upper() == "HEAD":
        headers.append((b"content-length", b"0"))
        response_body = b""
    else:
        headers.append((b"content-length", str(len(response_body)).encode("latin-1")))
    return status, headers, response_body


def _header(raw_headers: Sequence[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    """Return the first header value with *name*."""
    for header, value in raw_headers:
        if header == name:
            return value
    return None


async def _read_response_body(
    reader: asyncio.StreamReader,
    raw_headers: Sequence[tuple[bytes, bytes]],
    method: str,
) -> bytes:
    """Read the response body according to its framing headers."""
    if method.upper() == "HEAD":
        return b""
    if _header(raw_headers, b"transfer-encoding") == b"chunked":
        return await _read_chunked(reader)
    length = _header(raw_headers, b"content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError as exc:
            raise ValueError(f"invalid Content-Length: {length!r}") from exc
        if size > MAX_PROXY_BYTES:
            raise ValueError("admin panel response is too large")
        return await asyncio.wait_for(reader.readexactly(size), timeout=PROXY_TIMEOUT)
    return await _read_to_eof(reader)


async def _read_chunked(reader: asyncio.StreamReader) -> bytes:
    """Decode a chunked response body."""
    chunks: list[bytes] = []
    total = 0
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=PROXY_TIMEOUT)
        size = int(line.split(b";", 1)[0].strip(), 16)
        if size == 0:
            # Consume the trailer section.
            while True:
                trailer = await asyncio.wait_for(
                    reader.readline(), timeout=PROXY_TIMEOUT
                )
                if trailer in (b"\r\n", b"\n", b""):
                    break
            break
        total += size
        if total > MAX_PROXY_BYTES:
            raise ValueError("admin panel response is too large")
        chunks.append(
            await asyncio.wait_for(reader.readexactly(size + 2), timeout=PROXY_TIMEOUT)
        )
    return b"".join(chunk[:-2] for chunk in chunks)


async def _read_to_eof(reader: asyncio.StreamReader) -> bytes:
    """Read a response body that has no explicit length."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await asyncio.wait_for(reader.read(65536), timeout=PROXY_TIMEOUT)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_PROXY_BYTES:
            raise ValueError("admin panel response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _json(send: Any, status: int, payload: Mapping[str, Any]) -> None:
    """Send a small JSON error response."""
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = [
    "GatewayAdminHost",
    "GatewayAdminProxyASGI",
    "MAX_PROXY_BYTES",
    "PROXY_TIMEOUT",
    "SourceManagerHook",
    "parse_source_payload",
]
