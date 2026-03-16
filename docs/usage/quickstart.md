# Quick Start

This guide walks you through the basic usage of `toolregistry-server` to expose your tools as services.

## Using RouteTable

The `RouteTable` is the central routing layer that bridges `ToolRegistry` with protocol adapters.

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable

# Create a registry and register tools
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

@registry.register
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b

# Create a route table
route_table = RouteTable(registry)

# List all routes
for route in route_table.list_routes():
    print(f"{route.path} -> {route.tool_name}")
```

## Creating an OpenAPI Server

Expose your tools as RESTful HTTP endpoints using FastAPI:

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app

# Setup registry and route table
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

route_table = RouteTable(registry)

# Create FastAPI app
app = create_openapi_app(route_table)

# Run with uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Your tools are now accessible as POST endpoints at `http://localhost:8000/`.

## Creating an MCP Server

Expose your tools via the Model Context Protocol for LLM integration:

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.mcp import create_mcp_server, run_streamable_http

# Setup registry and route table
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

route_table = RouteTable(registry)

# Create and run MCP server
server = create_mcp_server(route_table)

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_streamable_http(server, host="0.0.0.0", port=8000))
```

## Using the CLI

The quickest way to start a server without writing code:

```bash
# Start OpenAPI server
toolregistry-server openapi --config config.json --port 8000

# Start MCP server (stdio transport)
toolregistry-server mcp --config config.json

# Start MCP server (streamable HTTP transport)
toolregistry-server mcp --config config.json --transport streamable-http --port 8000
```

See the [CLI Reference](../cli/) and [Configuration Guide](configuration.md) for details on the config file format.

## Next Steps

- [Runnable Examples](https://github.com/Oaklight/toolregistry-server/tree/master/examples) - Complete scripts you can run directly
- [Configuration](configuration.md) - Learn about JSON/JSONC configuration files
- [Authentication](authentication.md) - Set up Bearer token authentication
- [OpenAPI Adapter](../adapters/openapi.md) - Deep dive into the REST API adapter
- [MCP Adapter](../adapters/mcp.md) - Deep dive into the MCP adapter
