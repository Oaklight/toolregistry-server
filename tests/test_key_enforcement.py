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
from typing import Any

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
def discovery_table() -> RouteTable:
    """A table that also serves the discovery/call_deferred proxy pair."""
    registry = ToolRegistry()
    registry.register(add, namespace="alpha")
    registry.register(echo, namespace="beta")
    registry.enable_tool_discovery()
    return RouteTable(registry)


@pytest.fixture
def scoped_discovery_table() -> RouteTable:
    """Discovery enabled, a deferred tool in ``alpha`` and one in ``beta``."""

    from toolregistry.tool import Tool, ToolMetadata

    registry = ToolRegistry()
    registry.register(add, namespace="alpha")
    registry.register(echo, namespace="beta")

    def search(query: str, limit: int = 10) -> str:
        """Search the alpha corpus."""
        return f"alpha results for '{query}' (limit={limit})"

    tool = Tool.from_function(
        search,
        namespace="alpha",
        metadata=ToolMetadata(defer=True),
    )
    registry.register(tool)
    registry.enable_tool_discovery()
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


@pytest.mark.asyncio
async def test_mcp_scoped_key_keeps_the_proxy_tools(
    scoped_discovery_table, store, resolver, monkeypatch
):
    """The proxies judge the tool they act on, so a scoped key keeps them."""
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(
        scoped_discovery_table, principal_resolver=resolver
    )

    async with create_test_client(server) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}

    assert {"discover_tools", "call_deferred"} <= names
    assert "alpha-add" in names
    assert "beta-echo" not in names


@pytest.mark.asyncio
async def test_mcp_explicit_deny_removes_the_proxy_tools(
    scoped_discovery_table, store, resolver, monkeypatch
):
    """A per-tool deny is a deliberate decision and beats transparency."""
    created = store.create(
        "alpha-denied",
        ScopeSpec(("alpha",), {"discover_tools": False, "call_deferred": False}),
    )
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(
        scoped_discovery_table, principal_resolver=resolver
    )

    async with create_test_client(server) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}
        message = await _mcp_error_message(
            client,
            "call_deferred",
            {"_target_tool": "alpha-search", "query": "hello"},
        )

    assert "discover_tools" not in names
    assert "call_deferred" not in names
    assert "not permitted" in message


@pytest.mark.asyncio
async def test_mcp_explicit_allow_keeps_the_proxy_tools(
    scoped_discovery_table, store, resolver, monkeypatch
):
    """An explicit allow is honoured as well, even outside the namespace list."""
    created = store.create(
        "alpha-explicit",
        ScopeSpec((), {"discover_tools": True, "call_deferred": True}),
    )
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(
        scoped_discovery_table, principal_resolver=resolver
    )

    async with create_test_client(server) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}

    assert {"discover_tools", "call_deferred"} <= names
    assert "alpha-add" not in names


@pytest.mark.asyncio
async def test_mcp_discovery_returns_only_tools_in_scope(
    scoped_discovery_table, store, resolver, monkeypatch
):
    """A discovered tool outside the key's scope is not returned."""
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(
        scoped_discovery_table, principal_resolver=resolver
    )

    async with create_test_client(server) as client:
        own = await client.call_tool("discover_tools", {"query": "add"})
        other = await client.call_tool("discover_tools", {"query": "echo"})

    own_hits = json.loads(own.content[0].text)
    other_hits = json.loads(other.content[0].text)
    assert [hit["name"] for hit in own_hits] == ["alpha-add"]
    assert other_hits == []


