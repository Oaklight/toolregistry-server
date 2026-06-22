---
title: 示例
---

# 示例

可运行的示例位于仓库的 [`examples/`](https://github.com/Oaklight/toolregistry-server/tree/master/examples) 目录中。以下代码片段供快速参考。

## 共享工具模块

所有示例从同一个共享模块导入工具：

```python title="examples/tools.py"
def add(a: float, b: float) -> float:
    """将两个数字相加。"""
    return a + b

def greet(name: str) -> str:
    """按名字问候某人。"""
    return f"Hello, {name}!"

def multiply(a: float, b: float) -> float:
    """将两个数字相乘。"""
    return a * b
```

## 编程方式 — OpenAPI 服务器

使用 `App.serve_openapi()` 一行启动服务器：

```python title="examples/openapi_server.py"
from toolregistry import ToolRegistry
from toolregistry_server import App
from tools import add, greet, multiply

registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

if __name__ == "__main__":
    App(registry=registry).serve_openapi(host="0.0.0.0", port=8000)
```

```bash
cd examples
python openapi_server.py
# Swagger UI → http://localhost:8000/docs
# 工具列表   → http://localhost:8000/tools
```

## 编程方式 — MCP 服务器

通过 stdio 暴露相同的工具，供 MCP 兼容客户端使用：

```python title="examples/mcp_server.py"
from toolregistry import ToolRegistry
from toolregistry_server import App
from tools import add, greet, multiply

registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

if __name__ == "__main__":
    App(registry=registry).serve_mcp(transport="stdio")
```

## 自定义 App 子类

重写 `prepare_registry()` 以注入完全自定义的注册表：

```python title="examples/custom_app.py"
from toolregistry import ToolRegistry
from toolregistry_server import App
from tools import add, greet, multiply

class MyApp(App):
    def prepare_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(add)
        registry.register(greet)
        registry.register(multiply)
        return registry

if __name__ == "__main__":
    MyApp().serve_openapi(host="0.0.0.0", port=8000)
```

## CLI 配置文件方式

CLI 可以从 JSON/JSONC 配置文件启动任一类型的服务器，无需编写 Python 代码：

```jsonc title="examples/config.jsonc"
{
  "mode": "denylist",
  "disabled": [],
  "tools": [
    {
      "type": "python",
      "module": "examples.tools",
      "namespace": "examples"
    }
  ]
}
```

```bash
# OpenAPI
PYTHONPATH=. toolregistry-server openapi --config examples/config.jsonc

# MCP (stdio)
PYTHONPATH=. toolregistry-server mcp --config examples/config.jsonc

# MCP (Streamable HTTP)
PYTHONPATH=. toolregistry-server mcp --config examples/config.jsonc \
    --transport streamable-http --port 8000
```

参见 [配置](../guides/configuration.md) 了解完整配置文件参考，以及 [CLI 参考](../reference/cli/) 了解所有命令行选项。
