# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，并在需要时保留项目自身的分类方式。

## [未发布]

### 新增

- **常驻 hub 可以由启动器拉起**：`local-hub ensure` 与 stdio shim 在 `TOOLREGISTRY_LOCAL_HUB_COMMAND` 包含非空白文本时，按 shell 命令拆分并用它替代 `toolregistry-server` 拉起 hub。Hub 部署把它指向 `toolregistry-hub`，按需启动的 hub 因此保留 Hub 启动器的注册表策略（预设默认值、configurable 钩子、管理面板）。未设置或为空白时沿用原有查找顺序：`PATH` 上的 `toolregistry-server`，然后是当前解释器执行包内引导代码。被拉起的 hub 会继承该变量；只有 shim 的密钥会从环境中移除。

## [0.9.0] - 2026-11-09

### 新增

- **每次对外调用都连同客户端身份被记录**（#224）：配置 `--keys-db` 的部署同时开启注册表执行日志，每个到达处理器的调用——MCP，以及两个后端上的 OpenAPI——写入一条记录，包含结果或错误，并用 `metadata={"key_id": ..., "client_id": ...}` 标明调用方。被密钥作用域、代理目标检查或进程策略拒绝的调用从未到达处理器，不会进入日志；MCP 的拒绝仍会写一条带归属的结构化日志，而 OpenAPI 的拒绝不在服务端留下记录。`--execution-log` / `--no-execution-log` 可在 `gateway`、`mcp` 与 `openapi` 上覆盖默认值。日志是每进程 1000 条的内存环，由管理面板 Logs 页读取；除非此前已以 `full_record` 开启，条目遵循 core 的脱敏规则。

- **gateway 管理前端**：`gateway` 可在可配置路径（默认 `/admin`，默认禁用）托管 core 管理面板。面板由回环地址上的 `AdminServer` 通过唯一对外监听器反向代理提供，使用 core 面板自己的运维令牌/会话而不是 gateway 的 Bearer 令牌，并用实时密钥存储、前端注册表与来源管理器加载 gateway 管理扩展。新增选项：`--admin`、`--admin-path`、`--admin-token`、`--admin-token-file` 与 `--admin-allow-private-hosts`。缺少运维令牌或扩展契约冲突会导致启动失败。
- **运行期工具来源**（#185）：`SourceManager` 把 Hub 的热重载 diff 移入服务器。`add`、`remove`、`replace`、`retry` 与 `list` 无需重启即可变更对外提供的工具集合，并为每个来源保留 `applied` / `excluded` / `failed` 状态、脱敏后的最近错误以及稳定的 `src_<16 hex>` id（`to_dicts()` 返回可直接序列化的列表）。由 API 发起的变更会在复制 `<path>.bak` 后重写配置文件，并报告 `persisted` / `persist_error`；JSONC 注释不会被保留，只读路径会被报告而不是静默忽略，`--no-config-write` 让变更只保留在内存中。`--config-reload` 的 watcher 成为同一管理器的薄驱动，并忽略管理器自身的写入。diff 注册的工具会应用部署配置过滤；一次 diff 只触发一次路由重建：`RouteTable.batch_changes()` 合并逐个事件的 `refresh_all`，`_rebuild` 以一次赋值安装新建的映射，而不是清空正在使用的映射。
- **gateway 模式在同一进程提供 OpenAPI 与 MCP**（#184）：`toolregistry-server gateway` 构建一个 ASGI 应用，默认把 OpenAPI 挂载在 `/openapi`、MCP streamable-HTTP 挂载在 `/mcp`、MCP SSE 挂载在 `/sse`，路径均可配置。每个前端的启用标记按请求读取，因此禁用后的前端立即返回 404，不会触达注册表，也不会重建 MCP session manager；修改路径也会在下一个请求生效。`--tokens` 的 Bearer 令牌保护所有前端，`/.well-known/oauth-protected-resource<MCP 路径>` 按当前 MCP 路径生成。前端路径与启用标记保存在配置文件的 `gateway.frontends` 下；`GatewayAdapter.update_frontend()` 在复制 `<path>.bak` 后写回配置，并报告 `persisted` / `warning`，`--no-config-write` 只改内存。多个前端必须使用 ASGI（`fastapi`）后端：启用第二个前端时 `--backend httpserver` 会被拒绝，且该后端只能提供 openapi 前端。streamable-HTTP 的 session manager 在 lifespan 启动时创建、关闭时释放。`GatewayAdapter.list_frontends()` 与 `update_frontend()` 是供管理扩展在进程内调用的 API。
- **可运行的 gateway 演示**（#188）：`examples/gateway-demo/` 在同一进程、同一来源上提供 `/openapi`、`/mcp` 与小型 `/ext/gateway` 管理面；客户端密钥保存在静态 JSON 文件中（只存 SHA-256 摘要，仅在创建时返回一次，可撤销），前端可关闭并返回 404。安装了 `toolregistry-hub` 时使用其工具，否则使用三个本地工具。`Dockerfile` 从仓库根目录构建镜像，`walkthrough.sh` 在 `Gateway demo` CI 工作流中对运行中的服务复跑 README 步骤。示例位于 `src` 之外，不进入 wheel 或 sdist。
- **限定作用域的入站 API 密钥**（#186）：`openapi` 与 `mcp` 新增可选的 `--keys-db PATH`，打开基于标准库 SQLite 的密钥存储。密钥包含标签、作用域（命名空间允许列表加单工具允许/拒绝覆盖）、可选过期时间、吊销时间和 `last_used_at`。密钥只由 `keys create` 打印一次，存储中只保留带每密钥独立盐的 scrypt 哈希，因此记录、列表和数据库转储都不包含明文。同一个 principal 解析器在四个入口执行作用域——MCP `tools/list`、MCP `tools/call`、`/openapi.json` 和 OpenAPI 工具路由——过滤 `RouteTable` 视图，并拒绝作用域外的调用（OpenAPI 返回 `403`，MCP 返回 `INVALID_PARAMS`）。每次请求都会读取密钥行，因此吊销和过期在下一个请求生效。`toolregistry-server keys create|list|get|revoke` 是同一 Python API（`ApiKeyStore`、`PrincipalResolver`）的无界面封装，供管理扩展使用；`list` 分页并按最近使用时间排序。静态 `--tokens` 令牌保持完整、不受限的访问权限，属于引导/兼容路径；不传 `--keys-db` 时行为不变，MCP `stdio` 传输会忽略该参数。启用密钥后，`/tools` 与 `/openapi.json` 的 ETag 按凭据区分，并带有 `Cache-Control: private` 与 `Vary: Authorization`。限定作用域的密钥保留 `discover_tools` / `call_deferred` 代理工具对，且两者都执行密钥作用域（详见下方 #195）。
- **gateway 前端支持限定作用域的密钥**（#200）：`toolregistry-server gateway` 接受 `--keys-db PATH`。每个进程只构建一个 `PrincipalResolver`，并传给 OpenAPI 应用、streamable-HTTP 服务器和 SSE 应用，因此 MCP `tools/list`、MCP `tools/call`、OpenAPI 文档和 OpenAPI 工具路由在每个前端都执行密钥作用域，httpserver 后端同样适用。静态 `--tokens` 令牌保持完整、不受限的访问权限；只要配置了任一凭据来源，就会提供 RFC 9728 元数据路由；不传 `--keys-db` 时行为不变，网关关闭时会关闭密钥存储。
- **限定作用域的密钥保留发现代理，且两者都执行作用域**（#195）：`discover_tools` 与 `call_deferred` 不再对限定作用域的密钥移除。发现结果只保留密钥可调用的名称——MCP、FastAPI OpenAPI 应用与 httpserver 后端皆如此——`call_deferred` 在派发前校验 `_target_tool`，作用域外的目标会得到与直接调用完全相同的信息并被拒绝。目标在实时注册表中解析（先精确名，再按注册表顺序取第一个归一化匹配），因为那正是 core 实际调用的对象，因此在配置 diff 推迟路由重建期间注册的工具同样会被判定。无论密钥的命名空间是什么，这对代理都可见——它们注册在 default 命名空间并自行执行目标的作用域——而针对任一名称的显式单工具覆盖仍然生效，声明拒绝的密钥既看不到也无法调用。

