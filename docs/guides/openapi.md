# OpenAPI 适配器

OpenAPI 适配器将 `ToolRegistry` 工具暴露为 RESTful HTTP 端点，可使用 [FastAPI](https://fastapi.tiangolo.com/) 或内联的零依赖 `httpserver` 后端。

## 概述

适配器自动：

- 为每个注册的工具创建 POST 端点
- 按路由发布的 JSON Schema（签名派生的工具则按工具自身的校验模型）校验每个请求体
- 生成 OpenAPI 模式（可在 `/openapi.json` 访问）
- 提供 `/tools` 元数据端点，列出可用工具
- 支持运行时启用/禁用单个工具
- 实现基于 ETag 的 HTTP 缓存
- 支持允许额外属性的 schema（`additionalProperties` 缺省、为 `true` 或 `{}`），例如 `call_deferred` 的 `**kwargs`

## 不使用 FastAPI 构建文档

`RouteTable` 已经持有 API 契约，因此可以直接由它生成 OpenAPI 文档：

```python
from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi.spec import build_openapi_schema

document = build_openapi_schema(RouteTable(registry), title="My Tools", version="2.0.0")
```

输出与服务端 `/openapi.json` 为已启用工具发布的内容一致——相同的 `operationId`、摘要、命名空间标签、参数 schema 与 `x-*` 执行提示扩展——被禁用的工具直接不出现，无需事后过滤。只想要工具路由时，可用 `include_meta=False` 省略 `/tools` 与 `/events` 两个 operation。

## 快速开始

### 通过 `App`（推荐）

```python
from toolregistry_server.app import App

# 从配置文件启动
App().serve_openapi(config_path="tools.yaml", host="0.0.0.0", port=8000)

# 使用已构建好的 registry
from toolregistry import ToolRegistry
registry = ToolRegistry()
# ... 注册工具 ...
App().serve_openapi(registry=registry, port=9000)
```

### 直接使用 `OpenAPIAdapter`

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.adapters.openapi import OpenAPIAdapter

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名称问候某人。"""
    return f"Hello, {name}!"

route_table = RouteTable(registry)

adapter = OpenAPIAdapter(
    route_table,
    title="My Tool Server",
    version="1.0.0",
    tokens=["secret-token"],   # 可选 Bearer 认证
)
adapter.run(host="0.0.0.0", port=8000)
```

### 一步构建并运行

```python
from toolregistry_server.adapters.openapi import OpenAPIAdapter

OpenAPIAdapter.create_and_run(
    route_table,
    host="0.0.0.0",
    port=8000,
    tokens_path="/etc/myapp/tokens.txt",
)
```

## 端点结构

每个工具作为 POST 端点暴露在其路由路径上：

```
POST /{namespace}/{tool_name}
```

例如，命名空间 `calculator` 中的工具 `evaluate` 变为：

```
POST /calculator/evaluate
```

### 私网地址

解析到 loopback、link-local 或私网地址的规范 URL 会在拉取前被拒绝。工具实际使用的 base URL——`servers[0]` 或配置的 `base_url`——会在每次请求前校验；重定向默认也被拒绝，因为下一跳无法校验（`follow_redirects=False`）。按源放行本地或私网 API：

```yaml
tools:
  - type: openapi
    url: http://127.0.0.1:8000/openapi.json
    namespace: local_api
    allow_private_hosts: true
```

自行构建客户端的调用方也可以在 `load_openapi_spec(..., allow_private_hosts=True)`、`validate_spec_url()` 与 `HttpClientConfig` 上传入同一标记。参见[配置](../guides/configuration.md#私网地址)。

### 规范加载与刷新

OpenAPI 源只在注册时读取一次规范文档。路由刷新——`RouteTable.refresh()`、`refresh_all()`，以及 `/tools` 上的 `last_refreshed_at` 字段——只根据注册表中已有的工具重建路由，不会重新下载规范。因此，远端文档新增的 path 在重新注册该源之前不会出现：重启服务器，或在 Hub 中于启用 `--config-reload` 时修改该源在 `tools.jsonc` 中的条目（声明未变化的源不会被重新注册）。

该源的 `allow_private_hosts` 同时作用于两项检查：规范 URL 在拉取前校验，客户端在每次请求前校验实际使用的 base URL（`servers[0]` 或配置的 `base_url`）。重新注册该源时会重新下载文档，并同样经过出站地址策略；没有任何刷新路径会跳过该校验。

### 请求格式

参数作为 JSON 主体传递：

```bash
curl -X POST http://localhost:8000/calculator/evaluate \
  -H "Content-Type: application/json" \
  -d '{"expression": "2 + 3 * 4"}'
```

### 响应格式

```json
{"result": 14}
```

## 工具元数据端点

`GET /tools` 返回所有可用工具及其模式：

```bash
curl http://localhost:8000/tools
```

每个工具条目包含 `schema_hash`（工具参数模式的 SHA-256 指纹）和 `last_refreshed_at`（上次模式刷新的 ISO 8601 时间戳，如可用）。这些字段使客户端能够检测模式变更，而无需重新获取完整的工具定义。

## 实时事件

`GET /events` 提供工具变更事件的 Server-Sent Events（SSE）流：

```bash
curl -N http://localhost:8000/events
```

当工具被注册、注销、启用或禁用时会发出事件。每个事件以 JSON 格式包含工具名称和事件类型。

## HTTP 后端

适配器可以在两种后端上提供服务，两者的线上契约完全一致：

| 后端 | 服务器 | 第三方依赖 |
|---|---|---|
| `fastapi`（默认） | uvicorn | FastAPI、uvicorn 及其依赖 |
| `httpserver` | 内联的 `httpserver` | 无 |

```bash
toolregistry-server openapi --config tools.yaml --backend httpserver
```

```python
OpenAPIAdapter(route_table, backend="httpserver")
App().serve_openapi(config_path="tools.yaml", backend="httpserver")
```

`httpserver` 是 epic #101 的零依赖路径：路由、422 `{"detail": [...]}` 结构、`/tools` 与 `/openapi.json` 上的 ETag/`304` 行为、以及 `/events` 的 Server-Sent Events 流都一致。有一组差分测试同时启动两个实现并通过真实 socket 断言状态码、`ETag` 与响应体字节完全相同。`--reload` 是 uvicorn 的特性，在 `httpserver` 后端上只会记录一条告警并被忽略。

两个后端都用同一个辅助函数编码工具结果，该函数使用 core 的 `ToolResultJSONEncoder`：`bytes` 结果在两个后端上都是 base64，非有限浮点数与 `Decimal`（`NaN`、`Infinity`）都输出 `null`，而不是裸 `NaN` 或字符串 `"NaN"`。

### 请求体上限

两个后端共用 `--max-body-size`（默认 `1048576`，即 1 MiB）：

```bash
toolregistry-server openapi --config tools.yaml --max-body-size 4194304
```

```python
OpenAPIAdapter(route_table, max_body_size=4 * 1024 * 1024)
```

该值必须是正整数：CLI 在解析参数时拒绝零或负数，`OpenAPIAdapter` 会抛出 `ValueError`。超过上限的请求在两个后端上都返回 HTTP `413`，响应体为 JSON `{"detail": "Request body too large"}`。

## 来自 spec 的正则

代理的 OpenAPI 工具会发布上游文档里的 `pattern` 与 `patternProperties`。Python 的 `re` 没有超时，schema 提供的表达式可能灾难性回溯。

未安装可选的 `regex` extra 时，服务端**不执行**这些正则：校验使用去掉 `pattern` 与 `patternProperties` 的副本，并且在该层把 `additionalProperties: false` 放宽。客户端仍能看到完整 schema；字段格式以上游 API 为准。

安装 extra 后即可在有界前提下执行：

```bash
pip install toolregistry-server[regex]
```

每次匹配都经过 `regex.search(..., timeout=...)`：默认单次 50 ms、每请求 250 ms。`patternProperties` 会先对每个 pattern 调用一次（即使对象为空也编译校验），再对每个 (pattern, key) 调用一次，因此 10 个 pattern 作用于 200 个键时共 2 010 次调用，共享 250 ms 预算。单次或预算超时会返回 `500`，detail 为 `{"detail": "Tool '<name>' validation timed out"}`；pattern 不会回显给客户端。

```bash
toolregistry-server openapi --config tools.yaml \
  --regex-timeout 0.05 --regex-budget 0.25
```

```python
OpenAPIAdapter(route_table, regex_timeout=0.05, regex_budget=0.25)
```

`0` 表示关闭执行，恢复默认行为；负数或非数字会在启动时报错。安装 extra 会收紧校验：此前因正则被忽略而通过的请求现在可能被拒绝，`additionalProperties` 也不再放宽。

加载期会编译新注册 source 中的每个 `pattern` 与 `patternProperties` 键。默认只丢弃自身 schema 含非法正则的工具——每个都会记录错误日志（工具名、schema 路径、pattern）——其余工具保留；只有没有任何工具存活时，才把 source 报为失败。`--strict-spec-regex` 恢复整源失败语义：移除该 source 的全部工具并终止启动。

## 认证

通过 token 文件或 `API_BEARER_TOKEN` 环境变量设置 Bearer 认证：

```python
from toolregistry_server.adapters.openapi import OpenAPIAdapter
from toolregistry_server.auth import load_tokens

tokens = load_tokens(tokens_path="/etc/myapp/tokens.txt")
adapter = OpenAPIAdapter(route_table, tokens=tokens or None)
adapter.run(host="0.0.0.0", port=8000)
```

```bash
# CLI
toolregistry-server openapi --config tools.yaml --tokens tokens.txt
```

## 禁用的工具

当工具在运行时被禁用时，其端点返回 `503 Service Unavailable`：

```json
{"detail": "Tool 'calculator_evaluate' is currently disabled"}
```

禁用的工具也会从动态 OpenAPI 模式中排除。

## ETag 缓存

适配器包含 `ETagMiddleware`，为 `/tools` 和 `/openapi.json` 端点提供 HTTP 缓存：

- 每个响应包含 `ETag` 头
- 客户端可以发送 `If-None-Match` 进行条件请求
- ETag 匹配时返回 `304 Not Modified`

## 操作扩展

OpenAPI 没有存放执行提示的标准字段，因此与 MCP 的 `annotations` 相同的发布提示会以 `x-` 扩展的形式出现在生成的每个 operation 上：

| 扩展 | 来源 |
|---|---|
| `x-read-only` | `read_only_hint` |
| `x-destructive` | `destructive_hint` |
| `x-idempotent` | `idempotent_hint` |
| `x-open-world` | `open_world_hint` |
| `x-title` | `ToolMetadata.title` |

默认只输出已声明的提示：没有任何声明的工具，其 operation 保持原有结构；除非 `RouteTable` 启用 `emit_derived_execution_hints`，否则由标签推导的值不会出现。与 MCP 侧一致，标签决定部署暴露范围，提示描述单次调用，因此方法级提示不会改变 profile 暴露哪些工具。

## API 参考

参见 [OpenAPI API 参考](../reference/api/openapi.md) 获取详细文档。
