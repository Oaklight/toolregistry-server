# Configuration

The CLI loads tool sources from JSONC or YAML configuration files. Three source types are supported: Python classes/modules, MCP servers, and OpenAPI endpoints.

Configuration parsing is powered by `toolregistry.config.load_config()` from the [ToolRegistry](https://toolregistry.readthedocs.io/en/latest/usage/configuration/) core library.

## Quick Start

```bash
# JSONC config
toolregistry-server openapi --config tools.jsonc

# YAML config
toolregistry-server mcp --config tools.yaml
```

## Config File Format

Both JSONC (`.json`, `.jsonc`) and YAML (`.yaml`, `.yml`) formats are supported. The format is auto-detected from the file extension.

### YAML Example

```yaml
mode: denylist
disabled:
  - filesystem

tools:
  # Python class
  - type: python
    class: toolregistry_hub.calculator.Calculator
    namespace: calculator

  # Python module (all public functions)
  - type: python
    module: my_package.tools
    namespace: custom

  # MCP server (stdio)
  - type: mcp
    transport: stdio
    command: ["python", "-m", "mcp_server"]
    namespace: mcp_tools

  # MCP server (SSE)
  - type: mcp
    transport: sse
    url: http://localhost:8080/sse
    namespace: remote_mcp

  # MCP server (streamable-http, alias "http")
  - type: mcp
    transport: http
    url: http://localhost:8080/mcp
    namespace: remote_http

  # OpenAPI endpoint
  - type: openapi
    url: https://api.example.com/openapi.json
    namespace: external_api
    auth:
      type: bearer
      token_env: EXTERNAL_API_TOKEN
```

### JSONC Example

```jsonc
{
  "mode": "denylist",
  "disabled": ["filesystem"],
  "tools": [
    {
      "type": "python",
      "class": "toolregistry_hub.calculator.Calculator",
      "namespace": "calculator"
    },
    {
      // MCP server via SSE
      "type": "mcp",
      "transport": "sse",
      "url": "http://localhost:8080/sse",
      "namespace": "remote_mcp"
    }
  ]
}
```

## Tool Source Types

### `python` — Python Class or Module

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"python"` | Yes | Source type identifier |
| `class` | string | One of `class`/`module` | Fully-qualified class path (e.g. `"pkg.Calculator"`) |
| `module` | string | One of `class`/`module` | Fully-qualified module path (e.g. `"pkg.tools"`) |
| `namespace` | string | No | Namespace for registered tools |
| `enabled` | bool | No | Per-source enable/disable (default: `true`) |

When `class` is specified, the server instantiates the class and calls `register_from_class()`. When `module` is specified, the server registers all public callables from the module.

!!! note "Legacy Format"
    The legacy format `{"module": "pkg", "class": "Cls"}` (without `type` field) is still supported. The type is inferred as `"python"` and `class_path` is combined as `"pkg.Cls"`.

### `mcp` — MCP Server

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"mcp"` | Yes | Source type identifier |
| `transport` | string | Yes | `"stdio"`, `"sse"`, `"streamable-http"`, or `"http"` |
| `command` | string or list | stdio only | Command to start the server |
| `url` | string | sse/http only | Server URL |
| `env` | dict | No | Environment variables for stdio subprocess |
| `headers` | dict | No | HTTP headers for network transports |
| `namespace` | string | No | Namespace for registered tools |
| `persistent` | bool | No | Keep connection alive (default: `true`) |
| `include` | list | No | Name or glob patterns to keep. Mutually exclusive with `exclude` |
| `exclude` | list | No | Name or glob patterns to drop. Mutually exclusive with `include` |
| `allow_private_hosts` | bool | No | Allow this source's URL transport to reach a loopback, link-local or private address (default: `false`) |
| `enabled` | bool | No | Per-source enable/disable (default: `true`) |

!!! tip "`http` Alias"
    `transport: "http"` is a shorthand for `"streamable-http"` and is normalized internally.

!!! warning "Error Handling"
    If an MCP server is unreachable at startup, the server logs a warning and continues loading other sources. The tools from that source will not be available.

### `openapi` — OpenAPI Endpoint

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"openapi"` | Yes | Source type identifier |
| `url` | string | Yes | URL to the OpenAPI spec. May be a local path; local paths are read only from operator configuration, and the admin panel input still rejects them |
| `namespace` | string | No | Namespace for registered tools |
| `auth` | object | No | Authentication configuration |
| `base_url` | string | No | Override `servers[0].url` from spec |
| `include` | list | No | Name or glob patterns to keep. Mutually exclusive with `exclude` |
| `exclude` | list | No | Name or glob patterns to drop. Mutually exclusive with `include` |
| `allow_private_hosts` | bool | No | Allow the spec URL and the base URL the tools use to reach a loopback, link-local or private address (default: `false`) |
| `enabled` | bool | No | Per-source enable/disable (default: `true`) |

#### Auth Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | `"bearer"` | `"bearer"` or `"header"` |
| `token` | string | — | Literal token value |
| `token_env` | string | — | Environment variable name (resolved at parse time) |
| `header_name` | string | `"Authorization"` | Custom header name (for `type: "header"`) |

If both `token_env` and `token` are specified, `token_env` takes precedence.

### Private Addresses

Both `mcp` and `openapi` sources refuse a URL whose host resolves to a loopback, link-local or private address. The check runs before any connection is opened and fails closed when the name does not resolve. Set `allow_private_hosts: true` on the individual source to allow it:

```yaml
tools:
  - type: openapi
    url: http://127.0.0.1:8000/openapi.json
    namespace: local_api
    allow_private_hosts: true

  - type: mcp
    transport: sse
    url: http://192.168.1.20:8080/sse
    namespace: lan_mcp
    allow_private_hosts: true
```

The flag applies to that source only. For `mcp` it covers the URL transports (SSE, streamable HTTP, WebSocket); a `stdio` source has no URL and is unaffected. For `openapi` it covers the spec URL and the base URL the tools use (`servers[0]` or the configured `base_url`). The admin panel has its own switch: `enable_admin(allow_private_hosts=True)`.

## Filtering Modes

### Denylist (Default)

All sources are loaded **except** those whose namespace matches a pattern in `disabled`:

```yaml
mode: denylist
disabled:
  - filesystem
  - web/dangerous
```

### Allowlist

**Only** sources whose namespace matches a pattern in `enabled` are loaded:

```yaml
mode: allowlist
enabled:
  - calculator
  - api
```

Namespace matching is hierarchical: pattern `"web"` matches `"web/brave_search"`.

### Per-Source Enable/Disable

Individual sources can be temporarily disabled with `enabled: false`, regardless of the filtering mode. This is useful for debugging or temporarily taking a source offline without removing it from the config:

```yaml
tools:
  - type: python
    class: toolregistry_hub.calculator.Calculator
    namespace: calculator

  - type: mcp
    transport: sse
    url: http://localhost:8080/sse
    namespace: remote_mcp
    enabled: false  # temporarily disabled

  - type: openapi
    url: https://api.example.com/openapi.json
    namespace: external_api
    enabled: false  # skip until API key is configured
```

## Deployment Profiles

`--profile` applies a named filter after all sources are registered. A config file can override the built-in defaults under `profiles:`:

```yaml
profiles:
  staging:
    disable_tags: [destructive, privileged]
    enable: [calculator]      # namespace patterns force-enabled after tag filtering
    disable: [internal/]      # namespace patterns force-disabled after tag filtering
```

| Key | Description |
|-----|-------------|
| `disable_tags` | Tag names whose tools are disabled. Replaces the built-in set for this profile |
| `enable` | Namespace patterns force-enabled after tag filtering (highest priority) |
| `disable` | Namespace patterns force-disabled after tag filtering (applied after `enable`) |

Without a `profiles:` entry the built-in sets apply: `remote` disables `file_system`, `destructive` and `privileged`; `local` disables `network`. An unknown profile name is logged as a warning and ignored.

## Runtime Source Changes

The server can change its source list without a restart. `App.prepare_registry`
builds a `SourceManager` over the loaded config and keeps it on
`app.source_manager`; an admin extension or an embedding application calls it
directly.

```python
from toolregistry.config import PythonSource

manager = app.source_manager
manager.list()                      # SourceEntry per declaration
manager.to_dicts()                  # JSON-ready status list

added = manager.add(PythonSource(class_path="my_tools.Extra", namespace="extra"))
source_id = added.entry.to_dict()["id"]
removed = manager.remove(source_id)
manager.replace(source_id, PythonSource(class_path="my_tools.Other"))
manager.retry(failed_entry.to_dict()["id"])   # re-run a failed registration
```

Each entry reports one of three statuses:

| Status | Meaning |
|--------|---------|
| `applied` | Registration succeeded. A source that produced no tools is still `applied` and is not registered again on an unrelated change. |
| `excluded` | The declaration is in the config but out of scope (`enabled: false`, or filtered by `mode` / `disabled` / `enabled`). Its tools are disabled, not unregistered, and are re-enabled when the source enters scope. |
| `failed` | Registration raised. `error` carries the redacted message; the source is retried on the next diff. A partial registration is rolled back. |

A source is identified by its declaration, so reordering `tools` is not a
change. `to_dict()` exposes only an opaque `src_<16 hex>` id derived from the
full declaration — two sources with the same target and different credentials
get different ids — plus the redacted detail, namespace, tool names,
enabled/total counts and `last_error`. The raw declaration and the internal
identity stay inside the manager; `get`, `remove`, `replace` and `retry` take
that id. `MutationResult.removed` is `True` for a removal, whose entry status
still describes the last apply attempt.

Tools registered by a change go through the deployment profile that was
applied at startup. A change is applied under one manager lock and wrapped in
`RouteTable.batch_changes()` when a route table is attached, so N tool
registrations produce one route rebuild instead of N; requests never take the
lock.

### Persistence

An add, remove or replace writes the configuration back to the file the server
was started with, so a restart reproduces the state. The previous file is
copied to `<path>.bak` first, and comments are lost because the parsed model
does not keep them — this holds for JSONC and YAML alike.
`MutationResult.persisted` and `persist_error` report the outcome: a read-only
path is reported, never silently ignored, and the runtime change still applies.
`--no-config-write` (or `write_config=False`) keeps every change in memory only.

### Watching the file

`--config-reload` starts a watcher that polls the config file and applies the
same source-level diff when it changes. The manager's own writes are
recognised by content hash, so a runtime mutation does not trigger a reload;
the watcher itself never writes. A malformed edit is logged and ignored, and
the running registry is untouched. The watcher is per process and has no
cross-process coordination.

## Gateway Frontends

The `gateway` subcommand serves the OpenAPI adapter, both MCP HTTP transports
and the operator admin panel from one process and one listener:

```bash
toolregistry-server gateway --config tools.yaml --port 8000
```

The default mounts are `/openapi` (OpenAPI), `/mcp` (MCP streamable-HTTP),
`/sse` (MCP SSE) and `/admin` (admin panel). A config file can change the
paths and enable flags under `gateway.frontends`:

```yaml
gateway:
  frontends:
    openapi:
      path: /api
      enabled: true
    mcp:
      path: /rpc
      enabled: true
    sse:
      path: /events
      enabled: false
    admin:
      path: /admin
      enabled: false
```

| Frontend | Default path | Transport | Default |
|----------|--------------|-----------|---------|
| `openapi` | `/openapi` | HTTP | enabled |
| `mcp` | `/mcp` | `streamable-http` | enabled |
| `sse` | `/sse` | SSE | enabled |
| `admin` | `/admin` | HTTP | disabled |

CLI options `--openapi-path`, `--mcp-path`, `--sse-path`, `--admin-path`,
`--openapi`, `--mcp`, `--sse` and `--admin` override the file for one
process. The enable flag is read on every request: disabling a frontend makes
its path answer `404` immediately, without a restart and without affecting the
other frontends. Changing a path also applies to the next request.

One `StreamableHTTPSessionManager` is created when the ASGI lifespan starts and
closed when it stops. Enabling or disabling the MCP frontend does not rebuild
it. The gateway always serves all enabled frontends from one ASGI application,
so multiple frontends require the default `fastapi` backend;
`--backend httpserver` is rejected when more than one frontend is enabled and
can serve only the openapi frontend. That backend serves OpenAPI at the
listener root, so an openapi path override is rejected rather than silently
ignored.

`--tokens` (and `API_BEARER_TOKEN`) protect the OpenAPI and MCP frontends
with the same token set. The admin panel is deliberately outside that set:
see [Admin panel](#admin-panel). When tokens or `--keys-db` are configured,
the MCP protected-resource metadata is served at
`/.well-known/oauth-protected-resource<mcp path>` and follows the current MCP
path.

### Admin panel

`--admin` enables the panel and `--admin-path` changes its mount. The panel is
the core ToolRegistry admin UI, so it uses the core panel's own token and
session authentication, not the gateway bearer tokens. Configure its operator
token with `--admin-token TOKEN` or, preferably, `--admin-token-file PATH`:

```bash
toolregistry-server gateway \
  --config tools.yaml \
  --tokens serving-tokens.txt \
  --admin \
  --admin-token-file operator-token.txt
```

The panel is disabled by default. Enabling it without an operator token is a
startup error, and a gateway serving token does not open the panel: a request
to `/admin/api/info` that carries only a serving token gets `401`.

The gateway listens on one external port. Internally it starts the core
`AdminServer` on a loopback ephemeral port and forwards the admin frontend to
it, preserving the public `Host` header so the panel's same-origin checks
keep working. The internal listener never accepts a non-loopback connection.
The panel starts on the first request after the frontend is enabled; while the
frontend is disabled, its path answers `404` and no admin request reaches the
panel.

The panel loads the gateway admin extension with the process's live objects:

- **Keys** uses the store from `--keys-db PATH`. Without it, the page answers
  `503`. The store is the scoped inbound key store; creating and revoking a
  key takes effect on the next request to the tool frontends as well.
- **Frontends** lists and changes the same frontend registry as
  `gateway.list_frontends()` / `gateway.update_frontend()`; a change applies
  to the next request and is persisted under the normal write policy.
- **Sources** uses the runtime source manager. The panel's own source
  registration also persists through that manager.

The extension routes live under the panel mount, for example
`/admin/ext/gateway/keys`. The shell loads the extension assets through the
panel's asset handler; no asset contains a secret. Disabling the admin
frontend makes the panel root and every extension route under it answer
`404`.

Hosting the panel requires core `toolregistry` with the admin extension
contract (`ADMIN_EXTENSION_API == 1`, core 0.21.0 or newer). On an older core
the frontend refuses to start with a clear error; the default configuration
does not load it.

`--keys-db` is the gateway's scoped-key option; its enforcement rules are
documented under [Authentication](authentication.md#scoped-api-keys). The
panel shares that store, so a key created or revoked there applies on the next
tool request.

The three pages and their actions are described in the
[Gateway Admin Panel guide](gateway-admin.md).

### Runtime frontend API

The gateway object is available in-process as `App.gateway` after
`App.create_gateway()` or `App.serve_gateway()`:

```python
gateway = app.create_gateway(config_path="tools.yaml")

gateway.list_frontends()
# [{"name": "openapi", "path": "/openapi", "enabled": True, "transport": "http"},
#  ..., {"name": "admin", "path": "/admin", "enabled": False, "transport": "http"}]

result = gateway.update_frontend("mcp", enabled=False)
# {"name": "mcp", "path": "/mcp", "enabled": False, ...,
#  "persisted": True,
#  "warning": "the config file was rewritten; comments are not preserved"}

gateway.update_frontend("sse", path="/events")
```

An update writes the new path and flag back to the config file after copying
the previous file to `<path>.bak`, so a restart keeps the change.

`result["persisted"]` says whether the write succeeded. `result["warning"]`
explains a skipped or failed write; a successful write carries the same
comment-loss warning as runtime source management, because comments are not
preserved. The runtime change still applies when the write fails.
`--no-config-write` (or `write_config=False`) disables the write and reports it
in `warning`.

On `--backend httpserver` the OpenAPI frontend has no mount prefix: it is
served at the listener root (`/tools`, `/openapi.json`, `/docs`, `/events`).
`list_frontends()` therefore reports its `path` as `"/"` together with a
`note` that says so, and passing `"/"` back to `update_frontend` is accepted
as a no-op instead of an error, so saving the unchanged record succeeds.

## Environment Variables

The CLI supports loading environment variables from `.env` files:

```bash
# .env
BRAVE_API_KEY=your-key-here
EXTERNAL_API_TOKEN=your-token-here
```

```bash
# Load from default .env file
toolregistry-server openapi --config config.yaml

# Load from custom .env file
toolregistry-server openapi --config config.yaml --env /path/to/.env

# Skip .env loading
toolregistry-server openapi --config config.yaml --no-env
```

## Error Handling

- **File not found** — the server exits with an error message
- **Invalid config** (bad mode, missing fields, unknown type) — the server exits with a descriptive error
- **Unreachable MCP/OpenAPI source** — the server logs a warning and continues loading other sources
- **Invalid Python module/class** — the server logs a warning and continues
