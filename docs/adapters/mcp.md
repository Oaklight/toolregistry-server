# MCP 适配器

MCP 适配器通过 [模型上下文协议](https://modelcontextprotocol.io/) 将 `ToolRegistry` 工具暴露给 LLM 集成。

## 概述

适配器：

- 注册 `list_tools` 和 `call_tool` MCP 处理程序
- 支持多种传输机制（stdio、SSE、可流式 HTTP）
- 在请求时从 `RouteTable` 读取数据，实现实时同步
- 透明处理异步和同步工具
- 将结果序列化为 JSON 兼容字符串

## 快速开始

### 可流式 HTTP 传输（推荐）

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.mcp import create_mcp_server, run_streamable_http
import asyncio

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名称问候某人。"""
    return f"Hello, {name}!"

route_table = RouteTable(registry)
server = create_mcp_server(route_table)

asyncio.run(run_streamable_http(server, host="0.0.0.0", port=8000))
```

### stdio 传输

用于基于子进程的通信（Claude Desktop 等使用）：

```python
from toolregistry_server.mcp import create_mcp_server, run_stdio
import asyncio

server = create_mcp_server(route_table)
asyncio.run(run_stdio(server))
```

### SSE 传输

用于基于 HTTP 的 Server-Sent Events：

```python
from toolregistry_server.mcp import create_mcp_server, run_sse
import asyncio

server = create_mcp_server(route_table)
asyncio.run(run_sse(server, host="0.0.0.0", port=8000))
```

## 传输方式比较

| 传输方式 | 使用场景 | 协议 |
|----------|----------|------|
| **可流式 HTTP** | 生产环境 Web 部署 | HTTP |
| **SSE** | 需要实时更新的 Web 客户端 | HTTP + SSE |
| **stdio** | 子进程模型（Claude Desktop、IDE 插件） | stdin/stdout |

## MCP 客户端配置

### Claude Desktop

添加到 Claude Desktop 配置中：

```json
{
  "mcpServers": {
    "my-tools": {
      "command": "toolregistry-server",
      "args": ["mcp", "--config", "config.json"]
    }
  }
}
```

### 基于 HTTP 的客户端

连接到服务器 URL：

```
http://localhost:8000/mcp
```

## 错误处理

工具错误作为结构化 MCP 错误响应返回，包含适当的错误代码。

## API 参考

参见 [MCP API 参考](../api/mcp.md) 获取详细文档。
