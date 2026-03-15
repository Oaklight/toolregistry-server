---
title: CLI Reference
---

# Command-Line Interface

`toolregistry-server` provides a CLI for running servers without writing custom code.

## Usage

```bash
toolregistry-server [options] <subcommand> [subcommand-options]
```

## Global Options

| Option | Description |
|--------|-------------|
| `--env-file PATH` | Path to .env file (default: `.env`) |
| `--no-env` | Skip loading .env file |

## Subcommands

### `openapi` - Start an OpenAPI Server

```bash
toolregistry-server openapi [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | Required | Path to JSON/JSONC configuration file |
| `--host HOST` | `0.0.0.0` | Bind host |
| `--port PORT` | `8000` | Bind port |
| `--auth-token TOKEN` | - | Bearer token for authentication |
| `--auth-tokens-file PATH` | - | File with tokens (one per line) |
| `--reload` | `false` | Enable auto-reload for development |

**Example:**

```bash
toolregistry-server openapi \
  --config config.json \
  --port 8000 \
  --auth-token "my-secret"
```

### `mcp` - Start an MCP Server

```bash
toolregistry-server mcp [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | Required | Path to JSON/JSONC configuration file |
| `--transport TYPE` | `stdio` | Transport type: `stdio`, `sse`, or `streamable-http` |
| `--host HOST` | `0.0.0.0` | Bind host (for HTTP transports) |
| `--port PORT` | `8000` | Bind port (for HTTP transports) |

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

See the [Configuration Guide](../usage/configuration.md) for details on the JSON/JSONC configuration format.
