---
title: CLI Reference
---

# Command-Line Interface

`toolregistry-server` provides a CLI for running servers without writing custom code.

## Usage

```bash
toolregistry-server [options] <subcommand> [subcommand-options]
```

## Top-Level Options

| Option | Description |
|--------|-------------|
| `--version`, `-V` | Show version and exit |
| `--no-banner` | Disable the startup banner |

## Common Subcommand Options

| Option | Description |
|--------|-------------|
| `--env PATH` | Path to .env file (default: `.env` in the current directory) |
| `--no-env` | Skip loading .env file |
| `--config PATH` | Path to a JSONC or YAML configuration file |
| `--profile PROFILE` | Deployment profile: `remote`, `local`, or a profile declared under `profiles:` in the config file |
| `--config-reload` / `--no-config-reload` | Watch the config file and apply a source-level diff when it changes (default: disabled) |
| `--no-config-write` | Keep runtime source changes in memory; never rewrite the config file |

## Subcommands

### `openapi` - Start an OpenAPI Server

```bash
toolregistry-server openapi [options]
```

The standalone server always runs on the vendored `httpserver`: it needs no
extra, and it has no `--backend` and no `--reload` (both were uvicorn-only).
The gateway command keeps its `--backend` choice.

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | - | Path to a JSONC or YAML configuration file (required) |
| `--host HOST` | `0.0.0.0` | Bind host |
| `--port PORT` | `8000` | Bind port |
| `--tokens PATH` | - | File of Bearer tokens, one per line (`#` starts a comment). Tokens are compared in constant time; `API_BEARER_TOKEN` (comma-separated) is read as well |
| `--execution-log` / `--no-execution-log` | on with `--keys-db` | Record every tool call in the registry execution log. See [Authentication](../guides/authentication.md#execution-log) |
| `--oauth-issuer URL` | - | Authorization-server issuer (`iss`) to accept. Enables OAuth bearer verification; tokens are verified locally against the issuer's JWKS |
| `--oauth-audience URL` | derived from `--host`/`--port` | Expected `aud` or RFC 8707 `resource` value. Required when the URL cannot be derived |
| `--oauth-jwks URL` | discovered from RFC 8414/OIDC metadata, else `<issuer>/.well-known/jwks.json` | JWKS endpoint override |
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | Map one token scope to namespace patterns and tool overrides; repeatable. An entry is a namespace pattern, `+tool-name` to allow one tool, or `-tool-name` to deny it. Unmapped scopes grant nothing |
| `--oauth-scopes "SCOPE ..."` | - | Space-separated scopes advertised in protected-resource metadata. Omitted when empty, and by default when an issuer is set; without OAuth the metadata keeps advertising `mcp` |
| `--oauth-authorization-url URL` | from AS metadata | Authorization endpoint for the OpenAPI `authorizationCode` flow |
| `--oauth-token-url URL` | from AS metadata | Token endpoint for the OpenAPI `authorizationCode` flow |
| `--oauth-refresh-url URL` | - | Optional `refreshUrl`; omitted unless set |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | Token claim that may carry a client's in-flight call limit; a non-positive or non-integer value is ignored, and a wider value never raises the client's current budget |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | Static in-flight call limit for one OAuth client; repeatable. A valid concurrency claim wins |
| `--oauth-introspection-url URL` | - | RFC 7662 introspection endpoint; every locally verified token is confirmed with it |
| `--oauth-introspection-client-id ID` | - | Client id for the introspection endpoint (HTTP Basic); required with the URL |
| `--oauth-introspection-client-secret SECRET` | - | Client secret for the introspection endpoint (HTTP Basic); required with the URL |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | How long one introspection result is reused; bounds how long a revoked token is still accepted. `0` disables caching |
| `--oauth-introspection-optional` | `false` | Fall back to local verification when the introspection endpoint fails; by default a failure rejects the token |
| `--oauth-allow-private-hosts` | `false` | Let OAuth discovery, JWKS and introspection reach a loopback, link-local or private address; refused by default |
| `--max-body-size BYTES` | `1048576` (1 MiB) | Largest accepted request body. Must be positive; a larger body returns `413` with a JSON error |
| `--profile PROFILE` | - | Deployment profile: `remote`, `local`, or a profile declared under `profiles:` in the config file |
| `--regex-timeout SECONDS` | `0.05` | Per-match ceiling for spec-supplied regexes (requires the `regex` extra). `0` disables execution |
| `--regex-budget SECONDS` | `0.25` | Per-request ceiling shared by all spec-supplied regex matches. `0` disables execution |
| `--strict-spec-regex` | `false` | Abort startup when an OpenAPI source contains an invalid regex; by default only the tools whose own schema contains it are dropped |

**Example:**

```bash
toolregistry-server openapi \
  --config config.yaml \
  --port 8000 \
  --tokens tokens.txt \
  --profile remote
```

### `mcp` - Start an MCP Server

```bash
toolregistry-server mcp [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | - | Path to a JSON, JSONC, or YAML configuration file (required) |
| `--transport TYPE` | `stdio` | Transport type: `stdio`, `sse`, `streamable-http`, or `http` |
| `--host HOST` | `127.0.0.1` | Bind host (for SSE/HTTP transports) |
| `--port PORT` | `8000` | Bind port (for SSE/HTTP transports) |
| `--tokens PATH` | - | File of Bearer tokens, one per line, for `streamable-http`/`http`. `API_BEARER_TOKEN` (comma-separated) is read as well |
| `--keys-db PATH` | - | SQLite key store; scoped client keys are enforced on every MCP surface |
| `--allow-operator-token` | `false` | Accept a static operator token on MCP even when a key store is configured |
| `--client-concurrency N` | `8` | In-flight calls one client may run at once on `sse`/`streamable-http`; a key can lower it but never raise it |
| `--concurrency-timeout SECONDS` | `30` | How long a call waits for a slot before it returns `-32000` |
| `--execution-log` / `--no-execution-log` | on with `--keys-db` | Record every tool call in the registry execution log. See [Authentication](../guides/authentication.md#execution-log) |
| `--oauth-issuer URL` | - | Authorization-server issuer (`iss`) to accept. Enables OAuth bearer verification; tokens are verified locally against the issuer's JWKS |
| `--oauth-audience URL` | derived from `--host`/`--port` | Expected `aud` or RFC 8707 `resource` value. Required when the URL cannot be derived |
| `--oauth-jwks URL` | discovered from RFC 8414/OIDC metadata, else `<issuer>/.well-known/jwks.json` | JWKS endpoint override |
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | Map one token scope to namespace patterns and tool overrides; repeatable. An entry is a namespace pattern, `+tool-name` to allow one tool, or `-tool-name` to deny it. Unmapped scopes grant nothing |
| `--oauth-scopes "SCOPE ..."` | - | Space-separated scopes advertised in protected-resource metadata. Omitted when empty, and by default when an issuer is set; without OAuth the metadata keeps advertising `mcp` |
| `--oauth-authorization-url URL` | from AS metadata | Authorization endpoint for the OpenAPI `authorizationCode` flow |
| `--oauth-token-url URL` | from AS metadata | Token endpoint for the OpenAPI `authorizationCode` flow |
| `--oauth-refresh-url URL` | - | Optional `refreshUrl`; omitted unless set |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | Token claim that may carry a client's in-flight call limit; a non-positive or non-integer value is ignored, and a wider value never raises the client's current budget |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | Static in-flight call limit for one OAuth client; repeatable. A valid concurrency claim wins |
| `--oauth-introspection-url URL` | - | RFC 7662 introspection endpoint; every locally verified token is confirmed with it |
| `--oauth-introspection-client-id ID` | - | Client id for the introspection endpoint (HTTP Basic); required with the URL |
| `--oauth-introspection-client-secret SECRET` | - | Client secret for the introspection endpoint (HTTP Basic); required with the URL |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | How long one introspection result is reused; bounds how long a revoked token is still accepted. `0` disables caching |
| `--oauth-introspection-optional` | `false` | Fall back to local verification when the introspection endpoint fails; by default a failure rejects the token |
| `--oauth-allow-private-hosts` | `false` | Let OAuth discovery, JWKS and introspection reach a loopback, link-local or private address; refused by default |
| `--server-url URL` | derived from `--host`/`--port` | Public URL advertised in OAuth protected-resource metadata (required behind a reverse proxy) |
| `--tools-list-ttl-ms MS` | `300000` | `ttlMs` cache hint for `tools/list`; `0` disables caching |
| `--tools-list-cache-scope {public,private}` | `private` | `cacheScope` hint for `tools/list` |
| `--profile PROFILE` | - | Deployment profile: `remote`, `local`, or a profile declared under `profiles:` in the config file |

!!! note
    `--client-concurrency` and `--concurrency-timeout` apply to the network
    transports. `stdio` validates both but runs unthrottled: it serves one
    client with no per-client credential, so a quota would only throttle that
    client against itself.

**Examples:**

```bash
# stdio transport (for Claude Desktop, etc.)
toolregistry-server mcp --config config.json

# Streamable HTTP transport
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --port 8000

# SSE transport
toolregistry-server mcp \
  --config config.json \
  --transport sse \
  --port 8000

# With bearer token authentication (streamable-http)
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --tokens tokens.txt
```

### `gateway` - Serve OpenAPI, MCP and the admin panel from one process

```bash
toolregistry-server gateway [options]
```

One ASGI application mounts the OpenAPI adapter at `/openapi`, MCP
streamable-HTTP at `/mcp`, MCP SSE at `/sse` and the admin panel at `/admin`.
Each frontend has a configurable path and an enable flag that is checked on
every request. The admin panel is disabled by default.

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | - | Path to a JSONC or YAML configuration file (required) |
| `--host HOST` | `0.0.0.0` | Bind host |
| `--port PORT` | `8000` | Bind port |
| `--socket PATH` | - | Bind a Unix domain socket instead of `--host`/`--port` (POSIX only; takes precedence). Clients need a UDS-capable HTTP transport |
| `--tokens PATH` | - | File of Bearer tokens applied to every frontend. `API_BEARER_TOKEN` is read as well |
| `--oauth-issuer URL` | - | Authorization-server issuer (`iss`) to accept. Enables OAuth bearer verification; tokens are verified locally against the issuer's JWKS |
| `--oauth-audience URL` | derived from `--host`/`--port` | Expected `aud` or RFC 8707 `resource` value. Required when the URL cannot be derived |
| `--oauth-jwks URL` | discovered from RFC 8414/OIDC metadata, else `<issuer>/.well-known/jwks.json` | JWKS endpoint override |
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | Map one token scope to namespace patterns and tool overrides; repeatable. An entry is a namespace pattern, `+tool-name` to allow one tool, or `-tool-name` to deny it. Unmapped scopes grant nothing |
| `--oauth-scopes "SCOPE ..."` | - | Space-separated scopes advertised in protected-resource metadata. Omitted when empty, and by default when an issuer is set; without OAuth the metadata keeps advertising `mcp` |
| `--oauth-authorization-url URL` | from AS metadata | Authorization endpoint for the OpenAPI `authorizationCode` flow |
| `--oauth-token-url URL` | from AS metadata | Token endpoint for the OpenAPI `authorizationCode` flow |
| `--oauth-refresh-url URL` | - | Optional `refreshUrl`; omitted unless set |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | Token claim that may carry a client's in-flight call limit; a non-positive or non-integer value is ignored, and a wider value never raises the client's current budget |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | Static in-flight call limit for one OAuth client; repeatable. A valid concurrency claim wins |
| `--oauth-introspection-url URL` | - | RFC 7662 introspection endpoint; every locally verified token is confirmed with it |
| `--oauth-introspection-client-id ID` | - | Client id for the introspection endpoint (HTTP Basic); required with the URL |
| `--oauth-introspection-client-secret SECRET` | - | Client secret for the introspection endpoint (HTTP Basic); required with the URL |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | How long one introspection result is reused; bounds how long a revoked token is still accepted. `0` disables caching |
| `--oauth-introspection-optional` | `false` | Fall back to local verification when the introspection endpoint fails; by default a failure rejects the token |
| `--oauth-allow-private-hosts` | `false` | Let OAuth discovery, JWKS and introspection reach a loopback, link-local or private address; refused by default |
| `--server-url URL` | derived from `--host`/`--port` | Public MCP URL used in protected-resource metadata |
| `--backend {fastapi,httpserver}` | `fastapi` | Multiple frontends require `fastapi`; `httpserver` can serve only the openapi frontend |
| `--openapi-path PATH` | `/openapi` | OpenAPI mount path (rejected with `--backend httpserver`, which serves at the listener root) |
| `--mcp-path PATH` | `/mcp` | MCP streamable-HTTP mount path |
| `--sse-path PATH` | `/sse` | MCP SSE mount path |
| `--admin-path PATH` | `/admin` | Admin panel mount path |
| `--openapi` / `--no-openapi` | enabled | Enable or disable the OpenAPI frontend |
| `--mcp` / `--no-mcp` | enabled | Enable or disable MCP streamable-HTTP |
| `--sse` / `--no-sse` | enabled | Enable or disable MCP SSE |
| `--admin` / `--no-admin` | disabled | Enable or disable the admin panel. Enabling requires an operator token |
| `--admin-token TOKEN` | - | Operator token accepted by the admin panel. Prefer `--admin-token-file` |
| `--admin-token-file PATH` | - | Read the admin operator token from a file |
| `--keys-db PATH` | - | SQLite store backing the panel's Keys page |
| `--allow-operator-token` | `false` | Accept a static operator token on MCP even when a key store is configured |
| `--client-concurrency N` | `8` | In-flight calls one client may run at once; a key can lower it but never raise it |
| `--concurrency-timeout SECONDS` | `30` | How long a call waits for a slot before it returns `-32000` |
| `--execution-log` / `--no-execution-log` | on with `--keys-db` | Record every tool call in the registry execution log. See [Authentication](../guides/authentication.md#execution-log) |
| `--admin-allow-private-hosts` | `false` | Let the panel fetch a source URL that resolves to a private address |
| `--no-config-write` | `false` | Do not persist runtime frontend changes to the config file |
| `--max-body-size BYTES` | `1048576` (1 MiB) | Largest request body accepted by the OpenAPI frontend |
| `--regex-timeout SECONDS` | `0.05` | Per-match ceiling for spec-supplied regexes |
| `--regex-budget SECONDS` | `0.25` | Per-request ceiling for spec-supplied regexes |
| `--strict-spec-regex` | `false` | Abort startup on an invalid regex in an OpenAPI source |
| `--tools-list-ttl-ms MS` | `300000` | `ttlMs` cache hint for MCP `tools/list` |
| `--tools-list-cache-scope {public,private}` | `private` | `cacheScope` hint for MCP `tools/list` |
| `--profile PROFILE` | - | Deployment profile |

**Example:**

```bash
toolregistry-server gateway \
  --config config.yaml \
  --port 8000 \
  --openapi-path /api \
  --no-sse \
  --tokens tokens.txt \
  --admin \
  --admin-token-file operator-token.txt
```

A disabled frontend answers `404` without touching the registry. Frontend
paths and flags can also be stored under `gateway.frontends` in the config
file; see the [Configuration Guide](../../guides/configuration.md#gateway-frontends).
The admin panel uses its own operator token, not the `--tokens` set; extension
pages live under the panel mount (for example `/admin/ext/gateway/keys`). See
the [Gateway Admin Panel guide](../../guides/gateway-admin.md) for the Keys,
Frontends and Sources pages.

### `local-hub` - Run one shared gateway for local MCP clients

```bash
toolregistry-server local-hub [run|ensure|stop|status] --config config.yaml --name default
```

Serves the gateway as one resident process per machine, so several MCP clients
share one registry, one tool schema set and one set of caches. POSIX only; on
Windows the actions refuse and point at `gateway`. See the
[Local Hub guide](../../guides/local-hub.md).

A hub started by `ensure` or by a shim is spawned as
`toolregistry-server local-hub run ...`. Set
`TOOLREGISTRY_LOCAL_HUB_COMMAND` to start it with a different launcher — a Hub
deployment uses `TOOLREGISTRY_LOCAL_HUB_COMMAND=toolregistry-hub`, so the
resident process keeps the Hub launcher's registry policy. The value is split
like a shell command and used verbatim, so a wrapper or another interpreter can
be named. Precedence: the variable when it holds non-blank text, then
`toolregistry-server` on `PATH`, then the current interpreter running the
package bootstrap. The spawned hub inherits the variable.

| Option | Default | Description |
|--------|---------|-------------|
| `action` | `run` | `run` serves in the foreground; `ensure` adopts a live hub or starts a detached one and prints its endpoint; `stop` signals the owner; `status` reports it |
| `--name NAME` | `default` | Hub name; differently named hubs coexist |
| `--config PATH` | - | Tool sources. Required to serve; `stop` and `status` do not need it |
| `--host HOST` | `127.0.0.1` | Bind host |
| `--port PORT` | `0` | Bind port; `0` lets the OS choose, so several hubs coexist |
| `--socket PATH` | - | Bind a Unix socket instead of TCP |
| `--idle-timeout SECONDS` | `0` | Exit after that long without a request or an open MCP/SSE session; `0` stays resident |
| `--timeout SECONDS` | - | Wait limit for `ensure` and `stop` |
| `--json` | off | `status` prints the endpoint document |
| `--oauth-issuer URL` | - | Authorization-server issuer (`iss`) to accept. Enables OAuth bearer verification; tokens are verified locally against the issuer's JWKS |
| `--oauth-audience URL` | from `--server-url`, else required | Expected `aud` or RFC 8707 `resource` value. A hub binds loopback or a Unix socket, so the audience must be explicit whenever an issuer is set |
| `--oauth-jwks URL` | discovered from RFC 8414/OIDC metadata, else `<issuer>/.well-known/jwks.json` | JWKS endpoint override |
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | Map one token scope to namespace patterns and tool overrides; repeatable. An entry is a namespace pattern, `+tool-name` to allow one tool, or `-tool-name` to deny it. Unmapped scopes grant nothing |
| `--oauth-scopes "SCOPE ..."` | - | Space-separated scopes advertised in protected-resource metadata. Omitted when empty, and by default when an issuer is set; without OAuth the metadata keeps advertising `mcp` |
| `--oauth-authorization-url URL` | from AS metadata | Authorization endpoint for the OpenAPI `authorizationCode` flow |
| `--oauth-token-url URL` | from AS metadata | Token endpoint for the OpenAPI `authorizationCode` flow |
| `--oauth-refresh-url URL` | - | Optional `refreshUrl`; omitted unless set |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | Token claim that may carry a client's in-flight call limit; a non-positive or non-integer value is ignored, and a wider value never raises the client's current budget |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | Static in-flight call limit for one OAuth client; repeatable. A valid concurrency claim wins |
| `--oauth-introspection-url URL` | - | RFC 7662 introspection endpoint; every locally verified token is confirmed with it |
| `--oauth-introspection-client-id ID` | - | Client id for the introspection endpoint (HTTP Basic); required with the URL |
| `--oauth-introspection-client-secret SECRET` | - | Client secret for the introspection endpoint (HTTP Basic); required with the URL |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | How long one introspection result is reused; bounds how long a revoked token is still accepted. `0` disables caching |
| `--oauth-introspection-optional` | `false` | Fall back to local verification when the introspection endpoint fails; by default a failure rejects the token |
| `--oauth-allow-private-hosts` | `false` | Let OAuth discovery, JWKS and introspection reach a loopback, link-local or private address; refused by default |
| `--server-url URL` | from `--host`/`--port` | Public MCP URL advertised in protected-resource metadata |

!!! note
    A hub needs an explicit audience whenever an issuer is set, with or
    without `--socket`: it binds `127.0.0.1` on an ephemeral port or a Unix
    socket, and neither address is one a client carries in a token. Either
    `--oauth-audience` or `--server-url` supplies it, by flag or from the
    [`oauth` section](../oauth.md#configuration-file) of the config file. See
    the [OAuth reference](../oauth.md#per-command-behaviour).

The hub publishes an endpoint document and takes an exclusive lock that makes
ownership unambiguous. `status` exits `0` when the hub runs and `3` when it
does not; `stop` exits `3` when nothing owns the name, and refuses to signal
when nothing accepts on the published address, so a stale document cannot
target an unrelated reused pid.

### `hub-shim` - Expose a local hub to one stdio MCP client

```bash
toolregistry-server hub-shim --name default --config config.yaml
```

Speaks MCP over stdio to the client and forwards to the named hub over
streamable HTTP. `initialize` is answered locally and `tools/list` is empty
while the hub is down; a call then fails once with `-32000` and the shim
reconnects in the background (0.25 s doubling to 5 s). When the hub's tool
list differs from the one the client was last served, the shim sends
`notifications/tools/list_changed`.

| Option | Default | Description |
|--------|---------|-------------|
| `--name NAME` | `default` | Hub to attach to |
| `--config PATH` | - | Config used when the shim has to start the hub; without it the shim waits instead of starting one |
| `--socket PATH` | - | Unix socket for a hub the shim starts |
| `--connect-timeout SECONDS` | `5` | Limit for one connect + initialize attempt |
| `--no-spawn` | off | Never start a hub; report it as unavailable |

POSIX only, like `local-hub`. One shim per client, all sharing one hub.

### `keys` — Manage scoped client API keys

```bash
toolregistry-server keys --db PATH <command> [command-options]
```

Works on the SQLite key store named by `--db`, the same file the servers read
with `--keys-db`. `--db` is required and comes before the command. The scope
model is described in the
[Authentication guide](../../guides/authentication.md#scoped-api-keys).

| Command | Purpose |
|---------|---------|
| `create` | Create a key and print its secret once |
| `list` | List keys ordered by last use |
| `get KEY_ID` | Show one key with its usage counters |
| `rotate KEY_ID` | Issue a new secret for an existing key and print it once |
| `update KEY_ID` | Change a key's label, scopes, expiry or concurrency |
| `revoke KEY_ID` | Revoke a key immediately |
| `set-concurrency KEY_ID` | Change the key's in-flight call limit |

`list` takes `--limit` (default 50) and `--offset`. Every command accepts
`--json` for machine-readable output.

**Rotating a key.** `keys rotate KEY_ID` issues a new secret for a key that
already exists. The key id, label, scopes, expiry, concurrency and usage
history are kept; only the secret changes. The new secret is printed once, and
the old one is refused on its next request. Rotation is refused for a revoked
key and for an expired key: extend or clear the expiry with `update` first,
then rotate.

**Updating a key.** `keys update KEY_ID` changes the named fields and leaves
the rest alone:

| Flag | Effect |
|------|--------|
| `--label LABEL` | New non-empty label |
| `--namespace PATTERN` | Replace the namespace allow-list; repeatable. `*` allows every namespace and `prefix/*` allows a prefix |
| `--allow-tool NAME` | Replace the tool-override map with an allow; repeatable |
| `--deny-tool NAME` | Replace the tool-override map with a deny; repeatable |
| `--expires ISO8601` | New expiry timestamp; UTC is assumed when no offset is given. Mutually exclusive with `--clear-expiry` |
| `--clear-expiry` | Remove the expiry so the key does not expire |
| `--concurrency N` | New in-flight call limit. Mutually exclusive with `--clear-concurrency` |
| `--clear-concurrency` | Drop the override and use the server's `--client-concurrency` |

The scope flags merge by category, not wholesale. `--namespace` replaces only
the namespace list, and `--allow-tool` / `--deny-tool` together define only the
tool-override map, with a name given to both flags denied. A category without a
flag keeps its current value, so `update KEY_ID --label build-bot-v2` changes
the label and nothing else. Note that `create` spells the expiry flag
`--expires-at` and `update` spells it `--expires`; neither form accepts a time
in the past. An update is refused when the key is revoked. Changes apply to the
next request, with no restart.

**Usage counters.** `keys list --usage` adds a `usage` object to every record,
and `keys get` always includes one. The object carries `calls`, `ok`, `error`,
`busy`, `denied` and `last_success_at`; the text output prints a `calls:` line
with the four outcomes and a `last ok:` line. The counters are persisted in the
key database, so they survive a restart and do not depend on the in-memory
execution log, and they are not cleared by rotation, revocation or expiry. A
usage object never contains a secret.

**Exit codes.** `0` on success. `1` when `KEY_ID` is unknown. `2` when the store
refuses the change — a revoked key, an expiry in the past, an invalid
concurrency — or when `update` is given no field to change.

## Configuration File

See the [Configuration Guide](../../guides/configuration.md) for details on the JSON, JSONC, or YAML configuration format.

## Programmatic API

### `App` Class (recommended)

The `App` class is the canonical programmatic entry point. Subclass it and override `prepare_registry()` to build your registry with custom logic:

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

class MyApp(App):
    def prepare_registry(self):
        registry = ToolRegistry()
        registry.register(my_tool)
        return registry

if __name__ == "__main__":
    MyApp().serve_openapi(host="0.0.0.0", port=8000)
    # or: MyApp().serve_mcp(transport="stdio")
```

### `CLI` Class — Custom Subcommands

The `CLI` class is the canonical CLI entry point. Subclass it and override `configure_subparsers()` to add custom arguments:

```python
from argparse import ArgumentParser
from toolregistry_server import CLI, App

class MyCLI(CLI):
    def configure_subparsers(self, subparsers: dict[str, ArgumentParser]):
        # Add a custom flag to the openapi subcommand
        subparsers["openapi"].add_argument(
            "--my-flag", action="store_true", help="Enable custom feature"
        )

if __name__ == "__main__":
    MyCLI().main()
```

Use the standalone `run_cli()` helper for simple entry points:

```python
from toolregistry_server import run_cli

run_cli()
```

### `ServerIdentity` — Custom Banner & Version

Pass a `ServerIdentity` to customise the startup banner and `--version` output:

```python
from toolregistry_server import App, ServerIdentity

identity = ServerIdentity(
    name="my-server",
    version="1.2.3",
    description="My custom tool server",
)

App(identity=identity).serve_openapi()
```

## Deployment Profiles

The `--profile` flag applies tag-based tool filtering after the registry is built:

| Profile | Effect |
|---------|--------|
| `remote` | Disables tools tagged `file_system`, `destructive`, or `privileged` |
| `local` | Disables tools tagged `network` |
| *(none)* | No filtering (default) |

A config file can define its own profiles under `profiles:`; those settings take precedence over the built-in tag sets, and an unknown profile name is logged as a warning and ignored:

```yaml
profiles:
  staging:
    disable_tags: [destructive, privileged]
    enable: [calculator]      # namespace patterns force-enabled after tag filtering
    disable: [internal/]      # namespace patterns force-disabled after tag filtering
```

Each profile accepts `disable_tags` (replaces the built-in set), `enable` and `disable` (namespace patterns, applied in that order).

Use `remote` when serving tools to end users who should not have access to the server's own filesystem or privileged operations:

```bash
toolregistry-server openapi --config config.json --profile remote
toolregistry-server mcp --config config.json --profile remote
```
