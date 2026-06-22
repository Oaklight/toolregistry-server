---
title: Extending toolregistry-server
---

# Extending toolregistry-server

`toolregistry-server` is designed to be **embedded and extended** by downstream packages (such as `toolregistry-hub`). Rather than forking or patching the library, you can build on top of it using three primary extension points:

| Extension point | Purpose |
|-----------------|---------|
| `App` subclassing | Inject a custom registry; override startup logic |
| `CLI` subclassing | Add command-line flags without touching argparse internals |
| `ServerIdentity` | Customise name, version, and description shown in banners |

---

## Subclassing `App`

`App` is the canonical programmatic entry point. It constructs the `RouteTable` and dispatches to the appropriate adapter. Override `prepare_registry()` to inject your own tools:

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

class HubApp(App):
    """An App that loads tools from toolregistry-hub."""

    def prepare_registry(self) -> ToolRegistry:
        from toolregistry_hub import load_hub_tools  # your package

        registry = ToolRegistry()
        load_hub_tools(registry)
        return registry

# Start an OpenAPI server on port 8000
HubApp().serve_openapi(host="0.0.0.0", port=8000)

# Or start an MCP server (stdio)
HubApp().serve_mcp(transport="stdio")
```

`prepare_registry()` is called once during startup. The returned `ToolRegistry` is used to build the `RouteTable`.

---

## Subclassing `CLI`

`CLI` wraps `App` with a full argument-parsing loop. Subclass it to add custom flags to the built-in `openapi` and `mcp` subparsers:

```python
from argparse import ArgumentParser
from toolregistry_server import CLI, ServerIdentity
from my_package import HubApp  # your App subclass

class HubCLI(CLI):
    """Custom CLI that adds an --admin-port flag."""

    def configure_subparsers(self, subparsers: dict[str, ArgumentParser]) -> None:
        # subparsers keys: "openapi", "mcp"
        for name, parser in subparsers.items():
            parser.add_argument(
                "--admin-port",
                type=int,
                default=9000,
                help="Port for the admin interface",
            )

if __name__ == "__main__":
    identity = ServerIdentity(
        name="Hub Server",
        version="1.0.0",
        description="toolregistry-hub server with admin interface",
    )
    HubCLI(app=HubApp(), identity=identity).run()
```

### What `configure_subparsers` receives

`subparsers` is a plain `dict[str, ArgumentParser]` where each key is a subcommand name (`"openapi"`, `"mcp"`). You can call any standard `ArgumentParser` method on the values — `add_argument`, `set_defaults`, etc. — without touching argparse internals or redefining existing flags.

---

## `ServerIdentity`

`ServerIdentity` controls the name and version string shown in startup banners and in the `--version` flag:

```python
from toolregistry_server import ServerIdentity

identity = ServerIdentity(
    name="My Tool Server",
    version="2.1.0",
    description="Powered by toolregistry-server",
)
```

Pass it to either `App.__init__` or `CLI.__init__`:

```python
# Via App
app = HubApp(identity=identity)
app.serve_openapi(port=8000)

# Via CLI (takes precedence)
HubCLI(app=HubApp(), identity=identity).run()
```

---

## `run_cli()` Standalone Helper

If you prefer **composition over inheritance**, use `run_cli()` to start the CLI loop without subclassing:

```python
from toolregistry_server import run_cli, ServerIdentity
from my_package import HubApp

run_cli(
    app=HubApp(),
    identity=ServerIdentity(name="Hub Server", version="1.0.0"),
)
```

`run_cli()` is equivalent to `CLI(app=..., identity=...).run()` but removes the need to define a `CLI` subclass when you only need custom identity or a pre-built `App`.

---

## Putting It Together

Here is a complete mini-example that mirrors what a downstream package like `toolregistry-hub` might do:

```python
# my_hub_server/__main__.py
from argparse import ArgumentParser
from toolregistry import ToolRegistry
from toolregistry_server import App, CLI, ServerIdentity

# 1. Custom App — loads hub tools
class HubApp(App):
    def prepare_registry(self) -> ToolRegistry:
        from toolregistry_hub import load_hub_tools

        registry = ToolRegistry()
        load_hub_tools(registry)
        return registry

# 2. Custom CLI — adds --admin-port
class HubCLI(CLI):
    def configure_subparsers(self, subparsers: dict[str, ArgumentParser]) -> None:
        for parser in subparsers.values():
            parser.add_argument(
                "--admin-port",
                type=int,
                default=9000,
                metavar="PORT",
                help="Admin interface port (default: 9000)",
            )

# 3. Wire it all together
IDENTITY = ServerIdentity(
    name="toolregistry-hub",
    version="1.0.0",
    description="Curated tools served via OpenAPI and MCP",
)

def main() -> None:
    HubCLI(app=HubApp(), identity=IDENTITY).run()

if __name__ == "__main__":
    main()
```

Run it just like the built-in CLI:

```bash
# OpenAPI server with custom admin port
python -m my_hub_server openapi --port 8000 --admin-port 9000

# MCP server via stdio
python -m my_hub_server mcp --transport stdio
```

---

## Authentication in Extended Apps

Use `auth.load_tokens()` to load Bearer tokens from a file and pass them to `App` or directly to adapters:

```python
from toolregistry_server.auth import load_tokens

tokens = load_tokens("/etc/my-server/tokens.txt")
HubApp(tokens=tokens).serve_openapi(port=8000)
```

See [Authentication](authentication.md) for the full token file format reference.
