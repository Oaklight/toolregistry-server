# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，并在需要时保留项目自身的分类方式。

## [未发布]

### 新增

- **MCP 工具注解与标题**（#280）：`tools/list` 现在会带上 `Tool.metadata.title` 对应的 `title`，以及来自 `Tool.execution_hints` 的 `annotations`（`readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint`）。未声明的提示保持缺省，因此没有做出任何声明的工具仍保持最小的 `{name, description, inputSchema}` 定义。MCP SDK v1（camelCase）与 v2（snake_case）的 Python 字段拼写不同，但两者序列化后都是规范中的 camelCase JSON 键。
- **OpenAPI 操作扩展与路由访问器**（#280）：生成的 operation 会带上 `x-read-only`、`x-destructive`、`x-idempotent`、`x-open-world` 与 `x-title`，与 MCP 的 `annotations` 保持一致；未声明的提示不会输出。`RouteEntry` 新增 `title` 与 `execution_hints` 属性，使两个适配器读取同一份元数据，而不再直接访问 `route.tool`。
- **MCP 子命令新增 `--server-url`**（#78）：用于设置 OAuth protected-resource 元数据中的公网 URL。此前只能通过 `adapter.run(server_url=...)` 传入，因此用 CLI 部署在反向代理之后的实例无法声明一个客户端可访问的源，只能退回监听地址。此外，当即将声明的 URL 来自通配监听地址（`0.0.0.0`、`::`）时，服务器会在启动时输出警告。
- **MCP 子命令新增 `--tokens`**（#99）：适配器本来就接受 `tokens_path`，文档也写明了该参数，但只有 `openapi` 子命令定义了它，导致 `mcp --transport http --tokens tokens.txt` 在 argparse 阶段直接失败。`API_BEARER_TOKEN` 环境变量的用法保持不变。
- **MCP `serverInfo` 带上版本号与使用说明**（#79）：`initialize` 返回的 `serverInfo.version` 一直是空的，因为适配器从未向 SDK 传入版本。现在会填入当前安装的 `toolregistry-server` 版本，嵌入方也可以传入自己的版本——`App` 与 CLI 会转发 `ServerIdentity.version`，因此 Hub 声明的是 Hub 的版本而非 server 库的版本。当服务提供 `discover_tools` 时，响应还会带上 `instructions`，说明「先 `discover_tools` 再 `call_deferred`」的两步延迟工具流程——此前客户端只能从工具描述中自行推断。
- **MCP SDK v2 上开始提供 `subscriptions/listen`**（#107）：2026-07-28 协议没有常驻通知通道，SDK 会丢弃在连接上发送的 `notifications/tools/list_changed`，因此 modern era 客户端永远不知道工具列表发生了变化。适配器现在注册 SDK 的监听 handler 并挂接进程内总线，每次路由表变更都会发布 tools-list-changed 事件，打开监听流（SEP-2575）的客户端即可收到并重新拉取 `tools/list`。旧版协议客户端仍走原有的 session 通知路径；没有监听钩子的 SDK 版本不受影响。

### 变更

- **`tools/list` 默认缓存五分钟**（#114，**行为变更**）：服务器已提供 `subscriptions/listen`（#110），监听变更的客户端会在每次变更时使缓存失效，因此 `ttlMs` 默认值改为规范示例中的 `300000`，而不再是 `0`。不监听变更的客户端会在最多五分钟内使用缓存列表；`--tools-list-ttl-ms 0` 可恢复为每次重新获取。`cacheScope` 仍为 `private`。
- **`tools/list` 缓存提示可配置**（#83）：新增的 `--tools-list-ttl-ms` 与 `--tools-list-cache-scope` 用于设置 `tools/list`（SDK v2）的 `ttlMs` / `cacheScope` 提示，此前只能通过适配器参数传入且从未有人传过。默认仍为 `ttlMs: 0`（「永不缓存」），直到 `notifications/tools/list_changed` 在 SDK v2 上得到验证（#107）——在失效通知不可用时启用缓存，会让客户端在缓存期内一直显示已不存在的工具。`cacheScope` 默认为 `private`，因为实际提供的工具集合取决于 profile 与授权。
- **未知或已禁用的工具属于协议错误**（#81，对 MCP 客户端而言是**行为变更**）：当 `tools/call` 调用的工具不存在或当前处于禁用状态时，现在返回 JSON-RPC 错误 `-32602`（Invalid params），而不再是带 `isError: true` 的 `CallToolResult`。此时请求本身无效，宿主不应把它当作工具结果回传给模型。工具执行失败仍返回 `isError: true`，以便模型读取错误信息并调整做法。该行为在 MCP SDK v1 与 v2 上一致。

