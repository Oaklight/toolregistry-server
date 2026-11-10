"""Per-key call usage for the Keys page and ``keys list --usage`` (#249).

The execution log is the event stream: every recorded call or refusal carries
the caller's ``key_id`` and an outcome in ``metadata``.  This module defines
the aggregate shape, and :class:`~toolregistry_server.keystore.ApiKeyStore`
persists one counter row per key so the panel and the CLI read the same
numbers without scanning the in-process log.

Outcomes are the four values the call sites report:

* ``ok`` — the handler returned.
* ``error`` — the handler raised.
* ``busy`` — the client's concurrency budget refused the call.
* ``denied`` — the key scope or the process permission policy refused it.

A record never contains a secret.  The counters are cumulative for the
database: they survive a restart and stay visible after a key is revoked or
expires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["OUTCOMES", "KeyUsage"]

#: Every outcome a call may report.  ``record_usage`` rejects anything else so
#: a misspelt call site fails loudly instead of silently creating a bucket
#: the panel never reads.
OUTCOMES = frozenset({"ok", "error", "busy", "denied"})


@dataclass(frozen=True)
class KeyUsage:
    """Aggregated usage for one key.

    Attributes:
        key_id: The key these counters belong to.
        calls: Calls recorded for the key, including refused and busy ones.
        ok: Calls whose handler returned.
        error: Calls whose handler raised.
        busy: Calls refused by the client concurrency budget.
        denied: Calls refused by the key scope or the permission policy.
        last_success_at: Canonical ISO 8601 UTC time of the latest successful
            call, or ``None`` when the key has no successful call yet.
    """

    key_id: str
    calls: int = 0
    ok: int = 0
    error: int = 0
    busy: int = 0
    denied: int = 0
    last_success_at: str | None = None

    @classmethod
    def empty(cls, key_id: str) -> KeyUsage:
        """Return the zero record for a key with no recorded usage.

        Args:
            key_id: The key the record describes.

        Returns:
            A record with every counter at zero.
        """
        return cls(key_id=key_id)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable view without any secret material."""
        return {
            "key_id": self.key_id,
            "calls": self.calls,
            "ok": self.ok,
            "error": self.error,
            "busy": self.busy,
            "denied": self.denied,
            "last_success_at": self.last_success_at,
        }
