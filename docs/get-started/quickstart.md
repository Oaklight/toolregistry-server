# Quick Start

This guide walks you through the basic usage of `toolregistry-server` to expose your tools as services using the v0.4.0 API.

## Programmatic Usage: OpenAPI Server

Use the `App` class to serve your tools as RESTful HTTP endpoints:

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

class MyApp(App):
    def prepare_registry(self):
        registry = ToolRegistry()

        @registry.register
        def greet(name: str) -> str:
            """Greet someone by name."""
            return f"Hello, {name}!"

        @registry.register
        def add(a: float, b: float) -> float:
            """Add two numbers."""
            return a + b

        self.registry = registry

if __name__ == "__main__":
    MyApp().serve_openapi(host="0.0.0.0", port=8000)
```

Your tools are now accessible as POST endpoints at `http://localhost:8000/`.

For a one-liner without subclassing:

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

registry = ToolRegistry()
registry.register(my_tool)

App(registry=registry).serve_openapi(host="0.0.0.0", port=8000)
```

## Programmatic Usage: MCP Server

Use `App.serve_mcp()` to expose your tools via the Model Context Protocol:

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    # stdio transport (default, for Claude Desktop, etc.)
    App(registry=registry).serve_mcp()

    # Or streamable-http transport
    # App(registry=registry).serve_mcp(transport="streamable-http", host="0.0.0.0", port=8000)
```

For a one-call shortcut using the low-level adapter:

```python
from toolregistry_server.adapters.mcp import MCPAdapter

MCPAdapter.create_and_run(registry=registry, transport="stdio")
```

## Using the CLI

The quickest way to start a server without writing code:

```bash
# Start OpenAPI server
toolregistry-server openapi --config config.json --port 8000

# Start MCP server (stdio transport, default)
toolregistry-server mcp --config config.json

# Start MCP server (streamable-http transport)
toolregistry-server mcp --config config.json --transport streamable-http --port 8000

# Start MCP server (SSE transport)
toolregistry-server mcp --config config.json --transport sse --port 8000

# With bearer token authentication
toolregistry-server openapi --config config.json --tokens tokens.txt
```

See the [CLI Reference](../reference/cli/) and [Configuration Guide](../guides/configuration.md) for details on config file format and all available flags.

## Next Steps

- [Examples](../examples/) - Runnable scripts and config snippets
- [Configuration](../guides/configuration.md) - Learn about JSON/JSONC configuration files
- [Authentication](../guides/authentication.md) - Set up Bearer token authentication
- [OpenAPI Adapter](../guides/openapi.md) - Deep dive into the REST API adapter
- [MCP Adapter](../guides/mcp.md) - Deep dive into the MCP adapter
