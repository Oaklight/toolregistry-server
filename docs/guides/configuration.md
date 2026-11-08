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
| `allow_private_hosts` | bool | No | Allow the spec URL, every `servers[]` entry and the tools' request URLs to reach a loopback, link-local or private address (default: `false`) |
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

The flag applies to that source only. For `mcp` it covers the URL transports (SSE, streamable HTTP, WebSocket); a `stdio` source has no URL and is unaffected. For `openapi` it covers the spec URL, every `servers[]` entry, and the tools' request URLs. The admin panel has its own switch: `enable_admin(allow_private_hosts=True)`.

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
