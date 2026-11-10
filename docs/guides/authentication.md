# Authentication

`toolregistry-server` provides built-in Bearer token authentication for both OpenAPI and MCP Streamable-HTTP endpoints.

## Overview

The authentication module supports:

- Multiple tokens loaded from a file
- Bearer token validation on all incoming requests
- Optional scoped API keys: namespace allow-list, per-tool overrides, expiry and revocation
- Runtime token management (add/remove tokens without restart)
- Dynamic enable/disable

## Setting Up Authentication

### Via Code — `App`-based (recommended)

`App` forwards `tokens_path` to the adapter, which reads the file and the `API_BEARER_TOKEN` environment variable (comma-separated):

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

registry = ToolRegistry()
registry.register(my_tool)

App().serve_openapi(
    registry=registry,
    tokens_path="tokens.txt",   # one token per line, # comments ignored
    host="0.0.0.0",
    port=8000,
)
```

The same pattern works for MCP Streamable-HTTP:

```python
App().serve_mcp(
    registry=registry,
    transport="streamable-http",
    tokens_path="tokens.txt",
    host="0.0.0.0",
    port=8000,
)
```

To pass tokens inline without a file, build the OpenAPI adapter directly (the MCP adapter takes a token file or `API_BEARER_TOKEN`):

```python
from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import OpenAPIAdapter

OpenAPIAdapter(RouteTable(registry), tokens=["token-one", "token-two"]).run(
    host="0.0.0.0", port=8000
)
```

### Via CLI — `--tokens` flag

```bash
# OpenAPI server with token file
toolregistry-server openapi --config config.json --tokens tokens.txt

# MCP streamable-http server with token file
toolregistry-server mcp --config config.json --transport streamable-http --tokens tokens.txt
```

Token file format (one token per line; lines starting with `#` are ignored):

```
# My API tokens
token-one
token-two
token-three
```

## Scoped API Keys

`--tokens` credentials are all-or-nothing: a token can call every tool. To
issue a client credential that is limited to some tools, use the API key
store. The store is optional; omit `--keys-db` and the server behaves exactly
as before.

### Creating and managing keys

```bash
# Create a key for the calculator namespace. The secret is printed once.
toolregistry-server keys --db keys.db create \
  --label build-bot \
  --namespace calculator \
  --deny-tool calculator-dangerous

# List keys ordered by last use (paginated).
toolregistry-server keys --db keys.db list --limit 20

# Show or revoke one key by id.
toolregistry-server keys --db keys.db get key_...
toolregistry-server keys --db keys.db revoke key_...
```

`create` prints the secret once. The store keeps only a scrypt hash with a
per-key salt, so the secret cannot be recovered. `list`, `get` and `revoke`
never show it. Add `--json` to any subcommand for machine-readable output,
and `--expires-at ISO8601` to make a key expire.

### Starting a server with keys

```bash
toolregistry-server openapi --config config.json --keys-db keys.db
toolregistry-server mcp --config config.json --transport streamable-http \
  --keys-db keys.db
toolregistry-server gateway --config config.json --keys-db keys.db
```

The key database may be combined with `--tokens`. Static `--tokens`
credentials keep full, unscoped access and remain the bootstrap/legacy path;
scoping applies only to keys created in the database. `--keys-db` is ignored
on the MCP `stdio` transport, which carries no request credential.

The `gateway` command builds one resolver per process and passes it to the
OpenAPI app, the streamable-HTTP server and the SSE app, so all three
frontends share the key store and enforce the same four surfaces. The RFC
9728 metadata route is served whenever a key store or a static token is
configured.

### Scope rules

A scope is a namespace allow-list plus optional per-tool overrides:

```json
{
  "namespaces": ["calculator", "web/*"],
  "tools": {
    "calculator-dangerous": false,
    "beta-echo": true
  }
}
```

- A tool is allowed when its namespace matches an entry. `"*"` matches every
  namespace; `"web/*"` matches `web` and every namespace below it; any other
  value is an exact match.
- A tool entry always wins. `false` denies a tool inside an allowed namespace;
  `true` allows a tool outside the list.
- An empty scope denies every tool. If you omit `--namespace`, the key can
  call nothing.

### Enforcement and lifecycle

One principal resolver applies the key's scope to all four surfaces: MCP
`tools/list`, MCP `tools/call`, the OpenAPI document at `/openapi.json`, and
the OpenAPI tool routes. A tool outside the scope is absent from listings and
the document, and a call to it is refused (`403` on OpenAPI; an `INVALID_PARAMS`
error on MCP).

The key row is read on every request. Revocation and expiry therefore take
effect on the next request; there is no restart or cache to wait for.
`last_used_at` records only the latest successful use.

