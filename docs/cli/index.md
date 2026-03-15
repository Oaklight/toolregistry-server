---
title: 命令行工具参考
---

# 命令行工具

`toolregistry-server` 提供命令行工具，无需编写代码即可运行服务器。

## 用法

```bash
toolregistry-server [选项] <子命令> [子命令选项]
```

## 全局选项

| 选项 | 描述 |
|------|------|
| `--env-file PATH` | .env 文件路径（默认：`.env`） |
| `--no-env` | 跳过加载 .env 文件 |

## 子命令

### `openapi` - 启动 OpenAPI 服务器

```bash
toolregistry-server openapi [选项]
```

| 选项 | 默认值 | 描述 |
|------|--------|------|
| `--config PATH` | 必需 | JSON/JSONC 配置文件路径 |
| `--host HOST` | `0.0.0.0` | 绑定主机 |
| `--port PORT` | `8000` | 绑定端口 |
| `--auth-token TOKEN` | - | 用于认证的 Bearer 令牌 |
| `--auth-tokens-file PATH` | - | 令牌文件（每行一个） |
| `--reload` | `false` | 启用开发自动重载 |

**示例：**

```bash
toolregistry-server openapi \
  --config config.json \
  --port 8000 \
  --auth-token "my-secret"
```

### `mcp` - 启动 MCP 服务器

```bash
toolregistry-server mcp [选项]
```

| 选项 | 默认值 | 描述 |
|------|--------|------|
| `--config PATH` | 必需 | JSON/JSONC 配置文件路径 |
| `--transport TYPE` | `stdio` | 传输类型：`stdio`、`sse` 或 `streamable-http` |
| `--host HOST` | `0.0.0.0` | 绑定主机（用于 HTTP 传输） |
| `--port PORT` | `8000` | 绑定端口（用于 HTTP 传输） |

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

参见 [配置指南](../usage/configuration.md) 了解 JSON/JSONC 配置格式的详细信息。