- **OpenAPI 源可就地刷新规范文档**（#182）：`SourceManager.refresh(ref)` 会带着 ETag 重新拉取来源保存的 `spec_url`，并把文档交给 core 的 `OpenAPIIntegration.refresh()`。`304` 不做任何改动——不更新 `last_refreshed_at`，不重建路由。文档变化时会对新增、移除与更新的工具做 diff，保持手动禁用的工具仍被禁用，并重新应用命名空间标签、部署 profile 与 `tool_metadata`；spec 正则无法编译的工具会被丢弃并在 diff 中报告。当保存的 base URL 本就来自文档时，移动后的 `servers[0]` 会被采纳，且只有该候选地址接受出站地址策略检查。每次拉取都在 I/O 前重新校验规范 URL；失败时注册表与上一份有效文档保持不变。通过 `POST /ext/gateway/sources/<id>/refresh` 与 OpenAPI 源行上的 **刷新规范** 动作暴露。见[OpenAPI API](guides/openapi.md)。

- **httpserver 版 OpenAPI 应用补齐 FastAPI 组装中剩余的表面**（epic #101）：`/redoc` 从 CDN 提供 ReDoc，版本固定并带 SRI 哈希，运维配置的标题像 `/docs` 一样经过 HTML 转义。应用构建之后注册的工具通过 `RouteTable` 变更监听器即时可调用，无需重启；被移除的工具保留其路由并返回 503，与 FastAPI 的快照行为一致。关闭时会等待进行中的请求完成并移除监听器。默认后端不变；移除 FastAPI 是下一阶段。见[配置](guides/configuration.md)。

- **配置中的按路由参数覆盖会真正生效**（#165）：顶层 `tool_parameters` 块按精确的已注册工具名索引，替换某条路由对外发布并据以校验的 schema，不修改已注册的工具本身。服务器在构建路由表时应用该块，并在注册表每次变化时再次应用：配置热重载会应用、修改或清除条目；source 重试成功后会为它带回的工具应用条目；OpenAPI 刷新会为它新增的工具应用条目。兼容性沿用既有守卫按工具自身的模型判断，因此比模型更严格的覆盖会记日志并跳过，更宽松的会记警告并应用，未注册工具的条目会记日志并跳过；单条条目不会让启动或重载失败。清除条目时即使路由已不存在也会丢弃存储的覆盖，同名工具之后不会再继承它。该块需要 `toolregistry` 0.21.0 或更高版本。见[配置](guides/configuration.md)。

- **网关可绑定 Unix 域套接字并发布 hub endpoint**（#145）：`gateway --socket PATH` 改为绑定 Unix 套接字而不是 TCP 地址（仅 POSIX，优先于 `--host`/`--port`）。两个后端都会在绑定前校验路径——过长 `sun_path`（Linux 107 字节、macOS 103 字节）、非 socket 文件与仍在监听的实例都会被拒绝，崩溃服务器遗留的 socket 会被清除，父目录以 0700 创建。uvicorn 后端在启动完成后立即把 socket 收紧为 0600，因为 uvicorn 自身会把它改成 0666。`toolregistry_server.gateway.endpoint` 定义了常驻 hub 将发布的文档 `<状态根>/hub/<name>.json`——名称、协议主版本、pid、版本、传输方式与地址、`started_at` 及前端路径——原子替换、位于 0700 目录中的 0600 文件，且仅在文件属主为调用者、无组/其他用户权限位、不是符号链接且写入进程仍存活时可读。状态根依次为 `$TOOLREGISTRY_HOME`、`$XDG_RUNTIME_DIR/toolregistry`、`~/.local/state/toolregistry`（Windows 为 `%LOCALAPPDATA%\toolregistry`）。

