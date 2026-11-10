"""On-disk token store for upstream MCP OAuth.

One SQLite database holds one row per protected MCP source plus the
short-lived authorization-code requests the panel will drive.  The store is
deliberately small and has no HTTP surface: callers read a token to put it in
a request header, and nothing here is ever serialised into an API response.

Security notes:

* the database file is created ``0600`` and its directory ``0700``; the WAL
  and shared-memory sidecars are restricted the same way;
* the OAuth client secret is never stored here.  It stays in the
  configuration file (preferably behind ``client_secret_env``);
* access and refresh tokens must be replayed, so they cannot be hashed.
  They are protected by file permissions, not by a hash.  A value that only
  needs verification, such as an API key, belongs in
  :mod:`toolregistry_server.keystore` instead;
* :meth:`TokenRecord.to_public_dict` and :meth:`TokenRecord.__repr__` omit
  both tokens, so a status payload or a debug log cannot leak them.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "PendingAuthorization",
    "TokenRecord",
    "TokenStore",
    "utcnow",
]

_SCHEMA = """
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


def utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def _format_time(value: datetime | None) -> str | None:
    """Serialise an aware datetime as an ISO 8601 string."""
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _parse_time(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp, tolerating a missing value."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class TokenRecord:
    """A stored token set for one MCP source.

    Attributes:
        source_id: Selector the declaration was written under.
        issuer: Authorization-server issuer the token came from.
        client_id: OAuth client the token was issued to.
        scopes: Scopes the token was issued for.
        access_token: Bearer token, or ``None`` after a re-authorization
            requirement cleared it.
        refresh_token: Refresh token, or ``None``.
        token_type: Token type from the response, normally ``Bearer``.
        expires_at: Absolute expiry, or ``None`` when the server did not
            state one.
        updated_at: When the row was last written.
        needs_reauth: ``True`` after a definitive refresh or exchange
            failure; the source must be authorized again.
        error: Redacted reason for :attr:`needs_reauth`.
    """

    source_id: str
    issuer: str
    client_id: str
    scopes: tuple[str, ...] = ()
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    updated_at: datetime = field(default_factory=utcnow)
    needs_reauth: bool = False
    error: str | None = None

    def is_expiring(self, now: datetime, skew: float) -> bool:
        """Whether the access token is expired or within *skew* seconds.

        A token with no stated expiry is never considered expiring.
        """
        if self.expires_at is None:
            return False
        return now >= self.expires_at - timedelta(seconds=skew)

    def to_public_dict(self) -> dict[str, Any]:
        """Return a status view with no token material."""
        return {
            "source": self.source_id,
            "issuer": self.issuer,
            "client_id": self.client_id,
            "scopes": list(self.scopes),
            "token_type": self.token_type,
            "expires_at": _format_time(self.expires_at),
            "updated_at": _format_time(self.updated_at),
            "needs_reauth": self.needs_reauth,
            "error": self.error,
            "has_access_token": bool(self.access_token),
            "has_refresh_token": bool(self.refresh_token),
        }


@dataclass(frozen=True)
class PendingAuthorization:
    """An authorization-code request waiting for its callback.

    Attributes:
        state: Opaque value the callback must present.
        source_id: Selector of the source being authorized.
        code_verifier: PKCE verifier.  It stays on disk, never in a response.
        redirect_uri: Redirect URI sent with the authorization request.
        created_at: When the request started.
        expires_at: After this instant the request is refused.
    """

    state: str
    source_id: str
    code_verifier: str = field(repr=False)
    redirect_uri: str
    created_at: datetime
    expires_at: datetime


def _prepare_directory(database: str) -> None:
    """Create the store's directory owner-only.

    Only directories this call creates are chmodded: an existing parent is
    the operator's to manage, and tightening it could break an unrelated
    service that shares it.
    """
    parent = Path(database).parent
    missing: list[Path] = []
    cursor = parent
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in missing:
        with contextlib.suppress(OSError):
            os.chmod(directory, 0o700)


def _create_private_file(database: str) -> None:
    """Create the database file with mode 0600 before SQLite opens it."""
    if Path(database).exists():
        return
    descriptor = os.open(database, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)


def _restrict_permissions(database: str) -> None:
    """Make the database and its WAL sidecars owner-only."""
    for candidate in (database, f"{database}-wal", f"{database}-shm"):
        with contextlib.suppress(OSError):
            os.chmod(candidate, 0o600)


class TokenStore:
    """SQLite-backed store for upstream MCP OAuth tokens.

    Args:
        path: Database path.  ``":memory:"`` is supported for tests.
        clock: Callable returning the current aware datetime.  Tests use it
            to move expiry boundaries without sleeping.

    The connection is shared and guarded by a re-entrant lock; SQLite's
    ``check_same_thread`` is disabled because registration runs on worker
    threads and the panel may read status from the event loop.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        database = os.fspath(path)
        if not database:
            raise ValueError("token-store path must not be empty")
        self._path = database
        self._clock = clock or utcnow
        self._lock = threading.RLock()
        if database != ":memory:":
            _prepare_directory(database)
            _create_private_file(database)
        self._connection = sqlite3.connect(database, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(_SCHEMA)
            if database != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.commit()
        if database != ":memory:":
            _restrict_permissions(database)

    def close(self) -> None:
        """Close the database connection.  The store is unusable afterwards."""
        with self._lock:
            self._connection.close()

    def __enter__(self) -> TokenStore:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def get(self, source_id: str) -> TokenRecord | None:
        """Return the stored record for *source_id*, or ``None``."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM oauth_client_tokens WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def put(self, record: TokenRecord) -> TokenRecord:
        """Insert or replace *record* and return the stored form.

        The write clears ``needs_reauth`` and the stored error: a new token
        is a successful authorization.
        """
        stored = TokenRecord(
            source_id=record.source_id,
            issuer=record.issuer,
            client_id=record.client_id,
            scopes=tuple(record.scopes),
            access_token=record.access_token,
            refresh_token=record.refresh_token,
            token_type=record.token_type or "Bearer",
            expires_at=record.expires_at,
            updated_at=self._clock(),
            needs_reauth=False,
            error=None,
        )
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO oauth_client_tokens (
                    source_id, issuer, client_id, scopes, access_token,
                    refresh_token, token_type, expires_at, updated_at,
                    needs_reauth, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                ON CONFLICT(source_id) DO UPDATE SET
                    issuer = excluded.issuer,
                    client_id = excluded.client_id,
                    scopes = excluded.scopes,
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    token_type = excluded.token_type,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at,
                    needs_reauth = 0,
                    error = NULL
                """,
                _record_values(stored),
            )
            self._connection.commit()
        return stored

    def mark_reauth(
        self,
        source_id: str,
        error: str,
        *,
        issuer: str = "",
        client_id: str = "",
    ) -> None:
        """Record that *source_id* must be authorized again.

        The access and refresh tokens are cleared.  Keeping either would let
        a later connect fall back to a credential the server has already
        rejected.  The row is inserted when it does not exist yet, so a
        failure on the very first fetch is visible too.

        Args:
            source_id: Selector the declaration is written under.
            error: Redacted failure text.
            issuer: Issuer to record when the row is new.
            client_id: Client id to record when the row is new.
        """
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO oauth_client_tokens (
                    source_id, issuer, client_id, scopes, updated_at,
                    needs_reauth, error
                ) VALUES (?, ?, ?, '[]', ?, 1, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    access_token = NULL,
                    refresh_token = NULL,
                    needs_reauth = 1,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (source_id, issuer, client_id, _format_time(self._clock()), error),
            )
            self._connection.commit()

    def clear(self, source_id: str) -> None:
        """Delete the stored record for *source_id*."""
        with self._lock:
            self._connection.execute(
                "DELETE FROM oauth_client_tokens WHERE source_id = ?", (source_id,)
            )
            self._connection.commit()

    def put_pending(self, pending: PendingAuthorization) -> None:
        """Store an authorization request awaiting its callback."""
        with self._lock:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO oauth_client_pending (
                    state, source_id, code_verifier, redirect_uri,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    pending.state,
                    pending.source_id,
                    pending.code_verifier,
                    pending.redirect_uri,
                    _format_time(pending.created_at),
                    _format_time(pending.expires_at),
                ),
            )
            self._connection.commit()

    def pop_pending(self, state: str) -> PendingAuthorization | None:
        """Remove and return the request for *state*, or ``None``.

        The row is consumed even when it has expired, so a replayed callback
        cannot succeed on a second attempt.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM oauth_client_pending WHERE state = ?", (state,)
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "DELETE FROM oauth_client_pending WHERE state = ?", (state,)
            )
            self._connection.commit()
        return _pending_from_row(row)

    def purge_expired_pending(self, now: datetime | None = None) -> int:
        """Delete expired authorization requests and return how many went."""
        moment = now or self._clock()
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM oauth_client_pending WHERE expires_at <= ?",
                (_format_time(moment),),
            )
            self._connection.commit()
            return cursor.rowcount


