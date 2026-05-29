# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，并在需要时保留项目自身的分类方式。

## [0.3.2] - 2026-05-28

### 变更

- `toolregistry` 最低版本要求提升至 `>=0.11.0`，下游包可依赖最新的 `ToolConfig` 元数据字段。
- 将 complexipy pre-commit 钩子切换为官方 `complexipy-pre-commit`，并使用明确的文件过滤。

## [0.3.1] - 2026-05-19

### 新增

- 路由列表现在会过滤 deferred 工具，与服务端注册表的渐进式披露行为保持一致。
- 部署 profile 过滤现在支持通过配置文件覆盖。

### 变更

- `toolregistry` 最低版本要求提升至 `>=0.10.2`。

## [0.3.0] - 2026-05-18

### 新增

- **`--profile` CLI 标志**：基于部署场景的标签过滤。`--profile remote` 通过 `ToolRegistry.disable_by_tags()` 禁用带 `file_system`、`destructive` 或 `privileged` 标签的工具；`--profile local` 不做任何过滤。`profile` 参数同样在 `run_openapi_server()` 和 `run_mcp_server()` 中以编程方式暴露（[#30](https://github.com/Oaklight/toolregistry-server/issues/30)）。
- **`create_registry_from_config()` 支持 post-register hook**：函数新增可选参数 `post_register_hooks: list[PostRegisterHook] | None`。hooks 在任何 source 加载前注册到 `ToolRegistry`；hook 返回非空字符串时自动 disable 对应工具（[#31](https://github.com/Oaklight/toolregistry-server/issues/31)）。
- **`apply_profile(registry, profile)`**：新增公开辅助函数，可对任意 `ToolRegistry` 应用命名的 profile 过滤。profile→tag 映射通过 `PROFILE_DISABLE_TAGS` 字典管理，便于扩展。

### 变更

- `toolregistry` 最低版本要求提升至 `>=0.10.1`，以获取 `disable_by_tags()` 和 `add_post_register_hook()`。

## [0.2.2] - 2026-05-18

### 变更

- **`run_openapi_server()` 和 `run_mcp_server()` 支持传入预构建的 registry**：两个函数新增可选参数 `registry: ToolRegistry | None`。当提供该参数时，`config_path` 将被忽略，`create_registry_from_config` 不会被调用，下游包可直接注入自定义 registry 而无需重新实现完整的启动流程（[#28](https://github.com/Oaklight/toolregistry-server/issues/28)）。

## [0.2.1] - 2026-05-10

### 变更

- 将零依赖 vendored 模块重组至 `_vendor/` 子包，结构更清晰。
- 将 `loguru` 替换为轻量级 vendored structlog shim，消除外部日志依赖。
- 将 `complexipy` 切换为本地 pre-commit 钩子。

### 修复

- 将 `_vendor/` 目录从 `ruff` 检查中排除，避免对 vendored 代码产生误报。

## [0.2.0] - 2026-05-06

### 新增

- **统一配置系统**：`run_openapi_server()` 和 `run_mcp_server()` 现支持通过 `--config` 参数从 JSONC/YAML 配置文件加载工具，支持 Python 类/模块、MCP 服务器和 OpenAPI 端点三种来源，以及拒绝列表/允许列表过滤（[#24](https://github.com/Oaklight/toolregistry-server/issues/24)）。

### 变更

- 适配 `toolregistry >= 0.9.1` API 变更（废弃的 `toolregistry.openapi` 导入路径已迁移至 `toolregistry.integrations.openapi`）。

## [0.1.3] - 2026-04-15

### 新增

- **MCP 会话上下文透传**：工具处理函数现可接收 MCP 会话上下文对象，支持有状态的工具实现。

### 修复

- 在 OpenAPI 适配器将输入传递给处理函数前，剔除框架注入的字段（如 `__mcp_session__`），避免意外的关键字参数错误。
- 将 `python-dotenv` 替换为零依赖的 vendored dotenv 加载器，消除外部依赖。
- 现要求 `toolregistry >= 0.6.1` 以获取 kwargs JSON Schema 修复。

## [0.1.2] - 2026-03-22

### 新增

- **MCP 适配器参数校验**：`RouteEntry` 现在携带来自 `Tool` 的 `parameters_model`，在调用处理程序前通过 Pydantic 进行类型强转（例如字符串 `"8"` → `int(8)`）。修复了与将所有参数序列化为字符串的 MCP 客户端（如 Codex）的兼容性问题。

### 变更

- MCP `call_tool` 处理程序现使用 `validate_input=False` 绕过 MCP SDK 的严格 JSON Schema 校验，将类型验证委托给 Pydantic 更宽松的类型强转机制。

## [0.1.1] - 2026-03-18

### 修复

- **RouteTable 与 ToolRegistry 同步**：RouteTable 现在能正确同步 ToolRegistry 的外部状态变更（例如在适配器层之外添加/移除/切换工具）。
- **命名空间级别的启用/禁用**：修复了 RouteTable 同步中命名空间级别启用/禁用操作的处理，确保批量切换正确传播到命名空间内的所有工具。

### 变更

- 要求 `toolregistry >= 0.6.0` 以支持 RouteTable 同步所使用的 `on_change` 回调。

## [0.1.0] - 2026-03-14

`toolregistry-server` 作为独立包的首次发布，从 [toolregistry-hub](https://github.com/Oaklight/toolregistry-hub) 中分离。

### 新增

- **中央路由表**（`RouteTable`、`RouteEntry`）：统一的路由层，桥接 `ToolRegistry` 和协议适配器，支持 ETag 版本控制、观察者模式和动态启用/禁用（[#2](https://github.com/Oaklight/toolregistry-server/issues/2)、[PR #7](https://github.com/Oaklight/toolregistry-server/pull/7)）
- **OpenAPI 适配器**：基于 FastAPI 的 REST API 适配器，自动从 JSON Schema 生成 Pydantic 模型、动态 OpenAPI 模式、按命名空间分组工具（[#3](https://github.com/Oaklight/toolregistry-server/issues/3)、[PR #8](https://github.com/Oaklight/toolregistry-server/pull/8)）
- **MCP 适配器**：模型上下文协议适配器，包含 `list_tools`/`call_tool` 处理程序，支持 stdio、SSE 和可流式 HTTP 传输（[#4](https://github.com/Oaklight/toolregistry-server/issues/4)、[PR #9](https://github.com/Oaklight/toolregistry-server/pull/9)）
- **ETag 缓存**：为 `/tools` 和 `/openapi.json` 端点提供 HTTP 缓存中间件，支持 `If-None-Match` 条件请求（[#5](https://github.com/Oaklight/toolregistry-server/issues/5)、[PR #10](https://github.com/Oaklight/toolregistry-server/pull/10)）
- **命令行工具**：包含 `openapi` 和 `mcp` 子命令的 CLI，支持 JSON/JSONC 配置、可自定义的启动横幅（[#6](https://github.com/Oaklight/toolregistry-server/issues/6)、[PR #11](https://github.com/Oaklight/toolregistry-server/pull/11)、[PR #13](https://github.com/Oaklight/toolregistry-server/pull/13)）
- **认证**：Bearer 令牌认证模块，支持多令牌、运行时令牌管理和动态启用/禁用
- **.env 文件加载**：支持从 `.env` 文件加载环境变量，提供 `--env-file` 和 `--no-env` CLI 选项（[PR #14](https://github.com/Oaklight/toolregistry-server/pull/14)）

### 修复

- MCP 可流式 HTTP 和 SSE 传输问题（[PR #12](https://github.com/Oaklight/toolregistry-server/pull/12)）
- 禁用工具在横幅显示和运行时的处理（[PR #13](https://github.com/Oaklight/toolregistry-server/pull/13)）
- 配置解析中的拒绝列表/允许列表模式支持（[PR #15](https://github.com/Oaklight/toolregistry-server/pull/15)）
