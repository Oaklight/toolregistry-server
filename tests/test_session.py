"""Unit tests for the session module.

Tests SessionContext, should_inject_session, session_context_var,
and SessionManager independently of any transport adapter.
"""

from __future__ import annotations

import gc
from unittest.mock import MagicMock

import pytest

from toolregistry_server.session import (
    SessionContext,
    SessionManager,
    session_context_var,
    should_inject_session,
)

# ---------------------------------------------------------------------------
# SessionContext
# ---------------------------------------------------------------------------


class TestSessionContext:
    """Tests for SessionContext dataclass."""

    def test_create_with_required_fields(self) -> None:
        """SessionContext should be created with session_id and transport."""
        ctx = SessionContext(session_id="abc", transport="stdio")
        assert ctx.session_id == "abc"
        assert ctx.transport == "stdio"
        assert ctx.created_at > 0

    def test_get_set(self) -> None:
        """get/set should store and retrieve values."""
        ctx = SessionContext(session_id="s1", transport="sse")
        assert ctx.get("key") is None
        assert ctx.get("key", 42) == 42

        ctx.set("key", "hello")
        assert ctx.get("key") == "hello"

    def test_dict_interface(self) -> None:
        """__getitem__, __setitem__, __contains__ should work."""
        ctx = SessionContext(session_id="s2", transport="stdio")
        ctx["count"] = 10
        assert ctx["count"] == 10
        assert "count" in ctx
        assert "missing" not in ctx

    def test_get_item_missing_key_raises(self) -> None:
        """__getitem__ should raise KeyError for missing keys."""
        ctx = SessionContext(session_id="s3", transport="stdio")
        with pytest.raises(KeyError):
            _ = ctx["nope"]

    def test_separate_contexts_have_separate_data(self) -> None:
        """Two SessionContext instances should not share data."""
        ctx_a = SessionContext(session_id="a", transport="stdio")
        ctx_b = SessionContext(session_id="b", transport="stdio")
        ctx_a.set("x", 1)
        assert ctx_b.get("x") is None


# ---------------------------------------------------------------------------
# should_inject_session
# ---------------------------------------------------------------------------


class TestShouldInjectSession:
    """Tests for should_inject_session detection."""

    def test_no_session_param(self) -> None:
        """Function without _session should return False."""

        def f(x: int) -> int:
            return x

        assert should_inject_session(f) is False

    def test_session_param_typed(self) -> None:
        """Function with _session: SessionContext should return True."""

        def f(x: int, _session: SessionContext) -> int:
            return x

        assert should_inject_session(f) is True

    def test_session_param_untyped(self) -> None:
        """Function with _session (no annotation) should return True."""

        def f(x: int, _session) -> int:  # type: ignore[no-untyped-def]
            return x

        assert should_inject_session(f) is True

    def test_session_param_string_annotation(self) -> None:
        """Function with _session: 'SessionContext' (string) should return True."""

        def f(x: int, _session: SessionContext) -> int:
            return x

        assert should_inject_session(f) is True

    def test_wrong_type_annotation(self) -> None:
        """Function with _session: int should return False."""

        def f(x: int, _session: int) -> int:
            return x

        assert should_inject_session(f) is False

    def test_non_callable(self) -> None:
        """Non-callable should return False without raising."""
        assert should_inject_session(42) is False  # type: ignore[arg-type]

    def test_lambda(self) -> None:
        """Lambda without _session should return False."""
        assert should_inject_session(lambda x: x) is False


# ---------------------------------------------------------------------------
# session_context_var
# ---------------------------------------------------------------------------


class TestSessionContextVar:
    """Tests for session_context_var contextvar."""

    def test_default_is_unset(self) -> None:
        """Accessing without set should raise LookupError."""
        # Reset to ensure clean state
        token = None
        try:
            token = session_context_var.set(
                SessionContext(session_id="tmp", transport="stdio")
            )
            session_context_var.reset(token)
            token = None
        except Exception:
            pass

        with pytest.raises(LookupError):
            session_context_var.get()

    def test_set_and_reset(self) -> None:
        """set/reset should manage the contextvar lifecycle."""
        ctx = SessionContext(session_id="cv1", transport="sse")
        token = session_context_var.set(ctx)
        assert session_context_var.get() is ctx
        session_context_var.reset(token)


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class TestSessionManager:
    """Tests for SessionManager lifecycle and caching."""

    def test_get_or_create_new(self) -> None:
        """First call should create a new session via factory."""
        mgr = SessionManager()
        ctx = mgr.get_or_create(
            100, lambda: SessionContext(session_id="s1", transport="stdio")
        )
        assert ctx.session_id == "s1"

    def test_get_or_create_existing(self) -> None:
        """Same key should return the same session (no double-create)."""
        mgr = SessionManager()
        factory_calls = 0

        def factory() -> SessionContext:
            nonlocal factory_calls
            factory_calls += 1
            return SessionContext(session_id="s2", transport="sse")

        ctx_a = mgr.get_or_create(200, factory)
        ctx_b = mgr.get_or_create(200, factory)
        assert ctx_a is ctx_b
        assert factory_calls == 1

    def test_different_keys_different_sessions(self) -> None:
        """Different keys should produce different sessions."""
        mgr = SessionManager()
        ctx_a = mgr.get_or_create(
            1, lambda: SessionContext(session_id="a", transport="stdio")
        )
        ctx_b = mgr.get_or_create(
            2, lambda: SessionContext(session_id="b", transport="stdio")
        )
        assert ctx_a is not ctx_b
        assert ctx_a.session_id != ctx_b.session_id

    def test_remove_session(self) -> None:
        """remove_session should clean up session data."""
        mgr = SessionManager()
        mgr.get_or_create(
            300, lambda: SessionContext(session_id="s3", transport="stdio")
        )
        assert mgr.active_session_count == 1

        mgr.remove_session(300)
        assert mgr.active_session_count == 0

    def test_remove_nonexistent_session(self) -> None:
        """Removing a non-existent session should not raise."""
        mgr = SessionManager()
        mgr.remove_session(999)  # no error

    def test_new_session_id_unique(self) -> None:
        """new_session_id should return unique values."""
        ids = {SessionManager.new_session_id() for _ in range(100)}
        assert len(ids) == 100

    def test_active_session_count(self) -> None:
        """active_session_count should track sessions correctly."""
        mgr = SessionManager()
        assert mgr.active_session_count == 0
        mgr.get_or_create(1, lambda: SessionContext(session_id="a", transport="stdio"))
        assert mgr.active_session_count == 1
        mgr.get_or_create(2, lambda: SessionContext(session_id="b", transport="stdio"))
        assert mgr.active_session_count == 2
        mgr.remove_session(1)
        assert mgr.active_session_count == 1


