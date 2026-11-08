# App

`App` 是编排入口：构建 `ToolRegistry`、包装为 `RouteTable`，再分派给 OpenAPI 或 MCP 适配器。继承并重写 `prepare_registry()` 可提供自定义注册表；参见[扩展应用](../../guides/extending.md)。

::: toolregistry_server.app.App
