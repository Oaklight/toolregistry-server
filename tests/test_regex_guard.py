"""Unit tests for :mod:`toolregistry_server.regex_guard`.

The optional ``regex`` package is replaced with a fake so the budget and the
error mapping are exercised deterministically on every CI job, including the
default matrix where the extra is not installed.
"""

import pytest

from toolregistry_server import regex_guard
from toolregistry_server.regex_guard import (
    SpecPatternError,
    SpecRegexTimeout,
    build_matcher_factory,
    find_invalid_patterns,
    make_pattern_matcher,
    validate_bounds,
)


class _FakeRegex:
    """Stand-in for the optional ``regex`` package."""

    class error(Exception):
        """Matches the shape of ``regex.error``."""

    def __init__(self, *, timeout: bool = False, invalid: bool = False) -> None:
        self.timeout = timeout
        self.invalid = invalid
        self.calls: list[tuple[str, str, float | None]] = []

    def search(self, pattern: str, text: str, timeout: float | None = None):
        self.calls.append((pattern, text, timeout))
        if self.timeout:
            raise TimeoutError("regex timed out")
        if self.invalid:
            raise self.error(f"bad pattern {pattern!r} at offset 0")
        return pattern if pattern else None

    def compile(self, pattern: str):
        if pattern in {"(", "["}:
            raise self.error(
                f"missing ), unterminated subpattern at offset 0 in {pattern!r}"
            )
        return pattern


class _Clock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def install_fake(monkeypatch):
    """Install a fake ``regex`` module and report it as available."""

    def install(**kwargs) -> _FakeRegex:
        module = _FakeRegex(**kwargs)
        monkeypatch.setattr(regex_guard, "_regex", module)
        monkeypatch.setattr(regex_guard, "HAS_REGEX", True)
        return module

    return install


def test_matcher_forwards_the_per_match_timeout(install_fake):
    module = install_fake()
    matcher = make_pattern_matcher(per_match=0.05, budget=0.25, clock=_Clock())

    assert matcher("^a", "abc") is True
    assert module.calls == [("^a", "abc", 0.05)]


def test_matcher_uses_the_remaining_budget(install_fake):
    module = install_fake()
    clock = _Clock()
    matcher = make_pattern_matcher(per_match=0.05, budget=0.25, clock=clock)

    clock.now = 0.24
    matcher("^a", "abc")
    assert module.calls[-1][2] == pytest.approx(0.01)


def test_budget_exhaustion_is_deterministic(install_fake):
    """The clock, not wall time, decides: no flaky CI failures."""
    module = install_fake()
    clock = _Clock()
    matcher = make_pattern_matcher(per_match=0.05, budget=0.25, clock=clock)

    matcher("^a", "abc")
    clock.now = 0.25
    with pytest.raises(SpecRegexTimeout):
        matcher("^a", "abc")
    # The exhausted call never reached the regex engine.
    assert len(module.calls) == 1


def test_match_timeout_propagates(install_fake):
    install_fake(timeout=True)
    matcher = make_pattern_matcher(per_match=0.05, budget=0.25, clock=_Clock())

    with pytest.raises(TimeoutError, match="regex timed out"):
        matcher("a", "a")


def test_invalid_pattern_hides_the_raw_regex_message(install_fake):
    install_fake(invalid=True)
    matcher = make_pattern_matcher(per_match=0.05, budget=0.25, clock=_Clock())

    with pytest.raises(SpecPatternError) as excinfo:
        matcher("(", "x")

    message = str(excinfo.value)
    # The pattern is kept for the log; the engine's own message is not.
    assert "'('" in message
    assert "bad pattern" not in message
    assert excinfo.value.__cause__ is None


@pytest.mark.parametrize(
    ("per_match", "budget"),
    [
        (-0.1, 0.25),
        (0.05, -0.1),
        (float("nan"), 0.25),
        (0.05, float("inf")),
    ],
    ids=["negative-timeout", "negative-budget", "nan", "inf"],
)
def test_bounds_reject_negative_and_non_finite(per_match, budget):
    with pytest.raises(ValueError):
        validate_bounds(per_match, budget)


def test_factory_disabled_without_the_extra(monkeypatch):
    monkeypatch.setattr(regex_guard, "HAS_REGEX", False)
    assert build_matcher_factory(0.05, 0.25) is None


@pytest.mark.parametrize(("per_match", "budget"), [(0, 0.25), (0.05, 0)])
def test_factory_disabled_by_zero(install_fake, per_match, budget):
    install_fake()
    assert build_matcher_factory(per_match, budget) is None


def test_factory_returns_a_fresh_matcher_per_request(install_fake):
    install_fake()
    factory = build_matcher_factory(0.05, 0.25)

    assert factory is not None
    assert factory() is not factory()


def test_valid_patterns_pass(install_fake):
    install_fake()
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "pattern": "^[a-z]+$"}},
        "patternProperties": {"^x-": {"type": "integer"}},
    }
    assert find_invalid_patterns(schema) == []


def test_invalid_patterns_cover_pattern_properties_keys(install_fake):
    install_fake()
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "pattern": "("}},
        "patternProperties": {"[": {"type": "string"}},
    }

    failures = find_invalid_patterns(schema)

    assert {path for path, _, _ in failures} == {
        "properties.name.pattern",
        "patternProperties['[']",
    }
    assert {pattern for _, pattern, _ in failures} == {"(", "["}


def test_example_data_is_not_a_schema(install_fake):
    """A ``pattern`` key inside example/default data is not a constraint."""
    install_fake()
    schema = {
        "type": "object",
        "example": {"pattern": "("},
        "default": {"pattern": "("},
        "properties": {"a": {"example": {"pattern": "("}}},
    }
    assert find_invalid_patterns(schema) == []


def test_walker_is_a_noop_without_the_extra(monkeypatch):
    monkeypatch.setattr(regex_guard, "_regex", None)
    assert find_invalid_patterns({"pattern": "("}) == []
