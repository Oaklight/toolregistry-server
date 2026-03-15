# Authentication

`toolregistry-server` provides built-in Bearer token authentication for securing your OpenAPI endpoints.

## Overview

The authentication module uses HTTP Bearer token authentication via FastAPI's dependency injection system. It supports:

- Multiple tokens
- Runtime token management (add/remove tokens)
- Dynamic enable/disable without server restart

## Setting Up Authentication

### Via Code

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app
from toolregistry_server.auth import BearerTokenAuth, create_bearer_dependency

# Setup registry and route table
registry = ToolRegistry()
route_table = RouteTable(registry)

# Create auth with tokens
auth = BearerTokenAuth(tokens=["my-secret-token", "another-token"])
bearer_dep = create_bearer_dependency(auth)

# Create app with authentication
app = create_openapi_app(route_table, dependencies=[bearer_dep])
```

### Via CLI

```bash
# Single token
toolregistry-server openapi --config config.json --auth-token "your-secret-token"

# Token file (one token per line)
toolregistry-server openapi --config config.json --auth-tokens-file tokens.txt
```

Token file format:

```
token-one
token-two
token-three
```

## Making Authenticated Requests

Include the Bearer token in the `Authorization` header:

```bash
curl -X POST http://localhost:8000/calculator/evaluate \
  -H "Authorization: Bearer my-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"expression": "2 + 3"}'
```

## Runtime Token Management

The `BearerTokenAuth` class supports runtime token management:

```python
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
