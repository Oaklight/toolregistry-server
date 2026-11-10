"""On-disk token store for upstream MCP OAuth.

One SQLite database holds one row per protected MCP source plus the
short-lived authorization-code requests the panel will drive.  The store is
deliberately small and has no HTTP surface: callers read a token to put it in
a request header, and nothing here is ever serialised into an API response.

Security notes:

* the database file is created ``0600`` and its directory ``0700``; the WAL
  and shared-memory sidecars are restricted the same way;
* the OAuth client secret itself is never stored here.  A salted,
  iterated fingerprint of it (PBKDF2-HMAC-SHA256) is stored so a rotation can
  be detected and a stale re-authorization verdict discarded.  The
  fingerprint is protected by the file permissions, exactly like the tokens:
  it does not add a second boundary;
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
import random
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Mapping
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
    error TEXT,
    rejections INTEGER NOT NULL DEFAULT 0,
    source_url TEXT NOT NULL DEFAULT '',
    grant TEXT NOT NULL DEFAULT '',
    token_endpoint TEXT NOT NULL DEFAULT '',
    requested_scopes TEXT NOT NULL DEFAULT '[]',
    secret_digest TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS oauth_client_pending (
    state TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS oauth_client_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


#: Columns added after the first release.  ``CREATE TABLE IF NOT EXISTS`` is a
#: no-op on an existing database, so a column an older file lacks has to be
#: added explicitly or the first statement that names it fails at upgrade time.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("oauth_client_tokens", "rejections", "INTEGER NOT NULL DEFAULT 0"),
    ("oauth_client_tokens", "source_url", "TEXT NOT NULL DEFAULT ''"),
    ("oauth_client_tokens", "grant", "TEXT NOT NULL DEFAULT ''"),
    ("oauth_client_tokens", "token_endpoint", "TEXT NOT NULL DEFAULT ''"),
    ("oauth_client_tokens", "requested_scopes", "TEXT NOT NULL DEFAULT '[]'"),
    ("oauth_client_tokens", "secret_digest", "TEXT NOT NULL DEFAULT ''"),
    ("oauth_client_pending", "source_url", "TEXT NOT NULL DEFAULT ''"),
)


def _migrate(connection: sqlite3.Connection) -> None:
    """Add any column an older database file is missing.

    Args:
        connection: The open connection, before the first read.

    Only missing columns are added; an existing column is left untouched, so
    this is safe to run at every open.
    """
    for table, column, declaration in _ADDED_COLUMNS:
        present = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in present:
            continue
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


#: Seconds a token is trusted when its response stated no expiry.  A token
#: with no ``expires_in`` never looks expired, so without an assumption it is
#: kept until something else replaces it; an hour bounds that without
#: re-fetching on every call.
DEFAULT_ASSUMED_TTL = 3600.0


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


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    """Whether *exc* is SQLite reporting a lock held by another writer."""
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


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
            failure, or after repeated resource-server rejections; the source
            must be authorized again.
        error: Redacted reason for :attr:`needs_reauth`.
        rejections: Consecutive resource-server rejections since the
            credential was last known to work.  Reset when a connect
            succeeds, never by :meth:`TokenStore.put` alone.
        source_url: URL of the MCP source the token was minted for, so a
            token is never presented to another host after the declaration
            is repointed.  Empty on a row written before the column existed.
        grant: Grant that produced the token.  A change clears the row.
        token_endpoint: Explicit token endpoint at mint time, or empty.
        requested_scopes: Scopes the declaration asked for, which may differ
            from the granted :attr:`scopes`.  A change clears the row.
        secret_digest: Salted, iterated PBKDF2 fingerprint of the client
            credential the token was minted with, so correcting a secret
            clears a latched row without storing the secret.  It covers the
            env-var name and the client authentication method too, and the
            algorithm, iteration count and salt are stored in the value.
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
    rejections: int = 0
    source_url: str = ""
    grant: str = ""
    token_endpoint: str = ""
    requested_scopes: tuple[str, ...] = ()
    secret_digest: str = ""

    def is_expiring(
        self,
        now: datetime,
        skew: float,
        *,
        assumed_ttl: float | None = DEFAULT_ASSUMED_TTL,
    ) -> bool:
        """Whether the access token is expired or within *skew* seconds.

        A token whose response stated no expiry is assumed to last
        *assumed_ttl* seconds from :attr:`updated_at`; ``None`` opts out and
        means such a token never expires.  The assumption is applied here, on
        the read path, so a row written before the assumption existed is
        bounded too.  ``0`` (the default) means no ``expires_in`` at all.
        """
        if self.expires_at is None:
            if assumed_ttl is None:
                return False
            return now >= self.updated_at + timedelta(seconds=assumed_ttl) - timedelta(
                seconds=skew
            )
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
            "rejections": self.rejections,
            "source_url": self.source_url,
            "grant": self.grant,
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
        source_url: URL of the source being authorized, when the caller
            supplied the parsed source.  The completed token is bound to it.
    """

    state: str
    source_id: str
    code_verifier: str = field(repr=False)
    redirect_uri: str
    created_at: datetime
    expires_at: datetime
    source_url: str = ""


