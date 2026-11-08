# App

`App` 是编排入口：构建 `ToolRegistry`、包装为 `RouteTable`，再分派给 OpenAPI 或 MCP 适配器。`serve_gateway()` 构建 `GatewayAdapter`，在同一个 ASGI 应用中提供 OpenAPI 与两种 MCP HTTP 传输；`create_gateway()` 只构建适配器、不启动监听，便于进程内管理扩展调用 `list_frontends()` 与 `update_frontend()`。继承并重写 `prepare_registry()` 可提供自定义注册表；参见[扩展应用](../../guides/extending.md)。

::: toolregistry_server.app.App