Scoped keys keep the `discover_tools` / `call_deferred` pair, because both
enforce the key's scope on the tool they act on: discovery results are
filtered to names the key may call, and `call_deferred` refuses a target
outside the scope with the same message a direct call to it gets. The pair
stays visible whatever the key's namespaces are — the proxies are registered
in the default namespace, and judging them by it would deny discovery to every
key scoped elsewhere — but an explicit per-tool override for either name still
decides, so a key that denies them does not see them or reach them. The target
check reads the live registry rather than the route table, so a tool
registered while a configuration diff is still applying is judged the same
way.

### Per-client isolation

A key is also the identity the server accounts to.

- **Reserved headers are refused.** A request carrying `x-client-id`,
  `x-client-identity`, `x-client-cwd`, `x-client-env`, `x-client-environment`,
  `x-forwarded-user` or `x-remote-user` fails with `400` after authentication
  and is logged with the caller's key. Identity comes from the credential, so
  strip these headers at any proxy in front of the server.
- **Operator tokens are refused on MCP when keys are configured.** With
  `--keys-db`, a static token cannot identify a client, so an MCP request that
  resolves to an unscoped principal is refused for `tools/list` and
  `tools/call`. `--allow-operator-token` re-admits it; OpenAPI and the admin
  panel are unaffected.
- **Concurrency is capped per key.** `--client-concurrency` (default 8) is the
  process ceiling; a key's `max_concurrency` can lower it and never raise it.
  A call that waits longer than `--concurrency-timeout` (default 30 s) returns
  `-32000` "too many concurrent calls".
- **Calls are attributed.** Each call logs `key_id` and `client_id`, and the
  admin panel's Clients tab (backed by `/ext/gateway/clients`) reports calls,
  errors, in-flight and queued counts per client.

The process `PermissionPolicy` is evaluated on every served call as well, on
both the MCP and the OpenAPI frontends; a key's scope can only narrow what the
policy allows.

### Execution log

A deployment that configures `--keys-db` also enables the registry execution
log: a keyed deployment is where a call has a stable identity worth keeping.
Every call that reaches a tool handler — MCP, and OpenAPI on both backends —
writes one entry with the tool, its arguments, the result or the error, the
duration, and `metadata={"key_id": ..., "client_id": ...}`. A call refused by
key scope, the proxy-target check or the process permission policy never
reaches a handler and is not logged; an MCP refusal is still attributed in the
structured log, while an OpenAPI refusal is not recorded server-side.

The structured attribution line is written for every attempt past
authentication, with one of four outcomes: `ok`, `error`, `busy` (the
concurrency limiter) or `denied` (scope, proxy target or policy). Its
`duration_ms` runs from before authorisation; the log row measures only the
handler call.

The log is an in-memory ring buffer of the last 1000 entries per process, read
by the admin panel's Logs page. It is not persisted and resets on restart.
Unless the log was already enabled with `full_record`, entries follow core's
redaction rules: credential-named keys are masked, arguments and results
longer than 2000 JSON characters are replaced by a preview, and tracebacks are
dropped. Arguments are otherwise stored as received, so a tool that takes a
secret in an ordinary argument will have it in the log.

`--execution-log` enables the log without a key store and `--no-execution-log`
disables it; `gateway`, `mcp` and `openapi` all accept both.

### Python API