- **本机 MCP 客户端可共享一个常驻 hub**（#145）：`toolregistry-server local-hub [run|ensure|stop|status]` 把网关作为每机一个进程提供服务，默认绑定 `127.0.0.1` 的随机端口（或用 `--socket PATH`），并以 `--name` 命名（默认 `default`），因此不同名字的 hub 可共存。`run` 前台运行；`ensure` 复用已运行的 hub，或拉起 detached 进程并打印 endpoint；`stop` 向属主发送信号；`status` 报告状态并以 0 或 3 退出。所有权是 `hub/<name>.lock` 上的独占锁，锁文件永不删除，属主 pid 写在 `hub/<name>.pid`，因此陈旧文档不会让 `stop` 误杀无关进程：必须锁、pid 文件与 endpoint pid 三者一致，且公布的地址确实接受连接。文档与日志位于 0700 目录下的 0600 文件；endpoint 文档原子替换并携带协议主版本；读取方会拒绝非常规文件、符号链接、他人所有或组/其他用户可读的文件。`--idle-timeout`（默认 0，即常驻）在指定秒数内既无请求也无打开的 MCP/SSE 会话时退出。收到 SIGTERM 时 hub 会先清理 endpoint 与 pid 文件再退出。`local-hub` 目前仅支持 POSIX；Windows 上动作会被拒绝并提示改用 `gateway`。

- **shim 让单个 stdio 客户端在 hub 重启后仍可用**（#145）：`toolregistry-server hub-shim` 对客户端使用 stdio 的 MCP，并通过 streamable HTTP 转发到指定 hub。hub 不在线时 `initialize` 由本地应答、`tools/list` 为空，因此即使没有任何服务在运行客户端也能启动；调用会先失败一次并返回 MCP 错误 `-32000`，随后 shim 在后台重连（0.25 秒起步倍增到 5 秒，单次尝试受 `--connect-timeout` 限制，默认 5 秒）。重连后 shim 会比较 hub 的工具列表与客户端最近一次拿到的列表（名称、描述与输入 schema），仅在两者不同时发送 `notifications/tools/list_changed`。地址无人接受连接的文档按「没有 hub」处理，shim 会经 `local-hub ensure` 拉起一个（除非给出 `--no-spawn`）；拉起需要 `--config`，且每分钟最多一次。协议主版本不匹配会在开始服务前被拒绝。传输与会话由同一个长生命周期任务持有，因此 hub 死亡不会取消 stdio 服务器；Unix 套接字通过 SDK 自带的 HTTP 客户端访问。

- **共享 hub 上的客户端按密钥隔离**（#145、#223）：声称身份、工作目录或环境的请求头（`x-client-id`、`x-client-identity`、`x-client-cwd`、`x-client-env`、`x-client-environment`、`x-forwarded-user`、`x-remote-user`）会在认证之后以 `400` 拒绝，并连同调用方密钥记录日志，因此身份只能来自凭据。每次调用都按 principal 计数：`--client-concurrency`（默认 8）限制在途调用，`--concurrency-timeout`（默认 30 秒）限制等待时间；密钥上可空的 `max_concurrency`（用 `keys create --concurrency` 设置、`keys set-concurrency` 修改）只能收紧进程值；等待超过超时的调用返回 MCP `-32000`「too many concurrent calls」。每次调用都会记录 `key_id` 与 `client_id`，`/ext/gateway/clients`（面板的 Clients 页）按客户端报告实时计数。`hub-shim` 新增 `--key-file` / `TOOLREGISTRY_HUB_KEY` 与 `--keys-db`，用于拉起需要密钥的 hub。

- **独立 MCP 服务器拥有同样的按客户端配额**（#223 后续）：`mcp` 在 `sse` 与 `streamable-http` 上接受 `--client-concurrency`（默认 8）与 `--concurrency-timeout`（默认 30 秒），按密钥收紧的规则与 gateway 相同，繁忙时同样返回 `-32000`。`stdio` 会校验这两个值但不限流，因为它只服务一个客户端且没有按客户端的凭据。管理面板的 Keys 表单也接受 `max_concurrency`，与 `keys create --concurrency` 一致。

- **OAuth 2.0 资源服务器基础**（#234）：`openapi`、`mcp`（HTTP 传输）、`gateway` 与 `local-hub` 接受 `--oauth-issuer`、`--oauth-audience`、`--oauth-jwks` 以及可重复的 `--oauth-scope SCOPE=NAMESPACE[,NAMESPACE]`。由外部授权服务器签发的 Bearer 令牌在本地用该服务器的 JWKS 校验（仅 RS256/ES256、精确 `iss`、`aud`/RFC 8707 `resource`、`exp`/`nbf` 允许 60 秒偏差；JWKS 缓存 5 分钟，未知 `kid` 会强制重新拉取一次）。作用域映射显式且遵循最小权限：未映射的作用域不授予任何权限。先尝试 API 密钥和静态令牌，再尝试 OAuth，因此省略 `--oauth-issuer` 时行为不变。MCP 受保护资源元数据在 `authorization_servers` 中公布配置的 issuer；绑定 Unix socket 的 hub 必须显式提供 `--oauth-audience`。未实现令牌内省（RFC 7662）；`PyJWT`（含 `crypto`）成为 `gateway` extra 的新依赖。配置外部 issuer 后，受保护资源元数据不再公布 `scopes_supported`，因为授权服务器可能在authorize 阶段拒绝通用的 `mcp` 作用域：`--oauth-scopes "openid profile email offline_access"` 指定要公布的作用域，空值则省略该字段，未配置 OAuth 的服务器保持原来的 `["mcp"]`。

