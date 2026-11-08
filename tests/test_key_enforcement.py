"""Scope enforcement on the four served surfaces (#186).

Each surface is exercised independently:

* MCP ``tools/list``
* MCP ``tools/call``
* the OpenAPI document
* the OpenAPI tool routes

Both OpenAPI backends are covered: the FastAPI app and the vendored
``httpserver`` app.  Static ``--tokens`` credentials are checked to keep
their historical unscoped behaviour.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp import route_table_to_mcp_server
from toolregistry_server.adapters.mcp._compat import (
    McpErrorClass,
    create_test_client,
    get_field,
)
from toolregistry_server.adapters.openapi import OpenAPIAdapter
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app
from toolregistry_server.keystore import ApiKeyStore, PrincipalResolver, ScopeSpec

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


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
def store(tmp_path):
    database = ApiKeyStore(tmp_path / "keys.db")
    yield database
    database.close()


@pytest.fixture
def resolver(store):
    return PrincipalResolver(store, operator_tokens=["operator-token"])


def _bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


async def _mcp_error_message(client, tool_name: str, arguments: dict) -> str:
    """Call a tool and return the refusal message on either SDK generation.

    SDK v1 turns the protocol error into an ``isError`` result; SDK v2
    re-raises ``INVALID_PARAMS``.  Both are the same decision.
    """
    try:
        result = await client.call_tool(tool_name, arguments)
    except McpErrorClass as exc:
        return str(exc)
    assert get_field(result, "is_error", "isError") is True
    return result.content[0].text


# ---------------------------------------------------------------------------
# MCP tools/list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_list_tools_shows_only_the_keys_namespace(
    table, store, resolver, monkeypatch
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}

    assert names == {"alpha-add"}


@pytest.mark.asyncio
async def test_mcp_list_tools_is_empty_for_a_revoked_key(
    table, store, resolver, monkeypatch
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    store.revoke(created.record.id)
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        result = await client.list_tools()

    assert list(result.tools) == []


@pytest.mark.asyncio
async def test_mcp_list_tools_is_unchanged_without_a_resolver(table, monkeypatch):
    """``--keys-db`` absent means today's behaviour, byte for byte."""
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: "any-token",
    )
    server = route_table_to_mcp_server(table)

    async with create_test_client(server) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}

    assert names == {"alpha-add", "beta-echo"}


# ---------------------------------------------------------------------------
# MCP tools/call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_call_tool_rejects_a_tool_outside_the_scope(
    table, store, resolver, monkeypatch
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        message = await _mcp_error_message(client, "beta-echo", {"text": "hello"})

    assert "not permitted" in message


@pytest.mark.asyncio
async def test_mcp_call_tool_allows_a_tool_inside_the_scope(
    table, store, resolver, monkeypatch
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        result = await client.call_tool("alpha-add", {"a": 1, "b": 2})

    assert result.content[0].text == "3"


@pytest.mark.asyncio
async def test_mcp_call_tool_rejects_a_revoked_key(table, store, resolver, monkeypatch):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    store.revoke(created.record.id)
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        message = await _mcp_error_message(client, "alpha-add", {"a": 1, "b": 2})

    assert "not permitted" in message


@pytest.mark.asyncio
async def test_mcp_call_tool_rejects_an_expired_key(tmp_path, table, monkeypatch):
    clock = _Clock()
    database = ApiKeyStore(tmp_path / "expiring.db", clock=clock)
    try:
        created = database.create(
            "short",
            {"namespaces": ["alpha"]},
            expires_at=clock.now + timedelta(seconds=5),
        )
        resolver = PrincipalResolver(database)
        clock.advance(6)
        monkeypatch.setattr(
            "toolregistry_server.adapters.mcp.adapter._request_authorization",
            lambda: created.secret,
        )
        server = route_table_to_mcp_server(table, principal_resolver=resolver)

        async with create_test_client(server) as client:
            message = await _mcp_error_message(client, "alpha-add", {"a": 1, "b": 2})
    finally:
        database.close()

    assert "not permitted" in message


@pytest.mark.asyncio
async def test_mcp_static_token_keeps_full_access(table, store, resolver, monkeypatch):
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: "operator-token",
    )
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        result = await client.list_tools()
        call = await client.call_tool("beta-echo", {"text": "hello"})

    assert {tool.name for tool in result.tools} == {"alpha-add", "beta-echo"}
    assert call.content[0].text == "hello"


