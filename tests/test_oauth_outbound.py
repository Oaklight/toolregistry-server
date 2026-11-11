"""Outbound address screening for OAuth requests (#264).

Every URL is an IP literal, so the policy decides without a DNS lookup.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from toolregistry_server.oauth import _outbound
from toolregistry_server.oauth.upstream.config import OAuthClientConfig
from toolregistry_server.oauth.upstream.provider import (
    OAuthClientProvider,
    TransientOAuthError,
)
from toolregistry_server.oauth.upstream.store import TokenStore
from toolregistry_server.oauth.verifier import _load_json, _post_form

pytestmark = pytest.mark.usefixtures("real_outbound_policy")

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/token",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/jwks",
        "http://[::1]/token",
    ],
)
def test_a_private_host_is_refused(url):
    with pytest.raises(_outbound.OutboundRefused):
        _outbound.screen_url(url)


def test_a_private_host_is_allowed_on_request():
    _outbound.screen_url("http://10.0.0.5/jwks", allow_private_hosts=True)


def test_a_public_literal_is_allowed():
    _outbound.screen_url("https://93.184.216.34/token")


def test_verifier_fetches_are_screened_before_dialing(monkeypatch):
    dialed: list[object] = []
    monkeypatch.setattr(_outbound, "open_url", lambda *a, **k: dialed.append(a))
    with pytest.raises(_outbound.OutboundRefused):
        _load_json("http://169.254.169.254/jwks", 5.0)
    with pytest.raises(_outbound.OutboundRefused):
        _post_form("http://127.0.0.1/introspect", {"token": "T"}, 5.0, {})
    assert dialed == []


class _Recording:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, url, timeout):
        self.calls.append(url)
        return {}

    def post_form(self, url, fields, headers, timeout):
        self.calls.append(url)
        return {"access_token": "AT", "expires_in": 3600}


def _provider(transport, *, allow_private_hosts: bool):
    declaration = OAuthClientConfig(
        source="namespace:example",
        client_id="toolregistry",
        issuer="http://127.0.0.1",
        client_secret="secret",
        token_endpoint="http://127.0.0.1/token",
        token_store=":memory:",
        allow_private_hosts=allow_private_hosts,
    )
    store = TokenStore(":memory:", clock=lambda: NOW)
    provider = OAuthClientProvider(
        (declaration,), store=store, transport=transport, clock=lambda: NOW
    )
    return provider, declaration, store


def test_a_loopback_token_endpoint_is_a_transient_refusal():
    transport = _Recording()
    provider, declaration, store = _provider(transport, allow_private_hosts=False)
    try:
        with pytest.raises(TransientOAuthError, match="outbound address policy"):
            provider.access_token(declaration)
    finally:
        store.close()
    assert transport.calls == []


def test_allow_private_hosts_lets_the_token_request_through():
    transport = _Recording()
    provider, declaration, store = _provider(transport, allow_private_hosts=True)
    try:
        record = provider.access_token(declaration)
    finally:
        store.close()
    assert record.access_token == "AT"
    assert transport.calls == ["http://127.0.0.1/token"]


def test_a_verifier_request_does_not_follow_a_redirect():
    """A 302 must not re-aim a screened request at another host."""
    import http.server
    import threading
    import urllib.error

    hits: list[str] = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):  # noqa: D102 - silence the test log
            pass

    target = http.server.HTTPServer(("127.0.0.1", 0), Target)
    target_url = f"http://127.0.0.1:{target.server_port}/meta"

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", target_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.do_GET()

        def log_message(self, *args):  # noqa: D102
            pass

    source = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    source_url = f"http://127.0.0.1:{source.server_port}/jwks"
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (source, target)
    ]
    for thread in threads:
        thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            _load_json(source_url, 5.0, allow_private_hosts=True)
        assert caught.value.code == 302
        with pytest.raises(urllib.error.HTTPError):
            _post_form(
                source_url,
                {"token": "T"},
                5.0,
                {"X-Probe": "1"},
                allow_private_hosts=True,
            )
    finally:
        for server in (source, target):
            server.shutdown()
            server.server_close()
    assert hits == []
