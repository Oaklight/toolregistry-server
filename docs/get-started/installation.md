# 安装

## 基本安装

安装核心包：

```bash
pip install toolregistry-server
```

这将安装核心路由层（`RouteTable`、`RouteEntry`），不包含任何协议适配器。

## 协议支持

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

- `mcp` SDK (>=1.8.0)

### 完整安装

安装所有协议适配器：

```bash
pip install toolregistry-server[all]
```

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

- **Python**: 3.10 或更高版本
- **核心依赖**: [`toolregistry`](https://toolregistry.readthedocs.io/) >= 0.5.0

## 验证安装

```python
from toolregistry_server import RouteTable, RouteEntry
print("toolregistry-server 安装成功！")
```
