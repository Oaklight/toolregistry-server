"""Token verifiers for MCP Bearer authentication.

This module provides ``TokenVerifier`` implementations that integrate with
the MCP SDK's auth infrastructure to enable Bearer token authentication on
streamable HTTP and SSE transports without requiring a full OAuth
authorization server.

* :class:`StaticTokenVerifier` checks a fixed set of tokens loaded at startup.
* :class:`ScopedTokenVerifier` checks keys in the SQLite store, so a
  revocation or expiry takes effect on the next request.  The principal it
  resolves is re-read by the tool handlers, which enforce the key's scopes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from mcp.server.auth.provider import AccessToken, TokenVerifier

if TYPE_CHECKING:
    from ...keystore import PrincipalResolver


class StaticTokenVerifier(TokenVerifier):
    """Verify Bearer tokens against a fixed set of valid tokens.

    This is the simplest possible TokenVerifier implementation: tokens
    are loaded once at startup (from env vars, a file, or CLI args)
    and checked with a constant-time set lookup.

    Args:
        valid_tokens: Set of tokens that should be accepted.
    """

    def __init__(self, valid_tokens: set[str]) -> None:
        self._valid_tokens = frozenset(valid_tokens)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an AccessToken if the token is in the valid set, else None."""
        if token not in self._valid_tokens:
            return None
        return AccessToken(
            token=token,
            client_id="static",
            scopes=["mcp"],
        )


class ScopedTokenVerifier(TokenVerifier):
    """Verify Bearer tokens against the API key store.

    Verification runs in a worker thread because scrypt is CPU-bound.  The
    returned ``AccessToken`` carries only informational scopes; the tool
    handlers resolve the principal again, which re-reads the key row, so a
    key revoked between the middleware check and the handler still fails.

    Args:
        resolver: The principal resolver backed by the key store.  It may
            also carry static operator tokens, which keep full access.
    """

    def __init__(self, resolver: PrincipalResolver) -> None:
        self._resolver = resolver

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an AccessToken for an active key, else None."""
        principal = await asyncio.to_thread(self._resolver.resolve, token)
        if principal is None:
            return None
        return AccessToken(
            token=token,
            client_id=principal.key_id or "operator",
            scopes=principal.token_scopes(),
        )


__all__ = ["ScopedTokenVerifier", "StaticTokenVerifier"]
