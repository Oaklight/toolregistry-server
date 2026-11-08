# Installation

## Basic Installation

Install the core package:

```bash
pip install toolregistry-server
```

This installs the core routing layer (`RouteTable`, `RouteEntry`) without any protocol adapters.

## With Protocol Support

!!! tip
    **zsh users** (default on macOS): wrap the package name in quotes, e.g. `pip install 'toolregistry-server[openapi]'` — zsh treats `[]` as glob patterns.

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

- `mcp` SDK (>=1.22.0,<3)

### Full Installation

Install all protocol adapters:

```bash
pip install toolregistry-server[all]
```

`all` installs the `openapi` and `mcp` extras. The bounded spec-regex support is a separate opt-in:

### Bounded Spec-Regex Validation (optional)

```bash
pip install 'toolregistry-server[regex]'
```

This installs `regex` (>=2024.4.16). Without it, a `pattern` or `patternProperties` that came from an OpenAPI document is not executed during request validation; with it, every match runs under the per-match timeout and per-request budget (`--regex-timeout`, `--regex-budget`). See the [OpenAPI guide](../guides/openapi.md#spec-supplied-regexes).

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

- **Python**: 3.10 or higher (tested on 3.10–3.13)
- **Core dependency**: [`toolregistry`](https://toolregistry.readthedocs.io/) >= 0.20.1,<1.0.0

## Verify Installation

```python
from toolregistry_server import RouteTable, RouteEntry
print("toolregistry-server installed successfully!")
```
