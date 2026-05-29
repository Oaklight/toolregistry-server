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

## Subcommands

### `openapi` - Start an OpenAPI Server

```bash
toolregistry-server openapi [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | - | Path to JSONC or YAML configuration file |
| `--host HOST` | `0.0.0.0` | Bind host |
| `--port PORT` | `8000` | Bind port |
| `--tokens PATH` | - | File with bearer tokens (one per line) |
| `--reload` | `false` | Enable auto-reload for development |
| `--profile PROFILE` | - | Deployment profile: `remote` or `local` |

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
| `--config PATH` | - | Path to JSON, JSONC, or YAML configuration file |
| `--transport TYPE` | `stdio` | Transport type: `stdio`, `sse`, or `streamable-http` |
| `--host HOST` | `0.0.0.0` | Bind host (for HTTP transports) |
| `--port PORT` | `8000` | Bind port (for HTTP transports) |
| `--profile PROFILE` | - | Deployment profile: `remote` or `local` |

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
```

## Configuration File

See the [Configuration Guide](../usage/configuration.md) for details on the JSON, JSONC, or YAML configuration format.

## Programmatic API

Both server functions can be called directly from Python and accept an optional pre-built `ToolRegistry`, letting you skip the config file entirely:

```python
from toolregistry import ToolRegistry
from toolregistry_server.cli.openapi import run_openapi_server
from toolregistry_server.cli.mcp import run_mcp_server

# Build your registry with custom logic
registry = ToolRegistry()
registry.register(my_tool)

# Pass it directly — config_path is ignored
run_openapi_server(host="0.0.0.0", port=8000, registry=registry)
run_mcp_server(transport="stdio", registry=registry)
```

When `registry` is `None` (the default), the functions fall back to loading tools from `config_path` as usual.

## Deployment Profiles

The `--profile` flag applies tag-based tool filtering after the registry is built:

| Profile | Effect |
|---------|--------|
| `remote` | Disables tools tagged `file_system`, `destructive`, or `privileged` |
| `local` | No tag filter — all tools remain enabled |
| *(none)* | No filtering (default) |

Use `remote` when serving tools to end users who should not have access to the server's own filesystem or privileged operations:

```bash
toolregistry-server openapi --config config.json --profile remote
toolregistry-server mcp --config config.json --profile remote
```

`apply_profile()` is also available as a public Python API:

```python
from toolregistry_server.cli.openapi import apply_profile, PROFILE_DISABLE_TAGS

# Use a built-in profile
apply_profile(registry, "remote")

# Inspect or extend the mapping
print(PROFILE_DISABLE_TAGS)
```
