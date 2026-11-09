"""Tests for runtime source management (#185).

The manager owns the live source list: it registers to a running registry,
records per-source status, and persists API-originated changes.  These tests
port the Hub hot-reload suite to the server API and add the add/remove/replace,
persistence and batching cases the gateway needs.
"""

from __future__ import annotations

import dataclasses
import json
import time

import pytest
from toolregistry import ToolRegistry
from toolregistry.config import MCPSource, PythonSource, load_config

from toolregistry_server import PROFILE_DISABLE_REASON, RouteTable, apply_profile
from toolregistry_server.source_manager import (
    CONFIG_DISABLE_REASON,
    ConfigWatcher,
    SourceEntry,
    SourceManager,
    SourceStatus,
    _describe_source,
    _entry_id,
    index_sources,
    source_key,
)

ALPHA_MODULE = """
class Alpha:
    def one(self) -> str:
        \"\"\"First alpha tool.\"\"\"
        return "alpha-one"

    def two(self) -> str:
        \"\"\"Second alpha tool.\"\"\"
        return "alpha-two"
"""

BETA_MODULE = """
class Beta:
    def one(self) -> str:
        \"\"\"Only beta tool.\"\"\"
        return "beta-one"
"""

GAMMA_MODULE = """
class Gamma:
    def one(self) -> str:
        \"\"\"Only gamma tool.\"\"\"
        return "gamma-one"
"""


