# Gateway Admin Panel

The `gateway` subcommand can host the core ToolRegistry admin panel and load
the gateway admin extension, which adds six operator pages:

| Page | What it manages |
|------|-----------------|
| **Keys** | Inbound client keys for the OpenAPI and MCP frontends |
| **Clients** | Live per-client call counters |
| **Frontends** | The enable flag and mount path of every gateway frontend |
| **Sources** | Tool sources added, edited, tested or removed at runtime |
| **Tools** | One tool's schema and a guarded call tester |
| **Logs** | The execution log filtered by principal, outcome, tool and time |

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
`/admin/ext/gateway/keys`, `/admin/ext/gateway/clients`,
`/admin/ext/gateway/frontends`, `/admin/ext/gateway/sources`,
`/admin/ext/gateway/tools` and `/admin/ext/gateway/logs`. In the console
navigation the extension is a tab named **Gateway**, and it opens on **Keys**.

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
| Calls | Calls recorded for this key |
| OK | Calls whose handler returned |
| Errors | Calls whose handler raised |
| Busy | Calls refused by the client concurrency budget |
| Denied | Calls refused by the key scope or the permission policy |
| Last success | Time of the latest successful call, or `—` |
| Status | `Active`, `Expired` or `Revoked` |
| Actions | `Details`, `Edit`, `Rotate` and `Revoke`, as the key's status allows |

Revoked and expired keys stay in the table so their history remains visible.
The store keeps secrets hashed; no response and no asset contains a plaintext
secret.

### Usage counters

The usage columns are read from counters persisted in the key database, not
from the execution log. Each call or refusal that is charged to a key
increments its row, so the numbers survive a restart and stay visible after a
key is revoked or expires. The Keys page reads all counters in one query when
it lists the keys.

!!! note "Usage and the Logs page"
    The execution log is an in-memory ring of 1000 entries per process. The
    key counters are cumulative and unbounded, so past that window the two
    views diverge by design: the Keys page keeps counting while the Logs page
    has already dropped the oldest entries. Compare a key's counts with the
    Logs page only inside the log's current window. The gateway's own Logs tab
    reads the same ring and filters it by principal, outcome, tool and time.

A key with no recorded call shows zeros. A usage response never includes a
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
- **Concurrency** — optional; the most calls this key may run at once. It can
  only lower the server's `--client-concurrency`, never raise it. Leaving it
  empty uses the server default.

An empty namespace list with no overrides creates a key that can call
nothing; that is the fail-closed default.

On success the page shows the secret once, with a copy button:

!!! warning "The secret is shown once"
    The full secret is returned only by the creation response, which is sent
    with `Cache-Control: no-store`. It is not stored by the page and cannot be
    recovered later. Closing the dialog without copying it means creating a
    new key.

### Rotating a key

**Rotate** asks for confirmation, then issues a new secret for the key. The
key id, label, scopes, expiry, concurrency and usage history stay the same;
only the secret changes, and the old secret is refused on its next request.

The new secret is shown once, in the same one-time panel as a created key,
and the response carries `Cache-Control: no-store`. It cannot be recovered
later; rotating again is the only way to get another one.

**Rotate** is offered only for an `Active` key. A revoked key cannot be
rotated, and an expired key must have its expiry extended or cleared with
**Edit** first.

### Editing a key

**Edit** opens a form for the label, scopes, tool overrides, concurrency and
expiry, filled with the key's current values. **Save** sends only these fields
and applies the change to the next request, with no restart.

- **Label** is required and must not be empty.
- **Scopes** is a comma-separated namespace list; **Tool overrides** is one
  `tool=allow` or `tool=deny` per line.
- **Expires** empty means no expiry. A time in the past is refused.
- **Concurrency** empty uses the server default.

Saving with no change is refused with "Change at least one field."

**Edit** is not offered for a revoked key. A revoked key keeps its history but
cannot be changed.

### Key details

**Details** opens a read-only view of one key: label, id, scopes, tool
overrides, creation time, expiry, last used, concurrency, status and every
usage counter including the last success. It reads the key from the store
through `GET /ext/gateway/keys/<key_id>`, so it shows the current row rather
than the last list refresh.

