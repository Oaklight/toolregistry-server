# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with project-specific grouping where useful.

## [0.4.0] - 2026-06-22

### Added

- **`ServerIdentity` + `CLI` class**: downstream packages can now subclass `CLI` to build a fully-branded, customised command-line entry point without reimplementing argument parsing ([#39](https://github.com/Oaklight/toolregistry-server/pull/39)).
- **`configure_subparsers()` hook**: `CLI.create_parser()` calls `configure_subparsers(subparsers)` after building the `openapi`/`mcp` subparsers, giving subclasses a clean way to add arguments without touching argparse private API ([#47](https://github.com/Oaklight/toolregistry-server/pull/47)).
- **`run_cli()` reusable main loop**: a standalone `run_cli()` helper encapsulates the full parse-dispatch lifecycle so downstream can embed the CLI loop without subclassing.
- **CLI argument builders as public API**: `OpenAPIAdapter.add_cli_arguments()` and `MCPAdapter.add_cli_arguments()` are now part of the public surface, enabling downstream CLIs to reuse the standard argument sets.
- **Bearer token auth for Streamable HTTP transport**: the MCP adapter now enforces Bearer token authentication on the `streamable-http` transport, consistent with the existing SSE auth support.

### Changed

- **`App` class + `Adapter.create_and_run()`**: the `App` class is now the canonical entry point for programmatic use; `Adapter.create_and_run()` provides a single-call path for generic dispatch ([#42](https://github.com/Oaklight/toolregistry-server/pull/42)).
- **Lazy-init default `App`**: module-level `serve_openapi` / `serve_mcp` convenience functions now create the default `App` instance on first call instead of at import time, avoiding a redundant instance when the CLI path is used ([#47](https://github.com/Oaklight/toolregistry-server/pull/47)).
- **Refactored adapter and CLI internals**: banner logic extracted, CLI arguments moved into adapters, and `cli`/`auth` modules flattened for a cleaner package layout ([#42](https://github.com/Oaklight/toolregistry-server/pull/42)).

### Fixed

- Explicit list-to-set conversion for token collections, preventing silent de-duplication issues with ordered iterables.

## [0.3.3] - 2026-05-31

### Fixed

- Normalize route parameter schemas at the `RouteTable` boundary so all server adapters receive canonical object schemas.
- Ensure MCP `tools/list` responses always emit object-shaped `inputSchema` values.
- Harden OpenAPI request model generation for empty or non-object parameter schemas.
- Bump the minimum `toolregistry` version to `>=0.11.1` for schema-generation fixes in the core package.

## [0.3.2] - 2026-05-28

### Changed

- Minimum `toolregistry` version bumped to `>=0.11.0` so downstream packages can rely on the latest `ToolConfig` metadata fields.
- Switched the complexipy pre-commit hook to the official `complexipy-pre-commit` hook with explicit file filtering.

## [0.3.1] - 2026-05-19

### Added

- Route listing now filters deferred tools, matching the progressive-disclosure behavior used by served registries.
- Deployment profile filtering can now be overridden by configuration files.

### Changed

- Minimum `toolregistry` version bumped to `>=0.10.2`.

## [0.3.0] - 2026-05-18

### Added

- **`--profile` CLI flag** for deployment-context tag filtering. `--profile remote` disables tools tagged `file_system`, `destructive`, or `privileged` via `ToolRegistry.disable_by_tags()`; `--profile local` applies no filter. The `profile` parameter is also exposed on `run_openapi_server()` and `run_mcp_server()` for programmatic use ([#30](https://github.com/Oaklight/toolregistry-server/issues/30)).
- **Post-register hooks in `create_registry_from_config()`**: the function now accepts an optional `post_register_hooks: list[PostRegisterHook] | None` parameter. Hooks are registered on the `ToolRegistry` before any source is loaded; returning a non-empty string from a hook auto-disables the tool ([#31](https://github.com/Oaklight/toolregistry-server/issues/31)).
- **`apply_profile(registry, profile)`**: new public helper that applies a named profile filter to any `ToolRegistry`. Profile→tag mapping is expressed in `PROFILE_DISABLE_TAGS` (dict) for easy extension.

### Changed

- Minimum `toolregistry` version bumped to `>=0.10.1` to pick up `disable_by_tags()` and `add_post_register_hook()`.

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
