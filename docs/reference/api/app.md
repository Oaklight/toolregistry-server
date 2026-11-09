# App

`App` is the orchestration entry point: it builds a `ToolRegistry`, wraps it in a `RouteTable`, and dispatches to the OpenAPI or MCP adapter. `serve_gateway()` builds a `GatewayAdapter` that serves OpenAPI, both MCP HTTP transports and, when enabled, the admin panel from one ASGI application; `create_gateway()` returns that adapter without starting a listener, so an in-process admin extension can call `list_frontends()` and `update_frontend()`.

Pass `admin_enabled=True` with `admin_token=` (or `admin_token_file=`) to host the admin panel. `keys_db=` supplies the panel's key store, and the runtime source manager is attached automatically when a config file is loaded. The panel starts on the first admin request, fails startup on a core extension contract violation, and is stopped by `GatewayAdapter.shutdown()`. The operator token is separate from the gateway bearer `tokens`; serving tokens never authenticate the panel. Subclass `App` and override `prepare_registry()` to supply a custom registry; see [Extending Applications](../../guides/extending.md).

::: toolregistry_server.app.App