### 修复

- **一次 `tools/call` 只解析一次路由**（#97）：此前路由被解析两次——一次在 SDK v1 的预分发钩子中，一次在 handler 中。若两次解析之间工具被禁用，两代 SDK 会对同一请求给出不同答案：v1 返回 `isError` 结果，v2 返回 `-32602` 协议错误。现在钩子解析出的路由会传递给 handler。
- **工具列表变更通知改为跟踪连接而非单次请求**（#108）：在 MCP SDK v2 上，每个请求都会得到一个新的 `ServerSession`，因此 `list_tools` 登记的 session 在任何变更事件发生前就已被回收，跟踪集合始终为空。现在记录的是底层连接，其生命周期与客户端一致。面向 modern era 连接的推送仍需要回传通道（#107），所以这是前置修复而非完整修复。
- **只跟踪真正能收到变更事件的连接**（#111）：自 #108 起 `tools/list` handler 跟踪 `session._connection`，但在 2026-07-28 路径上该对象是每个请求新建的，且没有变更通知通道，因此每次 `tools/list` 都会留下一条永远不会释放的死记录。现在会跳过 modern era 连接（单次交换 HTTP 与 modern duplex 均是如此）；这些客户端由 `subscriptions/listen` 服务（#110）。握手时代客户端仍会被跟踪。
- **事件循环之外的路由变更不再遗留未等待的协程**（#117）：通知调度器在检查是否存在运行中的事件循环之前就创建了协程，因此在同步代码中构建服务器或修改路由表会触发 `RuntimeWarning: coroutine ... was never awaited`。现在只有存在可运行的事件循环时才会创建协程。
- **`initialize` 不再宣传已禁用的发现工具链**（#112、#113）：只要路由表中存在 `discover_tools` 就会附带默认 `instructions`，即使某个 profile 已将其禁用，客户端因此被告知去调用一个会返回 `-32602` 的工具。现在要求 `discover_tools` 与 `call_deferred` 均处于启用状态。
- **源级 `include`/`exclude` 过滤会下传**（#88）：配置加载器会解析这两个字段，但 `register_mcp_source()` 与 `register_openapi_source()` 从未把它们传下去，导致配置了过滤的远程源仍会注册全部工具。现在原样下传（显式空元组仍表示「什么都不匹配」）。生效前提是所安装的 `toolregistry` 提供这些字段；在旧版 core 上服务器行为保持不变。
- **MCP extra 的下限提高到 `mcp>=1.22.0`**（#95）：低于 1.22 的 SDK 版本会导入一个 pydantic 2.12+ 已移除的内部符号（`eval_type_backport`），因此依赖解析落在 1.17–1.21 时，安装出来的包在执行 `import mcp.server.lowlevel` 时会直接失败。1.21 失败、1.22 可导入，因此下限取在该边界上。`test-mcp-v1` CI 任务现在安装声明的下限版本，并打印实际测试到的版本。
- **启动横幅不再污染 stdio 数据流**（#80）：`print_banner()` 原先写入 stdout，而在 MCP stdio 传输下 stdout 就是 JSON-RPC 通道——客户端会在消息之间读到横幅字符画，有时甚至出现在首批响应之后。现在横幅改写到 stderr，与日志同流，并且 `print_banner()` 新增可选的 `file` 参数。`--no-banner` 仍可完全关闭横幅。Hub 调用的是同一辅助函数，因此无需改动 Hub 代码即可修复——但要等它安装的 `toolregistry-server` 升到包含该修复的版本之后才会生效。

### 安全

- **禁用原因不再泄露部署 profile**（#84）：`apply_profile()` 原先把 `Disabled by profile 'remote'` 作为工具的禁用原因存储，而该字符串会经由 MCP 的 `tools/call` 错误以及 core 抛出的 `RuntimeError` 传到调用方。现在存储的原因是中性的 `"not available on this server"`，profile 名称只在服务端日志中记录。

