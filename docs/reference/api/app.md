# App

`App` is the orchestration entry point: it builds a `ToolRegistry`, wraps it in a `RouteTable`, and dispatches to the OpenAPI or MCP adapter. `serve_gateway()` builds a `GatewayAdapter` that serves OpenAPI and both MCP HTTP transports from one ASGI application; `create_gateway()` returns that adapter without starting a listener, so an in-process admin extension can call `list_frontends()` and `update_frontend()`. Subclass `App` and override `prepare_registry()` to supply a custom registry; see [Extending Applications](../../guides/extending.md).

::: toolregistry_server.app.App
