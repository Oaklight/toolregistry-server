---
title: 首页
author: Oaklight
hide:
  - navigation
---

<section class="tr-hero" markdown>
<p class="tr-kicker">将注册表作为 API 服务</p>

# 一个路由表，多种协议。

<p class="tr-hero__desc">将标准化的 ToolRegistry 暴露为多种 API 端点，并围绕中央 RouteTable 提供认证、配置与部署基础能力。</p>

<p class="tr-badges">
  <a href="https://pypi.org/project/toolregistry-server/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/toolregistry-server?labelColor=475569&color=166534"></a>
  <a href="https://github.com/Oaklight/toolregistry-server/actions"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Oaklight/toolregistry-server/ci.yml?branch=master&label=CI&labelColor=475569&color=14532d"></a>
  <a href="https://opensource.org/licenses/MIT"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-14532d?labelColor=475569"></a>
</p>

<div class="tr-actions" markdown>
[快速开始](get-started/quickstart.md){ .tr-button .tr-button--primary }
[OpenAPI 指南](guides/openapi.md){ .tr-button .tr-button--secondary }
[MCP 指南](guides/mcp.md){ .tr-button .tr-button--secondary }
</div>
</section>

## toolregistry-server 是什么？

`toolregistry-server` 是 [ToolRegistry 生态系统](ecosystem.md)中的**服务层**。它将包含 Python 函数的 `ToolRegistry` 暴露为网络服务——通过 OpenAPI 提供 REST API，或通过 Model Context Protocol (MCP) 提供 LLM 工具接口。

```
toolregistry（核心）          → 定义和管理工具
toolregistry-server（本项目） → 通过 OpenAPI 和 MCP 提供工具服务
toolregistry-hub（扩展）      → 精选的即用型工具集合
```

## 快速开始

```bash
pip install toolregistry-server[all]
```

```python
from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app

registry = ToolRegistry()

@registry.register
def greet(name: str) -> str:
    """按名字问候某人。"""
    return f"Hello, {name}!"

route_table = RouteTable(registry)
app = create_openapi_app(route_table)
```

[安装 →](get-started/installation.md) · [快速开始 →](get-started/quickstart.md) · [示例 →](examples/)

## 核心特性

- **中央路由表** — 连接 `ToolRegistry` 和协议适配器的统一路由层
- **OpenAPI 适配器** — 自动生成 schema 的 RESTful HTTP 端点
- **MCP 适配器** — 用于 LLM 集成的 [Model Context Protocol](https://modelcontextprotocol.io/)
- **认证** — 内置 Bearer 令牌支持
- **CLI** — 通过配置文件运行服务器，无需编写代码
- **动态启用/禁用** — 运行时切换工具状态，无需重启
- **ETag 缓存** — 通过 ETag 头实现高效 HTTP 缓存

## 架构

```mermaid
graph TD
    TR[ToolRegistry<br/>工具定义]
    RT[RouteTable<br/>中央路由层<br/><i>RouteEntry · RouteEntry · ...</i>]
    OA[OpenAPI 适配器<br/>FastAPI · REST]
    MA[MCP 适配器<br/>MCP SDK · LLM 集成]
    GA[gRPC 适配器<br/>规划中]

    TR --> RT
    RT --> OA
    RT --> MA
    RT -.-> GA
```

## 许可证

ToolRegistry Server 使用 **MIT 许可证**。
