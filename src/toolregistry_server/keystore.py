"""Inbound API keys for a tool-serving gateway.

This module owns the lifecycle of a client API key:

* :class:`ApiKeyStore` persists keys in a stdlib ``sqlite3`` database.  A
  secret is generated once, returned once, and stored only as a scrypt hash
  with a per-key salt.  Records carry a label, a :class:`ScopeSpec`, creation
  time, optional expiry, revocation time and the last time the key was used.
* :class:`ScopeSpec` describes what a key may call: a namespace allow-list
  plus optional per-tool overrides.  An explicit tool entry wins over the
  namespace decision, so a key can deny one tool inside an allowed namespace
  or allow one tool outside it.  An empty specification allows nothing.
* :class:`Principal` is the authenticated caller.  :class:`PrincipalResolver`
  turns a bearer token into a principal and answers the one authorization
  question every surface asks: may this principal call this route?
* :class:`ApiKeyStore` is also usable directly as a Python API for the admin
  panel (#187), which imports it in-process instead of going through a second
  HTTP authentication path.

Design notes:

* Authorization is read from the database on every request.  Revocation and
  expiry therefore take effect on the next request; no startup snapshot is
  used.  The in-process cache only remembers which key id a secret digest
  verified to, so the expensive scrypt step runs once per secret rather than
  once per surface.  The row itself is re-read and re-checked on every call.
* Static bearer tokens passed with ``--tokens`` keep their historical
  meaning: they are unscoped and grant full access.  They are documented as
  bootstrap/legacy credentials.  Scoping applies to keys created here.
* A secret never appears in :meth:`ApiKeyRecord.to_dict`, a log message, or
  an exception.  Only :class:`CreatedKey` carries it, for the one response
  that creates the key.

Example:
    ```python
    from toolregistry_server.keystore import ApiKeyStore, PrincipalResolver

    store = ApiKeyStore("keys.db")
    created = store.create("build-bot", {"namespaces": ["calculator"]})
    print(created.secret)  # shown once; store it now

    resolver = PrincipalResolver(store)
    principal = resolver.resolve(created.secret)  # or None when inactive
    assert principal is not None
    ```
"""

from __future__ import annotations

import base64
import builtins
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

__all__ = [
    "ApiKeyRecord",
    "ApiKeyStore",
    "CreatedKey",
    "Principal",
    "PrincipalResolver",
    "SCOPE_TRANSPARENT_TOOLS",
    "SCOPED_DENIED_TOOLS",
    "ScopeSpec",
    "bearer_credential",
]

#: Prefix on every generated secret.  A prefix makes a leaked value easy to
#: recognise in logs and secret scanners without revealing the key material.
SECRET_PREFIX = "trk_"

#: Rows fetched by ``list`` when the caller does not name a page size.
DEFAULT_PAGE_SIZE = 50

#: Upper bound on ``limit``.  A larger page would not be a page.
MAX_PAGE_SIZE = 500

#: Tools that invoke another tool by name and cannot make their own scope
#: decision.  Empty today: ``call_deferred`` checks its target with this
#: resolver before dispatching and ``discover_tools`` filters its result
#: (#195), so both are scope-aware.  The set stays as a fail-closed valve for
#: a future proxy that cannot enforce the caller's scope itself.
SCOPED_DENIED_TOOLS: frozenset[str] = frozenset()

#: Tools that speak for other tools and enforce the caller's scope
#: themselves.  A scoped key keeps them whatever its namespaces are: the
#: proxies are registered in the default namespace, so judging them by that
#: namespace would deny discovery to every key scoped elsewhere even though
#: the proxy can only return or invoke tools the key may already use.
SCOPE_TRANSPARENT_TOOLS = frozenset({"discover_tools", "call_deferred"})

#: scrypt work factors.  ``n`` is the CPU/memory cost, ``r`` the block size
#: and ``p`` the parallelisation factor.  16 MiB at the default settings.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

