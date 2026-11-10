"""Tests for the upstream MCP OAuth provider (#254, stage 1).

Every HTTP interaction goes through a fake transport, so nothing reaches the
network.  The store is in-memory except where a test explicitly checks disk
permissions.  The transport tests use loopback servers, because the redirect
and truncation behaviour under test is urllib's.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import socket
import socketserver
import sqlite3
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
    OAuthStoreError,
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
    # The refresh token is kept: a corrected secret can recover the row.
    assert stored.refresh_token == "RT"

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


# -- resource-server rejection, assumed expiry, moved endpoints ---------------


def _stored(store, *, refresh="RT", expires=None):
    return store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="REJECTED",
            refresh_token=refresh,
            expires_at=expires,
            updated_at=NOW,
        )
    )


def test_a_resource_server_rejection_clears_only_the_access_token(store, clock):
    """The access token goes unconditionally; the refresh token stays."""
    _stored(store, expires=NOW + timedelta(hours=1))
    provider = _provider(_declaration(), FakeTransport(), store, clock)

    assert provider.reject_at_resource_server(_source()) is True

    record = store.get(SOURCE)
    assert record.access_token is None
    assert record.expires_at is None
    assert record.refresh_token == "RT"
    assert record.rejections == 1
    assert record.needs_reauth is False


def test_a_resource_server_rejection_without_a_row_is_a_no_op(store, clock):
    provider = _provider(_declaration(), FakeTransport(), store, clock)
    assert provider.reject_at_resource_server(_source()) is False
    assert store.get(SOURCE) is None


def test_rejection_cell_authorization_code_with_refresh(store, clock):
    """access cleared, refresh kept, authorization_code -> refresh."""
    _stored(store, expires=NOW + timedelta(hours=1))
    transport = FakeTransport(responses=[{"access_token": "FRESH", "expires_in": 3600}])
    provider = _provider(
        _declaration(grant="authorization_code"), transport, store, clock
    )
    provider.reject_at_resource_server(_source())

    assert provider.headers_for(_source()) == {"Authorization": "Bearer FRESH"}
    assert transport.posts[0][1]["grant_type"] == "refresh_token"
    # A token-endpoint success proves nothing about the resource server, so
    # the counter stays until the connect path accepts the token.
    assert store.get(SOURCE).rejections == 1


def test_rejection_cell_client_credentials_with_refresh(store, clock):
    """access cleared, refresh kept, client_credentials -> refresh."""
    _stored(store, expires=NOW + timedelta(hours=1))
    transport = FakeTransport(responses=[{"access_token": "FRESH", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    provider.reject_at_resource_server(_source())

    assert provider.headers_for(_source()) == {"Authorization": "Bearer FRESH"}
    # The row carries a refresh token, so the refresh path wins over a new
    # client-credentials fetch.
    assert transport.posts[0][1]["grant_type"] == "refresh_token"


def test_rejection_cell_authorization_code_without_refresh(store, clock):
    """access cleared, no refresh, authorization_code -> reauthorize on strike 2."""
    _stored(store, refresh=None, expires=NOW + timedelta(hours=1))
    provider = _provider(
        _declaration(grant="authorization_code"), FakeTransport(), store, clock
    )
    provider.reject_at_resource_server(_source())

    # First strike: no refresh token, so there is nothing to do yet.
    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())

    # Second strike escalates, and the reason names the rejections.
    _stored(store, refresh=None, expires=None)
    store.mark_rejected(SOURCE)
    with pytest.raises(ReauthorizationRequired, match="rejected by the MCP server"):
        provider.headers_for(_source())
    assert store.get(SOURCE).needs_reauth is True


def test_rejection_cell_client_credentials_without_refresh(store, clock):
    """access cleared, no refresh, client_credentials -> refetch at once.

    This is the cell that pins the rule: the clear must happen on every
    rejection, not on the second one, or this source never recovers.
    """
    _stored(store, refresh=None, expires=NOW + timedelta(hours=1))
    transport = FakeTransport(responses=[{"access_token": "FRESH", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    provider.reject_at_resource_server(_source())

    assert provider.headers_for(_source()) == {"Authorization": "Bearer FRESH"}
    assert len(transport.posts) == 1
    assert store.get(SOURCE).rejections == 1


def test_client_credentials_restarts_after_the_rejection_bound(store, clock):
    """At the bound a client-credentials row is dropped, then fetched again."""
    _stored(store, refresh=None, expires=None)
    store.mark_rejected(SOURCE)
    store.mark_rejected(SOURCE)
    assert store.get(SOURCE).rejections == 2

    transport = FakeTransport(responses=[{"access_token": "FRESH", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    assert provider.headers_for(_source()) == {"Authorization": "Bearer FRESH"}
    assert store.get(SOURCE).access_token == "FRESH"
    assert store.get(SOURCE).rejections == 0


def test_put_preserves_the_rejection_counter(store, clock):
    """A refresh must not reset the counter, or the bound never trips."""
    _stored(store, refresh="RT", expires=None)
    store.mark_rejected(SOURCE)
    transport = FakeTransport(responses=[{"access_token": "FRESH", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    provider.headers_for(_source())

    assert store.get(SOURCE).rejections == 1


def test_accept_at_resource_server_resets_the_counter(store, clock):
    _stored(store, expires=None)
    store.mark_rejected(SOURCE)
    provider = _provider(_declaration(), FakeTransport(), store, clock)
    provider.accept_at_resource_server(_source())
    assert store.get(SOURCE).rejections == 0


def test_a_token_without_an_expiry_is_assumed_to_expire(store, clock):
    """``expires_at=None`` is bounded by the row's ``updated_at``.

    ``put`` stamps ``updated_at`` from the clock, so the row is aged by
    advancing the clock, which is exactly what a real deployment does.
    """
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="OLD",
            refresh_token="RT",
            expires_at=None,
        )
    )
    clock.advance(7200)
    transport = FakeTransport(responses=[{"access_token": "NEW", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)

    assert provider.headers_for(_source()) == {"Authorization": "Bearer NEW"}
    assert transport.posts[0][1]["grant_type"] == "refresh_token"


def test_a_fresh_token_without_an_expiry_is_still_used(store, clock):
    """The assumption is an age limit, not a reason to refresh immediately."""
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="AT",
            expires_at=None,
        )
    )
    clock.advance(300)
    transport = FakeTransport()
    provider = _provider(_declaration(), transport, store, clock)
    assert provider.headers_for(_source()) == {"Authorization": "Bearer AT"}
    assert transport.posts == []


def test_the_assumed_lifetime_is_configurable(store, clock):
    store.put(
        TokenRecord(
            source_id=SOURCE,
            issuer="https://idp.example.com",
            client_id="toolregistry",
            access_token="AT",
            expires_at=None,
        )
    )
    clock.advance(300)
    provider = OAuthClientProvider(
        (_declaration(grant="authorization_code"),),
        store=store,
        transport=FakeTransport(),
        clock=clock,
        assumed_ttl=60.0,
    )
    with pytest.raises(ReauthorizationRequired):
        # No refresh token and an authorization-code grant: nothing to do.
        provider.access_token(provider.declarations[0])


class _MovingEndpointTransport:
    """Discover an endpoint, then move it after the first failed POST."""

    def __init__(self, *, first_status=404):
        self.first_status = first_status
        self.gets = 0
        self.posts: list[str] = []

    def get_json(self, url, timeout):
        self.gets += 1
        if self.gets == 1:
            return {"token_endpoint": "https://idp.example.com/old/token"}
        return {"token_endpoint": "https://idp.example.com/new/token"}

    def post_form(self, url, fields, headers, timeout):
        self.posts.append(url)
        if url.endswith("/old/token"):
            raise OAuthHTTPError(self.first_status, None, None)
        return {"access_token": "AT", "expires_in": 3600}


@pytest.mark.parametrize("status", [404, 410])
def test_a_moved_discovered_endpoint_is_rediscovered_once(store, clock, status):
    transport = _MovingEndpointTransport(first_status=status)
    provider = _provider(_declaration(token_endpoint=None), transport, store, clock)

    assert provider.headers_for(_source()) == {"Authorization": "Bearer AT"}
    assert transport.posts == [
        "https://idp.example.com/old/token",
        "https://idp.example.com/new/token",
    ]
    assert transport.gets == 2


def test_a_second_404_from_the_discovered_endpoint_is_definitive(store, clock):
    class Always404(_MovingEndpointTransport):
        def post_form(self, url, fields, headers, timeout):
            self.posts.append(url)
            raise OAuthHTTPError(404, None, None)

    transport = Always404()
    provider = _provider(_declaration(token_endpoint=None), transport, store, clock)

    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    # Discovered once, retried once, then classified.  The grant is
    # client_credentials, so the rejected row is dropped rather than latched.
    assert len(transport.posts) == 2
    assert store.get(SOURCE) is None


def test_an_explicit_token_endpoint_is_not_rediscovered(store, clock):
    class Always404:
        def __init__(self):
            self.gets = 0
            self.posts: list[str] = []

        def get_json(self, url, timeout):
            self.gets += 1
            return {"token_endpoint": "https://idp.example.com/elsewhere/token"}

        def post_form(self, url, fields, headers, timeout):
            self.posts.append(url)
            raise OAuthHTTPError(404, None, None)

    transport = Always404()
    provider = _provider(_declaration(), transport, store, clock)

    with pytest.raises(TransientOAuthError):
        provider.headers_for(_source())
    # An explicit endpoint is never re-discovered: nothing else could replace
    # it, so a 404 is the operator's endpoint being wrong -- a config fault,
    # not a verdict on the grant, and it must not latch the source.
    assert transport.gets == 0
    assert transport.posts == ["https://idp.example.com/token"]
    assert store.get(SOURCE) is None


def test_abandoned_pending_requests_are_purged(store, clock):
    provider, _ = _authorization_provider(store, clock, [])
    first = provider.start_authorization(SOURCE, "https://gateway.example.com/callback")
    clock.advance(601)
    provider.start_authorization(SOURCE, "https://gateway.example.com/callback")

    with pytest.raises(OAuthStateError, match="unknown or already used"):
        provider.complete_authorization("CODE", first.state)


# -- token binding, latch recovery, metadata, redaction ----------------------


def test_a_token_is_not_reused_for_another_source_url(store, clock):
    """A repointed source must not be served the old host's token."""
    transport = FakeTransport(
        responses=[
            {"access_token": "A", "expires_in": 3600},
            {"access_token": "B", "expires_in": 3600},
        ]
    )
    provider = _provider(_declaration(), transport, store, clock)

    first = provider.headers_for(_source(url="https://a.example.com/mcp"))
    again = provider.headers_for(_source(url="https://a.example.com/mcp"))
    moved = provider.headers_for(_source(url="https://b.example.com/mcp"))

    assert first == {"Authorization": "Bearer A"}
    assert again == first
    assert moved == {"Authorization": "Bearer B"}
    assert len(transport.posts) == 2
    assert store.get(SOURCE).source_url == "https://b.example.com/mcp"


