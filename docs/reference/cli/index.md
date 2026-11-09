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
| `--config-reload` / `--no-config-reload` | 监听配置文件，变化时应用源级 diff（默认：禁用） |
| `--no-config-write` | 运行期来源变更只保留在内存中，不重写配置文件 |

## 子命令

### `openapi` — 启动 OpenAPI 服务器

```bash
toolregistry-server openapi [选项]
```

独立服务器始终运行在内联的 `httpserver` 上：不需要 extra，也没有 `--backend` 与
`--reload`（两者都只属于 uvicorn）。`gateway` 命令保留 `--backend` 选择。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--config PATH` | - | JSONC 或 YAML 配置文件路径（必需） |
| `--host HOST` | `0.0.0.0` | 绑定主机 |
| `--port PORT` | `8000` | 绑定端口 |
| `--tokens PATH` | - | Bearer 令牌文件，每行一个（`#` 开始注释）。令牌以恒定时间比较；同时读取 `API_BEARER_TOKEN`（逗号分隔） |
| `--max-body-size BYTES` | `1048576`（1 MiB） | 接受的最大请求体。必须为正数；超限返回带 JSON 错误的 `413` |
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

### `gateway` — 单进程提供 OpenAPI、MCP 与管理面板

```bash
toolregistry-server gateway [选项]
```

一个 ASGI 应用把 OpenAPI 适配器挂载在 `/openapi`、MCP streamable-HTTP 挂载在
`/mcp`、MCP SSE 挂载在 `/sse`、管理面板挂载在 `/admin`。每个前端的路径可配置，
启用标记按请求检查。管理面板默认禁用。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--config PATH` | - | JSONC 或 YAML 配置文件路径（必需） |
| `--host HOST` | `0.0.0.0` | 绑定主机 |
| `--port PORT` | `8000` | 绑定端口 |
| `--tokens PATH` | - | Bearer 令牌文件，作用于所有前端；同时读取 `API_BEARER_TOKEN` |
| `--server-url URL` | 由 `--host`/`--port` 推导 | 受保护资源元数据中使用的公开 MCP URL |
| `--backend {fastapi,httpserver}` | `fastapi` | 多个前端必须使用 `fastapi`；`httpserver` 只能提供 openapi 前端 |
| `--openapi-path PATH` | `/openapi` | OpenAPI 挂载路径（`--backend httpserver` 下会被拒绝，因为该后端在监听器根路径提供服务） |
| `--mcp-path PATH` | `/mcp` | MCP streamable-HTTP 挂载路径 |
| `--sse-path PATH` | `/sse` | MCP SSE 挂载路径 |
| `--admin-path PATH` | `/admin` | 管理面板挂载路径 |
| `--openapi` / `--no-openapi` | 启用 | 启用或禁用 OpenAPI 前端 |
| `--mcp` / `--no-mcp` | 启用 | 启用或禁用 MCP streamable-HTTP |
| `--sse` / `--no-sse` | 启用 | 启用或禁用 MCP SSE |
| `--admin` / `--no-admin` | 禁用 | 启用或禁用管理面板；启用时必须配置运维令牌 |
| `--admin-token TOKEN` | - | 管理面板接受的运维令牌；推荐改用 `--admin-token-file` |
| `--admin-token-file PATH` | - | 从文件读取运维令牌 |
| `--keys-db PATH` | - | 支撑面板 Keys 页的 SQLite 存储 |
| `--admin-allow-private-hosts` | `false` | 允许面板拉取解析到私有地址的来源 URL |
| `--no-config-write` | `false` | 运行期前端变更不写回配置文件 |
| `--max-body-size BYTES` | `1048576`（1 MiB） | OpenAPI 前端接受的最大请求体 |
| `--regex-timeout SECONDS` | `0.05` | 来自 spec 的正则单次匹配上限 |
| `--regex-budget SECONDS` | `0.25` | 单次请求内所有 spec 正则匹配的上限 |
| `--strict-spec-regex` | `false` | OpenAPI source 含非法正则时终止启动 |
| `--tools-list-ttl-ms MS` | `300000` | MCP `tools/list` 的 `ttlMs` 缓存提示 |
| `--tools-list-cache-scope {public,private}` | `private` | MCP `tools/list` 的 `cacheScope` 提示 |
| `--profile PROFILE` | - | 部署配置 |

**示例：**

```bash
toolregistry-server gateway \
  --config config.yaml \
  --port 8000 \
  --openapi-path /api \
  --no-sse \
  --tokens tokens.txt \
  --admin \
  --admin-token-file operator-token.txt
```

被禁用的前端返回 `404`，不会触达注册表。前端路径与启用标记也可以保存在配置文件的
`gateway.frontends` 下；参见[配置指南](../../guides/configuration.md)。
管理面板使用自己的运维令牌，而不是 `--tokens` 集合；扩展页面位于面板挂载路径之下
（例如 `/admin/ext/gateway/keys`）。Keys、Frontends 与 Sources 三个页面的说明见
[网关管理面板指南](../../guides/gateway-admin.md)。

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