@pytest.fixture
def tools_dir(tmp_path, monkeypatch):
    """Expose alpha/beta/gamma modules on sys.path for PythonSource."""
    (tmp_path / "alpha.py").write_text(ALPHA_MODULE, encoding="utf-8")
    (tmp_path / "beta.py").write_text(BETA_MODULE, encoding="utf-8")
    (tmp_path / "gamma.py").write_text(GAMMA_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_config(path, *entries: str, extra: str = "") -> None:
    """Write a JSONC config, keeping the syntax obvious in each test."""
    body = "{\n" + extra + '"tools": [' + ", ".join(entries) + "]\n}\n"
    path.write_text(body, encoding="utf-8")


ALPHA = '{"class": "alpha.Alpha", "namespace": "alpha"}'
ALPHA_MATH = '{"class": "alpha.Alpha", "namespace": "math"}'
BETA = '{"class": "beta.Beta", "namespace": "beta"}'
GAMMA = '{"class": "gamma.Gamma", "namespace": "gamma"}'
ALPHA_TAGGED = (
    '{"class": "alpha.Alpha", "namespace": "tagged", "tags": ["file_system"]}'
)
ALPHA_TAGGED_V2 = (
    '{"class": "alpha.Alpha", "namespace": "tagged", '
    '"tags": ["file_system", "read_only"]}'
)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "tools.jsonc"
    _write_config(path, ALPHA, BETA)
    return path


def _build(path, **kwargs) -> SourceManager:
    """Build a manager the way App.prepare_registry does."""
    config = load_config(str(path))
    registry = ToolRegistry()
    manager = SourceManager(registry, config, config_path=str(path), **kwargs)
    manager.apply_initial()
    return manager


@pytest.fixture
def manager(tools_dir, config_path):
    return _build(config_path)


def _names(manager: SourceManager) -> set[str]:
    return set(manager.registry._tools)


def _entry(manager: SourceManager, namespace: str):
    return next(e for e in manager.list() if e.source.namespace == namespace)


class TestInitialBuild:
    def test_records_one_entry_per_source(self, manager):
        entries = manager.list()

        assert [e.source.namespace for e in entries] == ["alpha", "beta"]
        assert _names(manager) >= {"alpha-one", "alpha-two", "beta-one"}
        assert all(e.status is SourceStatus.APPLIED for e in entries)

    def test_names_and_tools_match(self, manager):
        alpha = _entry(manager, "alpha")

        assert alpha.names == {"alpha-one", "alpha-two"}
        assert alpha.names <= _names(manager)

    def test_excluded_source_is_recorded_without_tools(self, tools_dir, tmp_path):
        path = tmp_path / "tools.jsonc"
        _write_config(
            path,
            ALPHA,
            BETA,
            extra='"mode": "allowlist", "enabled": ["alpha"], ',
        )
        manager = _build(path)

        beta = _entry(manager, "beta")
        assert beta.status is SourceStatus.EXCLUDED
        assert beta.names == frozenset()
        assert "beta-one" not in _names(manager)


class TestReloadDiff:
    def test_added_source_is_registered(self, manager, config_path):
        assert "gamma-one" not in _names(manager)

        _write_config(config_path, ALPHA, BETA, GAMMA)
        assert manager.reload_from_path() is True

        assert "gamma-one" in _names(manager)
        assert _entry(manager, "gamma").status is SourceStatus.APPLIED

    def test_removed_source_is_unregistered(self, manager, config_path):
        _write_config(config_path, ALPHA)
        manager.reload_from_path()

        assert "beta-one" not in _names(manager)
        assert "alpha-one" in _names(manager)
        assert [e.source.namespace for e in manager.list()] == ["alpha"]

    def test_scope_change_disables_and_reenables(self, manager, config_path):
        _write_config(
            config_path,
            ALPHA,
            BETA,
            extra='"mode": "allowlist", "enabled": ["alpha"], ',
        )
        manager.reload_from_path()

        # The tool stays registered so callers holding a reference still
        # resolve a name, but it is not served.
        assert "beta-one" in _names(manager)
        assert manager.registry.is_enabled("beta-one") is False
        assert manager.registry.get_disable_reason("beta-one") == CONFIG_DISABLE_REASON

        _write_config(config_path, ALPHA, BETA)
        manager.reload_from_path()

        assert manager.registry.is_enabled("beta-one") is True

    def test_changed_source_is_replaced(self, manager, config_path):
        old_tool = manager.registry._tools["alpha-one"]

        _write_config(config_path, ALPHA_MATH, BETA)
        manager.reload_from_path()

        assert "alpha-one" not in _names(manager)
        assert "math-one" in _names(manager)
        assert manager.registry._tools["math-one"] is not old_tool

    def test_unrelated_edit_does_not_touch_registry(self, manager, config_path):
        before = dict(manager.registry._tools)

        _write_config(
            config_path,
            ALPHA,
            BETA,
            extra='"mode": "denylist", "disabled": [], ',
        )
        manager.reload_from_path()

        assert manager.registry._tools == before

    def test_reordering_is_not_a_change(self, manager, config_path):
        before = dict(manager.registry._tools)

        _write_config(config_path, BETA, ALPHA)
        manager.reload_from_path()

        assert manager.registry._tools == before
        assert [e.source.namespace for e in manager.list()] == ["beta", "alpha"]


class TestRegistrationCallback:
    def test_callback_receives_only_the_registered_names(self, manager, config_path):
        seen: list[set[str]] = []
        manager.on_registered = lambda _registry, names: seen.append(set(names))

        _write_config(config_path, ALPHA, BETA, GAMMA)
        manager.reload_from_path()

        assert seen == [{"gamma-one"}]

    def test_callback_receives_the_startup_names(self, tools_dir, tmp_path):
        seen: list[set[str]] = []
        path = tmp_path / "tools.jsonc"
        _write_config(path, ALPHA, BETA)

        manager = _build(
            path,
            on_registered=lambda _registry, names: seen.append(set(names)),
        )

        assert seen == [{"alpha-one", "alpha-two", "beta-one"}]
        assert set(_names(manager)) == {"alpha-one", "alpha-two", "beta-one"}

    def test_no_callback_without_sources(self, tools_dir, tmp_path):
        seen: list[set[str]] = []
        path = tmp_path / "tools.jsonc"
        _write_config(path)

        _build(
            path,
            on_registered=lambda _registry, names: seen.append(set(names)),
        )

        assert seen == []

    def test_no_callback_when_every_source_is_excluded(self, tools_dir, tmp_path):
        seen: list[set[str]] = []
        path = tmp_path / "tools.jsonc"
        _write_config(
            path,
            ALPHA,
            extra='"mode": "allowlist", "enabled": ["nothing"], ',
        )

        manager = _build(
            path,
            on_registered=lambda _registry, names: seen.append(set(names)),
        )

        assert seen == []
        assert [entry.status for entry in manager.list()] == [SourceStatus.EXCLUDED]

    def test_a_raising_startup_callback_keeps_the_tools(self, tools_dir, tmp_path):
        def boom(_registry, _names):
            raise RuntimeError("hook failure")

        path = tmp_path / "tools.jsonc"
        _write_config(path, ALPHA, BETA)

        manager = _build(path, on_registered=boom)

        assert set(_names(manager)) == {"alpha-one", "alpha-two", "beta-one"}
        assert [entry.status for entry in manager.list()] == [
            SourceStatus.APPLIED,
            SourceStatus.APPLIED,
        ]

    def test_file_metadata_wins_over_the_callback(self, tools_dir, tmp_path):
        path = tmp_path / "tools.jsonc"
        metadata = '"tool_metadata": {"gamma-one": {"defer": false}}, '
        _write_config(path, ALPHA, extra=metadata)
        manager = _build(
            path,
            on_registered=lambda registry, names: [
                registry.update_tool_metadata(name, defer=True) for name in names
            ],
        )

        _write_config(path, ALPHA, GAMMA, extra=metadata)
        manager.reload_from_path()

        # The callback runs first (Hub metadata defaults), the file last.
        assert manager.registry._tools["gamma-one"].metadata.defer is False

    def test_a_raising_callback_does_not_desynchronise_the_manager(
        self, manager, config_path
    ):
        def boom(_registry, _names):
            raise RuntimeError("hook failure")

        manager.on_registered = boom
        _write_config(config_path, ALPHA, BETA, GAMMA)

        assert manager.reload_from_path() is True
        assert "gamma-one" in _names(manager)
        assert _entry(manager, "gamma").status is SourceStatus.APPLIED

        # The declaration was recorded, so a later diff still removes it.
        _write_config(config_path, ALPHA, BETA)
        assert manager.reload_from_path() is True
        assert "gamma-one" not in _names(manager)


class TestScopeTransitions:
    def test_source_excluded_at_startup_registers_when_in_scope(
        self, tools_dir, tmp_path
    ):
        path = tmp_path / "tools.jsonc"
        _write_config(
            path,
            ALPHA,
            BETA,
            extra='"mode": "allowlist", "enabled": ["alpha"], ',
        )
        manager = _build(path)
        assert "beta-one" not in _names(manager)

        _write_config(path, ALPHA, BETA)
        manager.reload_from_path()

        assert "beta-one" in _names(manager)
        assert _entry(manager, "beta").status is SourceStatus.APPLIED


class TestFailedRegistration:
    def test_a_failed_source_is_retried_on_the_next_reload(
        self, manager, config_path, monkeypatch
    ):
        import toolregistry_server.source_manager as source_manager

        real = source_manager.register_python_source
        attempts = 0

        def flaky(registry, source):
            nonlocal attempts
            if source.namespace == "gamma":
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("transient failure")
            return real(registry, source)

        monkeypatch.setattr(source_manager, "register_python_source", flaky)
        _write_config(config_path, ALPHA, BETA, GAMMA)
        manager.reload_from_path()

        gamma = _entry(manager, "gamma")
        assert gamma.status is SourceStatus.FAILED
        assert gamma.error is not None and "transient failure" in gamma.error
        assert "gamma-one" not in _names(manager)
        # The declaration is recorded as the current one, so the next diff
        # sees it unchanged and retries because the status is failed.
        manager.reload_from_path()
        assert _entry(manager, "gamma").status is SourceStatus.APPLIED
        assert "gamma-one" in _names(manager)

    def test_partial_registration_is_rolled_back(
        self, manager, config_path, monkeypatch
    ):
        import toolregistry_server.source_manager as source_manager

        real = source_manager.register_python_source

        def partial_then_raise(registry, source):
            if source.namespace == "gamma":
                real(registry, source)
                raise RuntimeError("failed after a partial registration")
            return real(registry, source)

        monkeypatch.setattr(
            source_manager, "register_python_source", partial_then_raise
        )
        _write_config(config_path, ALPHA, BETA, GAMMA)
        manager.reload_from_path()

        # Without the rollback this tool would stay registered but be absent
        # from the entry's names, so a later removal would never see it.
        assert "gamma-one" not in _names(manager)

        monkeypatch.setattr(source_manager, "register_python_source", real)
        manager.reload_from_path()
        assert "gamma-one" in _names(manager)

        _write_config(config_path, ALPHA, BETA)
        manager.reload_from_path()
        assert "gamma-one" not in _names(manager)


class TestProfileFilter:
    @staticmethod
    def _build_with_profile(path):
        manager = _build(path, profile="remote")
        apply_profile(manager.registry, "remote")
        return manager

    def test_redeclared_source_stays_profile_disabled(self, tools_dir, tmp_path):
        path = tmp_path / "tools.jsonc"
        _write_config(path, ALPHA, ALPHA_TAGGED)
        manager = self._build_with_profile(path)
        assert (
            manager.registry.get_disable_reason("tagged-one") == PROFILE_DISABLE_REASON
        )

        # An administrator's manual disable must survive an unrelated change.
        manager.registry.disable("alpha-two", "admin edit")

        _write_config(path, ALPHA, ALPHA_TAGGED_V2)
        manager.reload_from_path()

        # The tagged source was replaced; its new tools are still filtered.
        assert (
            manager.registry.get_disable_reason("tagged-one") == PROFILE_DISABLE_REASON
        )
        assert manager.registry.get_disable_reason("alpha-two") == "admin edit"

    def test_unrelated_reload_does_not_change_profile_state(self, tools_dir, tmp_path):
        path = tmp_path / "tools.jsonc"
        _write_config(path, ALPHA, ALPHA_TAGGED)
        manager = self._build_with_profile(path)

        _write_config(path, ALPHA, ALPHA_TAGGED, BETA)
        manager.reload_from_path()

        assert "beta-one" in _names(manager)
        assert (
            manager.registry.get_disable_reason("tagged-one") == PROFILE_DISABLE_REASON
        )


class TestDescribeSource:
    def test_url_userinfo_is_redacted(self):
        source = MCPSource(
            transport="streamable-http", url="https://u:secret@example.com/mcp"
        )
        assert _describe_source(source) == "https://example.com/mcp"

    def test_stdio_arguments_are_not_logged(self):
        source = MCPSource(
            transport="stdio", command=("npx", "srv", "--token", "SECRET")
        )
        assert _describe_source(source) == "npx ..."


class TestMalformedConfig:
    def test_invalid_config_leaves_registry_untouched(self, manager, config_path):
        before = dict(manager.registry._tools)

        config_path.write_text("{ this is not jsonc", encoding="utf-8")
        assert manager.reload_from_path() is False

        assert manager.registry._tools == before
        assert manager.last_reload_error


class TestStatuses:
    """An empty name set is disambiguated by the per-source status."""

    def test_zero_tool_source_is_not_registered_again(
        self, tools_dir, tmp_path, monkeypatch
    ):
        import toolregistry_server.source_manager as source_manager

        path = tmp_path / "tools.jsonc"
        _write_config(path, ALPHA, BETA)
        real = source_manager.register_python_source
        calls: list[str] = []

        def registers_nothing(registry, source):
            if source.namespace == "beta":
                calls.append(source.namespace)
                return None
            return real(registry, source)

        monkeypatch.setattr(source_manager, "register_python_source", registers_nothing)
        manager = _build(path)

        beta = _entry(manager, "beta")
        assert beta.status is SourceStatus.APPLIED
        assert beta.names == frozenset()
        assert calls == ["beta"]

        # An applied source that produced no tools must not be registered
        # again on an unrelated edit (#233).
        _write_config(
            path,
            ALPHA,
            BETA,
            extra='"mode": "denylist", "disabled": [], ',
        )
        manager.reload_from_path()
        assert calls == ["beta"]

    def test_failed_source_retries_until_success(
        self, manager, config_path, monkeypatch
    ):
        import toolregistry_server.source_manager as source_manager

        real = source_manager.register_python_source
        attempts = 0

        def flaky(registry, source):
            nonlocal attempts
            if source.namespace == "gamma":
                attempts += 1
                if attempts < 3:
                    raise RuntimeError("transient failure")
            return real(registry, source)

        monkeypatch.setattr(source_manager, "register_python_source", flaky)
        _write_config(config_path, ALPHA, BETA, GAMMA)
        manager.reload_from_path()
        assert _entry(manager, "gamma").status is SourceStatus.FAILED

        manager.reload_from_path()
        assert _entry(manager, "gamma").status is SourceStatus.FAILED

        manager.reload_from_path()
        assert _entry(manager, "gamma").status is SourceStatus.APPLIED
        assert "gamma-one" in _names(manager)


class TestMutations:
    def test_add_registers_and_persists(self, manager, config_path):
        before = config_path.read_text(encoding="utf-8")

        result = manager.add(PythonSource(class_path="gamma.Gamma", namespace="gamma"))

        assert result.entry.status is SourceStatus.APPLIED
        assert result.removed is False
        assert "gamma-one" in _names(manager)
        assert result.persisted is True
        assert result.backup_path is not None
        assert config_path.read_text(encoding="utf-8") != before
        assert "gamma.Gamma" in config_path.read_text(encoding="utf-8")
        # A restart reproduces the state.
        restarted = _build(config_path)
        assert "gamma-one" in _names(restarted)

    def test_remove_unregisters_and_persists(self, manager, config_path):
        key = _entry(manager, "beta").key

        result = manager.remove(key)

        assert result.entry.source.namespace == "beta"
        assert result.removed is True
        assert "beta-one" not in _names(manager)
        assert result.persisted is True
        assert "beta.Beta" not in config_path.read_text(encoding="utf-8")
        restarted = _build(config_path)
        assert "beta-one" not in _names(restarted)

    def test_remove_accepts_the_opaque_id(self, manager):
        source_id = _entry(manager, "beta").to_dict()["id"]

        result = manager.remove(source_id)

        assert result.removed is True
        assert "beta-one" not in _names(manager)
        with pytest.raises(KeyError):
            manager.get(source_id)

    def test_replace_accepts_the_opaque_id(self, manager):
        source_id = _entry(manager, "alpha").to_dict()["id"]

        manager.replace(
            source_id, PythonSource(class_path="alpha.Alpha", namespace="math")
        )

        assert "math-one" in _names(manager)
        assert "alpha-one" not in _names(manager)

    def test_replace_swaps_the_declaration(self, manager, config_path):
        key = _entry(manager, "alpha").key

        result = manager.replace(
            key, PythonSource(class_path="alpha.Alpha", namespace="math")
        )

        assert "alpha-one" not in _names(manager)
        assert "math-one" in _names(manager)
        assert result.entry.source.namespace == "math"
        restarted = _build(config_path)
        assert "math-one" in _names(restarted)

    def test_remove_unknown_key_raises(self, manager):
        with pytest.raises(KeyError):
            manager.remove(("NoSuchSource",))

    def test_retry_reruns_a_failed_source(self, manager, config_path, monkeypatch):
        import toolregistry_server.source_manager as source_manager

        real = source_manager.register_python_source
        attempts = 0

        def flaky(registry, source):
            nonlocal attempts
            if source.namespace == "gamma":
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("transient failure")
            return real(registry, source)

        monkeypatch.setattr(source_manager, "register_python_source", flaky)
        result = manager.add(PythonSource(class_path="gamma.Gamma", namespace="gamma"))
        assert result.entry.status is SourceStatus.FAILED

        entry = manager.retry(result.entry.key)
        assert entry.status is SourceStatus.APPLIED
        assert "gamma-one" in _names(manager)

        # Retrying an applied source is a no-op.
        assert manager.retry(result.entry.key).status is SourceStatus.APPLIED

    def test_write_config_false_keeps_changes_in_memory(self, tools_dir, tmp_path):
        path = tmp_path / "tools.jsonc"
        _write_config(path, ALPHA)
        manager = _build(path, write_config=False)
        before = path.read_text(encoding="utf-8")

        result = manager.add(PythonSource(class_path="beta.Beta", namespace="beta"))

        assert "beta-one" in _names(manager)
        assert result.persisted is False
        assert result.warnings
        assert path.read_text(encoding="utf-8") == before

    def test_read_only_config_is_reported(self, manager, monkeypatch):
        import toolregistry_server.source_manager as source_manager

        def refuse(*_args, **_kwargs):
            raise PermissionError("read-only file system")

        monkeypatch.setattr(source_manager, "save_config", refuse)

        result = manager.add(PythonSource(class_path="gamma.Gamma", namespace="gamma"))

        assert "gamma-one" in _names(manager)
        assert result.persisted is False
        assert result.persist_error is not None
        assert "read-only" in result.persist_error


class TestEntryPayload:
    def test_to_dict_shape_and_stable_id(self, manager, config_path):
        alpha = _entry(manager, "alpha")
        payload = alpha.to_dict()

        assert payload["id"].startswith("src_")
        assert len(payload["id"]) == 4 + 16
        assert payload["kind"] == "python"
        assert payload["detail"] == "alpha.Alpha"
        assert payload["namespace"] == "alpha"
        assert payload["status"] == "applied"
        assert payload["last_error"] is None
        assert payload["names"] == ["alpha-one", "alpha-two"]
        assert payload["tool_count"] == {"enabled": 2, "total": 2}

        # The id survives a restart and a reordering of the file.
        _write_config(config_path, BETA, ALPHA)
        restarted = _build(config_path)
        assert _entry(restarted, "alpha").to_dict()["id"] == payload["id"]

    def test_enabled_count_reflects_disables(self, manager):
        manager.registry.disable("alpha-one", "admin edit")
        alpha = next(p for p in manager.to_dicts() if p["namespace"] == "alpha")

        assert alpha["tool_count"] == {"enabled": 1, "total": 2}

    def test_payload_does_not_expose_the_internal_key(self):
        source = MCPSource(
            transport="streamable-http",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer SECRET-TOKEN"},
        )
        entry = SourceEntry((), source, frozenset(), SourceStatus.EXCLUDED)

        payload = entry.to_dict()
        text = json.dumps(payload)

        # The internal key contains the raw URL and the declaration; neither
        # may reach an admin client.  Only the opaque id is exposed.
        assert "key" not in payload
        assert "SECRET-TOKEN" not in text
        assert "Authorization" not in text
        assert payload["detail"] == "https://example.com/mcp"

    def test_same_target_with_different_credentials_has_distinct_ids(self):
        base = MCPSource(
            transport="streamable-http",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer alpha"},
        )
        other = MCPSource(
            transport="streamable-http",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer beta"},
        )

        assert _entry_id(base) != _entry_id(other)
        # Deterministic for the same declaration, including a rebuild.
        assert _entry_id(base) == _entry_id(dataclasses.replace(base))


class TestBatchRefresh:
    def test_one_diff_produces_one_route_rebuild(self, manager):
        table = RouteTable(manager.registry)
        manager.attach_route_table(table)
        events: list[str] = []
        table.add_listener(lambda _name, event: events.append(event))

        manager.add(PythonSource(class_path="gamma.Gamma", namespace="gamma"))

        assert events.count("refresh_all") == 1
        assert table.get_route("gamma-one") is not None

    def test_nested_batches_join_the_outermost(self, manager):
        table = RouteTable(manager.registry)
        manager.attach_route_table(table)
        events: list[str] = []
        table.add_listener(lambda _name, event: events.append(event))

        with table.batch_changes():
            manager.add(PythonSource(class_path="gamma.Gamma", namespace="gamma"))
            with table.batch_changes():
                manager.add(PythonSource(class_path="beta.Beta", namespace="beta2"))

        assert events.count("refresh_all") == 1
        assert table.get_route("gamma-one") is not None
        assert table.get_route("beta2-one") is not None


class TestWatcher:
    def test_reloads_when_the_file_changes(self, manager, config_path):
        watcher = ConfigWatcher(manager, interval=0.02)
        watcher.start()
        try:
            time.sleep(0.05)
            _write_config(config_path, ALPHA, BETA, GAMMA)

            deadline = time.time() + 5
            while time.time() < deadline and "gamma-one" not in _names(manager):
                time.sleep(0.02)

            assert "gamma-one" in _names(manager)
        finally:
            watcher.stop()
            watcher.join(timeout=2)
            assert not watcher.is_alive()

    def test_poll_once_is_a_noop_after_the_first_poll(self, manager):
        watcher = ConfigWatcher(manager, interval=0.02)

        # The first poll always runs: it reconciles the window between the
        # registry build and the watcher start.  The diff is idempotent.
        assert watcher.poll_once() is True
        assert watcher.poll_once() is False

    def test_first_poll_picks_up_an_edit_before_the_watcher_started(
        self, manager, config_path
    ):
        _write_config(config_path, ALPHA, BETA, GAMMA)
        watcher = ConfigWatcher(manager, interval=0.02)

        assert watcher.poll_once() is True
        assert "gamma-one" in _names(manager)

    def test_missing_file_is_not_an_error(self, manager, config_path):
        watcher = ConfigWatcher(manager, interval=0.02)
        config_path.unlink()

        assert watcher.poll_once() is False

    def test_run_survives_a_failing_poll(self, manager, monkeypatch):
        watcher = ConfigWatcher(manager, interval=0.01)
        calls = 0

        def boom():
            nonlocal calls
            calls += 1
            if calls >= 2:
                watcher.stop()
            raise RuntimeError("boom")

        monkeypatch.setattr(watcher, "poll_once", boom)
        watcher.start()
        watcher.join(timeout=3)

        assert calls >= 2
        assert not watcher.is_alive()

    def test_stop_waits_for_the_thread(self, manager):
        watcher = ConfigWatcher(manager, interval=0.01)
        watcher.start()

        watcher.stop(timeout=2)

        assert not watcher.is_alive()

    def test_own_write_is_not_reloaded(self, manager, config_path):
        watcher = ConfigWatcher(manager, interval=0.02)

        manager.add(PythonSource(class_path="gamma.Gamma", namespace="gamma"))

        # The manager recorded the hash it wrote; the watcher must not treat
        # its own write as an external edit.
        assert watcher.poll_once() is False
        assert "gamma-one" in _names(manager)

        # An external edit is still picked up.
        _write_config(config_path, ALPHA, BETA)
        assert watcher.poll_once() is True
        assert "gamma-one" not in _names(manager)


class TestSourceKeys:
    def test_index_sources_keeps_duplicate_declarations(self):
        one = PythonSource(class_path="alpha.Alpha", namespace="alpha")
        two = PythonSource(class_path="alpha.Alpha", namespace="alpha")

        indexed = index_sources((one, two))

        assert len(indexed) == 2
        keys = list(indexed)
        assert keys[0] == source_key(one)
        assert len(keys[1]) == len(keys[0]) + 1
