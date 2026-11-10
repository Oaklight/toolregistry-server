"""OAuth 2.0 resource-server support.

Public surface: :class:`OAuthSettings`, :class:`OAuthTokenVerifier`,
:class:`OAuthResolver`, :class:`CompositePrincipalResolver`, the resolver
builders, and :func:`settings_from_args`.
"""

from .verifier import (
    CompositePrincipalResolver,
    OAuthResolver,
    OAuthSettings,
    OAuthTokenVerifier,
    build_oauth_resolver,
    build_resolver,
    settings_from_args,
)

__all__ = [
    "CompositePrincipalResolver",
    "OAuthResolver",
    "OAuthSettings",
    "OAuthTokenVerifier",
    "build_oauth_resolver",
    "build_resolver",
    "settings_from_args",
]
