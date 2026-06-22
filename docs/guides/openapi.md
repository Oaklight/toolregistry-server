# OpenAPI 适配器

OpenAPI 适配器使用 [FastAPI](https://fastapi.tiangolo.com/) 将 `ToolRegistry` 工具暴露为 RESTful HTTP 端点。

## 概述

适配器自动：

- 为每个注册的工具创建 POST 端点
- 从 JSON Schema 参数生成动态 Pydantic 模型
- 生成 OpenAPI 模式（可在 `/openapi.json` 访问）
- 提供 `/tools` 元数据端点，列出可用工具
- 支持运行时启用/禁用单个工具
- 实现基于 ETag 的 HTTP 缓存

## 快速开始

### 通过 `App`（推荐）

```python
from toolregistry_server.app import App

# 从配置文件启动
App().serve_openapi(config_path="tools.yaml", host="0.0.0.0", port=8000)

# 使用已构建好的 registry
from toolregistry import ToolRegistry
registry = ToolRegistry()
# ... 注册工具 ...
App().serve_openapi(registry=registry, port=9000)
```

### 直接使用 `OpenAPIAdapter`

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import OpenAPIAdapter

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名称问候某人。"""
    return f"Hello, {name}!"

route_table = RouteTable(registry)

adapter = OpenAPIAdapter(
    route_table,
    title="My Tool Server",
    version="1.0.0",
    tokens=["secret-token"],   # 可选 Bearer 认证
)
adapter.run(host="0.0.0.0", port=8000)
```

### 一步构建并运行

```python
from toolregistry_server.adapters.openapi import OpenAPIAdapter

OpenAPIAdapter.create_and_run(
    route_table,
    host="0.0.0.0",
    port=8000,
    tokens_path="/etc/myapp/tokens.txt",
)
```

## 端点结构

每个工具作为 POST 端点暴露在其路由路径上：

```
POST /{namespace}/{tool_name}
```

例如，命名空间 `calculator` 中的工具 `evaluate` 变为：

```
POST /calculator/evaluate
```

### 请求格式

参数作为 JSON 主体传递：

```bash
curl -X POST http://localhost:8000/calculator/evaluate \
  -H "Content-Type: application/json" \
  -d '{"expression": "2 + 3 * 4"}'
```

### 响应格式

```json
{"result": 14}
```

## 工具元数据端点

`GET /tools` 返回所有可用工具及其模式：

```bash
curl http://localhost:8000/tools
```

## 认证

通过 token 文件或 `API_BEARER_TOKEN` 环境变量设置 Bearer 认证：

```python
from toolregistry_server.adapters.openapi import OpenAPIAdapter
from toolregistry_server.auth import load_tokens

tokens = load_tokens(tokens_path="/etc/myapp/tokens.txt")
adapter = OpenAPIAdapter(route_table, tokens=tokens or None)
adapter.run(host="0.0.0.0", port=8000)
```

```bash
# CLI
toolregistry-server openapi --config tools.yaml --tokens tokens.txt
```

## 禁用的工具

当工具在运行时被禁用时，其端点返回 `503 Service Unavailable`：

```json
{"detail": "Tool 'calculator_evaluate' is currently disabled"}
```

禁用的工具也会从动态 OpenAPI 模式中排除。

## ETag 缓存

适配器包含 `ETagMiddleware`，为 `/tools` 和 `/openapi.json` 端点提供 HTTP 缓存：

- 每个响应包含 `ETag` 头
- 客户端可以发送 `If-None-Match` 进行条件请求
- ETag 匹配时返回 `304 Not Modified`

## API 参考

参见 [OpenAPI API 参考](../reference/api/openapi.md) 获取详细文档。
