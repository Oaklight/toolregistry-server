# MCP Adapter

The MCP adapter exposes `ToolRegistry` tools via the [Model Context Protocol](https://modelcontextprotocol.io/) for LLM integration.

## Overview

The adapter:

- Registers `list_tools` and `call_tool` MCP handlers that read from `RouteTable` at request time
- Supports stdio, SSE, and Streamable HTTP transports
- Handles async and sync tools transparently
- Provides both blocking (`run`) and async (`run_async`) entry points

## Server Identity and Instructions

`initialize` reports `serverInfo.version` as the installed `toolregistry-server` version. Embedders that brand the server keep their own version instead: `App` and the CLI forward `ServerIdentity.version`, so the Hub advertises the Hub version.

When `discover_tools` is served (see below), the `initialize` response also carries `instructions` describing the two-step deferred-tool flow, so a client does not have to infer it from tool descriptions. Both fields can be set directly:

```python
from toolregistry_server.adapters.mcp import MCPAdapter

adapter = MCPAdapter(
    route_table,
    version="1.2.3",
    instructions="Use the math tools; other tools are deferred.",
)
```

## Deferred Tools and `call_deferred`

When the underlying `ToolRegistry` has tool discovery enabled (via `enable_tool_discovery()`), two infrastructure tools are automatically exposed through the MCP adapter:

- **`discover_tools`** — BM25 fuzzy search over all registered tools. Returns full schema for deferred tools.
- **`call_deferred`** — Proxy tool for invoking deferred tools by name. Accepts `_target_tool` and forwards `**kwargs` to the target.

Deferred tools (those with `metadata.defer=True`) are excluded from `tools/list` but remain callable via `call_deferred` after the LLM discovers their schema. This enables progressive disclosure of large tool catalogs without overwhelming the initial tool listing.

## Live Tool Updates

The MCP adapter advertises `tools_changed` capability and notifies clients when the route table changes (tools registered, unregistered, enabled, or disabled).

How the notification travels depends on the protocol era:

- **Handshake-era clients** (through `2025-11-25`): the adapter sends `notifications/tools/list_changed` on every connected session. Clients that handle it can re-fetch `tools/list`.
- **Modern-era clients** (`2026-07-28`, SDK v2): the wire has no standing server→client channel, so a notification sent on the connection is dropped. Instead the adapter serves `subscriptions/listen` (SEP-2575); a client opens a listen stream requesting `tools_list_changed` and receives the event there.

A modern-era client that wants to see live updates should therefore open a listen stream once, and refetch `tools/list` when the event arrives:

```python
from mcp.client.subscriptions import listen

async with listen(session, tools_list_changed=True) as sub:
    async for event in sub:
        tools = await session.list_tools()
```

### Caching the tool list

On SDK v2 (MCP spec 2026-07-28) `tools/list` carries cache hints. The default is `ttlMs: 0` — clients refetch every time — with `cacheScope: "private"`, because the served set depends on the deployment's profile and the caller's authorization.

Caching remains opt-in: a client that does not subscribe to changes keeps a cached list for the whole TTL. With a `subscriptions/listen` stream open, the event invalidates the cache (the SDK client evicts `tools/list` on the notification) and the next `list_tools()` refetches.

```bash
# opt into a five-minute cache, still per-caller
toolregistry-server mcp --config tools.yaml --transport http \
  --tools-list-ttl-ms 300000 --tools-list-cache-scope private
```

## Quick Start

### Via `App` (recommended)

```python
from toolregistry_server.app import App

# From a config file
App().serve_mcp(config_path="tools.yaml", transport="stdio")
App().serve_mcp(config_path="tools.yaml", transport="sse", host="0.0.0.0", port=8000)
App().serve_mcp(config_path="tools.yaml", transport="http", host="0.0.0.0", port=8000)
```

### Via `MCPAdapter` directly

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp import MCPAdapter

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

route_table = RouteTable(registry)
adapter = MCPAdapter(route_table)

# Blocking (suitable for scripts / CLI)
adapter.run(transport="stdio")
adapter.run(transport="sse", host="0.0.0.0", port=8000)
adapter.run(transport="http", host="0.0.0.0", port=8000)
```

### Async entry point

Use `run_async` when you're already inside an event loop:

```python
import asyncio
from toolregistry_server.adapters.mcp import MCPAdapter

adapter = MCPAdapter(route_table)
asyncio.run(adapter.run_async(transport="sse", host="0.0.0.0", port=8000))

# or within an existing loop:
await adapter.run_async(transport="stdio")
```

### Accessing the underlying MCP server

```python
from toolregistry_server.adapters.mcp import MCPAdapter, run_stdio

adapter = MCPAdapter(route_table)
server = adapter.server   # mcp.server.lowlevel.Server instance
asyncio.run(run_stdio(server))
```

## Transport Comparison

| Transport | Alias | Use Case |
|-----------|-------|----------|
| `stdio` | — | Subprocess model (Claude Desktop, IDE plugins) |
| `sse` | — | SSE-based HTTP clients |
| `streamable-http` | `http` | Production HTTP deployments |

`http` is accepted as an alias for `streamable-http` and is normalized internally.

## Authentication (Streamable HTTP)

Pass a Bearer tokens file via `tokens_path`, or set the `API_BEARER_TOKEN` environment variable (comma-separated):

```python
adapter.run(
    transport="http",
    host="0.0.0.0",
    port=8000,
    tokens_path="/etc/myapp/tokens.txt",
    server_url="https://tools.example.com/mcp",
)
```

```bash
# CLI equivalent
toolregistry-server mcp --config tools.yaml --transport http \
  --tokens /etc/myapp/tokens.txt \
  --server-url https://tools.example.com/mcp
```

### Behind a reverse proxy

When token auth is enabled the server publishes RFC 9728 protected-resource metadata, and points clients at it from the `WWW-Authenticate` header of a 401. That URL has to be one clients can reach, so it must be the public origin, not the bind address:

```bash
toolregistry-server mcp --config tools.yaml --transport http \
  --host 0.0.0.0 --port 8000 \
  --server-url https://tools.example.com/mcp
```

Without `--server-url` the URL is derived from `--host`/`--port`. That is correct only when clients reach the server directly: binding `0.0.0.0` (the usual container/proxy setup) advertises `http://0.0.0.0:8000/...`, which no client can resolve — and which tells unauthenticated callers how the server is bound. The server logs a warning at startup when that is what would be advertised.

The reverse proxy also has to forward the discovery paths, since clients fetch them from the same origin: `/.well-known/oauth-protected-resource/mcp` alongside `/mcp`.

## MCP Client Configuration

### Claude Desktop (stdio)

```json
{
  "mcpServers": {
    "my-tools": {
      "command": "toolregistry-server",
      "args": ["mcp", "--config", "/path/to/tools.yaml"]
    }
  }
}
```

### HTTP-based clients

Connect to:

```
http://localhost:8000/mcp       # streamable-http
http://localhost:8000/sse       # SSE
```

## Tool Annotations

When a tool's metadata declares a display title or execution hints, `tools/list` carries them as MCP `title` and `annotations`:

- `ToolMetadata.title` → `title`
- `Tool.execution_hints` → `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`)

