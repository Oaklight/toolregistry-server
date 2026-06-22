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

Use `App.serve_openapi()` to start the server in one call:

```python title="examples/openapi_server.py"
from toolregistry import ToolRegistry
from toolregistry_server import App
from tools import add, greet, multiply

registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

if __name__ == "__main__":
    App(registry=registry).serve_openapi(host="0.0.0.0", port=8000)
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
from toolregistry import ToolRegistry
from toolregistry_server import App
from tools import add, greet, multiply

registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

if __name__ == "__main__":
    App(registry=registry).serve_mcp(transport="stdio")
```

## Custom App Subclass

Override `prepare_registry()` to inject a fully customised registry:

```python title="examples/custom_app.py"
from toolregistry import ToolRegistry
from toolregistry_server import App
from tools import add, greet, multiply

class MyApp(App):
    def prepare_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(add)
        registry.register(greet)
        registry.register(multiply)
        return registry

if __name__ == "__main__":
    MyApp().serve_openapi(host="0.0.0.0", port=8000)
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
