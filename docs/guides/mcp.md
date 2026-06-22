# MCP 适配器

MCP 适配器通过 [模型上下文协议](https://modelcontextprotocol.io/) 将 `ToolRegistry` 工具暴露给 LLM 集成。

## 概述

适配器：

- 注册 `list_tools` 和 `call_tool` MCP 处理程序，在请求时从 `RouteTable` 读取，保持实时同步
- 支持 stdio、SSE、Streamable HTTP 多种传输方式
- 透明处理异步和同步工具
- 提供阻塞式 (`run`) 和异步 (`run_async`) 两种入口

## 快速开始

### 通过 `App`（推荐）

```python
from toolregistry_server.app import App

# 从配置文件启动
App().serve_mcp(config_path="tools.yaml", transport="stdio")
App().serve_mcp(config_path="tools.yaml", transport="sse", host="0.0.0.0", port=8000)
App().serve_mcp(config_path="tools.yaml", transport="http", host="0.0.0.0", port=8000)
```

### 直接使用 `MCPAdapter`

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp import MCPAdapter

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名称问候某人。"""
    return f"Hello, {name}!"

route_table = RouteTable(registry)
adapter = MCPAdapter(route_table)

# 阻塞式运行（适合脚本 / CLI）
adapter.run(transport="stdio")
adapter.run(transport="sse", host="0.0.0.0", port=8000)
adapter.run(transport="http", host="0.0.0.0", port=8000)
```

### 异步入口

已在事件循环内时使用 `run_async`：

```python
import asyncio
from toolregistry_server.adapters.mcp import MCPAdapter

adapter = MCPAdapter(route_table)
asyncio.run(adapter.run_async(transport="sse", host="0.0.0.0", port=8000))

# 或在已有事件循环中：
await adapter.run_async(transport="stdio")
```

### 访问底层 MCP Server 实例

```python
from toolregistry_server.adapters.mcp import MCPAdapter, run_stdio

adapter = MCPAdapter(route_table)
server = adapter.server   # mcp.server.lowlevel.Server 实例
asyncio.run(run_stdio(server))
```

## 传输方式比较

| 传输方式 | 别名 | 使用场景 |
|----------|------|----------|
| `stdio` | — | 子进程模型（Claude Desktop、IDE 插件） |
| `sse` | — | 基于 SSE 的 HTTP 客户端 |
| `streamable-http` | `http` | 生产环境 HTTP 部署 |

`http` 是 `streamable-http` 的别名，内部会自动归一化。

## 认证（Streamable HTTP）

通过 `tokens_path` 指定 Bearer token 文件，或设置 `API_BEARER_TOKEN` 环境变量（逗号分隔）：

```python
adapter.run(
    transport="http",
    host="0.0.0.0",
    port=8000,
    tokens_path="/etc/myapp/tokens.txt",
)
```

```bash
# CLI 等效写法
toolregistry-server mcp --config tools.yaml --transport http --tokens tokens.txt
```

## MCP 客户端配置

### Claude Desktop（stdio）

```json
{
  "mcpServers": {
    "my-tools": {
      "command": "toolregistry-server",
      "args": ["mcp", "--config", "/path/to/tools.yaml"]
    }
  }
}
```

### 基于 HTTP 的客户端

连接到：

```
http://localhost:8000/mcp       # streamable-http
http://localhost:8000/sse       # SSE
```

## API 参考

参见 [MCP API 参考](../reference/api/mcp.md) 获取详细文档。