### 变更

- **独立 OpenAPI 服务器变为零依赖，FastAPI 移至 `gateway` extra**（epic #101）：`toolregistry-server openapi` 现在只运行在内联的 `httpserver` 上，因此 `pip install toolregistry-server` 即可提供完整的 OpenAPI 契约，无需任何第三方包。FastAPI 组装移至 gateway 私有的 `toolregistry_server.gateway.openapi_asgi`，只由 gateway 的 ASGI 后端使用；`create_openapi_app` 与 ETag/请求体上限中间件不再能从 `adapters.openapi` 导入。`pyproject.toml` 新增 `gateway = [fastapi, uvicorn]`，`openapi = []` 作为空别名保留；`all` 改为拉取 `[gateway]` 与 `[mcp]`。**破坏性变更**：`openapi` 命令移除 `--backend` 与 `--reload`（两者都只属于 uvicorn；gateway 保留 `--backend`），`OpenAPIAdapter(..., backend=...)` 被移除，`OpenAPIAdapter.app` 现在返回内联的 `httpserver.App` 而不是 ASGI 应用。未安装该 extra 时启动 gateway 会抛出指明 `toolregistry-server[gateway]` 的 `ImportError`。见[OpenAPI API](guides/openapi.md)与[安装](get-started/installation.md)。

- **需要密钥的部署在 MCP 上只接受客户端密钥**（#145、#223）：配置了 `--keys-db` 或在 `local-hub` 模式下，携带静态运维令牌的 MCP 请求会在整条路径上被拒绝（包括 `tools/list`），因为共享凭据无法标识客户端；`--allow-operator-token`（gateway、mcp 与 local-hub）可以显式放行。OpenAPI 与管理面板继续接受运维令牌。`local-hub` 现在要求 `--keys-db`，`hub-shim` 访问需要密钥的 hub 时必须提供密钥文件。

### 修复

- **启动来源现在也会触发 `on_registered`**（#203）：`SourceManager.apply_initial()` 会注册配置中的来源，但从不调用 `on_registered`，因此依赖该钩子做注册后处理的下游——例如 Hub 的 `output_schema` 处理——在启动时不会执行，工具要等到后续一次 diff 才获得这些元数据。现在启动过程会在注册循环结束后以本次注册的全部名称调用一次钩子，位置与一次 diff 相同：在 `tool_metadata` 与部署配置过滤之前。没有任何名称注册时（空配置或全部来源被排除）不调用；钩子抛异常时记录日志，工具保持注册。
- **同名替换会被复查非法 spec 正则，部分丢弃也可见**（#171）：加载时的检查比较的是工具名快照，因此当 source 用同名工具替换已存在的工具时，不会产生*新*名字，替换后的 schema 也就不会被编译——替换引入的坏 `pattern` 会留在注册表中，直到第一次调用才失败。现在改为比较工具对象：本次注册创建或替换的每个名字都会被检查，带非法正则的替换与新工具一样被丢弃。source 的汇总行会报告丢弃数量与工具名（例如 `Loaded OpenAPI tools from '…': dropped 2 tool(s) with an invalid regex: ['a', 'b']`），全部丢弃时的日志也带上数量与列表。`--strict-spec-regex` 与 hub 的 source 失败语义（只有全部丢弃才为 false）不变。
- **httpserver 后端上报真正提供服务的 OpenAPI 路径，注释丢失警告同时覆盖两种格式**（#196）：gateway 之前上报配置的 `/openapi`，但该后端实际上把 OpenAPI 服务在监听根（`/tools`、`/openapi.json`、`/docs`、`/events`）。现在 `list_frontends()` 返回 `path: "/"` 并用 `note` 列出这些路由，`update_frontend()` 返回同样的结构；提交 `"/"` 会被视为「无变更」，因此保存未改动的记录也能成功，其他路径仍会得到「运行期不可修改」的拒绝。持久化仍写入配置中的路径，文件可正常重新加载。`COMMENTS_WARNING` 现在写「注释不会被保留」，而不是只提 JSONC——对 YAML writer 而言旧措辞本就不准确。