@pytest.mark.asyncio
async def test_mcp_call_deferred_loads_a_tool_in_scope(
    scoped_discovery_table, store, resolver, monkeypatch
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(
        scoped_discovery_table, principal_resolver=resolver
    )

    async with create_test_client(server) as client:
        result = await client.call_tool(
            "call_deferred",
            {"_target_tool": "alpha-search", "query": "hello"},
        )

    assert get_field(result, "is_error", "isError") is False
    assert "alpha results for 'hello'" in result.content[0].text


@pytest.mark.asyncio
async def test_mcp_call_deferred_refuses_a_target_outside_the_scope(
    scoped_discovery_table, store, resolver, monkeypatch
):
    """The refusal is indistinguishable from a direct out-of-scope call."""
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(
        scoped_discovery_table, principal_resolver=resolver
    )

    async with create_test_client(server) as client:
        proxied = await _mcp_error_message(
            client,
            "call_deferred",
            {"_target_tool": "beta-echo", "text": "hello"},
        )
        direct = await _mcp_error_message(client, "beta-echo", {"text": "hello"})

    assert "not permitted" in proxied
    assert proxied == direct


def _deferred_tool(namespace: str, name: str, ran: list[str]) -> Any:
    """Build a deferred tool whose invocation records that it ran."""
    from toolregistry.tool import Tool, ToolMetadata

    def body() -> str:
        """Run the tool."""
        ran.append(name)
        return f"ran {name}"

    return Tool.from_function(
        body,
        name=name,
        namespace=namespace,
        metadata=ToolMetadata(defer=True),
    )


@pytest.mark.asyncio
async def test_mcp_call_deferred_refuses_a_target_added_while_routes_lag(
    discovery_table, store, resolver, monkeypatch
):
    """A tool registered inside batch_changes is judged by the registry.

    ``batch_changes`` defers the route rebuild, so the route table does not
    know the new tool while core's ``call_deferred`` will resolve it in the
    live registry.  Resolving the target against the registry closes that
    window.
    """
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(discovery_table, principal_resolver=resolver)
    registry = discovery_table.registry
    ran: list[str] = []
    tool = _deferred_tool("admin", "admin-only", ran)

    async with create_test_client(server) as client:
        with discovery_table.batch_changes():
            registry.register(tool)
            # The window: the registry has it, the serving view does not.
            assert discovery_table.get_route(tool.name) is None
            message = await _mcp_error_message(
                client, "call_deferred", {"_target_tool": tool.name}
            )

    assert "not permitted" in message
    assert ran == []


@pytest.mark.asyncio
async def test_mcp_call_deferred_loads_a_target_added_while_routes_lag(
    discovery_table, store, resolver, monkeypatch
):
    """The same window, with the target inside the key's scope."""
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(discovery_table, principal_resolver=resolver)
    registry = discovery_table.registry
    ran: list[str] = []
    tool = _deferred_tool("alpha", "alpha-fresh", ran)

    async with create_test_client(server) as client:
        with discovery_table.batch_changes():
            registry.register(tool)
            assert discovery_table.get_route(tool.name) is None
            result = await client.call_tool(
                "call_deferred", {"_target_tool": tool.name}
            )

    assert get_field(result, "is_error", "isError") is False
    assert ran == ["alpha-fresh"]


@pytest.mark.asyncio
async def test_mcp_call_deferred_refuses_a_rewritten_target_name(
    scoped_discovery_table, store, resolver, monkeypatch
):
    """A name core would normalise must not slip past the scope check."""
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(
        scoped_discovery_table, principal_resolver=resolver
    )

    async with create_test_client(server) as client:
        message = await _mcp_error_message(
            client,
            "call_deferred",
            {"_target_tool": "beta_echo", "text": "hello"},
        )

    assert "not permitted" in message
    assert "beta-echo" in message


@pytest.mark.asyncio
async def test_mcp_call_deferred_keeps_the_not_found_shape(
    scoped_discovery_table, store, resolver, monkeypatch
):
    """An unknown target is not refused by the scope check."""
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    server = route_table_to_mcp_server(
        scoped_discovery_table, principal_resolver=resolver
    )

    async with create_test_client(server) as client:
        message = await _mcp_error_message(
            client, "call_deferred", {"_target_tool": "missing-tool"}
        )

    assert "not permitted" not in message
    assert "execution failed" in message


@pytest.mark.asyncio
async def test_mcp_operator_keeps_the_proxy_tools(
    discovery_table, store, resolver, monkeypatch
):
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: "operator-token",
    )
    server = route_table_to_mcp_server(discovery_table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}

    assert {"discover_tools", "call_deferred"} <= names


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
    from fastapi import Depends

    from toolregistry_server.auth import create_scoped_bearer_dependency
    from toolregistry_server.gateway.openapi_asgi import create_openapi_app

    app = create_openapi_app(
        table,
        dependencies=[Depends(create_scoped_bearer_dependency(resolver))],
        principal_resolver=resolver,
    )
    return TestClient(app)


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
    usage = store.usage(created.record.id)
    assert usage.calls == 2
    assert usage.ok == 1
    assert usage.denied == 1


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


