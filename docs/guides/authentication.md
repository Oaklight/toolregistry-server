# 认证

`toolregistry-server` 为 OpenAPI 和 MCP Streamable-HTTP 端点提供内置的 Bearer Token 认证。

## 概述

认证模块支持：

- 从文件加载多个令牌
- 对所有传入请求进行 Bearer Token 验证
- 运行时令牌管理（无需重启服务器即可添加/删除令牌）
- 动态启用/禁用

## 配置认证

### 编程方式 — 使用 `App`（推荐）

使用 `auth.load_tokens()` 从文件加载令牌，并传递给 `App`：

```python
from toolregistry import ToolRegistry
from toolregistry_server import App
from toolregistry_server.auth import load_tokens

registry = ToolRegistry()
registry.register(my_tool)

tokens = load_tokens("tokens.txt")   # 每行一个令牌，# 开头为注释
App(registry=registry, tokens=tokens).serve_openapi(host="0.0.0.0", port=8000)
```

同样的方式适用于 MCP Streamable-HTTP：

```python
App(registry=registry, tokens=tokens).serve_mcp(
    transport="streamable-http", host="0.0.0.0", port=8000
)
```

直接内联传入令牌（无需文件）：

```python
App(registry=registry, tokens=["token-one", "token-two"]).serve_openapi()
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

## MCP Streamable-HTTP Bearer 认证（v0.4.0 新特性）

Bearer Token 认证现已支持 MCP Streamable-HTTP 传输，与 OpenAPI 一样。使用相同的 `--tokens` 参数或 `App(tokens=...)` API，无需额外配置。

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
