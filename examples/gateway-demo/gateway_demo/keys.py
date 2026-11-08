"""Static, file-backed client keys for the gateway demo.

The full key lifecycle (SQLite storage, scopes, expiry, revocation and
``last_used``) arrives with toolregistry-server#186.  This module keeps the
same shape behind a small interface so that swapping the store does not
touch the ASGI app:

* a secret is generated once and shown once, never stored in plaintext;
* the file keeps a SHA-256 digest and a short prefix for identification;
* ``verify`` returns the record, so a later release can enforce scopes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

SECRET_PREFIX = "gw_"


@dataclass(frozen=True)
class KeyRecord:
    """Non-secret fields of a client key."""

    name: str
    prefix: str
    scopes: tuple[str, ...]
    created_at: str


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FileKeyStore:
    """A JSON file that records key digests; secrets are shown once."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._records: dict[str, KeyRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        for entry in raw.get("keys", []):
            record = KeyRecord(
                name=entry["name"],
                prefix=entry["prefix"],
                scopes=tuple(entry.get("scopes", ["*"])),
                created_at=entry.get("created_at", ""),
            )
            self._records[entry["digest"]] = record

    def _flush(self) -> None:
        payload = {
            "version": 1,
            "keys": [
                {"digest": digest, **asdict(record)}
                for digest, record in sorted(self._records.items())
            ],
        }
        temporary = self._path.with_name(self._path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self._path)

    def create(
        self, name: str, scopes: list[str] | None = None
    ) -> tuple[str, KeyRecord]:
        """Create a key and return ``(secret, record)``.

        Args:
            name: Unique operator-facing name.
            scopes: Namespaces the key may use; defaults to ``["*"]``.

        Raises:
            ValueError: If *name* is empty, already used, or *scopes* is
                empty or not a list of strings.
        """
        name = name.strip()
        if not name:
            raise ValueError("'name' must not be empty")
        scopes = ["*"] if scopes is None else scopes
        if not scopes or not all(isinstance(item, str) and item for item in scopes):
            raise ValueError("'scopes' must be a non-empty list of strings")
        with self._lock:
            if any(record.name == name for record in self._records.values()):
                raise ValueError(f"key name {name!r} already exists")
            secret = SECRET_PREFIX + secrets.token_urlsafe(24)
            record = KeyRecord(
                name=name,
                prefix=secret[:12],
                scopes=tuple(scopes),
                created_at=_now(),
            )
            self._records[_digest(secret)] = record
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._flush()
            return secret, record

    def verify(self, secret: str | None) -> KeyRecord | None:
        """Return the record for *secret*, or ``None`` when it does not match."""
        if not secret:
            return None
        digest = _digest(secret)
        with self._lock:
            for stored, record in self._records.items():
                if hmac.compare_digest(stored, digest):
                    return record
        return None

    def list(self) -> list[KeyRecord]:
        """Return every key record, ordered by name."""
        with self._lock:
            return sorted(self._records.values(), key=lambda record: record.name)

    def revoke(self, name: str) -> bool:
        """Remove the key named *name*; return whether one was removed."""
        with self._lock:
            for digest, record in list(self._records.items()):
                if record.name == name:
                    del self._records[digest]
                    self._flush()
                    return True
        return False
