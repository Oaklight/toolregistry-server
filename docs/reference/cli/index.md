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

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | - | Path to a JSONC or YAML configuration file (required) |
| `--host HOST` | `0.0.0.0` | Bind host |
| `--port PORT` | `8000` | Bind port |
| `--tokens PATH` | - | File of Bearer tokens, one per line (`#` starts a comment). Tokens are compared in constant time; `API_BEARER_TOKEN` (comma-separated) is read as well |
| `--backend {fastapi,httpserver}` | `fastapi` | HTTP backend: `fastapi` (uvicorn) or the vendored, zero-dependency `httpserver` |
| `--max-body-size BYTES` | `1048576` (1 MiB) | Largest accepted request body. Must be positive; a larger body returns `413` with a JSON error |
| `--reload` | `false` | Enable auto-reload for development (FastAPI/uvicorn only; ignored with a warning on `httpserver`) |
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
| `--server-url URL` | derived from `--host`/`--port` | Public URL advertised in OAuth protected-resource metadata (required behind a reverse proxy) |
| `--tools-list-ttl-ms MS` | `300000` | `ttlMs` cache hint for `tools/list`; `0` disables caching |
| `--tools-list-cache-scope {public,private}` | `private` | `cacheScope` hint for `tools/list` |
| `--profile PROFILE` | - | Deployment profile: `remote`, `local`, or a profile declared under `profiles:` in the config file |

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
| `--tokens PATH` | - | File of Bearer tokens applied to every frontend. `API_BEARER_TOKEN` is read as well |
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
