# OpenAPI Adapter

The OpenAPI adapter exposes `ToolRegistry` tools as RESTful HTTP endpoints, using [FastAPI](https://fastapi.tiangolo.com/) or the vendored zero-dependency `httpserver` backend.

## Overview

The adapter automatically:

- Creates POST endpoints for each registered tool
- Validates each request body against the route's served JSON Schema (or, for signature-derived tools, the tool's own validation model)
- Produces an OpenAPI schema (accessible at `/openapi.json`)
- Provides a `/tools` metadata endpoint listing available tools
- Supports runtime enable/disable of individual tools
- Implements ETag-based HTTP caching
- Supports schemas that permit extra properties (`additionalProperties` absent, `true`, or `{}`), such as `call_deferred`'s `**kwargs`

## Building the document without FastAPI

`RouteTable` already owns the API contract, so the OpenAPI document can be built from it directly:

```python
from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi.spec import build_openapi_schema

document = build_openapi_schema(RouteTable(registry), title="My Tools", version="2.0.0")
```

The output matches what the served `/openapi.json` publishes for enabled tools — the same `operationId`, summary, namespace tag, parameter schema and `x-*` execution-hint extensions — and a disabled tool is simply absent, with no post-filtering. `include_meta=False` omits the `/tools` and `/events` operations when only the tool routes are wanted.

## Quick Start

### Via `App` (recommended)

```python
from toolregistry_server.app import App

# From a config file
App().serve_openapi(config_path="tools.yaml", host="0.0.0.0", port=8000)

# From a pre-built registry
from toolregistry import ToolRegistry
registry = ToolRegistry()
# ... register tools ...
App().serve_openapi(registry=registry, port=9000)
```

### Via `OpenAPIAdapter` directly

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import OpenAPIAdapter

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

route_table = RouteTable(registry)

adapter = OpenAPIAdapter(
    route_table,
    title="My Tool Server",
    version="1.0.0",
    tokens=["secret-token"],   # optional Bearer auth
)
adapter.run(host="0.0.0.0", port=8000)
```

### One-shot classmethod

```python
from toolregistry_server.adapters.openapi import OpenAPIAdapter

OpenAPIAdapter.create_and_run(
    route_table,
    host="0.0.0.0",
    port=8000,
    tokens_path="/etc/myapp/tokens.txt",
)
```

## Endpoint Structure

Each tool is exposed as a POST endpoint at its route path:

```
POST /{namespace}/{tool_name}
```

For example, a tool `evaluate` in namespace `calculator` becomes:

```
POST /calculator/evaluate
```

### Private addresses

A spec URL that resolves to a loopback, link-local or private address is
refused before it is fetched. The base URL the tools use — `servers[0]` or the
configured `base_url` — is validated before each request, and a redirect is
refused by default because the next hop cannot be validated
(`follow_redirects=False`). Allow a local or private API per source:

```yaml
tools:
  - type: openapi
    url: http://127.0.0.1:8000/openapi.json
    namespace: local_api
    allow_private_hosts: true
```

The same flag is accepted by `load_openapi_spec(..., allow_private_hosts=True)`, `validate_spec_url()` and `HttpClientConfig` for callers that build the client themselves. See [Configuration](../guides/configuration.md#private-addresses).

### Spec loading and refresh

An OpenAPI source loads its document once, when the source is registered. A
route refresh — `RouteTable.refresh()`, `refresh_all()`, and the
`last_refreshed_at` field on `/tools` — rebuilds routes from the tools already
in the registry. It does not download the spec again, so a path added to the
remote document stays invisible until the source is registered again: restart
the server, or, in the Hub, change that source's entry in `tools.jsonc` while
`--config-reload` is on (an unchanged declaration is not re-registered).

The source's `allow_private_hosts` reaches both checks: the spec URL is
validated before it is fetched, and the client validates the base URL it
actually uses (`servers[0]` or the configured `base_url`) before each request.
Registering the source again re-downloads the document through the same
outbound address policy; no refresh path fetches a spec without that check.

### Request Format

Parameters are passed as a JSON body:

```bash
curl -X POST http://localhost:8000/calculator/evaluate \
  -H "Content-Type: application/json" \
  -d '{"expression": "2 + 3 * 4"}'
