# App

`App` is the orchestration entry point: it builds a `ToolRegistry`, wraps it in a `RouteTable`, and dispatches to the OpenAPI or MCP adapter. Subclass it and override `prepare_registry()` to supply a custom registry; see [Extending Applications](../../guides/extending.md).

::: toolregistry_server.app.App
