# 认证

`toolregistry-server` 为 OpenAPI 和 MCP Streamable-HTTP 端点提供内置的 Bearer Token 认证。

## 概述

认证模块支持：

- 从文件加载多个令牌
- 对所有传入请求进行 Bearer Token 验证
- 可选的限定作用域 API 密钥：命名空间允许列表、单工具覆盖、过期和吊销
- 运行时令牌管理（无需重启服务器即可添加/删除令牌）
- 动态启用/禁用

## 配置认证

### 编程方式 — 使用 `App`（推荐）

`App` 会把 `tokens_path` 转发给适配器，后者读取该文件以及 `API_BEARER_TOKEN` 环境变量（逗号分隔）：

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

registry = ToolRegistry()
registry.register(my_tool)

App().serve_openapi(
    registry=registry,
    tokens_path="tokens.txt",   # 每行一个令牌，# 开头为注释
    host="0.0.0.0",
    port=8000,
)
```

同样的方式适用于 MCP Streamable-HTTP：

```python
App().serve_mcp(
    registry=registry,
    transport="streamable-http",
    tokens_path="tokens.txt",
    host="0.0.0.0",
    port=8000,
)
```

如需直接内联令牌（不用文件），请直接构造 OpenAPI 适配器（MCP 适配器只接受令牌文件或 `API_BEARER_TOKEN`）：

```python
from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import OpenAPIAdapter

OpenAPIAdapter(RouteTable(registry), tokens=["token-one", "token-two"]).run(
    host="0.0.0.0", port=8000
)
```

### 命令行方式 — `--tokens` 参数

```bash
# OpenAPI 服务器使用令牌文件
toolregistry-server openapi --config config.json --tokens tokens.txt

# MCP streamable-http 服务器使用令牌文件
toolregistry-server mcp --config config.json --transport streamable-http --tokens tokens.txt
```

令牌文件格式（每行一个令牌；以 `#` 开头的行被忽略）：

```
# 我的 API 令牌
token-one
token-two
token-three
```

## 限定作用域的 API 密钥

`--tokens` 令牌是全有或全无的：一个令牌可以调用所有工具。若要签发只能调用部分工具的客户端凭据，请使用 API 密钥存储。该功能是可选的；不传 `--keys-db` 时，服务器行为与之前完全一致。

### 创建和管理密钥

```bash
# 为 calculator 命名空间创建密钥；密钥只显示一次
toolregistry-server keys --db keys.db create \
  --label build-bot \
  --namespace calculator \
  --deny-tool calculator-dangerous

# 按最近使用时间排序列出密钥（分页）
toolregistry-server keys --db keys.db list --limit 20

# 按键 id 查看或吊销
toolregistry-server keys --db keys.db get key_...
toolregistry-server keys --db keys.db revoke key_...
```

`create` 只打印一次密钥。存储中只保留带每密钥独立盐的 scrypt 哈希，因此密钥无法恢复。`list`、`get` 和 `revoke` 都不会显示它。任意子命令加 `--json` 可输出机器可读格式；加 `--expires-at ISO8601` 可设置过期时间。

### 使用密钥启动服务器

```bash
toolregistry-server openapi --config config.json --keys-db keys.db
toolregistry-server mcp --config config.json --transport streamable-http \
  --keys-db keys.db
toolregistry-server gateway --config config.json --keys-db keys.db
```

密钥数据库可以与 `--tokens` 同时使用。静态 `--tokens` 令牌保持完整、不受限的访问权限，属于引导/兼容路径；作用域只适用于数据库中创建的密钥。MCP 的 `stdio` 传输不携带请求凭据，因此会忽略 `--keys-db`。

`gateway` 命令为每个进程构建一个解析器，并传给 OpenAPI 应用、streamable-HTTP 服务器和 SSE 应用，因此三个前端共享同一密钥存储，并在四个入口执行相同的作用域检查。只要配置了密钥存储或静态令牌，就会提供 RFC 9728 元数据路由。

