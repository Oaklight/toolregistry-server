---
title: OAuth Reference
---

# OAuth Resource Server Reference

This page is the consolidated flag reference for the OAuth 2.0 resource
server. The behaviour behind the flags — token verification, scope mapping,
credential precedence and limitations — is in the
[Authentication guide](../guides/authentication.md).

The server **verifies** access tokens; it does not issue them. Tokens must be
JWTs signed with RS256 or ES256 by an external authorization server (AS).

## Flags

All four serving commands — `openapi`, `mcp` (HTTP transports), `gateway`
and `local-hub` — accept the same group. OAuth is off until
`--oauth-issuer` is set.

| Flag | Default | Meaning |
|------|---------|---------|
| `--oauth-issuer URL` | - | Exact `iss` to accept. Setting it enables OAuth verification |
| `--oauth-audience URL` | derived per command | Expected `aud`, or the RFC 8707 `resource` value |
| `--oauth-jwks URL` | discovered | JWKS endpoint; overrides discovery |
| `--oauth-scope SCOPE=NAMESPACE[,NAMESPACE]` | - | Map one token scope to namespace patterns; repeatable. Unmapped scopes grant nothing |
| `--oauth-scopes "SCOPE ..."` | omitted with an issuer | Space-separated scopes published in the protected-resource metadata and in the OpenAPI OAuth2 scheme |
| `--oauth-authorization-url URL` | from AS metadata | OpenAPI `authorizationCode` authorization endpoint |
| `--oauth-token-url URL` | from AS metadata | OpenAPI `authorizationCode` token endpoint |
| `--oauth-refresh-url URL` | - | Optional OpenAPI `refreshUrl`; published only when set |

The three endpoint flags affect only the served OpenAPI document. Token
verification uses the issuer, audience and JWKS.

### Precedence

- **JWKS**: `--oauth-jwks`, else `jwks_uri` from RFC 8414 metadata, else from
  OIDC discovery, else `<issuer>` (trailing slash removed)
  `/.well-known/jwks.json`. A path-bearing issuer is tried in the RFC 8414
  form first (well-known segment before the path), then the suffix form.
- **OpenAPI endpoints**: explicit flag, else `authorization_endpoint` /
  `token_endpoint` from RFC 8414 or OIDC metadata. If neither source supplies
  both endpoints, the OAuth2 scheme is omitted with a warning.
- **Audience**: `--oauth-audience`, else the URL derived from the command (see
  below). There is no implicit fallback to the issuer.

A token is accepted when its `aud` or its RFC 8707 `resource` contains the
configured audience.

## Per-command behaviour

| Command | Derived audience | Notes |
|---------|------------------|-------|
| `openapi` | `http://<host>:<port>` | Warns when the host is a bind address (`0.0.0.0`, `::`); behind a reverse proxy pass `--oauth-audience` with the public origin |
| `mcp` | `--server-url`, else `http://<host>:<port>/mcp` | HTTP transports only; `stdio` warns and ignores the OAuth options |
| `gateway` | `--server-url`, else `http://<host>:<port><mcp path>` | Publishes PRM at `/.well-known/oauth-protected-resource<mcp path>`; a Unix-socket gateway with no `--server-url` publishes no PRM and sends a bare `WWW-Authenticate: Bearer` |
| `local-hub` | `--server-url`, else requires `--oauth-audience` | The hub binds a Unix socket or `127.0.0.1:0`; neither is a public audience, so the audience must be explicit whenever `--oauth-issuer` is set — with or without `--socket` |

## What the server publishes and expects

| Artifact | Where | Content |
|----------|-------|---------|
| Protected-resource metadata (RFC 9728) | `/.well-known/oauth-protected-resource<mcp path>` | `resource`, `authorization_servers: [issuer]`, `bearer_methods_supported`. `scopes_supported` only when `--oauth-scopes` is set |
| 401 challenge | Every rejected request | `WWW-Authenticate: Bearer resource_metadata="<PRM URL>"` |
| OpenAPI security scheme | `/openapi.json` | `OAuth2` (`authorizationCode`) plus `HTTPBearer` when keys or static tokens are also accepted |
| Expected token claims | Every request | `iss` (exact), `aud` or `resource`, `exp`, optional `nbf`; `scope` for namespace mapping |

## Configuration file

OAuth is currently configured by command-line flags only. The tool-source
configuration file (`--config`) has no OAuth section, so a container or
service manager must pass the flags or the equivalent environment-driven
command line. See [Known gaps](#known-gaps).

On the standalone `openapi` command, OAuth needs PyJWT with a crypto backend:
install `toolregistry-server[openapi]`. The base install stays free of
third-party packages, and `[gateway]`/`[mcp]` already carry it.

## Known gaps

These are tracked, not available today:

- No OAuth section in the configuration file.
- No token introspection (RFC 7662): the AS must issue JWTs.
- No built-in authorization server and no client-registration commands.

## See also

- [Authentication guide](../guides/authentication.md) — verification rules and credential precedence.
- [Deploying with an external authorization server](../guides/oauth-deployment.md).
- [Connecting OAuth clients](../guides/oauth-clients.md).
- Changelog entries for the OAuth resource server (#234) and the OpenAPI
  scheme (#235) — the flag surface is not repeated there.
