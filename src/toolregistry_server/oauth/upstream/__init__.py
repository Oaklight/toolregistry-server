"""Upstream OAuth client support: the credentials this server presents.

Stage 1 provides the configuration, the on-disk token store and the runtime
flows: client credentials, refresh, and the authorization-code + PKCE pair the
admin panel will drive in stage 2.  The panel routes are not part of this
package yet.

The declarations live in the server-owned ``oauth`` section, under
``clients``, beside the resource-server settings; see
:mod:`toolregistry_server.oauth.upstream.config`.

Example:
    ```python
    from toolregistry_server.oauth.upstream import (
        OAuthClientProvider,
        load_oauth_clients,
    )

    declarations = load_oauth_clients("tools.yaml", config)
    provider = OAuthClientProvider(declarations)
    headers = provider.headers_for(mcp_source)
    ```
"""

from __future__ import annotations

from .config import (
    OAUTH_CLIENT_KEYS,
    OAUTH_CLIENTS_KEY,
    OAuthClientConfig,
    default_token_store_path,
    load_oauth_clients,
    selector_matches_source,
)
from .provider import (
    AuthorizationRequest,
    OAuthClientError,
    OAuthClientProvider,
    OAuthStateError,
    ReauthorizationRequired,
    TransientOAuthError,
    redact,
)
from .store import PendingAuthorization, TokenRecord, TokenStore

__all__ = [
    "OAUTH_CLIENTS_KEY",
    "OAUTH_CLIENT_KEYS",
    "AuthorizationRequest",
    "OAuthClientConfig",
    "OAuthClientError",
    "OAuthClientProvider",
    "OAuthStateError",
    "PendingAuthorization",
    "ReauthorizationRequired",
    "TokenRecord",
    "TokenStore",
    "TransientOAuthError",
    "default_token_store_path",
    "load_oauth_clients",
    "redact",
    "selector_matches_source",
]
