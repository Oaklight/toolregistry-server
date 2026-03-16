---
title: 首页
author: Oaklight
hide:
  - navigation
---

# ToolRegistry Server

[![PyPI version](https://badge.fury.io/py/toolregistry-server.svg)](https://badge.fury.io/py/toolregistry-server)
[![Python Version](https://img.shields.io/pypi/pyversions/toolregistry-server.svg)](https://pypi.org/project/toolregistry-server/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**定义自定义工具，通过 OpenAPI 或 MCP 接口提供服务。** 基于 [ToolRegistry](https://toolregistry.readthedocs.io/) 构建。

## 概述

`toolregistry-server` 让您将 Python 函数注册为工具，并通过多种协议将其暴露为服务——通过 OpenAPI 提供 REST API，通过 Model Context Protocol 实现 LLM 集成。

## 生态系统

ToolRegistry 生态系统由三个包组成：

| 包 | 描述 |
|---|------|
| [`toolregistry`](https://toolregistry.readthedocs.io/) | 核心库 - 工具模型、ToolRegistry、客户端集成 |
| [`toolregistry-server`](https://toolregistry-server.readthedocs.io/) | 工具服务器 - 定义工具并通过 OpenAPI/MCP 提供服务 |
| [`toolregistry-hub`](https://toolregistry-hub.readthedocs.io/) | 工具集合 - 内置工具、默认服务器配置 |

```
toolregistry (核心)
       ↓
toolregistry-server (工具服务器)
       ↓
toolregistry-hub (工具集合 + 服务器配置)
```

详情请参阅[生态系统](ecosystem.md)页面，了解所有包的详细概览。

## 快速开始

```bash
pip install toolregistry-server[all]
```

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app

# 创建注册表并注册工具
registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名称问候某人。"""
    return f"Hello, {name}!"

# 创建路由表和 FastAPI 应用
route_table = RouteTable(registry)
app = create_openapi_app(route_table)
```

[安装指南 →](usage/installation.md) | [快速开始 →](usage/quickstart.md)

## 主要特性

- **中央路由表**：统一的路由层，桥接 `ToolRegistry` 和协议适配器
- **OpenAPI 适配器**：将工具暴露为 RESTful HTTP 端点，自动生成 OpenAPI 模式
- **MCP 适配器**：通过 [模型上下文协议](https://modelcontextprotocol.io/) 暴露工具，用于 LLM 集成
- **认证**：内置 Bearer 令牌认证支持
- **命令行工具**：无需编写代码即可运行服务器
- **动态启用/禁用**：运行时工具状态管理，无需重启服务器
- **ETag 缓存**：通过 ETag 头实现 HTTP 缓存，提高 API 响应效率

## 架构

```mermaid
graph TD
    TR[ToolRegistry<br/>工具定义]
    RT[RouteTable<br/>中央路由层<br/><i>RouteEntry · RouteEntry · ...</i>]
    OA[OpenAPI 适配器<br/>FastAPI · REST]
    MA[MCP 适配器<br/>MCP SDK · LLM 集成]
    GA[gRPC 适配器<br/>计划中]

    TR --> RT
    RT --> OA
    RT --> MA
    RT -.-> GA
```

## 文档内容

- [**安装指南**](usage/installation.md) - 安装 `toolregistry-server` 及可选扩展
- [**快速开始**](usage/quickstart.md) - 几分钟内启动并运行
- [**配置**](usage/configuration.md) - CLI 的 JSON/JSONC 配置
- [**认证**](usage/authentication.md) - Bearer 令牌认证设置
- [**适配器**](adapters/) - OpenAPI 和 MCP 协议适配器
- [**命令行工具参考**](cli/) - 命令行接口使用
- [**API 参考**](api/) - 完整的 API 文档

## 许可证

ToolRegistry Server 使用 **MIT 许可证**。
