---
title: OAuth 参考
---

# OAuth 资源服务器参考

本页汇总 OAuth 2.0 资源服务器的全部选项。选项背后的行为——令牌校验、作用域映射、凭据优先级
与限制——见[认证指南](../guides/authentication.md)。

服务器**校验**访问令牌，不签发令牌。令牌必须由外部授权服务器（AS）用 RS256 或 ES256 签名。

## 选项

四个服务命令——`openapi`、`mcp`（HTTP 传输）、`gateway` 与 `local-hub`——接受同一组选项。
未设置 `--oauth-issuer` 时 OAuth 关闭。

| 选项 | 默认值 | 含义 |
|------|--------|------|
| `--oauth-issuer URL` | - | 接受的精确 `iss`。设置后启用 OAuth 校验 |
| `--oauth-audience URL` | 按命令推导 | 期望的 `aud` 或 RFC 8707 `resource` 值 |
| `--oauth-jwks URL` | 自动发现 | JWKS 端点，覆盖发现结果 |
| `--oauth-scope SCOPE=NAMESPACE[,NAMESPACE]` | - | 把一个令牌作用域映射到命名空间模式；可重复。未映射的作用域不授予任何权限 |
| `--oauth-scopes "SCOPE ..."` | 配置 issuer 时省略 | 在受保护资源元数据与 OpenAPI OAuth2 方案中公布的作用域，空格分隔 |
| `--oauth-authorization-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 的授权端点 |
| `--oauth-token-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 的令牌端点 |
| `--oauth-refresh-url URL` | - | 可选的 OpenAPI `refreshUrl`；仅显式设置时公布 |

三个端点选项只影响对外提供的 OpenAPI 文档。令牌校验使用 issuer、audience 与 JWKS。

### 优先级

- **JWKS**：`--oauth-jwks`，否则 RFC 8414 元数据中的 `jwks_uri`，否则 OIDC 发现，最后是
  `<issuer>`（去掉末尾斜杠）`/.well-known/jwks.json`。issuer 带路径时先尝试 RFC 8414
  形式（well-known 段在路径之前），再尝试后缀形式。
- **OpenAPI 端点**：显式选项，否则 RFC 8414 或 OIDC 元数据中的 `authorization_endpoint` /
  `token_endpoint`。两者都无法提供完整端点时，OAuth2 方案会被省略并记录警告。
- **Audience**：`--oauth-audience`，否则按命令推导的 URL（见下表）。不会隐式回退到 issuer。

令牌的 `aud` 或 RFC 8707 `resource` 包含配置的 audience 时才被接受。

## 各命令的行为

| 命令 | 推导的 audience | 说明 |
|------|----------------|------|
| `openapi` | `http://<host>:<port>` | host 为绑定地址（`0.0.0.0`、`::`）时告警；反向代理后请用 `--oauth-audience` 传入公开源 |
| `mcp` | `--server-url`，否则 `http://<host>:<port>/mcp` | 仅 HTTP 传输；`stdio` 会警告并忽略 OAuth 选项 |
| `gateway` | `--server-url`，否则 `http://<host>:<port><mcp 路径>` | 在 `/.well-known/oauth-protected-resource<mcp 路径>` 公布 PRM；绑定 Unix socket 且无 `--server-url` 时不公布 PRM，401 只发送裸 `WWW-Authenticate: Bearer` |
| `local-hub` | `--server-url`，否则必须提供 `--oauth-audience` | hub 绑定 Unix socket 或 `127.0.0.1:0`，两者都不是公开 audience；设置 `--oauth-issuer` 时必须显式给出 audience，无论是否使用 `--socket` |

## 服务器公布与期望的内容

| 内容 | 位置 | 说明 |
|------|------|------|
| 受保护资源元数据（RFC 9728） | `/.well-known/oauth-protected-resource<mcp 路径>` | `resource`、`authorization_servers: [issuer]`、`bearer_methods_supported`。仅在设置 `--oauth-scopes` 时包含 `scopes_supported` |
| 401 质询 | 每个被拒绝的请求 | `WWW-Authenticate: Bearer resource_metadata="<PRM URL>"` |
| OpenAPI 安全方案 | `/openapi.json` | `OAuth2`（`authorizationCode`）；同时接受密钥或静态令牌时再加 `HTTPBearer` |
| 期望的令牌声明 | 每个请求 | `iss`（精确）、`aud` 或 `resource`、`exp`、可选 `nbf`；`scope` 用于命名空间映射 |

## 配置文件

目前 OAuth 只能通过命令行选项配置。工具来源配置文件（`--config`）没有 OAuth 段，因此容器或
服务管理器必须传递这些选项，或使用由环境变量生成的等价命令行。见[已知缺口](#已知缺口)。

独立 `openapi` 命令上的 OAuth 需要带加密后端的 PyJWT：请安装
`toolregistry-server[openapi]`。基础安装仍不含第三方包，`[gateway]`/`[mcp]` 已自带该依赖。

## 已知缺口

以下能力尚未提供：

- 配置文件中没有 OAuth 段。
- 不支持令牌内省（RFC 7662）：AS 必须签发 JWT。
- 没有内置授权服务器，也没有客户端注册命令。

## 另见

- [认证指南](../guides/authentication.md) — 校验规则与凭据优先级。
- [使用外部授权服务器部署](../guides/oauth-deployment.md)。
- [连接 OAuth 客户端](../guides/oauth-clients.md)。
- 变更日志中 OAuth 资源服务器（#234）与 OpenAPI 方案（#235）条目——本页不重复其内容。
