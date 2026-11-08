"""Bounded execution of schema-supplied regular expressions.

Python's :mod:`re` has no timeout, so a pattern that comes from an
untrusted OpenAPI document can backtrack catastrophically and stall the
process.  The server therefore never executes a spec-supplied pattern
through ``re``.  When the optional ``regex`` package is installed, the
OpenAPI adapters instead validate with a matcher that bounds every match
and the whole request; without it, spec patterns are ignored (see
``_without_spec_regex`` in the adapter).

The hook is supplied to ``jsonschema.iter_errors`` / ``schema_validate`` as
``pattern_matcher(pattern, text)``, with ``re.search`` semantics.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

try:
    import regex as _regex
except ImportError:  # optional dependency: toolregistry-server[regex]
    _regex = None  # type: ignore[assignment]

#: Whether the optional ``regex`` package is importable.
HAS_REGEX = _regex is not None

#: Per-match ceiling, in seconds.  A legitimate OpenAPI pattern finishes in
#: microseconds; 50 ms leaves two orders of magnitude of headroom.
DEFAULT_REGEX_TIMEOUT = 0.05

#: Per-request ceiling, in seconds.  ``patternProperties`` calls the matcher
#: once per (pattern, key), so the per-match ceiling alone cannot bound a
#: large object.
DEFAULT_REGEX_BUDGET = 0.25

#: A ``(pattern, text) -> bool`` callable with ``re.search`` semantics.
PatternMatcher = Callable[[str, str], bool]

#: Clock used to enforce the budget.  Tests replace it to make budget
#: exhaustion deterministic instead of timing-dependent.
_clock = time.monotonic


class SpecPatternError(ValueError):
    """A schema-supplied regular expression is invalid.

    Raised at load time for a malformed pattern, and at request time when
    the matcher cannot compile a pattern.  The message may name the pattern:
    it is only ever logged, never put in an HTTP response.
    """


class SpecRegexTimeout(TimeoutError):
    """A schema-supplied regular expression exceeded its time bound.

    Subclasses :class:`TimeoutError`, so callers can catch either type.  The
    message names the bounds, never the pattern.
    """

    def __init__(self, *, per_match: float, budget: float) -> None:
        self.per_match = per_match
        self.budget = budget
        super().__init__(
            f"spec regex exceeded {per_match * 1000:g} ms per match "
            f"or the {budget * 1000:g} ms request budget"
        )


def validate_bounds(per_match: float, budget: float) -> None:
    """Reject a negative or non-finite matcher bound.

    Args:
        per_match: Per-match ceiling, in seconds.  ``0`` disables the hook.
        budget: Per-request ceiling, in seconds.  ``0`` disables the hook.

    Raises:
        ValueError: If either bound is negative or not finite.
    """
    for name, value in (("regex_timeout", per_match), ("regex_budget", budget)):
        if not isinstance(value, (int, float)) or value != value:
            raise ValueError(f"{name} must be a number, got {value!r}")
        if value < 0:
            raise ValueError(f"{name} must not be negative, got {value}")
        if value == float("inf"):
            raise ValueError(f"{name} must be finite, got {value}")


def make_pattern_matcher(
    *,
    per_match: float,
    budget: float,
    clock: Callable[[], float] | None = None,
) -> PatternMatcher:
    """Build a bounded ``pattern_matcher`` for one request.

    Args:
        per_match: Ceiling for a single ``regex.search`` call, in seconds.
        budget: Ceiling for every call made through the returned matcher.
        clock: Monotonic clock; defaults to :func:`time.monotonic`.

    Returns:
        A ``(pattern, text) -> bool`` callable.  It raises
        :class:`SpecRegexTimeout` once the budget is spent and
        :class:`SpecPatternError` when a pattern does not compile.

    Raises:
        ValueError: If either bound is negative or not finite.
        RuntimeError: If the ``regex`` extra is not installed.
    """
    validate_bounds(per_match, budget)
    module = _regex
    if module is None:
        raise RuntimeError(
            "make_pattern_matcher requires the optional regex extra: "
            "pip install toolregistry-server[regex]"
        )
    now = clock or _clock
    deadline = now() + budget

    def matcher(pattern: str, text: str) -> bool:
        remaining = deadline - now()
        if remaining <= 0:
            raise SpecRegexTimeout(per_match=per_match, budget=budget)
        try:
            return (
                module.search(pattern, text, timeout=min(per_match, remaining))
                is not None
            )
        except module.error as exc:
            # ``str(exc)`` carries the pattern and the offset.  Keep the
            # class name only: this message is logged and must not be
            # mistaken for client-facing text.
            raise SpecPatternError(
                f"invalid regex ({type(exc).__name__}) for pattern {pattern!r}"
            ) from None

    return matcher


def log_status(logger: Any, per_match: float, budget: float, enabled: bool) -> None:
    """Log whether bounded spec-regex validation is active.

    Emitted once per app factory so an operator who installed the ``regex``
    extra can see that request validation now executes spec patterns.

    Args:
        logger: A structlog-style logger.
        per_match: Per-match ceiling, in seconds.
        budget: Per-request ceiling, in seconds.
        enabled: Whether a matcher factory was built.
    """
    if enabled:
        logger.info(
            f"bounded spec-regex validation enabled "
            f"(per-match {per_match * 1000:g} ms, budget {budget * 1000:g} ms)"
        )
    else:
        logger.debug(
            "spec-supplied regexes are not executed "
            "(regex extra missing or validation disabled)"
        )


def build_matcher_factory(
    per_match: float, budget: float
) -> Callable[[], PatternMatcher] | None:
    """Return a per-request matcher factory, or ``None`` when disabled.

    Args:
        per_match: Per-match ceiling, in seconds.
        budget: Per-request ceiling, in seconds.

    Returns:
        A zero-argument callable returning a fresh matcher, or ``None``.

    Raises:
        ValueError: If either bound is negative or not finite.
    """
    validate_bounds(per_match, budget)
    if not HAS_REGEX or per_match <= 0 or budget <= 0:
        return None

    def factory() -> PatternMatcher:
        return make_pattern_matcher(per_match=per_match, budget=budget)

    return factory


#: Schema keywords whose values are data, not subschemas.  A ``pattern`` key
#: inside them is an example value, not a constraint, so the walker must not
#: treat it as one.
_DATA_KEYWORDS = frozenset({"example", "examples", "default", "enum", "const"})

#: Keywords whose value maps arbitrary names to subschemas.  Their keys are
#: property or definition names, so a name such as ``default`` or ``example``
#: is not a data keyword and its value must still be walked.  ``dependencies``
#: also accepts an array of property names per key; the list branch handles
#: that form without looking at the strings.  Keep this set aligned with the
#: core admin walker (toolregistry #433).
_SCHEMA_MAP_KEYWORDS = frozenset(
    {
        "properties",
        "patternProperties",
        "dependentSchemas",
        "dependencies",
        "$defs",
        "definitions",
    }
)


def _record_pattern(
    module: Any,
    pattern: Any,
    path: str,
    seen: set[str],
    failures: list[tuple[str, str, str]],
) -> None:
    """Compile one pattern and record a failure, at most once per pattern."""
    if not isinstance(pattern, str) or pattern in seen:
        return
    seen.add(pattern)
    try:
        module.compile(pattern)
    except module.error as exc:
        failures.append((path, pattern, type(exc).__name__))


def _join(path: str, key: str) -> str:
    """Join a schema path and a keyword or name."""
    return f"{path}.{key}" if path else key


def _walk_patterns(
    module: Any,
    node: Any,
    path: str,
    seen: set[str],
    failures: list[tuple[str, str, str]],
) -> None:
    """Collect every pattern under *node* that ``regex`` cannot compile."""
    if not isinstance(node, dict):
        if isinstance(node, list):
            for index, value in enumerate(node):
                _walk_patterns(module, value, f"{path}[{index}]", seen, failures)
        return

    _record_pattern(module, node.get("pattern"), _join(path, "pattern"), seen, failures)

    pattern_properties = node.get("patternProperties")
    if isinstance(pattern_properties, dict):
        for key, sub in pattern_properties.items():
            _record_pattern(
                module,
                key,
                f"{_join(path, 'patternProperties')}[{key!r}]",
                seen,
                failures,
            )
            _walk_patterns(
                module,
                sub,
                _join(_join(path, "patternProperties"), str(key)),
                seen,
                failures,
            )

    for key, value in node.items():
        if key in ("pattern", "patternProperties") or key in _DATA_KEYWORDS:
            continue
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            # Keys here are user-defined names, not schema keywords: walk
            # every value even when its name is ``default`` or ``example``.
            for name, sub in value.items():
                _walk_patterns(
                    module, sub, _join(_join(path, str(key)), str(name)), seen, failures
                )
            continue
        _walk_patterns(module, value, _join(path, str(key)), seen, failures)


def find_invalid_patterns(schema: Any) -> list[tuple[str, str, str]]:
    """Return ``(schema_path, pattern, error_class)`` for unparsable patterns.

    Walks the schema the same way validation does: ``pattern`` and the keys
    of ``patternProperties`` are regular expressions; data-carrying keywords
    (``example``, ``default``, …) are not descended into.

    Args:
        schema: A JSON Schema fragment.

    Returns:
        One entry per distinct invalid pattern; empty when all compile.
    """
    failures: list[tuple[str, str, str]] = []
    module = _regex
    if module is None:
        return failures
    _walk_patterns(module, schema, "", set(), failures)
    return failures
