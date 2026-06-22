# toolregistry-server

[![PyPI version](https://img.shields.io/pypi/v/toolregistry-server?color=green)](https://pypi.org/project/toolregistry-server/)
[![CI](https://github.com/Oaklight/toolregistry-server/actions/workflows/ci.yml/badge.svg)](https://github.com/Oaklight/toolregistry-server/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

Define custom tools and serve them via OpenAPI or MCP interfaces. Built on [ToolRegistry](https://github.com/Oaklight/ToolRegistry).

## Overview

`toolregistry-server` lets you register Python functions as tools and expose them as services through multiple protocols. It provides:

- **Registry Builder**: Protocol-agnostic config loading and source registration (`registry_builder`)
- **Protocol Adapters**: `OpenAPIAdapter` (FastAPI/REST) and `MCPAdapter` (Model Context Protocol)
- **App Orchestration**: `App` class for building registries and dispatching to any adapter; subclass `prepare_registry()` for custom registries
- **Authentication**: Unified Bearer token support (`auth.load_tokens`)
- **CLI**: `toolregistry-server openapi` / `toolregistry-server mcp` with `--config`, `--profile`, and more

## Ecosystem

| Package | Description | PyPI | Docs |
|---------|-------------|------|------|
| [**toolregistry**](https://github.com/Oaklight/ToolRegistry) | Core library — tool registration, schema generation, execution | [![PyPI](https://img.shields.io/pypi/v/toolregistry?color=green)](https://pypi.org/project/toolregistry/) | [Docs](https://toolregistry.readthedocs.io/) |
| [**toolregistry-server**](https://github.com/Oaklight/toolregistry-server) | Server adapters — expose tools via OpenAPI & MCP | [![PyPI](https://img.shields.io/pypi/v/toolregistry-server?color=green)](https://pypi.org/project/toolregistry-server/) | [Docs](https://toolregistry-server.readthedocs.io/) |
| [**toolregistry-hub**](https://github.com/Oaklight/toolregistry-hub) | Ready-to-use tools — calculator, web search, file ops, etc. | [![PyPI](https://img.shields.io/pypi/v/toolregistry-hub?color=green)](https://pypi.org/project/toolregistry-hub/) | [Docs](https://toolregistry-hub.readthedocs.io/) |

```
toolregistry (core)
       ↓
toolregistry-server (tool server)
       ↓
toolregistry-hub (tool collection + server config)
```

## Installation

```bash
# Base (RouteTable, registry_builder, auth)
pip install toolregistry-server

# With OpenAPI support
pip install toolregistry-server[openapi]

# With MCP support
pip install toolregistry-server[mcp]

# Full
pip install toolregistry-server[all]
```

## Quick Start

### Programmatic — OpenAPI server

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import OpenAPIAdapter

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

route_table = RouteTable(registry)
adapter = OpenAPIAdapter(route_table)
adapter.run(host="0.0.0.0", port=8000)
```

### Programmatic — MCP server

```python
import asyncio
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp import MCPAdapter

registry = ToolRegistry()
# ... register tools ...
route_table = RouteTable(registry)

adapter = MCPAdapter(route_table)
adapter.run(transport="stdio")                        # blocking
# or: asyncio.run(adapter.run_async(transport="sse", host="0.0.0.0", port=8000))
```

### High-level — `App` class

```python
from toolregistry_server.app import App

# From a config file
App().serve_openapi(config_path="tools.yaml", host="0.0.0.0", port=8000)
App().serve_mcp(config_path="tools.yaml", transport="stdio")

# From a pre-built registry
from toolregistry import ToolRegistry
registry = ToolRegistry()
# ... register tools ...
App().serve_openapi(registry=registry, port=9000)
```

### Custom App subclass

Override `prepare_registry` to add built-in tools, hooks, or metadata:

```python
from toolregistry_server.app import App

class MyApp(App):
    def prepare_registry(self, **kwargs):
        from toolregistry import ToolRegistry
        registry = ToolRegistry()
        registry.register(my_builtin_tool)
        # optionally apply user config on top
        if kwargs.get("config_path"):
            from toolregistry_server import apply_config, load_config
            apply_config(registry, load_config(kwargs["config_path"]))
        return registry

MyApp().serve_openapi(host="0.0.0.0", port=8000)
```

### CLI

```bash
# OpenAPI server from config file
toolregistry-server openapi --config tools.yaml --port 8000

# MCP server (stdio)
toolregistry-server mcp --config tools.yaml --transport stdio

# MCP server (SSE)
toolregistry-server mcp --config tools.yaml --transport sse --port 8000

# With deployment profile (disables network/filesystem tools)
toolregistry-server openapi --config tools.yaml --profile remote

# With Bearer token auth
toolregistry-server openapi --config tools.yaml --tokens /path/to/tokens.txt
```

## Config File

JSONC and YAML are both supported. Three source types: `python`, `mcp`, `openapi`.

```yaml
mode: denylist     # or "allowlist"
disabled: []       # namespaces to exclude (denylist mode)

tools:
  # Python module — all public functions
  - type: python
    module: my_package.tools
    namespace: my_tools

  # Python class
  - type: python
    class: my_package.Calculator
    namespace: calculator

  # MCP server (stdio subprocess)
  - type: mcp
    transport: stdio
    command: ["python", "-m", "my_mcp_server"]
    namespace: mcp_tools

  # MCP server (SSE / streamable-http)
  - type: mcp
    transport: http
    url: http://localhost:8080/mcp
    namespace: remote_mcp

  # OpenAPI endpoint
  - type: openapi
    url: https://api.example.com/openapi.json
    namespace: external_api
    auth:
      type: bearer
      token_env: EXTERNAL_API_TOKEN
```

See [`examples/config.yaml`](examples/config.yaml) and [`examples/config.jsonc`](examples/config.jsonc) for full examples.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    registry_builder                         │
│   load_config · apply_config · register_*_source            │
│   apply_profile · PROFILE_DISABLE_TAGS                      │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                       RouteTable                            │
│              (central routing layer)                        │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  OpenAPIAdapter │ │   MCPAdapter    │ │  (your adapter) │
│   (FastAPI)     │ │  stdio/sse/http │ │  Adapter ABC    │
└─────────────────┘ └─────────────────┘ └─────────────────┘
          │               │
          ▼               ▼
┌─────────────────┐ ┌─────────────────┐
│  HTTP Clients   │ │   MCP Clients   │
└─────────────────┘ └─────────────────┘
```

### Adding a New Adapter

1. Subclass `Adapter` from `toolregistry_server.adapters`
2. Implement `run(**kwargs)` and `create_and_run(cls, route_table, **kwargs)`
3. Optionally implement `add_cli_arguments(parser)` for CLI integration
4. Call `App().serve(MyAdapter, ...)` — no changes to `App` needed

## Deployment Profiles

`--profile` applies tag-based tool filtering at startup:

| Profile | Disables |
|---------|----------|
| `remote` | `FILE_SYSTEM`, `DESTRUCTIVE`, `PRIVILEGED` tagged tools |
| `local` | `NETWORK` tagged tools |

## Documentation

- [Full Documentation](https://toolregistry-server.readthedocs.io)
- [Configuration Guide](https://toolregistry-server.readthedocs.io/guides/configuration/)
- [API Reference](https://toolregistry-server.readthedocs.io/reference/)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## License

MIT — see [LICENSE](LICENSE).

## Related Projects

- [ToolRegistry](https://github.com/Oaklight/ToolRegistry) — Core library
- [toolregistry-hub](https://github.com/Oaklight/toolregistry-hub) — Built-in tool collection
- [Model Context Protocol](https://modelcontextprotocol.io/) — MCP specification
