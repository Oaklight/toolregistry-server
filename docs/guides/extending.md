---
title: 扩展 toolregistry-server
---

# 扩展 toolregistry-server

`toolregistry-server` 的设计目标是**可被下游包嵌入和扩展**（例如 `toolregistry-hub`）。无需 fork 或 patch 库本身，您可以通过三个主要扩展点构建上层应用：

| 扩展点 | 用途 |
|--------|------|
| `App` 子类化 | 注入自定义注册表；重写启动逻辑 |
| `CLI` 子类化 | 添加命令行标志，无需触碰 argparse 内部实现 |
| `ServerIdentity` | 自定义在启动横幅和 `--version` 中显示的名称/版本 |

---

## 子类化 `App`

`App` 是规范的编程入口点。它构建 `RouteTable` 并分发到相应的适配器。重写 `prepare_registry()` 以注入您自己的工具：

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

class HubApp(App):
    """加载 toolregistry-hub 工具的 App。"""

    def prepare_registry(self) -> ToolRegistry:
        from toolregistry_hub import load_hub_tools  # 您的包

        registry = ToolRegistry()
        load_hub_tools(registry)
        return registry

# 在 8000 端口启动 OpenAPI 服务器
HubApp().serve_openapi(host="0.0.0.0", port=8000)

# 或启动 MCP 服务器（stdio）
HubApp().serve_mcp(transport="stdio")
```

`prepare_registry()` 在启动时调用一次，返回的 `ToolRegistry` 用于构建 `RouteTable`。

---

## 子类化 `CLI`

`CLI` 将 `App` 包装为完整的命令行参数解析循环。通过子类化，可以向内置的 `openapi` 和 `mcp` 子命令添加自定义标志：

```python
from argparse import ArgumentParser
from toolregistry_server import CLI, ServerIdentity
from my_package import HubApp  # 您的 App 子类

class HubCLI(CLI):
    """添加 --admin-port 标志的自定义 CLI。"""

    def configure_subparsers(self, subparsers: dict[str, ArgumentParser]) -> None:
        # subparsers 的键："openapi"、"mcp"
        for name, parser in subparsers.items():
            parser.add_argument(
                "--admin-port",
                type=int,
                default=9000,
                help="管理界面端口",
            )

if __name__ == "__main__":
    identity = ServerIdentity(
        name="Hub Server",
        version="1.0.0",
        description="带管理界面的 toolregistry-hub 服务器",
    )
    HubCLI(app=HubApp(), identity=identity).run()
```

### `configure_subparsers` 接收的内容

`subparsers` 是一个普通的 `dict[str, ArgumentParser]`，其中每个键是子命令名称（`"openapi"`、`"mcp"`）。您可以对值调用任意标准 `ArgumentParser` 方法——`add_argument`、`set_defaults` 等——而无需触碰 argparse 内部实现或重新定义已有标志。

---

## `ServerIdentity`

`ServerIdentity` 控制启动横幅和 `--version` 标志中显示的名称和版本字符串：

```python
from toolregistry_server import ServerIdentity

identity = ServerIdentity(
    name="My Tool Server",
    version="2.1.0",
    description="由 toolregistry-server 驱动",
)
```

将其传给 `App.__init__` 或 `CLI.__init__`：

```python
# 通过 App
app = HubApp(identity=identity)
app.serve_openapi(port=8000)

# 通过 CLI（优先级更高）
HubCLI(app=HubApp(), identity=identity).run()
```

---

## `run_cli()` 独立辅助函数

如果您更倾向于**组合而非继承**，可使用 `run_cli()` 启动 CLI 循环，无需子类化：

```python
from toolregistry_server import run_cli, ServerIdentity
from my_package import HubApp

run_cli(
    app=HubApp(),
    identity=ServerIdentity(name="Hub Server", version="1.0.0"),
)
```

`run_cli()` 等同于 `CLI(app=..., identity=...).run()`，但在您只需要自定义标识或预构建 `App` 时，无需定义 `CLI` 子类。

---

## 整合示例

以下是一个完整的迷你示例，展示了下游包（如 `toolregistry-hub`）的典型实现方式：

```python
# my_hub_server/__main__.py
from argparse import ArgumentParser
from toolregistry import ToolRegistry
from toolregistry_server import App, CLI, ServerIdentity

# 1. 自定义 App——加载 hub 工具
class HubApp(App):
    def prepare_registry(self) -> ToolRegistry:
        from toolregistry_hub import load_hub_tools

        registry = ToolRegistry()
        load_hub_tools(registry)
        return registry

# 2. 自定义 CLI——添加 --admin-port
class HubCLI(CLI):
    def configure_subparsers(self, subparsers: dict[str, ArgumentParser]) -> None:
        for parser in subparsers.values():
            parser.add_argument(
                "--admin-port",
                type=int,
                default=9000,
                metavar="PORT",
                help="管理界面端口（默认：9000）",
            )

# 3. 串联所有组件
IDENTITY = ServerIdentity(
    name="toolregistry-hub",
    version="1.0.0",
    description="通过 OpenAPI 和 MCP 提供精选工具服务",
)

def main() -> None:
    HubCLI(app=HubApp(), identity=IDENTITY).run()

if __name__ == "__main__":
    main()
```

使用方式与内置 CLI 完全相同：

```bash
# 带自定义管理端口的 OpenAPI 服务器
python -m my_hub_server openapi --port 8000 --admin-port 9000

# 通过 stdio 的 MCP 服务器
python -m my_hub_server mcp --transport stdio
```

---

## 扩展应用中的认证

使用 `auth.load_tokens()` 从文件加载 Bearer 令牌，并将其传给 `App` 或直接传给适配器：

```python
from toolregistry_server.auth import load_tokens

tokens = load_tokens("/etc/my-server/tokens.txt")
HubApp(tokens=tokens).serve_openapi(port=8000)
```

完整的令牌文件格式参考请参见 [认证](authentication.md)。