- **托管面板按真实客户端计节流键**（#205）：gateway 之前原样转发入站请求头到回环面板，客户端自带的 `X-Forwarded-For` 会原样到达，所有请求看起来都来自 127.0.0.1。现在会丢弃 `Forwarded`、`X-Forwarded-For`、`X-Forwarded-Proto` 与 `X-Forwarded-Host`，改为声明 socket 对端、scheme 以及它看到的 `Host`。当面板绑定回环地址时，gateway 还会把该地址作为受信代理传给面板（`trusted_proxies`，core#431），于是面板的认证节流按转发来的客户端计键：单个客户端耗尽配额不再锁死所有面板用户，不可信对端的伪造请求头也无法换桶。非回环绑定不受信任何人；单租户假设——能连到面板端口的本机进程都是受信对端——记录在常量旁边。该受信代理接线在 `toolregistry` 0.21.0 上生效，在 0.20.1 下限上是无操作。
- **无变更的前端保存不再重写配置**（#209）：httpserver 后端把 openapi 前端上报为 `path: "/"`，而管理界面提交的正是它显示的那条记录，因此一次无变更保存会把路径映射回 `None`，重写文件（丢失注释）并报告 `COMMENTS_WARNING`。现在更新会比较前后记录，当路径与启用标记都没有变化时跳过持久化，返回 `persisted: false, warning: null`；真正的变更仍会写回并仍会报告警告。`path` 的文档字符串说明上报的 `"/"` 表示「不改变路径」。
- **进程权限策略在对外的两个前端上被绕过**（#145、#223）：MCP 与 OpenAPI 的调用路径都直接执行路由处理器，配置好的 `PermissionPolicy` 在那里从未被评估——只有进程内 API 遵守规则。现在两种后端都通过同一个助手解析策略：`ASK` 由配置的处理器应答，否则落到回退结果（默认拒绝）；`call_deferred` 既按自身、也按它将要调用的工具判定。未配置策略的注册表行为不变。
- **SDK v2 上限定作用域的 `tools/list` 返回空**（#145、#223）：v2 的列表处理器在请求上下文之外运行，principal 无法解析，因此作用域内的路由列表为空，而 `tools/call` 正常。处理器现在会在列出之前发布请求上下文。
- **不是 `PermissionPolicy` 的策略此前被静默忽略**（#223 后续）：core 的 `set_permission_policy` 接受任意对象，其进程内路径会调用 `evaluate`，而服务器只执行 `PermissionPolicy` 实例。现在这类鸭子类型策略会按类型各记一次警告，而不是毫无痕迹地被忽略；类型契约本身见 Oaklight/ToolRegistry#467。
- **shim 拉起的 hub 继承了客户端密钥**（#223 后续）：`TOOLREGISTRY_HUB_KEY` 曾被传给 shim 拉起的 hub，使 hub 启动的每个进程都带着它用不到的凭据。子进程环境现在会移除该变量。

## [0.8.0] - 2026-11-08

### 新增

- **来自 spec 的正则可在时间上限内执行**（#160）：新增可选的 `regex` extra（不属于 `all`），为请求校验提供 `pattern_matcher`，代理 OpenAPI 工具的 `pattern` 与 `patternProperties` 得以执行，默认单次 50 ms、每请求 250 ms。`patternProperties` 先对每个 pattern 调用一次（空对象也校验），再对每个 (pattern, key) 调用一次。超时返回 `500`，detail 为 `{"detail": "Tool '<name>' validation timed out"}`；正则无法编译时返回 `500` 与 `"validation failed"`，两者都不回显 pattern。未安装 extra 时行为不变：不执行 spec 正则，含 pattern 的层级继续放宽 `additionalProperties`。安装 extra 会收紧校验，此前因正则被忽略而通过的请求现在可能被拒绝。`--regex-timeout` / `--regex-budget` 配置上限（`0` 关闭执行）；`--strict-spec-regex` 让非法正则终止启动，而不是跳过该 source。
- **路由级覆盖：发布用的参数 schema 与执行提示**（#137）：`RouteTable.override_parameters(name, schema)` 与 `override_execution_hints(name, hints)` 可改变单个路由发布的内容，而不改动注册表中的 `Tool` 或其 `schema_hash`。`RouteEntry` 通过 `served_parameters_schema` / `served_execution_hints` 暴露结果，两个适配器都读取该视图，因此 MCP 的 `inputSchema` / `annotations` 与 OpenAPI 的请求 schema / `x-` 扩展保持一致。`{}` 会抑制所有注解/扩展；`None` 清除覆盖。覆盖在 `refresh()` 与 `refresh_all()` 后仍然保留，并会通知监听者，因此 ETag 与 `tools/list_changed` 能反映变化。这取代了包裹私有 `tools/list` handler 的做法。
- **OpenAPI 适配器新增零依赖 HTTP 后端**（#127、#128，epic #101）：`--backend httpserver`（或 `OpenAPIAdapter(..., backend="httpserver")`）在内联的 `httpserver` 上提供完全相同的线上契约——工具路由、带 ETag/`304` 的 `/tools` 与 `/openapi.json`、`/docs`，以及 SSE 的 `/events`——且无需安装 FastAPI/uvicorn。默认仍是 `fastapi`；`--reload` 在新后端上会明确提示不支持。返回值统一用 core 的 `ToolResultJSONEncoder` 序列化，dataclass、pydantic 模型、集合、日期时间与嵌套容器都与 FastAPI 路径产出相同的 JSON 形状；`bytes` 依旧按设计使用 base64。
- **两种后端的差分比对测试**（#129）：同时启动两个实现并通过真实 socket 发送同一批请求，断言状态码、`ETag` 与响应体字节完全一致，覆盖工具调用、错误结构、条件请求、404/405、禁用工具的 503 以及三种鉴权情形。

### 变更

