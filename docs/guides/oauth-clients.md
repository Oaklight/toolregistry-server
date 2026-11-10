---
title: Connecting OAuth Clients
---

# Connecting OAuth Clients

How a client connects depends on the client-registration mechanism its
vendor supports. The tool server supports all three:

| Mechanism | Who hosts the client identity | When to use |
|-----------|-------------------------------|-------------|
| **CIMD** (client ID metadata document) | The client, as a URL | The client supports it; no registration at the AS |
| **DCR** (dynamic client registration) | The AS, created on first connect | The client supports it and the AS has DCR enabled |
| **Manual registration** | You, in the AS console | Neither of the above, or the vendor requires a confidential client |

The tool server itself never registers clients. Everything below uses
placeholders: `https://auth.example.com` is the issuer,
`https://tools.example.com/mcp` is the MCP endpoint, and `tools.example.com`
is the public host.

## Gemini custom connected app

Gemini connects through a Google-hosted OAuth client. That client is
**confidential** and is registered manually; dynamic registration is not
available.

1. In Gemini's connected-app form, choose OAuth as the authentication type.
   The form shows a redirect URI of the form:

   ```
   https://oauth-redirect.googleusercontent.com/r/<GOOGLE_PROJECT_ID>
   ```

2. Create a web client at the authorization server with that redirect URI,
   exactly as shown. Do not add a trailing slash, change the scheme, or use
   a localhost redirect.
3. Enter the AS's client ID and client secret in the Gemini form (the secret
   comes from the AS client you just created — never from this repository).
4. Use the MCP endpoint `https://tools.example.com/mcp`. Gemini follows
   discovery itself: it fetches the protected-resource metadata, then the
   AS metadata, then opens the authorize URL.
5. Request scopes the AS issues and the server maps. If Gemini asks for a
   scope the AS rejects, authorization fails before the callback.

Because the client is confidential and pre-registered, no DCR or CIMD
settings are needed at the AS for this client.

## Claude remote / custom connector

Claude's remote MCP connectors start from the 401 challenge and prefer, in
order:

1. **CIMD** — the client presents a URL to its client-metadata document.
   Prefer this when the Claude version supports it; the AS then needs to
   allow URL-based client IDs.
2. **DCR** — the client registers itself at the AS. Enable dynamic client
   registration at the AS if it is available.
3. **Manual credentials** — the connector asks for a client ID (and secret).
   Use this only when neither of the above works; register the connector's
   redirect URI at the AS first.

Configure the connector with:

```
URL:    https://tools.example.com/mcp
```

Claude does not need the issuer or JWKS URL; it reads them from
`/.well-known/oauth-protected-resource/mcp` and the AS metadata.

## ChatGPT / OpenAI custom MCP

OpenAI's custom MCP connectors also discover the authorization server from
the 401 challenge. Use the same order as Claude:

- enable **CIMD** at the AS when the connector offers it;
- otherwise enable **DCR** and let the connector register;
- if the connector asks for manual client details, register its redirect URI
  and paste the client ID/secret.

Provide the MCP URL `https://tools.example.com/mcp`. If the connector reports
that dynamic registration is unsupported, that is an AS setting, not a tool
server setting.

## Swagger UI against the served document

The served OpenAPI document contains the `OAuth2` authorization-code scheme
whenever `--oauth-issuer` is set. In Swagger UI:

1. Open the document (gateway: `https://tools.example.com/openapi/openapi.json`).
2. Click **Authorize**, pick the `OAuth2` scheme, enter the scopes the AS
   issues, and continue.
3. Swagger UI sends the browser to `authorizationUrl`; after the AS callback
   it exchanges the code at `tokenUrl` and calls the tools with the resulting
   bearer token.

Register Swagger UI's redirect URI at the AS. Stock Swagger UI expects an
`oauth2-redirect.html` next to the documentation page. The built-in `/docs`
page does not serve that file today, so the final redirect fails even though
the document and the scheme are correct. Until that is fixed, use an
OAuth-capable client that completes the authorization-code flow (or a
Swagger UI you host, with its redirect page served from the same origin).

The scopes listed in the Authorize dialog come from `--oauth-scopes`; they
must be a subset of what the AS issues.

## Common failures

| Message | Cause | Fix |
|---------|-------|-----|
| `invalid_redirect_uri` at the AS | The URI registered at the AS differs from the one the client sends | Copy the URI from the client's error or documentation verbatim |
| `invalid_scope` | The client requested a scope the AS does not support | Make `--oauth-scopes` a subset of the AS's supported scopes |
| `unsupported_grant_type` / 401 after callback | The AS issued an opaque token or an ID token instead of a JWT access token | Configure the AS to issue a JWT access token for this client |
| `invalid_client` with DCR | DCR is disabled at the AS | Enable it, or switch to CIMD/manual registration |
| 401 from the tool server right after a successful login | Audience/resource mismatch | Mint `aud` or `resource` equal to `--oauth-audience` |
| 401 after a key rotation | Cached JWKS lacks the new `kid` | Retry after 30 seconds; confirm the AS publishes the new key |

## See also

- [Deploying with an external authorization server](oauth-deployment.md).
- [OAuth reference](../reference/oauth.md).
