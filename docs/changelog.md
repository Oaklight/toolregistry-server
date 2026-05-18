# Changelog

All notable changes to this project will be documented in this file.

## [0.2.2] - 2026-05-18

### Changed

- **`run_openapi_server()` and `run_mcp_server()` accept a pre-built registry**: both functions now accept an optional `registry: ToolRegistry | None` parameter. When provided, `config_path` is ignored and `create_registry_from_config` is skipped entirely, letting downstream packages inject a custom registry without reimplementing the startup sequence ([#28](https://github.com/Oaklight/toolregistry-server/issues/28)).

## [0.2.1] - 2026-05-10

### Changed

- Reorganized vendored zero-dependency modules into `_vendor/` sub-package for cleaner separation.
- Replaced `loguru` with a vendored lightweight structlog shim, removing the external logging dependency.
- Switched `complexipy` to a local pre-commit hook.

### Fixed

- Excluded `_vendor/` from `ruff` lint checks to avoid false positives on vendored code.

## [0.2.0] - 2026-05-06

### Added

- **Unified config system**: `run_openapi_server()` and `run_mcp_server()` now load tools from a JSONC/YAML configuration file via `--config`, supporting Python class/module, MCP server, and OpenAPI endpoint sources with denylist/allowlist filtering ([#24](https://github.com/Oaklight/toolregistry-server/issues/24)).

### Changed

- Adapted to `toolregistry >= 0.9.1` API changes (deprecated `toolregistry.openapi` import migrated to `toolregistry.integrations.openapi`).

## [0.1.3] - 2026-04-15

### Added

- **MCP session context passthrough**: tool handlers can now receive the MCP session context object, enabling session-aware tool implementations.

### Fixed

- Strip framework-injected fields (e.g. `__mcp_session__`) from OpenAPI adapter input before calling the handler, preventing unexpected keyword argument errors.
- Replace `python-dotenv` with a vendored zero-dependency dotenv loader, removing the external dependency.
- `toolregistry >= 0.6.1` now required to pick up the kwargs JSON Schema fix.

## [0.1.2] - 2026-03-22

### Added

- **Parameter validation in MCP adapter**: `RouteEntry` now carries `parameters_model` from `Tool`, enabling Pydantic-based type coercion (e.g. string `"8"` → `int(8)`) before handler execution. This fixes compatibility with MCP clients that serialize all arguments as strings (e.g. Codex).

### Changed

- MCP `call_tool` handler now uses `validate_input=False` to bypass the MCP SDK's strict JSON Schema validation, delegating type validation to Pydantic's more lenient coercion instead.

## [0.1.1] - 2026-03-18

### Fixed

- **RouteTable sync with ToolRegistry**: RouteTable now properly syncs with ToolRegistry on external state changes (e.g. tools added/removed/toggled outside the adapter layer).
- **Namespace-level enable/disable**: Fixed handling of namespace-level enable/disable operations in RouteTable sync, ensuring bulk toggle propagates correctly to all tools in a namespace.

### Changed

- Require `toolregistry >= 0.6.0` for `on_change` callback support used by RouteTable sync.

## [0.1.0] - 2026-03-14

Initial release of `toolregistry-server` as a standalone package, spun off from [toolregistry-hub](https://github.com/Oaklight/toolregistry-hub).

### Added

- **Central Route Table** (`RouteTable`, `RouteEntry`): Unified routing layer that bridges `ToolRegistry` and protocol adapters, with ETag versioning, observer pattern, and dynamic enable/disable support ([#2](https://github.com/Oaklight/toolregistry-server/issues/2), [PR #7](https://github.com/Oaklight/toolregistry-server/pull/7))
- **OpenAPI Adapter**: FastAPI-based REST API adapter with automatic Pydantic model generation from JSON Schema, dynamic OpenAPI schema, and tool grouping by namespace ([#3](https://github.com/Oaklight/toolregistry-server/issues/3), [PR #8](https://github.com/Oaklight/toolregistry-server/pull/8))
- **MCP Adapter**: Model Context Protocol adapter with `list_tools`/`call_tool` handlers and support for stdio, SSE, and Streamable HTTP transports ([#4](https://github.com/Oaklight/toolregistry-server/issues/4), [PR #9](https://github.com/Oaklight/toolregistry-server/pull/9))
- **ETag Caching**: HTTP caching middleware for `/tools` and `/openapi.json` endpoints with `If-None-Match` conditional request support ([#5](https://github.com/Oaklight/toolregistry-server/issues/5), [PR #10](https://github.com/Oaklight/toolregistry-server/pull/10))
- **CLI**: Command-line interface with `openapi` and `mcp` subcommands, JSON/JSONC configuration, customizable banner display ([#6](https://github.com/Oaklight/toolregistry-server/issues/6), [PR #11](https://github.com/Oaklight/toolregistry-server/pull/11), [PR #13](https://github.com/Oaklight/toolregistry-server/pull/13))
- **Authentication**: Bearer token authentication module with multi-token support, runtime token management, and dynamic enable/disable
- **.env file loading**: Support for loading environment variables from `.env` files with `--env-file` and `--no-env` CLI options ([PR #14](https://github.com/Oaklight/toolregistry-server/pull/14))

### Fixed

- MCP Streamable HTTP and SSE transport issues ([PR #12](https://github.com/Oaklight/toolregistry-server/pull/12))
- Disabled tools handling in banner display and runtime ([PR #13](https://github.com/Oaklight/toolregistry-server/pull/13))
- Denylist/allowlist mode support in configuration parsing ([PR #15](https://github.com/Oaklight/toolregistry-server/pull/15))
