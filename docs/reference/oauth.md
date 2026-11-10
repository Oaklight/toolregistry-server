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
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | Map one token scope to namespace patterns and tool overrides; repeatable. An entry is a namespace pattern, `+tool-name` to allow one tool, or `-tool-name` to deny it. Unmapped scopes grant nothing |
| `--oauth-scopes "SCOPE ..."` | omitted with an issuer | Space-separated scopes published in the protected-resource metadata and in the OpenAPI OAuth2 scheme |
| `--oauth-authorization-url URL` | from AS metadata | OpenAPI `authorizationCode` authorization endpoint |
| `--oauth-token-url URL` | from AS metadata | OpenAPI `authorizationCode` token endpoint |
| `--oauth-refresh-url URL` | - | Optional OpenAPI `refreshUrl`; published only when set |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | Token claim that may carry a client's in-flight call limit. A non-positive or non-integer value is ignored |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | Static in-flight call limit for one OAuth client; repeatable. A valid concurrency claim wins over this table |
| `--oauth-introspection-url URL` | - | RFC 7662 introspection endpoint. When set, every locally verified token is confirmed with it |
| `--oauth-introspection-client-id ID` | - | Client id for the introspection endpoint (HTTP Basic); required together with the URL |
| `--oauth-introspection-client-secret SECRET` | - | Client secret for the introspection endpoint (HTTP Basic); required together with the URL |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | How long one introspection result is reused; this bounds how long a revoked token is still accepted. `0` disables caching |
| `--oauth-introspection-optional` | off | Fall back to local verification when the introspection endpoint fails; by default a failure rejects the token |

The three endpoint flags affect only the served OpenAPI document. Token
verification uses the issuer, audience and JWKS; introspection uses its own
endpoint.

### Scope entries

`--oauth-scope` takes `SCOPE=ENTRY[,ENTRY]`. Each entry is one of:

| Entry | Effect |
|-------|--------|
| `calculator` or `web/*` | Allow the namespace pattern. `*` allows every namespace |
| `+tool-name` | Allow one tool outside the namespace list |
| `-tool-name` | Deny one tool inside an allowed namespace |

For example, `--oauth-scope "tools:read=calculator,+web-search,-calculator-danger"`
allows the `calculator` namespace, allows `web-search` even though its
namespace is not listed, and denies `calculator-danger`. When two granted
scopes disagree about one tool, the deny wins, so adding a scope cannot widen
an existing deny. The scopes are read from the `scope` claim, falling back to
`scp` (a string or a JSON array) that some identity platforms use.

### Precedence

- **JWKS**: `--oauth-jwks`, else `jwks_uri` from RFC 8414 metadata, else from
  OIDC discovery, else `<issuer>` (trailing slash removed)
  `/.well-known/jwks.json`. A path-bearing issuer is tried in the RFC 8414
  form first (well-known segment before the path), then the suffix form. The
  trailing-slash removal also applies to the last-resort fallback, so an
  issuer configured as `https://auth.example.com/` whose metadata cannot be
  reached still fetches `https://auth.example.com/.well-known/jwks.json`
  rather than a doubled slash. `iss` itself is still compared verbatim.
- **OpenAPI endpoints**: explicit flag, else `authorization_endpoint` /
  `token_endpoint` from RFC 8414 or OIDC metadata. If neither source supplies
  both endpoints, the OAuth2 scheme is omitted with a warning.
- **Audience**: `--oauth-audience`, else the URL derived from the command (see
  below). There is no implicit fallback to the issuer.
- **Concurrency**: a positive integer in the `--oauth-concurrency-claim`
  claim, else the `--oauth-client-concurrency` entry for the token's client.
  Either value can only narrow the process `--client-concurrency`.

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
| Expected token claims | Every request | `iss` (exact), `aud` or `resource`, `exp`, optional `nbf`; `scope` (or `scp`) for scope mapping; `client_id` or `azp` for the client identity; the `--oauth-concurrency-claim` claim (default `max_concurrency`) for an in-flight limit |

## Swagger UI redirect page

Swagger UI finishes the authorization-code flow by opening
`oauth2-redirect.html` next to the documentation page. The OpenAPI frontend
serves that page itself, on both backends:

| Backend | Page | Swagger UI setting |
|---------|------|--------------------|
| Standalone `openapi` (`httpserver`) | `GET /oauth2-redirect.html` at the listener root | `/docs` sets `oauth2RedirectUrl` to the relative `oauth2-redirect.html` |
| `gateway` (ASGI) | `GET <openapi path>/oauth2-redirect.html`, for example `/openapi/oauth2-redirect.html` | `/docs` sets `oauth2RedirectUrl` to `<openapi path>/oauth2-redirect.html` and the document URL to `<openapi path>/openapi.json` |

Both follow the configured path, so `--openapi-path /rest` moves the page to
`/rest/oauth2-redirect.html`. The gateway answers the path before its bearer
wrapper, and the standalone route is exempt from its token check.

!!! warning "The redirect page is unauthenticated"
    The authorization server redirects the browser here without a bearer
    token, so the route cannot require one; a token check would turn the
    callback into a `401` and leave the flow unfinished. The page is static,
    loads no external asset, holds no secret, and only hands the
    authorization response back to the Swagger UI window that opened it.

The page mirrors the parsing of the upstream `swagger-ui-dist` redirect
script: it percent-decodes each callback parameter, accepts a hash form with a
leading `?` (`#?code=...`), and recognises the flow names `accessCode`,
`authorizationCode` and `authorization_code`.

