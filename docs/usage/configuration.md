# Configuration

The CLI uses JSON or JSONC (JSON with comments) configuration files to define which tools to load and how to serve them.

## Configuration File Format

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

## Configuration Fields

### `mode`

Controls how tools are filtered:

- **`denylist`** (default): Load all tools except those listed in `disabled`. This is the most common mode.
- **`allowlist`**: Load only tools listed in `enabled`.

### `disabled`

A list of namespace prefixes to exclude (used with `denylist` mode):

```json
{
  "mode": "denylist",
  "disabled": ["websearch", "filesystem"]
}
```

### `enabled`

A list of namespace prefixes to include (used with `allowlist` mode):

```json
{
  "mode": "allowlist",
  "enabled": ["calculator", "datetime"]
}
```

### `tools`

An array of tool definitions to load. Each entry specifies:

| Field | Type | Description |
|-------|------|-------------|
| `module` | string | Python module path containing the tool |
| `class` | string | Class name to instantiate (optional if using functions) |
| `namespace` | string | Namespace prefix for the tool's routes |
| `enabled` | boolean | Whether the tool is initially enabled |

## Namespace Matching

Namespace filtering uses hierarchical prefix matching. For example, disabling `"web"` will also disable `"web/brave_search"` and `"web/tavily"`.

```json
{
  "mode": "denylist",
  "disabled": ["web"]
}
```

This disables all tools under the `web` namespace hierarchy.

## Environment Variables

The CLI supports loading environment variables from `.env` files:

```bash
# .env
BRAVE_API_KEY=your-key-here
TAVILY_API_KEY=your-key-here
```

```bash
# Load from default .env file
toolregistry-server openapi --config config.json

# Load from custom .env file
toolregistry-server openapi --config config.json --env-file .env.production

# Skip .env loading
toolregistry-server openapi --config config.json --no-env
```

## JSONC Support

Configuration files support JSON with Comments (JSONC), allowing inline documentation:

```jsonc
{
  // Operating mode: "denylist" or "allowlist"
  "mode": "denylist",

  // Namespaces to exclude
  "disabled": [
    "filesystem"  // Disable filesystem tools in production
  ],

  "tools": [
    {
      "module": "toolregistry_hub.calculator",
      "class": "Calculator",
      "namespace": "calculator",
      "enabled": true  // Always available
    }
  ]
}
```
