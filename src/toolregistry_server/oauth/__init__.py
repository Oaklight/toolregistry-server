"""OAuth 2.0 resource-server support.

Public surface: :class:`OAuthSettings`, :class:`OAuthTokenVerifier`,
:class:`OAuthResolver`, :class:`CompositePrincipalResolver`,
:class:`OpenAPIAuth`, the resolver builders, and :func:`settings_from_args`.
"""

from .openapi import OpenAPIAuth
from .redirect import (
    OAUTH2_REDIRECT_HTML,
    OAUTH2_REDIRECT_PATH,
    OAUTH2_REDIRECT_URL,
    oauth2_redirect_html,
)
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
    "OAUTH2_REDIRECT_HTML",
    "OAUTH2_REDIRECT_PATH",
    "OAUTH2_REDIRECT_URL",
    "OAuthResolver",
    "OpenAPIAuth",
    "OAuthSettings",
    "OAuthTokenVerifier",
    "build_oauth_resolver",
    "build_resolver",
    "oauth2_redirect_html",
    "settings_from_args",
]
