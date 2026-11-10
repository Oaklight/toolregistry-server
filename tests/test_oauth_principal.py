"""OAuth principals behave like API keys at the authorization layer (#239).

Each surface is exercised with two OAuth clients that carry different
scopes: MCP ``tools/list``/``tools/call``, the FastAPI OpenAPI app and the
vendored httpserver app.  The tests also check that the two clients never
share principal state and that every outcome reaches the execution log.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp._compat import create_test_client
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app
from toolregistry_server.auth import create_scoped_bearer_dependency
from toolregistry_server.gateway.openapi_asgi import create_openapi_app
from toolregistry_server.isolation import (
    ClientBusyTimeout,
    ClientLimiter,
    limiter_key,
    principal_key,
)
from toolregistry_server.oauth import OAuthResolver, OAuthSettings, OAuthTokenVerifier

ISSUER = "https://idp.example.com/realms/dev"
AUDIENCE = "https://tools.example.com/mcp"
JWKS_URL = "https://idp.example.com/realms/dev/jwks"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key()))
_JWK.update({"kid": "key-1", "alg": "RS256", "use": "sig"})


class _Response:
    def __init__(self, document: dict) -> None:
        self._payload = json.dumps(document).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


_REAL_URLOPEN = urllib.request.urlopen


def _urlopen(url: Any, timeout: float | None = None) -> _Response:
    """Serve the fake JWKS and delegate every other URL to the real opener."""
    target = getattr(url, "full_url", url)
    if target == JWKS_URL:
        return _Response({"keys": [_JWK]})
    return _REAL_URLOPEN(url, timeout=timeout)


@pytest.fixture(autouse=True)
def _jwks(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)


def _settings(**overrides) -> OAuthSettings:
    values = {"issuer": ISSUER, "audience": AUDIENCE, "jwks_uri": JWKS_URL}
    values.update(overrides)
    return OAuthSettings(**values)


def _token(*, client_id: str, scope: str, sub: str = "user-1", **claims) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "client_id": client_id,
        "scope": scope,
        "iat": now,
        "exp": now + 300,
    }
    payload.update(claims)
    return jwt.encode(payload, _KEY, algorithm="RS256", headers={"kid": "key-1"})


def _resolver(**overrides) -> OAuthResolver:
    settings = _settings(
        scope_map={
            "tools:alpha": ("alpha",),
            "tools:beta": ("beta",),
        },
        **overrides,
    )
    return OAuthResolver(OAuthTokenVerifier(settings))


def _table() -> RouteTable:
    registry = ToolRegistry()

    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    def echo(text: str) -> str:
        """Echo text."""
        return text

    registry.register(add, namespace="alpha")
    registry.register(echo, namespace="beta")
    return RouteTable(registry)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _fastapi_client(table: RouteTable, resolver: OAuthResolver) -> TestClient:
    from fastapi import Depends

    app = create_openapi_app(
        table,
        dependencies=[Depends(create_scoped_bearer_dependency(resolver))],
        principal_resolver=resolver,
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
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
    url: str, payload: Any, headers: dict[str, str] | None = None
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**(headers or {}), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


class TestMcp:
    async def test_tools_list_is_scoped_to_the_token(self, monkeypatch):
        from toolregistry_server.adapters.mcp import route_table_to_mcp_server

        table = _table()
        resolver = _resolver()
        server = route_table_to_mcp_server(table, principal_resolver=resolver)
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        monkeypatch.setattr(
            "toolregistry_server.adapters.mcp.adapter._request_authorization",
            lambda: token,
        )

        async with create_test_client(server) as client:
            result = await client.list_tools()

        assert {tool.name for tool in result.tools} == {"alpha-add"}

    async def test_an_out_of_scope_call_is_denied_and_logged(self, monkeypatch):
        from toolregistry_server.adapters.mcp import MCPAdapter

        table = _table()
        resolver = _resolver()
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        monkeypatch.setattr(
            "toolregistry_server.adapters.mcp.adapter._request_authorization",
            lambda: token,
        )
        adapter = MCPAdapter(table, principal_resolver=resolver, execution_log=True)

        async with create_test_client(adapter.server) as client:
            try:
                await client.call_tool("beta-echo", {"text": "hi"})
            except Exception as exc:
                message = str(exc)
            else:  # pragma: no cover - the call must be refused
                raise AssertionError("the out-of-scope call was accepted")

        assert "not permitted for this credential" in message
        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.tool_name == "beta-echo"
        assert entry.arguments == {}
        assert entry.metadata == {
            "key_id": None,
            "client_id": "alpha-bot",
            "outcome": "denied",
        }

    async def test_an_in_scope_call_records_the_client(self, monkeypatch):
        from toolregistry_server.adapters.mcp import MCPAdapter

        table = _table()
        resolver = _resolver()
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        monkeypatch.setattr(
            "toolregistry_server.adapters.mcp.adapter._request_authorization",
            lambda: token,
        )
        adapter = MCPAdapter(table, principal_resolver=resolver, execution_log=True)

        async with create_test_client(adapter.server) as client:
            result = await client.call_tool("alpha-add", {"a": 1, "b": 2})

        assert result.content[0].text == "3"
        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.tool_name == "alpha-add"
        assert entry.metadata == {
            "key_id": None,
            "client_id": "alpha-bot",
        }

    async def test_a_busy_client_is_recorded(self, monkeypatch):
        from contextlib import asynccontextmanager

        from toolregistry_server.adapters.mcp import route_table_to_mcp_server
        from toolregistry_server.adapters.mcp._compat import McpErrorClass
        from toolregistry_server.isolation import ClientBusyTimeout

        table = _table()
        resolver = _resolver()
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        monkeypatch.setattr(
            "toolregistry_server.adapters.mcp.adapter._request_authorization",
            lambda: token,
        )

        class _BusyLimiter:
            @asynccontextmanager
            async def slot(self, principal):
                raise ClientBusyTimeout("oauth:alpha-bot:user-1", 1, 0.01)
                yield  # pragma: no cover - unreachable

        table.registry.enable_logging()
        server = route_table_to_mcp_server(
            table, principal_resolver=resolver, client_limiter=_BusyLimiter()
        )

        async with create_test_client(server) as client:
            try:
                result = await client.call_tool("alpha-add", {"a": 1, "b": 2})
            except McpErrorClass as exc:
                message = str(exc)
            else:
                message = result.content[0].text

        assert "too many concurrent calls" in message
        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.tool_name == "alpha-add"
        assert entry.metadata == {
            "key_id": None,
            "client_id": "alpha-bot",
            "outcome": "busy",
        }


# ---------------------------------------------------------------------------
# OpenAPI (FastAPI)
# ---------------------------------------------------------------------------


class TestFastApi:
    def test_the_document_lists_only_the_tokens_namespaces(self):
        table = _table()
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        client = _fastapi_client(table, _resolver())

        response = client.get("/openapi.json", headers=_bearer(token))

        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "/tools/alpha/add" in paths
        assert "/tools/beta/echo" not in paths

    def test_an_out_of_scope_call_is_denied_and_logged(self):
        table = _table()
        table.registry.enable_logging()
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        client = _fastapi_client(table, _resolver())

        response = client.post(
            "/tools/beta/echo", json={"text": "hi"}, headers=_bearer(token)
        )

        assert response.status_code == 403
        assert "not permitted" in response.json()["detail"]
        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.tool_name == "beta-echo"
        assert entry.metadata == {
            "key_id": None,
            "client_id": "alpha-bot",
            "outcome": "denied",
        }

    def test_an_in_scope_call_records_the_client(self):
        table = _table()
        table.registry.enable_logging()
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        client = _fastapi_client(table, _resolver())

        response = client.post(
            "/tools/alpha/add", json={"a": 1, "b": 2}, headers=_bearer(token)
        )

        assert response.status_code == 200
        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.metadata == {
            "key_id": None,
            "client_id": "alpha-bot",
        }


# ---------------------------------------------------------------------------
# OpenAPI (vendored httpserver)
# ---------------------------------------------------------------------------


class TestHttpserver:
    def test_tools_lists_only_the_tokens_namespaces(self):
        table = _table()
        resolver = _resolver()
        app = create_httpserver_app(table, principal_resolver=resolver)
        url = _serve(app)
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        try:
            request = urllib.request.Request(url + "/tools", headers=_bearer(token))
            with urllib.request.urlopen(request) as response:
                payload = json.loads(response.read())
        finally:
            app.shutdown()

        assert [tool["name"] for tool in payload["tools"]] == ["alpha-add"]

    def test_an_out_of_scope_call_is_denied_and_logged(self):
        table = _table()
        table.registry.enable_logging()
        app = create_httpserver_app(table, principal_resolver=_resolver())
        url = _serve(app)
        token = _token(client_id="alpha-bot", scope="tools:alpha")
        try:
            status, body = _request(
                url + "/tools/beta/echo", {"text": "hi"}, _bearer(token)
            )
        finally:
            app.shutdown()

        assert status == 403
        assert "not permitted" in json.loads(body)["detail"]
        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.tool_name == "beta-echo"
        assert entry.metadata == {
            "key_id": None,
            "client_id": "alpha-bot",
            "outcome": "denied",
        }


# ---------------------------------------------------------------------------
# Cross-client isolation
# ---------------------------------------------------------------------------


class TestNoCrossClientLeakage:
    def test_two_clients_for_one_user_have_separate_state(self):
        resolver = _resolver()
        alpha = resolver.resolve(
            _token(client_id="alpha-bot", scope="tools:alpha", sub="user-1")
        )
        beta = resolver.resolve(
            _token(client_id="beta-bot", scope="tools:beta", sub="user-1")
        )

        assert alpha is not None and beta is not None
        assert principal_key(alpha) != principal_key(beta)
        assert alpha.allows("alpha", "alpha-add") is True
        assert alpha.allows("beta", "beta-echo") is False
        assert beta.allows("beta", "beta-echo") is True
        assert beta.allows("alpha", "alpha-add") is False

    def test_the_limiter_keys_on_the_client_not_the_subject(self):
        resolver = _resolver()
        alpha = resolver.resolve(
            _token(
                client_id="alpha-bot",
                scope="tools:alpha",
                sub="user-1",
                max_concurrency=1,
            )
        )
        beta = resolver.resolve(
            _token(
                client_id="beta-bot",
                scope="tools:beta",
                sub="user-1",
                max_concurrency=2,
            )
        )

        limiter = ClientLimiter(default_limit=8, timeout=0.01)

        async def run():
            async with limiter.slot(alpha):
                with pytest.raises(ClientBusyTimeout):
                    # The same client's second slot exceeds its limit of one.
                    async with limiter.slot(alpha):
                        pass
                async with limiter.slot(beta):
                    pass

        asyncio.run(run())

        snapshot = {record["client_key"]: record for record in limiter.snapshot()}
        assert snapshot[limiter_key(alpha)]["limit"] == 1
        assert snapshot[limiter_key(beta)]["limit"] == 2
        assert snapshot[limiter_key(alpha)]["client_id"] == "alpha-bot"
        assert snapshot[limiter_key(beta)]["client_id"] == "beta-bot"
        # The sub stays in the accounting key, so the log can still tell the
        # two users apart.
        assert principal_key(alpha) == "oauth:alpha-bot:user-1"

    def test_two_subjects_of_one_client_share_one_budget(self):
        resolver = _resolver()
        first = resolver.resolve(
            _token(
                client_id="reports-bot",
                scope="tools:alpha",
                sub="user-1",
                max_concurrency=2,
            )
        )
        second = resolver.resolve(
            _token(
                client_id="reports-bot",
                scope="tools:alpha",
                sub="user-2",
                max_concurrency=2,
            )
        )
        assert first is not None and second is not None
        assert principal_key(first) != principal_key(second)
        assert limiter_key(first) == limiter_key(second) == "oauth:reports-bot"

        limiter = ClientLimiter(default_limit=8, timeout=0.01)

        async def run():
            async with limiter.slot(first), limiter.slot(second):
                with pytest.raises(ClientBusyTimeout):
                    # The client's budget is two in flight, not two per user.
                    async with limiter.slot(first):
                        pass

        asyncio.run(run())

        [record] = limiter.snapshot()
        assert record["client_key"] == "oauth:reports-bot"
        assert record["limit"] == 2
        assert record["calls"] == 2
        assert record["errors"] == 1

    def test_two_clients_do_not_share_one_budget(self):
        resolver = _resolver()
        alpha = resolver.resolve(
            _token(
                client_id="alpha-bot",
                scope="tools:alpha",
                sub="user-1",
                max_concurrency=1,
            )
        )
        beta = resolver.resolve(
            _token(
                client_id="beta-bot",
                scope="tools:beta",
                sub="user-1",
                max_concurrency=1,
            )
        )
        assert alpha is not None and beta is not None

        limiter = ClientLimiter(default_limit=8, timeout=0.01)

        async def run():
            # Each client's single slot is free while the other is held.
            async with limiter.slot(alpha), limiter.slot(beta):
                pass

        asyncio.run(run())

        keys = [record["client_key"] for record in limiter.snapshot()]
        assert keys == ["oauth:alpha-bot", "oauth:beta-bot"]

    def test_a_wider_claim_cannot_raise_a_shared_client_budget(self):
        resolver = _resolver()
        narrow = resolver.resolve(
            _token(
                client_id="reports-bot",
                scope="tools:alpha",
                sub="user-1",
                max_concurrency=2,
            )
        )
        wide = resolver.resolve(
            _token(
                client_id="reports-bot",
                scope="tools:alpha",
                sub="user-2",
                max_concurrency=6,
            )
        )
        assert narrow is not None and wide is not None

        limiter = ClientLimiter(default_limit=8, timeout=0.01)

        async def run():
            async with limiter.slot(narrow), limiter.slot(wide):
                with pytest.raises(ClientBusyTimeout):
                    # The client's budget stays at two; the wider claim on
                    # the second token does not enlarge it.
                    async with limiter.slot(wide):
                        pass

        asyncio.run(run())

        [record] = limiter.snapshot()
        assert record["limit"] == 2

    def test_a_stored_key_keeps_its_key_id_as_the_budget_key(self):
        resolver = _resolver()
        principal = resolver.resolve(_token(client_id="alpha-bot", scope="tools:alpha"))
        assert principal is not None
        keyed = replace(principal, key_id="key_1")

        assert limiter_key(keyed) == principal_key(keyed) == "key_1"