Register the redirect URI at the authorization server as the public URL of
this page — `<public origin>/oauth2-redirect.html` for the standalone server,
`<public origin><openapi path>/oauth2-redirect.html` for a gateway.

## Token introspection

Local JWKS verification cannot see a token that the authorization server
revoked or disabled after it was minted; the token keeps working until it
expires. Configure an RFC 7662 introspection endpoint to close that window:

```bash
toolregistry-server gateway --config config.yaml \
  --oauth-issuer https://auth.example.com \
  --oauth-introspection-url https://auth.example.com/introspect \
  --oauth-introspection-client-id toolregistry \
  --oauth-introspection-client-secret "$INTROSPECTION_SECRET" \
  --oauth-introspection-cache-ttl 5
```

- The endpoint is called after the local check passes, with the token and
  `token_type_hint=access_token` in a form body and the client credential in
  HTTP Basic. Only an `active: true` answer accepts the token.
- Introspection does not accept opaque tokens: the JWT is still verified
  locally first, so the AS must keep issuing RS256 or ES256 JWTs.
- The result is cached per token for `--oauth-introspection-cache-ttl`
  (default 5 seconds). Only a SHA-256 digest of the token is kept, never the
  token itself, and both positive and negative answers are cached. This TTL
  is the longest time a revoked token is still accepted; `0` disables the
  cache and introspects on every request.
- An endpoint failure rejects the token. `--oauth-introspection-optional`
  falls back to the local result instead, trading revocation latency for
  availability. The failure is cached for the same TTL, so an outage costs
  one probe per TTL rather than one timed-out request per call.
- The token and the request body are never logged; a failure logs the
  endpoint and the exception type only.
- Setting the URL without a client id and secret is a startup error.
  Leaving the URL unset keeps local verification only, which is the default.

The endpoint must be reachable from the tool server. If it sits on a private
network, either expose it to the server or use
`--oauth-introspection-optional`.

## Principal, scopes and accounting

An OAuth token produces the same kind of principal as an API key, so the
authorization layer behaves the same on every frontend:

- **Label** — the `sub` claim, else `client_id`/`azp`, else `oauth`.
- **Client** — `client_id`, else `azp`. It is kept apart from `sub`, so two
  clients acting for one user are separate principals with separate scopes,
  execution-log rows and counters.
- **Scopes** — the `--oauth-scope` mapping only. An unmapped scope grants
  nothing, and tool overrides from `+tool`/`-tool` entries apply on top of the
  namespaces.
- **Concurrency** — `--oauth-concurrency-claim` or
  `--oauth-client-concurrency`; it narrows the process
  `--client-concurrency` and never raises it. The budget belongs to the OAuth
  client, so every user that client acts for shares one in-flight limit; the
  limiter key is `oauth:<client_id>`. The budget is monotonically
  non-increasing: a token whose claim is wider than the limit already in
  force never raises it, while a narrower claim lowers it. Accounting stays
  per principal: the execution-log row and the counters carry the client and
  the `sub` (`oauth:<client_id>:<label>`), so two users of one client remain
  distinguishable.
- **Listing** — `tools/list` on MCP and `/tools` and `/openapi.json` on the
  OpenAPI frontends show only the tools the token may call.
- **Execution log** — each call records `client_id` (the OAuth client) and
  `outcome` (`ok`, `error`, `busy` or `denied`). A refused call is recorded
  with `ERROR` status and the precise outcome in metadata, and with empty
  arguments, because the payload of a call that was not allowed is not
  needed to audit the decision. Configuring an issuer enables the execution
  log by default, the same way a key store does; `--no-execution-log` turns
  it off.

Because every request re-reads the token and its claims, a scope change or a
revocation at the authorization server applies on the next request when
introspection is enabled, and on the next token otherwise. No restart is
needed.

## Configuration file

The tool-source configuration file (`--config`) may carry an `oauth` section.
Only the introspection settings and the static client limits are read from
it; a command-line flag wins over the file for every key.

```yaml
oauth:
  introspection_url: https://auth.example.com/introspect
  introspection_client_id: toolregistry
  introspection_client_secret: change-me
  introspection_cache_ttl: 5
  introspection_optional: false
  client_concurrency:
    reports-bot: 2
```

The issuer, audience and scope mapping remain command-line options, so OAuth
still requires the corresponding flags. The section is checked: a key the
server does not read is rejected at startup, because a typo such as
`introspection_ur1` would otherwise leave a security control silently off.
The accepted keys are `introspection_url`, `introspection_client_id`,
`introspection_client_secret`, `introspection_cache_ttl`,
`introspection_optional`, `concurrency_claim` and `client_concurrency`. A bad
per-client limit names its source, for example
`oauth.client_concurrency['reports-bot'] must be a positive integer`.

On the standalone `openapi` command, OAuth needs PyJWT with a crypto backend:
install `toolregistry-server[openapi]`. The base install stays free of
third-party packages, and `[gateway]`/`[mcp]` already carry it.

## Known gaps

These are tracked, not available today:

- No built-in authorization server and no client-registration commands.
- Introspection does not make opaque tokens usable: local JWT verification
  still runs first.
- The `oauth` configuration section covers introspection and static client
  limits only; issuer, audience and scope mapping are flags.

## See also

- [Authentication guide](../guides/authentication.md) — verification rules and credential precedence.
- [Deploying with an external authorization server](../guides/oauth-deployment.md).
- [Connecting OAuth clients](../guides/oauth-clients.md).
- Changelog entries for the OAuth resource server (#234) and the OpenAPI
  scheme (#235) — the flag surface is not repeated there.