### Revoking a key

**Revoke** asks for confirmation and marks the key revoked immediately. A
client that presents it is refused on its next request. Revocation is
idempotent, and the original revocation time is kept. A revoked key keeps its
row and its usage counters.

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
  namespace. An MCP source also takes a transport (`streamable-http`, `sse` or
  `stdio`) and, for the HTTP transports, an optional credential. The gateway
  validates the declaration and turns it into the same typed source object the
  config file produces, applies the deployment profile, and persists the
  addition through the source manager.
- **Edit** — opens the same form, prefilled from the stored declaration through
  `GET /ext/gateway/sources/<id>`, and saves with
  `PUT /ext/gateway/sources/<id>`. Saving replaces the declaration in one diff:
  the old tools are unregistered and the new set registered. Fields the form
  does not show — `tags`, `include`, `exclude`, and `persistent` for MCP or
  `base_url` for OpenAPI — are carried over from the stored declaration, so
  saving an unrelated field does not reset them.
- **Test** — checks a declaration without registering anything.
  `POST /ext/gateway/sources/test` tests what the form currently holds;
  `POST /ext/gateway/sources/<id>/test` tests the stored source. An MCP source
  opens a throwaway session, runs `initialize` and `tools/list`, and reports the
  tool count with the server name and version. An OpenAPI source fetches and
  parses the document and counts its operations. A failure is returned verbatim
  with its exception type, with credentials in a URL redacted.
- **Tools** — expands the row and lists the tools the source registered, with
  each tool's name, a short description and its enabled state
  (`GET /ext/gateway/sources/<id>/tools`). A name the registry no longer holds
  is not reported as enabled.
- **Retry** — shown only for a `Failed` source. It re-applies the unchanged
  declaration; a partial registration is rolled back, so a retry never
  duplicates tools. A source that is not failed is left untouched.
- **Remove** — unregisters the source's tools and persists the removal.

### Secrets are write-only

A declaration may carry credentials: MCP `headers`, a Python source's
constructor `kwargs`, and an OpenAPI `auth.token`. No GET response ever
contains their values. `GET /ext/gateway/sources/<id>` returns the declaration
with those fields removed and lists their names under `secret_fields`, for
example `["headers.Authorization"]`, so the form can say that a credential is
stored and ask for it again. Credentials embedded in `url` or `base_url` — the
`user:password@` part — are redacted the same way, and the field itself is
named in `secret_fields`. A URL the form submits back unchanged, or does not
send at all, keeps the stored value with its userinfo.

One field is deliberately not treated as a secret: an MCP stdio source's
`command` is returned verbatim, because it is the only way to edit the
declaration. A credential belongs in `env`, whose values are withheld.

Because the form never receives the value, a blank credential field on save
keeps the stored one. To delete a credential, tick **Remove the stored
credential**, which sends `clear_secrets: true`; that removes the stored
`headers`, `env`, `kwargs` and `auth.token` instead of merging them. A request
that carries both `auth` and `clear_secrets` is refused.

For an MCP source, the form's credential is stored as the header the config
file uses: a bearer token becomes `Authorization: Bearer <token>`, and a custom
header stores the token under the header name given. A header name already set
in `headers` is refused, so the credential has one home.

When the config file cannot be written, the change still applies in memory and
the page says it was not saved, with the reason. A read-only configuration is
not reported as success.

The panel's own **Sources** tab (the core panel section, outside the Gateway
pages) still offers the preview → select tools → register flow. Preview lists
what a source offers without registering anything; registration sends back
exactly the ticked names and persists through the same hook. Use it when you
want to see the tool list before adding a source; the Gateway Sources page is
the shorter path for sources you already know.

## Tools page

The Tools page lists every registered tool, disabled ones included, with its
namespace, source, enabled state and tags (`GET /ext/gateway/tools`).
**Inspect** opens the detail view for one tool (`GET /ext/gateway/tools/<name>`):
the full description, the input schema a client is served, the output schema,
tags, namespace, enabled state and the reason it is disabled, the source and
its redacted detail, and the execution hints. A tool tagged `destructive` or
`privileged` is called out before it can be run.