Unset hints stay absent: a tool that declares nothing keeps the minimal `{name, description, inputSchema}` definition, so hosts apply their protocol defaults. Set them in code or in the config file:

```yaml
tool_metadata:
  datetime-now:
    title: Current Date and Time
    read_only_hint: true
    idempotent_hint: true
    open_world_hint: false
```

!!! note "Tags and hints are different layers"
    Tags (`read_only`, `file_system`, `network`, …) drive local permission rules and deployment profiles: the `remote` profile disables `file_system`, `destructive` and `privileged` tools. Hints describe a single call to an MCP host. A method-level hint does not change profile exposure — a read-only file tool is still hidden from a `remote` deployment.

## Error Semantics

`tools/call` draws the line at what the model can repair. A name the server cannot dispatch is a request error; anything the model could fix or learn from stays a tool result:

| Situation | What the client sees |
|-----------|----------------------|
| Tool name unknown, or the tool is disabled | JSON-RPC error `-32602` (Invalid params) |
| Arguments invalid (missing required field, wrong type) | `CallToolResult` with `isError: true` and the error text as content |
| Tool raises while executing | `CallToolResult` with `isError: true` and the error text as content |
| `call_deferred` with an unknown `_target_tool` | `CallToolResult` with `isError: true` |

A protocol error means the call itself was invalid, so it should not be fed back to the model as a tool result. An execution error is model-visible: the LLM reads the message and can retry or change approach. Disabled tools report the disable reason in the error message.

Bad arguments are deliberately *not* a protocol error even though they are also a request problem: the text (`Expected int at 'a', got str`) is what lets the model correct itself. The same reasoning keeps the `call_deferred` dispatcher's answer a tool result — its text is guidance ("tool 'ghost' not found in registry. Did you mean: …") aimed at the model. A host that switches on `-32602` therefore sees one shape for "that name is not a tool here" when calling directly, and another when dispatching through `call_deferred`.

## API Reference

See the [MCP API Reference](../reference/api/mcp.md) for detailed documentation.
