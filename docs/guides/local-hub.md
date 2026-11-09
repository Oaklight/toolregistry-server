# 本地 Hub

本地 hub 是同一台机器上为多个 MCP 客户端提供服务的一个常驻网关。没有它，每个启动
stdio 服务器的客户端都会各自拉起进程、各自维护注册表、工具 schema 与缓存；有了它，
注册表只构建一次，客户端各自只保留一个轻量的 stdio shim。

## 为什么

- **一份注册表。** 工具来源只注册一次，所有会话看到相同的工具与 schema。
- **共享缓存与连接。** OpenAPI 文档、MCP 会话与工具状态不再按客户端重复构建。
- **重启不会断开客户端。** shim 在本地应答 `initialize`，hub 恢复后在后台重连。

## 快速开始

启动一次 hub，可前台运行，也可 detached：

```bash
# detached：没有在运行的 hub 就拉起一个，并打印 endpoint
toolregistry-server local-hub ensure --config tools.yaml

# 前台运行，保留一个终端
toolregistry-server local-hub run --config tools.yaml
```

把只支持 stdio 的客户端指向 shim：

```json
{
  "mcpServers": {
    "toolregistry": {
      "command": "toolregistry-server",
      "args": ["hub-shim", "--name", "default", "--config", "/path/to/tools.yaml"]
    }
  }
}
```

shim 上的 `--config` 只在它需要自行拉起 hub 时使用；不提供时 shim 会等待运维手动启动
的 hub，`--no-spawn` 则完全禁止它拉起 hub。

## 生命周期

```bash
toolregistry-server local-hub status --name default   # 运行中退出 0，否则 3
toolregistry-server local-hub stop   --name default   # 没有在运行时退出 3
```

`run` 前台运行；`ensure` 复用已在运行的 hub，或拉起 detached 进程并打印 endpoint；
`status` 报告状态；`stop` 通知属主并等待其退出。四者都接受 `--name`，因此不同名字的
hub 可以在同一台机器上共存。

所有权由独占锁表示，属主 pid 发布在 endpoint 文档旁边。`stop` 只在同时满足「锁被持有、
pid 文件与 endpoint 指向同一进程、公布地址能接受连接」时才发送信号；崩溃 hub 遗留的
文档因此无法让它误杀无关进程。`status` 是纯读取：它不会删除自己检查的文档。

## 状态目录

状态根按以下顺序选择：

1. `$TOOLREGISTRY_HOME`（它命名的是根，而不是 `hub` 目录）。
2. POSIX 上的 `$XDG_RUNTIME_DIR/toolregistry`。
3. `~/.local/state/toolregistry`。
4. Windows 上的 `%LOCALAPPDATA%\toolregistry`。

其下的 `hub/` 保存 `<name>.json`（endpoint 文档）、`<name>.pid`（属主）、
`<name>.lock`（独占锁）、`<name>.sock`（默认 Unix 套接字）与 `<name>.log`
（detached hub 输出）。目录权限为 `0700`，每个文件为 `0600`。读取方会拒绝符号链接、
非常规文件、他人所有、或组/其他用户可读的文档。

## 连接客户端

- `initialize` 由 shim 自己应答，因此 hub 不在线时客户端也能启动；此时
  `tools/list` 为空。
- 没有 hub 时，一次工具调用返回一次 MCP 错误 `-32000`
  （`hub '<name>' is unavailable`），并在后台安排重连；重连成功后的下一次调用即可用。
- shim 以 0.25 秒起步、倍增到 5 秒上限重连，单次连接加初始化受
  `--connect-timeout`（默认 5 秒）限制。
- 当 hub 的工具列表与客户端最近一次拿到的列表不同时，客户端会收到
  `notifications/tools/list_changed` 并可刷新。比较范围包括名称、描述与输入 schema；
  列表未变化则保持安静。
- 文档地址无人接受连接时按「没有 hub」处理，而不是报错，因此陈旧文档不会阻塞启动。

## 密钥与隔离

`local-hub` 要求 `--keys-db`：共享 hub 服务多个客户端，每个客户端都要出示自己的密钥；
除非用 `--allow-operator-token` 启动，hub 会拒绝任何 MCP 请求（包括 `tools/list`）上的
静态运维令牌。为每个客户端创建一个密钥，并交给它自己的 shim：

```bash
toolregistry-server keys create --label laptop --keys-db hub-keys.db   # 仅打印一次
```

```json
{
  "mcpServers": {
    "toolregistry": {
      "command": "toolregistry-server",
      "args": ["hub-shim", "--name", "default", "--config", "/path/to/tools.yaml",
               "--key-file", "/home/me/.config/toolregistry/hub.key"]
    }
  }
}
```

`--key-file`（或 `TOOLREGISTRY_HUB_KEY`）携带客户端密钥；只有当 shim 需要自行拉起 hub
时才需要 `--keys-db`。每次调用按密钥计数：`--client-concurrency` 限制在途调用，密钥可用
`max_concurrency` 收紧但永不能放宽。任何在请求头中声称身份、工作目录或环境的请求
（`x-client-id`、`x-client-identity`、`x-client-cwd`、`x-client-env`、
`x-client-environment`、`x-forwarded-user`、`x-remote-user`）都会以 `400` 被拒绝，
因此请在前置代理上剥掉这类请求头。面板的 Clients 页与 `/ext/gateway/clients` 展示实时
的按客户端计数。

## 限制

- `local-hub` 与 `hub-shim` 目前仅支持 POSIX。Windows 上命令会被拒绝并提示改用
  `gateway`：那里字节区锁是强制的，被终止的进程无法清理自己的 endpoint，且没有
  Windows CI 验证生命周期。
- `--idle-timeout` 默认为 `0`，hub 会一直常驻直到 `stop`。设为非 0 时，在指定秒数内
  既无请求也无打开的 MCP/SSE 会话后退出。
- 客户端身份与工具作用域来自 hub 上的入站 API 密钥，而不是 shim；直接使用
  streamable HTTP 的客户端同样受作用域限制。见[认证](authentication.md)。
