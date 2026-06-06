---
title: Examples
---

# Examples

Runnable examples live in the [`examples/`](https://github.com/Oaklight/toolregistry-server/tree/master/examples) directory of the repository. The snippets below are reproduced for quick reference.

## Shared Tools Module

All examples import tools from a shared module:

```python title="examples/tools.py"
def add(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b

def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b
```

## Programmatic — OpenAPI Server

Create a FastAPI app and run it with Uvicorn:

```python title="examples/openapi_server.py"
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app
from tools import add, greet, multiply

registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

route_table = RouteTable(registry)
app = create_openapi_app(route_table)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

```bash
cd examples
python openapi_server.py
# Swagger UI → http://localhost:8000/docs
# Tool list  → http://localhost:8000/tools
```

## Programmatic — MCP Server

Expose the same tools via stdio for MCP-compatible clients:

```python title="examples/mcp_server.py"
import asyncio
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.mcp import create_mcp_server, run_stdio
from tools import add, greet, multiply

registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

route_table = RouteTable(registry)
server = create_mcp_server(route_table)

if __name__ == "__main__":
    asyncio.run(run_stdio(server))
```

## CLI with Config File

The CLI can start either server type from a JSON/JSONC configuration file — no Python code required:

```jsonc title="examples/config.jsonc"
{
  "mode": "denylist",
  "disabled": [],
  "tools": [
    {
      "type": "python",
      "module": "examples.tools",
      "namespace": "examples"
    }
  ]
}
```

```bash
# OpenAPI
PYTHONPATH=. toolregistry-server openapi --config examples/config.jsonc

# MCP (stdio)
PYTHONPATH=. toolregistry-server mcp --config examples/config.jsonc

# MCP (Streamable HTTP)
PYTHONPATH=. toolregistry-server mcp --config examples/config.jsonc \
    --transport streamable-http --port 8000
```

See [Configuration](../guides/configuration.md) for the full config file reference and [CLI Reference](../reference/cli/) for all command-line options.