# ---------------------------------------------------------------------------
# SessionManager – handler_factory caching
# ---------------------------------------------------------------------------


class TestSessionManagerHandlerFactory:
    """Tests for per-session handler caching via handler_factory."""

    def test_no_factory_returns_handler(self) -> None:
        """When handler_factory is None, get_session_handler returns route.handler."""
        mgr = SessionManager()
        mgr.get_or_create(1, lambda: SessionContext(session_id="s1", transport="stdio"))

        route = MagicMock()
        route.handler = lambda: "ok"
        route.handler_factory = None
        route.tool_name = "tool1"

        handler = mgr.get_session_handler("s1", route)
        assert handler is route.handler

    def test_factory_creates_handler(self) -> None:
        """handler_factory should be called to create a handler for the session."""
        mgr = SessionManager()
        ctx = mgr.get_or_create(
            1, lambda: SessionContext(session_id="s1", transport="stdio")
        )

        created_handler = MagicMock()
        factory = MagicMock(return_value=created_handler)

        route = MagicMock()
        route.handler = MagicMock()
        route.handler_factory = factory
        route.tool_name = "tool1"

        handler = mgr.get_session_handler("s1", route)
        assert handler is created_handler
        factory.assert_called_once_with(ctx)

    def test_factory_caches_handler(self) -> None:
        """Subsequent calls for the same session+tool should return cached handler."""
        mgr = SessionManager()
        mgr.get_or_create(1, lambda: SessionContext(session_id="s1", transport="stdio"))

        created_handler = MagicMock()
        factory = MagicMock(return_value=created_handler)

        route = MagicMock()
        route.handler = MagicMock()
        route.handler_factory = factory
        route.tool_name = "tool1"

        h1 = mgr.get_session_handler("s1", route)
        h2 = mgr.get_session_handler("s1", route)
        assert h1 is h2
        assert factory.call_count == 1

    def test_factory_different_sessions_different_handlers(self) -> None:
        """Different sessions should get different handler instances."""
        mgr = SessionManager()
        mgr.get_or_create(1, lambda: SessionContext(session_id="s1", transport="stdio"))
        mgr.get_or_create(2, lambda: SessionContext(session_id="s2", transport="stdio"))

        route = MagicMock()
        route.handler = MagicMock()
        route.handler_factory = MagicMock(side_effect=[MagicMock(), MagicMock()])
        route.tool_name = "tool1"

        h1 = mgr.get_session_handler("s1", route)
        h2 = mgr.get_session_handler("s2", route)
        assert h1 is not h2

    def test_remove_session_clears_cached_handlers(self) -> None:
        """Removing a session should clear its cached handlers."""
        mgr = SessionManager()
        mgr.get_or_create(1, lambda: SessionContext(session_id="s1", transport="stdio"))

        route = MagicMock()
        route.handler = MagicMock()
        route.handler_factory = MagicMock(return_value=MagicMock())
        route.tool_name = "tool1"

        mgr.get_session_handler("s1", route)
        assert "s1" in mgr._session_handlers

        mgr.remove_session(1)
        assert "s1" not in mgr._session_handlers


# ---------------------------------------------------------------------------
# SessionManager – weakref finalizer
# ---------------------------------------------------------------------------


class TestSessionManagerFinalizer:
    """Tests for weakref-based automatic session cleanup."""

    def test_finalizer_cleans_up_on_gc(self) -> None:
        """When the tracked object is garbage-collected, session is removed."""
        mgr = SessionManager()

        obj = MagicMock()
        key = id(obj)
        mgr.get_or_create(
            key, lambda: SessionContext(session_id="gc1", transport="stdio")
        )
        mgr.register_finalizer(obj, key)
        assert mgr.active_session_count == 1

        # Drop reference and force GC — triggers finalizer
        del obj
        gc.collect()
        assert mgr.active_session_count == 0