#: Verified digests kept in memory.  Bounded so a long-running process cannot
#: grow without limit; the value only avoids a second scrypt for the same
#: request, so eviction is harmless.
_VERIFIED_CACHE_SIZE = 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    secret_digest TEXT NOT NULL UNIQUE,
    secret_hash TEXT NOT NULL,
    scopes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_last_used
    ON api_keys(last_used_at DESC);
"""


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def _normalize_timestamp(value: datetime | str) -> str:
    """Return a canonical ISO 8601 UTC timestamp.

    The canonical spelling is ``YYYY-MM-DDTHH:MM:SS+00:00``.  It sorts
    lexicographically in the same order as time, which keeps the SQLite
    comparisons and the ``last_used`` ordering correct without parsing dates
    in SQL.

    Args:
        value: An aware or naive datetime, or an ISO 8601 string.  A naive
            datetime is interpreted as UTC.

    Returns:
        The canonical timestamp.

    Raises:
        ValueError: If *value* is not a valid timestamp.
    """
    if isinstance(value, datetime):
        moment = value
    else:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: str) -> datetime:
    """Parse a canonical timestamp back into an aware datetime."""
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text)


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------


def _b64encode(raw: bytes) -> str:
    """Encode bytes as unpadded URL-safe base64."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    """Decode unpadded URL-safe base64."""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sha256_hex(value: str) -> str:
    """Return the hex SHA-256 digest of *value*."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_secret(secret: str, *, salt: bytes | None = None) -> str:
    """Return a self-describing scrypt hash of *secret*.

    The encoded form is ``scrypt$n$r$p$salt$hash``.  Every parameter is
    stored with the hash, so a future tuning change remains verifiable
    against existing rows.
    """
    salt = salt if salt is not None else os.urandom(_SALT_BYTES)
    derived = hashlib.scrypt(
        secret.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(derived)}"


def _verify_secret(secret: str, encoded: str) -> bool:
    """Check *secret* against an encoded scrypt hash in constant time.

    A malformed stored hash is a failed verification, not an exception: a
    damaged row must not turn a login attempt into a 500.
    """
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            secret.encode("utf-8"),
            salt=_b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_b64decode(hash_b64)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, _b64decode(hash_b64))


#: A hash of a random value, verified on a lookup miss so an unknown token
#: costs the same scrypt work as a known one.  Built lazily: importing the
#: package must not pay for a hash nobody uses.
_DUMMY_HASH: str | None = None
_DUMMY_LOCK = threading.Lock()


