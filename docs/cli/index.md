---
title: 命令行工具参考
---

# 命令行工具

`toolregistry-server` 提供命令行工具，无需编写代码即可运行服务器。

## 用法

```bash
toolregistry-server [选项] <子命令> [子命令选项]
```

## 顶层选项

| 选项 | 描述 |
|------|------|
| `--version`, `-V` | 显示版本并退出 |
| `--no-banner` | 禁用启动横幅 |

## 通用子命令选项

| 选项 | 描述 |
|------|------|
| `--env PATH` | .env 文件路径（默认：当前目录下的 `.env`） |
| `--no-env` | 跳过加载 .env 文件 |

## 子命令

### `openapi` - 启动 OpenAPI 服务器

```bash
toolregistry-server openapi [选项]
```

| 选项 | 默认值 | 描述 |
|------|--------|------|
| `--config PATH` | - | JSONC 或 YAML 配置文件路径 |
| `--host HOST` | `0.0.0.0` | 绑定主机 |
| `--port PORT` | `8000` | 绑定端口 |
| `--tokens PATH` | - | Bearer 令牌文件（每行一个） |
| `--reload` | `false` | 启用开发自动重载 |
| `--profile PROFILE` | - | 部署 profile：`remote` 或 `local` |

**示例：**

```bash
toolregistry-server openapi \
  --config config.yaml \
  --port 8000 \
  --tokens tokens.txt \
  --profile remote
```

### `mcp` - 启动 MCP 服务器

```bash
toolregistry-server mcp [选项]
```

| 选项 | 默认值 | 描述 |
|------|--------|------|
| `--config PATH` | - | JSON、JSONC 或 YAML 配置文件路径 |
| `--transport TYPE` | `stdio` | 传输类型：`stdio`、`sse` 或 `streamable-http` |
| `--host HOST` | `0.0.0.0` | 绑定主机（用于 HTTP 传输） |
| `--port PORT` | `8000` | 绑定端口（用于 HTTP 传输） |
| `--profile PROFILE` | - | 部署 profile：`remote` 或 `local` |

**示例：**

```bash
# stdio 传输（用于 Claude Desktop 等）
toolregistry-server mcp --config config.json

# 可流式 HTTP 传输
toolregistry-server mcp \
  --config config.json \
  --transport streamable-http \
  --port 8000

# SSE 传输
toolregistry-server mcp \
  --config config.json \
  --transport sse \
  --port 8000
```

## 配置文件

参见 [配置指南](../usage/configuration.md) 了解 JSON、JSONC 或 YAML 配置格式的详细信息。

## Python API 用法

两个服务器函数均可在 Python 中直接调用，并接受可选的预构建 `ToolRegistry`，无需配置文件：

```python
from toolregistry import ToolRegistry
from toolregistry_server.cli.openapi import run_openapi_server
from toolregistry_server.cli.mcp import run_mcp_server

# 使用自定义逻辑构建 registry
registry = ToolRegistry()
registry.register(my_tool)

# 直接传入 registry — config_path 将被忽略
run_openapi_server(host="0.0.0.0", port=8000, registry=registry)
run_mcp_server(transport="stdio", registry=registry)
```

当 `registry` 为 `None`（默认值）时，函数将按常规方式从 `config_path` 加载工具。

## 部署 Profile

`--profile` 标志在 registry 构建完成后应用基于标签的工具过滤：

| Profile | 效果 |
|---------|------|
| `remote` | 禁用带 `file_system`、`destructive` 或 `privileged` 标签的工具 |
| `local` | 不做标签过滤，所有工具保持启用 |
| *(不指定)* | 不过滤（默认） |

当向终端用户提供服务且不希望其访问服务器本地文件系统或特权操作时，使用 `remote`：

```bash
toolregistry-server openapi --config config.json --profile remote
toolregistry-server mcp --config config.json --profile remote
```

`apply_profile()` 也作为公开 Python API 提供：

```python
from toolregistry_server.cli.openapi import apply_profile, PROFILE_DISABLE_TAGS

# 使用内置 profile
apply_profile(registry, "remote")

# 查看或扩展映射关系
print(PROFILE_DISABLE_TAGS)
```