def _operation_ids(document: dict) -> set[str]:
    return {
        operation["operationId"]
        for path in document["paths"].values()
        for operation in path.values()
        if "operationId" in operation
    }


def _proxy_paths(table: RouteTable) -> tuple[str, str]:
    discover = table.get_route("discover_tools")
    deferred = table.get_route("call_deferred")
    assert discover is not None and deferred is not None
    return discover.path, deferred.path


def test_openapi_scoped_key_keeps_the_proxy_tools(
    scoped_discovery_table, store, resolver
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    client = _fastapi_client(scoped_discovery_table, resolver)
    headers = _bearer(created.secret)

    document = client.get("/openapi.json", headers=headers).json()
    operations = _operation_ids(document)

    assert {"discover_tools", "call_deferred"} <= operations
    assert "alpha-add" in operations
    assert "beta-echo" not in operations


def test_openapi_discovery_returns_only_tools_in_scope(
    scoped_discovery_table, store, resolver
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    client = _fastapi_client(scoped_discovery_table, resolver)
    headers = _bearer(created.secret)
    discover_path, _ = _proxy_paths(scoped_discovery_table)

    own = client.post(discover_path, json={"query": "add"}, headers=headers)
    other = client.post(discover_path, json={"query": "echo"}, headers=headers)

    assert [hit["name"] for hit in own.json()] == ["alpha-add"]
    assert other.json() == []


def test_openapi_call_deferred_loads_a_tool_in_scope(
    scoped_discovery_table, store, resolver
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    client = _fastapi_client(scoped_discovery_table, resolver)
    headers = _bearer(created.secret)
    _, deferred_path = _proxy_paths(scoped_discovery_table)

    result = client.post(
        deferred_path,
        json={"_target_tool": "alpha-search", "query": "hello"},
        headers=headers,
    )

    assert result.status_code == 200
    assert "alpha results for 'hello'" in result.text


def test_openapi_call_deferred_refuses_a_target_outside_the_scope(
    scoped_discovery_table, store, resolver
):
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    client = _fastapi_client(scoped_discovery_table, resolver)
    headers = _bearer(created.secret)
    _, deferred_path = _proxy_paths(scoped_discovery_table)
    target = scoped_discovery_table.get_route("beta-echo")
    assert target is not None

    proxied = client.post(
        deferred_path,
        json={"_target_tool": "beta-echo", "text": "hello"},
        headers=headers,
    )
    direct = client.post(target.path, json={"text": "hello"}, headers=headers)

    assert proxied.status_code == 403
    assert direct.status_code == 403
    assert proxied.json()["detail"] == direct.json()["detail"]


def test_openapi_operator_keeps_the_proxy_tools(discovery_table, resolver):
    client = _fastapi_client(discovery_table, resolver)

    document = client.get("/openapi.json", headers=_bearer("operator-token")).json()

    assert {"discover_tools", "call_deferred"} <= _operation_ids(document)


def test_openapi_without_a_resolver_keeps_todays_behaviour(table):
    """No ``--keys-db`` means no scoping and no key authentication."""
    from toolregistry_server.gateway.openapi_asgi import create_openapi_app

    client = TestClient(create_openapi_app(table))

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
    usage = store.usage(created.record.id)
    assert usage.calls == 2
    assert usage.ok == 1
    assert usage.denied == 1


def test_httpserver_rejects_a_revoked_key(httpserver, store):
    url, _ = httpserver
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    headers = _bearer(created.secret)
    assert _request(url, "/openapi.json", headers=headers)[0] == 200

    store.revoke(created.record.id)

    assert _request(url, "/openapi.json", headers=headers)[0] == 401


@pytest.fixture
def scoped_httpserver(scoped_discovery_table, resolver):
    app = create_httpserver_app(scoped_discovery_table, principal_resolver=resolver)
    url = _serve(app)
    yield url, app
    app.shutdown()


def test_httpserver_discovery_returns_only_tools_in_scope(
    scoped_httpserver, scoped_discovery_table, store
):
    url, _ = scoped_httpserver
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    headers = _bearer(created.secret)
    discover_path, _ = _proxy_paths(scoped_discovery_table)

    own_status, _, own_body = _request(url, discover_path, {"query": "add"}, headers)
    other_status, _, other_body = _request(
        url, discover_path, {"query": "echo"}, headers
    )

    assert own_status == 200
    assert [hit["name"] for hit in json.loads(own_body)] == ["alpha-add"]
    assert other_status == 200
    assert json.loads(other_body) == []


def test_httpserver_call_deferred_refuses_a_target_outside_the_scope(
    scoped_httpserver, scoped_discovery_table, store
):
    url, _ = scoped_httpserver
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    headers = _bearer(created.secret)
    _, deferred_path = _proxy_paths(scoped_discovery_table)
    target = scoped_discovery_table.get_route("beta-echo")
    assert target is not None

    proxied_status, _, proxied_body = _request(
        url,
        deferred_path,
        {"_target_tool": "beta-echo", "text": "hello"},
        headers,
    )
    direct_status, _, direct_body = _request(
        url, target.path, {"text": "hello"}, headers
    )

    assert proxied_status == 403
    assert direct_status == 403
    assert json.loads(proxied_body)["detail"] == json.loads(direct_body)["detail"]


def test_httpserver_call_deferred_loads_a_tool_in_scope(
    scoped_httpserver, scoped_discovery_table, store
):
    url, _ = scoped_httpserver
    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    headers = _bearer(created.secret)
    _, deferred_path = _proxy_paths(scoped_discovery_table)

    status, _, body = _request(
        url,
        deferred_path,
        {"_target_tool": "alpha-search", "query": "hello"},
        headers,
    )

    assert status == 200
    assert "alpha results for 'hello'" in body.decode("utf-8")


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


# ---------------------------------------------------------------------------
# Per-client isolation (#145): operator tokens, quota and the process policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_refuses_an_operator_token_when_keys_are_configured(
    table, store, resolver, monkeypatch
):
    """A key-scoped MCP endpoint accepts client keys only."""
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: "operator-token",
    )
    server = route_table_to_mcp_server(
        table, principal_resolver=resolver, refuse_operator_token=True
    )

    async with create_test_client(server) as client:
        try:
            listed = await client.list_tools()
        except McpErrorClass as exc:
            assert "operator tokens are not accepted" in str(exc)
        else:
            # If a generation turns the refusal into an empty page, no tool
            # may leak; either way the operator sees nothing.
            assert list(listed.tools) == []
        message = await _mcp_error_message(client, "alpha-add", {"a": 1, "b": 2})

    assert "operator tokens are not accepted" in message


@pytest.mark.asyncio
async def test_mcp_keeps_an_operator_token_when_the_deployment_allows_it(
    table, store, resolver, monkeypatch
):
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: "operator-token",
    )
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        result = await client.list_tools()

    assert {tool.name for tool in result.tools} == {"alpha-add", "beta-echo"}