def _record_values(record: TokenRecord) -> tuple[Any, ...]:
    """Return the positional values for one token row."""
    return (
        record.source_id,
        record.issuer,
        record.client_id,
        json.dumps(list(record.scopes), separators=(",", ":")),
        record.access_token,
        record.refresh_token,
        record.token_type,
        _format_time(record.expires_at),
        _format_time(record.updated_at),
    )


def _record_from_row(row: sqlite3.Row) -> TokenRecord:
    """Build a :class:`TokenRecord` from a database row."""
    return TokenRecord(
        source_id=row["source_id"],
        issuer=row["issuer"],
        client_id=row["client_id"],
        scopes=tuple(json.loads(row["scopes"] or "[]")),
        access_token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_type=row["token_type"] or "Bearer",
        expires_at=_parse_time(row["expires_at"]),
        updated_at=_parse_time(row["updated_at"]) or utcnow(),
        needs_reauth=bool(row["needs_reauth"]),
        error=row["error"],
    )


def _pending_from_row(row: sqlite3.Row) -> PendingAuthorization:
    """Build a :class:`PendingAuthorization` from a database row."""
    return PendingAuthorization(
        state=row["state"],
        source_id=row["source_id"],
        code_verifier=row["code_verifier"],
        redirect_uri=row["redirect_uri"],
        created_at=_parse_time(row["created_at"]) or utcnow(),
        expires_at=_parse_time(row["expires_at"]) or utcnow(),
    )