The admin extension (#187) imports the same store in-process:

```python
from toolregistry_server import ApiKeyStore, PrincipalResolver

store = ApiKeyStore("keys.db")
created = store.create("build-bot", {"namespaces": ["calculator"]})
print(created.secret)  # shown once

resolver = PrincipalResolver(store, operator_tokens=["bootstrap-token"])
principal = resolver.resolve(created.secret)
if principal is not None and principal.allows("calculator", "calculator-add"):
    ...
```

`ApiKeyRecord.to_dict()` never contains the secret or its hash.

## MCP Streamable-HTTP Bearer Auth (new in v0.4.0)

Bearer token authentication is now supported for the MCP Streamable-HTTP transport, in addition to OpenAPI. Use the same `--tokens` flag (or pass `tokens_path` to `App().serve_mcp()`) — no extra configuration required.

```bash
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --port 8000 \
  --tokens tokens.txt
```

Clients connect with a standard `Authorization: Bearer <token>` header.

## OAuth 2.0 Resource Server

The server can accept access tokens minted by an external authorization
server instead of issuing its own credentials. Verification is local: the
token signature is checked against the issuer's JWKS, and no token is ever
introspected or logged per request.

```bash
toolregistry-server gateway \
  --config config.yaml \
  --oauth-issuer https://idp.example.com/realms/dev \
  --oauth-scope tools:read=calculator,web
```

| Option | Description |
|--------|-------------|
| `--oauth-issuer URL` | Exact `iss` value to accept. Setting it enables OAuth verification |
| `--oauth-audience URL` | Expected `aud` or RFC 8707 `resource` value. Defaults to the advertised MCP URL |
| `--oauth-jwks URL` | JWKS endpoint (default: discovered from the issuer's metadata) |
| `--oauth-scope SCOPE=NAMESPACE[,NAMESPACE]` | Map one scope to namespace patterns; repeatable |
| `--oauth-scopes "SCOPE ..."` | Scopes advertised in the protected-resource metadata. Omitted when empty, and by default when an issuer is set |

The flags are accepted by `openapi`, `mcp` (HTTP transports), `gateway` and
`local-hub`.

### Verification rules

- Signature algorithms are limited to RS256 and ES256. `none` and HMAC
  algorithms are rejected, and so is a token whose `kid` is not published.
- `iss` must match `--oauth-issuer` exactly. The token's `aud`, or its RFC 8707
  `resource` claim, must contain the expected audience.
- `exp` and `nbf` are enforced with 60 seconds of clock leeway.
- The JWKS endpoint is taken from `--oauth-jwks` when given. Otherwise it is
  read from RFC 8414 metadata
  (`<issuer>/.well-known/oauth-authorization-server`), then OIDC discovery
  (`<issuer>/.well-known/openid-configuration`), and finally falls back to
  `<issuer>/.well-known/jwks.json`. A provider whose JWKS is not published
  through either metadata document needs the explicit flag.
- The JWKS is cached for 5 minutes. An unknown `kid` may force a refetch, but
  at most once per 30 seconds, so random `kid` values cannot drive outbound
  requests; a rotated key is picked up after that window.
- A failed JWKS fetch rejects the token; startup prefetch is best-effort and
  only logs a warning. A failed prefetch does not block the first request's
  own attempt.

### Scope mapping

The mapping is least privilege: a token whose scopes are not listed in
`--oauth-scope` maps to an empty scope and can call no tool. Each entry grants
the namespaces on the right-hand side, using the same pattern rules as API key
scopes (`*`, `web/*`, exact names):

```bash
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --oauth-issuer https://idp.example.com/realms/dev \
  --oauth-scope tools:read=calculator,web \
  --oauth-scope tools:admin=admin
```

### Credential precedence

API keys from `--keys-db` and static `--tokens` are tried first, then OAuth
tokens. A request credential is therefore only resolved as an OAuth token when
it does not match a key or static token. Omitting `--oauth-issuer` leaves the
previous behaviour untouched.

On MCP streamable-HTTP, the RFC 9728 protected-resource metadata advertises
the configured issuer in `authorization_servers`, so a client can discover the
authorization server. A rejected request carries it directly:
`WWW-Authenticate: Bearer resource_metadata="<metadata URL>"`, with the URL
following the current MCP path; the standalone MCP server and the gateway
both send that challenge. A hub bound to a Unix socket has no derivable URL: pass
`--oauth-audience` explicitly (and `--server-url` if clients should discover
the metadata).

`scopes_supported` is opt-in once an external issuer is configured, because
the authorization server validates the scopes it issues and may reject the
generic `mcp` scope at authorize time. List the scopes the deployment really
issues:

```bash
toolregistry-server gateway \
  --config config.yaml \
  --oauth-issuer https://idp.example.com/realms/dev \
  --oauth-scopes "openid profile email offline_access"
```

`--oauth-scopes ""` omits the field. Without `--oauth-issuer`, the metadata
keeps advertising `mcp`, as before.

### Limitations

Token introspection (RFC 7662) is not implemented: the deployment must issue
JWTs the server can verify offline. An opaque-token authorization server needs
a proxy that introspects, or `--keys-db` instead.

The Python API is available in process: `OAuthSettings`, `OAuthTokenVerifier`
and `build_resolver()` in `toolregistry_server.oauth` build the same verifier
and composite resolver the CLI wires up.

## Making Authenticated Requests

Include the Bearer token in the `Authorization` header:

```bash
curl -X POST http://localhost:8000/calculator/evaluate \
  -H "Authorization: Bearer my-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"expression": "2 + 3"}'
```

## Runtime Token Management (Advanced)

The underlying `BearerTokenAuth` class supports runtime token management without restarting the server. This is useful for advanced scenarios where tokens need to be rotated dynamically:

```python
from toolregistry_server.auth import BearerTokenAuth

auth = BearerTokenAuth(tokens=["initial-token"])

# Add a new token
auth.add_token("new-token")

# Remove a token
auth.remove_token("initial-token")

# Disable authentication entirely
auth.enabled = False

# Re-enable
auth.enabled = True
```

## Unauthenticated Requests

When authentication is enabled, requests without a valid token receive a `401 Unauthorized` response:

```json
{
  "detail": "Invalid or missing bearer token"
}
```

If no tokens are configured, authentication is automatically disabled and all requests are allowed.
