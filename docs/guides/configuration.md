# 配置

命令行工具从 JSONC 或 YAML 配置文件加载工具源。支持三种源类型：Python 类/模块、MCP 服务器和 OpenAPI 端点。

配置解析由 [ToolRegistry](https://toolregistry.readthedocs.io/zh-cn/latest/usage/configuration/) 核心库的 `toolregistry.config.load_config()` 驱动。

## 快速开始

```bash
# JSONC 配置
toolregistry-server openapi --config tools.jsonc

# YAML 配置
toolregistry-server mcp --config tools.yaml
```

## 配置文件格式

支持 JSONC（`.json`、`.jsonc`）和 YAML（`.yaml`、`.yml`）两种格式，根据文件扩展名自动检测。

### YAML 示例

```yaml
mode: denylist
disabled:
  - filesystem

tools:
  # Python 类
  - type: python
    class: toolregistry_hub.calculator.Calculator
    namespace: calculator

  # Python 模块（所有公开函数）
  - type: python
    module: my_package.tools
    namespace: custom

  # MCP 服务器（stdio）
  - type: mcp
    transport: stdio
    command: ["python", "-m", "mcp_server"]
    namespace: mcp_tools

  # MCP 服务器（SSE）
  - type: mcp
    transport: sse
    url: http://localhost:8080/sse
    namespace: remote_mcp

  # MCP 服务器（streamable-http，别名 "http"）
  - type: mcp
    transport: http
    url: http://localhost:8080/mcp
    namespace: remote_http

  # OpenAPI 端点
  - type: openapi
    url: https://api.example.com/openapi.json
    namespace: external_api
    auth:
      type: bearer
      token_env: EXTERNAL_API_TOKEN
```

### JSONC 示例

```jsonc
{
  "mode": "denylist",
  "disabled": ["filesystem"],
  "tools": [
    {
      "type": "python",
      "class": "toolregistry_hub.calculator.Calculator",
      "namespace": "calculator"
    },
    {
      // 通过 SSE 连接的 MCP 服务器
      "type": "mcp",
      "transport": "sse",
      "url": "http://localhost:8080/sse",
      "namespace": "remote_mcp"
    }
  ]
}
```

## 工具源类型

### `python` — Python 类或模块

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `"python"` | 是 | 源类型标识 |
| `class` | string | `class`/`module` 二选一 | 完整的类路径（如 `"pkg.Calculator"`） |
| `module` | string | `class`/`module` 二选一 | 完整的模块路径（如 `"pkg.tools"`） |
| `namespace` | string | 否 | 注册工具的命名空间 |
| `enabled` | bool | 否 | 按源启用/禁用（默认：`true`） |

指定 `class` 时，服务器实例化该类并调用 `register_from_class()`。指定 `module` 时，服务器注册模块中所有公开的可调用对象。

!!! note "旧版格式"
    旧版格式 `{"module": "pkg", "class": "Cls"}`（不含 `type` 字段）仍然支持。类型自动推断为 `"python"`，`class_path` 合并为 `"pkg.Cls"`。

### `mcp` — MCP 服务器

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `"mcp"` | 是 | 源类型标识 |
| `transport` | string | 是 | `"stdio"`、`"sse"`、`"streamable-http"` 或 `"http"` |
| `command` | string 或 list | 仅 stdio | 启动服务器的命令 |
| `url` | string | 仅 sse/http | 服务器 URL |
| `env` | dict | 否 | stdio 子进程的环境变量 |
| `headers` | dict | 否 | 网络传输的 HTTP 头 |
| `namespace` | string | 否 | 注册工具的命名空间 |
| `persistent` | bool | 否 | 保持连接（默认：`true`） |
| `include` | list | 否 | 保留的名称或 glob 模式。与 `exclude` 互斥 |
| `exclude` | list | 否 | 丢弃的名称或 glob 模式。与 `include` 互斥 |
| `allow_private_hosts` | bool | 否 | 允许该源的 URL 传输访问 loopback、link-local 或私网地址（默认：`false`） |
| `enabled` | bool | 否 | 按源启用/禁用（默认：`true`） |

!!! tip "`http` 别名"
    `transport: "http"` 是 `"streamable-http"` 的简写，内部会自动归一化。

!!! warning "错误处理"
    如果 MCP 服务器在启动时不可达，服务器会记录警告并继续加载其他源。该源的工具将不可用。

### `openapi` — OpenAPI 端点

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `"openapi"` | 是 | 源类型标识 |
| `url` | string | 是 | OpenAPI 规范的 URL 或本地路径。本地路径仅从 operator 配置读取，面板输入仍会被拒绝 |
| `namespace` | string | 否 | 注册工具的命名空间 |
| `auth` | object | 否 | 认证配置 |
| `base_url` | string | 否 | 覆盖规范中的 `servers[0].url` |
| `include` | list | 否 | 保留的名称或 glob 模式。与 `exclude` 互斥 |
| `exclude` | list | 否 | 丢弃的名称或 glob 模式。与 `include` 互斥 |
| `allow_private_hosts` | bool | 否 | 允许规范 URL 与工具实际使用的 base URL 访问 loopback、link-local 或私网地址（默认：`false`） |
| `enabled` | bool | 否 | 按源启用/禁用（默认：`true`） |

#### 认证配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | string | `"bearer"` | `"bearer"` 或 `"header"` |
| `token` | string | — | 字面量 token 值 |
| `token_env` | string | — | 环境变量名（解析时读取） |
| `header_name` | string | `"Authorization"` | 自定义头名称（用于 `type: "header"`） |

如果同时指定了 `token_env` 和 `token`，`token_env` 优先。

### 私网地址

`mcp` 与 `openapi` 源都会拒绝解析到 loopback、link-local 或私网地址的 URL。检查在任何连接建立之前执行，名称无法解析时按失败处理。在单个源上设置 `allow_private_hosts: true` 即可放行：

```yaml
tools:
  - type: openapi
    url: http://127.0.0.1:8000/openapi.json
    namespace: local_api
    allow_private_hosts: true

  - type: mcp
    transport: sse
    url: http://192.168.1.20:8080/sse
    namespace: lan_mcp
    allow_private_hosts: true
```

该标记只作用于这个源。对 `mcp` 而言，它覆盖 URL 传输（SSE、streamable HTTP、WebSocket）；`stdio` 源没有 URL，不受影响。对 `openapi` 而言，它覆盖规范 URL 与工具实际使用的 base URL（`servers[0]` 或配置的 `base_url`）。管理面板有自己的开关：`enable_admin(allow_private_hosts=True)`。

## 过滤模式

### 拒绝列表（默认）

加载所有源，**排除**命名空间匹配 `disabled` 中模式的源：

```yaml
mode: denylist
disabled:
  - filesystem
  - web/dangerous
```

### 允许列表

**仅**加载命名空间匹配 `enabled` 中模式的源：

```yaml
mode: allowlist
enabled:
  - calculator
  - api
```

命名空间匹配是层级式的：模式 `"web"` 匹配 `"web/brave_search"`。

### 按源启用/禁用

可以通过 `enabled: false` 临时禁用单个源，不受过滤模式影响。这在调试或临时下线某个源时非常有用，无需从配置中删除：

```yaml
tools:
  - type: python
    class: toolregistry_hub.calculator.Calculator
    namespace: calculator

  - type: mcp
    transport: sse
    url: http://localhost:8080/sse
    namespace: remote_mcp
    enabled: false  # 临时禁用

  - type: openapi
    url: https://api.example.com/openapi.json
    namespace: external_api
    enabled: false  # 等 API 密钥配置好再启用
```

## 部署配置

`--profile` 在所有源注册完成后应用命名过滤器。配置文件可以在 `profiles:` 下覆盖内置默认值：

```yaml
profiles:
  staging:
    disable_tags: [destructive, privileged]
    enable: [calculator]      # 标签过滤后强制启用的命名空间模式
    disable: [internal/]      # 标签过滤后强制禁用的命名空间模式
```

| 键 | 说明 |
|----|------|
| `disable_tags` | 要禁用的标签名。替换该部署配置的内置集合 |
| `enable` | 标签过滤后强制启用的命名空间模式（最高优先级） |
| `disable` | 标签过滤后强制禁用的命名空间模式（在 `enable` 之后应用） |

没有 `profiles:` 条目时使用内置集合：`remote` 禁用 `file_system`、`destructive` 与 `privileged`；`local` 禁用 `network`。未知的部署配置名会记录警告并被忽略。

## 环境变量

命令行工具支持从 `.env` 文件加载环境变量：

```bash
# .env
BRAVE_API_KEY=your-key-here
EXTERNAL_API_TOKEN=your-token-here
```

```bash
# 从默认 .env 文件加载
toolregistry-server openapi --config config.yaml

# 从自定义 .env 文件加载
toolregistry-server openapi --config config.yaml --env /path/to/.env

# 跳过 .env 加载
toolregistry-server openapi --config config.yaml --no-env
```

## 错误处理

- **文件未找到** — 服务器输出错误信息并退出
- **配置无效**（无效模式、缺少必填字段、未知类型）— 服务器输出描述性错误并退出
- **MCP/OpenAPI 源不可达** — 服务器记录警告并继续加载其他源
- **Python 模块/类无效** — 服务器记录警告并继续
