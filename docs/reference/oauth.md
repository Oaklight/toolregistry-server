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
| `--oauth-allow-private-hosts` | off | Let discovery, JWKS and introspection reach a loopback, link-local or private address; the outbound address policy refuses such a host by default |

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
| Expected token claims | Every request | `iss` (exact), `aud` or `resource`, `exp`, optional `nbf`, and `iat` when present (a future `iat` is rejected within the 60 s leeway); `scope` (or `scp`) for scope mapping; `client_id` or `azp` for the client identity; the `--oauth-concurrency-claim` claim (default `max_concurrency`) for an in-flight limit |

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
Every OAuth setting is available there, named after its flag with the
`oauth_` prefix removed; a command-line flag wins over the file for every
key, and the file supplies a value only when its flag is absent.

```yaml
oauth:
  issuer: https://auth.example.com
  audience: https://tools.example.com/mcp
  jwks: https://auth.example.com/.well-known/jwks.json
  scope:
    tools:read: [calculator, web/*, +web-search, -calculator-danger]
    tools:admin: web/*
  scopes: [openid, profile, email]
  authorization_url: https://auth.example.com/authorize
  token_url: https://auth.example.com/token
  refresh_url: https://auth.example.com/refresh
  introspection_url: https://auth.example.com/introspect
  introspection_client_id: toolregistry
  introspection_client_secret: change-me
  introspection_cache_ttl: 5
  introspection_optional: false
  concurrency_claim: max_concurrency
  client_concurrency:
    reports-bot: 2
```

`issuer` is the only key needed to turn OAuth on: with it in the file and no
OAuth flag on the command line, the server behaves exactly as
`--oauth-issuer` would. `audience` replaces the derived audience. The
`jwks`, `authorization_url`, `token_url` and `refresh_url` keys override
discovery for the same flags. The remaining keys are `introspection_url`,
`introspection_client_id`, `introspection_client_secret`,
`introspection_cache_ttl`, `introspection_optional`, `concurrency_claim`,
`client_concurrency` and `clients`.

| Key | Flag | Accepted values |
|-----|------|-----------------|
| `issuer` | `--oauth-issuer` | string |
| `audience` | `--oauth-audience` | string |
| `jwks` | `--oauth-jwks` | string |
| `authorization_url` | `--oauth-authorization-url` | string |
| `token_url` | `--oauth-token-url` | string |
| `refresh_url` | `--oauth-refresh-url` | string |
| `scope` | `--oauth-scope` | mapping from a scope to its entries; each value is a list of entries or one comma-separated string. The flag's `[SCOPE=ENTRY[,ENTRY], ...]` list is accepted too |
| `scopes` | `--oauth-scopes` | list of strings, or one space-separated string |
| `clients` | no flag | list of mappings, one per source this server presents a credential to; file-only, there is no command-line equivalent |

The `scope` entries use the same grammar as the flag: a namespace pattern,
`+tool-name` to allow one tool, or `-tool-name` to deny it. An unmapped scope
still grants nothing, so `scope` is where least privilege is expressed for a
file-driven deployment.