- **core 下限提升到 0.20.1，兼容层已移除**（#159、#154）：`toolregistry>=0.20.1,<1.0.0`。`register_openapi_source()` 直接传入 `allow_local_files=True`，不再探测加载器签名；OpenAPI 后端直接使用 core 的 `ToolResultJSONEncoder`：非有限兜底编码器与能力探测已删除，工具结果中的 `NaN`、`Infinity` 与非有限 `Decimal` 在两个后端上都输出 `null`。最低 core 版本由安装依赖强制；早于 0.20.1 的 core 不再支持。0.20.1 是本轮补丁基线：包含 netpolicy 特殊网段修复（#425）与 discovery 再入修复（#428），因此 0.8.0 不支持 0.20.0。
- **配置中的 OpenAPI source 现在会遵循 `allow_private_hosts`**（#176）：配置加载器接受该键，但 `register_openapi_source()` 从未把它传下去，因此指向回环或私网地址的 spec／服务器 URL 仍会被拒绝。现在该标记同时传给 `load_openapi_spec()`（spec 拉取）和工具使用的 `HttpClientConfig`；客户端在请求前校验实际使用的 base URL（`servers[0]` 或配置的 `base_url`）。它只作用于该 source；不设置或为 `false` 时仍保持默认拒绝。
- **配置中的 MCP source 可以访问私网地址**（#168）：`register_mcp_source()` 此前没有传递 `MCPSource.allow_private_hosts`，因此配置里写了 `allow_private_hosts: true` 仍会被 core 的出站地址检查拒绝。现在该标记随注册调用一起传递。它只作用于该 source 的 HTTP、SSE 与 WebSocket 传输；不设置或设为 `false` 时仍保持默认拒绝。该拒绝信息会点名 `allow_private_hosts`，且不会打印解析出的地址，地址只保留在异常的 `addresses` 属性上。该字段需要 core 0.20.0，现已由安装下限强制。
- **非法 spec 正则现在只丢弃受影响的工具**（#171）：加载期会编译 OpenAPI source 工具 schema 中的每个 `pattern` 与 `patternProperties` 键。此前一条非法正则会移除该 source 的全部工具；现在只丢弃自身 schema 含该正则的工具，每个都记录错误日志（工具名、schema 路径、pattern），只有没有任何工具存活时才把 source 报为失败。`--strict-spec-regex` 保留整源失败语义并终止启动。加载期 walker 也不再把这些映射中**名为** `default`、`example`、`enum`、`const` 的属性或定义当作数据关键字，因此 `{"properties": {"default": {"pattern": "("}}}` 这类 schema 会被检出。
- **默认只发布显式声明的执行提示**（#136）：`READ_ONLY` / `DESTRUCTIVE` 标签可能是为本地权限策略选择的，发布解析后的映射会让该标签变成面向客户端的自动批准声明。`served_execution_hints` 现在优先使用 `Tool.declared_execution_hints`；只有 `RouteTable` 通过 `emit_derived_execution_hints=True` 或 `set_emit_derived_execution_hints(True)` 显式选择加入时，才会发布由标签推导的提示。显式声明的提示不受影响；由于两个适配器读取同一视图，MCP 与 OpenAPI 遵循相同策略。在尚未提供 `declared_execution_hints` 的 core 上，服务端会从 `ToolMetadata` 的提示字段重建相同映射（这些字段在 0.19.x 下限中已存在），因此无需等待 core 发布即可生效；更旧的 core 则不发布任何声明。
- **比工具模型更严格的参数覆盖会被拒绝**（core #384）：对 model-backed 工具，`override_parameters` 现在拒绝新增 `required`、收紧 `minimum`/`maximum`/`minLength`/`maxLength`、修改 `pattern`、收窄或改变 `enum`、或收窄或改变属性类型的覆盖，因为工具的校验模型不会执行更严格的契约。更宽松的覆盖会保存并发出 `UserWarning`。现有的更严格覆盖需要放宽或改用代理工具；代理工具按发布的 schema 校验，不受影响。拒绝由程序化的 `override_parameters` 调用抛出。`toolregistry-server` 自身不会从配置应用路由参数覆盖，因此升级不会仅因此导致启动失败；下游加载器若确实应用这些覆盖，应捕获 `ValueError`、记录工具名与原因、跳过该条覆盖，使服务仍能启动。
- **OpenAPI 请求校验不再依赖 pydantic 模型**（#123，epic #101）：`_schema_to_pydantic`、`_resolve_type` 以及 `__annotations__` 的修补全部移除。请求体现在改走两条路径：由函数签名生成的工具直接调用 `Tool.validate_parameters()`——与 `invoke` 相同的类型转换与未声明键过滤；代理工具（MCP / OpenAPI / LangChain，没有 `parameters_model`）则使用内联的 `jsonschema.schema_validate()`。两者都沿用既有的 422 `{"detail": [...]}` 结构（含 `loc` / `msg` / `type`），客户端错误处理无需改动。非 JSON 对象（数组、字符串、数字、`null`、表单提交）现在返回 422，而不是在参数处理阶段失败。
- **`/openapi.json` 由 `RouteTable` 构建器生成**（#124，epic #101）：文档改由 `build_openapi_schema(route_table)` 生成，而不再通过 FastAPI 的 `get_openapi()` 遍历生成的 pydantic 模型，应用发布的文档与独立构建器输出的文档不会再出现偏差。被禁用的路由天然不在文档中。
- **内联 jsonschema 更新到 0.4.4**：内置校验器升级到 zerodep 0.4.4，公开 API 不变。

### 修复

