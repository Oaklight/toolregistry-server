# OpenAPI Adapter

The OpenAPI adapter exposes `ToolRegistry` tools as RESTful HTTP endpoints using [FastAPI](https://fastapi.tiangolo.com/).

## Overview

The adapter automatically:

- Creates POST endpoints for each registered tool
- Generates dynamic Pydantic models from JSON Schema parameters
- Produces an OpenAPI schema (accessible at `/openapi.json`)
- Provides a `/tools` metadata endpoint listing available tools
- Supports runtime enable/disable of individual tools
- Implements ETag-based HTTP caching

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

## API Reference

See the [OpenAPI API Reference](../reference/api/openapi.md) for detailed documentation.
