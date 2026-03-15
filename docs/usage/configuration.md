# 配置

命令行工具使用 JSON 或 JSONC（带注释的 JSON）配置文件来定义要加载的工具及其服务方式。

## 配置文件格式

```json
{
  "mode": "denylist",
  "disabled": [],
  "enabled": [],
  "tools": [
    {
      "module": "toolregistry_hub.calculator",
      "class": "Calculator",
      "namespace": "calculator",
      "enabled": true
    },
    {
      "module": "toolregistry_hub.datetime_tool",
      "class": "DateTime",
      "namespace": "datetime",
      "enabled": true
    }
  ]
}
```

## 配置字段

### `mode`

控制工具的过滤方式：

- **`denylist`**（默认）：加载所有工具，除了 `disabled` 中列出的。这是最常用的模式。
- **`allowlist`**：仅加载 `enabled` 中列出的工具。

### `disabled`

要排除的命名空间前缀列表（与 `denylist` 模式配合使用）：

```json
{
  "mode": "denylist",
  "disabled": ["websearch", "filesystem"]
}
```

### `enabled`

要包含的命名空间前缀列表（与 `allowlist` 模式配合使用）：

```json
{
  "mode": "allowlist",
  "enabled": ["calculator", "datetime"]
}
```

### `tools`

要加载的工具定义数组。每个条目指定：

| 字段 | 类型 | 描述 |
|------|------|------|
| `module` | string | 包含工具的 Python 模块路径 |
| `class` | string | 要实例化的类名（使用函数时可选） |
| `namespace` | string | 工具路由的命名空间前缀 |
| `enabled` | boolean | 工具是否初始启用 |

## 命名空间匹配

命名空间过滤使用层级前缀匹配。例如，禁用 `"web"` 也会禁用 `"web/brave_search"` 和 `"web/tavily"`。

```json
{
  "mode": "denylist",
  "disabled": ["web"]
}
```

这将禁用 `web` 命名空间层级下的所有工具。

## 环境变量

命令行工具支持从 `.env` 文件加载环境变量：

```bash
# .env
BRAVE_API_KEY=your-key-here
TAVILY_API_KEY=your-key-here
```

```bash
# 从默认 .env 文件加载
toolregistry-server openapi --config config.json

# 从自定义 .env 文件加载
toolregistry-server openapi --config config.json --env-file .env.production

# 跳过 .env 加载
toolregistry-server openapi --config config.json --no-env
```

## JSONC 支持

配置文件支持带注释的 JSON (JSONC)，允许内联文档：

```jsonc
{
  // 运行模式："denylist" 或 "allowlist"
  "mode": "denylist",

  // 要排除的命名空间
  "disabled": [
    "filesystem"  // 在生产环境中禁用文件系统工具
  ],

  "tools": [
    {
      "module": "toolregistry_hub.calculator",
      "class": "Calculator",
      "namespace": "calculator",
      "enabled": true  // 始终可用
    }
  ]
}
```