### 作用域规则

作用域由命名空间允许列表和可选的单工具覆盖组成：

```json
{
  "namespaces": ["calculator", "web/*"],
  "tools": {
    "calculator-dangerous": false,
    "beta-echo": true
  }
}
```

- 工具的命名空间匹配列表项时允许调用。`"*"` 匹配所有命名空间；`"web/*"` 匹配 `web` 及其下所有命名空间；其他值为精确匹配。
- 单工具条目优先。`false` 在被允许的命名空间内拒绝某个工具；`true` 允许列表之外的工具。
- 空作用域拒绝所有工具。省略 `--namespace` 时，该密钥不能调用任何工具。

### 执行与生命周期

同一个 principal 解析器把密钥作用域应用到四个入口：MCP `tools/list`、MCP `tools/call`、`/openapi.json` 文档和 OpenAPI 工具路由。作用域之外的工具不会出现在列表和文档中，调用会被拒绝（OpenAPI 返回 `403`；MCP 返回 `INVALID_PARAMS` 错误）。

每次请求都会读取密钥行。因此吊销和过期在下一个请求立即生效，无需重启或等待缓存。`last_used_at` 只记录最近一次成功使用时间。

限定作用域的密钥保留 `discover_tools` / `call_deferred` 代理工具对，因为两者都会对它
们所操作的工具执行密钥作用域：发现结果只保留密钥可调用的名称，`call_deferred` 对
作用域外的目标会给出与直接调用完全相同的信息并拒绝。无论密钥的命名空间是什么，这对
代理都可见——它们注册在 default 命名空间，若按此判断，任何作用域在别处的密钥都无法
使用发现功能——但针对这两个名称的显式单工具覆盖仍然生效，声明拒绝的密钥既看不到
也无法调用。目标检查读取实时注册表而不是路由表，因此在配置 diff 仍在应用期间注册的
工具也会以同样方式判定。

### 按客户端隔离

密钥同时是服务器记账的身份。

- **保留头会被拒绝。** 携带 `x-client-id`、`x-client-identity`、`x-client-cwd`、
  `x-client-env`、`x-client-environment`、`x-forwarded-user` 或 `x-remote-user`
  的请求会在认证之后以 `400` 失败，并连同调用方密钥记录日志。身份只来自凭据，请在
  服务器前面的任何代理上剥掉这些请求头。
- **配置了密钥时，MCP 拒绝运维令牌。** 使用 `--keys-db` 时，静态令牌无法标识客户端，
  因此解析为不受限 principal 的 MCP 请求会在 `tools/list` 与 `tools/call` 上被拒绝。
  `--allow-operator-token` 可以重新放行；OpenAPI 与管理面板不受影响。
- **并发按密钥限制。** `--client-concurrency`（默认 8）是进程上限；密钥的
  `max_concurrency` 只能调低，不能调高。等待超过 `--concurrency-timeout`（默认 30 秒）
  的调用返回 `-32000`「too many concurrent calls」。
- **调用可归因。** 每次调用记录 `key_id` 与 `client_id`，管理面板的 Clients 页
  （由 `/ext/gateway/clients` 支撑）按客户端报告调用、错误、在途与排队数量。

进程的 `PermissionPolicy` 也会在每次对外调用上评估，MCP 与 OpenAPI 两个前端都如此；
密钥作用域只能在此基础上进一步收紧。

### 执行日志

配置了 `--keys-db` 的部署同时开启注册表的执行日志：有密钥的部署正是调用具有稳定身份、
值得保留的场景。每个到达工具处理器的调用——MCP，以及两个后端上的 OpenAPI——写入一条
记录，包含工具名、参数、结果或错误、耗时，以及
`metadata={"key_id": ..., "client_id": ...}`。被密钥作用域、代理目标检查或进程权限策略
拒绝的调用从未到达处理器，不会进入执行日志；MCP 的拒绝仍会在结构化日志中归属，而
OpenAPI 的拒绝不在服务端留下记录。

