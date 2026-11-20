# Contributing to toolregistry-server

## Getting Started

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-change`)
3. Install dev dependencies: `pip install -e ".[dev]"`
4. Set up pre-commit hooks: `pre-commit install`
5. Make your changes
6. Run `make lint` (or let pre-commit catch issues on commit)
7. Run `make test` to verify nothing is broken
8. Commit and push
9. Open a Pull Request

## Branch Naming

Use descriptive prefixes:

- `feature/xxx` — new functionality
- `fix/xxx` — bug fix
- `refactor/xxx` — code restructuring
- `docs/xxx` — documentation updates
- `test/xxx` — test additions or changes

## Commit Messages

Keep commit messages concise and focused on *why*, not *what*. One logical change per commit. This project uses conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`.

## Pull Requests

- Keep PRs focused — one feature or fix per PR
- Include a brief description of what changed and why
- Mention any breaking changes explicitly
- Ensure `make lint` passes before submitting
- Merge strategy: rebase

Every PR uses `.github/pull_request_template.md`. The rules below decide how
much review a PR needs and in what order PRs merge.

### Review tiers

Tick one risk tier in the template. When a PR spans tiers, it takes the
highest.

| Tier | Typical change | Review |
| --- | --- | --- |
| Pure move | Code moves with no logic edit | One reviewer checks only that the mechanical proof is credible. CI green plus the proof can merge. |
| Boundary or contract change | Imports, public API, config schema, wire format | One reviewer plus the maintainer. |
| Runtime-behaviour change | Logic that runs differently after the PR | One or two reviewers who read the logic. |
| Security-relevant | Auth, keys, credentials, sanitizing, network exposure | Always reviewed, ideally by someone other than the author's usual reviewer. |

For a pure move, state the mechanical proof in the template: the summary of
`python scripts/dev/check_move.py origin/master --markdown` (when
`scripts/dev/check_move.py` exists) and a passing `ty` hook from
`pre-commit run --all-files`.

### Who reviews what

Reviewer assignment follows the path, not the author's choice. The repository
has no `CODEOWNERS` file, so ask the maintainer (`@Oaklight`) to assign the
reviewer for the area. The areas follow
the repository's `area:*` labels (`.github/labels.yml`). Every path below
exists in the tree (checked with `git ls-files`).

| Paths under `src/toolregistry_server/` | Area |
| --- | --- |
| `adapters/` | `area:adapters`: protocol adapters (MCP, OpenAPI) |
| `route_table.py`, `registry_builder.py`, `session.py` | `area:routing`: route table, registry builder, session management |
| `auth.py`, `identity.py`, `isolation.py`, `keystore.py`, `keys_cli.py`, `oauth/` | `area:auth`: authentication, identity, token verification. Security-relevant tier. |
| `usage.py` | `area:auth`: per-key call counters that `keystore.py` persists. Reviewed with `keystore.py`. |
| `cli.py`, `_config_file.py`, `app.py`, `banner.py` | `area:cli`: CLI, configuration, app bootstrap |
| `source_manager.py` | `area:cli`: runtime tool-source list and config write-back. It changes the served tool set, so it takes the runtime-behaviour tier at least. |
| `adapters/mcp/token_verifier.py`, `_vendor/jwt.py`, `regex_guard.py`, `scoped_discovery.py` | Security-relevant tier as well, whichever `area:*` label the PR carries. They decide who may call (MCP bearer verification, the JWT implementation behind it), what a tool may be called with (scope checks of `discover_tools` and `call_deferred`), and whether an untrusted OpenAPI pattern can stall the process (bounded regex execution). |
| `_vendor/` | Vendored through zerodep (`_vendor/zerodep.lock.json`). Never edit in place: fix it upstream and re-vendor. |
| `gateway/`, `ext/gateway/` | No `area:*` label exists for them. The maintainer reviews every PR that touches them. `gateway/admin.py`, `gateway/unix.py`, `gateway/endpoint.py` and the admin panel in `ext/gateway/` decide who can reach the gateway and its keys; ask the maintainer whether the change is security-relevant. |
| `.github/`, `scripts/`, `Makefile`, `pyproject.toml`, `.pre-commit-config.yaml` | Build, CI, tooling |

Any other path: ask the maintainer.

### Series and overlap

Before you open a PR, run the overlap check:

```bash
python scripts/dev/pr_overlap.py            # current branch against origin/master
python scripts/dev/pr_overlap.py --local    # also other local branches
python scripts/dev/pr_overlap.py --fail-on-overlap   # gate: 1 overlap, 2 unverified
```

It lists every open PR that touches the same files, and leaves out the PR of
the branch itself (matched by branch name, by head commit, or by
`$GITHUB_HEAD_REF`), so `--head HEAD` or a SHA works in a detached checkout. It
needs the `gh` CLI and exits 0 unless you pass `--fail-on-overlap`. That gate
exits 1 on overlap and 2 when the check could not complete (`gh` missing or
failing, bad output, or 200 open PRs so the list may be truncated); a gate does
not pass on a check that did not finish. The base is `origin/master`; pass
`--base` for another one.

If a PR overlaps, merge a series one PR at a time and rebase the rest after
each merge.

### Small PRs

- One refactor or one concern per PR.
- Follow-on mechanical edits (formatting, doc touch-ups) go after the PR they
  depend on. Do not open them as independent PRs.

### Requesting review

Request review only after CI is fully green. Do not skip a hook with `SKIP=...`
to get there: the hooks pass on `master`, so a failure is a real diagnostic or
a local environment that does not match CI. If you skipped any hook, name it
and the reason in the template.

## AI-Assisted Contributions

Using AI tools (e.g. Claude, Cursor, Copilot) to assist with development is welcome. However:

- **No AI co-author tags in commits.** Do not add `Co-authored-by` lines for AI tools in git commit messages. This keeps the git history clean and readable.
- **Disclose in PR description.** If AI tools were used significantly in your contribution, add a brief note in the PR description (e.g. "AI was used to assist with implementation").
- **You own the code.** Contributors are fully responsible for any AI-generated code they submit — review it, test it, understand it.

## Code Style

- Python code follows `ruff` defaults
- Docstrings use Google style
- Comments and docstrings in English
- Type hints are encouraged

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
