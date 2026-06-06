# 认证

`toolregistry-server` 提供内置的 Bearer 令牌认证，用于保护 OpenAPI 端点。

## 概述

认证模块通过 FastAPI 的依赖注入系统使用 HTTP Bearer 令牌认证。它支持：

- 多令牌
- 运行时令牌管理（添加/移除令牌）
- 动态启用/禁用，无需重启服务器

## 设置认证

### 通过代码

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app
from toolregistry_server.auth import BearerTokenAuth, create_bearer_dependency

# 设置注册表和路由表
registry = ToolRegistry()
route_table = RouteTable(registry)

# 创建带令牌的认证
auth = BearerTokenAuth(tokens=["my-secret-token", "another-token"])
bearer_dep = create_bearer_dependency(auth)

# 创建带认证的应用
app = create_openapi_app(route_table, dependencies=[bearer_dep])
```

### 通过命令行

```bash
# 单个令牌
toolregistry-server openapi --config config.json --auth-token "your-secret-token"

# 令牌文件（每行一个令牌）
toolregistry-server openapi --config config.json --auth-tokens-file tokens.txt
```

令牌文件格式：

```
token-one
token-two
token-three
```

## 发送认证请求

在 `Authorization` 头中包含 Bearer 令牌：

```bash
curl -X POST http://localhost:8000/calculator/evaluate \
  -H "Authorization: Bearer my-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"expression": "2 + 3"}'
```

## 运行时令牌管理

`BearerTokenAuth` 类支持运行时令牌管理：

```python
auth = BearerTokenAuth(tokens=["initial-token"])

# 添加新令牌
auth.add_token("new-token")

# 移除令牌
auth.remove_token("initial-token")

# 完全禁用认证
auth.enabled = False

# 重新启用
auth.enabled = True
```

## 未认证请求

当认证启用时，没有有效令牌的请求会收到 `401 Unauthorized` 响应：

```json
{
  "detail": "Invalid or missing bearer token"
}
```

如果没有配置令牌，认证会自动禁用，所有请求都被允许。
