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

## Checking that a PR is a pure move

A PR that only moves code (splitting a module, relocating methods) should
change no definition. A reviewer cannot see a dropped `@classmethod` in a large
move, so compare the two sides by AST:

```bash
python scripts/dev/check_move.py origin/master          # head defaults to HEAD
python scripts/dev/check_move.py origin/master --markdown   # paste into the PR body
```

The tool compares every function, method, class and class-level assignment of
the changed files by qualified name (and by bare name when a method changes
class), including decorators, arguments, annotations, body and docstring.
Comments and formatting are ignored. Files under `_vendor/` are skipped. It
exits nonzero on a def that is `missing` or `moved-with-diff`. It also fails on
a changed census of `@staticmethod`, `@classmethod` and `@property` decorators
over the changed files (only defs matched between base and head count, so a
split that adds a decorated helper passes, while a decorator dropped from a
moved def fails), and on `undefined-global`: a moved function that reads a
module-level name (a lost `logger` or constant) its new module does not define.
Added `if TYPE_CHECKING:` stubs, a module-level import of a name that used to be
imported inside the function, and `getLogger(__name__)` becoming
`getLogger("<old module name>")` are reported but do not fail.

Record an intended difference with a reason in a file of
`path::qualified.name  reason` lines and pass it with `--allow FILE`. The path
is the repo-relative file of the def; for a def that moved between files, the
head or the base path both work. Use `undefined-global:<head path>::<name>` and
`decorator-census:<decorator>` for those two checks, with the decorator name as
the report prints it in `(allow entry: ...)`, for example
`decorator-census:property` (`decorator-census:@property` is accepted too); the
census counts across all files, so that entry is not tied to a path. A bare
`qualified.name` is rejected, because it would excuse the name in every file.

CI runs the same check, as the `Pure move` workflow, on pull requests that
carry the `pure-move` label, including when the label is added or removed after
the PR was opened. The workflow reads `.github/pure-move-allow.txt` from the
**base branch**, so a PR cannot exempt itself: land the allow-file entry first,
in its own reviewed change. Apply the label only to a PR you claim is a pure
move. A `refactor:` PR that changes behaviour on purpose does not get the
label.

The label is defined in `.github/labels.yml`, but that file does not create it.
A maintainer creates it once:

```bash
gh label create pure-move --color 57606A \
  --description "PR only moves code; runs the pure-move CI check"
```

The job runs only when the label is present. GitHub counts a job that its `if:`
skips as a success. If `Pure move` is ever added to the required checks, a PR
that fails the check can still merge once someone removes the label. To make the
claim binding, run the job on every PR and have it `exit 0` explicitly when the
label is absent.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
