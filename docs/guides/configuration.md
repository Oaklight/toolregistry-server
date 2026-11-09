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

## 运行期来源变更

服务器无需重启即可变更来源列表。`App.prepare_registry` 在加载配置后构建
`SourceManager` 并保存在 `app.source_manager` 上；管理扩展或嵌入式应用可直接调用。

```python
from toolregistry.config import PythonSource

manager = app.source_manager
manager.list()                      # 每个声明对应一个 SourceEntry
manager.to_dicts()                  # 可直接序列化的状态列表

added = manager.add(PythonSource(class_path="my_tools.Extra", namespace="extra"))
source_id = added.entry.to_dict()["id"]
removed = manager.remove(source_id)
manager.replace(source_id, PythonSource(class_path="my_tools.Other"))
manager.retry(failed_entry.to_dict()["id"])   # 重试失败的注册
```

每个条目处于三种状态之一：

| 状态 | 含义 |
|------|------|
| `applied` | 注册成功。没有产出任何工具的源仍记为 `applied`，不会在无关变更时被再次注册。 |
| `excluded` | 声明仍在配置中，但不在作用域内（`enabled: false`，或被 `mode` / `disabled` / `enabled` 过滤）。其工具被禁用而非注销，源重新进入作用域时会重新启用。 |
| `failed` | 注册抛出异常。`error` 保存脱敏后的消息，下次 diff 会重试。部分注册会回滚。 |

源以声明本身标识，因此调整 `tools` 顺序不算变更。`to_dict()` 只暴露由完整声明推导的
不透明 `src_<16 hex>` id——同一目标、不同凭据的两个源会得到不同的 id——以及脱敏后的
详情、命名空间、工具名、启用/总数计数与 `last_error`。原始声明与内部标识保留在管理器内；
`get`、`remove`、`replace`、`retry` 都接收该 id。`MutationResult.removed` 在删除时为
`True`，其 entry 状态仍描述最后一次应用尝试。

变更注册的工具会应用启动时的部署配置过滤。变更在管理器的一把锁下执行，并在
挂载了路由表时包在 `RouteTable.batch_changes()` 中：N 个工具注册只触发一次路由重建；
请求路径不获取该锁。

### 持久化

add、remove、replace 会把配置写回服务器启动时使用的文件，因此重启后状态保持。
写入前先把原文件复制为 `<path>.bak`；解析模型不保留 JSONC 注释，因此注释会丢失。
`MutationResult.persisted` 与 `persist_error` 报告结果：只读路径会被报告而不会静默忽略，
运行期变更仍然生效。`--no-config-write`（或 `write_config=False`）让所有变更只保留在内存中。

### 监听文件

`--config-reload` 启动轮询配置文件的 watcher，文件变化时应用同一套源级 diff。
管理器自己的写入通过内容哈希识别，因此运行期变更不会触发重载；watcher 自身从不写文件。
格式错误的编辑会记录并忽略，运行中的注册表保持不变。watcher 是进程内的，没有跨进程协调。

## Gateway 前端

`gateway` 子命令在一个进程、一个监听器上同时提供 OpenAPI 适配器、两种 MCP HTTP 传输
与管理面板：

```bash
toolregistry-server gateway --config tools.yaml --port 8000
```

默认挂载点为 `/openapi`（OpenAPI）、`/mcp`（MCP streamable-HTTP）、`/sse`
（MCP SSE）与 `/admin`（管理面板）。配置文件可在 `gateway.frontends` 下修改路径与
启用标记：

```yaml
gateway:
  frontends:
    openapi:
      path: /api
      enabled: true
    mcp:
      path: /rpc
      enabled: true
    sse:
      path: /events
      enabled: false
    admin:
      path: /admin
      enabled: false
```

| 前端 | 默认路径 | 传输 | 默认 |
|------|----------|------|------|
| `openapi` | `/openapi` | HTTP | 启用 |
| `mcp` | `/mcp` | `streamable-http` | 启用 |
| `sse` | `/sse` | SSE | 启用 |
| `admin` | `/admin` | HTTP | 禁用 |

命令行选项 `--openapi-path`、`--mcp-path`、`--sse-path`、`--admin-path`、
`--openapi`、`--mcp`、`--sse` 与 `--admin` 可对本次进程覆盖文件设置。启用标记在每个请求上读取：禁用某个前端后，
其路径立即返回 `404`，无需重启，也不影响其他前端。修改路径同样在下一个请求生效。

