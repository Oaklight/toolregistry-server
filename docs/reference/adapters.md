---
title: Adapters
---

# Protocol Adapters

`toolregistry-server` provides protocol adapters that expose your custom tools as network services. Each adapter reads from the central `RouteTable` and translates tool definitions into protocol-specific endpoints.

!!! tip "Recommended entry point"
    For most use cases, start with the [`App`](../guides/extending.md) class rather than the adapter layer directly. `App` wires up `ToolRegistry → RouteTable → Adapter` for you and exposes `serve_openapi()` / `serve_mcp()` as one-liners. The adapter layer described here is what `App` delegates to internally.

## Available Adapters

| Adapter | Protocol | Transport | Status |
|---------|----------|-----------|--------|
| [OpenAPI](../guides/openapi.md) | REST/HTTP | HTTP | Stable |
| [MCP](../guides/mcp.md) | Model Context Protocol | stdio, SSE, Streamable HTTP | Stable |
| gRPC | gRPC | HTTP/2 | Planned |

## How Adapters Work

All adapters share the same flow, with `App` sitting above the adapter layer as the orchestration entry point:

```
App → RouteTable → Adapter → Protocol-specific endpoints
 ↑
CLI (optional, sits above App)
```

1. Tools are registered in a `ToolRegistry` instance
2. `App` constructs a `RouteTable` from the registry and dispatches to the appropriate adapter
3. A `RouteTable` generates `RouteEntry` objects from the registry
4. The adapter reads `RouteEntry` objects and creates protocol-specific endpoints
5. Clients interact with tools via the adapter's protocol

## Using Adapters Directly

When you need fine-grained control, you can bypass `App` and call the adapter layer directly via `Adapter.create_and_run()`:

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import OpenAPIAdapter

registry = ToolRegistry()
# ... register tools ...

route_table = RouteTable(registry)

# One-call static dispatch — equivalent to App().serve_openapi()
OpenAPIAdapter.create_and_run(route_table, host="0.0.0.0", port=8000)
```

Similarly for MCP:

```python
from toolregistry_server.mcp import MCPAdapter

MCPAdapter.create_and_run(route_table, transport="stdio")
```

## Route-Level Overrides

`RouteTable` can change what one route publishes without modifying the registry `Tool`:

```python
route_table.override_parameters(
    "analyse",
    {"type": "object", "properties": {"lat_spec": {"type": "array"}}},
)
route_table.override_execution_hints("analyse", {})  # suppress all annotations
```

`override_parameters` replaces the parameter schema; `override_execution_hints` replaces the hint mapping, and `{}` means "no claim". Pass `None` to clear either override. Both are reflected in MCP (`inputSchema` / `annotations`) and OpenAPI (request schema / `x-` extensions), survive `refresh()` and `refresh_all()`, and notify `RouteTable` listeners. The registry `Tool` and its `schema_hash` are untouched, so an override cannot leak into another consumer.

By default adapters publish only hints a tool declared explicitly. Tag-derived hints are opt-in: `RouteTable(..., emit_derived_execution_hints=True)` or `set_emit_derived_execution_hints(True)` restores them.

## Dynamic Behavior

Adapters read from the `RouteTable` at request time, which means:

- **Enable/Disable**: Tools can be toggled at runtime without restarting the server
- **No drift**: The adapter always reflects the current state of the `RouteTable`
- **Observer pattern**: Adapters can subscribe to `RouteTable` changes via listeners
