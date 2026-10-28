# MCP 适配器

MCP 适配器通过 [模型上下文协议](https://modelcontextprotocol.io/) 将 `ToolRegistry` 工具暴露给 LLM 集成。

## 概述

适配器：

- 注册 `list_tools` 和 `call_tool` MCP 处理程序，在请求时从 `RouteTable` 读取，保持实时同步
- 支持 stdio、SSE、Streamable HTTP 多种传输方式
- 透明处理异步和同步工具
- 提供阻塞式 (`run`) 和异步 (`run_async`) 两种入口

## 服务器标识与使用说明

`initialize` 的 `serverInfo.version` 报告当前安装的 `toolregistry-server` 版本。需要自定义品牌的嵌入方会保留自己的版本：`App` 与 CLI 会转发 `ServerIdentity.version`，因此 Hub 声明的是 Hub 的版本。

当服务提供 `discover_tools`（见下文）时，`initialize` 响应还会带上 `instructions`，说明两步式延迟工具流程，客户端无需再从工具描述中自行推断。这两个字段都可以直接指定：

```python
from toolregistry_server.adapters.mcp import MCPAdapter

adapter = MCPAdapter(
    route_table,
    version="1.2.3",
    instructions="数学工具可直接使用，其余工具为延迟工具。",
)
```

## 延迟工具与 `call_deferred`

当底层 `ToolRegistry` 启用了工具发现（通过 `enable_tool_discovery()`）时，两个基础设施工具会自动通过 MCP 适配器暴露：

- **`discover_tools`** — 基于 BM25 的模糊搜索，覆盖所有已注册工具。对延迟工具返回完整 schema。
- **`call_deferred`** — 代理工具，通过名称调用延迟工具。接受 `_target_tool` 参数并将 `**kwargs` 转发给目标工具。

延迟工具（`metadata.defer=True`）不会出现在 `tools/list` 中，但 LLM 在通过 `discover_tools` 发现其 schema 后，可通过 `call_deferred` 调用。这实现了大型工具目录的渐进式暴露，避免初始工具列表过于庞大。

## 实时工具更新

MCP 适配器声明了 `tools_changed` 能力，并在路由表发生变更（工具注册、注销、启用或禁用）时自动向所有已连接的会话发送 `notifications/tools/list_changed` 通知。支持此通知的客户端可以重新获取 `tools/list` 以得到最新的工具目录。

### 缓存工具列表

在 SDK v2（MCP 规范 2026-07-28）上，`tools/list` 会携带缓存提示。默认 `ttlMs: 300000`——五分钟——`cacheScope` 为 `"private"`，因为实际提供的工具集合取决于部署的 profile 与调用方的授权。

打开 `subscriptions/listen` 流的 modern era 客户端会在每次变更时使缓存失效（SDK 客户端会在收到事件时清除 `tools/list` 缓存），因此始终能看到最新的工具集合。**不**监听变更的客户端则会在 TTL 内一直使用缓存列表——这正是该默认值所做的权衡，也是为什么它是一个有界值而不是无限期缓存。

```bash
# 对于从不监听变更的客户端，关闭缓存
toolregistry-server mcp --config tools.yaml --transport http --tools-list-ttl-ms 0

# 更短、但仍按调用方区分的缓存
toolregistry-server mcp --config tools.yaml --transport http \
  --tools-list-ttl-ms 60000 --tools-list-cache-scope private
```

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
    server_url="https://tools.example.com/mcp",
)
```

```bash
# CLI 等效写法
toolregistry-server mcp --config tools.yaml --transport http \
  --tokens /etc/myapp/tokens.txt \
  --server-url https://tools.example.com/mcp
```

### 部署在反向代理之后

启用令牌认证后，服务器会发布 RFC 9728 的 protected-resource 元数据，并在 401 响应的 `WWW-Authenticate` 头中指向该文档。这个 URL 必须是客户端可访问的，因此应当是公网源（origin），而不是监听地址：

```bash
toolregistry-server mcp --config tools.yaml --transport http \
  --host 0.0.0.0 --port 8000 \
  --server-url https://tools.example.com/mcp
```

未指定 `--server-url` 时，URL 由 `--host`/`--port` 推导。只有在客户端直连服务器时这才是正确的：绑定 `0.0.0.0`（容器/代理部署的常见做法）会对外声明 `http://0.0.0.0:8000/...`，客户端无法解析，同时也向未认证的调用方暴露了服务器的绑定方式。若即将声明的正是这种地址，服务器会在启动时输出警告。

反向代理还需要转发发现路径，因为客户端会从同一源获取这些文档：除 `/mcp` 外，还要转发 `/.well-known/oauth-protected-resource/mcp`。

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

## 工具注解

当工具元数据声明了显示标题或执行提示时，`tools/list` 会以 MCP 的 `title` 与 `annotations` 字段返回：

- `ToolMetadata.title` → `title`
- `Tool.declared_execution_hints` → `annotations`（`readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint`）

未声明的提示保持缺省：没有做出任何声明的工具仍保持最小的 `{name, description, inputSchema}` 定义，由宿主套用协议默认值。默认只发布显式声明：`execution_hints` 还会从 `read_only` / `destructive` 标签推导值，而这些标签可能是为本地权限策略选择的，并非面向客户端的安全声明。如需同时发布标签推导值，请使用 `RouteTable(..., emit_derived_execution_hints=True)`。可在代码中或配置文件中设置显式提示：

```yaml
tool_metadata:
  datetime-now:
    title: Current Date and Time
    read_only_hint: true
    idempotent_hint: true
    open_world_hint: false
```

!!! note "标签与提示是两个层次"
    标签（`read_only`、`file_system`、`network` 等）驱动本地权限规则与部署 profile：`remote` profile 会禁用 `file_system`、`destructive` 与 `privileged` 工具。提示描述的是面向 MCP 宿主的单次调用。方法级提示不会改变 profile 的暴露范围——只读的文件工具在 `remote` 部署下仍会被隐藏。

## 错误语义

`tools/call` 以「模型能否自行修复」为界：服务端无法派发的名字属于请求错误，而模型可以修正或能从中获得信息的情况则保持为工具结果：

| 情形 | 客户端看到的内容 |
|------|------------------|
| 工具名不存在，或工具处于禁用状态 | JSON-RPC 错误 `-32602`（Invalid params） |
| 参数不合法（缺少必填字段、类型错误） | `CallToolResult`，其中 `isError: true`，错误文本作为 content |
| 工具在执行过程中抛出异常 | `CallToolResult`，其中 `isError: true`，错误文本作为 content |
| `call_deferred` 的 `_target_tool` 不存在 | `CallToolResult`，其中 `isError: true` |

协议错误意味着调用本身无效，因此不应作为工具结果回传给模型。执行错误则对模型可见：LLM 可以读到错误信息并重试或换一种做法。被禁用的工具会在错误信息中给出禁用原因。

参数不合法同样属于请求层面的问题，但这里刻意**不**作为协议错误返回：错误文本（`Expected int at 'a', got str`）正是模型自我纠正的依据。同理，`call_deferred` 分发器的回复也保持为工具结果——它的文本是面向模型的提示（`tool 'ghost' not found in registry. Did you mean: …`）。因此，如果宿主以 `-32602` 作为判断依据，直接调用时「这个名字在这里不是工具」是一种形态，而经 `call_deferred` 分发时则是另一种形态。

## API 参考

参见 [MCP API 参考](../reference/api/mcp.md) 获取详细文档。
