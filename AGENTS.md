# AGENTS.md — toolregistry-server

> Context file for AI coding assistants. Symlinked as `CLAUDE.md`.

## What this project is

toolregistry-server is a **protocol adapter layer** that exposes tools from
`toolregistry` via OpenAPI (REST) and MCP (Model Context Protocol) interfaces.
It sits between the core library and end-user tool collections.

| Package | Role | Depends on |
|---------|------|------------|
| `toolregistry` | Core library: `Tool` model, `ToolRegistry`, client integrations | — |
| `toolregistry-server` (this) | Server infrastructure: `RouteTable`, OpenAPI/MCP adapters, auth, CLI | `toolregistry` |
| `toolregistry-hub` | Tool implementations + default server configuration | `toolregistry-server` |

Upstream compat is checked weekly via `upstream-compat.yml`; downstream
(`toolregistry-hub`) is notified on release via `notify-downstream.yml`.

## Architecture

Central bridge: `RouteTable` converts a `ToolRegistry` into protocol-specific
endpoints. Both adapters read live state from the route table (no drift).

| Module | Purpose |
|--------|---------|
| `route_table.py` | Central routing bridge (observer pattern, ETag versioning) |
| `session.py` | Per-request/session state, `SessionContext`, `SessionManager` |
| `openapi/adapter.py` | JSON Schema → Pydantic conversion, FastAPI router generation |
| `openapi/middleware.py` | ETag-based HTTP caching (304 Not Modified) |
| `mcp/adapter.py` | MCP handler registration, session injection |
| `mcp/server.py` | Transport runners (stdio, SSE, streamable-http) |
| `auth/` | Bearer token authentication (FastAPI dependency factory) |
| `cli/` | CLI entry point with `openapi` / `mcp` subcommands |
| `_dotenv.py`, `_structlog.py` | Vendored zero-dep modules (from zerodep) |

## Repository layout

```
src/toolregistry_server/
├── __init__.py              # Exports: RouteTable, RouteEntry, SessionContext
├── route_table.py           # Central routing layer
├── session.py               # Session context + manager
├── _dotenv.py               # Vendored dotenv parser
├── _structlog.py            # Vendored structured logging
├── auth/                    # Bearer token auth
├── openapi/                 # FastAPI adapter + ETag middleware
├── mcp/                     # MCP adapter + transport runners
└── cli/                     # CLI (openapi.py, mcp.py subcommands)

tests/                       # 7 test modules, pytest
examples/                    # Usage examples
docs_en/, docs_zh/           # Documentation (git worktrees, orphan branches)
```

## Setup and commands

```bash
conda activate toolregistry_server   # or your env name
pip install -e ".[dev]"
```

Run `make help` for all targets:

```bash
make lint          # ruff check + ruff format --check
make lint-fix      # ruff check --fix + ruff format
make test          # pytest
make build         # python -m build
make push          # twine upload
```

No pre-commit hooks — quality is enforced by CI workflows and `make lint`.

## Definition of done

1. `make lint` passes (ruff check + format)
2. `make test` passes across Python 3.10–3.13
3. New code has tests in `tests/`
4. Google-style docstrings on public APIs; comments in English
5. Vendored `_dotenv.py` and `_structlog.py` are never edited directly

## Workflow

- **Branch from master**, open a PR, require CI green before merge.
- **Merge strategy: rebase** — keep commits atomic and well-messaged.
- **Conventional commits**: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`
- Never force-push to `master` unless the user explicitly requests it.

## Documentation

User-facing docs live on **orphan branches** (`docs_en`, `docs_zh`), mounted
as git worktrees. MkDocs + mkdocs-material, deployed to Zensical.

## Escalation

- Upstream incompatibility → check `upstream-compat.yml` CI, verify `toolregistry` version
- Vendored files (`_dotenv.py`, `_structlog.py`) → never fix in-place; update in zerodep repo
- Test failure after 3 attempts → stop, report full output
- Never: delete files to fix errors, skip tests, modify vendored files directly

## Files to never edit

- `src/toolregistry_server/_dotenv.py` — vendored from zerodep
- `src/toolregistry_server/_structlog.py` — vendored from zerodep
- `docs_en/`, `docs_zh/` — separate git branches, edit inside the worktree only