```

### Response Format

```json
{"result": 14}
```

## Tools Metadata Endpoint

`GET /tools` returns all available tools with their schemas:

```bash
curl http://localhost:8000/tools
```

Each tool entry includes `schema_hash` (SHA-256 fingerprint of the tool's parameter schema) and `last_refreshed_at` (ISO 8601 timestamp of the last schema refresh) when available. These fields enable clients to detect schema changes without re-fetching full tool definitions.

## Real-time Events

`GET /events` provides a Server-Sent Events (SSE) stream of tool change events:

```bash
curl -N http://localhost:8000/events
```

Events are emitted when tools are registered, unregistered, enabled, or disabled. Each event contains the tool name and event type as JSON.

## HTTP Backends

The adapter can serve on either of two backends with the same wire contract:

| Backend | Server | Third-party packages |
|---|---|---|
| `fastapi` (default) | uvicorn | FastAPI, uvicorn and their dependencies |
| `httpserver` | vendored `httpserver` | none |

```bash
toolregistry-server openapi --config tools.yaml --backend httpserver
```

```python
OpenAPIAdapter(route_table, backend="httpserver")
App().serve_openapi(config_path="tools.yaml", backend="httpserver")
```

`httpserver` is the zero-dependency path from epic #101. It serves the same routes, the same 422 `{"detail": [...]}` envelope, the same ETag/`304` behaviour on `/tools` and `/openapi.json`, and streams `/events` over Server-Sent Events; a differential test suite exercises both apps over sockets and asserts identical status, `ETag` and body bytes. `--reload` is a uvicorn feature: on `httpserver` it logs a warning and is ignored.

Both backends encode tool results with the same helper, which uses core's `ToolResultJSONEncoder`: a `bytes` result is base64 on either backend, and non-finite floats and `Decimal` values (`NaN`, `Infinity`) become `null` instead of a bare `NaN` or the string `"NaN"`.

### Request body limit

Both backends share `--max-body-size` (default `1048576`, one MiB):

```bash
toolregistry-server openapi --config tools.yaml --max-body-size 4194304
```

```python
OpenAPIAdapter(route_table, max_body_size=4 * 1024 * 1024)
```

The value must be a positive integer: the CLI rejects zero or a negative value at parse time, and `OpenAPIAdapter` raises `ValueError`. A body over the limit returns HTTP `413` with a JSON `{"detail": "Request body too large"}` envelope on both backends.

## Spec-supplied regexes

Proxied OpenAPI tools publish `pattern` and `patternProperties` from the
upstream document. Python's `re` has no timeout, so a schema-supplied
expression can backtrack catastrophically.

Without the optional `regex` extra the server does **not** execute them:
validation uses a copy without `pattern` and `patternProperties`, and an
`additionalProperties: false` at a level that carried patterns is relaxed.
Clients still see the full schema; the upstream API stays the authority for
the format.

Install the extra to enforce them under a bound:

```bash
pip install toolregistry-server[regex]
```

Every match then runs through `regex.search(..., timeout=...)`: 50 ms per
match and 250 ms per request by default. `patternProperties` calls the
matcher once per pattern (to compile it, even for an empty object) and once
per (pattern, key), so 10 patterns over a 200-key object is 2 010 calls
sharing the 250 ms budget. A match or budget timeout answers `500` with
`{"detail": "Tool '<name>' validation timed out"}`; the pattern is never
echoed to the client.

```bash
toolregistry-server openapi --config tools.yaml \
  --regex-timeout 0.05 --regex-budget 0.25
```

```python
OpenAPIAdapter(route_table, regex_timeout=0.05, regex_budget=0.25)
```

`0` disables execution and restores the default behaviour. A negative or
non-numeric bound fails at startup. Installing the extra tightens
validation: a payload that used to pass because its pattern was ignored can
now be rejected, and `additionalProperties` is no longer relaxed.

At load time every `pattern` and `patternProperties` key in a newly
registered source is compiled. By default only the tools whose own schema
contains an invalid pattern are dropped — each with an error log naming the
tool, the schema path and the pattern — and the rest of the source stays
registered; the source counts as failed only when no tool survives.
`--strict-spec-regex` restores all-or-nothing behaviour: every tool from
the source is removed and startup aborts.

## Authentication

Pass Bearer tokens via a file or `API_BEARER_TOKEN` env var:

```python
from toolregistry_server.adapters.openapi import OpenAPIAdapter
from toolregistry_server.auth import load_tokens

tokens = load_tokens(tokens_path="/etc/myapp/tokens.txt")
adapter = OpenAPIAdapter(route_table, tokens=tokens or None)
adapter.run(host="0.0.0.0", port=8000)
```

```bash
# CLI
toolregistry-server openapi --config tools.yaml --tokens tokens.txt
```

## Disabled Tools

When a tool is disabled at runtime, its endpoint returns `503 Service Unavailable`:

```json
{"detail": "Tool 'calculator_evaluate' is currently disabled"}
```

Disabled tools are also excluded from the dynamic OpenAPI schema.

## ETag Caching

The adapter includes `ETagMiddleware` for HTTP caching on `/tools` and `/openapi.json`:

- Each response includes an `ETag` header
- Clients can send `If-None-Match` for conditional requests
- Returns `304 Not Modified` when ETag matches

## Operation Extensions

OpenAPI has no standard slot for execution hints, so the same served hints that MCP emits as `annotations` are exposed as `x-` extensions on each generated operation:

| Extension | Source |
|---|---|
| `x-read-only` | `read_only_hint` |
| `x-destructive` | `destructive_hint` |
| `x-idempotent` | `idempotent_hint` |
| `x-open-world` | `open_world_hint` |
| `x-title` | `ToolMetadata.title` |

Only declared hints appear by default: an operation for a tool that declares nothing keeps its previous shape, and tag-derived values are omitted unless the `RouteTable` enables `emit_derived_execution_hints`. As on the MCP side, tags gate deployment exposure and hints describe a call, so a method-level hint does not change which tools a profile exposes.

## API Reference

See the [OpenAPI API Reference](../reference/api/openapi.md) for detailed documentation.