def test_the_minted_token_records_its_target(store, clock):
    transport = FakeTransport(responses=[{"access_token": "A", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    provider.headers_for(_source(url="https://mint.example.com/mcp"))

    record = store.get(SOURCE)
    assert record.source_url == "https://mint.example.com/mcp"
    assert record.grant == "client_credentials"
    assert record.token_endpoint == TOKEN_URL
    assert record.requested_scopes == ("read",)
    assert record.secret_digest.startswith("pbkdf2_sha256$")


def test_a_credential_change_clears_the_latch_with_one_refresh(store, clock):
    """Correcting the secret must recover without a new authorization."""
    provider, transport = _authorization_provider(
        store,
        clock,
        [
            {"access_token": "NEW", "refresh_token": "RT", "expires_in": 1},
            OAuthHTTPError(401, "invalid_client", "bad secret"),
            {"access_token": "FIXED", "expires_in": 3600},
        ],
    )
    request = provider.start_authorization(
        _source(), "https://gateway.example.com/callback"
    )
    provider.complete_authorization("CODE", request.state)
    clock.advance(10)

    with pytest.raises(ReauthorizationRequired, match="invalid_client"):
        provider.headers_for(_source())
    assert store.get(SOURCE).needs_reauth is True
    assert store.get(SOURCE).refresh_token == "RT"

    # Only the secret changes.
    provider.replace_declarations(
        (
            _declaration(
                grant="authorization_code",
                client_secret="corrected-secret",
                token_endpoint=None,
                discovery_url=None,
            ),
        )
    )
    assert provider.headers_for(_source()) == {"Authorization": "Bearer FIXED"}
    assert store.get(SOURCE).needs_reauth is False
    # One refresh after the change, not a full re-authorization.
    assert len(transport.posts) == 3


def test_an_unchanged_declaration_keeps_the_latch(store, clock):
    provider, transport = _authorization_provider(
        store,
        clock,
        [
            {"access_token": "NEW", "refresh_token": "RT", "expires_in": 1},
            OAuthHTTPError(400, "invalid_grant", "revoked"),
        ],
    )
    request = provider.start_authorization(
        _source(), "https://gateway.example.com/callback"
    )
    provider.complete_authorization("CODE", request.state)
    clock.advance(10)

    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())

    provider.replace_declarations(provider.declarations)
    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    assert len(transport.posts) == 2


@pytest.mark.parametrize("status", [408, 425])
def test_request_timeout_statuses_are_transient(store, clock, status):
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
    transport = FakeTransport(responses=[OAuthHTTPError(status, None, "timeout")])
    provider = _provider(
        _declaration(grant="authorization_code"), transport, store, clock
    )

    with pytest.raises(TransientOAuthError):
        provider.headers_for(_source())
    assert store.get(SOURCE).needs_reauth is False


class _SequenceMetadata:
    """Return one scripted metadata document per GET, then repeat the last."""

    def __init__(self, documents):
        self.documents = list(documents)
        self.gets = 0

    def get_json(self, url, timeout):
        index = min(self.gets, len(self.documents) - 1)
        self.gets += 1
        return self.documents[index]

    def post_form(self, url, fields, headers, timeout):
        self.posts = getattr(self, "posts", [])
        self.posts.append(url)
        return {"access_token": "AT", "expires_in": 3600}


def test_metadata_for_another_issuer_is_rejected(store, clock):
    transport = _SequenceMetadata(
        [
            {
                "issuer": "https://evil.example.com",
                "token_endpoint": "https://evil.example.com/token",
            }
        ]
    )
    provider = _provider(_declaration(token_endpoint=None), transport, store, clock)

    with pytest.raises(TransientOAuthError, match="could not discover"):
        provider.headers_for(_source())
    assert not hasattr(transport, "posts") or transport.posts == []


def test_metadata_endpoint_must_be_http(store, clock):
    transport = _SequenceMetadata(
        [
            {
                "issuer": "https://idp.example.com",
                "token_endpoint": "file:///etc/passwd",
            }
        ]
    )
    provider = _provider(_declaration(token_endpoint=None), transport, store, clock)

    with pytest.raises(TransientOAuthError, match="could not discover"):
        provider.headers_for(_source())


def test_an_unusable_metadata_document_is_not_cached(store, clock):
    """A partial document must not stop the next candidate from answering."""
    transport = _SequenceMetadata(
        [
            {},
            {
                "issuer": "https://idp.example.com",
                "token_endpoint": "https://idp.example.com/token",
            },
        ]
    )
    provider = _provider(_declaration(token_endpoint=None), transport, store, clock)

    assert provider.headers_for(_source()) == {"Authorization": "Bearer AT"}
    assert transport.gets == 2


def test_a_one_character_secret_does_not_rewrite_the_message():
    assert redact("OAuth request rejected", "r") == "OAuth request rejected"


def test_the_basic_credential_is_redacted_from_an_error(store, clock):
    credential = base64.b64encode(b"toolregistry:client-secret").decode("ascii")
    transport = FakeTransport(
        responses=[
            OAuthHTTPError(
                401, "invalid_client", f"credential Basic {credential} refused"
            )
        ]
    )
    provider = _provider(
        _declaration(client_auth="client_secret_basic"), transport, store, clock
    )

    with pytest.raises(ReauthorizationRequired) as caught:
        provider.headers_for(_source())

    message = str(caught.value)
    assert credential not in message
    assert "[redacted]" in message


def test_complete_authorization_holds_the_token_lock(store, clock):
    """The exchange and the write must be atomic against a refresh."""
    provider, transport = _authorization_provider(store, clock, [])
    observed: dict[str, bool] = {}

    def post_form(url, fields, headers, timeout):
        observed["locked"] = provider._token_lock(SOURCE).locked()
        return {"access_token": "ACAT", "refresh_token": "ACRT", "expires_in": 3600}

    transport.post_form = post_form
    request = provider.start_authorization(
        _source(), "https://gateway.example.com/callback"
    )
    record = provider.complete_authorization("CODE", request.state)

    assert observed["locked"] is True
    assert record["has_access_token"] is True
    assert store.get(SOURCE).source_url == "https://mcp.example.com/mcp"


class _LockedStore:
    """A store whose every read fails the way a locked database does."""

    def get(self, source_id):
        raise sqlite3.OperationalError("database is locked")

    def put(self, record):  # pragma: no cover - the read fails first
        raise AssertionError("put must not run")

    def clear(self, source_id):  # pragma: no cover - the read fails first
        raise AssertionError("clear must not run")


def test_a_store_failure_is_a_typed_transient_error(store, clock):
    provider = _provider(_declaration(), FakeTransport(), _LockedStore(), clock)

    with pytest.raises(OAuthStoreError) as caught:
        provider.headers_for(_source())

    assert isinstance(caught.value, TransientOAuthError)
    assert caught.value.reauth_required is False


def test_a_stored_token_survives_a_temporary_store_failure(store, clock):
    transport = FakeTransport(responses=[{"access_token": "A", "expires_in": 3600}])
    provider = _provider(_declaration(), transport, store, clock)
    assert provider.headers_for(_source()) == {"Authorization": "Bearer A"}

    provider._store = _LockedStore()
    with pytest.raises(OAuthStoreError):
        provider.headers_for(_source())

    provider._store = store
    assert provider.headers_for(_source()) == {"Authorization": "Bearer A"}
    assert len(transport.posts) == 1


def test_the_secret_fingerprint_is_salted_and_iterated():
    """A store reader must not recover a low-entropy client secret cheaply."""
    from toolregistry_server.oauth.upstream.provider import (
        _secret_digest,
        _secret_digest_matches,
    )

    declaration = _declaration(client_secret="x", client_secret_env="MCP_SECRET")
    fingerprint = _secret_digest(declaration)
    algorithm, iterations, _salt, _digest = fingerprint.split("$")

    assert algorithm == "pbkdf2_sha256"
    assert int(iterations) >= 50_000
    # Not a single fast hash of the secret or of a component.
    for component in ("x", "MCP_SECRET", "client_secret_post"):
        assert hashlib.sha256(component.encode()).hexdigest() not in fingerprint
    assert fingerprint != hashlib.sha256(b"x").hexdigest()
    # Two mints of the same declaration differ: the salt is per store row.
    assert _secret_digest(declaration) != fingerprint

    # Verification re-derives with the stored salt, so it is deterministic
    # even though the stored strings differ.
    assert _secret_digest_matches(declaration, fingerprint)
    assert _secret_digest_matches(declaration, fingerprint)
    assert not _secret_digest_matches(_declaration(client_secret="y"), fingerprint)
    assert not _secret_digest_matches(
        declaration.__class__(
            source=declaration.source,
            client_id=declaration.client_id,
            issuer=declaration.issuer,
            client_secret="x",
            client_secret_env=None,
            scopes=declaration.scopes,
            token_endpoint=declaration.token_endpoint,
        ),
        fingerprint,
    )
    # A legacy row has no fingerprint; nothing to compare.
    assert _secret_digest_matches(declaration, "")


def test_the_secret_never_appears_in_a_raw_row(tmp_path):
    """The amended store contract, asserted against the database itself."""
    store = TokenStore(str(tmp_path / "tokens.db"), clock=lambda: NOW)
    secret = "super-secret-value-123"
    try:
        transport = FakeTransport(
            responses=[{"access_token": "AT", "expires_in": 3600}]
        )
        provider = _provider(
            _declaration(client_secret=secret), transport, store, lambda: NOW
        )
        provider.headers_for(_source())
    finally:
        store.close()

    connection = sqlite3.connect(str(tmp_path / "tokens.db"))
    try:
        rows = connection.execute("SELECT * FROM oauth_client_tokens").fetchall()
    finally:
        connection.close()
    assert rows
    for row in rows:
        assert secret not in "|".join(str(value) for value in row)


def test_a_noop_reconnect_keeps_the_minted_token(store, clock):
    """The fingerprint check must not discard a token on an ordinary read."""
    provider, transport = _authorization_provider(
        store,
        clock,
        [{"access_token": "ACAT", "refresh_token": "ACRT", "expires_in": 3600}],
    )
    request = provider.start_authorization(
        _source(), "https://gateway.example.com/callback"
    )
    provider.complete_authorization("CODE", request.state)

    for _ in range(2):
        assert provider.headers_for(_source()) == {"Authorization": "Bearer ACAT"}
    assert len(transport.posts) == 1
    assert store.get(SOURCE).needs_reauth is False


def test_a_dead_refresh_token_does_not_loop_after_a_secret_change(store, clock):
    """A corrected secret gets one attempt; a dead grant then re-latches."""
    provider, transport = _authorization_provider(
        store,
        clock,
        [
            {"access_token": "NEW", "refresh_token": "RT", "expires_in": 1},
            OAuthHTTPError(400, "invalid_grant", "revoked"),
            OAuthHTTPError(400, "invalid_grant", "revoked"),
        ],
    )
    request = provider.start_authorization(
        _source(), "https://gateway.example.com/callback"
    )
    provider.complete_authorization("CODE", request.state)
    clock.advance(10)

    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    assert store.get(SOURCE).needs_reauth is True
    assert store.get(SOURCE).refresh_token == "RT"

    provider.replace_declarations(
        (
            _declaration(
                grant="authorization_code",
                client_secret="corrected-secret",
                token_endpoint=None,
                discovery_url=None,
            ),
        )
    )
    # One refresh attempt with the dead token, then the new latch holds.
    with pytest.raises(ReauthorizationRequired, match="invalid_grant"):
        provider.headers_for(_source())
    assert store.get(SOURCE).needs_reauth is True
    assert len(transport.posts) == 3

    # The same corrected credential must not clear the new verdict again.
    with pytest.raises(ReauthorizationRequired):
        provider.headers_for(_source())
    assert len(transport.posts) == 3
