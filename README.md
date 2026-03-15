# toolregistry-server

[![PyPI version](https://badge.fury.io/py/toolregistry-server.svg)](https://badge.fury.io/py/toolregistry-server)
[![Python Version](https://img.shields.io/pypi/pyversions/toolregistry-server.svg)](https://pypi.org/project/toolregistry-server/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Define custom tools and serve them via OpenAPI or MCP interfaces. Built on [ToolRegistry](https://github.com/Oaklight/ToolRegistry).

## Overview

`toolregistry-server` lets you register Python functions as tools and expose them as services through multiple protocols. It provides:

- **Central Route Table**: A unified routing layer that bridges `ToolRegistry` and protocol adapters
- **OpenAPI Adapter**: Expose tools as RESTful HTTP endpoints with automatic OpenAPI schema generation
- **MCP Adapter**: Expose tools via the [Model Context Protocol](https://modelcontextprotocol.io/) for LLM integration
- **Authentication**: Built-in Bearer token authentication support
- **CLI**: Command-line interface for running servers

## Ecosystem

The ToolRegistry ecosystem consists of three packages:

| Package | Description | Repository |
|---------|-------------|------------|
| [`toolregistry`](https://pypi.org/project/toolregistry/) | Core library - Tool model, ToolRegistry, client integration | [Oaklight/ToolRegistry](https://github.com/Oaklight/ToolRegistry) |
| [`toolregistry-server`](https://pypi.org/project/toolregistry-server/) | Tool server - define tools and serve via OpenAPI/MCP | [Oaklight/toolregistry-server](https://github.com/Oaklight/toolregistry-server) |
| [`toolregistry-hub`](https://pypi.org/project/toolregistry-hub/) | Tool collection - Built-in tools, default server configuration | [Oaklight/toolregistry-hub](https://github.com/Oaklight/toolregistry-hub) |

```
toolregistry (core)
       ↓
toolregistry-server (tool server)
       ↓
toolregistry-hub (tool collection + server config)
```

## Installation

### Basic Installation

```bash
pip install toolregistry-server
```

### With OpenAPI Support

```bash
pip install toolregistry-server[openapi]
```

### With MCP Support

```bash
pip install toolregistry-server[mcp]
```

### Full Installation

```bash
pip install toolregistry-server[all]
```

## Quick Start

### Using RouteTable

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable

# Create a registry and register tools
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

# Create a route table
route_table = RouteTable(registry)

# List all routes
for route in route_table.list_routes():
    print(f"{route.path} -> {route.tool_name}")
```

### Creating an OpenAPI Server

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app

# Setup registry and route table
registry = ToolRegistry()
# ... register tools ...
route_table = RouteTable(registry)

# Create FastAPI app
app = create_openapi_app(route_table)

# Run with uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### Creating an MCP Server

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.mcp import create_mcp_server

# Setup registry and route table
registry = ToolRegistry()
# ... register tools ...
route_table = RouteTable(registry)

# Create MCP server
server = create_mcp_server(route_table)

# Run the server
if __name__ == "__main__":
    import asyncio
    asyncio.run(server.run())
```

### Using the CLI

```bash
# Start OpenAPI server
toolregistry-server --mode openapi --port 8000

# Start MCP server (stdio)
toolregistry-server --mode mcp

# With authentication
toolregistry-server --mode openapi --auth-token "your-secret-token"
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      ToolRegistry                           │
│                   (tool definitions)                        │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                       RouteTable                            │
│              (central routing layer)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ RouteEntry  │  │ RouteEntry  │  │ RouteEntry  │  ...    │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ OpenAPI Adapter │ │   MCP Adapter   │ │  gRPC Adapter   │
│   (FastAPI)     │ │   (MCP SDK)     │ │    (future)     │
└─────────────────┘ └─────────────────┘ └─────────────────┘
          │               │               │
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  HTTP Clients   │ │   MCP Clients   │ │  gRPC Clients   │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Documentation

- [Full Documentation](https://toolregistry-server.readthedocs.io)
- [API Reference](https://toolregistry-server.readthedocs.io/api/)
- [Examples](https://toolregistry-server.readthedocs.io/examples/)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Related Projects

- [ToolRegistry](https://github.com/Oaklight/ToolRegistry) - Core library
- [toolregistry-hub](https://github.com/Oaklight/toolregistry-hub) - Built-in tool collection
- [Model Context Protocol](https://modelcontextprotocol.io/) - MCP specification