- **请求校验不再阻塞 httpserver 的事件循环**（#161）：`_validate_arguments` 过去在 async handler 中同步执行，请求体或 schema 较大时会卡住 SSE 流与同进程的其他请求。两个 OpenAPI 后端现在都在工作线程中校验：httpserver handler 与 FastAPI 异步端点使用 `asyncio.to_thread`，FastAPI 同步端点本来就在其线程池中运行。`HTTPException` 仍以相同的 422 结构返回客户端。
- **OpenAPI 后端的工具失败日志重新包含堆栈**（#162）：内联 `structlog` 的处理器链没有 `format_exc_info`，`logger.exception()` 到达 `ConsoleRenderer` 时只剩 `exc_info=True`，堆栈被丢弃。两个后端现在与 MCP 路径一样显式传入 `exception=traceback.format_exc()`；FastAPI 适配器改用内联 logger，不再使用标准库 `logging`，以便接受该关键字。返回给客户端的 500 响应体不变。
- **OpenAPI 请求校验不再执行来自 spec 的正则**（core #384）：OpenAPI 文档中的 `pattern` 或 `patternProperties` 可能发生灾难性回溯并卡住工具调用，因为 Python 的 `re` 没有超时。代理工具现在使用去掉这些关键字的副本校验（移除 `patternProperties` 时同层 `additionalProperties: false` 会放宽），而 `/openapi.json`、MCP 的 `inputSchema` 与路由 ETag 仍发布原始约束。覆盖保留其正则；字段格式以上游 API 为准。同一批修复中，`additionalProperties: {}` 不再被当作 `False`：空 schema 现在允许额外字段，与 override 路径一致，而不是在校验中接受、随后又被过滤掉。
- **operator 配置中的本地 OpenAPI 路径现在可以加载**（core #376）：core #376 让 `load_openapi_spec()` 默认拒绝本地路径，除非调用方显式开启，而 `register_openapi_source()` 没有开启，因此指向本地文件的 `openapi` 源不会注册任何工具，只记录一条警告。现在服务端会传入 `allow_local_files=True`；面板输入仍然只接受 http(s)。
- **httpserver 后端的请求体上限可配置**（#134）：内联后端此前继承 1 MiB 上限，参数超过该值的工具调用在 FastAPI 后端可用，在 httpserver 后端却返回 413。现在可用 `--max-body-size BYTES` 与 `OpenAPIAdapter(max_body_size=...)` 设置该上限；默认仍为 1 MiB，非正值会抛出 `ValueError`，超限请求返回不含 traceback 的 JSON `413`。如果请求体大到服务端拒绝时客户端仍在写入，仍可能表现为连接重置——排空未读请求体必须在内部服务端中完成。
- **核心层覆写现在会传达到所有对外发布的 schema**：`ToolRegistry.override()` 会替换
  `Tool`，而 `RouteEntry` 持有它的直接引用，但 `RouteTable._on_registry_change()` 忽略了
  `ChangeEventType.OVERRIDE`。因此 MCP 的 `tools/list` 与 OpenAPI 路由仍保留被替换的描述、
  标题与注解。现在 `OVERRIDE` 与 `METADATA_UPDATE` 同等处理：按名称从已安装的工具重建该路由，
  描述与 `title` 一并更新，并通知监听者。订阅变更的客户端会收到 `tools/list_changed`，
  其余客户端在下一次 `tools/list` 时看到新定义。由
  `tests/test_route_table.py::TestRouteTable::test_override_replaces_the_route_and_notifies`
  （override、restore）与
  `tests/test_mcp_session.py::TestBackwardCompatibility::test_override_reaches_list_tools_title`
  （MCP `tools/list`）固定。
- **路由级覆盖会被校验并具有权威性**（#138）：覆盖现在是一份经校验的副本。`override_parameters` 要求对象 schema（接受 `["object", "null"]`），把省略的 `additionalProperties` 规范化为 `false`，从工具的校验模型补齐 `required`，当覆盖缺少模型必填字段时发出警告，并拒绝模型型工具无法执行的带类型 `additionalProperties`。OpenAPI 适配器在走校验模型之前先检查实际发布的 schema，因此覆盖未声明的键会以 `extra_forbidden` 被拒绝，而不会静默传给可调用对象。
- **两个 OpenAPI 后端的非有限工具结果现在都是严格 JSON**（#130、#135）：FastAPI 通过 pydantic 序列化工具结果，把非有限 `Decimal` 写成字符串 `"NaN"`，而 httpserver 则写出裸 `NaN`。两个后端现在都用 `adapters.openapi.serialisation.encode_tool_result()` 编码结果，非有限浮点数与 `Decimal` 一律输出 `null`。编码器就是 core 的 `ToolResultJSONEncoder`，因此默认矩阵与 `core-master` 任务上的投影一致（#159）。由于共用同一个编码器，`bytes` 结果在两个后端上都是 base64（FastAPI 此前按 UTF-8 解码），集合按 `repr` 排序，工具响应的 `Content-Type` 在两个后端上都是 `application/json; charset=utf-8`。
- **500 响应不再回显异常文本**（#147、#150）：两个后端此前都把 `str(exc)` 作为执行错误 detail 返回，其中可能含连接串、内部路径与上游 URL。现在执行错误返回点名工具的稳定消息，完整异常只写入服务端日志，不可序列化的结果同样处理。FastAPI 后端遇到意外的 500 时改为返回与 httpserver 相同的 JSON `{"detail": ...}` 结构，而不是纯文本。
- **MCP 工具失败不再泄漏异常文本**（#153）：此前工具异常会以 `str(e)` 作为 `isError` 结果返回客户端。现在客户端只会收到稳定的 `Tool '<name>' execution failed` 消息，完整异常与堆栈只写入服务端日志。`INVALID_PARAMS` 等协议级错误不变。

### 安全

- **OpenAPI 后端加固凭据比较与 `/docs` 页面**（#149）：两个后端（FastAPI 依赖与 httpserver）都用 SHA-256 摘要配合 `secrets.compare_digest` 比较 bearer 凭据，并逐一比较所有已配置 token，因此匹配位置不会泄漏。operator 提供的 `/docs` 标题会做 HTML 转义，Swagger UI 固定为 5.34.1，两个 CDN 资源都带 SRI 哈希。`/events` 默认最多 64 个并发订阅者（`0` 关闭该端点，负值报错）；超出返回 503，不再无限增长队列集合。

## [0.7.0] - 2026-10-23

### 新增

