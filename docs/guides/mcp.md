# MCP Adapter

The MCP adapter exposes `ToolRegistry` tools via the [Model Context Protocol](https://modelcontextprotocol.io/) for LLM integration.

## Overview

The adapter:

- Registers `list_tools` and `call_tool` MCP handlers that read from `RouteTable` at request time
- Supports stdio, SSE, and Streamable HTTP transports
- Handles async and sync tools transparently
- Provides both blocking (`run`) and async (`run_async`) entry points

## Quick Start

### Via `App` (recommended)

```python
from toolregistry_server.app import App

# From a config file
App().serve_mcp(config_path="tools.yaml", transport="stdio")
App().serve_mcp(config_path="tools.yaml", transport="sse", host="0.0.0.0", port=8000)
App().serve_mcp(config_path="tools.yaml", transport="http", host="0.0.0.0", port=8000)
```

### Via `MCPAdapter` directly

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp import MCPAdapter

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

route_table = RouteTable(registry)
adapter = MCPAdapter(route_table)

# Blocking (suitable for scripts / CLI)
adapter.run(transport="stdio")
adapter.run(transport="sse", host="0.0.0.0", port=8000)
adapter.run(transport="http", host="0.0.0.0", port=8000)
```

### Async entry point

Use `run_async` when you're already inside an event loop:

```python
import asyncio
from toolregistry_server.adapters.mcp import MCPAdapter

adapter = MCPAdapter(route_table)
asyncio.run(adapter.run_async(transport="sse", host="0.0.0.0", port=8000))

# or within an existing loop:
await adapter.run_async(transport="stdio")
```

### Accessing the underlying MCP server

```python
from toolregistry_server.adapters.mcp import MCPAdapter, run_stdio

adapter = MCPAdapter(route_table)
server = adapter.server   # mcp.server.lowlevel.Server instance
asyncio.run(run_stdio(server))
```

## Transport Comparison

| Transport | Alias | Use Case |
|-----------|-------|----------|
| `stdio` | — | Subprocess model (Claude Desktop, IDE plugins) |
| `sse` | — | SSE-based HTTP clients |
| `streamable-http` | `http` | Production HTTP deployments |

`http` is accepted as an alias for `streamable-http` and is normalized internally.

## Authentication (Streamable HTTP)

Pass a Bearer tokens file via `tokens_path`, or set the `API_BEARER_TOKEN` environment variable (comma-separated):

```python
adapter.run(
    transport="http",
    host="0.0.0.0",
    port=8000,
    tokens_path="/etc/myapp/tokens.txt",
)
```

```bash
# CLI equivalent
toolregistry-server mcp --config tools.yaml --transport http --tokens tokens.txt
```

## MCP Client Configuration

### Claude Desktop (stdio)

```json
{
  "mcpServers": {
    "my-tools": {
      "command": "toolregistry-server",
      "args": ["mcp", "--config", "/path/to/tools.yaml"]
    }
  }
}
```

### HTTP-based clients

Connect to:

```
http://localhost:8000/mcp       # streamable-http
http://localhost:8000/sse       # SSE
```

## API Reference

See the [MCP API Reference](../reference/api/mcp.md) for detailed documentation.