def _dummy_hash() -> str:
    """Return the memoized hash used to equalize unknown-token timing."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        with _DUMMY_LOCK:
            if _DUMMY_HASH is None:
                _DUMMY_HASH = _hash_secret(secrets.token_urlsafe(32))
    return _DUMMY_HASH


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeSpec:
    """What one key may call.

    Attributes:
        namespaces: Namespace patterns the key may use.  ``"*"`` matches
            every namespace; ``"web/*"`` matches ``web`` and any namespace
            below it; any other value is an exact namespace name.  An empty
            tuple matches nothing, so an omitted scope is fail-closed.
        tools: Per-tool overrides keyed by full tool name.  ``True`` allows
            the tool even when its namespace is not listed; ``False`` denies
            it even when its namespace is listed.  The override always wins.

    Example:
        ```python
        scope = ScopeSpec(
            namespaces=("calculator",),
            tools={"calculator-dangerous": False},
        )
        scope.allows("calculator", "calculator-add")       # True
        scope.allows("calculator", "calculator-dangerous")  # False
        scope.allows("web", "web-search")                  # False
        ```
    """

    namespaces: tuple[str, ...] = ()
    tools: Mapping[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        namespaces = tuple(
            dict.fromkeys(name.strip() for name in self.namespaces if name.strip())
        )
        tools = {
            str(name).strip(): bool(allowed)
            for name, allowed in dict(self.tools).items()
            if str(name).strip()
        }
        object.__setattr__(self, "namespaces", namespaces)
        object.__setattr__(self, "tools", MappingProxyType(tools))

    @classmethod
    def coerce(
        cls, value: ScopeSpec | Mapping[str, Any] | Sequence[str] | None
    ) -> ScopeSpec:
        """Build a specification from its wire form.

        Args:
            value: A :class:`ScopeSpec`, a mapping with ``namespaces`` and
                ``tools`` keys, a sequence of namespace patterns, or ``None``.

        Returns:
            The corresponding specification.

        Raises:
            TypeError: If *value* has an unsupported shape.
            ValueError: If *value* contains a non-boolean tool override.
        """
        if value is None:
            return cls()
        if isinstance(value, ScopeSpec):
            return value
        if isinstance(value, Mapping):
            namespace_values: Any = value.get("namespaces") or ()
            tool_values: Any = value.get("tools") or {}
            if isinstance(namespace_values, str):
                namespace_values = (namespace_values,)
            if not isinstance(tool_values, Mapping):
                raise TypeError("scope 'tools' must be a mapping of name to bool")
            invalid = [
                name
                for name, allowed in tool_values.items()
                if not isinstance(allowed, bool)
            ]
            if invalid:
                raise ValueError(
                    f"scope tool override(s) {invalid} must be true or false"
                )
            return cls(tuple(namespace_values), dict(tool_values))
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return cls(tuple(value))
        raise TypeError(
            "scope must be a ScopeSpec, a mapping, a sequence of namespaces, or None"
        )

    @classmethod
    def all(cls) -> ScopeSpec:
        """Return a specification that allows every namespace."""
        return cls(("*",))

    def allows(self, namespace: str | None, tool_name: str) -> bool:
        """Return whether this specification permits one tool call.

        Args:
            namespace: The tool's namespace.  ``None`` or an empty string is
                treated as ``"default"``, matching ``RouteEntry.namespace``.
            tool_name: The full tool name used as the override key.

        Returns:
            ``True`` when the tool is permitted.
        """
        override = self.tools.get(tool_name)
        if override is not None:
            return override
        effective = namespace or "default"
        return any(
            _namespace_matches(pattern, effective) for pattern in self.namespaces
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serializable wire form."""
        return {
            "namespaces": list(self.namespaces),
            "tools": dict(self.tools),
        }

    def empty(self) -> bool:
        """Whether this specification grants nothing."""
        return not self.namespaces and not self.tools


def _namespace_matches(pattern: str, namespace: str) -> bool:
    """Return whether a namespace pattern covers *namespace*.

    ``"*"`` is global, ``"prefix/*"`` covers the prefix itself and every
    namespace below it, and anything else is an exact match.
    """
    if pattern == "*":
        return True
    if pattern.endswith("/*"):
        prefix = pattern[:-1]
        return namespace == prefix[:-1] or namespace.startswith(prefix)
    return pattern == namespace


