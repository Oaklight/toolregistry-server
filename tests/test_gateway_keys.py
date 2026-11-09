"""Scoped API keys on the gateway path (#200).

The gateway mounts the OpenAPI app, the MCP streamable-HTTP server and the
MCP SSE app in one process.  One ``--keys-db`` resolver must protect every
frontend and enforce the same four surfaces as the standalone commands:
MCP ``tools/list``, MCP ``tools/call``, the OpenAPI document and the OpenAPI
tool routes.  Static ``--tokens`` keep full, unscoped access.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request

import httpx
import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp._compat import (
    McpErrorClass,
    create_test_client,
    get_field,
)
from toolregistry_server.gateway import GatewayAdapter
from toolregistry_server.keystore import ApiKeyStore


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def echo(text: str) -> str:
    """Echo text."""
    return text


@pytest.fixture
def table() -> RouteTable:
    registry = ToolRegistry()
    registry.register(add, namespace="alpha")
    registry.register(echo, namespace="beta")
    return RouteTable(registry)


@pytest.fixture
def key_store(tmp_path):
    """Yield the store plus the database path the gateway should open."""
    database = ApiKeyStore(tmp_path / "keys.db")
    yield database, str(tmp_path / "keys.db")
    database.close()


def _gateway(table: RouteTable, keys_db: str, **kwargs) -> GatewayAdapter:
    kwargs.setdefault("tokens", [])
    return GatewayAdapter.create(table, keys_db=keys_db, **kwargs)


def _client(gateway: GatewayAdapter) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app),
        base_url="http://test",
    )


def _bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def _mcp_error_message(client, tool_name: str, arguments: dict) -> str:
    """Call a tool and return the refusal message on either SDK generation."""
    try:
        result = await client.call_tool(tool_name, arguments)
    except McpErrorClass as exc:
        return str(exc)
    assert get_field(result, "is_error", "isError") is True
    return result.content[0].text


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """``App.run`` installs signal handlers, which only work in the main thread."""
    loop_classes = [asyncio.BaseEventLoop]
    unix = getattr(asyncio, "unix_events", None)
    if unix is not None:
        loop_classes.append(unix._UnixSelectorEventLoop)  # noqa: SLF001 - test shim
    for loop_class in loop_classes:
        monkeypatch.setattr(loop_class, "add_signal_handler", lambda *a, **k: None)


def _serve(gateway: GatewayAdapter) -> str:
    thread = threading.Thread(
        target=gateway.run, kwargs={"host": "127.0.0.1", "port": 0}
    )
    thread.daemon = True
    thread.start()
    for _ in range(100):
        if getattr(gateway._openapi_app, "port", None):  # noqa: SLF001 - test shim
            return f"http://127.0.0.1:{gateway._openapi_app.port}"
        time.sleep(0.05)
    raise RuntimeError("gateway did not start")


def _request(
    url: str,
    path: str,
    payload: object = None,
    headers: dict | None = None,
) -> tuple[int, dict, bytes]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url + path,
        data=data,
        headers=headers or {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


# ---------------------------------------------------------------------------
# Shared gateway authentication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/openapi/tools", "/mcp", "/sse"])
async def test_unknown_key_is_rejected_on_every_frontend(table, key_store, path):
    """One resolver protects all three frontends."""
    _, keys_db = key_store
    gateway = _gateway(table, keys_db)

    async with _client(gateway) as client:
        missing = await client.get(path)
        invalid = await client.get(path, headers=_bearer("trk_unknown"))

    assert missing.status_code == 401
    assert invalid.status_code == 401


@pytest.mark.asyncio
async def test_scoped_key_passes_the_shared_auth_on_mcp_and_sse(table, key_store):
    """A store key, not only a static token, satisfies the gateway wrapper."""
    store, keys_db = key_store
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    gateway = _gateway(table, keys_db)
    headers = _bearer(created.secret)

    async with _client(gateway) as client:
        mcp = await client.get("/mcp", headers=headers)
        # A POST cannot open the SSE stream, but it must get past auth.
        sse = await client.post("/sse", headers=headers)

    assert mcp.status_code != 401
    assert sse.status_code != 401


@pytest.mark.asyncio
async def test_revoked_key_fails_on_the_next_request(table, key_store):
    """The resolver reads the row per request, so revocation is immediate."""
    store, keys_db = key_store
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    gateway = _gateway(table, keys_db)
    headers = _bearer(created.secret)

    async with _client(gateway) as client:
        first = await client.get("/openapi/tools", headers=headers)
        store.revoke(created.record.id)
        second = await client.get("/openapi/tools", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_metadata_is_advertised_when_only_keys_are_configured(table, key_store):
    """The RFC 9728 route follows the resolver, not only the static tokens."""
    _, keys_db = key_store
    gateway = _gateway(table, keys_db, host="127.0.0.1", port=9000)

    async with _client(gateway) as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json()["resource"] == "http://127.0.0.1:9000/mcp"


# ---------------------------------------------------------------------------
# Surface 1: MCP tools/list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_list_tools_is_scoped_on_the_gateway_server(
    table, key_store, monkeypatch
):
    store, keys_db = key_store
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    gateway = _gateway(table, keys_db)
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )

    async with create_test_client(gateway._mcp_server) as client:  # noqa: SLF001
        result = await client.list_tools()

    assert {tool.name for tool in result.tools} == {"alpha-add"}


# ---------------------------------------------------------------------------
# Surface 2: MCP tools/call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_call_tool_is_scoped_on_the_gateway_server(
    table, key_store, monkeypatch
):
    store, keys_db = key_store
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    gateway = _gateway(table, keys_db)
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )

    async with create_test_client(gateway._mcp_server) as client:  # noqa: SLF001
        allowed = await client.call_tool("alpha-add", {"a": 1, "b": 2})
        denied = await _mcp_error_message(client, "beta-echo", {"text": "hello"})

    assert allowed.content[0].text == "3"
    assert "not permitted" in denied


@pytest.mark.asyncio
async def test_mcp_operator_token_keeps_full_access(table, key_store, monkeypatch):
    _, keys_db = key_store
    gateway = _gateway(table, keys_db, tokens=["operator-token"])
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: "operator-token",
    )

    async with create_test_client(gateway._mcp_server) as client:  # noqa: SLF001
        result = await client.list_tools()

    assert {tool.name for tool in result.tools} == {"alpha-add", "beta-echo"}


# ---------------------------------------------------------------------------
# Surface 3: the OpenAPI document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_document_is_scoped(table, key_store):
    store, keys_db = key_store
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    gateway = _gateway(table, keys_db)

    async with _client(gateway) as client:
        response = await client.get(
            "/openapi/openapi.json", headers=_bearer(created.secret)
        )

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/tools/alpha/add" in paths
    assert "/tools/beta/echo" not in paths


@pytest.mark.asyncio
async def test_static_token_keeps_full_access_in_the_gateway_document(table, key_store):
    _, keys_db = key_store
    gateway = _gateway(table, keys_db, tokens=["operator-token"])

    async with _client(gateway) as client:
        response = await client.get(
            "/openapi/openapi.json", headers=_bearer("operator-token")
        )

    paths = response.json()["paths"]
    assert {"/tools/alpha/add", "/tools/beta/echo"} <= set(paths)


# ---------------------------------------------------------------------------
# Surface 4: the OpenAPI tool routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_route_outside_the_scope_is_forbidden(table, key_store):
    store, keys_db = key_store
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    gateway = _gateway(table, keys_db)
    headers = _bearer(created.secret)

    async with _client(gateway) as client:
        allowed = await client.post(
            "/openapi/tools/alpha/add", json={"a": 1, "b": 2}, headers=headers
        )
        denied = await client.post(
            "/openapi/tools/beta/echo", json={"text": "hello"}, headers=headers
        )

    assert allowed.status_code == 200
    assert allowed.json() == 3
    assert denied.status_code == 403
    assert "not permitted" in denied.json()["detail"]


# ---------------------------------------------------------------------------
# The httpserver backend on the gateway path
# ---------------------------------------------------------------------------


def test_httpserver_gateway_is_scoped(table, key_store):
    """The vendored backend reaches the same resolver through the gateway."""
    store, keys_db = key_store
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    gateway = _gateway(
        table, keys_db, backend="httpserver", mcp_enabled=False, sse_enabled=False
    )
    url = _serve(gateway)
    try:
        status, _, body = _request(
            url, "/openapi.json", headers=_bearer(created.secret)
        )
        denied_status, _, denied_body = _request(
            url, "/tools/beta/echo", {"text": "hello"}, _bearer(created.secret)
        )
    finally:
        gateway._openapi_app.shutdown()  # noqa: SLF001 - test shim

    paths = json.loads(body)["paths"]
    assert status == 200
    assert "/tools/alpha/add" in paths
    assert "/tools/beta/echo" not in paths
    assert denied_status == 403
    assert "not permitted" in json.loads(denied_body)["detail"]
