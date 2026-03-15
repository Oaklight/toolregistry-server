# 快速开始

本指南将引导您使用 `toolregistry-server` 将工具暴露为服务的基本用法。

## 使用 RouteTable

`RouteTable` 是中央路由层，桥接 `ToolRegistry` 和协议适配器。

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable

# 创建注册表并注册工具
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名称问候某人。"""
    return f"Hello, {name}!"

@registry.register
def add(a: float, b: float) -> float:
    """两数相加。"""
    return a + b

# 创建路由表
route_table = RouteTable(registry)

# 列出所有路由
for route in route_table.list_routes():
    print(f"{route.path} -> {route.tool_name}")
```

## 创建 OpenAPI 服务器

使用 FastAPI 将工具暴露为 RESTful HTTP 端点：

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app

# 设置注册表和路由表
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名称问候某人。"""
    return f"Hello, {name}!"

route_table = RouteTable(registry)

# 创建 FastAPI 应用
app = create_openapi_app(route_table)

# 使用 uvicorn 运行
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

您的工具现在可以通过 `http://localhost:8000/` 的 POST 端点访问。

## 创建 MCP 服务器

通过模型上下文协议暴露工具，用于 LLM 集成：

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.mcp import create_mcp_server, run_streamable_http

# 设置注册表和路由表
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名称问候某人。"""
    return f"Hello, {name}!"

route_table = RouteTable(registry)

# 创建并运行 MCP 服务器
server = create_mcp_server(route_table)

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_streamable_http(server, host="0.0.0.0", port=8000))
```

## 使用命令行工具

无需编写代码即可启动服务器的最快方式：

```bash
# 启动 OpenAPI 服务器
toolregistry-server openapi --config config.json --port 8000

# 启动 MCP 服务器（stdio 传输）
toolregistry-server mcp --config config.json

# 启动 MCP 服务器（可流式 HTTP 传输）
toolregistry-server mcp --config config.json --transport streamable-http --port 8000
```

参见 [命令行工具参考](../cli/) 和 [配置指南](configuration.md) 了解配置文件格式的详细信息。

## 下一步

- [配置](configuration.md) - 了解 JSON/JSONC 配置文件
- [认证](authentication.md) - 设置 Bearer 令牌认证
- [OpenAPI 适配器](../adapters/openapi.md) - 深入了解 REST API 适配器
- [MCP 适配器](../adapters/mcp.md) - 深入了解 MCP 适配器
