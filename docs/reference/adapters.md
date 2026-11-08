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
from toolregistry_server.adapters.openapi import OpenAPIAdapter

registry = ToolRegistry()
# ... register tools ...

route_table = RouteTable(registry)

# One-call static dispatch — equivalent to App().serve_openapi()
OpenAPIAdapter.create_and_run(route_table, host="0.0.0.0", port=8000)
```

Similarly for MCP:

```python
from toolregistry_server.adapters.mcp import MCPAdapter

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

An override is descriptive: request validation still uses the tool's own validation model. For a model-backed tool, an override that is stricter than that model — adds a required field, tightens `minimum`/`maximum`/`minLength`/`maxLength`, changes `pattern`, narrows or changes `enum`, or narrows or changes a property's type — is rejected at save time, because the model would not enforce it. A looser override is saved with a `UserWarning`. Proxy tools validate against the served schema, so they are not restricted.

`override_parameters` raises `ValueError` for a rejected override. A loader that applies overrides from a configuration file should catch that error, log the tool name and reason, and skip the override (keeping the tool's own schema) so the service still starts.

By default adapters publish only hints a tool declared explicitly. Tag-derived hints are opt-in: `RouteTable(..., emit_derived_execution_hints=True)` or `set_emit_derived_execution_hints(True)` restores them.

## Request Validation

The OpenAPI adapter validates a request body before calling the tool:

- Signature-derived tools are checked against their generated model, including coercion and unknown-key filtering.
- Proxy tools (OpenAPI, MCP, LangChain) have no model, so the body is checked against the tool's JSON Schema.

For proxy tools, a `pattern` or `patternProperties` that came from the OpenAPI document is **not executed by default**. A spec is untrusted content, Python's `re` has no timeout, and a short catastrophic expression such as `(a+)+$` can hang a request. Without the optional `regex` extra, validation uses a copy without those keywords, and `additionalProperties: false` at a level that carried `patternProperties` is relaxed so keys the patterns used to cover are not rejected as unknown. With the extra installed, the patterns run under a per-match timeout and a per-request budget (`--regex-timeout`, `--regex-budget`). The constraint is always published: `/openapi.json`, the MCP `inputSchema` and the route ETag keep the original schema, and the upstream API remains the authority for the field format. Overrides supplied through `RouteTable.override_parameters` are operator code and keep their regexes.

## Dynamic Behavior

Adapters read from the `RouteTable` at request time, which means:

- **Enable/Disable**: Tools can be toggled at runtime without restarting the server
- **No drift**: The adapter always reflects the current state of the `RouteTable`
- **Observer pattern**: Adapters can subscribe to `RouteTable` changes via listeners