## [0.6.0] - 2026-09-15

### 新增

- **`call_deferred` 集成**：`toolregistry` core 的 `call_deferred` 代理工具可通过 MCP 和 OpenAPI 适配器透明暴露（需启用工具发现）。MCP 客户端可在通过 `discover_tools` 发现 schema 后调用延迟工具。
- **OpenAPI 适配器支持 `additionalProperties`**：当 JSON Schema 声明 `additionalProperties: true` 时，`_schema_to_pydantic` 生成带 `extra="allow"` 的 Pydantic 模型，支持 `call_deferred` 等接受 `**kwargs` 的代理工具。
- **下划线前缀字段处理**：`_schema_to_pydantic` 通过 Pydantic 别名映射 `_` 前缀字段名（Pydantic 禁止前导下划线）。别名冲突检测支持双向检查，冲突时抛出 `ValueError`。
- **`CompatProxy` MCP SDK 兼容层**：透明包装器，自动解析 MCP SDK 对象上的 snake_case 和 camelCase 属性名。`create_test_client` 自动包装结果，测试代码在 MCP SDK v1（camelCase）和 v2（snake_case）间无缝运行。
- **MCP 和 OpenAPI 源注册测试**：为 `registry_builder` 的 `register_mcp_source` 和 `register_openapi_source` 增加测试覆盖。
- **Schema 指纹和通知**：`/tools` 端点暴露 `schema_hash` 和 `last_refreshed_at`。路由表变更时向已连接的 MCP 会话推送 `tools/list_changed` 通知。
- **测试发布工作流**：新增向 Test PyPI 发布开发版本的 CI 工作流。

### 变更

- **冻结数据类兼容** （对直接修改 `Tool`/`ToolMetadata` 的下游代码为**破坏性变更**）：`_apply_ns_tags` 使用 `_replace_tool_metadata()` 替代直接属性赋值。`RouteEntry` 直接引用冻结的 `Tool` 而非复制字段，`parameters_schema` 作为 `cached_property`。
- **两层 Schema 模型注释更新**：MCP 和 OpenAPI 适配器中的 `toolcall_reason` 剥离注释已更新，反映 `tool.parameters` 现在是干净 schema，`get_schema()` 按需注入 `toolcall_reason`。
- OpenAPI 端点使用 `model_dump(by_alias=True)` 保留原始 JSON 键名。
- `toolregistry` 最低版本要求提升至 `>=0.18.0`。

### 修复

- SSE 队列溢出改为 debug 级别日志，不再静默丢弃。
- 修复所有测试中 MCP SDK snake_case/camelCase 字段访问兼容性。

## [0.5.0] - 2026-08-25

### 新增

- **原生多模态内容支持**（#58、#59）：MCP 适配器现在将内容块列表转换为原生 MCP 类型，而非 JSON 转储为文本。返回图片、音频、资源链接或嵌入资源的工具现以实际的 `ImageContent`、`AudioContent`、`ResourceLink` 和 `EmbeddedResource` 类型传递。
- **`outputSchema` / `structuredContent`**（#61、#65）：工具可通过 `metadata.extra['output_schema']` 声明输出 schema。schema 在 `tools/list` 中作为 `outputSchema` 发布，工具结果在文本内容块之外以 `structuredContent` 返回——按 MCP spec 2026-07-28 支持客户端验证。
- **`tools/list` 缓存提示**（#62、#64）：`route_table_to_mcp_server()` 和 `MCPAdapter` 接受 `list_tools_ttl_ms` 和 `list_tools_cache_scope` 参数。客户端可缓存工具目录以减少重复获取，提高 LLM prompt 缓存命中率。
- **音频、resource_link 和嵌入资源内容类型**（#59）：完整的 MCP 2026-07-28 内容类型覆盖——全部五种类型（`text`、`image`、`audio`、`resource_link`、`resource`）均原生支持。未知的未来类型优雅降级为 JSON `TextContent` 并附带警告。

### 变更

- `CallToolHandler` 返回类型使用 `mcp.types.ContentBlock` 联合类型替代 `list[Any]`。
- MIME 类型提取通过 `_get_mime_type()` 辅助函数在所有内容块类型间统一。
- `toolregistry` 最低版本要求提升至 `>=0.16.0`。

### 修复