# ---------------------------------------------------------------------------
# Records and principals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiKeyRecord:
    """Metadata for one stored key.  It never contains the secret or its hash.

    Attributes:
        id: Opaque key identifier (``key_`` plus 32 hex characters).
        label: Operator-supplied human-readable label.
        scopes: What the key may call.
        created_at: Canonical ISO 8601 UTC creation time.
        expires_at: Canonical expiry time, or ``None`` for no expiry.
        revoked_at: Canonical revocation time, or ``None`` while active.
        last_used_at: Canonical time of the latest successful verification,
            or ``None`` when the key has never been used.
    """

    id: str
    label: str
    scopes: ScopeSpec
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None

    @property
    def revoked(self) -> bool:
        """Whether the key has been revoked."""
        return self.revoked_at is not None

    def expired(self, now: datetime | None = None) -> bool:
        """Whether the key is expired at *now* (default: the current time)."""
        if self.expires_at is None:
            return False
        moment = now or _utcnow()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return _parse_timestamp(self.expires_at) <= moment.astimezone(timezone.utc)

    def active(self, now: datetime | None = None) -> bool:
        """Whether the key is neither revoked nor expired."""
        return not self.revoked and not self.expired(now)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable view without any secret material."""
        return {
            "id": self.id,
            "label": self.label,
            "scopes": self.scopes.to_dict(),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "last_used_at": self.last_used_at,
            "active": self.active(),
        }


@dataclass(frozen=True)
class CreatedKey:
    """A freshly created key and its one-time secret.

    Attributes:
        record: The stored metadata.
        secret: The plaintext secret.  It is not stored and cannot be
            retrieved again.
    """

    record: ApiKeyRecord
    secret: str

    def to_dict(self) -> dict[str, Any]:
        """Return the record plus the secret, for the creation response only."""
        return {**self.record.to_dict(), "secret": self.secret}


@dataclass(frozen=True)
class Principal:
    """An authenticated caller.

    Attributes:
        key_id: The stored key id, or ``None`` for a static operator token.
        label: The key label, or ``None`` for a static operator token.
        scopes: The scope specification.  Ignored when *unrestricted* is set.
        unrestricted: Whether the caller may use every tool.  Static
            ``--tokens`` credentials map here.
    """

    key_id: str | None = None
    label: str | None = None
    scopes: ScopeSpec = field(default_factory=ScopeSpec)
    unrestricted: bool = False

    @classmethod
    def operator(cls, label: str = "operator") -> Principal:
        """Return an unrestricted principal for a static bearer token."""
        return cls(label=label, scopes=ScopeSpec.all(), unrestricted=True)

    def allows(self, namespace: str | None, tool_name: str) -> bool:
        """Return whether this principal may call one tool."""
        if self.unrestricted:
            return True
        return self.scopes.allows(namespace, tool_name)

    def tool_override(self, tool_name: str) -> bool | None:
        """Return this principal's explicit per-tool override, if any.

        ``True`` allows the tool whatever its namespace, ``False`` denies it
        whatever its namespace, and ``None`` means the namespace patterns
        decide.  An unrestricted principal has no overrides to honour.

        Args:
            tool_name: The tool the caller named.

        Returns:
            The override, or ``None`` when the scope does not name the tool.
        """
        if self.unrestricted:
            return None
        return self.scopes.tools.get(tool_name)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable view without any secret material."""
        return {
            "key_id": self.key_id,
            "label": self.label,
            "scopes": self.scopes.to_dict(),
            "unrestricted": self.unrestricted,
        }

    def token_scopes(self) -> list[str]:
        """Return the informational scopes for an MCP ``AccessToken``.

        ``mcp`` is always present because the transport's authorization
        middleware requires it; the namespace patterns follow so a client can
        display them.
        """
        if self.unrestricted:
            return ["mcp", "*"]
        return ["mcp", *self.scopes.namespaces]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ApiKeyStore:
    """SQLite-backed API key store.

    Args:
        path: Database path.  ``":memory:"`` is supported for tests and
            short-lived processes.
        clock: Optional callable returning the current aware datetime.  Tests
            use it to move expiry boundaries without sleeping.

    The connection is shared and guarded by a re-entrant lock.  SQLite's
    ``check_same_thread`` is disabled because the OpenAPI backends and the
    MCP transport call the store from worker threads and the event loop.

    Raises:
        ValueError: If *path* is empty.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        database = os.fspath(path)
        if not database:
            raise ValueError("database path must not be empty")
        self._path = database
        self._clock = clock or _utcnow
        self._lock = threading.RLock()
        self._verified: OrderedDict[str, str] = OrderedDict()
        self._connection = sqlite3.connect(database, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(_SCHEMA)
            if database != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.commit()
        if database != ":memory:":
            _restrict_permissions(database)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Close the database connection.  The store is unusable afterwards."""
        with self._lock:
            self._connection.close()
            self._verified.clear()

    def __enter__(self) -> ApiKeyStore:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- clock -------------------------------------------------------------

    def _now(self) -> str:
        """Return the current time in canonical form."""
        return _normalize_timestamp(self._clock())

    # -- create / read / revoke -------------------------------------------

    def create(
        self,
        label: str,
        scopes: ScopeSpec | Mapping[str, Any] | Sequence[str] | None = None,
        *,
        expires_at: datetime | str | None = None,
    ) -> CreatedKey:
        """Create a key and return its one-time secret.

        Args:
            label: Human-readable label.  Must not be empty.
            scopes: What the key may call; see :meth:`ScopeSpec.coerce`.
                ``None`` creates a key that can call nothing.
            expires_at: Optional expiry timestamp.  Must be in the future.

        Returns:
            The stored record and the plaintext secret.

        Raises:
            ValueError: If *label* is empty, or *expires_at* is not in the
                future.
        """
        if not label or not label.strip():
            raise ValueError("label must not be empty")
        specification = ScopeSpec.coerce(scopes)
        now_moment = self._clock()
        if now_moment.tzinfo is None:
            now_moment = now_moment.replace(tzinfo=timezone.utc)
        now = _normalize_timestamp(now_moment)
        expires = _normalize_timestamp(expires_at) if expires_at is not None else None
        if expires is not None and _parse_timestamp(expires) <= now_moment:
            raise ValueError("expires_at must be in the future")

        secret = SECRET_PREFIX + secrets.token_urlsafe(32)
        key_id = "key_" + uuid.uuid4().hex
        row = (
            key_id,
            label.strip(),
            _sha256_hex(secret),
            _hash_secret(secret),
            _json_scopes(specification),
            now,
            expires,
            None,
            None,
        )
        with self._lock:
            self._connection.execute(
                "INSERT INTO api_keys (id, label, secret_digest, secret_hash, "
                "scopes, created_at, expires_at, revoked_at, last_used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            self._connection.commit()
            stored = self._select_id(key_id)
        assert stored is not None  # the row was just written
        return CreatedKey(record=_row_to_record(stored), secret=secret)

    def get(self, key_id: str) -> ApiKeyRecord | None:
        """Return a record by id, or ``None`` when the id is unknown."""
        with self._lock:
            row = self._select_id(key_id)
        return _row_to_record(row) if row is not None else None

    def list(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> builtins.list[ApiKeyRecord]:
        """Return a page of records ordered by last use.

        The most recently used keys come first; keys that were never used
        follow, newest first.  Revoked and expired keys stay in the list so an
        operator can see them.

        Args:
            limit: Page size, between 1 and 500.
            offset: Number of records to skip.

        Returns:
            The requested page.

        Raises:
            ValueError: If *limit* or *offset* is out of range.
        """
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if offset < 0:
            raise ValueError("offset must not be negative")
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM api_keys "
                "ORDER BY (last_used_at IS NULL), last_used_at DESC, "
                "created_at DESC, id "
                "LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def revoke(self, key_id: str) -> ApiKeyRecord:
        """Revoke a key.  The change applies to the next request.

        Revoking an already revoked key keeps the original timestamp, so the
        call is idempotent.

        Args:
            key_id: The key to revoke.

        Returns:
            The revoked record.

        Raises:
            KeyError: If *key_id* is unknown.
        """
        now = self._now()
        with self._lock:
            if self._select_id(key_id) is None:
                raise KeyError(key_id)
            self._connection.execute(
                "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
                (now, key_id),
            )
            self._connection.commit()
            self._forget(key_id)
            row = self._select_id(key_id)
        assert row is not None
        return _row_to_record(row)

    # -- authentication ----------------------------------------------------

    def verify(self, secret: str | None) -> Principal | None:
        """Authenticate *secret* and return the matching principal.

        The row is read on every call and the ``last_used_at`` update is
        conditional on the key still being active, so a revocation or expiry
        that lands at any point makes this call return ``None``.

        Args:
            secret: The presented bearer secret.

        Returns:
            The principal, or ``None`` when the secret is unknown, revoked,
            expired or malformed.
        """
        if not secret:
            return None
        digest = _sha256_hex(secret)
        with self._lock:
            row = self._select_digest(digest)
            cached = self._verified.get(digest)
        if row is None:
            # Same scrypt cost as a known key, so timing does not disclose
            # whether the secret exists.
            _verify_secret(secret, _dummy_hash())
            return None

        key_id = row["id"]
        if cached != key_id and not _verify_secret(secret, row["secret_hash"]):
            # A digest hit with a failed scrypt means the stored row was
            # altered; never let a stale cache entry turn that into access.
            self._forget(key_id)
            return None

        now = self._now()
        with self._lock:
            self._connection.execute(
                "UPDATE api_keys SET last_used_at = ? "
                "WHERE id = ? AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (now, key_id, now),
            )
            self._connection.commit()
            fresh = self._select_id(key_id)
        if fresh is None or not _row_is_active(fresh, now):
            self._forget(key_id)
            return None
        self._remember(digest, key_id)
        record = _row_to_record(fresh)
        return Principal(key_id=record.id, label=record.label, scopes=record.scopes)

    # Alias with the verb used by the existing authentication module.
    authenticate = verify

    # -- internals ---------------------------------------------------------

    def _select_id(self, key_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()

    def _select_digest(self, digest: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM api_keys WHERE secret_digest = ?", (digest,)
        ).fetchone()

    def _remember(self, digest: str, key_id: str) -> None:
        """Remember that *digest* verified to *key_id*, bounded."""
        with self._lock:
            self._verified[digest] = key_id
            self._verified.move_to_end(digest)
            while len(self._verified) > _VERIFIED_CACHE_SIZE:
                self._verified.popitem(last=False)

    def _forget(self, key_id: str) -> None:
        """Drop cached digests for *key_id*."""
        with self._lock:
            stale = [
                digest for digest, kept in self._verified.items() if kept == key_id
            ]
            for digest in stale:
                self._verified.pop(digest, None)


def _restrict_permissions(path: str) -> None:
    """Make the database and its WAL sidecars owner-only.

    Platforms without POSIX modes are ignored: the store still works, and the
    operator is responsible for file access there.
    """
    for candidate in (path, f"{path}-wal", f"{path}-shm"):
        with contextlib.suppress(OSError):
            os.chmod(candidate, 0o600)


def _json_scopes(specification: ScopeSpec) -> str:
    """Serialize a scope specification for storage."""
    return json.dumps(specification.to_dict(), sort_keys=True, separators=(",", ":"))


def _row_is_active(row: sqlite3.Row, now: str) -> bool:
    """Whether a row is neither revoked nor expired at canonical time *now*."""
    if row["revoked_at"] is not None:
        return False
    expires = row["expires_at"]
    return expires is None or expires > now


def _row_to_record(row: sqlite3.Row) -> ApiKeyRecord:
    """Convert a database row into a record."""
    return ApiKeyRecord(
        id=row["id"],
        label=row["label"],
        scopes=ScopeSpec.coerce(json.loads(row["scopes"])),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
    )


# ---------------------------------------------------------------------------
# Principal resolution
# ---------------------------------------------------------------------------


class PrincipalResolver:
    """Turn a bearer token into a :class:`Principal` and answer scope checks.

    Args:
        store: The key store, or ``None`` when only static tokens are used.
        operator_tokens: Static bearer tokens that keep full, unscoped
            access.  They are the bootstrap/legacy credential.
        allow_anonymous: Whether a missing token maps to an unrestricted
            principal.  The default is ``False``: once a store is attached,
            every request must present a credential.

    A resolver with no store and no operator tokens is inert: :meth:`resolve`
    returns ``None`` unless *allow_anonymous* is set.
    """

    def __init__(
        self,
        store: ApiKeyStore | None = None,
        *,
        operator_tokens: Iterable[str] = (),
        allow_anonymous: bool = False,
    ) -> None:
        self._store = store
        self._operator_tokens = tuple(token for token in operator_tokens if token)
        self._allow_anonymous = allow_anonymous
        self._operator = Principal.operator()
        self._anonymous = Principal.operator(label="anonymous")

    @property
    def store(self) -> ApiKeyStore | None:
        """The attached store, or ``None``."""
        return self._store

    @property
    def has_keys(self) -> bool:
        """Whether a key store is attached."""
        return self._store is not None

    @property
    def enabled(self) -> bool:
        """Whether this resolver requires or accepts a credential."""
        return self._store is not None or bool(self._operator_tokens)

    def resolve(self, token: str | None) -> Principal | None:
        """Resolve a bearer token.

        Static operator tokens are checked first and always grant full
        access.  Otherwise the store is consulted, which re-reads the row so a
        revoked or expired key fails immediately.

        Args:
            token: The presented secret, or ``None``.

        Returns:
            The principal, or ``None`` when the credential is missing or
            unknown.
        """
        if not token:
            return self._anonymous if self._allow_anonymous else None
        if self._operator_tokens and _token_in(token, self._operator_tokens):
            return self._operator
        if self._store is not None:
            return self._store.verify(token)
        return None

    def resolve_headers(self, headers: Mapping[str, str] | None) -> Principal | None:
        """Resolve the ``Authorization`` header of a request.

        Args:
            headers: A case-insensitive mapping (Starlette) or a plain
                mapping (the MCP SDK exposes either).

        Returns:
            The principal, or ``None`` when the header is absent or invalid.
        """
        return self.resolve(bearer_credential(headers))

    def allows(
        self, principal: Principal | None, namespace: str | None, tool_name: str
    ) -> bool:
        """Whether *principal* may call one tool.  ``None`` denies.

        A scoped principal is denied the proxy tools in
        :data:`SCOPED_DENIED_TOOLS` and keeps the ones in
        :data:`SCOPE_TRANSPARENT_TOOLS`.  The transparent pair enforce the
        scope on the tool they act on, so denying them by their own namespace
        would only break discovery for keys scoped elsewhere (#195).  Only the
        namespace component is bypassed: an explicit per-tool override for a
        transparent tool still decides, because an operator who denies the
        proxy made a deliberate choice.  Static operator tokens are
        unrestricted and meet neither rule.
        """
        if principal is None:
            return False
        if not principal.unrestricted:
            if tool_name in SCOPED_DENIED_TOOLS:
                return False
            if tool_name in SCOPE_TRANSPARENT_TOOLS:
                # Only the namespace component is bypassed: an explicit
                # per-tool override is a deliberate decision and still wins.
                override = principal.tool_override(tool_name)
                return True if override is None else override
        return principal.allows(namespace, tool_name)

    def allows_route(self, principal: Principal | None, route: Any) -> bool:
        """Whether *principal* may call the route named by *route*."""
        return self.allows(
            principal,
            getattr(route, "namespace", None),
            getattr(route, "tool_name", ""),
        )

    def filter_routes(
        self, principal: Principal | None, routes: Iterable[Any]
    ) -> list[Any]:
        """Return the routes *principal* may see.

        This is the single filtering point used by the MCP ``tools/list``
        handler, the OpenAPI document builder and the tool listings.
        """
        if principal is None:
            return []
        if principal.unrestricted:
            return list(routes)
        return [route for route in routes if self.allows_route(principal, route)]


def bearer_credential(headers: Mapping[str, str] | None) -> str | None:
    """Extract the credential from an ``Authorization: Bearer`` header.

    Args:
        headers: A case-insensitive or plain mapping.  ``None`` yields
            ``None``.

    Returns:
        The credential, or ``None`` when the header is absent, uses another
        scheme, or carries an empty value.
    """
    if not headers:
        return None
    value: str | None = None
    try:
        items = headers.items()
    except AttributeError:
        return None
    for name, candidate in items:
        if str(name).lower() == "authorization":
            value = str(candidate)
            break
    if value is None:
        return None
    scheme, _, credential = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    credential = credential.strip()
    return credential or None


def _token_in(candidate: str, valid_tokens: Sequence[str]) -> bool:
    """Compare *candidate* against every token in constant time.

    Every token is compared so the position of a match does not leak.
    """
    candidate_digest = _sha256_hex(candidate)
    matched = False
    for valid in valid_tokens:
        if hmac.compare_digest(candidate_digest, _sha256_hex(valid)):
            matched = True
    return matched
