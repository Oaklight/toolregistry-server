---
title: CLI 参考
---

# 命令行界面

`toolregistry-server` 提供命令行工具，无需编写自定义代码即可运行服务器。

## 用法

```bash
toolregistry-server [选项] <子命令> [子命令选项]
```

## 顶层选项

| 选项 | 说明 |
|------|------|
| `--version`, `-V` | 显示版本并退出 |
| `--no-banner` | 禁用启动横幅 |

## 子命令通用选项

| 选项 | 说明 |
|------|------|
| `--env PATH` | .env 文件路径（默认：当前目录下的 `.env`）|
| `--no-env` | 跳过加载 .env 文件 |
| `--config PATH` | JSONC 或 YAML 配置文件路径 |
| `--profile PROFILE` | 部署配置：`remote`、`local`，或配置文件 `profiles:` 中声明的名称 |
| `--config-reload` / `--no-config-reload` | 监听配置文件，变化时应用源级 diff（默认：禁用） |
| `--no-config-write` | 运行期来源变更只保留在内存中，不重写配置文件 |

## 子命令

### `openapi` — 启动 OpenAPI 服务器

```bash
toolregistry-server openapi [选项]
```

独立服务器始终运行在内联的 `httpserver` 上：不需要 extra，也没有 `--backend` 与
`--reload`（两者都只属于 uvicorn）。`gateway` 命令保留 `--backend` 选择。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--config PATH` | - | JSONC 或 YAML 配置文件路径（必需） |
| `--host HOST` | `0.0.0.0` | 绑定主机 |
| `--port PORT` | `8000` | 绑定端口 |
| `--tokens PATH` | - | Bearer 令牌文件，每行一个（`#` 开始注释）。令牌以恒定时间比较；同时读取 `API_BEARER_TOKEN`（逗号分隔） |
| `--execution-log` / `--no-execution-log` | 配置 `--keys-db` 时开启 | 在注册表执行日志中记录每次工具调用。见[认证](../guides/authentication.md#执行日志) |
| `--oauth-issuer URL` | - | 接受的授权服务器 issuer（`iss`）。启用 OAuth Bearer 校验，令牌在本地用该服务器的 JWKS 验证 |
| `--oauth-audience URL` | 由 `--host`/`--port` 推导 | 期望的 `aud` 或 RFC 8707 `resource` 值。无法推导 URL 时必须显式提供 |
| `--oauth-jwks URL` | 从 RFC 8414/OIDC 元数据发现，否则 `<issuer>/.well-known/jwks.json` | JWKS 端点覆盖 |
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | 把一个令牌作用域映射到命名空间模式与工具覆盖；可重复。每个条目是命名空间模式、`+tool-name`（允许单个工具）或 `-tool-name`（拒绝单个工具）。未映射的作用域不授予任何权限 |
| `--oauth-scopes "SCOPE ..."` | - | 在受保护资源元数据中公布的作用域，空格分隔。为空时省略；配置 issuer 后默认省略；未配置 OAuth 时仍公布 `mcp` |
| `--oauth-authorization-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 流程的授权端点 |
| `--oauth-token-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 流程的令牌端点 |
| `--oauth-refresh-url URL` | - | 可选的 `refreshUrl`；未设置时省略 |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | 可能携带客户端在途调用上限的令牌声明；非正整数会被忽略，更宽的值不会提高客户端当前生效的预算 |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | 单个 OAuth 客户端的静态在途调用上限；可重复。有效的并发声明优先 |
| `--oauth-introspection-url URL` | - | RFC 7662 内省端点；每个本地校验通过的令牌都会向它确认 |
| `--oauth-introspection-client-id ID` | - | 内省端点使用的客户端 id（HTTP Basic）；与 URL 同时必需 |
| `--oauth-introspection-client-secret SECRET` | - | 内省端点使用的客户端密钥（HTTP Basic）；与 URL 同时必需 |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | 一次内省结果的复用时长；决定被撤销的令牌还会被接受多久。`0` 关闭缓存 |
| `--oauth-introspection-optional` | `false` | 内省端点失败时回退到本地校验；默认情况下失败即拒绝令牌 |
| `--max-body-size BYTES` | `1048576`（1 MiB） | 接受的最大请求体。必须为正数；超限返回带 JSON 错误的 `413` |
| `--profile PROFILE` | - | 部署配置：`remote`、`local`，或配置文件 `profiles:` 中声明的名称 |
| `--regex-timeout SECONDS` | `0.05` | 来自 spec 的正则单次匹配上限（需要 `regex` extra）。`0` 禁用执行 |
| `--regex-budget SECONDS` | `0.25` | 单次请求内所有 spec 正则匹配共享的上限。`0` 禁用执行 |
| `--strict-spec-regex` | `false` | OpenAPI source 含非法正则时终止启动；默认只丢弃自身 schema 含该正则的工具 |

**示例：**

```bash
toolregistry-server openapi \
  --config config.yaml \
  --port 8000 \
  --tokens tokens.txt \
  --profile remote
```

### `mcp` — 启动 MCP 服务器

```bash
toolregistry-server mcp [选项]
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--config PATH` | - | JSON、JSONC 或 YAML 配置文件路径（必需） |
| `--transport TYPE` | `stdio` | 传输类型：`stdio`、`sse`、`streamable-http` 或 `http` |
| `--host HOST` | `127.0.0.1` | 绑定主机（SSE/HTTP 传输） |
| `--port PORT` | `8000` | 绑定端口（SSE/HTTP 传输） |
| `--tokens PATH` | - | Bearer 令牌文件，每行一个，用于 `streamable-http`/`http`。同时读取 `API_BEARER_TOKEN`（逗号分隔） |
| `--keys-db PATH` | - | SQLite 密钥存储；限定作用域的客户端密钥在每个 MCP 入口上执行 |
| `--allow-operator-token` | `false` | 即使配置了密钥存储，也接受 MCP 上的静态运维令牌 |
| `--client-concurrency N` | `8` | `sse`/`streamable-http` 上单个客户端的在途调用上限；密钥只能调低，不能调高 |
| `--concurrency-timeout SECONDS` | `30` | 调用等待空位的上限，超时返回 `-32000` |
| `--execution-log` / `--no-execution-log` | 配置 `--keys-db` 时开启 | 在注册表执行日志中记录每次工具调用。见[认证](../guides/authentication.md#执行日志) |
| `--oauth-issuer URL` | - | 接受的授权服务器 issuer（`iss`）。启用 OAuth Bearer 校验，令牌在本地用该服务器的 JWKS 验证 |
| `--oauth-audience URL` | 由 `--host`/`--port` 推导 | 期望的 `aud` 或 RFC 8707 `resource` 值。无法推导 URL 时必须显式提供 |
| `--oauth-jwks URL` | 从 RFC 8414/OIDC 元数据发现，否则 `<issuer>/.well-known/jwks.json` | JWKS 端点覆盖 |
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | 把一个令牌作用域映射到命名空间模式与工具覆盖；可重复。每个条目是命名空间模式、`+tool-name`（允许单个工具）或 `-tool-name`（拒绝单个工具）。未映射的作用域不授予任何权限 |
| `--oauth-scopes "SCOPE ..."` | - | 在受保护资源元数据中公布的作用域，空格分隔。为空时省略；配置 issuer 后默认省略；未配置 OAuth 时仍公布 `mcp` |
| `--oauth-authorization-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 流程的授权端点 |
| `--oauth-token-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 流程的令牌端点 |
| `--oauth-refresh-url URL` | - | 可选的 `refreshUrl`；未设置时省略 |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | 可能携带客户端在途调用上限的令牌声明；非正整数会被忽略，更宽的值不会提高客户端当前生效的预算 |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | 单个 OAuth 客户端的静态在途调用上限；可重复。有效的并发声明优先 |
| `--oauth-introspection-url URL` | - | RFC 7662 内省端点；每个本地校验通过的令牌都会向它确认 |
| `--oauth-introspection-client-id ID` | - | 内省端点使用的客户端 id（HTTP Basic）；与 URL 同时必需 |
| `--oauth-introspection-client-secret SECRET` | - | 内省端点使用的客户端密钥（HTTP Basic）；与 URL 同时必需 |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | 一次内省结果的复用时长；决定被撤销的令牌还会被接受多久。`0` 关闭缓存 |
| `--oauth-introspection-optional` | `false` | 内省端点失败时回退到本地校验；默认情况下失败即拒绝令牌 |
| `--server-url URL` | 由 `--host`/`--port` 推导 | 在 OAuth 受保护资源元数据中公布的公开 URL（反向代理后必需） |
| `--tools-list-ttl-ms MS` | `300000` | `tools/list` 的 `ttlMs` 缓存提示；`0` 禁用缓存 |
| `--tools-list-cache-scope {public,private}` | `private` | `tools/list` 的 `cacheScope` 提示 |
| `--profile PROFILE` | - | 部署配置：`remote`、`local`，或配置文件 `profiles:` 中声明的名称 |

!!! note
    `--client-concurrency` 与 `--concurrency-timeout` 只作用于网络传输。`stdio`
    会校验这两个值但不限流：它只有一个客户端且没有按客户端的凭据，配额只会让这个
    客户端与自己竞争。

**示例：**

```bash
# stdio 传输（适用于 Claude Desktop 等）
toolregistry-server mcp --config config.json

# Streamable HTTP 传输
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --port 8000

# SSE 传输
toolregistry-server mcp \
  --config config.json \
  --transport sse \
  --port 8000

# 带 Bearer Token 认证（streamable-http）
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --tokens tokens.txt
```

### `gateway` — 单进程提供 OpenAPI、MCP 与管理面板

```bash
toolregistry-server gateway [选项]
```

一个 ASGI 应用把 OpenAPI 适配器挂载在 `/openapi`、MCP streamable-HTTP 挂载在
`/mcp`、MCP SSE 挂载在 `/sse`、管理面板挂载在 `/admin`。每个前端的路径可配置，
启用标记按请求检查。管理面板默认禁用。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--config PATH` | - | JSONC 或 YAML 配置文件路径（必需） |
| `--host HOST` | `0.0.0.0` | 绑定主机 |
| `--port PORT` | `8000` | 绑定端口 |
| `--socket PATH` | - | 改为绑定 Unix 域套接字而不是 `--host`/`--port`（仅 POSIX；优先级更高）。客户端需要支持 UDS 的 HTTP 传输 |
| `--tokens PATH` | - | Bearer 令牌文件，作用于所有前端；同时读取 `API_BEARER_TOKEN` |
| `--oauth-issuer URL` | - | 接受的授权服务器 issuer（`iss`）。启用 OAuth Bearer 校验，令牌在本地用该服务器的 JWKS 验证 |
| `--oauth-audience URL` | 由 `--host`/`--port` 推导 | 期望的 `aud` 或 RFC 8707 `resource` 值。无法推导 URL 时必须显式提供 |
| `--oauth-jwks URL` | 从 RFC 8414/OIDC 元数据发现，否则 `<issuer>/.well-known/jwks.json` | JWKS 端点覆盖 |
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | 把一个令牌作用域映射到命名空间模式与工具覆盖；可重复。每个条目是命名空间模式、`+tool-name`（允许单个工具）或 `-tool-name`（拒绝单个工具）。未映射的作用域不授予任何权限 |
| `--oauth-scopes "SCOPE ..."` | - | 在受保护资源元数据中公布的作用域，空格分隔。为空时省略；配置 issuer 后默认省略；未配置 OAuth 时仍公布 `mcp` |
| `--oauth-authorization-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 流程的授权端点 |
| `--oauth-token-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 流程的令牌端点 |
| `--oauth-refresh-url URL` | - | 可选的 `refreshUrl`；未设置时省略 |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | 可能携带客户端在途调用上限的令牌声明；非正整数会被忽略，更宽的值不会提高客户端当前生效的预算 |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | 单个 OAuth 客户端的静态在途调用上限；可重复。有效的并发声明优先 |
| `--oauth-introspection-url URL` | - | RFC 7662 内省端点；每个本地校验通过的令牌都会向它确认 |
| `--oauth-introspection-client-id ID` | - | 内省端点使用的客户端 id（HTTP Basic）；与 URL 同时必需 |
| `--oauth-introspection-client-secret SECRET` | - | 内省端点使用的客户端密钥（HTTP Basic）；与 URL 同时必需 |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | 一次内省结果的复用时长；决定被撤销的令牌还会被接受多久。`0` 关闭缓存 |
| `--oauth-introspection-optional` | `false` | 内省端点失败时回退到本地校验；默认情况下失败即拒绝令牌 |
| `--server-url URL` | 由 `--host`/`--port` 推导 | 受保护资源元数据中使用的公开 MCP URL |
| `--backend {fastapi,httpserver}` | `fastapi` | 多个前端必须使用 `fastapi`；`httpserver` 只能提供 openapi 前端 |
| `--openapi-path PATH` | `/openapi` | OpenAPI 挂载路径（`--backend httpserver` 下会被拒绝，因为该后端在监听器根路径提供服务） |
| `--mcp-path PATH` | `/mcp` | MCP streamable-HTTP 挂载路径 |
| `--sse-path PATH` | `/sse` | MCP SSE 挂载路径 |
| `--admin-path PATH` | `/admin` | 管理面板挂载路径 |
| `--openapi` / `--no-openapi` | 启用 | 启用或禁用 OpenAPI 前端 |
| `--mcp` / `--no-mcp` | 启用 | 启用或禁用 MCP streamable-HTTP |
| `--sse` / `--no-sse` | 启用 | 启用或禁用 MCP SSE |
| `--admin` / `--no-admin` | 禁用 | 启用或禁用管理面板；启用时必须配置运维令牌 |
| `--admin-token TOKEN` | - | 管理面板接受的运维令牌；推荐改用 `--admin-token-file` |
| `--admin-token-file PATH` | - | 从文件读取运维令牌 |
| `--keys-db PATH` | - | 支撑面板 Keys 页的 SQLite 存储 |
| `--allow-operator-token` | `false` | 即使配置了密钥存储，也接受 MCP 上的静态运维令牌 |
| `--client-concurrency N` | `8` | 单个客户端的在途调用上限；密钥只能调低，不能调高 |
| `--concurrency-timeout SECONDS` | `30` | 调用等待空位的上限，超时返回 `-32000` |
| `--execution-log` / `--no-execution-log` | 配置 `--keys-db` 时开启 | 在注册表执行日志中记录每次工具调用。见[认证](../guides/authentication.md#执行日志) |
| `--admin-allow-private-hosts` | `false` | 允许面板拉取解析到私有地址的来源 URL |
| `--no-config-write` | `false` | 运行期前端变更不写回配置文件 |
| `--max-body-size BYTES` | `1048576`（1 MiB） | OpenAPI 前端接受的最大请求体 |
| `--regex-timeout SECONDS` | `0.05` | 来自 spec 的正则单次匹配上限 |
| `--regex-budget SECONDS` | `0.25` | 单次请求内所有 spec 正则匹配的上限 |
| `--strict-spec-regex` | `false` | OpenAPI source 含非法正则时终止启动 |
| `--tools-list-ttl-ms MS` | `300000` | MCP `tools/list` 的 `ttlMs` 缓存提示 |
| `--tools-list-cache-scope {public,private}` | `private` | MCP `tools/list` 的 `cacheScope` 提示 |
| `--profile PROFILE` | - | 部署配置 |

**示例：**

```bash
toolregistry-server gateway \
  --config config.yaml \
  --port 8000 \
  --openapi-path /api \
  --no-sse \
  --tokens tokens.txt \
  --admin \
  --admin-token-file operator-token.txt
```

被禁用的前端返回 `404`，不会触达注册表。前端路径与启用标记也可以保存在配置文件的
`gateway.frontends` 下；参见[配置指南](../../guides/configuration.md)。
管理面板使用自己的运维令牌，而不是 `--tokens` 集合；扩展页面位于面板挂载路径之下
（例如 `/admin/ext/gateway/keys`）。Keys、Frontends 与 Sources 三个页面的说明见
[网关管理面板指南](../../guides/gateway-admin.md)。

### `local-hub` — 为本地 MCP 客户端运行一个共享网关

```bash
toolregistry-server local-hub [run|ensure|stop|status] --config config.yaml --name default
```

把网关作为每机一个常驻进程提供服务，让多个 MCP 客户端共享一份注册表、一套工具
schema 与一组缓存。仅支持 POSIX；Windows 上动作会被拒绝并提示改用 `gateway`。见
[本地 Hub 指南](../../guides/local-hub.md)。

由 `ensure` 或 shim 拉起的 hub 以 `toolregistry-server local-hub run ...` 启动。
设置 `TOOLREGISTRY_LOCAL_HUB_COMMAND` 可改用其他启动器——Hub 部署把它设为
`TOOLREGISTRY_LOCAL_HUB_COMMAND=toolregistry-hub`，让常驻进程保留 Hub 启动器的
注册表策略。该值按 shell 命令拆分并原样使用，因此可以指定包装脚本或其他解释器。
优先级：变量包含非空白文本时使用它，其次使用 `PATH` 上的 `toolregistry-server`，
最后使用当前解释器执行包内引导代码。被拉起的 hub 会继承该变量。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `action` | `run` | `run` 前台运行；`ensure` 复用已运行的 hub 或拉起 detached 进程并打印 endpoint；`stop` 通知属主；`status` 报告状态 |
| `--name NAME` | `default` | hub 名称；不同名字的 hub 可共存 |
| `--config PATH` | - | 工具来源。提供服务时必需；`stop` 与 `status` 不需要 |
| `--host HOST` | `127.0.0.1` | 绑定主机 |
| `--port PORT` | `0` | 绑定端口；`0` 由系统分配，便于多个 hub 共存 |
| `--socket PATH` | - | 改为绑定 Unix 套接字而不是 TCP |
| `--idle-timeout SECONDS` | `0` | 在该秒数内既无请求也无打开的 MCP/SSE 会话后退出；`0` 表示常驻 |
| `--timeout SECONDS` | - | `ensure` 与 `stop` 的等待上限 |
| `--json` | 关闭 | `status` 输出 endpoint 文档 |

hub 会发布 endpoint 文档并持有独占锁，使所有权无歧义。hub 运行时 `status` 退出 `0`，
否则退出 `3`；没有属主时 `stop` 退出 `3`，且当公布地址无人接受连接时拒绝发信号，
因此陈旧文档不会误伤被复用的无关 pid。

### `hub-shim` — 把一个本地 hub 暴露给单个 stdio MCP 客户端

```bash
toolregistry-server hub-shim --name default --config config.yaml
```

对客户端使用 stdio 的 MCP，并通过 streamable HTTP 转发到指定 hub。hub 不在线时
`initialize` 由本地应答、`tools/list` 为空；调用会先失败一次并返回 `-32000`，随后
shim 在后台重连（0.25 秒起步倍增到 5 秒）。当 hub 的工具列表与客户端最近一次拿到的
不同时，shim 发送 `notifications/tools/list_changed`。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--name NAME` | `default` | 要连接的 hub |
| `--config PATH` | - | shim 需要自行拉起 hub 时使用的配置；不提供则只等待已有 hub |
| `--socket PATH` | - | shim 拉起的 hub 使用的 Unix 套接字 |
| `--connect-timeout SECONDS` | `5` | 单次连接加初始化的上限 |
| `--no-spawn` | 关闭 | 从不拉起 hub，只报告不可用 |

与 `local-hub` 一样仅支持 POSIX。每个客户端一个 shim，共享同一个 hub。

### `keys` — 管理限定作用域的客户端 API 密钥

```bash
toolregistry-server keys --db PATH <命令> [命令选项]
```

操作 `--db` 指定的 SQLite 密钥存储，即服务器用 `--keys-db` 读取的同一文件。
`--db` 必填，且位于命令之前。作用域模型见
[认证指南](../../guides/authentication.md#限定作用域的-api-密钥)。

| 命令 | 用途 |
|------|------|
| `create` | 创建密钥并打印一次密钥值 |
| `list` | 按最近使用时间排序列出密钥 |
| `get KEY_ID` | 显示一个密钥及其用量计数 |
| `rotate KEY_ID` | 为已有密钥签发新密钥值并打印一次 |
| `update KEY_ID` | 修改密钥的名称、作用域、过期时间或并发上限 |
| `revoke KEY_ID` | 立即撤销密钥 |
| `set-concurrency KEY_ID` | 修改密钥的在途调用上限 |

`list` 接受 `--limit`（默认 50）与 `--offset`。所有命令都接受 `--json`，
输出机器可读结果。

**轮换密钥。** `keys rotate KEY_ID` 为已存在的密钥签发新密钥值。密钥 id、
名称、作用域、过期时间、并发上限与用量历史都保持不变，只有密钥值改变。新
密钥值只打印一次，旧值在下一个请求即被拒绝。已撤销的密钥与已过期的密钥不能
轮换：先用 `update` 延长或清除过期时间，再轮换。

**修改密钥。** `keys update KEY_ID` 只改指定的字段，其余保持不变：

| 选项 | 作用 |
|------|------|
| `--label LABEL` | 新的非空名称 |
| `--namespace PATTERN` | 替换命名空间允许列表；可重复。`*` 允许所有命名空间，`prefix/*` 允许一个前缀 |
| `--allow-tool NAME` | 用允许项替换按工具覆盖表；可重复 |
| `--deny-tool NAME` | 用拒绝项替换按工具覆盖表；可重复 |
| `--expires ISO8601` | 新的过期时间；未给时区时按 UTC 处理。与 `--clear-expiry` 互斥 |
| `--clear-expiry` | 清除过期时间，使密钥不再过期 |
| `--concurrency N` | 新的在途调用上限。与 `--clear-concurrency` 互斥 |
| `--clear-concurrency` | 丢弃该覆盖，改用服务器的 `--client-concurrency` |

作用域选项按类别合并，而不是整体替换。`--namespace` 只替换命名空间列表，
`--allow-tool` 与 `--deny-tool` 合起来只定义按工具覆盖表，同时出现在两者中的
名称按拒绝处理。未给出选项的类别保留当前值，因此
`update KEY_ID --label build-bot-v2` 只改名称，其余不变。注意 `create` 的过期
选项拼作 `--expires-at`，`update` 拼作 `--expires`；两者都拒绝过去的时间。
密钥已撤销时修改会被拒绝。修改在下一个请求生效，无需重启。

**用量计数。** `keys list --usage` 为每条记录附加 `usage` 对象，`keys get` 总是
包含它。该对象包含 `calls`、`ok`、`error`、`busy`、`denied` 与
`last_success_at`；文本输出会打印一行 `calls:`（含四种结果）与一行
`last ok:`。计数持久化在密钥数据库中，因此重启后仍在，也不依赖内存中的执行
日志；轮换、撤销与过期都不会清除计数。用量对象从不包含密钥值。

**退出码。** 成功为 `0`。`KEY_ID` 不存在为 `1`。存储拒绝该修改——密钥已撤销、
过期时间在过去、并发上限非法——或 `update` 没有给出任何要改的字段时为 `2`。

## 配置文件

有关 JSON、JSONC 或 YAML 配置格式的详细说明，请参阅 [配置指南](../../guides/configuration.md)。

## 编程 API

### `App` 类（推荐）

`App` 类是规范的编程入口点。继承它并重写 `prepare_registry()` 以使用自定义逻辑构建注册表：

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

class MyApp(App):
    def prepare_registry(self):
        registry = ToolRegistry()
        registry.register(my_tool)
        return registry

if __name__ == "__main__":
    MyApp().serve_openapi(host="0.0.0.0", port=8000)
    # 或：MyApp().serve_mcp(transport="stdio")
```

### `CLI` 类 — 自定义子命令

`CLI` 类是规范的命令行入口点。继承它并重写 `configure_subparsers()` 以添加自定义参数：

```python
from argparse import ArgumentParser
from toolregistry_server import CLI, App

class MyCLI(CLI):
    def configure_subparsers(self, subparsers: dict[str, ArgumentParser]):
        # 向 openapi 子命令添加自定义参数
        subparsers["openapi"].add_argument(
            "--my-flag", action="store_true", help="启用自定义功能"
        )

if __name__ == "__main__":
    MyCLI().main()
```

使用独立的 `run_cli()` 辅助函数作为简单入口点：

```python
from toolregistry_server import run_cli

run_cli()
```

### `ServerIdentity` — 自定义横幅与版本

传入 `ServerIdentity` 以自定义启动横幅和 `--version` 输出：

```python
from toolregistry_server import App, ServerIdentity

identity = ServerIdentity(
    name="my-server",
    version="1.2.3",
    description="我的自定义工具服务器",
)

App(identity=identity).serve_openapi()
```

## 部署配置文件

`--profile` 参数在注册表构建完成后应用基于标签的工具过滤：

| 配置文件 | 效果 |
|----------|------|
| `remote` | 禁用标记为 `file_system`、`destructive` 或 `privileged` 的工具 |
| `local` | 禁用标记为 `network` 的工具 |
| *（无）* | 不过滤（默认） |

配置文件可以用 `profiles:` 定义自己的部署配置；这些设置优先于内置标签集合，未知名称会记录警告并被忽略：

```yaml
profiles:
  staging:
    disable_tags: [destructive, privileged]
    enable: [calculator]      # 标签过滤后强制启用的命名空间模式
    disable: [internal/]      # 标签过滤后强制禁用的命名空间模式
```

每个配置接受 `disable_tags`（替换内置集合）、`enable` 与 `disable`（命名空间模式，按此顺序应用）。

向不应访问服务器文件系统或特权操作的最终用户提供工具时，请使用 `remote`：

```bash
toolregistry-server openapi --config config.json --profile remote
toolregistry-server mcp --config config.json --profile remote
```
