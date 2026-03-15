# Examples

Minimal examples for running toolregistry-server.

## Setup

Install from the project root:

```bash
pip install -e ".[openapi,mcp]"
```

## Programmatic usage

### OpenAPI server

```bash
cd examples
python openapi_server.py
```

- Swagger UI: http://localhost:8000/docs
- Tool list: http://localhost:8000/tools

### MCP stdio server

```bash
cd examples
python mcp_server.py
```

Starts an MCP server over stdio (for use by MCP-compatible clients).

## CLI with config file

From the project root (`PYTHONPATH=.` makes `examples.tools` importable):

```bash
# OpenAPI
PYTHONPATH=. toolregistry-server openapi --config examples/config.jsonc

# MCP (stdio)
PYTHONPATH=. toolregistry-server mcp --config examples/config.jsonc

# MCP (SSE)
PYTHONPATH=. toolregistry-server mcp --transport sse --config examples/config.jsonc
```
