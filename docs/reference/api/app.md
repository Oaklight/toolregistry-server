# App

`App` 是编排入口：构建 `ToolRegistry`、包装为 `RouteTable`，再分派给 OpenAPI 或 MCP 适配器。`serve_gateway()` 构建 `GatewayAdapter`，在同一个 ASGI 应用中提供 OpenAPI、两种 MCP HTTP 传输，并在启用时托管管理面板；`create_gateway()` 只构建适配器、不启动监听，便于进程内管理扩展调用 `list_frontends()` 与 `update_frontend()`。

传入 `admin_enabled=True` 与 `admin_token=`（或 `admin_token_file=`）即可托管管理面板。`keys_db=` 提供面板的密钥存储；加载配置文件时会自动接入运行期来源管理器。面板在第一个管理请求到来时启动，core 扩展契约不满足时启动失败，并由 `GatewayAdapter.shutdown()` 停止。运维令牌与 gateway 的 Bearer `tokens` 相互独立，对外令牌永远不能通过面板鉴权。继承并重写 `prepare_registry()` 可提供自定义注册表；参见[扩展应用](../../guides/extending.md)。

::: toolregistry_server.app.App
