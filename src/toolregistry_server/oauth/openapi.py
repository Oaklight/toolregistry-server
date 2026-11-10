"""OpenAPI security schemes for the OAuth resource server.

The served OpenAPI document describes how a client authenticates: an
``oauth2`` authorization-code scheme when an external issuer is configured,
and the existing HTTP bearer scheme when API keys or static tokens are also
accepted.  Both serving paths (the standalone httpserver app and the
gateway's two OpenAPI assemblies) share this builder, so the scheme
definitions cannot drift.

Endpoint discovery reuses :func:`iter_authorization_server_metadata`; the
resolved document is fetched at most once per builder instance.

Precedence for each endpoint: the explicit ``--oauth-authorization-url`` /
``--oauth-token-url`` / ``--oauth-refresh-url`` value, then the issuer's
RFC 8414 or OIDC metadata.  No scheme is published when the authorization
and token endpoints cannot be resolved; a warning explains how to set them.
"""

from __future__ import annotations

import threading
from typing import Any

from .._vendor.structlog import get_logger
from .verifier import OAuthSettings, iter_authorization_server_metadata

logger = get_logger()

#: Component name of the OAuth2 security scheme.
OAUTH2_SCHEME: str = "OAuth2"

#: Component name of the existing HTTP bearer scheme.
BEARER_SCHEME: str = "HTTPBearer"

#: The bearer scheme definition the server has always published for keys.
BEARER_SCHEME_DEFINITION: dict[str, Any] = {"type": "http", "scheme": "bearer"}


class OpenAPIAuth:
    """Build the document-level security objects once.

    Args:
        oauth: OAuth settings, or ``None`` when OAuth is not configured.
            With ``None`` the builder returns no security objects and the
            callers keep their previous document.
        bearer_accepted: Whether API keys or static bearer tokens are also
            accepted.  When they are, the document expresses OR semantics as
            two security requirements.

    The first :meth:`build` call resolves the endpoints; failures are cached
    too, so a missing metadata document is not retried on every
    ``/openapi.json`` request.
    """

    def __init__(
        self,
        oauth: OAuthSettings | None,
        *,
        bearer_accepted: bool = False,
    ) -> None:
        self._oauth = oauth
        self._bearer_accepted = bool(bearer_accepted and oauth is not None)
        self._schemes: dict[str, Any] | None = None
        self._security: list[dict[str, list[str]]] | None = None
        self._built = False
        self._lock = threading.Lock()

    def build(self) -> tuple[dict[str, Any] | None, list[dict[str, list[str]]] | None]:
        """Return ``(security_schemes, security)``, resolving once."""
        with self._lock:
            if not self._built:
                self._schemes, self._security = self._build()
                self._built = True
            return self._schemes, self._security

    def _build(
        self,
    ) -> tuple[dict[str, Any] | None, list[dict[str, list[str]]] | None]:
        """Construct the schemes and requirements for one configuration."""
        if self._oauth is None:
            return None, None
        schemes: dict[str, Any] = {}
        security: list[dict[str, list[str]]] = []

        scheme = self._oauth_scheme(self._oauth)
        if scheme is not None:
            schemes[OAUTH2_SCHEME] = scheme
            security.append({OAUTH2_SCHEME: []})
        if self._bearer_accepted:
            schemes[BEARER_SCHEME] = dict(BEARER_SCHEME_DEFINITION)
            security.append({BEARER_SCHEME: []})
        return (schemes or None), (security or None)

    def _oauth_scheme(self, settings: OAuthSettings) -> dict[str, Any] | None:
        """Return the ``oauth2`` scheme, or ``None`` when endpoints are unknown."""
        authorization, token, refresh = self._endpoints(settings)
        if not (authorization and token):
            logger.warning(
                "OAuth2 security scheme omitted from the OpenAPI document: "
                "the authorization and token endpoints are unknown. Publish "
                "them in the issuer's metadata or pass "
                "--oauth-authorization-url and --oauth-token-url"
            )
            return None
        flow: dict[str, Any] = {
            "authorizationUrl": authorization,
            "tokenUrl": token,
            "scopes": {str(scope): "" for scope in (settings.scopes_supported or ())},
        }
        if refresh:
            flow["refreshUrl"] = refresh
        return {"type": "oauth2", "flows": {"authorizationCode": flow}}

    def _endpoints(
        self, settings: OAuthSettings
    ) -> tuple[str | None, str | None, str | None]:
        """Return ``(authorization, token, refresh)`` endpoints."""
        authorization = settings.authorization_url
        token = settings.token_url
        refresh = settings.refresh_url
        if authorization and token:
            return authorization, token, refresh
        for document in iter_authorization_server_metadata(
            settings.issuer, settings.timeout
        ):
            authorization = authorization or _url_field(
                document, "authorization_endpoint"
            )
            token = token or _url_field(document, "token_endpoint")
            if authorization and token:
                break
        return authorization, token, refresh


def _url_field(document: dict[str, Any], key: str) -> str | None:
    """Return a non-empty string field from a metadata document."""
    value = document.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "BEARER_SCHEME",
    "BEARER_SCHEME_DEFINITION",
    "OAUTH2_SCHEME",
    "OpenAPIAuth",
]
