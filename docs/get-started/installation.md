# 安装

## 基本安装

安装核心包：

```bash
pip install toolregistry-server
```

这会安装核心路由层（`RouteTable`、`RouteEntry`）以及零依赖的 `httpserver` OpenAPI 后端。FastAPI 后端与 MCP 支持属于 extra。

## 协议支持

!!! tip
    **zsh 用户**（macOS 默认 shell）：需要用引号包裹包名，例如 `pip install 'toolregistry-server[openapi]'` — zsh 会将 `[]` 解释为通配符。

### OpenAPI 支持

将工具暴露为使用 FastAPI 的 RESTful HTTP 端点：

```bash
pip install toolregistry-server[openapi]
```

这将额外安装：

- `FastAPI` (>=0.119.0)
- `Uvicorn` 及标准扩展 (>=0.24.0)

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