- **不依赖 FastAPI 的 OpenAPI 文档构建器**：`toolregistry_server.adapters.openapi.spec.build_openapi_schema(route_table)` 直接从 `RouteTable` 生成 OpenAPI 3.1 文档，为每个启用路由输出一个 `POST` operation，其 `operationId`、`summary`（描述截断到 120 字符）、命名空间标签、内联参数 schema、`x-*` 执行提示扩展，以及 `/tools` 与 `/events` 两个 meta operation 均与 FastAPI 应用发布的一致。被禁用的路由直接不出现，因此无需事后再过滤。每个 operation 还包含响应契约——`200` 携带工具声明的 `outputSchema`（未声明时为空 schema），`422` 内联 `{"detail": [...]}` 校验错误结构——并可通过 `security_schemes` / `security` 参数输出 `components.securitySchemes` 与文档级的鉴权要求，使 Bearer 鉴权在移植后依然被描述。这是从适配器中移除 FastAPI 的文档生成部分（epic #101）；在移植完成前 `/openapi.json` 仍由 FastAPI 提供。逐路径比对的测试会解析 FastAPI 的 `$ref`、去掉 pydantic 的 `title` 键，并比较请求体与响应体。
- **MCP 工具注解与标题**（#280）：`tools/list` 现在会带上 `Tool.metadata.title` 对应的 `title`，以及来自 `Tool.execution_hints` 的 `annotations`（`readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint`）。未声明的提示保持缺省，因此没有做出任何声明的工具仍保持最小的 `{name, description, inputSchema}` 定义。MCP SDK v1（camelCase）与 v2（snake_case）的 Python 字段拼写不同，但两者序列化后都是规范中的 camelCase JSON 键。
- **OpenAPI 操作扩展与路由访问器**（#280）：生成的 operation 会带上 `x-read-only`、`x-destructive`、`x-idempotent`、`x-open-world` 与 `x-title`，与 MCP 的 `annotations` 保持一致；未声明的提示不会输出。`RouteEntry` 新增 `title` 与 `execution_hints` 属性，使两个适配器读取同一份元数据，而不再直接访问 `route.tool`。
- **MCP 子命令新增 `--server-url`**（#78）：用于设置 OAuth protected-resource 元数据中的公网 URL。此前只能通过 `adapter.run(server_url=...)` 传入，因此用 CLI 部署在反向代理之后的实例无法声明一个客户端可访问的源，只能退回监听地址。此外，当即将声明的 URL 来自通配监听地址（`0.0.0.0`、`::`）时，服务器会在启动时输出警告。
- **MCP 子命令新增 `--tokens`**（#99）：适配器本来就接受 `tokens_path`，文档也写明了该参数，但只有 `openapi` 子命令定义了它，导致 `mcp --transport http --tokens tokens.txt` 在 argparse 阶段直接失败。`API_BEARER_TOKEN` 环境变量的用法保持不变。
- **MCP `serverInfo` 带上版本号与使用说明**（#79）：`initialize` 返回的 `serverInfo.version` 一直是空的，因为适配器从未向 SDK 传入版本。现在会填入当前安装的 `toolregistry-server` 版本，嵌入方也可以传入自己的版本——`App` 与 CLI 会转发 `ServerIdentity.version`，因此 Hub 声明的是 Hub 的版本而非 server 库的版本。当服务提供 `discover_tools` 时，响应还会带上 `instructions`，说明「先 `discover_tools` 再 `call_deferred`」的两步延迟工具流程——此前客户端只能从工具描述中自行推断。
- **MCP SDK v2 上开始提供 `subscriptions/listen`**（#107）：2026-07-28 协议没有常驻通知通道，SDK 会丢弃在连接上发送的 `notifications/tools/list_changed`，因此 modern era 客户端永远不知道工具列表发生了变化。适配器现在注册 SDK 的监听 handler 并挂接进程内总线，每次路由表变更都会发布 tools-list-changed 事件，打开监听流（SEP-2575）的客户端即可收到并重新拉取 `tools/list`。旧版协议客户端仍走原有的 session 通知路径；没有监听钩子的 SDK 版本不受影响。

### 变更

- **核心依赖下限提升到 `toolregistry>=0.19.0`，discovery/profile 回归测试转为强制执行** (#115)：服务端现在要求会在禁用事件时使发现索引失效的那条 core 版本线，因此 `apply_profile()` 之后不会再留下仍可被发现的已禁用工具。测试不再探测已安装的 core 版本、也不再在旧版本上跳过，而是无条件运行。
- **`tools/list` 默认缓存五分钟**（#114，**行为变更**）：服务器已提供 `subscriptions/listen`（#110），监听变更的客户端会在每次变更时使缓存失效，因此 `ttlMs` 默认值改为规范示例中的 `300000`，而不再是 `0`。不监听变更的客户端会在最多五分钟内使用缓存列表；`--tools-list-ttl-ms 0` 可恢复为每次重新获取。`cacheScope` 仍为 `private`。
- **`tools/list` 缓存提示可配置**（#83）：新增的 `--tools-list-ttl-ms` 与 `--tools-list-cache-scope` 用于设置 `tools/list`（SDK v2）的 `ttlMs` / `cacheScope` 提示，此前只能通过适配器参数传入且从未有人传过。默认仍为 `ttlMs: 0`（「永不缓存」），直到 `notifications/tools/list_changed` 在 SDK v2 上得到验证（#107）——在失效通知不可用时启用缓存，会让客户端在缓存期内一直显示已不存在的工具。`cacheScope` 默认为 `private`，因为实际提供的工具集合取决于 profile 与授权。
- **未知或已禁用的工具属于协议错误**（#81，对 MCP 客户端而言是**行为变更**）：当 `tools/call` 调用的工具不存在或当前处于禁用状态时，现在返回 JSON-RPC 错误 `-32602`（Invalid params），而不再是带 `isError: true` 的 `CallToolResult`。此时请求本身无效，宿主不应把它当作工具结果回传给模型。工具执行失败仍返回 `isError: true`，以便模型读取错误信息并调整做法。该行为在 MCP SDK v1 与 v2 上一致。

### 修复

- **dataclass 工具结果按结构化形式序列化**（#187、#192）：`_serialize_result` 原先使用 `json.dumps(..., default=str)`，返回 `list[SearchResult]` 的工具因此把 Python `repr` 字符串数组交给模型——既不可机器解析，也无法按字段索引。现在 dataclass（顶层或嵌套）、datetime、enum、Path、bytes 与 set 都会编码为对应的 JSON 形式，`str` 仅作为最后兜底。
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
