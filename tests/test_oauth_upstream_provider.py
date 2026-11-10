"""Tests for the upstream MCP OAuth provider (#254, stage 1).

Every HTTP interaction goes through a fake transport, so nothing reaches the
network.  The store is in-memory except where a test explicitly checks disk
permissions.  The transport tests use loopback servers, because the redirect
and truncation behaviour under test is urllib's.
"""

from __future__ import annotations

import base64
import http.server
import json
import socket
import socketserver
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from toolregistry.config import MCPSource

from toolregistry_server.oauth.upstream.config import OAuthClientConfig
from toolregistry_server.oauth.upstream.provider import (
    OAuthClientError,
    OAuthClientProvider,
    OAuthHTTPError,
    OAuthStateError,
    ReauthorizationRequired,
    TransientOAuthError,
    UrllibTransport,
    redact,
)
from toolregistry_server.oauth.upstream.store import TokenRecord, TokenStore

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
SOURCE = "namespace:example"
TOKEN_URL = "https://idp.example.com/token"
AUTH_URL = "https://idp.example.com/authorize"


class FakeTransport:
    """Scripted transport: each POST consumes the next queued response."""

    def __init__(self, *, metadata=None, responses=None):
        self.metadata = metadata or {}
        self.responses = list(responses or [])
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def get_json(self, url: str, timeout: float):
        self.gets.append(url)
        return self.metadata

    def post_form(self, url, fields, headers, timeout):
        self.posts.append((url, dict(fields), dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _declaration(**overrides) -> OAuthClientConfig:
    values: dict[str, Any] = {
        "source": SOURCE,
        "client_id": "toolregistry",
        "issuer": "https://idp.example.com",
        "client_secret": "client-secret",
        "scopes": ("read",),
        "token_endpoint": TOKEN_URL,
        "token_store": ":memory:",
    }
    values.update(overrides)
    return OAuthClientConfig(**values)


def _source(**overrides) -> MCPSource:
    values: dict[str, Any] = {
        "transport": "streamable-http",
        "namespace": "example",
        "url": "https://mcp.example.com/mcp",
    }
    values.update(overrides)
    return MCPSource(**values)


class MutableClock:
    """A clock tests can move forward without sleeping."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return MutableClock(NOW)


@pytest.fixture
def store(clock):
    with TokenStore(":memory:", clock=clock) as opened:
        yield opened


def _provider(declaration, transport, store, clock):
    return OAuthClientProvider(
        (declaration,), store=store, transport=transport, clock=clock
    )


def test_client_credentials_fetch_and_cache(store, clock):
    transport = FakeTransport(responses=[{"access_token": "AT", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)

    headers = provider.headers_for(_source())
    assert headers == {"Authorization": "Bearer AT"}
    assert len(transport.posts) == 1
    assert transport.posts[0][0] == TOKEN_URL
    assert transport.posts[0][1]["grant_type"] == "client_credentials"
    assert transport.posts[0][1]["scope"] == "read"

    # A second connect reuses the stored token.
    assert provider.headers_for(_source()) == {"Authorization": "Bearer AT"}
    assert len(transport.posts) == 1

    stored = store.get(SOURCE)
    assert stored.access_token == "AT"
    assert stored.issuer == "https://idp.example.com"


def test_refresh_before_expiry(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
            refresh_token="RT",
            expires_at=NOW + timedelta(seconds=30),
            updated_at=NOW,
        )
    )
    transport = FakeTransport(responses=[{"access_token": "NEW", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    assert provider.headers_for(_source()) == {"Authorization": "Bearer NEW"}
    assert transport.posts[0][1]["grant_type"] == "refresh_token"
    assert transport.posts[0][1]["refresh_token"] == "RT"


def test_refresh_keeps_the_old_refresh_token(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
            refresh_token="RT",
            expires_at=NOW - timedelta(seconds=1),
            updated_at=NOW,
        )
    )
    transport = FakeTransport(responses=[{"access_token": "NEW", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    provider.headers_for(_source())
    assert store.get(SOURCE).refresh_token == "RT"


def test_invalid_grant_marks_reauth_and_clears_tokens(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
            refresh_token="RT",
            expires_at=NOW - timedelta(seconds=1),
            updated_at=NOW,
        )
    )
    transport = FakeTransport(
        responses=[OAuthHTTPError(400, "invalid_grant", "revoked")]
    )
    # The authorization-code grant has an interactive path, so a rejected
    # refresh latches the source until the operator authorizes it again.
    provider = _provider(
        _declaration(grant="authorization_code"), transport, store, clock
    )

    with pytest.raises(ReauthorizationRequired, match="invalid_grant"):
        provider.headers_for(_source())

    stored = store.get(SOURCE)
    assert stored.needs_reauth is True
    assert stored.access_token is None
    assert stored.refresh_token is None

    # A later connect fails fast, without another request.
    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    assert len(transport.posts) == 1


def test_transient_failure_keeps_the_stored_grant(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
            refresh_token="RT",
            expires_at=NOW - timedelta(seconds=1),
            updated_at=NOW,
        )
    )
    transport = FakeTransport(
        responses=[OAuthHTTPError(503, "temporarily_unavailable", "down")]
    )
    provider = _provider(_declaration(), transport, store, clock)

    with pytest.raises(TransientOAuthError):
        provider.headers_for(_source())

    stored = store.get(SOURCE)
    assert stored.needs_reauth is False
    assert stored.refresh_token == "RT"


def test_authorization_code_without_a_token_requires_reauth(store, clock):
    transport = FakeTransport()
    declaration = _declaration(grant="authorization_code")
    provider = _provider(declaration, transport, store, clock)
    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    assert transport.posts == []


def test_discovery_resolves_the_token_endpoint(store, clock):
    transport = FakeTransport(
        metadata={
            "issuer": "https://idp.example.com",
            "token_endpoint": "https://idp.example.com/discovered/token",
        },
        responses=[{"access_token": "AT", "expires_in": 3600}],
    )
    declaration = _declaration(token_endpoint=None)
    provider = _provider(declaration, transport, store, clock)
    provider.headers_for(_source())
    assert transport.gets  # at least one discovery attempt
    assert transport.posts[0][0] == "https://idp.example.com/discovered/token"


def test_explicit_token_endpoint_skips_discovery(store, clock):
    transport = FakeTransport(responses=[{"access_token": "AT", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    provider.headers_for(_source())
    assert transport.gets == []


def test_discovery_failure_is_transient(store, clock):
    class Down(FakeTransport):
        def get_json(self, url, timeout):
            raise TransientOAuthError("network down")

    provider = _provider(_declaration(token_endpoint=None), Down(), store, clock)
    with pytest.raises(TransientOAuthError, match="could not discover"):
        provider.headers_for(_source())


def test_client_secret_basic_is_used_when_configured(store, clock):
    transport = FakeTransport(responses=[{"access_token": "AT", "expires_in": 3600}])
    declaration = _declaration(client_auth="client_secret_basic")
    provider = _provider(declaration, transport, store, clock)
    provider.headers_for(_source())
    _, fields, headers = transport.posts[0]
    expected = base64.b64encode(b"toolregistry:client-secret").decode("ascii")
    assert headers["Authorization"] == f"Basic {expected}"
    assert "client_secret" not in fields


def test_client_secret_is_redacted_from_errors(store, clock):
    transport = FakeTransport(
        responses=[OAuthHTTPError(400, "invalid_request", "bad client-secret value")]
    )
    provider = _provider(_declaration(), transport, store, clock)
    with pytest.raises(ReauthorizationRequired) as caught:
        provider.headers_for(_source())
    assert "client-secret" not in str(caught.value)
    assert "[redacted]" in str(caught.value)


def test_redact_removes_url_userinfo():
    assert redact("https://user:pass@host/token") == "https://host/token"


def test_redact_removes_credential_parameters():
    assert redact("refresh_token=RT&code=CODE") == (
        "refresh_token=[redacted]&code=[redacted]"
    )


def test_malformed_token_response_raises(store, clock):
    transport = FakeTransport(responses=[{"expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    with pytest.raises(OAuthClientError, match="no access_token"):
        provider.headers_for(_source())


def test_headers_for_returns_none_without_a_declaration(store, clock):
    provider = OAuthClientProvider(
        (),
        store=store,
        transport=FakeTransport(),
        clock=clock,
    )
    assert provider.headers_for(_source()) is None


def test_ambiguous_declarations_raise(store, clock):
    declarations = (
        _declaration(),
        _declaration(issuer="https://other.example.com"),
    )
    provider = OAuthClientProvider(
        declarations,
        store=store,
        transport=FakeTransport(),
        clock=clock,
    )
    with pytest.raises(OAuthClientError, match="select the same"):
        provider.headers_for(_source())


def _authorization_provider(store, clock, responses):
    transport = FakeTransport(
        metadata={
            "issuer": "https://idp.example.com",
            "authorization_endpoint": AUTH_URL,
            "token_endpoint": TOKEN_URL,
        },
        responses=responses,
    )
    declaration = _declaration(
        grant="authorization_code", token_endpoint=None, discovery_url=None
    )
    return _provider(declaration, transport, store, clock), transport


def test_start_authorization_returns_a_pkce_url(store, clock):
    provider, _ = _authorization_provider(store, clock, [])
    request = provider.start_authorization(
        SOURCE, "https://gateway.example.com/callback"
    )
    parsed = urlsplit(request.authorization_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTH_URL
    params = parse_qs(parsed.query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["toolregistry"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == [request.state]
    assert "code_verifier" not in request.to_dict()
    assert len(params["code_challenge"][0]) >= 43


def test_complete_authorization_persists_the_token(store, clock):
    provider, transport = _authorization_provider(
        store,
        clock,
        [{"access_token": "ACAT", "refresh_token": "ACRT", "expires_in": 3600}],
    )
    request = provider.start_authorization(
        SOURCE, "https://gateway.example.com/callback"
    )
    public = provider.complete_authorization("CODE", request.state)
    assert "ACAT" not in json.dumps(public)
    assert public["has_access_token"] is True
    assert transport.posts[0][1]["grant_type"] == "authorization_code"
    assert transport.posts[0][1]["code"] == "CODE"
    assert transport.posts[0][1]["code_verifier"]
    assert store.get(SOURCE).access_token == "ACAT"


def test_complete_authorization_rejects_a_reused_state(store, clock):
    provider, _ = _authorization_provider(
        store, clock, [{"access_token": "ACAT", "expires_in": 3600}]
    )
    request = provider.start_authorization(
        SOURCE, "https://gateway.example.com/callback"
    )
    provider.complete_authorization("CODE", request.state)
    with pytest.raises(OAuthStateError, match="unknown or already used"):
        provider.complete_authorization("CODE", request.state)


def test_complete_authorization_rejects_an_expired_state(store, clock):
    provider, _ = _authorization_provider(
        store, clock, [{"access_token": "ACAT", "expires_in": 3600}]
    )
    request = provider.start_authorization(
        SOURCE, "https://gateway.example.com/callback"
    )
    clock.advance(601)
    with pytest.raises(OAuthStateError, match="expired"):
        provider.complete_authorization("CODE", request.state)


def test_start_authorization_requires_the_authorization_code_grant(store, clock):
    provider = _provider(_declaration(), FakeTransport(), store, clock)
    with pytest.raises(OAuthClientError, match="client-credentials grant"):
        provider.start_authorization(SOURCE, "https://gateway.example.com/callback")


def test_replace_declarations_takes_effect(store, clock):
    provider = _provider(_declaration(), FakeTransport(), store, clock)
    assert provider.declarations
    provider.replace_declarations(())
    assert provider.declarations == ()
    assert provider.headers_for(_source()) is None


# -- real-transport behaviour -------------------------------------------------


class _RecordingServer(socketserver.ThreadingTCPServer):
    """A loopback HTTP server that records every request it receives."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, handler_cls):
        super().__init__(("127.0.0.1", 0), handler_cls)
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/token"


class _RedirectingHandler(http.server.BaseHTTPRequestHandler):
    """Answer every request with a redirect to the second server."""

    target = ""

    def do_GET(self):  # noqa: N802 - http.server naming
        self._redirect()

    def do_POST(self):  # noqa: N802 - http.server naming
        self._redirect()

    def _redirect(self):
        self.server.requests.append(
            (self.command, self.path, dict(self.headers.items()))
        )
        # Drain the body so the client does not see a reset.
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(self.server.redirect_status)
        self.send_header("Location", self.server.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # noqa: D102 - silence the test log
        pass


class _TargetHandler(http.server.BaseHTTPRequestHandler):
    """Record requests and answer with a token."""

    def do_GET(self):  # noqa: N802 - http.server naming
        self._record()

    def do_POST(self):  # noqa: N802 - http.server naming
        self._record()

    def _record(self):
        self.server.requests.append(
            (self.command, self.path, dict(self.headers.items()))
        )
        body = b'{"access_token": "AT", "expires_in": 3600}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D102 - silence the test log
        pass


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("client_auth", ["client_secret_post", "client_secret_basic"])
def test_transport_never_follows_a_redirect(status, client_auth):
    client_id, secret = "toolregistry", "s3cret"
    credential = base64.b64encode(f"{client_id}:{secret}".encode()).decode("ascii")
    headers = (
        {"Authorization": f"Basic {credential}"}
        if client_auth == "client_secret_basic"
        else {}
    )
    target_cls = type("Target", (_TargetHandler,), {})
    target = _RecordingServer(target_cls)
    source_cls = type("Source", (_RedirectingHandler,), {})
    source = _RecordingServer(source_cls)
    source.target = target.url
    source.redirect_status = status
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (source, target)
    ]
    for thread in threads:
        thread.start()
    try:
        transport = UrllibTransport()
        with pytest.raises(OAuthHTTPError) as caught:
            transport.post_form(
                source.url,
                {"grant_type": "client_credentials", "client_secret": secret},
                headers,
                5.0,
            )
    finally:
        for server in (source, target):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)

    assert caught.value.status == status
    # The redirect target must not be contacted at all: the client secret is
    # in the body or in the Authorization header, and neither may be replayed.
    assert target.requests == []
    assert len(source.requests) == 1


def test_transport_maps_a_truncated_body_to_transient():
    class Truncated(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            # Declare more bytes than are sent, then close the connection.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"access_token": ')
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *args):  # noqa: D102 - silence the test log
            pass

    server = _RecordingServer(type("Truncated2", (Truncated,), {}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(TransientOAuthError):
            UrllibTransport().get_json(server.url, 5.0)
    finally:
        server.shutdown()
        server.server_close()


def test_transport_maps_a_reset_socket_to_transient():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept_and_drop():
        connection, _ = listener.accept()
        connection.close()

    thread = threading.Thread(target=accept_and_drop, daemon=True)
    thread.start()
    try:
        with pytest.raises(TransientOAuthError):
            UrllibTransport().get_json(f"http://127.0.0.1:{port}/token", 5.0)
    finally:
        thread.join(timeout=5)
        listener.close()


# -- classification and store behaviour ---------------------------------------


def test_redirect_status_is_transient(store, clock):
    transport = FakeTransport(responses=[OAuthHTTPError(302, None, None)])
    provider = _provider(_declaration(), transport, store, clock)

    with pytest.raises(TransientOAuthError, match="HTTP 302"):
        provider.headers_for(_source())

    assert store.get(SOURCE) is None


def test_client_credentials_recovers_after_the_secret_is_corrected(store, clock):
    """A rejected client secret must not latch the source off forever."""
    transport = FakeTransport(
        responses=[
            OAuthHTTPError(401, "invalid_client", "bad secret"),
            {"access_token": "AT", "expires_in": 3600},
        ]
    )
    provider = _provider(_declaration(), transport, store, clock)

    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    assert store.get(SOURCE) is None

    # The operator fixed the secret; the next connect tries again.
    assert provider.headers_for(_source()) == {"Authorization": "Bearer AT"}
    assert len(transport.posts) == 2


def test_authorization_code_rejection_keeps_the_latch(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
            refresh_token="RT",
            expires_at=NOW - timedelta(seconds=1),
            updated_at=NOW,
        )
    )
    transport = FakeTransport(
        responses=[OAuthHTTPError(400, "invalid_grant", "revoked")]
    )
    provider = _provider(
        _declaration(grant="authorization_code"), transport, store, clock
    )

    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    # No interactive authorization can be started for this declaration, so
    # the latch stays and the failure is not retried silently.
    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    assert len(transport.posts) == 1


def test_a_token_for_another_issuer_is_not_reused(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://old-idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
            expires_at=NOW + timedelta(hours=1),
            updated_at=NOW,
        )
    )
    transport = FakeTransport(responses=[{"access_token": "NEW", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)

    assert provider.headers_for(_source()) == {"Authorization": "Bearer NEW"}
    assert len(transport.posts) == 1
    assert store.get(SOURCE).issuer == "https://idp.example.com"


def test_a_token_for_another_client_is_not_reused(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="another-client",
            access_token="OLD",
            expires_at=NOW + timedelta(hours=1),
            updated_at=NOW,
        )
    )
    transport = FakeTransport(responses=[{"access_token": "NEW", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)

    assert provider.headers_for(_source()) == {"Authorization": "Bearer NEW"}
    assert len(transport.posts) == 1


def test_clear_stored_token_removes_the_row(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="AT",
            expires_at=NOW + timedelta(hours=1),
            updated_at=NOW,
        )
    )
    provider = _provider(_declaration(), FakeTransport(), store, clock)
    provider.clear_stored_token(SOURCE)
    assert store.get(SOURCE) is None
    # A selector no declaration owns is a no-op.
    provider.clear_stored_token("namespace:unknown")


def test_echoed_refresh_token_is_redacted(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
            refresh_token="RT-SECRET",
            expires_at=NOW - timedelta(seconds=1),
            updated_at=NOW,
        )
    )
    transport = FakeTransport(
        responses=[
            OAuthHTTPError(400, "invalid_grant", "refresh token RT-SECRET is invalid")
        ]
    )
    provider = _provider(_declaration(), transport, store, clock)

    with pytest.raises(ReauthorizationRequired) as caught:
        provider.headers_for(_source())

    assert "RT-SECRET" not in str(caught.value)
    assert "[redacted]" in str(caught.value)


def test_abandoned_pending_requests_are_purged(store, clock):
    provider, _ = _authorization_provider(store, clock, [])
    first = provider.start_authorization(SOURCE, "https://gateway.example.com/callback")
    clock.advance(601)
    provider.start_authorization(SOURCE, "https://gateway.example.com/callback")

    with pytest.raises(OAuthStateError, match="unknown or already used"):
        provider.complete_authorization("CODE", first.state)
