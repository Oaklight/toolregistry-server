---
title: API Reference
---

# API Reference

This section provides detailed API documentation for all modules in `toolregistry-server`.

## Application & CLI

| Module | Description |
|--------|-------------|
| [App](app.md) | Canonical programmatic entry point — `serve_openapi()`, `serve_mcp()`, `prepare_registry()` |
| [CLI](cli.md) | Canonical CLI entry point — subclass and override `configure_subparsers()` |
| [ServerIdentity](server_identity.md) | Carries name/version/description for banner and `--version` output |

## Core

| Module | Description |
|--------|-------------|
| [RouteTable](core/route_table.md) | Central routing layer — `RouteTable` and `RouteEntry` classes |

## Adapters

| Module | Description |
|--------|-------------|
| [OpenAPI](openapi.md) | REST API adapter (FastAPI or vendored `httpserver`) — `OpenAPIAdapter`, `Adapter.create_and_run()` |
| [MCP](mcp.md) | Model Context Protocol adapter — `MCPAdapter`, `Adapter.create_and_run()` |

## Authentication

| Module | Description |
|--------|-------------|
| [Auth](auth.md) | Bearer token authentication — `load_tokens()`, `BearerTokenAuth` |
