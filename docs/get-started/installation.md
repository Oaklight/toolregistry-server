# 安装

## 基本安装

安装核心包：

```bash
pip install toolregistry-server
```

这会安装核心路由层（`RouteTable`、`RouteEntry`）以及零依赖的 `httpserver`
OpenAPI 服务器，独立 `openapi` 命令即运行在它之上。MCP 支持与 ASGI gateway 后端属于
extra。

## 协议支持

!!! tip
    **zsh 用户**（macOS 默认 shell）：需要用引号包裹包名，例如 `pip install 'toolregistry-server[gateway]'` — zsh 会将 `[]` 解释为通配符。

### ASGI Gateway 后端

gateway 在同一个 ASGI 应用中提供 OpenAPI、MCP 与管理面板，其 ASGI 后端需要
FastAPI 与 uvicorn：

```bash
pip install toolregistry-server[gateway]
```

这将额外安装：

- `FastAPI` (>=0.119.0)
- `Uvicorn` 及标准扩展 (>=0.24.0)

独立 `openapi` 命令不需要该 extra。它自己的 extra 保持精简，现在包含带加密后端的
`PyJWT`，因此无需安装 `[gateway]` 或 `[mcp]` 也能在该命令上使用 OAuth 资源服务器：

```bash
pip install toolregistry-server[openapi]
```

不安装任何 extra 时，基础安装仍不包含第三方包（包括 PyJWT）。

### MCP 支持

通过 [模型上下文协议](https://modelcontextprotocol.io/) 暴露工具：

```bash
pip install toolregistry-server[mcp]
```

这将额外安装：

- `mcp` SDK (>=1.22.0,<3)

### 完整安装

安装所有协议适配器：

```bash
pip install toolregistry-server[all]
```

`all` 会安装 `openapi` 与 `mcp` extra。有界的 spec 正则校验是单独的可选项：

### 有界的 Spec 正则校验（可选）

```bash
pip install 'toolregistry-server[regex]'
```

这会安装 `regex` (>=2024.4.16)。未安装时，OpenAPI 文档提供的 `pattern` 与 `patternProperties` 不会在请求校验中执行；安装后，每次匹配都受单次超时与每请求预算（`--regex-timeout`、`--regex-budget`）约束。参见 [OpenAPI 指南](../guides/openapi.md#来自-spec-的正则)。

## 开发安装

用于贡献或开发：

```bash
git clone https://github.com/Oaklight/toolregistry-server.git
cd toolregistry-server
pip install -e ".[all,dev]"
```

`dev` 扩展包括：

- `pytest`、`pytest-asyncio` - 测试
- `httpx` - HTTP 测试客户端
- `ruff` - 代码检查和格式化
- `build`、`twine` - 包构建和发布

## 系统要求

- **Python**: 3.10 或更高版本（已在 3.10–3.13 上测试）
- **核心依赖**: [`toolregistry`](https://toolregistry.readthedocs.io/) >= 0.20.1,<1.0.0

## 验证安装

```python
from toolregistry_server import RouteTable, RouteEntry
print("toolregistry-server 安装成功！")
```
