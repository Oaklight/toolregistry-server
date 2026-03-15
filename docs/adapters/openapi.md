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

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app
import uvicorn

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

route_table = RouteTable(registry)
app = create_openapi_app(route_table, title="My Tool Server")

uvicorn.run(app, host="0.0.0.0", port=8000)
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

Tool results are returned as JSON:

```json
{
  "result": 14
}
```

## Tools Metadata Endpoint

`GET /tools` returns a list of all available tools with their schemas:

```bash
curl http://localhost:8000/tools
```

## Disabled Tools

When a tool is disabled at runtime, its endpoint returns `503 Service Unavailable`:

```json
{
  "detail": "Tool 'calculator_evaluate' is currently disabled"
}
```

Disabled tools are also excluded from the dynamic OpenAPI schema.

## ETag Caching

The adapter includes an `ETagMiddleware` that provides HTTP caching for the `/tools` and `/openapi.json` endpoints:

- Each response includes an `ETag` header
- Clients can send `If-None-Match` for conditional requests
- The server returns `304 Not Modified` when the ETag matches

## API Reference

### `create_openapi_app`

```python
from toolregistry_server.openapi import create_openapi_app

app = create_openapi_app(
    route_table,
    title="My Server",
    version="1.0.0",
    description="My tool server",
    dependencies=[bearer_dep],  # optional auth
)
```

See the [OpenAPI API Reference](../api/openapi.md) for detailed documentation.