- 对多模态内容块结果跳过 `structuredContent` 以避免 spec 违规。
- 将未知内容块类型降级为 `TextContent` 而非静默丢弃。
- 对齐返回类型注解与 `MCPContentBlock` 类型别名。

## [0.4.3] - 2026-08-06

### 新增

- **MCP SDK v2 兼容**（#56）：服务端适配器同时支持 MCP SDK v1 (1.x) 和 v2 (2.x)。依赖 pin 放宽至 `mcp>=1.17,<3`。新增 `_compat.py` 兼容层透明处理 API 差异。

### 变更

- `toolregistry` 最低版本要求提升至 `>=0.15.0`。

## [0.4.2] - 2026-07-16

### 修复

- **将 `headers` 从 MCPSource 配置传递至 `register_from_mcp`**：`MCPSource.headers` 被配置加载器解析但从未转发至 `register_from_mcp()`，导致配置文件中的 MCP 服务器 header 认证无效。
- **`_FunctionToolWrapper` 中 await 协程**：修复工具 wrapper 在 OpenAPI/MCP 端点中返回协程而非普通值时的 session 注入崩溃。

### 变更

- `toolregistry` 最低版本要求提升至 `>=0.14.0`。

## [0.4.1] - 2026-06-26

### 修复

- 捕获 OpenAPI 端点中的工具异常，返回结构化 HTTP 500 而非未处理错误（#49）。
- 在 CI lint 中安装完整依赖，移除过时的 `ty: ignore` 注释。

### 变更

- 固定开发工具版本：ruff==0.15.20、ty==0.0.54、complexipy==5.6.1。

## [0.4.0] - 2026-06-22

### 新增

- **`ServerIdentity` + `CLI` 类**：下游包现可通过子类化 `CLI` 构建完全自定义的命令行入口，无需重新实现参数解析逻辑（[#39](https://github.com/Oaklight/toolregistry-server/pull/39)）。
- **`configure_subparsers()` 钩子**：`CLI.create_parser()` 在构建 `openapi`/`mcp` 子命令后调用 `configure_subparsers(subparsers)`，为子类提供干净的扩展点，无需访问 argparse 私有 API（[#47](https://github.com/Oaklight/toolregistry-server/pull/47)）。
- **`run_cli()` 可复用主循环**：独立的 `run_cli()` 辅助函数封装了完整的解析-分发生命周期，下游可直接嵌入 CLI 循环而无需子类化。
- **CLI 参数构建器作为公开 API**：`OpenAPIAdapter.add_cli_arguments()` 和 `MCPAdapter.add_cli_arguments()` 现已纳入公开接口，下游 CLI 可直接复用标准参数集。
- **Streamable HTTP 传输的 Bearer 令牌认证**：MCP 适配器现对 `streamable-http` 传输强制执行 Bearer 令牌认证，与既有的 SSE 认证行为保持一致。

### 变更

- **`App` 类 + `Adapter.create_and_run()`**：`App` 类现为编程使用的标准入口；`Adapter.create_and_run()` 提供单次调用的通用分发路径（[#42](https://github.com/Oaklight/toolregistry-server/pull/42)）。
- **默认 `App` 延迟初始化**：模块级 `serve_openapi` / `serve_mcp` 便捷函数现在首次调用时才创建默认 `App` 实例，避免仅使用 CLI 路径时产生冗余实例（[#47](https://github.com/Oaklight/toolregistry-server/pull/47)）。
- **适配器与 CLI 内部重构**：Banner 逻辑已提取，CLI 参数移入各适配器，`cli`/`auth` 模块结构扁平化，包布局更整洁（[#42](https://github.com/Oaklight/toolregistry-server/pull/42)）。

### 修复

- 对令牌集合进行显式的 list-to-set 转换，避免有序可迭代对象静默去重导致的问题。

## [0.3.3] - 2026-05-31

### 修复

- 在 `RouteTable` 边界规范化路由参数 schema，使所有服务适配器都收到标准 object schema。
- 确保 MCP `tools/list` 响应始终输出 object 形态的 `inputSchema`。
- 增强 OpenAPI 请求模型生成逻辑，兼容空 schema 或非 object 参数 schema。
- 将 `toolregistry` 最低版本要求提升至 `>=0.11.1`，以获得 core 包中的 schema 生成修复。

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