# ---------------------------------------------------------------------------
# MCP transport verifier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_token_verifier_tracks_the_store(store, resolver):
    from toolregistry_server.adapters.mcp.token_verifier import ScopedTokenVerifier

    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    verifier = ScopedTokenVerifier(resolver)

    accepted = await verifier.verify_token(created.secret)
    assert accepted is not None
    assert "mcp" in accepted.scopes
    assert accepted.client_id == created.record.id
    assert await verifier.verify_token("trk_unknown") is None

    store.revoke(created.record.id)

    assert await verifier.verify_token(created.secret) is None


@pytest.mark.asyncio
async def test_scoped_token_verifier_accepts_static_operator_tokens(resolver):
    from toolregistry_server.adapters.mcp.token_verifier import ScopedTokenVerifier

    accepted = await ScopedTokenVerifier(resolver).verify_token("operator-token")

    assert accepted is not None
    assert accepted.client_id == "operator"


# ---------------------------------------------------------------------------
# OpenAPI (FastAPI backend)
# ---------------------------------------------------------------------------


def _fastapi_client(table, resolver) -> TestClient:
    adapter = OpenAPIAdapter(table, principal_resolver=resolver)
    return TestClient(adapter.app)


def test_openapi_spec_lists_only_scoped_paths(table, store, resolver):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    client = _fastapi_client(table, resolver)

    response = client.get("/openapi.json", headers=_bearer(created.secret))

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/tools/alpha/add" in paths
    assert "/tools/beta/echo" not in paths


def test_openapi_route_rejects_a_tool_outside_the_scope(table, store, resolver):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    client = _fastapi_client(table, resolver)
    headers = _bearer(created.secret)

    allowed = client.post("/tools/alpha/add", json={"a": 1, "b": 2}, headers=headers)
    denied = client.post("/tools/beta/echo", json={"text": "hello"}, headers=headers)

    assert allowed.status_code == 200
    assert allowed.json() == 3
    assert denied.status_code == 403
    assert "not permitted" in denied.json()["detail"]


def test_openapi_rejects_a_revoked_key_on_the_next_request(table, store, resolver):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    client = _fastapi_client(table, resolver)
    headers = _bearer(created.secret)
    assert client.get("/openapi.json", headers=headers).status_code == 200

    store.revoke(created.record.id)

    assert client.get("/openapi.json", headers=headers).status_code == 401
    assert (
        client.post(
            "/tools/alpha/add", json={"a": 1, "b": 2}, headers=headers
        ).status_code
        == 401
    )


def test_openapi_rejects_an_unknown_token(table, resolver):
    client = _fastapi_client(table, resolver)

    response = client.get("/openapi.json", headers=_bearer("trk_unknown"))

    assert response.status_code == 401


def test_openapi_static_token_keeps_full_access(table, store, resolver):
    client = _fastapi_client(table, resolver)

    response = client.get("/openapi.json", headers=_bearer("operator-token"))

    paths = response.json()["paths"]
    assert "/tools/alpha/add" in paths
    assert "/tools/beta/echo" in paths