结构化归属行对通过认证后的每次尝试写入，`outcome` 为四种之一：`ok`、`error`、
`busy`（并发限制器）或 `denied`（作用域、代理目标或策略）。它的 `duration_ms`
从鉴权之前开始计时；日志行的 `duration_ms` 只计处理器调用。

日志是每进程 1000 条的内存环形缓冲，由管理面板的 Logs 页读取。它不持久化，重启即清空。
除非日志此前已以 `full_record` 开启，条目遵循 core 的脱敏规则：凭据类键名被掩码，参数
与结果超过 2000 个 JSON 字符时替换为预览，traceback 被丢弃。除此之外参数按原样保存，
因此在普通参数中携带密钥的工具会让它出现在日志里。

`--execution-log` 可在没有密钥库时开启日志，`--no-execution-log` 关闭它；`gateway`、
`mcp` 和 `openapi` 都接受这两个参数。

### Python API

管理扩展（#187）在进程内导入同一个存储：

```python
from toolregistry_server import ApiKeyStore, PrincipalResolver

store = ApiKeyStore("keys.db")
created = store.create("build-bot", {"namespaces": ["calculator"]})
print(created.secret)  # 只显示一次

resolver = PrincipalResolver(store, operator_tokens=["bootstrap-token"])
principal = resolver.resolve(created.secret)
if principal is not None and principal.allows("calculator", "calculator-add"):
    ...
```

`ApiKeyRecord.to_dict()` 永远不包含密钥或其哈希。

## MCP Streamable-HTTP Bearer 认证（v0.4.0 新特性）

Bearer Token 认证现已支持 MCP Streamable-HTTP 传输，与 OpenAPI 一样。使用相同的 `--tokens` 参数（或给 `App().serve_mcp()` 传 `tokens_path`），无需额外配置。

```bash
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --port 8000 \
  --tokens tokens.txt
```

客户端使用标准的 `Authorization: Bearer <token>` 请求头连接。

## OAuth 2.0 资源服务器

服务器可以接受由外部授权服务器签发的访问令牌，而不是自己签发凭据。验证在本地完成：
令牌签名用授权服务器的 JWKS 校验，每次请求都不做内省，也不记录令牌。

```bash
toolregistry-server gateway \
  --config config.yaml \
  --oauth-issuer https://idp.example.com/realms/dev \
  --oauth-scope tools:read=calculator,web
```

| 选项 | 说明 |
|------|------|
| `--oauth-issuer URL` | 接受的精确 `iss` 值。设置后启用 OAuth 校验 |
| `--oauth-audience URL` | 期望的 `aud` 或 RFC 8707 `resource` 值，默认为对外公布的 MCP URL |
| `--oauth-jwks URL` | JWKS 端点（默认：从 issuer 的元数据中发现） |
| `--oauth-scope SCOPE=NAMESPACE[,NAMESPACE]` | 把一个作用域映射到命名空间模式；可重复 |
| `--oauth-scopes "SCOPE ..."` | 在受保护资源元数据中公布的作用域。为空时省略；配置 issuer 后默认省略 |

`openapi`、`mcp`（HTTP 传输）、`gateway` 与 `local-hub` 都接受这些选项。

### 校验规则

- 仅接受 RS256 与 ES256 签名。`none` 与 HMAC 算法会被拒绝，`kid` 未公布的令牌也会被拒绝。
- `iss` 必须与 `--oauth-issuer` 完全一致；令牌的 `aud` 或 RFC 8707 `resource` 声明必须包含期望的 audience。
- `exp` 与 `nbf` 生效，允许 60 秒时钟偏差。
- JWKS 端点优先取 `--oauth-jwks`。否则依次从 RFC 8414 元数据
  （`<issuer>/.well-known/oauth-authorization-server`）、OIDC 发现
  （`<issuer>/.well-known/openid-configuration`）读取，最后回退到
  `<issuer>/.well-known/jwks.json`。若提供方的 JWKS 未通过这两份元数据公布，必须显式传入该参数。
