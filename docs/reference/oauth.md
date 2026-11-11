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
| `--oauth-scope SCOPE=ENTRY[,ENTRY]` | - | 把一个令牌作用域映射到命名空间模式与工具覆盖；可重复。每个条目是命名空间模式、`+tool-name`（允许单个工具）或 `-tool-name`（拒绝单个工具）。未映射的作用域不授予任何权限 |
| `--oauth-scopes "SCOPE ..."` | 配置 issuer 时省略 | 在受保护资源元数据与 OpenAPI OAuth2 方案中公布的作用域，空格分隔 |
| `--oauth-authorization-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 的授权端点 |
| `--oauth-token-url URL` | 来自 AS 元数据 | OpenAPI `authorizationCode` 的令牌端点 |
| `--oauth-refresh-url URL` | - | 可选的 OpenAPI `refreshUrl`；仅显式设置时公布 |
| `--oauth-concurrency-claim CLAIM` | `max_concurrency` | 可能携带客户端在途调用上限的令牌声明；非正整数会被忽略 |
| `--oauth-client-concurrency CLIENT=LIMIT` | - | 单个 OAuth 客户端的静态在途调用上限；可重复。有效的并发声明优先于该表 |
| `--oauth-introspection-url URL` | - | RFC 7662 内省端点。设置后，每个本地校验通过的令牌都会向它确认 |
| `--oauth-introspection-client-id ID` | - | 内省端点使用的客户端 id（HTTP Basic）；与 URL 同时必需 |
| `--oauth-introspection-client-secret SECRET` | - | 内省端点使用的客户端密钥（HTTP Basic）；与 URL 同时必需 |
| `--oauth-introspection-cache-ttl SECONDS` | `5` | 一次内省结果的复用时长；它决定被撤销的令牌还会被接受多久。`0` 关闭缓存 |
| `--oauth-introspection-optional` | 关闭 | 内省端点失败时回退到本地校验；默认情况下失败即拒绝令牌 |
| `--oauth-allow-private-hosts` | 关闭 | 允许发现、JWKS 与内省访问回环、链路本地或私有地址；出站地址策略默认拒绝此类主机 |

三个端点选项只影响对外提供的 OpenAPI 文档。令牌校验使用 issuer、audience 与 JWKS；
内省使用它自己的端点。

### 作用域条目

`--oauth-scope` 的形式是 `SCOPE=ENTRY[,ENTRY]`。每个条目可以是：

| 条目 | 作用 |
|------|------|
| `calculator` 或 `web/*` | 允许该命名空间模式。`*` 允许所有命名空间 |
| `+tool-name` | 在命名空间列表之外允许单个工具 |
| `-tool-name` | 在允许的命名空间内拒绝单个工具 |

例如 `--oauth-scope "tools:read=calculator,+web-search,-calculator-danger"` 允许
`calculator` 命名空间，在命名空间未列出时仍允许 `web-search`，并拒绝
`calculator-danger`。两个被授予的作用域对同一工具意见不一致时，拒绝优先，因此新增
作用域不会放宽已有的拒绝。作用域从 `scope` 声明读取，缺失时回退到部分身份平台使用
的 `scp`（字符串或 JSON 数组）。

### 优先级

- **JWKS**：`--oauth-jwks`，否则 RFC 8414 元数据中的 `jwks_uri`，否则 OIDC 发现，最后是
  `<issuer>`（去掉末尾斜杠）`/.well-known/jwks.json`。issuer 带路径时先尝试 RFC 8414
  形式（well-known 段在路径之前），再尝试后缀形式。去掉末尾斜杠同样作用于最后的回退：
  issuer 配置为 `https://auth.example.com/` 且元数据不可达时，仍会请求
  `https://auth.example.com/.well-known/jwks.json`，而不是出现双斜杠。`iss` 本身仍逐字节比较。
- **OpenAPI 端点**：显式选项，否则 RFC 8414 或 OIDC 元数据中的 `authorization_endpoint` /
  `token_endpoint`。两者都无法提供完整端点时，OAuth2 方案会被省略并记录警告。
- **Audience**：`--oauth-audience`，否则按命令推导的 URL（见下表）。不会隐式回退到 issuer。
- **并发**：`--oauth-concurrency-claim` 指定的声明中的正整数，否则该客户端在
  `--oauth-client-concurrency` 中的条目。两者都只能收紧进程的 `--client-concurrency`。

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
| 期望的令牌声明 | 每个请求 | `iss`（精确）、`aud` 或 `resource`、`exp`、可选 `nbf`；`scope`（或 `scp`）用于作用域映射；`client_id` 或 `azp` 表示客户端身份；`--oauth-concurrency-claim` 指定的声明（默认 `max_concurrency`）可携带在途上限 |

## Swagger UI 重定向页

Swagger UI 在授权码流程结束时打开文档页旁的 `oauth2-redirect.html`。OpenAPI 前端会自己
提供该页面，两个后端都如此：

| 后端 | 页面 | Swagger UI 设置 |
|------|------|-----------------|
| 独立 `openapi`（`httpserver`） | 监听器根路径的 `GET /oauth2-redirect.html` | `/docs` 把 `oauth2RedirectUrl` 设为相对路径 `oauth2-redirect.html` |
| `gateway`（ASGI） | `GET <openapi 路径>/oauth2-redirect.html`，例如 `/openapi/oauth2-redirect.html` | `/docs` 把 `oauth2RedirectUrl` 设为 `<openapi 路径>/oauth2-redirect.html`，文档 URL 设为 `<openapi 路径>/openapi.json` |

两者都跟随配置的路径，因此 `--openapi-path /rest` 会把页面移到
`/rest/oauth2-redirect.html`。gateway 在 Bearer 包装之前应答该路径，独立路由则免于
令牌检查。

!!! warning "重定向页不做鉴权"
    授权服务器重定向浏览器到这里时不带 Bearer 令牌，因此该路由不能要求令牌；加上检查会把
    回调变成 `401`，流程无法完成。页面是静态的，不加载任何外部资源，不包含密钥，只把授权
    响应交回打开它的 Swagger UI 窗口。

该页面与上游 `swagger-ui-dist` 重定向脚本的解析逻辑一致：对每个回调参数做百分号解码，
接受带前导 `?` 的 hash 形式（`#?code=...`），并识别 `accessCode`、`authorizationCode`
与 `authorization_code` 三种流程名。

在授权服务器上注册的重定向 URI 就是该页面的公开地址：独立服务器为
`<公开源>/oauth2-redirect.html`，gateway 为 `<公开源><openapi 路径>/oauth2-redirect.html`。

## 令牌内省

本地 JWKS 校验看不到令牌在签发后被授权服务器撤销或禁用；令牌会一直有效到过期。配置 RFC
7662 内省端点可以缩小这个窗口：

```bash
toolregistry-server gateway --config config.yaml \
  --oauth-issuer https://auth.example.com \
  --oauth-introspection-url https://auth.example.com/introspect \
  --oauth-introspection-client-id toolregistry \
  --oauth-introspection-client-secret "$INTROSPECTION_SECRET" \
  --oauth-introspection-cache-ttl 5
```

- 本地校验通过后才调用端点，请求体用表单提交令牌与 `token_type_hint=access_token`，
  客户端凭据放在 HTTP Basic 中。只有 `active: true` 的应答才接受令牌。
- 内省不会让不透明令牌可用：本地仍先做 JWT 校验，因此 AS 必须继续签发 RS256 或 ES256 JWT。
- 结果按令牌缓存 `--oauth-introspection-cache-ttl`（默认 5 秒）。缓存只保存令牌的
  SHA-256 摘要，从不保存令牌本身，肯定与否定应答都会缓存。该 TTL 就是被撤销令牌仍被
  接受的最长时间；`0` 关闭缓存，每个请求都内省。
- 端点失败时拒绝令牌。`--oauth-introspection-optional` 改为回退到本地结果，用撤销延迟
  换取可用性。失败结果同样按 TTL 缓存，因此一次故障每个 TTL 只探测一次，而不是每个请求
  都等一次超时。
- 令牌与请求体从不写入日志；失败只记录端点与异常类型。
- 设置 URL 却没有客户端 id 与密钥会导致启动失败。不设置 URL 则只做本地校验，这是默认行为。

端点必须能从工具服务器访问。若它位于私有网络，请向服务器开放，或使用
`--oauth-introspection-optional`。

## Principal、作用域与记账

OAuth 令牌与 API 密钥产生同类 principal，因此授权层在每个前端上的行为一致：

- **名称** —— `sub` 声明，否则 `client_id`/`azp`，否则 `oauth`。
- **客户端** —— `client_id`，否则 `azp`。它与 `sub` 分开保存，因此同一用户的两个客户端是
  不同的 principal，作用域、执行日志行与计数都各自独立。
- **作用域** —— 只由 `--oauth-scope` 映射决定。未映射的作用域不授予任何权限，
  `+tool`/`-tool` 条目在命名空间之上生效。
- **并发** —— `--oauth-concurrency-claim` 或 `--oauth-client-concurrency`；只能收紧进程的
  `--client-concurrency`，不能放宽。预算属于 OAuth 客户端：该客户端代表的所有用户共享
  同一个在途上限，限流键为 `oauth:<client_id>`。预算按客户端单调不增——声明值宽于当前
  生效的上限时不会提高它，更窄的声明则会降低它。记账仍按 principal：执行日志行与计数
  同时带上客户端与 `sub`（`oauth:<client_id>:<label>`），因此同一客户端的两个用户仍可区分。
- **列表** —— MCP 的 `tools/list` 与 OpenAPI 前端的 `/tools`、`/openapi.json` 只显示该
  令牌可调用的工具。
- **执行日志** —— 每次调用记录 `client_id`（OAuth 客户端）与 `outcome`（`ok`、`error`、
  `busy` 或 `denied`）。被拒绝的调用以 `ERROR` 状态记录，精确结果放在 metadata 中，
  参数为空，因为未被允许的调用的载荷对审计该决定没有用处。配置 issuer 会像配置密钥
  存储一样默认开启执行日志；`--no-execution-log` 可关闭。

每次请求都会重新读取令牌及其声明，因此启用内省后，作用域变更或撤销在下一个请求生效；
未启用时，在下一个令牌生效。无需重启。

## 配置文件

工具来源配置文件（`--config`）可以带 `oauth` 段。所有 OAuth 设置都可在其中给出，键名
是对应选项去掉 `oauth_` 前缀，与 `introspection_url` 一致；每个键都以命令行选项优先，
文件只在该选项缺席时生效。

```yaml
oauth:
  issuer: https://auth.example.com
  audience: https://tools.example.com/mcp
  jwks: https://auth.example.com/.well-known/jwks.json
  scope:
    tools:read: [calculator, web/*, +web-search, -calculator-danger]
    tools:admin: web/*
  scopes: [openid, profile, email]
  authorization_url: https://auth.example.com/authorize
  token_url: https://auth.example.com/token
  refresh_url: https://auth.example.com/refresh
  introspection_url: https://auth.example.com/introspect
  introspection_client_id: toolregistry
  introspection_client_secret: change-me
  introspection_cache_ttl: 5
  introspection_optional: false
  concurrency_claim: max_concurrency
  client_concurrency:
    reports-bot: 2
```

`issuer` 是开启 OAuth 唯一需要的键：文件中有它而命令行没有任何 OAuth 选项时，行为与
`--oauth-issuer` 完全相同。`audience` 取代推导出的 audience。`jwks`、
`authorization_url`、`token_url` 与 `refresh_url` 覆盖对应选项的元数据发现结果。其余键
是 `introspection_url`、`introspection_client_id`、`introspection_client_secret`、
`introspection_cache_ttl`、`introspection_optional`、`concurrency_claim`、
`client_concurrency` 与 `clients`。

| 键 | 选项 | 接受的值 |
|----|------|----------|
| `issuer` | `--oauth-issuer` | 字符串 |
| `audience` | `--oauth-audience` | 字符串 |
| `jwks` | `--oauth-jwks` | 字符串 |
| `authorization_url` | `--oauth-authorization-url` | 字符串 |
| `token_url` | `--oauth-token-url` | 字符串 |
| `refresh_url` | `--oauth-refresh-url` | 字符串 |
| `scope` | `--oauth-scope` | 从作用域到其条目的映射；每个值是一个条目列表或一个逗号分隔的字符串。也接受选项的 `[SCOPE=ENTRY[,ENTRY], ...]` 列表形式 |
| `scopes` | `--oauth-scopes` | 字符串列表，或一个空格分隔的字符串 |
| `clients` | 无对应选项 | 映射列表，每个条目对应本服务器要向其提交凭据的来源；仅限配置文件，没有命令行等价形式 |

`scope` 的条目语法与选项相同：命名空间模式、`+tool-name`（允许单个工具）或
`-tool-name`（拒绝单个工具）。未映射的作用域仍然不授予任何权限，因此在由文件驱动的
部署中，最小权限由 `scope` 表达。

`clients` 是同一个段的另一个方向：本服务器向它调用的接入点提交的 OAuth 凭据，
而不是它接受的凭据。每个条目用 `namespace:<name>` 或 `url:<url>` 选定一个 HTTP(S)
MCP 来源，并带有 `issuer` 或 `discovery_url`、`client_id`、`client_secret` 与
`client_secret_env` 中恰好一个、可选的 `scopes`、`grant`（`client_credentials` 或
`authorization_code`）、可选的 `token_endpoint`、`client_auth`（`client_secret_post`
或 `client_secret_basic`）以及绝对路径的 `token_store`。两个条目不能选中同一来源；
未知键、相对路径的 `token_store`，或来源已设置 `Authorization` 头，都会使加载失败。
令牌保存在配置文件之外的 SQLite 中。客户端密钥本身从不写入其中，但会保存一个加盐、多次迭代的指纹，以便在轮换密钥时丢弃过期的重新授权判定，同时不保存密钥。start/complete 流程由 gateway 面板驱动，见下文。面板的连接测试不会附加上游凭据，当声明选中该来源时，测试结果会说明这一点。参见
[部署指南](../guides/oauth-deployment.md#受保护-mcp-来源的上游凭据)。

已存令牌在被 MCP 服务器拒绝之前会一直复用。连接时来源返回 HTTP 401/403，会丢弃
已存的访问令牌并累计拒绝次数，同时保留刷新令牌。下一次连接在行中仍有刷新令牌时使用
刷新令牌；否则 `client_credentials` 来源立即重新获取令牌，而 `authorization_code`
来源报告 `reauth_required` 并等待运维处理。连续第二次拒绝会锁定
`authorization_code` 来源，并使 `client_credentials` 来源丢弃该行、改用当前密钥
重试。计数仅在连接端到端成功后才清零，因此在令牌端点获取的令牌在来源接受之前不被
信任。

存储行记录签发时的来源 URL、授权方式、请求的作用域、令牌端点以及客户端凭据的
指纹。令牌只会提交给其签发的 URL；把选择器改指向另一个主机时会重新签发，而不是
重放旧令牌。确定性授权拒绝会一直锁定，直到运维重新授权或修正文件中的客户端凭据：
issuer、client id、令牌端点、授权方式、作用域、密钥或客户端认证方式。issuer、
client id、授权方式或目标 URL 变化时，该行会被直接丢弃。

发现文档仅在其 `issuer` 等于配置的 issuer（RFC 8414 第 3.3 节）时使用；对于只给出
`discovery_url` 的情况，文档必须声明 issuer。其中的端点必须是绝对的 `http` 或
`https` URL，缺少可用 `token_endpoint` 的文档不会被缓存，以便下一个候选仍可作答。
缓存的文档在 300 秒后会重新获取，因此即使端点移动时没有返回 404/410，也无需重启即可
生效；重新获取失败时，会继续使用缓存文档直到下一次尝试，并记录一条警告。

会使 `oauth.clients` 选择器选不中来源或选中多个来源的面板修改，会在写入前被拒绝；
写入失败时令牌行保持不变。错误文本会被脱敏：URL 中的用户信息、凭据形式的参数、
客户端密钥以及 HTTP Basic 的 base64 凭据。长度不足六个字符的密钥不会在自由文本中
替换，否则会改写普通单词。

令牌行属于创建它的声明。存储中记录了哪个选择器声明在哪条 `token_store` 路径上，
下一次启动会删除记录中已不在文件里的选择器所对应的行——包括整个 `clients` 段是在
服务器停止期间被删除的情况。记录中未出现的行（例如由共享同一数据库的另一个配置
写入的行）会被保留。

响应中没有 `expires_in` 的令牌按一小时计算寿命，起算于存储行的 `updated_at`。
省略该字段的身份提供方因此会使每个来源每小时发起一次令牌请求，而不是得到一个永不
刷新的令牌。

来自发现文档的令牌端点若返回 404 或 410，会丢弃缓存的授权服务器元数据并重新发现
一次；第二次 404 视为确定性失败。显式设置的 `token_endpoint` 从不重新发现。

优先级是替换，而不是合并。只要给出了任何一个 `--oauth-scope` 值，文件中的 `scope`
映射就整体被忽略，而不是与之合并；`client_concurrency` 同样如此。可重复的
`--oauth-scope` 选项默认是空列表，因此"未给出值"与"完全省略"无法区分：空列表会回退到
文件，而文件中的 `scope` 无法在命令行上覆盖为*空*。需要时请删除该键。

该段会被校验：服务器不读取的键会在启动时被拒绝，否则像 `introspection_ur1` 这样的
拼写错误会让一项安全控制静默关闭。类型错误的值会指明键名，例如
`oauth.scope must be a mapping from a scope to its entries, or a list of
SCOPE=ENTRY[,ENTRY] strings` 或 `oauth.scopes must be a string or a list of
strings`。即使同时给出了对应选项，类型错误的标量值也会被拒绝，因为运行时由选项
取胜，否则该笔误要等到选项被移除才会暴露。非法的按客户端上限会指明来源，例如
`oauth.client_concurrency['reports-bot'] must be a positive integer`。

文件中设置了某项、但文件与选项都没有 issuer 时，与选项的情形一样是启动错误。audience
的检查针对合并后的值：`openapi`、`mcp` 与 `gateway` 命令在能推导时推导 audience，而
`local-hub` 需要显式的 `audience` 或 `--server-url`，因为它的绑定地址永远不是公开
audience。

令牌校验使用随包内置的 `zerodep` JWS 模块，因此任何 OAuth 路径都不需要第三方加密包。
支持 RS256/RS384/RS512、PS256/PS384/PS512 与 ES256/ES384；默认白名单为 `RS256` 与
`ES256`。独立 `openapi` 命令因此可在基础安装上使用。

### 回调白名单、出站筛查与面板授权流程

`redirect_uris` 列出 `authorization_code` 条目可使用的回调 URL，按精确匹配比较。
`start_authorization` 会在发现文档或写入待处理行之前拒绝其他 `redirect_uri`；列表为空时
拒绝所有流程；完成时会再次用当前列表比对待处理行中的 URI，因此重载移除的回调无法完成
此前已开始的流程。

比较是文本比较，而浏览器会规范化它发送的 URL：主机名转为小写，默认端口被去掉。请按这种
规范形式书写条目（`https://panel.example.com/ext/gateway/oauth/callback`，而不是
`https://Panel.Example.com:443/...`），否则回调会被拒绝。


服务器自身发起的每个 OAuth 请求——发现、JWKS、内省以及上游令牌 POST——都会在建立连接
之前经过 `toolregistry.netpolicy` 筛查。回环、链路本地或私有主机（例如
`169.254.169.254`）默认被拒绝，除非运维显式开启：资源服务器使用
`--oauth-allow-private-hosts` 或 `oauth.allow_private_hosts`，`oauth.clients` 使用
逐条目的 `allow_private_hosts: true`。在上游一侧，拒绝属于暂时性失败并保留已存凭据；
其消息既不包含 URL，也不包含解析出的地址。

每个经过筛查的请求都拒绝重定向，因此 3xx 无法把请求改指到另一台主机，也无法在那里重放
内省凭据。

与 MCP、OpenAPI 来源一样，该策略止于拨号之前：它解析域名、判断每个应答，然后把 URL
交给 HTTP 客户端，由客户端再次解析。若两次解析之间 DNS 应答改变（DNS rebinding，
core #399），请求仍可能到达第一次解析未返回的地址。要把已检查的地址固定到连接上，需要
支持指定地址的传输层；OAuth 客户端使用 `urllib`，它不支持。


gateway 面板负责驱动授权码流程。带有 `reauth_required` 的来源会显示 **授权** 按钮，
它以面板的回调 URL `<面板源>/ext/gateway/oauth/callback` 调用
`POST /ext/gateway/sources/<id>/authorize`。请把这个 URL 原样写入条目的
`redirect_uris`，并在授权服务器注册。回调完成兑换、重试该来源，并返回一个小型 HTML
页面，既不回显授权码也不回显令牌。没有 `oauth.clients` 声明时，两个路由都返回 503。

服务器运行期间，每个已应用、受 OAuth 保护的 MCP 来源每 300 秒检查一次。令牌端点的
确定性拒绝，或短探测会话收到的 HTTP 401/403，会注销该来源的工具并将其标记为
`failed` 与 `reauth_required`；令牌轮换后会用新请求头重新连接；暂时性失败在下一轮之前
不做任何改变。因此被撤销的上游授权会在一个周期内显现，而不必等到下次重启。

两个进程可以共享同一个令牌存储。某个来源的令牌获取会持有 `<token_store>.locks/` 中
逐来源文件上的排他 `flock`（目录 `0700`，文件 `0600`），因此第二个进程会等待、重新读取
该行并复用令牌，而不是再请求一次；使用即轮换刷新令牌的身份提供方不会再作废其中一个
进程的结果。持有者退出时内核会释放该锁，因此不存在过期租约；等待超过 30 秒属于暂时性
存储错误。存储必须位于本地文件系统：`flock` 在 NFS 上不可靠。

`apply_config` 与 `registry_from_config` 接受 `oauth_provider`。未提供时，若配置中
`oauth.clients` 有条目，会抛出 `ConfigError`，而不是以未认证方式连接受保护来源。

启动时并发解析受保护来源的令牌（最多同时 8 个），然后按声明顺序注册；网络 I/O 不再
占用状态读取所用的锁，因此慢速来源连接期间面板仍能及时响应。

## 已知缺口

以下能力尚未提供：

- 没有内置授权服务器，也没有客户端注册命令。
- 内省不会让不透明令牌可用：本地 JWT 校验仍先执行。

## 另见

- [认证指南](../guides/authentication.md) — 校验规则与凭据优先级。
- [使用外部授权服务器部署](../guides/oauth-deployment.md)。
- [连接 OAuth 客户端](../guides/oauth-clients.md)。
- 变更日志中 OAuth 资源服务器（#234）与 OpenAPI 方案（#235）条目——本页不重复其内容。
