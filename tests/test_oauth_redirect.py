"""Swagger UI OAuth2 redirect page on both OpenAPI backends (#245).

Swagger UI sends the browser to ``<page directory>/oauth2-redirect.html``
after the authorization server redirects back.  The redirect arrives without
a bearer token, so the page must be reachable unauthenticated on the
standalone httpserver and under the gateway's ``/openapi`` mount.
"""

from __future__ import annotations

import asyncio
import threading
import time
import urllib.error
import urllib.request

import httpx
import pytest
from fastapi.testclient import TestClient
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app
from toolregistry_server.gateway import GatewayAdapter
from toolregistry_server.gateway.openapi_asgi import create_openapi_app
from toolregistry_server.oauth.redirect import (
    OAUTH2_REDIRECT_PATH,
    OAUTH2_REDIRECT_URL,
    oauth2_redirect_html,
)


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


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class TestHttpserverBackend:
    def test_the_redirect_page_is_served_without_a_token(self):
        app = create_httpserver_app(_table(), tokens=["secret"])
        url = _serve(app)
        try:
            status, body = _get(url + OAUTH2_REDIRECT_PATH)
        finally:
            app.shutdown()

        assert status == 200
        assert body.decode("utf-8") == oauth2_redirect_html()

    def test_the_docs_page_names_the_served_redirect_location(self):
        app = create_httpserver_app(_table(), tokens=["secret"])
        url = _serve(app)
        try:
            status, body = _get(
                url + "/docs", headers={"Authorization": "Bearer secret"}
            )
        finally:
            app.shutdown()

        assert status == 200
        page = body.decode("utf-8")
        assert f'oauth2RedirectUrl: "{OAUTH2_REDIRECT_URL}"' in page


class TestGatewayBackend:
    def test_the_asgi_frontend_serves_the_page_before_its_auth_wrapper(self):
        gateway = GatewayAdapter.create(
            _table(), tokens=["secret"], backend="fastapi"
        )
        transport = httpx.ASGITransport(app=gateway.app)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get("/openapi" + OAUTH2_REDIRECT_PATH)

        response = asyncio.run(run())

        assert response.status_code == 200
        assert response.text == oauth2_redirect_html()

    def test_the_asgi_docs_page_uses_a_relative_redirect_url(self):
        gateway = GatewayAdapter.create(
            _table(), tokens=["secret"], backend="fastapi"
        )
        transport = httpx.ASGITransport(app=gateway.app)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get(
                    "/openapi/docs", headers={"Authorization": "Bearer secret"}
                )

        response = asyncio.run(run())

        assert response.status_code == 200
        assert f'oauth2RedirectUrl: "{OAUTH2_REDIRECT_URL}"' in response.text

    def test_the_redirect_page_follows_a_changed_frontend_path(self):
        gateway = GatewayAdapter.create(
            _table(), tokens=["secret"], backend="fastapi"
        )
        gateway.update_frontend("openapi", path="/rest")
        transport = httpx.ASGITransport(app=gateway.app)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get("/rest" + OAUTH2_REDIRECT_PATH)

        response = asyncio.run(run())

        assert response.status_code == 200
        assert response.text == oauth2_redirect_html()


class TestDirectAsgiApp:
    def test_create_openapi_app_serves_the_route_when_unauthenticated(self):
        client = TestClient(create_openapi_app(_table()))

        response = client.get(OAUTH2_REDIRECT_PATH)

        assert response.status_code == 200
        assert response.text == oauth2_redirect_html()
        assert "swaggerUIRedirectOauth2" in response.text
