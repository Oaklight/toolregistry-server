---
title: Adapters
---

# Protocol Adapters

`toolregistry-server` provides protocol adapters that expose your custom tools as network services. Each adapter reads from the central `RouteTable` and translates tool definitions into protocol-specific endpoints.

## Available Adapters

| Adapter | Protocol | Transport | Status |
|---------|----------|-----------|--------|
| [OpenAPI](openapi.md) | REST/HTTP | HTTP | Stable |
| [MCP](mcp.md) | Model Context Protocol | stdio, SSE, Streamable HTTP | Stable |
| gRPC | gRPC | HTTP/2 | Planned |

## How Adapters Work

All adapters share the same flow:

```
ToolRegistry → RouteTable → Adapter → Protocol-specific endpoints
```

1. Tools are registered in a `ToolRegistry` instance
2. A `RouteTable` generates `RouteEntry` objects from the registry
3. The adapter reads `RouteEntry` objects and creates protocol-specific endpoints
4. Clients interact with tools via the adapter's protocol

## Dynamic Behavior

Adapters read from the `RouteTable` at request time, which means:

- **Enable/Disable**: Tools can be toggled at runtime without restarting the server
- **No drift**: The adapter always reflects the current state of the `RouteTable`
- **Observer pattern**: Adapters can subscribe to `RouteTable` changes via listeners
