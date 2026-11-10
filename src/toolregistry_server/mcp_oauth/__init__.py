"""Upstream OAuth for protected MCP sources.

Stage 1 provides the configuration, the on-disk token store and the runtime
flows: client credentials, refresh, and the authorization-code + PKCE pair
the admin panel will drive in stage 2.  The panel routes are not part of this
package yet.

Example:
    ```python
    from toolregistry_server.mcp_oauth import (
        MCPOAuthProvider,
        load_mcp_oauth,
    )

    declarations = load_mcp_oauth("tools.yaml", config)
    provider = MCPOAuthProvider(declarations)
    headers = provider.headers_for(mcp_source)
    ```
"""

from __future__ import annotations

from .config import (
    MCP_OAUTH_SECTION,
    MCPOAuthConfig,
    default_token_store_path,
    load_mcp_oauth,
    selector_matches_source,
)
from .provider import (
    AuthorizationRequest,
    MCPOAuthError,
    MCPOAuthProvider,
    OAuthStateError,
    ReauthorizationRequired,
    TransientOAuthError,
    redact,
)
from .store import PendingAuthorization, TokenRecord, TokenStore

__all__ = [
    "AuthorizationRequest",
    "MCPOAuthConfig",
    "MCPOAuthError",
    "MCPOAuthProvider",
    "MCP_OAUTH_SECTION",
    "OAuthStateError",
    "PendingAuthorization",
    "ReauthorizationRequired",
    "TokenRecord",
    "TokenStore",
    "TransientOAuthError",
    "default_token_store_path",
    "load_mcp_oauth",
    "redact",
    "selector_matches_source",
]