ASGI lifespan 启动时创建一个 `StreamableHTTPSessionManager`，结束时关闭。启用或禁用
MCP 前端不会重建它。gateway 始终用一个 ASGI 应用提供所有启用的前端，因此多个前端
必须使用默认的 `fastapi` 后端；启用多个前端时 `--backend httpserver` 会被拒绝，
该后端只能提供 openapi 前端。该后端在监听器根路径提供 OpenAPI，因此 openapi
路径覆盖会被拒绝，而不是静默忽略。

`--tokens`（以及 `API_BEARER_TOKEN`）用同一组令牌保护 OpenAPI 与 MCP 前端。管理
面板刻意不在该令牌集合内，详见[管理面板](#管理面板)。配置令牌或 `--keys-db` 后，
MCP 的受保护资源元数据位于 `/.well-known/oauth-protected-resource<MCP 路径>`，并
跟随当前 MCP 路径。

### 管理面板

`--admin` 启用面板，`--admin-path` 修改挂载路径。面板就是 core 的 ToolRegistry 管理
界面，因此使用 core 面板自己的令牌与会话鉴权，而不是 gateway 的 Bearer 令牌。用
`--admin-token TOKEN` 或更推荐的 `--admin-token-file PATH` 配置运维令牌：

```bash
toolregistry-server gateway \
  --config tools.yaml \
  --tokens serving-tokens.txt \
  --admin \
  --admin-token-file operator-token.txt
```

面板默认禁用。启用但未配置运维令牌会导致启动失败；gateway 的对外令牌无法打开面板：
只用对外令牌请求 `/admin/api/info` 会得到 `401`。

gateway 只监听一个对外端口。进程内部在回环地址的临时端口启动 core 的 `AdminServer`，
并把管理前端反向代理过去，同时保留公开的 `Host` 头，使面板的同源检查继续有效。内部
监听器从不接受非回环连接。面板在前端启用后的第一个请求到来时启动；前端禁用期间，其
路径返回 `404`，没有任何管理请求到达面板。

面板会用进程中的实时对象加载 gateway 管理扩展：

- **Keys** 使用 `--keys-db PATH` 指定的存储；未配置时该页返回 `503`。创建与吊销会
  在下一个发往工具前端的请求上生效。
- **Frontends** 列出并修改与 `gateway.list_frontends()` /
  `gateway.update_frontend()` 相同的注册表；变更在下一个请求生效，并按常规写入策略
  持久化。
- **Sources** 使用运行期来源管理器；面板自身的来源注册也会通过该管理器持久化。

扩展路由位于面板挂载路径之下，例如 `/admin/ext/gateway/keys`。外壳通过面板的资源
处理器加载扩展资源，任何资源都不包含密钥。禁用管理前端后，面板根路径及其下所有扩展
路由都返回 `404`。

托管面板要求 core `toolregistry` 提供管理扩展契约（`ADMIN_EXTENSION_API == 1`，即
core 0.21.0 或更新的版本）。旧版 core 上该前端会以明确错误拒绝启动；默认配置不会加载
它。

`--keys-db` 是 gateway 的限定作用域密钥选项，执行规则见[认证](authentication.md#限定作用域的-api-密钥)。
面板共享该存储，因此在面板中创建或吊销的密钥会在下一个工具请求生效。

### 运行期前端 API

调用 `App.create_gateway()` 或 `App.serve_gateway()` 后，可通过 `App.gateway`
在进程内取得 gateway 对象：

```python
gateway = app.create_gateway(config_path="tools.yaml")

gateway.list_frontends()
# [{"name": "openapi", "path": "/openapi", "enabled": True, "transport": "http"},
#  ..., {"name": "admin", "path": "/admin", "enabled": False, "transport": "http"}]

result = gateway.update_frontend("mcp", enabled=False)
# {"name": "mcp", "path": "/mcp", "enabled": False, ...,
#  "persisted": True,
#  "warning": "the config file was rewritten; JSONC comments are not preserved"}

gateway.update_frontend("sse", path="/events")
```

更新会先把原文件复制为 `<path>.bak`，再把新路径与标记写回配置文件，因此重启后仍然生效。
`result["persisted"]` 表示写入是否成功。`result["warning"]` 说明跳过或失败的原因；
写入成功时它也会带上与运行期来源管理相同的注释丢失警告，因为 JSONC 注释不会被保留。
即使写入失败，运行期变更仍然生效。`--no-config-write`（或 `write_config=False`）
关闭写入，并在 `warning` 中报告。

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