The detail view also hosts the call tester. It is not a second execution path:
the call runs through the same steps as a client call — the shared argument
validator against the served schema, the process permission policy, the route
handler, and the attributed execution-log record under the operator principal.

- The arguments box starts from a skeleton built from the input schema and is
  sent as JSON.
- Every call needs **I understand this call runs on the server** ticked, which
  sends `confirm: true`; without it the route answers `409`.
- A disabled tool is refused with `409` before it runs, and the detail view
  disables the form. Enable the tool first.
- Invalid arguments answer `400` with the validator's detail; a policy refusal
  answers `403`.
- A handler failure is returned as `ok: false` with the verbatim error and its
  exception type, and is recorded in the log with its outcome.
- A refusal — disabled, unconfirmed or policy-denied — is recorded in the log
  with outcome `denied` under the operator principal, so it shows up in the
  Logs page filter just like a client call the policy refuses. An
  invalid-argument refusal is returned to the page and not logged, because
  nothing was attempted.
- A successful call shows the encoded result and the duration in milliseconds.

The tester is an operator surface: every route answers `401` without the
panel's operator session, and no tool can be called from the page without one.

## Logs page

The Logs tab reads the registry execution log and filters it by principal,
outcome, tool and time range (`GET /ext/gateway/logs`). The same filters feed
`GET /ext/gateway/logs/stats`, so the counters always describe the rows the
table shows.

| Filter | Value |
|--------|-------|
| Key id | A stored key id. `operator` selects entries attributed to the operator principal, never anonymous ones |
| Key label | The recorded principal label, case-insensitive |
| Outcome | `ok`, `error`, `busy`, `denied`, `timeout` or `disabled` |
| Tool | Exact tool name |
| Since / Until | Inclusive ISO 8601 bounds on the entry time |
| Limit | Rows to return, 1 to 1000 (default 100) |

All filters combine, and rows come back newest first. The outcome filter reads
the outcome recorded with the entry (`metadata.outcome`) and falls back to the
entry status, so `busy` and `denied` rows match when the backend recorded them:
an MCP refusal and an OpenAPI scope, proxy-target or policy refusal are
recorded as `denied`, and a call that never got a concurrency slot is recorded
as `busy`.

Each row shows the time, tool, principal, key id, status and duration. The
principal column is the recorded client identity: an API key shows its key
label, an OAuth principal shows its OAuth client id, a call made from the Tools
tester shows `operator`, and a call with no attribution shows `anonymous`. The
stats block reports the entry count, the average duration and a breakdown by
status, tool and principal.

!!! note "The extension Logs tab and the core Logs page"
    The core panel's own Logs page is served by the installed `toolregistry`
    package at `/api/logs` and offers a tool, status and warnings filter only.
    The gateway cannot add query parameters to a handler it does not own, so
    the filtered view is served at `/ext/gateway/logs` and rendered in the
    extension's own tab. The tab supplements the core page rather than
    replacing it; both read the same in-memory log.

## Security notes

- The panel and its extension pages are operator-only. A client key never
  grants access to them, and the extension adds no authentication code of its
  own: it inherits the panel's session and the panel's cross-site guard.
- Extension assets are static JavaScript and CSS served through the panel's
  asset handler. No asset contains a secret, and the page keeps the one-time
  secret only in memory.
- Every request path is checked in the panel before a page handler runs; an
  unknown path under the extension returns the panel's JSON error shape.
- Source credentials are write-only. A GET response names the stored secret
  fields and never returns their values, and credentials in a URL userinfo are
  redacted. A blank field on save keeps the stored value; deleting one is an
  explicit action.
- The call tester uses the client execution path, so it cannot bypass the
  argument validator, the process permission policy or the execution log. It
  requires an operator session and an explicit confirmation per call.

## Related

- [Configuration guide](configuration.md#gateway-frontends) — gateway
  frontend paths, enable flags and the admin panel options.
- [CLI reference](../reference/cli/index.md) — `gateway` options,
  including `--admin*` and `--keys-db`.
- [Authentication guide](authentication.md) — static tokens and scoped API
  keys, including the `ScopeSpec` form the Keys page writes.