#: How long SQLite waits for a held write lock before reporting
#: ``SQLITE_BUSY``.  Two processes may share one store (a server and a CLI,
#: or two servers), and a token refresh or a panel write holds the lock for
#: the duration of one transaction, so the default five seconds is too short.
BUSY_TIMEOUT_MS = 15_000

#: Extra attempts after a busy timeout, with exponential backoff and jitter.
#: Together with :data:`BUSY_TIMEOUT_MS` this bounds a write to roughly a
#: minute and keeps a contended writer from failing on the first collision.
BUSY_RETRIES = 3

#: Base backoff for a retried busy write, in seconds.
_BUSY_BACKOFF = 0.05

#: ``O_NOFOLLOW`` where the platform has it.  A zero value means the platform
#: cannot refuse a symlink at ``open`` time; the ``lstat`` check still runs.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

#: Manifest key: selector-to-store-path map of the declarations this
#: configuration has recorded.  See
#: :meth:`OAuthClientProvider.reconcile_stored_tokens`.
DECLARED_TARGETS_KEY = "declared_targets"


def _refuse_unsafe_target(database: str) -> None:
    """Refuse a symlink or a special file at the store path.

    ``Path.exists`` and a plain ``open`` follow a symlink, and opening a FIFO
    for writing blocks until a reader appears.  Both turn a writable state
    directory into a denial of service, so the target is inspected first and
    only a regular file (or no file at all) is accepted.

    Args:
        database: The store path.

    Raises:
        OSError: If the path exists and is a symlink or not a regular file.
    """
    try:
        info = os.lstat(database)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise OSError(
            f"refusing to open the OAuth token store {database!r}: "
            "the path is a symbolic link"
        )
    if not stat.S_ISREG(info.st_mode):
        raise OSError(
            f"refusing to open the OAuth token store {database!r}: "
            "the path is not a regular file"
        )


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
    """Create the database file with mode 0600 before SQLite opens it.

    ``O_NOFOLLOW`` makes the open refuse a symlink that appeared between the
    :func:`_refuse_unsafe_target` check and this call.  The file is not
    truncated: an existing database keeps its contents.
    """
    flags = os.O_CREAT | os.O_WRONLY | _NOFOLLOW
    descriptor = os.open(database, flags, 0o600)
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
            _refuse_unsafe_target(database)
            _create_private_file(database)
        self._connection = sqlite3.connect(database, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            self._connection.executescript(_SCHEMA)
            _migrate(self._connection)
            if database != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.commit()
        if database != ":memory:":
            _restrict_permissions(database)

    @property
    def path(self) -> str:
        """Return the database path this store opened."""
        return self._path

    def _run_write(
        self, statement: str, parameters: tuple[Any, ...] = ()
    ) -> sqlite3.Cursor:
        """Execute one write and commit it, retrying a busy database.

        ``PRAGMA busy_timeout`` already makes SQLite wait, so a retry only
        runs when the wait expired.  Backoff is exponential with jitter, and
        the total wait is bounded by :data:`BUSY_RETRIES`, so a lock held by
        another process produces a typed failure rather than a hang.

        Args:
            statement: The SQL to execute.
            parameters: Bound parameters.

        Returns:
            The cursor, for its ``rowcount``.

        Raises:
            sqlite3.OperationalError: When the database stays busy for every
                attempt, or on any other operational error.
        """
        attempt = 0
        while True:
            try:
                cursor = self._connection.execute(statement, parameters)
                self._connection.commit()
                return cursor
            except sqlite3.OperationalError as exc:
                if attempt >= BUSY_RETRIES or not _is_busy(exc):
                    raise
                with contextlib.suppress(sqlite3.Error):
                    self._connection.rollback()
                attempt += 1
                delay = _BUSY_BACKOFF * (2 ** (attempt - 1))
                time.sleep(delay + random.random() * _BUSY_BACKOFF)

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
        is a successful authorization.  The rejection counter is carried
        through unchanged: a refresh that succeeds at the token endpoint says
        nothing about whether the resource server accepts the new token, so
        resetting here would make the two-strike escalation unreachable.
        :meth:`reset_rejections` is the success path.
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
            source_url=record.source_url,
            grant=record.grant,
            token_endpoint=record.token_endpoint,
            requested_scopes=tuple(record.requested_scopes),
            secret_digest=record.secret_digest,
        )
        with self._lock:
            self._run_write(
                """
                INSERT INTO oauth_client_tokens (
                    source_id, issuer, client_id, scopes, access_token,
                    refresh_token, token_type, expires_at, updated_at,
                    needs_reauth, error, source_url, grant, token_endpoint,
                    requested_scopes, secret_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?)
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
                    error = NULL,
                    rejections = oauth_client_tokens.rejections,
                    source_url = excluded.source_url,
                    grant = excluded.grant,
                    token_endpoint = excluded.token_endpoint,
                    requested_scopes = excluded.requested_scopes,
                    secret_digest = excluded.secret_digest
                """,
                _record_values(stored),
            )
        return stored

    def mark_reauth(
        self,
        source_id: str,
        error: str,
        *,
        issuer: str = "",
        client_id: str = "",
        grant: str = "",
        token_endpoint: str = "",
        requested_scopes: tuple[str, ...] | None = None,
        secret_digest: str = "",
    ) -> None:
        """Record that *source_id* must be authorized again.

        The access token is cleared.  The refresh token is kept: a rejection
        such as ``invalid_client`` says the client credential is wrong, not
        that the refresh token is dead, and keeping it lets a corrected
        secret recover with one refresh instead of a full re-authorization.
        A rejection of the refresh token itself retries it once and latches
        again.  The row is inserted when it does not exist yet, so a failure
        on the very first fetch is visible too.

        The declaration fields are refreshed on every latch, so the same
        credential that produced this verdict no longer reads as "changed":
        without that, a corrected secret followed by a dead refresh token
        would clear the latch and retry forever.

        Args:
            source_id: Selector the declaration is written under.
            error: Redacted failure text.
            issuer: Issuer to record when the row is new.
            client_id: Client id to record when the row is new.
            grant: Grant that produced the verdict.
            token_endpoint: Explicit token endpoint, or empty.
            requested_scopes: Scopes the declaration asked for.
            secret_digest: Fingerprint of the credential that failed.
        """
        scopes_text = json.dumps(list(requested_scopes or ()), separators=(",", ":"))
        with self._lock:
            self._run_write(
                """
                INSERT INTO oauth_client_tokens (
                    source_id, issuer, client_id, scopes, updated_at,
                    needs_reauth, error, grant, token_endpoint,
                    requested_scopes, secret_digest
                ) VALUES (?, ?, ?, '[]', ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    access_token = NULL,
                    needs_reauth = 1,
                    error = excluded.error,
                    updated_at = excluded.updated_at,
                    grant = excluded.grant,
                    token_endpoint = excluded.token_endpoint,
                    requested_scopes = excluded.requested_scopes,
                    secret_digest = excluded.secret_digest
                """,
                (
                    source_id,
                    issuer,
                    client_id,
                    _format_time(self._clock()),
                    error,
                    grant,
                    token_endpoint,
                    scopes_text,
                    secret_digest,
                ),
            )

    def clear_latch(self, source_id: str) -> None:
        """Clear the re-authorization latch after a credential change.

        Called when a credential-affecting declaration change makes the old
        verdict stale.  The row and its refresh token stay; the next resolve
        refreshes with the corrected settings.

        Args:
            source_id: Selector the declaration is written under.
        """
        with self._lock:
            self._run_write(
                """
                UPDATE oauth_client_tokens
                   SET needs_reauth = 0,
                       error = NULL
                 WHERE source_id = ?
                """,
                (source_id,),
            )

    def mark_rejected(self, source_id: str) -> None:
        """Record that the resource server rejected the access token.

        The access token and its expiry are always cleared.  A row left with
        a live access token keeps being handed out, because the "not expiring"
        branch returns before any grant is considered: that is the loop this
        method exists to break.  The refresh token is kept, because it may
        still be good, and the rejection counter is incremented so repeated
        failures can escalate.

        Args:
            source_id: Selector the declaration is written under.
        """
        with self._lock:
            self._run_write(
                """
                UPDATE oauth_client_tokens
                   SET access_token = NULL,
                       expires_at = NULL,
                       updated_at = ?,
                       rejections = rejections + 1
                 WHERE source_id = ?
                """,
                (_format_time(self._clock()), source_id),
            )

    def reset_rejections(self, source_id: str) -> None:
        """Clear the rejection counter after the credential was accepted.

        Called from the connect path, which is the only place that knows the
        resource server accepted the token.

        Args:
            source_id: Selector the declaration is written under.
        """
        with self._lock:
            self._run_write(
                """
                UPDATE oauth_client_tokens
                   SET rejections = 0
                 WHERE source_id = ? AND rejections != 0
                """,
                (source_id,),
            )

    def clear(self, source_id: str) -> bool:
        """Delete the stored record for *source_id*.

        Returns:
            ``True`` when a row was deleted.
        """
        with self._lock:
            cursor = self._run_write(
                "DELETE FROM oauth_client_tokens WHERE source_id = ?", (source_id,)
            )
            return cursor.rowcount > 0

    def put_pending(self, pending: PendingAuthorization) -> None:
        """Store an authorization request awaiting its callback."""
        with self._lock:
            self._run_write(
                """
                INSERT OR REPLACE INTO oauth_client_pending (
                    state, source_id, code_verifier, redirect_uri,
                    created_at, expires_at, source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pending.state,
                    pending.source_id,
                    pending.code_verifier,
                    pending.redirect_uri,
                    _format_time(pending.created_at),
                    _format_time(pending.expires_at),
                    pending.source_url,
                ),
            )

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
            self._run_write(
                "DELETE FROM oauth_client_pending WHERE state = ?", (state,)
            )
        return _pending_from_row(row)

    def purge_expired_pending(self, now: datetime | None = None) -> int:
        """Delete expired authorization requests and return how many went."""
        moment = now or self._clock()
        with self._lock:
            cursor = self._run_write(
                "DELETE FROM oauth_client_pending WHERE expires_at <= ?",
                (_format_time(moment),),
            )
            return cursor.rowcount

    def declared_targets(self) -> dict[str, str]:
        """Return the selector-to-store-path manifest this store recorded.

        The manifest lets the next start delete the token row of a
        declaration that left the file while the server was stopped.  An
        empty mapping means nothing was recorded, not that nothing is
        declared now.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM oauth_client_meta WHERE key = ?",
                (DECLARED_TARGETS_KEY,),
            ).fetchone()
        if row is None:
            return {}
        try:
            parsed = json.loads(row["value"])
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, Mapping):
            return {}
        return {
            str(selector): str(path)
            for selector, path in parsed.items()
            if isinstance(selector, str) and isinstance(path, str)
        }

    def record_declared_targets(self, targets: Mapping[str, str]) -> None:
        """Replace the stored selector-to-store-path manifest."""
        payload = json.dumps(dict(sorted(targets.items())), separators=(",", ":"))
        with self._lock:
            self._run_write(
                """
                INSERT INTO oauth_client_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (DECLARED_TARGETS_KEY, payload),
            )


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
        record.source_url,
        record.grant,
        record.token_endpoint,
        json.dumps(list(record.requested_scopes), separators=(",", ":")),
        record.secret_digest,
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
        rejections=int(row["rejections"] or 0),
        source_url=row["source_url"] or "",
        grant=row["grant"] or "",
        token_endpoint=row["token_endpoint"] or "",
        requested_scopes=tuple(json.loads(row["requested_scopes"] or "[]")),
        secret_digest=row["secret_digest"] or "",
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
        source_url=row["source_url"] or "",
    )
