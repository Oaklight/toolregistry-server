# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with project-specific grouping where useful.

## [Unreleased]

### Added

- **A FastAPI-free OpenAPI document builder**: `toolregistry_server.adapters.openapi.spec.build_openapi_schema(route_table)` returns the OpenAPI 3.1 document straight from the `RouteTable`, emitting one `POST` per enabled route with the same `operationId`, `summary` (description truncated at 120 characters), namespace tag, inlined parameter schema, `x-*` execution-hint extensions and the `/tools` + `/events` meta operations as the FastAPI app publishes. Disabled routes are simply absent, so no post-filtering step is needed. This is the spec half of removing FastAPI from the adapter (epic #101); the app still serves `/openapi.json` through FastAPI until the port lands. A parity test compares the two documents path by path.
- **MCP tool annotations and title** (#280): `tools/list` now carries `title` from `Tool.metadata.title` and `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) from `Tool.execution_hints`. Hints that are not declared stay absent, so a tool that claims nothing keeps the minimal `{name, description, inputSchema}` definition. The Python field spelling differs between MCP SDK v1 (camelCase) and v2 (snake_case); both serialize to the spec's camelCase JSON keys.
- **OpenAPI operation extensions and route accessors** (#280): generated operations carry `x-read-only`, `x-destructive`, `x-idempotent`, `x-open-world` and `x-title` for the hints a tool declares, matching the MCP `annotations`; undeclared hints are omitted. `RouteEntry` gains `title` and `execution_hints` properties so both adapters read the same metadata instead of reaching into `route.tool`.
- **`--server-url` for the MCP subcommand** (#78): sets the public URL used in OAuth protected-resource metadata, which previously could only be passed as an `adapter.run(server_url=...)` kwarg — so CLI deployments behind a reverse proxy had no way to advertise a reachable origin and fell back to the bind address. The server now also logs a warning at startup when the advertised URL would be derived from a wildcard bind address (`0.0.0.0`, `::`).
- **`--tokens` for the MCP subcommand** (#99): the adapter already accepted `tokens_path` and the guide documented the flag, but only the `openapi` subcommand defined it, so `mcp --transport http --tokens tokens.txt` died in argparse. `API_BEARER_TOKEN` still works as before.
- **MCP `serverInfo` carries a version and usage instructions** (#79): `initialize` returned an empty `serverInfo.version`, because the adapter never passed one to the SDK. It now reports the installed `toolregistry-server` version, and embedders can pass their own — `App` and the CLI forward `ServerIdentity.version`, so the Hub advertises the Hub version instead of the server library's. When `discover_tools` is served, the response also carries `instructions` describing the two-step deferred-tool flow (`discover_tools`, then `call_deferred`), which a client could previously only infer from tool descriptions.
- **`subscriptions/listen` is served on MCP SDK v2** (#107): the 2026-07-28 wire has no standing notification channel, so the SDK drops a `notifications/tools/list_changed` sent on the connection and modern-era clients never learned that the tool list changed. The adapter now registers the SDK's listen handler over an in-process bus and publishes a tools-list-changed event on every route-table change, so a client that opens a listen stream (SEP-2575) receives it and can refetch `tools/list`. Legacy-era clients keep the existing session notification path; SDK builds without the listen hook are unaffected.

### Changed

- **`tools/list` is cached for five minutes by default** (#114, **behavior change**): with `subscriptions/listen` served (#110), a listening client's cache is invalidated on every change, so `ttlMs` now defaults to the spec's example (`300000`) instead of `0`. A client that does not listen keeps the list for up to five minutes; `--tools-list-ttl-ms 0` restores refetch-every-time. `cacheScope` remains `private`.
- **`tools/list` cache hints are configurable** (#83): `--tools-list-ttl-ms` and `--tools-list-cache-scope` set the `ttlMs` / `cacheScope` hints on `tools/list` (SDK v2), which previously could only be passed as adapter kwargs and never were. The default stays `ttlMs: 0` ("never cache") until `notifications/tools/list_changed` is verified on SDK v2 (#107) — caching without a working invalidation would leave clients showing tools that no longer exist. `cacheScope` defaults to `private`, since the served set depends on profile and authorization.
- **Unknown or disabled tools are protocol errors** (#81, **behavior change** for MCP clients): `tools/call` for a tool that does not exist, or that is currently disabled, now returns JSON-RPC error `-32602` (Invalid params) instead of a `CallToolResult` with `isError: true`. The request itself was invalid, so hosts should not hand it back to the model as a tool result. Tool execution failures keep returning `isError: true` so the model can read the message and adapt. Works on both MCP SDK v1 and v2.

### Fixed

- **Dataclass tool results are serialized structurally** (#187, #192): `_serialize_result` used `json.dumps(..., default=str)`, so a tool returning `list[SearchResult]` reached the model as a JSON array of Python `repr` strings — not machine-readable, and un-indexable by field. Dataclasses (top-level or nested), datetimes, enums, paths, byte strings and sets now encode to their JSON equivalents; `str` remains only the last resort.
- **A `tools/call` resolves its route once** (#97): the route was looked up twice — once in the SDK v1 pre-dispatch hook, once in the handler. A disable landing between the lookups made the two SDK generations answer the same request differently: v1 an `isError` result, v2 a `-32602` protocol error. The hook's result is now carried to the handler.
- **Tool-list change notifications track the connection, not the request** (#108): on MCP SDK v2 every request gets a fresh `ServerSession`, so the session `list_tools` registered was garbage-collected before any change event could use it, and the tracker was always empty. It now records the underlying connection, which lives as long as the client. Delivery to modern-era connections still needs a back-channel (#107), so this is a prerequisite rather than a complete fix.
- **Only connections that can receive a change event are tracked** (#111): since #108 the `tools/list` handler tracks `session._connection`, but on the 2026-07-28 path that object is created per request and has no change-notification channel, so every `tools/list` added one dead entry that was never released. Tracking now skips modern-era connections (single-exchange HTTP and modern duplex alike); those clients are served by `subscriptions/listen` (#110). Handshake-era clients are still tracked.
- **A route change outside an event loop no longer leaves a coroutine behind** (#117): the notification scheduler built its coroutine before checking for a running loop, so building a server or mutating a route table from synchronous code emitted `RuntimeWarning: coroutine ... was never awaited`. The coroutine is now created only when there is a loop to run it.
- **`initialize` no longer advertises a disabled discovery stack** (#112, #113): the default `instructions` were attached whenever `discover_tools` existed in the route table, including when a profile had disabled it, so a client was told to call a tool that answers `-32602`. The guidance now requires both `discover_tools` and `call_deferred` to be enabled.
- **Source-level `include`/`exclude` filters are forwarded** (#88): the config loader parsed them, but `register_mcp_source()` and `register_openapi_source()` never passed them on, so a filtered remote source registered every tool. They are now forwarded unchanged (an explicit empty tuple still means "match nothing"). Takes effect once the installed `toolregistry` provides the fields; on an older core the server behaves as before.
- **MCP extra floor raised to `mcp>=1.22.0`** (#95): SDK releases below 1.22 import a pydantic internal that pydantic 2.12+ removed (`eval_type_backport`), so a resolver landing anywhere in 1.17–1.21 produced an install that fails at `import mcp.server.lowlevel`. 1.21 fails and 1.22 imports, so the floor is the boundary. The `test-mcp-v1` CI job now installs the declared floor and prints the version it tested.
- **Startup banner no longer corrupts the stdio stream** (#80): `print_banner()` wrote to stdout, which over the MCP stdio transport is the JSON-RPC channel — clients read banner art between their messages, sometimes after the first responses. The banner now goes to stderr, alongside the logs, and `print_banner()` accepts an optional `file` argument. `--no-banner` still suppresses it. The Hub calls the same helper, so it is fixed without a Hub change — but only once its installed `toolregistry-server` crosses the version that carries this.

### Security

- **Disable reasons no longer disclose the deployment profile** (#84): `apply_profile()` stored `Disabled by profile 'remote'` as the tool's disable reason, and that string reaches callers through the MCP `tools/call` error and core's `RuntimeError`. The stored reason is now the neutral `"not available on this server"`; the profile name is only logged server-side.

## [0.6.0] - 2026-09-15

### Added

- **`call_deferred` integration**: The `call_deferred` proxy tool (from `toolregistry` core) is transparently exposed through both MCP and OpenAPI adapters when tool discovery is enabled. MCP clients can invoke deferred tools after discovering their schema via `discover_tools`.
- **`additionalProperties` support in OpenAPI adapter**: `_schema_to_pydantic` now generates Pydantic models with `extra="allow"` when the JSON Schema declares `additionalProperties: true`, enabling proxy tools like `call_deferred` that accept `**kwargs`.
- **Leading-underscore field handling**: `_schema_to_pydantic` maps `_`-prefixed field names via Pydantic aliases (since Pydantic forbids leading underscores). Alias collision detection raises `ValueError` for conflicting names in both directions.
- **`CompatProxy` for MCP SDK compat**: Transparent wrapper that resolves both snake_case and camelCase attribute names on MCP SDK objects. `create_test_client` auto-wraps results so tests work across MCP SDK v1 (camelCase) and v2 (snake_case).
- **MCP and OpenAPI source registration tests**: Test coverage for `register_mcp_source` and `register_openapi_source` in `registry_builder`.
- **Schema fingerprint and notifications**: `schema_hash` and `last_refreshed_at` exposed in `/tools` endpoint. MCP `tools/list_changed` notifications pushed to connected sessions on route table changes.
- **Test release workflow**: CI workflow for publishing dev versions to Test PyPI.

### Changed

- **Frozen dataclass compatibility** (**breaking** for downstream code mutating `Tool`/`ToolMetadata`): `_apply_ns_tags` uses `_replace_tool_metadata()` instead of direct attribute assignment. `RouteEntry` holds a direct reference to the frozen `Tool` instead of copying fields, with `parameters_schema` as a `cached_property`.
- **Two-layer schema model comments**: Updated `toolcall_reason` stripping comments in both MCP and OpenAPI adapters to reflect that `tool.parameters` is now a clean schema and `get_schema()` injects `toolcall_reason` on demand.
- OpenAPI endpoints use `model_dump(by_alias=True)` to preserve original JSON key names.
- Minimum `toolregistry` version bumped to `>=0.18.0`.

### Fixed

- SSE queue overflow now logs at debug level instead of silent suppression.
- MCP SDK snake_case/camelCase field access compat across all tests.

## [0.5.0] - 2026-08-25

### Added

- **Native multimodal content support** (#58, #59): The MCP adapter now converts content block lists to native MCP types instead of JSON-dumping them into text. Tools returning images, audio, resource links, or embedded resources are delivered as actual `ImageContent`, `AudioContent`, `ResourceLink`, and `EmbeddedResource`.
- **`outputSchema` / `structuredContent`** (#61, #65): Tools can declare an output schema via `metadata.extra['output_schema']`. The schema is advertised in `tools/list` as `outputSchema`, and tool results are returned as `structuredContent` alongside the text content block — enabling client-side validation per MCP spec 2026-07-28.
- **Cache hints for `tools/list`** (#62, #64): `route_table_to_mcp_server()` and `MCPAdapter` accept `list_tools_ttl_ms` and `list_tools_cache_scope` parameters. Clients can cache tool catalogs to reduce re-fetching and improve LLM prompt cache hit rates.
- **Audio, resource_link, and embedded resource content types** (#59): Full MCP 2026-07-28 content type coverage — all five types (`text`, `image`, `audio`, `resource_link`, `resource`) are natively supported. Unknown future types degrade gracefully to JSON `TextContent` with a warning.

### Changed

- `CallToolHandler` return type uses `mcp.types.ContentBlock` union instead of `list[Any]`.
- MIME type extraction unified via `_get_mime_type()` helper across all content block types.
- Minimum `toolregistry` version bumped to `>=0.16.0`.

### Fixed

- Skip `structuredContent` for multimodal content block results to avoid spec violations.
- Degrade unknown content block types to `TextContent` instead of silently dropping them.
- Align return annotations with `MCPContentBlock` type alias.

## [0.4.3] - 2026-08-06

### Added

- **MCP SDK v2 compatibility** (#56): Server-side adapter supports both MCP SDK v1 (1.x) and v2 (2.x). Dependency pin widened to `mcp>=1.17,<3`. Adds a `_compat.py` layer that handles API differences transparently.

### Changed

- Minimum `toolregistry` version bumped to `>=0.15.0`.

## [0.4.2] - 2026-07-16

### Fixed

- **Pass `headers` from MCPSource config to `register_from_mcp`**: `MCPSource.headers` was parsed by the config loader but never forwarded to `register_from_mcp()`, making config-based header auth for MCP servers ineffective.
- **Await coroutine from `_FunctionToolWrapper`**: fix session injection crash when a tool wrapper returns a coroutine instead of a plain value in OpenAPI/MCP endpoints.

### Changed

- Minimum `toolregistry` version bumped to `>=0.14.0`.

## [0.4.1] - 2026-06-26

### Fixed

- Catch tool exceptions in OpenAPI endpoints, return structured HTTP 500 instead of unhandled error (#49).
- Install full deps in CI lint and remove stale `ty: ignore` comments.

### Changed

- Pin dev tool versions: ruff==0.15.20, ty==0.0.54, complexipy==5.6.1.

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
