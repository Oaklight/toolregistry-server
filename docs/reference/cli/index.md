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
| `--transport TYPE` | `stdio` | Transport type: `stdio`, `sse`, `streamable-http`, or `http` |
| `--host HOST` | `0.0.0.0` | Bind host (for HTTP transports) |
| `--port PORT` | `8000` | Bind port (for HTTP transports) |
| `--tokens PATH` | - | File with bearer tokens (for streamable-http/http transports) |
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

# With bearer token authentication (streamable-http)
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --tokens tokens.txt
```

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
        self.registry = registry

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
| `local` | No tag filter — all tools remain enabled |
| *(none)* | No filtering (default) |

Use `remote` when serving tools to end users who should not have access to the server's own filesystem or privileged operations:

```bash
toolregistry-server openapi --config config.json --profile remote
toolregistry-server mcp --config config.json --profile remote
```
