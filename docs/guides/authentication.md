# Authentication

`toolregistry-server` provides built-in Bearer token authentication for both OpenAPI and MCP Streamable-HTTP endpoints.

## Overview

The authentication module supports:

- Multiple tokens loaded from a file
- Bearer token validation on all incoming requests
- Runtime token management (add/remove tokens without restart)
- Dynamic enable/disable

## Setting Up Authentication

### Via Code — `App`-based (recommended)

Load tokens from a file using `auth.load_tokens()` and pass them to `App`:

```python
from toolregistry import ToolRegistry
from toolregistry_server import App
from toolregistry_server.auth import load_tokens

registry = ToolRegistry()
registry.register(my_tool)

tokens = load_tokens("tokens.txt")   # one token per line, # comments ignored
App(registry=registry, tokens=tokens).serve_openapi(host="0.0.0.0", port=8000)
```

The same pattern works for MCP Streamable-HTTP:

```python
App(registry=registry, tokens=tokens).serve_mcp(
    transport="streamable-http", host="0.0.0.0", port=8000
)
```

Pass tokens inline without a file:

```python
App(registry=registry, tokens=["token-one", "token-two"]).serve_openapi()
```

### Via CLI — `--tokens` flag

```bash
# OpenAPI server with token file
toolregistry-server openapi --config config.json --tokens tokens.txt

# MCP streamable-http server with token file
toolregistry-server mcp --config config.json --transport streamable-http --tokens tokens.txt
```

Token file format (one token per line; lines starting with `#` are ignored):

```
# My API tokens
token-one
token-two
token-three
```

## MCP Streamable-HTTP Bearer Auth (new in v0.4.0)

Bearer token authentication is now supported for the MCP Streamable-HTTP transport, in addition to OpenAPI. Use the same `--tokens` flag or `App(tokens=...)` API — no extra configuration required.

```bash
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --port 8000 \
  --tokens tokens.txt
```

Clients connect with a standard `Authorization: Bearer <token>` header.

## Making Authenticated Requests

Include the Bearer token in the `Authorization` header:

```bash
curl -X POST http://localhost:8000/calculator/evaluate \
  -H "Authorization: Bearer my-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"expression": "2 + 3"}'
```

## Runtime Token Management (Advanced)

The underlying `BearerTokenAuth` class supports runtime token management without restarting the server. This is useful for advanced scenarios where tokens need to be rotated dynamically:

```python
from toolregistry_server.auth import BearerTokenAuth

auth = BearerTokenAuth(tokens=["initial-token"])

# Add a new token
auth.add_token("new-token")

# Remove a token
auth.remove_token("initial-token")

# Disable authentication entirely
auth.enabled = False

# Re-enable
auth.enabled = True
```

## Unauthenticated Requests

When authentication is enabled, requests without a valid token receive a `401 Unauthorized` response:

```json
{
  "detail": "Invalid or missing bearer token"
}
```

If no tokens are configured, authentication is automatically disabled and all requests are allowed.
