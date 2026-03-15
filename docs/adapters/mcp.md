# MCP Adapter

The MCP adapter exposes `ToolRegistry` tools via the [Model Context Protocol](https://modelcontextprotocol.io/) for LLM integration.

## Overview

The adapter:

- Registers `list_tools` and `call_tool` MCP handlers
- Supports multiple transport mechanisms (stdio, SSE, Streamable HTTP)
- Reads from `RouteTable` at request time for real-time sync
- Handles async and sync tools transparently
- Serializes results to JSON-compatible strings

## Quick Start

### Streamable HTTP Transport (Recommended)

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.mcp import create_mcp_server, run_streamable_http
import asyncio

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

route_table = RouteTable(registry)
server = create_mcp_server(route_table)

asyncio.run(run_streamable_http(server, host="0.0.0.0", port=8000))
```

### stdio Transport

For subprocess-based communication (used by Claude Desktop, etc.):

```python
from toolregistry_server.mcp import create_mcp_server, run_stdio
import asyncio

server = create_mcp_server(route_table)
asyncio.run(run_stdio(server))
```

### SSE Transport

For Server-Sent Events over HTTP:

```python
from toolregistry_server.mcp import create_mcp_server, run_sse
import asyncio

server = create_mcp_server(route_table)
asyncio.run(run_sse(server, host="0.0.0.0", port=8000))
```

## Transport Comparison

| Transport | Use Case | Protocol |
|-----------|----------|----------|
| **Streamable HTTP** | Production web deployments | HTTP |
| **SSE** | Web-based clients needing real-time updates | HTTP + SSE |
| **stdio** | Subprocess model (Claude Desktop, IDE plugins) | stdin/stdout |

## MCP Client Configuration

### Claude Desktop

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "my-tools": {
      "command": "toolregistry-server",
      "args": ["mcp", "--config", "config.json"]
    }
  }
}
```

### HTTP-based Clients

Connect to the server URL:

```
http://localhost:8000/mcp
```

## Error Handling

Tool errors are returned as structured MCP error responses with appropriate error codes.

## API Reference

See the [MCP API Reference](../api/mcp.md) for detailed documentation.
