---
title: 适配器
---

# 协议适配器

`toolregistry-server` 提供协议适配器，将 `ToolRegistry` 中注册的工具暴露为网络服务。每个适配器从中央 `RouteTable` 读取数据，并将工具定义转换为特定协议的端点。

## 可用适配器

| 适配器 | 协议 | 传输方式 | 状态 |
|--------|------|----------|------|
| [OpenAPI](openapi.md) | REST/HTTP | HTTP | 稳定 |
| [MCP](mcp.md) | 模型上下文协议 | stdio、SSE、可流式 HTTP | 稳定 |
| gRPC | gRPC | HTTP/2 | 计划中 |

## 适配器工作原理

所有适配器共享相同的流程：

```
ToolRegistry → RouteTable → 适配器 → 协议特定端点
```

1. 工具在 `ToolRegistry` 实例中注册
2. `RouteTable` 从注册表生成 `RouteEntry` 对象
3. 适配器读取 `RouteEntry` 对象并创建协议特定端点
4. 客户端通过适配器的协议与工具交互

## 动态行为

适配器在请求时从 `RouteTable` 读取数据，这意味着：

- **启用/禁用**：工具可以在运行时切换，无需重启服务器
- **无漂移**：适配器始终反映 `RouteTable` 的当前状态
- **观察者模式**：适配器可以通过监听器订阅 `RouteTable` 的变化