- JWKS 缓存 5 分钟。未知 `kid` 可触发重新拉取，但最多每 30 秒一次，因此随机 `kid` 无法驱动
  出站请求；轮换密钥在该窗口之后被接受。
- JWKS 拉取失败时拒绝令牌；启动时的预取是尽力而为，只记录警告。预取失败不会阻止第一个请求
  自己的拉取尝试。

### 作用域映射

映射遵循最小权限：作用域未出现在 `--oauth-scope` 中的令牌得到空作用域，不能调用任何工具。
每个条目把右侧的命名空间授予该作用域，模式规则与 API 密钥作用域相同（`*`、`web/*`、精确名称）：

```bash
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --oauth-issuer https://idp.example.com/realms/dev \
  --oauth-scope tools:read=calculator,web \
  --oauth-scope tools:admin=admin
```

### 凭据优先级

先尝试 `--keys-db` 的 API 密钥和静态 `--tokens`，再尝试 OAuth 令牌。因此只有当请求凭据
不匹配密钥或静态令牌时，才会按 OAuth 令牌解析。省略 `--oauth-issuer` 时行为完全不变。

在 MCP streamable-HTTP 上，RFC 9728 受保护资源元数据的 `authorization_servers` 会公布配置的
issuer，客户端据此发现授权服务器。被拒绝的请求直接携带该地址：
`WWW-Authenticate: Bearer resource_metadata="<元数据 URL>"`，URL 跟随当前 MCP 路径；
独立 MCP 服务器与 gateway 都会发送该质询。绑定 Unix socket 的 hub 没有可推导的 URL：请显式传入
`--oauth-audience`（若希望客户端发现元数据，还需传入 `--server-url`）。

配置外部 issuer 后，`scopes_supported` 变为显式开启：授权服务器会校验它签发的作用域，可能在
authorize 阶段拒绝通用的 `mcp` 作用域。请列出部署实际签发的作用域：

```bash
toolregistry-server gateway \
  --config config.yaml \
  --oauth-issuer https://idp.example.com/realms/dev \
  --oauth-scopes "openid profile email offline_access"
```

`--oauth-scopes ""` 会省略该字段。未配置 `--oauth-issuer` 时，元数据仍像以前一样公布 `mcp`。

### 限制

未实现令牌内省（RFC 7662）：部署方必须签发服务器可离线校验的 JWT。只签发不透明令牌的
授权服务器需要内省代理，或改用 `--keys-db`。

进程内 Python API：`toolregistry_server.oauth` 的 `OAuthSettings`、`OAuthTokenVerifier` 与
`build_resolver()` 构建与命令行相同的校验器和组合解析器。

## 发起认证请求

在 `Authorization` 请求头中携带 Bearer Token：

```bash
curl -X POST http://localhost:8000/calculator/evaluate \
  -H "Authorization: Bearer my-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"expression": "2 + 3"}'
```

## 运行时令牌管理（高级用法）

底层的 `BearerTokenAuth` 类支持在不重启服务器的情况下进行运行时令牌管理。这对于需要动态轮换令牌的高级场景非常有用：

```python
from toolregistry_server.auth import BearerTokenAuth

auth = BearerTokenAuth(tokens=["initial-token"])

# 添加新令牌
auth.add_token("new-token")

# 删除令牌
auth.remove_token("initial-token")

# 完全禁用认证
auth.enabled = False

# 重新启用
auth.enabled = True
```

## 未认证请求

当认证已启用时，没有有效令牌的请求将收到 `401 Unauthorized` 响应：

```json
{
  "detail": "Invalid or missing bearer token"
}
```

如果未配置任何令牌，认证将自动禁用，所有请求均被允许。
