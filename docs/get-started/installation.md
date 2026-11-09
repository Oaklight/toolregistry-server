# Installation

## Basic Installation

Install the core package:

```bash
pip install toolregistry-server
```

This installs the core routing layer (`RouteTable`, `RouteEntry`) and the
dependency-free `httpserver` OpenAPI server, which is what the standalone
`openapi` command runs. MCP support and the ASGI gateway backend are extras.

## With Protocol Support

!!! tip
    **zsh users** (default on macOS): wrap the package name in quotes, e.g. `pip install 'toolregistry-server[gateway]'` — zsh treats `[]` as glob patterns.

### ASGI Gateway Backend

The gateway serves OpenAPI, MCP and the admin panel from one ASGI application.
Its ASGI backend needs FastAPI and uvicorn:

```bash
pip install toolregistry-server[gateway]
```

This additionally installs:

- `FastAPI` (>=0.119.0)
- `Uvicorn` with standard extras (>=0.24.0)

The standalone `openapi` command does not need this extra. `[openapi]` is kept
as an empty alias so existing requirements still resolve; it installs nothing.

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
