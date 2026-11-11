"""OpenAPI OAuth2 security scheme tests (#235).

The scheme definitions live in :mod:`toolregistry_server.oauth.openapi` and
are consumed by both serving paths.  Endpoint discovery is exercised through
the shared metadata iterator, so no test reaches the network.
"""

import asyncio
import json
import threading
import time
import urllib.request
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app
from toolregistry_server.gateway import GatewayAdapter
from toolregistry_server.gateway.openapi_asgi import create_openapi_app
from toolregistry_server.keystore import PrincipalResolver
from toolregistry_server.oauth import OAuthSettings, OpenAPIAuth

ISSUER = "https://idp.example.com/realms/dev"
DOC_TOKEN = "doc-secret"
AUTHZ = "https://idp.example.com/realms/dev/authorize"
TOKEN = "https://idp.example.com/realms/dev/token"


def _settings(**overrides) -> OAuthSettings:
    values = {
        "issuer": ISSUER,
        "audience": "https://tools.example.com/mcp",
        "authorization_url": AUTHZ,
        "token_url": TOKEN,
    }
    values.update(overrides)
    return OAuthSettings(**values)


def _table() -> RouteTable:
    registry = ToolRegistry()

    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    registry.register(add)
    return RouteTable(registry)


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


def _document(
    openapi_auth: OpenAPIAuth | None = None,
    *,
    tokens: list[str] | None = None,
    resolver: object | None = None,
) -> dict:
    app = create_httpserver_app(
        _table(),
        tokens=tokens,
        principal_resolver=resolver,
        openapi_auth=openapi_auth,
    )
    url = _serve(app)
    headers = {}
    if tokens or resolver is not None:
        headers["Authorization"] = f"Bearer {DOC_TOKEN}"
    request = urllib.request.Request(f"{url}/openapi.json", headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    finally:
        app.shutdown()


def _oauth2(document: dict) -> dict:
    return document["components"]["securitySchemes"]["OAuth2"]


class TestDocumentSecurity:
    def test_a_configured_issuer_publishes_the_authorization_code_flow(self):
        document = _document(OpenAPIAuth(_settings(), bearer_accepted=False))

        assert document["openapi"] == "3.1.0"
        scheme = _oauth2(document)
        assert scheme["type"] == "oauth2"
        flow = scheme["flows"]["authorizationCode"]
        assert flow["authorizationUrl"] == AUTHZ
        assert flow["tokenUrl"] == TOKEN
        assert "refreshUrl" not in flow
        assert document["security"] == [{"OAuth2": []}]

    def test_the_urls_are_absolute(self):
        flow = _oauth2(_document(OpenAPIAuth(_settings(), bearer_accepted=False)))[
            "flows"
        ]["authorizationCode"]
        for key in ("authorizationUrl", "tokenUrl"):
            parsed = urlparse(flow[key])
            assert parsed.scheme == "https"
            assert parsed.netloc == "idp.example.com"

    def test_the_document_is_unchanged_without_an_issuer(self):
        document = _document(
            OpenAPIAuth(None, bearer_accepted=True), tokens=[DOC_TOKEN]
        )

        assert "security" not in document
        assert "components" not in document

    def test_static_tokens_alone_keep_the_previous_document(self):
        document = _document(tokens=[DOC_TOKEN])

        assert "security" not in document

    def test_bearer_alone_without_oauth_keeps_the_bearer_scheme(self):
        document = _document(resolver=PrincipalResolver(operator_tokens=[DOC_TOKEN]))

        assert document["security"] == [{"HTTPBearer": []}]
        assert "OAuth2" not in document["components"]["securitySchemes"]

    def test_keys_and_oauth_are_an_or_of_two_requirements(self):
        document = _document(
            OpenAPIAuth(_settings(), bearer_accepted=True), tokens=[DOC_TOKEN]
        )

        schemes = document["components"]["securitySchemes"]
        assert set(schemes) == {"OAuth2", "HTTPBearer"}
        assert schemes["HTTPBearer"] == {"type": "http", "scheme": "bearer"}
        assert document["security"] == [{"OAuth2": []}, {"HTTPBearer": []}]

    def test_scopes_follow_the_configured_list(self):
        document = _document(
            OpenAPIAuth(
                _settings(scopes_supported=("openid", "profile", "email")),
                bearer_accepted=False,
            )
        )

        assert _oauth2(document)["flows"]["authorizationCode"]["scopes"] == {
            "openid": "",
            "profile": "",
            "email": "",
        }

    def test_an_empty_scope_list_is_allowed(self):
        document = _document(
            OpenAPIAuth(_settings(scopes_supported=()), bearer_accepted=False)
        )

        assert _oauth2(document)["flows"]["authorizationCode"]["scopes"] == {}

    def test_a_refresh_url_is_published_when_set(self):
        document = _document(
            OpenAPIAuth(
                _settings(refresh_url="https://idp.example.com/realms/dev/refresh"),
                bearer_accepted=False,
            )
        )

        assert (
            _oauth2(document)["flows"]["authorizationCode"]["refreshUrl"]
            == "https://idp.example.com/realms/dev/refresh"
        )

    def test_the_scheme_is_omitted_when_no_endpoint_is_known(self, monkeypatch):
        monkeypatch.setattr(
            "toolregistry_server.oauth.openapi.iter_authorization_server_metadata",
            lambda issuer, timeout, **_: iter(()),
        )

        document = _document(
            OpenAPIAuth(
                _settings(authorization_url=None, token_url=None),
                bearer_accepted=False,
            )
        )

        assert "security" not in document


class TestEndpointDiscovery:
    def _metadata(self, monkeypatch, document: dict) -> None:
        monkeypatch.setattr(
            "toolregistry_server.oauth.openapi.iter_authorization_server_metadata",
            lambda issuer, timeout, **_: iter((document,)),
        )

    def test_endpoints_come_from_the_issuer_metadata(self, monkeypatch):
        self._metadata(
            monkeypatch,
            {
                "authorization_endpoint": AUTHZ,
                "token_endpoint": TOKEN,
            },
        )

        document = _document(
            OpenAPIAuth(
                _settings(authorization_url=None, token_url=None),
                bearer_accepted=False,
            )
        )

        flow = _oauth2(document)["flows"]["authorizationCode"]
        assert flow["authorizationUrl"] == AUTHZ
        assert flow["tokenUrl"] == TOKEN

    def test_explicit_overrides_win_over_the_metadata(self, monkeypatch):
        self._metadata(
            monkeypatch,
            {
                "authorization_endpoint": "https://metadata.example.com/auth",
                "token_endpoint": "https://metadata.example.com/token",
            },
        )

        document = _document(
            OpenAPIAuth(
                _settings(
                    authorization_url=AUTHZ,
                    token_url=TOKEN,
                ),
                bearer_accepted=False,
            )
        )

        flow = _oauth2(document)["flows"]["authorizationCode"]
        assert flow["authorizationUrl"] == AUTHZ
        assert flow["tokenUrl"] == TOKEN

    def test_an_override_can_supply_one_missing_endpoint(self, monkeypatch):
        self._metadata(monkeypatch, {"authorization_endpoint": AUTHZ})

        document = _document(
            OpenAPIAuth(
                _settings(authorization_url=None, token_url=TOKEN),
                bearer_accepted=False,
            )
        )

        flow = _oauth2(document)["flows"]["authorizationCode"]
        assert flow["authorizationUrl"] == AUTHZ
        assert flow["tokenUrl"] == TOKEN


class TestGatewayPaths:
    def test_the_asgi_document_carries_the_scheme(self):
        app = create_openapi_app(
            _table(),
            openapi_auth=OpenAPIAuth(
                _settings(scopes_supported=("openid",)), bearer_accepted=False
            ),
        )

        document = TestClient(app).get("/openapi.json").json()

        assert _oauth2(document)["flows"]["authorizationCode"]["tokenUrl"] == TOKEN
        assert document["security"] == [{"OAuth2": []}]

    async def test_the_gateway_adapter_documents_both_credentials(self):
        gateway = GatewayAdapter.create(
            _table(),
            tokens=["secret"],
            oauth=_settings(scopes_supported=("openid",)),
        )
        transport = httpx.ASGITransport(app=gateway.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                "/openapi/openapi.json", headers={"Authorization": "Bearer secret"}
            )

        assert response.status_code == 200
        document = response.json()
        assert set(document["components"]["securitySchemes"]) == {
            "OAuth2",
            "HTTPBearer",
        }
        assert document["security"] == [{"OAuth2": []}, {"HTTPBearer": []}]
