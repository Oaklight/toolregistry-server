---
title: 连接 OAuth 客户端
---

# 连接 OAuth 客户端

客户端如何连接取决于其厂商支持的客户端注册方式。工具服务器三种都支持：

| 方式 | 客户端身份由谁托管 | 适用场景 |
|------|--------------------|----------|
| **CIMD**（客户端 ID 元数据文档） | 客户端，以 URL 形式 | 客户端支持；无需在 AS 注册 |
| **DCR**（动态客户端注册） | AS，在首次连接时创建 | 客户端支持且 AS 已启用 DCR |
| **手动注册** | 你，在 AS 控制台 | 以上都不适用，或厂商要求机密客户端 |

工具服务器本身从不注册客户端。下文均使用占位符：`https://auth.example.com` 是 issuer，
`https://tools.example.com/mcp` 是 MCP 端点，`tools.example.com` 是公开主机。

## Gemini 自定义连接应用

Gemini 通过 Google 托管的 OAuth 客户端连接。该客户端是**机密客户端**，需要手动注册；
不支持动态注册。

1. 在 Gemini 的自定义连接应用表单中选择 OAuth 作为认证方式。表单会显示如下形式的重定向 URI：

   ```
   https://oauth-redirect.googleusercontent.com/r/<GOOGLE_PROJECT_ID>
   ```

2. 在授权服务器创建 Web 客户端，并**原样**填写该重定向 URI。不要添加末尾斜杠、更改协议，
   或使用 localhost 重定向。
3. 在 Gemini 表单中填入 AS 的客户端 ID 与客户端密钥（密钥来自你刚创建的 AS 客户端，
   绝不来自本仓库）。
4. 使用 MCP 端点 `https://tools.example.com/mcp`。Gemini 会自行完成发现：先取受保护资源
   元数据，再取 AS 元数据，然后打开授权 URL。
5. 请求 AS 会签发且服务器已映射的作用域。如果 Gemini 请求 AS 拒绝的作用域，授权会在回调前失败。

由于客户端是机密且预先注册的，AS 侧无需为它启用 DCR 或 CIMD。

## Claude 远程 / 自定义连接器

Claude 的远程 MCP 连接器从 401 质询开始，按以下顺序优先：

1. **CIMD** — 客户端给出其客户端元数据文档的 URL。Claude 版本支持时优先使用；AS 需允许
   基于 URL 的客户端 ID。
2. **DCR** — 客户端在 AS 自行注册。若 AS 支持，请启用动态客户端注册。
3. **手动凭据** — 连接器要求客户端 ID（和密钥）。仅在前两种都不可用时使用；先在 AS 注册
   连接器的重定向 URI。

连接器配置：

```
URL:    https://tools.example.com/mcp
```

Claude 不需要 issuer 或 JWKS URL；它从 `/.well-known/oauth-protected-resource/mcp` 与 AS
元数据读取。

## ChatGPT / OpenAI 自定义 MCP

OpenAI 的自定义 MCP 连接器同样从 401 质询发现授权服务器。顺序与 Claude 相同：

- 连接器提供 **CIMD** 时，在 AS 启用它；
- 否则启用 **DCR**，让连接器自行注册；
- 若连接器要求手动填写客户端信息，注册其重定向 URI 后粘贴客户端 ID/密钥。

填写 MCP URL `https://tools.example.com/mcp`。若连接器提示不支持动态注册，那是 AS 的设置，
不是工具服务器的设置。

## 使用 Swagger UI 访问对外文档

只要设置了 `--oauth-issuer`，对外 OpenAPI 文档就包含 `OAuth2` 授权码方案。在 Swagger UI 中：

1. 打开页面：独立 `openapi` 服务器为 `https://tools.example.com/docs`，使用默认挂载的
   gateway 为 `https://tools.example.com/openapi/docs`。
2. 点击 **Authorize**，选择 `OAuth2` 方案，填入 AS 签发的作用域并继续。
3. Swagger UI 把浏览器送到 `authorizationUrl`；AS 回调后它在 `tokenUrl` 换取令牌，并用得到的
   Bearer 令牌调用工具。

在 AS 注册 Swagger UI 的重定向 URI。Swagger UI 期望文档页面旁有一个
`oauth2-redirect.html`，OpenAPI 前端会自己提供该页面——独立服务器在监听器根路径，gateway
在挂载路径之下：

| 部署 | 需注册的重定向 URI |
|------|--------------------|
| `toolregistry-server openapi` | `https://tools.example.com/oauth2-redirect.html` |
| 使用默认 `/openapi` 挂载的 `gateway` | `https://tools.example.com/openapi/oauth2-redirect.html` |

`/docs` 页面会把对应位置传给 Swagger UI，因此即使 `--openapi-path` 改变了挂载路径，回调也会
落在服务提供的页面上。该页面有意不做鉴权——AS 重定向浏览器时不带 Bearer 令牌——并且不包含
任何密钥。

Authorize 对话框中的作用域来自 `--oauth-scopes`；它们必须是 AS 签发作用域的子集。

## 常见失败

| 提示 | 原因 | 处理 |
|------|------|------|
| AS 返回 `invalid_redirect_uri` | AS 注册的 URI 与客户端发送的不一致 | 从客户端错误或文档中逐字复制 URI |
| `invalid_scope` | 客户端请求了 AS 不支持的作用域 | 让 `--oauth-scopes` 成为 AS 支持作用域的子集 |
| 回调后 `unsupported_grant_type` / 401 | AS 签发的是不透明令牌或 ID token，而不是 JWT 访问令牌 | 让 AS 为该客户端签发 JWT 访问令牌 |
| DCR 报 `invalid_client` | AS 关闭了 DCR | 启用 DCR，或改用 CIMD/手动注册 |
| 登录成功后工具服务器仍返回 401 | audience/resource 不匹配 | 让 `aud` 或 `resource` 等于 `--oauth-audience` |
| 密钥轮换后 401 | 缓存的 JWKS 缺少新 `kid` | 30 秒后重试；确认 AS 已公布新密钥 |
| AS 回调后浏览器出现 404 | 注册的重定向 URI 不是服务提供的 `oauth2-redirect.html` 地址 | 注册 `<公开源><openapi 路径>/oauth2-redirect.html`；独立服务器在源根路径提供该页面 |

## 另见

- [使用外部授权服务器部署](oauth-deployment.md)。
- [OAuth 参考](../reference/oauth.md)。
