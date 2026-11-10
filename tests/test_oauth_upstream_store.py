"""Tests for the upstream MCP OAuth token store (#254, stage 1)."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from toolregistry_server.oauth.upstream.store import (
    PendingAuthorization,
    TokenRecord,
    TokenStore,
    utcnow,
)

SOURCE = "namespace:example"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    with TokenStore(":memory:", clock=lambda: NOW) as opened:
        yield opened


def _record(**overrides):
    values: dict[str, Any] = {
        "source_id": SOURCE,
        "issuer": "https://idp.example.com",
        "client_id": "toolregistry",
        "scopes": ("read", "write"),
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": NOW + timedelta(hours=1),
        "updated_at": NOW,
    }
    values.update(overrides)
    return TokenRecord(**values)


def test_round_trip(store):
    store.put(_record())
    stored = store.get(SOURCE)
    assert stored is not None
    assert stored.access_token == "access-token"
    assert stored.refresh_token == "refresh-token"
    assert stored.scopes == ("read", "write")
    assert stored.expires_at == NOW + timedelta(hours=1)
    assert stored.needs_reauth is False
    assert stored.error is None


def test_missing_row_returns_none(store):
    assert store.get("namespace:missing") is None


def test_put_clears_a_previous_reauth_marker(store):
    store.mark_reauth(SOURCE, "invalid_grant", issuer="issuer", client_id="client")
    assert store.get(SOURCE).needs_reauth is True
    store.put(_record())
    stored = store.get(SOURCE)
    assert stored.needs_reauth is False
    assert stored.error is None
    assert stored.access_token == "access-token"


def test_mark_reauth_inserts_a_row_and_clears_tokens(store):
    store.put(_record())
    store.mark_reauth(SOURCE, "revoked")
    stored = store.get(SOURCE)
    assert stored.needs_reauth is True
    assert stored.error == "revoked"
    assert stored.access_token is None
    assert stored.refresh_token is None


def test_mark_reauth_on_a_missing_row(store):
    store.mark_reauth(
        SOURCE,
        "invalid_client",
        issuer="https://idp.example.com",
        client_id="toolregistry",
    )
    stored = store.get(SOURCE)
    assert stored is not None
    assert stored.needs_reauth is True
    assert stored.issuer == "https://idp.example.com"
    assert stored.client_id == "toolregistry"


def test_public_view_and_repr_omit_tokens(store):
    store.put(_record())
    stored = store.get(SOURCE)
    public = stored.to_public_dict()
    assert "access-token" not in json.dumps(public)
    assert "refresh-token" not in json.dumps(public)
    assert public["has_access_token"] is True
    assert public["has_refresh_token"] is True
    assert "access-token" not in repr(stored)
    assert "refresh-token" not in repr(stored)


def test_is_expiring_uses_the_skew():
    record = _record(expires_at=NOW + timedelta(seconds=30))
    assert record.is_expiring(NOW, 60)
    assert not record.is_expiring(NOW, 10)


def test_is_expiring_ignores_a_missing_expiry():
    record = _record(expires_at=None)
    assert not record.is_expiring(NOW, 60)


def test_pending_authorization_round_trip(store):
    pending = PendingAuthorization(
        state="state-value",
        source_id=SOURCE,
        code_verifier="verifier-value",
        redirect_uri="https://gateway.example.com/callback",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    store.put_pending(pending)
    popped = store.pop_pending("state-value")
    assert popped == pending
    # The row is consumed: a replayed callback finds nothing.
    assert store.pop_pending("state-value") is None
    assert "verifier-value" not in repr(popped)


def test_purge_expired_pending(store):
    store.put_pending(
        PendingAuthorization(
            state="old",
            source_id=SOURCE,
            code_verifier="verifier",
            redirect_uri="https://gateway.example.com/callback",
            created_at=NOW - timedelta(hours=1),
            expires_at=NOW - timedelta(minutes=1),
        )
    )
    store.put_pending(
        PendingAuthorization(
            state="fresh",
            source_id=SOURCE,
            code_verifier="verifier",
            redirect_uri="https://gateway.example.com/callback",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )
    )
    assert store.purge_expired_pending() == 1
    assert store.pop_pending("old") is None
    assert store.pop_pending("fresh") is not None


def test_file_and_directory_permissions(tmp_path):
    path = tmp_path / "state" / "tokens.db"
    with TokenStore(str(path)) as opened:
        assert opened.get(SOURCE) is None
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_wal_is_enabled_for_a_file_store(tmp_path):
    path = tmp_path / "tokens.db"
    with TokenStore(str(path)) as opened:
        mode = opened._connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_empty_path_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        TokenStore("")


def test_clock_is_injectable(store):
    assert store._clock() == NOW


def test_utcnow_is_aware():
    assert utcnow().tzinfo is not None


# -- rejection counter, assumed expiry, schema migration ---------------------

_OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_client_tokens (
    source_id TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    client_id TEXT NOT NULL,
    scopes TEXT NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    token_type TEXT NOT NULL DEFAULT 'Bearer',
    expires_at TEXT,
    updated_at TEXT NOT NULL,
    needs_reauth INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS oauth_client_pending (
    state TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def test_mark_rejected_clears_the_access_token_and_counts(tmp_path):
    with TokenStore(":memory:", clock=lambda: NOW) as store:
        store.put(
            TokenRecord(
                source_id=SOURCE,
                issuer="https://idp.example.com",
                client_id="toolregistry",
                access_token="AT",
                refresh_token="RT",
                expires_at=NOW + timedelta(hours=1),
                updated_at=NOW,
            )
        )
        store.mark_rejected(SOURCE)

        record = store.get(SOURCE)
        assert record.access_token is None
        assert record.expires_at is None
        assert record.refresh_token == "RT"
        assert record.rejections == 1
        assert record.needs_reauth is False

        store.mark_rejected(SOURCE)
        assert store.get(SOURCE).rejections == 2


def test_mark_rejected_leaves_a_missing_row_missing():
    with TokenStore(":memory:") as store:
        store.mark_rejected("namespace:absent")
        assert store.get("namespace:absent") is None


def test_put_carries_the_rejection_counter_forward():
    with TokenStore(":memory:", clock=lambda: NOW) as store:
        store.mark_rejected(SOURCE)  # creates nothing; use a real row below
        store.put(
            TokenRecord(
                source_id=SOURCE,
                issuer="https://idp.example.com",
                client_id="toolregistry",
                access_token="AT",
            )
        )
        store.mark_rejected(SOURCE)
        assert store.get(SOURCE).rejections == 1

        store.put(
            TokenRecord(
                source_id=SOURCE,
                issuer="https://idp.example.com",
                client_id="toolregistry",
                access_token="AT2",
            )
        )
        # A successful token-endpoint call is not proof the resource server
        # accepts it, so the counter survives until the connect path resets it.
        assert store.get(SOURCE).rejections == 1
        assert store.get(SOURCE).access_token == "AT2"


def test_reset_rejections_clears_the_counter():
    with TokenStore(":memory:", clock=lambda: NOW) as store:
        store.put(
            TokenRecord(
                source_id=SOURCE,
                issuer="https://idp.example.com",
                client_id="toolregistry",
                access_token="AT",
            )
        )
        store.mark_rejected(SOURCE)
        store.reset_rejections(SOURCE)
        assert store.get(SOURCE).rejections == 0


def test_a_token_without_expiry_expires_after_the_assumed_lifetime():
    record = TokenRecord(
        source_id=SOURCE,
        issuer="https://idp.example.com",
        client_id="toolregistry",
        access_token="AT",
        expires_at=None,
        updated_at=NOW - timedelta(hours=2),
    )
    assert record.is_expiring(NOW, 0.0) is True
    # The window is anchored to updated_at, so it does not slide forward.
    assert record.is_expiring(NOW + timedelta(days=1), 0.0) is True
    fresh = TokenRecord(
        source_id=SOURCE,
        issuer="https://idp.example.com",
        client_id="toolregistry",
        access_token="AT",
        expires_at=None,
        updated_at=NOW - timedelta(minutes=5),
    )
    assert fresh.is_expiring(NOW, 0.0) is False
    assert fresh.is_expiring(NOW, 0.0, assumed_ttl=None) is False
    assert fresh.is_expiring(NOW, 0.0, assumed_ttl=60.0) is True


def test_an_old_database_gains_the_rejection_column(tmp_path):
    """A pre-change file must keep loading, not fail on the new column."""
    import sqlite3

    path = tmp_path / "tokens.db"
    connection = sqlite3.connect(str(path))
    connection.executescript(_OLD_SCHEMA)
    connection.execute(
        """
        INSERT INTO oauth_client_tokens (
            source_id, issuer, client_id, scopes, access_token, refresh_token,
            token_type, expires_at, updated_at, needs_reauth, error
        ) VALUES (?, ?, ?, '[]', ?, ?, 'Bearer', NULL, ?, 0, NULL)
        """,
        (
            SOURCE,
            "https://idp.example.com",
            "toolregistry",
            "AT",
            "RT",
            NOW.isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    with TokenStore(str(path), clock=lambda: NOW) as store:
        record = store.get(SOURCE)
        assert record is not None
        assert record.access_token == "AT"
        assert record.rejections == 0
        # The new column is writable on the migrated table.
        store.mark_rejected(SOURCE)
        assert store.get(SOURCE).rejections == 1
