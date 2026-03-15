# Installation

## Basic Installation

Install the core package:

```bash
pip install toolregistry-server
```

This installs the core routing layer (`RouteTable`, `RouteEntry`) without any protocol adapters.

## With Protocol Support

### OpenAPI Support

For exposing tools as RESTful HTTP endpoints with FastAPI:

```bash
pip install toolregistry-server[openapi]
```

This additionally installs:

- `FastAPI` (>=0.119.0)
- `Uvicorn` with standard extras (>=0.24.0)

### MCP Support

For exposing tools via the [Model Context Protocol](https://modelcontextprotocol.io/):

```bash
pip install toolregistry-server[mcp]
```

This additionally installs:

- `mcp` SDK (>=1.8.0)

### Full Installation

Install all protocol adapters:

```bash
pip install toolregistry-server[all]
```

## Development Installation

For contributing or development:

```bash
git clone https://github.com/Oaklight/toolregistry-server.git
cd toolregistry-server
pip install -e ".[all,dev]"
```

The `dev` extra includes:

- `pytest`, `pytest-asyncio` - Testing
- `httpx` - HTTP test client
- `ruff` - Linting and formatting
- `build`, `twine` - Package building and publishing

## Requirements

- **Python**: 3.10 or higher
- **Core dependency**: [`toolregistry`](https://toolregistry.readthedocs.io/) >= 0.5.0

## Verify Installation

```python
from toolregistry_server import RouteTable, RouteEntry
print("toolregistry-server installed successfully!")
```
