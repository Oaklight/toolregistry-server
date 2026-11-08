---
title: CLI 参考
---

# 命令行界面

`toolregistry-server` 提供命令行工具，无需编写自定义代码即可运行服务器。

## 用法

```bash
toolregistry-server [选项] <子命令> [子命令选项]
```

## 顶层选项

| 选项 | 说明 |
|------|------|
| `--version`, `-V` | 显示版本并退出 |
| `--no-banner` | 禁用启动横幅 |

## 子命令通用选项

| 选项 | 说明 |
|------|------|
| `--env PATH` | .env 文件路径（默认：当前目录下的 `.env`）|
| `--no-env` | 跳过加载 .env 文件 |
| `--config PATH` | JSONC 或 YAML 配置文件路径 |
| `--profile PROFILE` | 部署配置：`remote`、`local`，或配置文件 `profiles:` 中声明的名称 |

## 子命令

### `openapi` — 启动 OpenAPI 服务器

```bash
toolregistry-server openapi [选项]
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--config PATH` | - | JSONC 或 YAML 配置文件路径（必需） |
| `--host HOST` | `0.0.0.0` | 绑定主机 |
| `--port PORT` | `8000` | 绑定端口 |
| `--tokens PATH` | - | Bearer 令牌文件，每行一个（`#` 开始注释）。令牌以恒定时间比较；同时读取 `API_BEARER_TOKEN`（逗号分隔） |
| `--backend {fastapi,httpserver}` | `fastapi` | HTTP 后端：`fastapi`（uvicorn）或内联的零依赖 `httpserver` |
| `--max-body-size BYTES` | `1048576`（1 MiB） | 接受的最大请求体。必须为正数；超限返回带 JSON 错误的 `413` |
| `--reload` | `false` | 启用热重载（仅 FastAPI/uvicorn；`httpserver` 上记录警告并忽略） |
| `--profile PROFILE` | - | 部署配置：`remote`、`local`，或配置文件 `profiles:` 中声明的名称 |
| `--regex-timeout SECONDS` | `0.05` | 来自 spec 的正则单次匹配上限（需要 `regex` extra）。`0` 禁用执行 |
| `--regex-budget SECONDS` | `0.25` | 单次请求内所有 spec 正则匹配共享的上限。`0` 禁用执行 |
| `--strict-spec-regex` | `false` | OpenAPI source 含非法正则时终止启动；默认只丢弃自身 schema 含该正则的工具 |

**示例：**

```bash
toolregistry-server openapi \
  --config config.yaml \
  --port 8000 \
  --tokens tokens.txt \
  --profile remote
```

### `mcp` — 启动 MCP 服务器

```bash
toolregistry-server mcp [选项]
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--config PATH` | - | JSON、JSONC 或 YAML 配置文件路径（必需） |
| `--transport TYPE` | `stdio` | 传输类型：`stdio`、`sse`、`streamable-http` 或 `http` |
| `--host HOST` | `127.0.0.1` | 绑定主机（SSE/HTTP 传输） |
| `--port PORT` | `8000` | 绑定端口（SSE/HTTP 传输） |
| `--tokens PATH` | - | Bearer 令牌文件，每行一个，用于 `streamable-http`/`http`。同时读取 `API_BEARER_TOKEN`（逗号分隔） |
| `--server-url URL` | 由 `--host`/`--port` 推导 | 在 OAuth 受保护资源元数据中公布的公开 URL（反向代理后必需） |
| `--tools-list-ttl-ms MS` | `300000` | `tools/list` 的 `ttlMs` 缓存提示；`0` 禁用缓存 |
| `--tools-list-cache-scope {public,private}` | `private` | `tools/list` 的 `cacheScope` 提示 |
| `--profile PROFILE` | - | 部署配置：`remote`、`local`，或配置文件 `profiles:` 中声明的名称 |

**示例：**

```bash
# stdio 传输（适用于 Claude Desktop 等）
toolregistry-server mcp --config config.json

# Streamable HTTP 传输
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --port 8000

# SSE 传输
toolregistry-server mcp \
  --config config.json \
  --transport sse \
  --port 8000

# 带 Bearer Token 认证（streamable-http）
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --tokens tokens.txt
```

## 配置文件

有关 JSON、JSONC 或 YAML 配置格式的详细说明，请参阅 [配置指南](../../guides/configuration.md)。

## 编程 API

### `App` 类（推荐）

`App` 类是规范的编程入口点。继承它并重写 `prepare_registry()` 以使用自定义逻辑构建注册表：

```python
from toolregistry import ToolRegistry
from toolregistry_server import App

class MyApp(App):
    def prepare_registry(self):
        registry = ToolRegistry()
        registry.register(my_tool)
        return registry

if __name__ == "__main__":
    MyApp().serve_openapi(host="0.0.0.0", port=8000)
    # 或：MyApp().serve_mcp(transport="stdio")
```

### `CLI` 类 — 自定义子命令

`CLI` 类是规范的命令行入口点。继承它并重写 `configure_subparsers()` 以添加自定义参数：

```python
from argparse import ArgumentParser
from toolregistry_server import CLI, App

class MyCLI(CLI):
    def configure_subparsers(self, subparsers: dict[str, ArgumentParser]):
        # 向 openapi 子命令添加自定义参数
        subparsers["openapi"].add_argument(
            "--my-flag", action="store_true", help="启用自定义功能"
        )

if __name__ == "__main__":
    MyCLI().main()
```

使用独立的 `run_cli()` 辅助函数作为简单入口点：

```python
from toolregistry_server import run_cli

run_cli()
```

### `ServerIdentity` — 自定义横幅与版本

传入 `ServerIdentity` 以自定义启动横幅和 `--version` 输出：

```python
from toolregistry_server import App, ServerIdentity

identity = ServerIdentity(
    name="my-server",
    version="1.2.3",
    description="我的自定义工具服务器",
)

App(identity=identity).serve_openapi()
```

## 部署配置文件

`--profile` 参数在注册表构建完成后应用基于标签的工具过滤：

| 配置文件 | 效果 |
|----------|------|
| `remote` | 禁用标记为 `file_system`、`destructive` 或 `privileged` 的工具 |
| `local` | 禁用标记为 `network` 的工具 |
| *（无）* | 不过滤（默认） |

配置文件可以用 `profiles:` 定义自己的部署配置；这些设置优先于内置标签集合，未知名称会记录警告并被忽略：

```yaml
profiles:
  staging:
    disable_tags: [destructive, privileged]
    enable: [calculator]      # 标签过滤后强制启用的命名空间模式
    disable: [internal/]      # 标签过滤后强制禁用的命名空间模式
```

每个配置接受 `disable_tags`（替换内置集合）、`enable` 与 `disable`（命名空间模式，按此顺序应用）。

向不应访问服务器文件系统或特权操作的最终用户提供工具时，请使用 `remote`：

```bash
toolregistry-server openapi --config config.json --profile remote
toolregistry-server mcp --config config.json --profile remote
```
