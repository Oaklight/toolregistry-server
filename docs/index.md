---
title: Home
author: Oaklight
hide:
  - navigation
---

# ToolRegistry Server

[![PyPI version](https://img.shields.io/pypi/v/toolregistry-server?color=green)](https://pypi.org/project/toolregistry-server/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**Define custom tools and serve them via OpenAPI or MCP interfaces.** Built on [ToolRegistry](https://toolregistry.readthedocs.io/).

## Overview

`toolregistry-server` lets you register Python functions as tools and expose them as services through multiple protocols — REST APIs via OpenAPI and LLM integration via the Model Context Protocol.

## Ecosystem

The ToolRegistry ecosystem consists of three packages:

| Package | Description |
|---------|-------------|
| [`toolregistry`](https://toolregistry.readthedocs.io/) | Core library - Tool model, ToolRegistry, client integration |
| [`toolregistry-server`](https://toolregistry-server.readthedocs.io/) | Tool server - define tools and serve via OpenAPI/MCP |
| [`toolregistry-hub`](https://toolregistry-hub.readthedocs.io/) | Tool collection - Built-in tools, default server configuration |

```
toolregistry (core)
       ↓
toolregistry-server (tool server)
       ↓
toolregistry-hub (tool collection + server config)
```

See the [Ecosystem](ecosystem.md) page for a detailed overview of all packages.

## Quick Start

```bash
pip install toolregistry-server[all]
```

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app

# Create a registry and register tools
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

# Create route table and FastAPI app
route_table = RouteTable(registry)
app = create_openapi_app(route_table)
```

[Installation Guide →](usage/installation.md) | [Quick Start →](usage/quickstart.md)

## Key Features

- **Central Route Table**: A unified routing layer that bridges `ToolRegistry` and protocol adapters
- **OpenAPI Adapter**: Expose tools as RESTful HTTP endpoints with automatic OpenAPI schema generation
- **MCP Adapter**: Expose tools via the [Model Context Protocol](https://modelcontextprotocol.io/) for LLM integration
- **Authentication**: Built-in Bearer token authentication support
- **CLI**: Command-line interface for running servers without custom code
- **Dynamic Enable/Disable**: Runtime tool state management without server restart
- **ETag Caching**: HTTP caching via ETag headers for efficient API responses

## Architecture

```mermaid
graph TD
    TR[ToolRegistry<br/>tool definitions]
    RT[RouteTable<br/>central routing layer<br/><i>RouteEntry · RouteEntry · ...</i>]
    OA[OpenAPI Adapter<br/>FastAPI · REST]
    MA[MCP Adapter<br/>MCP SDK · LLM integration]
    GA[gRPC Adapter<br/>future]

    TR --> RT
    RT --> OA
    RT --> MA
    RT -.-> GA
```

## Documentation Contents

- [**Installation Guide**](usage/installation.md) - Install `toolregistry-server` with optional extras
- [**Quick Start**](usage/quickstart.md) - Get up and running in minutes
- [**Configuration**](usage/configuration.md) - JSON/JSONC configuration for the CLI
- [**Authentication**](usage/authentication.md) - Bearer token authentication setup
- [**Adapters**](adapters/) - OpenAPI and MCP protocol adapters
- [**CLI Reference**](cli/) - Command-line interface usage
- [**API Reference**](api/) - Comprehensive API documentation

## License

ToolRegistry Server is licensed under the **MIT License**.
