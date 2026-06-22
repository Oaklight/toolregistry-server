# 快速入门

本指南带您了解使用 `toolregistry-server` v0.4.0 API 将工具发布为服务的基本用法。

## 编程方式：OpenAPI 服务器

使用 `App` 类将工具以 RESTful HTTP 端点的形式发布：

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

class MyApp(App):
    def prepare_registry(self):
        registry = ToolRegistry()

        @registry.register
        def greet(name: str) -> str:
            """按名字问候某人。"""
            return f"你好，{name}！"

        @registry.register
        def add(a: float, b: float) -> float:
            """将两个数字相加。"""
            return a + b

        self.registry = registry

if __name__ == "__main__":
    MyApp().serve_openapi(host="0.0.0.0", port=8000)
```

您的工具现在可以通过 `http://localhost:8000/` 的 POST 端点访问。

无需继承子类的简洁写法：

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

registry = ToolRegistry()
registry.register(my_tool)

App(registry=registry).serve_openapi(host="0.0.0.0", port=8000)
```

## 编程方式：MCP 服务器

使用 `App.serve_mcp()` 通过模型上下文协议（MCP）发布工具：

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名字问候某人。"""
    return f"你好，{name}！"

if __name__ == "__main__":
    # stdio 传输（默认，适用于 Claude Desktop 等）
    App(registry=registry).serve_mcp()

    # 或使用 streamable-http 传输
    # App(registry=registry).serve_mcp(transport="streamable-http", host="0.0.0.0", port=8000)
```

使用底层适配器的一步调用方式：

```python
from toolregistry_server.adapters.mcp import MCPAdapter

MCPAdapter.create_and_run(registry=registry, transport="stdio")
```

## 使用命令行工具

无需编写代码即可快速启动服务器：

```bash
# 启动 OpenAPI 服务器
toolregistry-server openapi --config config.json --port 8000

# 启动 MCP 服务器（stdio 传输，默认）
toolregistry-server mcp --config config.json

# 启动 MCP 服务器（streamable-http 传输）
toolregistry-server mcp --config config.json --transport streamable-http --port 8000

# 启动 MCP 服务器（SSE 传输）
toolregistry-server mcp --config config.json --transport sse --port 8000

# 带 Bearer Token 认证
toolregistry-server openapi --config config.json --tokens tokens.txt
```

有关配置文件格式和所有可用参数的详细说明，请参阅 [CLI 参考](../reference/cli/) 和 [配置指南](../guides/configuration.md)。

## 后续步骤

- [示例](../examples/) — 可运行的脚本和配置片段
- [配置](../guides/configuration.md) — 了解 JSON/JSONC 配置文件
- [认证](../guides/authentication.md) — 配置 Bearer Token 认证
- [OpenAPI 适配器](../guides/openapi.md) — REST API 适配器深度解析
- [MCP 适配器](../guides/mcp.md) — MCP 适配器深度解析
