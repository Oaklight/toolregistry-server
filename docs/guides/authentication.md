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
```

密钥数据库可以与 `--tokens` 同时使用。静态 `--tokens` 令牌保持完整、不受限的访问权限，属于引导/兼容路径；作用域只适用于数据库中创建的密钥。MCP 的 `stdio` 传输不携带请求凭据，因此会忽略 `--keys-db`。

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

限定作用域的密钥不会获得 `discover_tools` / `call_deferred` 代理工具对。`call_deferred` 可以按名称调用任意已注册工具，若保持可达，密钥就能绕过作用域；因此两者都不会出现在列表和文档中，调用也会被拒绝。它们仍对运维/静态令牌 principal 可用。作用域感知的发现功能将在后续实现。

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
