# Gateway Admin Panel

The `gateway` subcommand can host the core ToolRegistry admin panel and load
the gateway admin extension, which adds three operator pages:

| Page | What it manages |
|------|-----------------|
| **Keys** | Inbound client keys for the OpenAPI and MCP frontends |
| **Frontends** | The enable flag and mount path of every gateway frontend |
| **Sources** | Tool sources added, retried or removed at runtime |

The panel is an operator surface. It uses the core panel's own token and
session authentication and is deliberately not part of the `--tokens` set
that protects the tool frontends. Nothing on these pages is available to a
client key.

## Enabling the panel

The admin frontend is disabled by default. Enable it with `--admin` and give
it an operator token with `--admin-token` or, preferably,
`--admin-token-file` so the secret stays out of the process list:

```bash
toolregistry-server gateway \
  --config tools.yaml \
  --keys-db keys.db \
  --admin \
  --admin-token-file operator-token.txt
```

- Enabling the frontend without an operator token is a startup error.
- A gateway serving token does not open the panel: a request that carries
  only a `--tokens` credential gets `401`.
- The mount defaults to `/admin`; `--admin-path` or `gateway.frontends.admin.path`
  changes it.
- Hosting the panel requires core `toolregistry` with the admin extension
  contract (`ADMIN_EXTENSION_API == 1`, core 0.21.0 or newer). On an older
  core the frontend refuses to start with a named error, and the default
  configuration does not load it.

Once enabled, the panel shell is served at the mount path, `http://host:8000/admin/`
by default, and the gateway pages live under it:
`/admin/ext/gateway/keys`, `/admin/ext/gateway/frontends` and
`/admin/ext/gateway/sources`. In the console navigation the extension is a
tab named **Gateway**, and it opens on **Keys**.

Disabling the admin frontend makes the panel root and every extension page
answer `404` on the next request. No restart is needed.

## Keys page

The Keys page manages the scoped inbound keys that the OpenAPI and MCP
frontends accept. It uses the store named by `--keys-db`; without that option
the page answers `503` and the tool frontends keep their static `--tokens`
credentials only. The panel and the tool frontends share one database, so a
key created here is accepted by the frontends on the next request, and a
revoked key is refused on the next request.

The table shows one row per key:

| Column | Meaning |
|--------|---------|
| Label | Operator-facing name |
| Scopes | Namespaces, plus any per-tool allow/deny overrides |
| Created | Creation time |
| Expires | Expiry time, or `Never` |
| Last used | Time of the latest successful authentication |
| Status | `Active`, `Expired` or `Revoked` |

Revoked and expired keys stay in the table so their history remains visible.
The store keeps secrets hashed; no response and no asset contains a plaintext
secret.

### Creating a key

Fill in the form under the table:

- **Label** — required; it is a display name and is not unique.
- **Scopes** — a comma-separated list of namespaces. `*` matches every
  namespace and `web/*` matches `web` and anything below it.
- **Tool overrides** — one `tool=allow` or `tool=deny` per line, for example
  `web-dangerous=deny`. An override always wins over the namespace list, so
  this is how one tool is denied inside an allowed namespace or allowed
  outside it.
- **Expires** — optional local time. The gateway sends it as an absolute
  timestamp and the store rejects a time in the past.

An empty namespace list with no overrides creates a key that can call
nothing; that is the fail-closed default.

On success the page shows the secret once, with a copy button:

!!! warning "The secret is shown once"
    The full secret is returned only by the creation response, which is sent
    with `Cache-Control: no-store`. It is not stored by the page and cannot be
    recovered later. Closing the dialog without copying it means creating a
    new key.

### Revoking a key

**Revoke** asks for confirmation and marks the key revoked immediately. A
client that presents it is refused on its next request. Revocation is
idempotent, and the original revocation time is kept.

### Scoped keys and deferred tools

!!! note "Scoped keys and the discovery proxies"
    A scoped key keeps `discover_tools` and `call_deferred`, and both enforce
    its scope on the tool they act on: discovery returns only names the key may
    call, and a `call_deferred` target outside the scope is refused with the
    same message a direct call to it gets. The pair stays visible whatever the
    key's namespaces are — the proxies live in the default namespace — while an
    explicit per-tool deny for either name still removes it. Operator and
    static-token principals are unaffected. See
    [Authentication](authentication.md).