def test_openapi_without_a_resolver_keeps_todays_behaviour(table):
    """No ``--keys-db`` means no scoping and no key authentication."""
    adapter = OpenAPIAdapter(table)
    client = TestClient(adapter.app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/tools/alpha/add" in paths
    assert "/tools/beta/echo" in paths


# ---------------------------------------------------------------------------
# OpenAPI (httpserver backend)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """``App.run`` installs signal handlers, which only work in the main thread."""
    loop_classes = [asyncio.BaseEventLoop]
    unix = getattr(asyncio, "unix_events", None)
    if unix is not None:
        loop_classes.append(unix._UnixSelectorEventLoop)  # noqa: SLF001 - test shim
    for loop_class in loop_classes:
        monkeypatch.setattr(loop_class, "add_signal_handler", lambda *a, **k: None)


def _serve(app) -> str:
    thread = threading.Thread(target=app.run, kwargs={"host": "127.0.0.1", "port": 0})
    thread.daemon = True
    thread.start()
    for _ in range(100):
        if getattr(app, "port", None):
            return f"http://127.0.0.1:{app.port}"
        time.sleep(0.05)
    raise RuntimeError("server did not start")


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


@pytest.fixture
def httpserver(table, resolver):
    app = create_httpserver_app(table, principal_resolver=resolver)
    url = _serve(app)
    yield url, app
    app.shutdown()


def test_httpserver_spec_lists_only_scoped_paths(httpserver, store):
    url, _ = httpserver
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})

    status, _, body = _request(url, "/openapi.json", headers=_bearer(created.secret))

    assert status == 200
    paths = json.loads(body)["paths"]
    assert "/tools/alpha/add" in paths
    assert "/tools/beta/echo" not in paths


def test_httpserver_route_rejects_a_tool_outside_the_scope(httpserver, store):
    url, _ = httpserver
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    headers = _bearer(created.secret)

    allowed_status, _, allowed_body = _request(
        url, "/tools/alpha/add", {"a": 1, "b": 2}, headers
    )
    denied_status, _, denied_body = _request(
        url, "/tools/beta/echo", {"text": "hello"}, headers
    )

    assert allowed_status == 200
    assert json.loads(allowed_body) == 3
    assert denied_status == 403
    assert "not permitted" in json.loads(denied_body)["detail"]


def test_httpserver_rejects_a_revoked_key(httpserver, store):
    url, _ = httpserver
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    headers = _bearer(created.secret)
    assert _request(url, "/openapi.json", headers=headers)[0] == 200

    store.revoke(created.record.id)

    assert _request(url, "/openapi.json", headers=headers)[0] == 401


def test_httpserver_static_token_keeps_full_access(httpserver):
    url, _ = httpserver

    status, _, body = _request(url, "/openapi.json", headers=_bearer("operator-token"))

    assert status == 200
    paths = json.loads(body)["paths"]
    assert "/tools/alpha/add" in paths
    assert "/tools/beta/echo" in paths


# ---------------------------------------------------------------------------
# Scope override combinations
# ---------------------------------------------------------------------------


def test_denied_tool_inside_an_allowed_namespace(table, store, resolver):
    created = store.create(
        "alpha-bot",
        ScopeSpec(("alpha",), {"alpha-add": False}),
    )
    client = _fastapi_client(table, resolver)
    headers = _bearer(created.secret)

    spec = client.get("/openapi.json", headers=headers).json()

    assert "/tools/alpha/add" not in spec["paths"]
    assert (
        client.post(
            "/tools/alpha/add", json={"a": 1, "b": 2}, headers=headers
        ).status_code
        == 403
    )


def test_allowed_tool_outside_the_namespace_list(table, store, resolver):
    created = store.create(
        "mixed-bot",
        ScopeSpec(("alpha",), {"beta-echo": True}),
    )
    client = _fastapi_client(table, resolver)
    headers = _bearer(created.secret)

    spec = client.get("/openapi.json", headers=headers).json()

    assert "/tools/alpha/add" in spec["paths"]
    assert "/tools/beta/echo" in spec["paths"]
    assert (
        client.post(
            "/tools/beta/echo", json={"text": "hello"}, headers=headers
        ).status_code
        == 200
    )
