---
title: API 参考
---

# API 参考

本节提供 `toolregistry-server` 所有模块的详细 API 文档。

## 应用与命令行

| 模块 | 描述 |
|------|------|
| [App](app.md) | 规范的编程入口点 — `serve_openapi()`、`serve_mcp()`、`prepare_registry()` |
| [CLI](cli.md) | 规范的命令行入口点 — 继承并重写 `configure_subparsers()` |
| [ServerIdentity](server_identity.md) | 携带名称/版本/描述，用于横幅和 `--version` 输出 |

## 核心

| 模块 | 描述 |
|------|------|
| [RouteTable](core/route_table.md) | 中央路由层 — `RouteTable` 和 `RouteEntry` 类 |

## 适配器

| 模块 | 描述 |
|------|------|
| [OpenAPI](openapi.md) | 基于 FastAPI 的 REST API 适配器 — `OpenAPIAdapter`、`Adapter.create_and_run()` |
| [MCP](mcp.md) | 模型上下文协议适配器 — `MCPAdapter`、`Adapter.create_and_run()` |

## 认证

| 模块 | 描述 |
|------|------|
| [Auth](auth.md) | Bearer Token 认证 — `load_tokens()`、`BearerTokenAuth` |