@pytest.mark.asyncio
async def test_mcp_applies_the_process_permission_policy(
    table, store, resolver, monkeypatch
):
    from toolregistry.permissions import (
        PermissionPolicy,
        PermissionResult,
        PermissionRule,
    )

    created = store.create("both-bot", {"namespaces": ["alpha", "beta"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    table.registry.set_permission_policy(
        PermissionPolicy(
            rules=[
                PermissionRule(
                    name="deny-echo",
                    match=lambda tool, _parameters: tool.name == "beta-echo",
                    result=PermissionResult.DENY,
                    reason="read-only host",
                )
            ],
            fallback=PermissionResult.ALLOW,
        )
    )
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        allowed = await client.call_tool("alpha-add", {"a": 1, "b": 2})
        denied = await _mcp_error_message(client, "beta-echo", {"text": "hi"})

    assert allowed.content[0].text == "3"
    assert "denied by the process permission policy" in denied
    assert "read-only host" in denied


@pytest.mark.asyncio
async def test_mcp_reports_a_busy_client_with_the_server_error_code(
    table, store, resolver, monkeypatch
):
    from contextlib import asynccontextmanager

    from toolregistry_server.isolation import ClientBusyTimeout

    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )

    class _BusyLimiter:
        @asynccontextmanager
        async def slot(self, principal):
            raise ClientBusyTimeout("key_1", 1, 0.01)
            yield  # pragma: no cover - unreachable, the raise is the point

    stream = _capture_mcp_adapter_log(monkeypatch)
    server = route_table_to_mcp_server(
        table, principal_resolver=resolver, client_limiter=_BusyLimiter()
    )

    async with create_test_client(server) as client:
        message = await _mcp_error_message(client, "alpha-add", {"a": 1, "b": 2})

    assert "too many concurrent calls" in message
    output = stream.getvalue()
    assert "outcome=busy" in output
    assert "tool=alpha-add" in output
    usage = store.usage(created.record.id)
    assert usage.calls == 1
    assert usage.busy == 1
    assert usage.last_success_at is None


# ---------------------------------------------------------------------------
# The process permission policy on both OpenAPI backends (#145)
# ---------------------------------------------------------------------------


def _deny_echo_policy():
    from toolregistry.permissions import (
        PermissionPolicy,
        PermissionResult,
        PermissionRule,
    )

    return PermissionPolicy(
        rules=[
            PermissionRule(
                name="deny-echo",
                match=lambda tool, _parameters: tool.name == "beta-echo",
                result=PermissionResult.DENY,
                reason="read-only host",
            )
        ],
        fallback=PermissionResult.ALLOW,
    )


def test_openapi_applies_the_process_permission_policy(table, store, resolver):
    created = store.create("both-bot", {"namespaces": ["alpha", "beta"]})
    table.registry.set_permission_policy(_deny_echo_policy())
    client = _fastapi_client(table, resolver)
    headers = _bearer(created.secret)

    allowed = client.post("/tools/alpha/add", json={"a": 1, "b": 2}, headers=headers)
    denied = client.post("/tools/beta/echo", json={"text": "hi"}, headers=headers)

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert "denied by the process permission policy" in denied.json()["detail"]
    assert "read-only host" in denied.json()["detail"]
    usage = store.usage(created.record.id)
    assert usage.calls == 2
    assert usage.ok == 1
    assert usage.denied == 1


def test_openapi_without_a_policy_still_allows_everything(table, store, resolver):
    created = store.create("both-bot", {"namespaces": ["alpha", "beta"]})
    client = _fastapi_client(table, resolver)

    response = client.post(
        "/tools/beta/echo", json={"text": "hi"}, headers=_bearer(created.secret)
    )

    assert response.status_code == 200


def test_httpserver_applies_the_process_permission_policy(httpserver, table, store):
    url, _ = httpserver
    created = store.create("both-bot", {"namespaces": ["alpha", "beta"]})
    table.registry.set_permission_policy(_deny_echo_policy())

    status, _, body = _request(
        url, "/tools/beta/echo", {"text": "hi"}, headers=_bearer(created.secret)
    )

    assert status == 403
    assert "denied by the process permission policy" in json.loads(body)["detail"]
    usage = store.usage(created.record.id)
    assert usage.calls == 1
    assert usage.denied == 1


# ---------------------------------------------------------------------------
# Durable attribution (#224)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_call_is_recorded_with_attribution(
    table, store, resolver, monkeypatch
):
    from toolregistry.admin import ExecutionStatus

    from toolregistry_server.adapters.mcp import MCPAdapter

    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    adapter = MCPAdapter(table, principal_resolver=resolver)

    async with create_test_client(adapter.server) as client:
        await client.call_tool("alpha-add", {"a": 1, "b": 2})

    [entry] = table.registry.get_execution_log().get_entries()
    assert entry.tool_name == "alpha-add"
    assert entry.status is ExecutionStatus.SUCCESS
    assert entry.arguments == {"a": 1, "b": 2}
    assert entry.result == "3"
    assert entry.metadata == {"key_id": created.record.id, "client_id": "alpha-bot"}
    usage = store.usage(created.record.id)
    assert usage.calls == 1
    assert usage.ok == 1
    assert usage.last_success_at is not None


def _capture_mcp_adapter_log(monkeypatch):
    """Replace the MCP adapter logger and return the captured stream."""
    from io import StringIO

    from toolregistry_server._vendor.structlog import (
        BoundLogger,
        ConsoleRenderer,
        PrintLogger,
        TimeStamper,
        add_log_level,
    )
    from toolregistry_server.adapters.mcp import adapter as adapter_module

    stream = StringIO()
    log = BoundLogger(
        logger=PrintLogger(file=stream),
        processors=[add_log_level, TimeStamper(), ConsoleRenderer(colors=False)],
        context={},
    )
    monkeypatch.setattr(adapter_module, "logger", log)
    return stream


@pytest.mark.asyncio
async def test_mcp_scope_refusal_is_logged_as_denied(
    table, store, resolver, monkeypatch
):
    """A refused attempt is logged as denied, not as an executed call."""
    from toolregistry_server.adapters.mcp import MCPAdapter

    created = store.create("alpha-bot", {"namespaces": ["alpha"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    stream = _capture_mcp_adapter_log(monkeypatch)
    adapter = MCPAdapter(table, principal_resolver=resolver)

    async with create_test_client(adapter.server) as client:
        message = await _mcp_error_message(client, "beta-echo", {"text": "hi"})

    assert "not permitted for this credential" in message
    output = stream.getvalue()
    assert "outcome=denied" in output
    assert "tool=beta-echo" in output
    assert "client_id=alpha-bot" in output
    assert "not permitted for this credential" in output
    [entry] = table.registry.get_execution_log().get_entries()
    assert entry.tool_name == "beta-echo"
    assert entry.status.value == "error"
    assert entry.arguments == {}
    assert entry.metadata == {
        "key_id": created.record.id,
        "client_id": "alpha-bot",
        "outcome": "denied",
    }
    usage = store.usage(created.record.id)
    assert usage.calls == 1
    assert usage.denied == 1


@pytest.mark.asyncio
async def test_mcp_policy_refusal_is_logged_as_denied(
    table, store, resolver, monkeypatch
):
    created = store.create("both-bot", {"namespaces": ["alpha", "beta"]})
    monkeypatch.setattr(
        "toolregistry_server.adapters.mcp.adapter._request_authorization",
        lambda: created.secret,
    )
    stream = _capture_mcp_adapter_log(monkeypatch)
    table.registry.set_permission_policy(_deny_echo_policy())
    table.registry.enable_logging()
    server = route_table_to_mcp_server(table, principal_resolver=resolver)

    async with create_test_client(server) as client:
        message = await _mcp_error_message(client, "beta-echo", {"text": "hi"})

    assert "read-only host" in message
    output = stream.getvalue()
    assert "outcome=denied" in output
    assert "tool=beta-echo" in output
    assert "read-only host" in output
    [entry] = table.registry.get_execution_log().get_entries()
    assert entry.tool_name == "beta-echo"
    assert entry.arguments == {}
    assert entry.metadata["outcome"] == "denied"
    usage = store.usage(created.record.id)
    assert usage.calls == 1
    assert usage.denied == 1


def _teapot_table() -> RouteTable:
    """A table whose only tool raises a deliberate ``HTTPException``."""
    from fastapi import HTTPException

    registry = ToolRegistry()

    def teapot() -> str:
        """Refuse with a deliberate HTTP status."""
        raise HTTPException(status_code=418, detail="teapot")

    registry.register(teapot, namespace="alpha")
    return RouteTable(registry)


def test_a_handler_raised_http_exception_is_recorded_on_fastapi(store, resolver):
    from toolregistry.admin import ExecutionStatus

    table = _teapot_table()
    table.registry.enable_logging()
    created = store.create("bot", {"namespaces": ["alpha"]})
    client = _fastapi_client(table, resolver)

    response = client.post(
        "/tools/alpha/teapot", json={}, headers=_bearer(created.secret)
    )

    assert response.status_code == 418
    [entry] = table.registry.get_execution_log().get_entries()
    assert entry.tool_name == "alpha-teapot"
    assert entry.status is ExecutionStatus.ERROR
    assert entry.metadata == {"key_id": created.record.id, "client_id": "bot"}
    usage = store.usage(created.record.id)
    assert usage.error == 1
    assert usage.ok == 0


def test_a_handler_raised_http_exception_is_recorded_on_httpserver(tmp_path):
    from toolregistry.admin import ExecutionStatus

    from toolregistry_server.keystore import ApiKeyStore, PrincipalResolver

    store = ApiKeyStore(tmp_path / "keys.db")
    try:
        resolver = PrincipalResolver(store)
        table = _teapot_table()
        table.registry.enable_logging()
        created = store.create("bot", {"namespaces": ["alpha"]})
        app = create_httpserver_app(table, principal_resolver=resolver)
        url = _serve(app)
        try:
            status, _, _ = _request(
                url, "/tools/alpha/teapot", {}, headers=_bearer(created.secret)
            )
        finally:
            app.shutdown()
        usage = store.usage(created.record.id)
    finally:
        store.close()

    # The httpserver backend has no HTTP-aware error path, so the deliberate
    # status becomes a 500 — but the call itself is still one log row.
    assert status == 500
    [entry] = table.registry.get_execution_log().get_entries()
    assert entry.tool_name == "alpha-teapot"
    assert entry.status is ExecutionStatus.ERROR
    assert usage.error == 1
    assert usage.ok == 0


def test_openapi_call_is_recorded_with_attribution(table, store, resolver):
    from toolregistry.admin import ExecutionStatus

    created = store.create("bot", {"namespaces": ["alpha"]})
    table.registry.enable_logging()
    client = _fastapi_client(table, resolver)

    response = client.post(
        "/tools/alpha/add", json={"a": 1, "b": 2}, headers=_bearer(created.secret)
    )

    assert response.status_code == 200
    [entry] = table.registry.get_execution_log().get_entries()
    assert entry.status is ExecutionStatus.SUCCESS
    assert entry.result == "3"
    assert entry.metadata == {
        "key_id": created.record.id,
        "client_id": "bot",
    }


def test_httpserver_call_is_recorded_with_attribution(httpserver, table, store):
    from toolregistry.admin import ExecutionStatus

    url, _ = httpserver
    created = store.create("bot", {"namespaces": ["alpha"]})
    table.registry.enable_logging()

    status, _, _ = _request(
        url, "/tools/alpha/add", {"a": 1, "b": 2}, headers=_bearer(created.secret)
    )

    assert status == 200
    [entry] = table.registry.get_execution_log().get_entries()
    assert entry.status is ExecutionStatus.SUCCESS
    assert entry.metadata == {
        "key_id": created.record.id,
        "client_id": "bot",
    }


def test_a_call_on_a_disabled_log_leaves_no_entry(table, resolver):
    client = _fastapi_client(table, resolver)

    response = client.post(
        "/tools/alpha/add", json={"a": 1, "b": 2}, headers=_bearer("operator-token")
    )

    assert response.status_code == 200
    assert table.registry.get_execution_log() is None


def test_the_standalone_openapi_adapter_enables_the_log(table, resolver):
    from toolregistry_server.adapters.openapi import OpenAPIAdapter

    OpenAPIAdapter(table, principal_resolver=resolver)

    assert table.registry.get_execution_log() is not None


def test_the_standalone_openapi_adapter_leaves_it_off_without_keys(table):
    from toolregistry_server.adapters.openapi import OpenAPIAdapter

    OpenAPIAdapter(table)

    assert table.registry.get_execution_log() is None