## Frontends page

The Frontends page lists every gateway frontend — `openapi`, `mcp`, `sse` and
`admin` — with its path, transport and enable flag, and edits the same
registry as `gateway.list_frontends()` and `gateway.update_frontend()`.

- **Enable/disable** takes effect on the next request. A disabled frontend
  answers `404` before its application or the registry runs; the other
  frontends keep serving. The MCP session manager is never rebuilt by a
  toggle.
- **Path** must start with `/`, be unique and not contain whitespace, `?`,
  `#` or `//`. A new path applies on the next request.
- **Persistence**: a change is written back to the config file after a
  `<config>.bak` copy. The result line says whether the change was persisted;
  when it was not, the page shows the reason instead of claiming success.
  A runtime-only deployment (no config path, or `--no-config-write`) keeps the
  change in memory until the process exits.
- **Comment loss**: rewriting a JSONC config drops its comments. The update
  response carries a warning and the page shows it; keep the config in YAML or
  keep a copy if the comments matter.
- **Backend restriction**: multiple frontends require the `fastapi` backend.
  `--backend httpserver` serves only the openapi frontend at the listener
  root, so a gateway with the admin frontend enabled cannot use it, and an
  openapi path override is rejected rather than ignored.

Disabling the `admin` frontend from this page is allowed; the panel then
answers `404` and the page itself becomes unreachable until the flag is turned
back on from the config file or the process arguments.

## Sources page

The Sources page shows the runtime source manager's view: one row per declared
source with its kind, detail, namespace, enabled/total tool count, status and
last error.

| Status | Meaning |
|--------|---------|
| `Applied` | The source registered its tools |
| `Excluded` | The deployment profile filtered the source out; it registers when it enters scope |
| `Failed` | The last apply attempt failed; the error is redacted and shown in the row |

Actions:

- **Add source** — choose a kind (`OpenAPI`, `MCP`, `Python`), give the spec or
  server URL, or the module/class for a Python source, and optionally a
  namespace. The gateway validates the declaration and turns it into the same
  typed source object the config file produces, applies the deployment
  profile, and persists the addition through the source manager.
- **Retry** — shown only for a `Failed` source. It re-applies the unchanged
  declaration; a partial registration is rolled back, so a retry never
  duplicates tools. A source that is not failed is left untouched.
- **Remove** — unregisters the source's tools and persists the removal.
- **Replace** — the page does not have a replace control. Replacing a
  declaration wholesale is available through the extension route
  `PUT /admin/ext/gateway/sources/{id}` with the same body as **Add source**;
  it unregisters the old tools and registers the new set in one diff.

When the config file cannot be written, the change still applies in memory and
the page says it was not saved, with the reason. A read-only configuration is
not reported as success.

The panel's own **Sources** tab (the core panel section, outside the Gateway
pages) still offers the preview → select tools → register flow. Preview lists
what a source offers without registering anything; registration sends back
exactly the ticked names and persists through the same hook. Use it when you
want to see the tool list before adding a source; the Gateway Sources page is
the shorter path for sources you already know.

## Security notes

- The panel and its extension pages are operator-only. A client key never
  grants access to them, and the extension adds no authentication code of its
  own: it inherits the panel's session and the panel's cross-site guard.
- Extension assets are static JavaScript and CSS served through the panel's
  asset handler. No asset contains a secret, and the page keeps the one-time
  secret only in memory.
- Every request path is checked in the panel before a page handler runs; an
  unknown path under the extension returns the panel's JSON error shape.

## Related

- [Configuration guide](configuration.md#gateway-frontends) — gateway
  frontend paths, enable flags and the admin panel options.
- [CLI reference](../reference/cli/index.md) — `gateway` options,
  including `--admin*` and `--keys-db`.
- [Authentication guide](authentication.md) — static tokens and scoped API
  keys, including the `ScopeSpec` form the Keys page writes.