`clients` is the other direction of the same section: the OAuth credential
this server presents to an access point it calls, rather than the one it
accepts. Each entry selects one HTTP(S) MCP source with `namespace:<name>` or
`url:<url>`, and carries `issuer` or `discovery_url`, `client_id`, exactly one
of `client_secret` and `client_secret_env`, optional `scopes`, `grant`
(`client_credentials` or `authorization_code`), a `token_endpoint` override, a
`client_auth` choice (`client_secret_post` or `client_secret_basic`) and an
absolute `token_store` path. Two entries cannot select the same source, and an
unknown key, a relative `token_store` or an existing `Authorization` header on
the source fails the load. Tokens are kept in SQLite outside the
configuration file. The client secret itself is never written there; a
salted, iterated fingerprint of it is, so rotating the secret discards a stale
re-authorization verdict without storing the secret. The start/complete pair
is driven by the gateway panel; see below. The panel's
connection test does not attach the upstream credential, and its result says
so when a declaration selects the source. See
[the deployment guide](../guides/oauth-deployment.md#upstream-credentials-for-a-protected-mcp-source).

A stored token is reused until the MCP server refuses it. An HTTP 401 or 403
from the source on connect drops the stored access token and counts the
rejection, keeping the refresh token. The next connect uses the refresh token
when the row has one; otherwise a `client_credentials` source requests a new
token at once, while an `authorization_code` source reports `reauth_required`
and waits for the operator. A second consecutive rejection latches an
`authorization_code` source and makes a `client_credentials` one drop the row
and retry the current secret. The counter resets only after a connect succeeds
end to end, so a token fetched at the token endpoint is not trusted until the
source accepts it.

The row records the source URL, grant, requested scopes, token endpoint and a
fingerprint of the client credential it was minted with. A token is presented
only to the URL it was minted for; repointing a selector at another host mints
a new token instead of replaying the old one. A definitive grant rejection
stays latched until the operator re-authorizes or corrects the client
credentials in the file: issuer, client id, token endpoint, grant, scopes,
secret or client authentication method. An issuer, client-id, grant or target
change discards the row outright.

Discovered metadata is used only when its `issuer` equals the configured
issuer (RFC 8414 section 3.3), or, for a bare `discovery_url`, when it names an
issuer at all. Its endpoints must be absolute `http` or `https` URLs, and a
document without a usable `token_endpoint` is not cached, so the next
discovery candidate can still answer. A cached document is fetched again after
300 seconds, so an endpoint that moves without answering 404/410 is still
picked up without a restart; when that fetch fails, the cached document is
used until the next attempt and a warning is logged.

A panel edit that would make an `oauth.clients` selector name no source, or
several, is rejected before it is written; a failed write leaves the token
rows in place. Error text is redacted: URL userinfo, credential-shaped
parameters, the client secret and the base64 HTTP Basic credential. A secret
shorter than six characters is not replaced in free text, because replacing it
would rewrite ordinary words.

A token row belongs to the declaration that created it. The store records
which selector was declared against which `token_store` path, and the next
start deletes the row of any recorded selector that the file no longer
declares — including when the whole `clients` section was removed while the
server was stopped. A row the record does not name, such as one written by
another configuration sharing the database, is left alone.

A token response without `expires_in` is assumed to last one hour, measured
from the stored row's `updated_at`. An identity provider that omits the field
therefore causes one token request per hour per source instead of a token that
is never refreshed.

A token endpoint taken from discovery that answers 404 or 410 drops the cached
authorization-server metadata and is re-discovered once; a second 404 is
definitive. An explicit `token_endpoint` is never re-discovered.

Precedence is replacement, not merging. When any `--oauth-scope` value is
given, the file's `scope` mapping is ignored as a whole rather than combined
with it, and the same holds for `client_concurrency`. The repeatable
`--oauth-scope` flag defaults to an empty list, so giving it no value cannot
be told apart from omitting it: an empty list falls through to the file, and
a file `scope` cannot be overridden to *nothing* from the command line.
Remove the key instead.

The section is checked: an unknown key is rejected at startup, because a typo
such as `introspection_ur1` would otherwise leave a security control silently
off. A value of the wrong type names its key, for example
`oauth.scope must be a mapping from a scope to its entries, or a list of
SCOPE=ENTRY[,ENTRY] strings` or `oauth.scopes must be a string or a list of
strings`. A wrong-typed scalar is rejected even when its flag is also given,
because the flag wins at runtime and the typo would otherwise stay hidden
until the flag was removed. A bad per-client limit names its source, for
example `oauth.client_concurrency['reports-bot'] must be a positive integer`.

A setting in the file without an issuer — in either the file or a flag — is a
startup error, the same as the flags. The audience gate runs on the merged
value: the `openapi`, `mcp` and `gateway` commands derive an audience when
they can, and `local-hub` needs an explicit `audience` or `--server-url`
because its bind address is never a public audience.

Token verification runs on the vendored `zerodep` JWS module, so no OAuth path
needs a third-party cryptography package. RS256/RS384/RS512, PS256/PS384/PS512
and ES256/ES384 are supported; the default allowlist is `RS256` and `ES256`.
The standalone `openapi` command therefore works with the base install.

The module applies any public-key operation through `libcrypto` over `ctypes`
when the library is available, and falls back to pure Python otherwise; the
result is the same either way. `ZERODEP_JWT_BACKEND=python` forces the
fallback, and `toolregistry_server._vendor.jwt.BACKEND` reports which one is
active.

### Callback allowlist, outbound screening and the panel flow

`redirect_uris` lists the callback URLs an `authorization_code` entry may use,
compared exactly. `start_authorization` refuses any other `redirect_uri`
before discovery runs or a pending row is written, an empty list refuses every
flow, and completion compares the pending row's URI with the current list
again, so a callback removed by a reload cannot finish a flow started before
it.

The comparison is textual, and the browser normalises the URL it sends: the
host is lowercased and a default port is dropped. Write the entry in that
normalised form (`https://panel.example.com/ext/gateway/oauth/callback`, not
`https://Panel.Example.com:443/...`), or the callback is refused.

Every OAuth request the server makes on its own behalf — discovery, JWKS,
introspection and the upstream token POST — is screened by
`toolregistry.netpolicy` before a connection opens. A loopback, link-local or
private host (for example `169.254.169.254`) is refused unless the operator
opts in: `--oauth-allow-private-hosts` or `oauth.allow_private_hosts` for the
resource server, and a per-entry `allow_private_hosts: true` in
`oauth.clients`. On the upstream side a refusal is transient and keeps the
stored grant; its message names neither the URL nor a resolved address.

Redirects are refused on every screened request, so a 3xx cannot re-aim the
request at another host or replay an introspection credential there.

The policy ends at the dial, as it does for MCP and OpenAPI sources: it
resolves the name, judges every answer, and then hands the URL to the HTTP
client, which resolves it again. A name whose DNS answer changes between the
two lookups (DNS rebinding, core #399) can therefore still reach an address
the first lookup did not return. Pinning the checked address to the
connection needs a transport that accepts one; the OAuth clients use
`urllib`, which does not.

The gateway panel drives the authorization-code flow. A source with
`reauth_required` shows an **Authorize** button, which calls
`POST /ext/gateway/sources/<id>/authorize` with the panel's callback URL,
`<panel origin>/ext/gateway/oauth/callback`. List that exact URL in the
entry's `redirect_uris` and register it at the authorization server. The
callback completes the exchange, retries the source, and answers with a small
HTML page that echoes neither the code nor a token. Without an `oauth.clients`
declaration both routes answer 503.

While the server runs, every applied OAuth-protected MCP source is checked
every 300 seconds. A definitive token-endpoint rejection, or an HTTP 401/403
from a short probe session, unregisters the source's tools and marks it
`failed` with `reauth_required`; a rotated token reconnects the source with
the new header; a transient failure changes nothing until the next pass. A
revoked upstream grant therefore surfaces within one interval instead of at
the next restart.

Two processes may share one token store. Token acquisition for a source holds
an exclusive `flock` on a per-source file in `<token_store>.locks/` (directory
`0700`, files `0600`), so a second process waits, re-reads the row and reuses
the token instead of requesting another; an identity provider that rotates
refresh tokens on use no longer retires one process's result. The kernel drops
the lock when its holder exits, so there is no stale lease, and a wait longer
than 30 seconds is a transient store error. The store must be on a local
filesystem: `flock` is not reliable over NFS.

`apply_config` and `registry_from_config` accept an `oauth_provider`. Without
one, a config whose `oauth.clients` has entries raises `ConfigError` rather
than connecting a protected source unauthenticated.

Startup resolves the tokens of protected sources concurrently (at most eight
at once) and then registers in declaration order, and network I/O no longer
holds the lock that status reads use, so the panel stays responsive while a
slow source connects.

## Known gaps

These are tracked, not available today:

- No built-in authorization server and no client-registration commands.
- Introspection does not make opaque tokens usable: local JWT verification
  still runs first.

## See also

- [Authentication guide](../guides/authentication.md) — verification rules and credential precedence.
- [Deploying with an external authorization server](../guides/oauth-deployment.md).
- [Connecting OAuth clients](../guides/oauth-clients.md).
- Changelog entries for the OAuth resource server (#234) and the OpenAPI
  scheme (#235) — the flag surface is not repeated there.
