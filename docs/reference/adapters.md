---
title: 适配器
---

# 协议适配器

`toolregistry-server` 提供协议适配器，将您的自定义工具暴露为网络服务。每个适配器从中央 `RouteTable` 读取数据，并将工具定义转换为特定协议的端点。

!!! tip "推荐入口点"
    大多数情况下，请优先使用 [`App`](../guides/extending.md) 类，而非直接操作适配器层。`App` 会自动完成 `ToolRegistry → RouteTable → Adapter` 的串联，并通过 `serve_openapi()` / `serve_mcp()` 提供一行式调用。本文档所描述的适配器层是 `App` 在内部委托的实现细节。

## 可用适配器

| 适配器 | 协议 | 传输方式 | 状态 |
|--------|------|----------|------|
| [OpenAPI](../guides/openapi.md) | REST/HTTP | HTTP | 稳定 |
| [MCP](../guides/mcp.md) | 模型上下文协议 | stdio、SSE、可流式 HTTP | 稳定 |
| gRPC | gRPC | HTTP/2 | 计划中 |

## 适配器工作原理

所有适配器共享相同的流程，`App` 作为编排入口点位于适配器层之上：

```
App → RouteTable → 适配器 → 协议特定端点
 ↑
CLI（可选，位于 App 之上）
```

1. 工具在 `ToolRegistry` 实例中注册
2. `App` 从注册表构建 `RouteTable`，并分发到相应的适配器
3. `RouteTable` 从注册表生成 `RouteEntry` 对象
4. 适配器读取 `RouteEntry` 对象并创建协议特定端点
5. 客户端通过适配器的协议与工具交互

## 直接使用适配器

当需要精细控制时，可以绕过 `App`，通过 `Adapter.create_and_run()` 直接调用适配器层：

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import OpenAPIAdapter

registry = ToolRegistry()
# ... 注册工具 ...

route_table = RouteTable(registry)

# 一次性静态分发——等同于 App().serve_openapi()
OpenAPIAdapter.create_and_run(route_table, host="0.0.0.0", port=8000)
```

MCP 同理：

```python
from toolregistry_server.mcp import MCPAdapter

MCPAdapter.create_and_run(route_table, transport="stdio")
```

## 动态行为

适配器在请求时从 `RouteTable` 读取数据，这意味着：

- **启用/禁用**：工具可以在运行时切换，无需重启服务器
- **无漂移**：适配器始终反映 `RouteTable` 的当前状态
- **观察者模式**：适配器可以通过监听器订阅 `RouteTable` 的变化
