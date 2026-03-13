"""
Authentication module for ToolRegistry Server.

This module provides authentication utilities for securing
ToolRegistry server endpoints.

Main Components:
    - BearerTokenAuth: Bearer token authentication for FastAPI
    - verify_token: Token verification utility

Example:
    >>> from toolregistry_server.auth import BearerTokenAuth
    >>>
    >>> auth = BearerTokenAuth(tokens=["secret-token"])
    >>> # Use with FastAPI dependency injection
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class BearerTokenAuth:
    """Bearer token authentication handler.

    This class provides Bearer token authentication for FastAPI endpoints.
    It supports multiple valid tokens and can be used as a FastAPI dependency.

    Attributes:
        tokens: Set of valid tokens.

    Example:
        >>> auth = BearerTokenAuth(tokens=["token1", "token2"])
        >>> auth.verify("token1")
        True
        >>> auth.verify("invalid")
        False
    """

    def __init__(self, tokens: "Sequence[str] | None" = None) -> None:
        """Initialize the authentication handler.

        Args:
            tokens: List of valid tokens. If None or empty, authentication
                   is disabled (all requests pass).
        """
        self._tokens: set[str] = set(tokens) if tokens else set()
        self._enabled = bool(self._tokens)

    @property
    def enabled(self) -> bool:
        """Check if authentication is enabled.

        Returns:
            True if at least one token is configured.
        """
        return self._enabled

    def verify(self, token: str) -> bool:
        """Verify a token.

        Args:
            token: The token to verify.

        Returns:
            True if the token is valid or auth is disabled.
        """
        if not self._enabled:
            return True
        return token in self._tokens

    def add_token(self, token: str) -> None:
        """Add a valid token.

        Args:
            token: The token to add.
        """
        self._tokens.add(token)
        self._enabled = True

    def remove_token(self, token: str) -> None:
        """Remove a token.

        Args:
            token: The token to remove.
        """
        self._tokens.discard(token)
        self._enabled = bool(self._tokens)


def verify_token(token: str, valid_tokens: "Sequence[str]") -> bool:
    """Verify a token against a list of valid tokens.

    Args:
        token: The token to verify.
        valid_tokens: List of valid tokens.

    Returns:
        True if the token is in the valid tokens list.
    """
    return token in valid_tokens


__all__ = [
    "BearerTokenAuth",
    "verify_token",
]
