"""Simple static token verifier for MCP Bearer authentication.

This module provides a TokenVerifier that validates Bearer tokens against
a pre-configured set of static tokens. It integrates with the MCP SDK's
auth infrastructure to enable Bearer token authentication on streamable
HTTP and SSE transports without requiring a full OAuth authorization server.
"""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken, TokenVerifier


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
