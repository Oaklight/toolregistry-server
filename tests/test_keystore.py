"""Tests for the inbound API key store and scope resolver (#186)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from toolregistry_server.keystore import (
    ApiKeyStore,
    KeyUsage,
    Principal,
    PrincipalResolver,
    ScopeSpec,
)


class Clock:
    """A controllable UTC clock for expiry and last-used assertions."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int = 1) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    database = ApiKeyStore(tmp_path / "keys.db", clock=clock)
    yield database
    database.close()


# ---------------------------------------------------------------------------
# Creation, secrecy and persistence
# ---------------------------------------------------------------------------


class TestCreate:
    def test_secret_is_returned_once_and_never_in_the_record(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})

        assert created.secret.startswith("trk_")
        assert created.record.id.startswith("key_")
        payload = created.record.to_dict()
        assert "secret" not in payload
        assert "secret_hash" not in payload
        assert created.secret not in json.dumps(payload)

    def test_get_does_not_return_the_secret(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        record = store.get(created.record.id)

        assert record is not None
        assert created.secret not in json.dumps(record.to_dict())

    def test_empty_label_is_rejected(self, store):
        with pytest.raises(ValueError):
            store.create("   ", {"namespaces": ["alpha"]})

    def test_expiry_must_be_in_the_future(self, store, clock):
        with pytest.raises(ValueError):
            store.create(
                "past",
                {"namespaces": ["alpha"]},
                expires_at=clock.now - timedelta(seconds=1),
            )

    def test_record_fields_are_stored(self, store, clock):
        expires = clock.now + timedelta(days=1)
        created = store.create(
            "expiring", {"namespaces": ["alpha"]}, expires_at=expires
        )

        record = created.record
        assert record.label == "expiring"
        assert record.created_at == "2026-01-01T12:00:00+00:00"
        assert record.expires_at is not None
        assert record.revoked_at is None
        assert record.last_used_at is None

    def test_database_dump_contains_no_plaintext_secret(self, tmp_path, clock):
        path = tmp_path / "keys.db"
        with ApiKeyStore(path, clock=clock) as database:
            created = database.create("build-bot", {"namespaces": ["alpha"]})

        raw = path.read_bytes()
        assert created.secret.encode("utf-8") not in raw
        for sidecar in (path.with_name("keys.db-wal"), path.with_name("keys.db-shm")):
            if sidecar.exists():
                assert created.secret.encode("utf-8") not in sidecar.read_bytes()

    def test_each_key_uses_a_distinct_salt(self, tmp_path, clock):
        path = tmp_path / "keys.db"
        with ApiKeyStore(path, clock=clock) as database:
            database.create("one", {"namespaces": ["alpha"]})
            database.create("two", {"namespaces": ["alpha"]})

        connection = sqlite3.connect(path)
        try:
            hashes = [
                row[0]
                for row in connection.execute(
                    "SELECT secret_hash FROM api_keys ORDER BY created_at, id"
                )
            ]
        finally:
            connection.close()

        assert len(hashes) == 2
        salts = {value.split("$")[4] for value in hashes}
        assert len(salts) == 2


# ---------------------------------------------------------------------------
# Verification, revocation and expiry
# ---------------------------------------------------------------------------


class TestVerify:
    def test_valid_secret_returns_a_scoped_principal(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})

        principal = store.verify(created.secret)

        assert principal is not None
        assert principal.key_id == created.record.id
        assert principal.label == "build-bot"
        assert principal.allows("alpha", "alpha-add")
        assert not principal.allows("beta", "beta-echo")

    def test_unknown_and_wrong_secrets_are_rejected(self, store):
        store.create("build-bot", {"namespaces": ["alpha"]})
        wrong = "trk_" + "x" * 43

        assert store.verify("not-a-key") is None
        assert store.verify(wrong) is None
        assert store.verify("") is None
        assert store.verify(None) is None

    def test_last_used_records_only_the_latest_timestamp(self, store, clock):
        created = store.create("build-bot", {"namespaces": ["alpha"]})

        store.verify(created.secret)
        first_record = store.get(created.record.id)
        assert first_record is not None
        first = first_record.last_used_at
        clock.advance(30)
        store.verify(created.secret)
        second_record = store.get(created.record.id)
        assert second_record is not None
        second = second_record.last_used_at

        assert first == "2026-01-01T12:00:00+00:00"
        assert second == "2026-01-01T12:00:30+00:00"

    def test_revocation_applies_to_the_next_request(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        assert store.verify(created.secret) is not None

        store.revoke(created.record.id)

        assert store.verify(created.secret) is None

    def test_revoke_is_idempotent_and_keeps_the_first_timestamp(self, store, clock):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        first = store.revoke(created.record.id)
        clock.advance(60)
        second = store.revoke(created.record.id)

        assert first.revoked_at == second.revoked_at

    def test_revoke_unknown_key_raises(self, store):
        with pytest.raises(KeyError):
            store.revoke("key_missing")

    def test_expiry_applies_to_the_next_request(self, tmp_path, clock):
        database = ApiKeyStore(tmp_path / "keys.db", clock=clock)
        try:
            created = database.create(
                "short",
                {"namespaces": ["alpha"]},
                expires_at=clock.now + timedelta(seconds=5),
            )
            assert database.verify(created.secret) is not None

            clock.advance(6)

            assert database.verify(created.secret) is None
            expired = database.get(created.record.id)
            assert expired is not None
            assert expired.active(clock.now) is False
        finally:
            database.close()


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


class TestScopeSpec:
    def test_namespace_allow_list_is_exact(self):
        scope = ScopeSpec(("alpha",))

        assert scope.allows("alpha", "alpha-add")
        assert not scope.allows("alpha/sub", "alpha-sub-add")
        assert not scope.allows("beta", "beta-echo")

    def test_empty_scope_denies_everything(self):
        scope = ScopeSpec()

        assert not scope.allows("alpha", "alpha-add")
        assert scope.empty()

    def test_explicit_deny_wins_inside_an_allowed_namespace(self):
        scope = ScopeSpec(("alpha",), {"alpha-dangerous": False})

        assert scope.allows("alpha", "alpha-add")
        assert not scope.allows("alpha", "alpha-dangerous")

    def test_explicit_allow_works_outside_the_namespace_list(self):
        scope = ScopeSpec(("alpha",), {"beta-echo": True})

        assert not scope.allows("beta", "beta-other")
        assert scope.allows("beta", "beta-echo")

    def test_wildcards(self):
        assert ScopeSpec(("*",)).allows("anything", "anything-tool")
        assert ScopeSpec(("web/*",)).allows("web", "web-search")
        assert ScopeSpec(("web/*",)).allows("web/search", "web-search")
        assert not ScopeSpec(("web/*",)).allows("webby", "webby-search")

    def test_coerce_accepts_mapping_and_sequence_forms(self):
        mapping = ScopeSpec.coerce(
            {"namespaces": ["alpha"], "tools": {"beta-echo": True}}
        )
        sequence = ScopeSpec.coerce(["alpha"])

        assert mapping.namespaces == ("alpha",)
        assert mapping.tools == {"beta-echo": True}
        assert sequence.namespaces == ("alpha",)

    def test_coerce_rejects_a_non_boolean_tool_override(self):
        with pytest.raises(ValueError):
            ScopeSpec.coerce({"namespaces": ["alpha"], "tools": {"x": "yes"}})


# ---------------------------------------------------------------------------
# Principal resolution
# ---------------------------------------------------------------------------


class TestPrincipalResolver:
    def test_static_tokens_keep_full_unscoped_access(self, store):
        resolver = PrincipalResolver(store, operator_tokens=["bootstrap"])
        routes = _routes()

        principal = resolver.resolve("bootstrap")

        assert principal is not None
        assert principal.unrestricted
        assert principal.allows("beta", "beta-echo")
        assert [
            route.tool_name for route in resolver.filter_routes(principal, routes)
        ] == ["alpha-add", "beta-echo"]

    def test_keys_are_scoped(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        resolver = PrincipalResolver(store, operator_tokens=["bootstrap"])

        principal = resolver.resolve(created.secret)

        assert principal is not None
        assert not principal.unrestricted
        assert principal.allows("alpha", "alpha-add")
        assert not principal.allows("beta", "beta-echo")

    def test_missing_credential_denies_by_default(self, store):
        resolver = PrincipalResolver(store)

        assert resolver.resolve(None) is None
        assert resolver.resolve("") is None
        assert resolver.filter_routes(None, _routes()) == []

    def test_anonymous_mode_is_explicit(self, store):
        resolver = PrincipalResolver(store, allow_anonymous=True)

        principal = resolver.resolve(None)

        assert principal is not None
        assert principal.unrestricted

    def test_bearer_header_is_parsed(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        resolver = PrincipalResolver(store)

        assert resolver.resolve_headers({"Authorization": f"Bearer {created.secret}"})
        assert resolver.resolve_headers({"authorization": "Basic abc"}) is None
        assert resolver.resolve_headers({}) is None

    def test_filter_routes_uses_one_predicate(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        resolver = PrincipalResolver(store)
        principal = resolver.resolve(created.secret)
        assert principal is not None

        routes = _routes()
        filtered = resolver.filter_routes(principal, routes)

        assert [route.tool_name for route in filtered] == ["alpha-add"]
        assert resolver.allows_route(principal, routes[0])
        assert not resolver.allows_route(principal, routes[1])

    def test_scoped_principals_keep_the_scope_aware_proxy_tools(self, store):
        """The proxies enforce the target's scope, so a key keeps them (#195)."""
        created = store.create("alpha-bot", {"namespaces": ["alpha"]})
        resolver = PrincipalResolver(store, operator_tokens=["bootstrap"])
        scoped = resolver.resolve(created.secret)
        operator = resolver.resolve("bootstrap")
        assert scoped is not None
        assert operator is not None

        # The proxies live in the default namespace; a key scoped elsewhere
        # still keeps them, because they judge the tool they act on.
        assert resolver.allows(scoped, "default", "discover_tools")
        assert resolver.allows(scoped, "default", "call_deferred")
        assert not resolver.allows(scoped, "default", "unrelated-tool")
        assert resolver.allows(operator, "default", "discover_tools")
        assert resolver.allows(operator, "default", "call_deferred")

    def test_transparent_proxies_honour_per_tool_overrides(self, store):
        """An explicit deny or allow wins over proxy transparency (#195)."""
        resolver = PrincipalResolver(store)

        denied = store.create(
            "alpha-denied",
            ScopeSpec(("alpha",), {"discover_tools": False, "call_deferred": False}),
        )
        denied_principal = resolver.resolve(denied.secret)
        assert not resolver.allows(denied_principal, "default", "discover_tools")
        assert not resolver.allows(denied_principal, "default", "call_deferred")

        allowed = store.create("alpha-allowed", ScopeSpec(("alpha",)))
        allowed_principal = resolver.resolve(allowed.secret)
        assert resolver.allows(allowed_principal, "default", "discover_tools")
        assert resolver.allows(allowed_principal, "default", "call_deferred")

        explicit = store.create(
            "alpha-explicit",
            ScopeSpec((), {"discover_tools": True, "call_deferred": True}),
        )
        explicit_principal = resolver.resolve(explicit.secret)
        assert resolver.allows(explicit_principal, "default", "discover_tools")
        assert resolver.allows(explicit_principal, "default", "call_deferred")


class _Route:
    def __init__(self, tool_name: str, namespace: str) -> None:
        self.tool_name = tool_name
        self.namespace = namespace


def _routes() -> list[_Route]:
    return [
        _Route("alpha-add", "alpha"),
        _Route("beta-echo", "beta"),
    ]


# ---------------------------------------------------------------------------
# Listing and pagination
# ---------------------------------------------------------------------------


class TestList:
    def test_orders_by_last_used_then_creation_and_paginates(self, tmp_path, clock):
        database = ApiKeyStore(tmp_path / "keys.db", clock=clock)
        try:
            first = database.create("one", {"namespaces": ["alpha"]})
            clock.advance(1)
            second = database.create("two", {"namespaces": ["alpha"]})
            clock.advance(1)
            third = database.create("three", {"namespaces": ["alpha"]})
            clock.advance(1)

            database.verify(second.secret)
            clock.advance(1)
            database.verify(first.secret)

            page = database.list(limit=2)
            rest = database.list(limit=2, offset=2)

            assert [record.id for record in page] == [
                first.record.id,
                second.record.id,
            ]
            assert [record.id for record in rest] == [third.record.id]
        finally:
            database.close()

    def test_revoked_keys_stay_visible(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        store.revoke(created.record.id)

        records = store.list()

        assert [record.id for record in records] == [created.record.id]
        assert records[0].revoked

    def test_page_arguments_are_validated(self, store):
        with pytest.raises(ValueError):
            store.list(limit=0)
        with pytest.raises(ValueError):
            store.list(limit=501)
        with pytest.raises(ValueError):
            store.list(offset=-1)


# ---------------------------------------------------------------------------
# Principal value object
# ---------------------------------------------------------------------------


def test_principal_to_dict_has_no_secret_material(store):
    created = store.create("build-bot", {"namespaces": ["alpha"]})
    principal = store.verify(created.secret)

    assert principal is not None
    payload = principal.to_dict()

    assert payload["key_id"] == created.record.id
    assert "secret" not in json.dumps(payload)
    assert created.secret not in json.dumps(payload)


def test_operator_principal_is_unrestricted():
    principal = Principal.operator()

    assert principal.unrestricted
    assert principal.allows("any", "any-tool")
    assert principal.token_scopes()[0] == "mcp"


# ---------------------------------------------------------------------------
# Per-key concurrency (#145)
# ---------------------------------------------------------------------------


class TestMaxConcurrency:
    def test_a_key_can_carry_its_own_limit(self, store):
        created = store.create("slow-bot", {"namespaces": ["alpha"]}, max_concurrency=2)

        assert created.record.max_concurrency == 2
        assert created.record.to_dict()["max_concurrency"] == 2
        principal = store.verify(created.secret)
        assert principal is not None
        assert principal.max_concurrency == 2

    def test_a_key_without_an_override_uses_the_process_default(self, store):
        created = store.create("plain", {"namespaces": ["alpha"]})

        assert created.record.max_concurrency is None
        principal = store.verify(created.secret)
        assert principal is not None
        assert principal.max_concurrency is None

    @pytest.mark.parametrize("value", [0, -1, True, 2.5, "2"])
    def test_invalid_limits_are_rejected(self, store, value):
        with pytest.raises(ValueError):
            store.create("bad", {"namespaces": ["alpha"]}, max_concurrency=value)

    def test_the_limit_can_be_changed_and_cleared(self, store):
        created = store.create("bot", {"namespaces": ["alpha"]}, max_concurrency=4)

        updated = store.set_max_concurrency(created.record.id, 1)
        assert updated.max_concurrency == 1

        cleared = store.set_max_concurrency(created.record.id, None)
        assert cleared.max_concurrency is None

    def test_an_unknown_key_cannot_be_changed(self, store):
        with pytest.raises(KeyError):
            store.set_max_concurrency("key_missing", 2)

    def test_an_old_database_gains_the_column_in_place(self, tmp_path):
        database = tmp_path / "old.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                secret_digest TEXT NOT NULL,
                secret_hash TEXT NOT NULL,
                scopes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                revoked_at TEXT,
                last_used_at TEXT
            );
            """
        )
        connection.commit()
        connection.close()

        old = ApiKeyStore(database)
        try:
            created = old.create("migrated", {"namespaces": ["alpha"]})
            assert created.record.max_concurrency is None
            assert old.set_max_concurrency(created.record.id, 3).max_concurrency == 3
        finally:
            old.close()


# ---------------------------------------------------------------------------
# Rotation (#248)
# ---------------------------------------------------------------------------


class TestRotate:
    def test_keeps_identity_and_replaces_the_secret(self, store, clock):
        created = store.create(
            "build-bot",
            {"namespaces": ["alpha"], "tools": {"alpha-dangerous": False}},
            expires_at=clock.now + timedelta(days=1),
            max_concurrency=3,
        )
        before = store.get(created.record.id)
        assert before is not None

        rotated = store.rotate(created.record.id)

        assert rotated.secret != created.secret
        assert rotated.secret.startswith("trk_")
        assert rotated.record.id == before.id
        assert rotated.record.label == before.label
        assert rotated.record.scopes == before.scopes
        assert rotated.record.created_at == before.created_at
        assert rotated.record.expires_at == before.expires_at
        assert rotated.record.max_concurrency == 3
        assert "secret" not in rotated.record.to_dict()

    def test_the_old_secret_is_refused_on_the_next_request(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        assert store.verify(created.secret) is not None  # populate the cache

        rotated = store.rotate(created.record.id)

        assert store.verify(created.secret) is None
        principal = store.verify(rotated.secret)
        assert principal is not None
        assert principal.key_id == created.record.id

    def test_rotation_is_persisted(self, tmp_path, clock):
        path = tmp_path / "keys.db"
        with ApiKeyStore(path, clock=clock) as database:
            created = database.create("build-bot", {"namespaces": ["alpha"]})
            rotated = database.rotate(created.record.id)

        with ApiKeyStore(path, clock=clock) as database:
            assert database.verify(created.secret) is None
            assert database.verify(rotated.secret) is not None

    def test_an_unknown_key_is_refused(self, store):
        with pytest.raises(KeyError):
            store.rotate("key_missing")

    def test_a_revoked_key_is_refused(self, store):
        created = store.create("build-bot", {"namespaces": ["alpha"]})
        store.revoke(created.record.id)

        with pytest.raises(ValueError, match="revoked"):
            store.rotate(created.record.id)

    def test_an_expired_key_is_refused(self, tmp_path, clock):
        database = ApiKeyStore(tmp_path / "keys.db", clock=clock)
        try:
            created = database.create(
                "short",
                {"namespaces": ["alpha"]},
                expires_at=clock.now + timedelta(seconds=5),
            )
            clock.advance(6)

            with pytest.raises(ValueError, match="expired"):
                database.rotate(created.record.id)
        finally:
            database.close()


# ---------------------------------------------------------------------------
# Editing (#248)
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_changes_every_field(self, store, clock):
        created = store.create("old", {"namespaces": ["alpha"]}, max_concurrency=2)
        expires = clock.now + timedelta(days=2)

        updated = store.update(
            created.record.id,
            label="new",
            scopes={"namespaces": ["beta"], "tools": {"beta-echo": True}},
            expires_at=expires,
            max_concurrency=5,
        )

        assert updated.id == created.record.id
        assert updated.label == "new"
        assert updated.scopes.namespaces == ("beta",)
        assert updated.scopes.tools == {"beta-echo": True}
        assert updated.expires_at == "2026-01-03T12:00:00+00:00"
        assert updated.max_concurrency == 5
        assert updated.created_at == created.record.created_at

    def test_the_old_secret_still_verifies_and_the_new_scope_applies(self, store):
        created = store.create("bot", {"namespaces": ["alpha"]})
        first = store.verify(created.secret)
        assert first is not None and first.allows("alpha", "alpha-add")

        store.update(created.record.id, scopes={"namespaces": ["beta"]})

        second = store.verify(created.secret)
        assert second is not None
        assert not second.allows("alpha", "alpha-add")
        assert second.allows("beta", "beta-echo")

    def test_optional_fields_can_be_cleared(self, store, clock):
        created = store.create(
            "bot",
            {"namespaces": ["alpha"]},
            expires_at=clock.now + timedelta(days=1),
            max_concurrency=4,
        )

        updated = store.update(created.record.id, expires_at=None, max_concurrency=None)

        assert updated.expires_at is None
        assert updated.max_concurrency is None

    def test_a_revoked_key_is_refused(self, store):
        created = store.create("bot", {"namespaces": ["alpha"]})
        store.revoke(created.record.id)

        with pytest.raises(ValueError, match="revoked"):
            store.update(created.record.id, label="renamed")

    def test_a_concurrent_revoke_is_caught_on_the_write(self, store, monkeypatch):
        created = store.create("bot", {"namespaces": ["alpha"]})
        original = store._select_id  # noqa: SLF001
        reads = {"count": 0}

        def revoking_read(key_id):
            reads["count"] += 1
            if reads["count"] == 2:
                store.revoke(key_id)
            return original(key_id)

        monkeypatch.setattr(store, "_select_id", revoking_read)

        with pytest.raises(ValueError, match="revoked"):
            store.update(created.record.id, label="renamed")

    def test_a_past_expiry_is_refused(self, store, clock):
        created = store.create("bot", {"namespaces": ["alpha"]})

        with pytest.raises(ValueError, match="future"):
            store.update(
                created.record.id,
                expires_at=clock.now - timedelta(seconds=1),
            )

    def test_an_empty_label_is_refused(self, store):
        created = store.create("bot", {"namespaces": ["alpha"]})

        with pytest.raises(ValueError, match="label"):
            store.update(created.record.id, label="   ")

    def test_an_unknown_key_is_refused(self, store):
        with pytest.raises(KeyError):
            store.update("key_missing", label="renamed")

    def test_no_fields_returns_the_current_record(self, store):
        created = store.create("bot", {"namespaces": ["alpha"]})

        updated = store.update(created.record.id)

        assert updated == created.record

    def test_the_change_is_persisted(self, tmp_path, clock):
        path = tmp_path / "keys.db"
        with ApiKeyStore(path, clock=clock) as database:
            created = database.create("bot", {"namespaces": ["alpha"]})
            database.update(created.record.id, label="renamed", max_concurrency=2)

        with ApiKeyStore(path, clock=clock) as database:
            record = database.get(created.record.id)
        assert record is not None
        assert record.label == "renamed"
        assert record.max_concurrency == 2


# ---------------------------------------------------------------------------
# Usage counters (#249)
# ---------------------------------------------------------------------------


class TestUsage:
    def test_counts_each_outcome(self, store, clock):
        created = store.create("bot", {"namespaces": ["alpha"]})

        store.record_usage(created.record.id, "ok")
        clock.advance(10)
        store.record_usage(created.record.id, "ok")
        clock.advance(10)
        store.record_usage(created.record.id, "error")
        store.record_usage(created.record.id, "busy")
        store.record_usage(created.record.id, "denied")

        usage = store.usage(created.record.id)
        assert usage == KeyUsage(
            key_id=created.record.id,
            calls=5,
            ok=2,
            error=1,
            busy=1,
            denied=1,
            last_success_at="2026-01-01T12:00:10+00:00",
        )
        assert "secret" not in json.dumps(usage.to_dict())

    def test_last_success_only_moves_on_an_ok(self, store, clock):
        created = store.create("bot", {"namespaces": ["alpha"]})

        store.record_usage(created.record.id, "error")
        assert store.usage(created.record.id).last_success_at is None

        store.record_usage(created.record.id, "ok")
        first = store.usage(created.record.id).last_success_at
        clock.advance(30)
        store.record_usage(created.record.id, "denied")

        assert first == "2026-01-01T12:00:00+00:00"
        assert store.usage(created.record.id).last_success_at == first

    def test_a_key_without_calls_has_zero_usage(self, store):
        created = store.create("bot", {"namespaces": ["alpha"]})

        usage = store.usage(created.record.id)

        assert usage == KeyUsage.empty(created.record.id)
        assert usage.to_dict()["calls"] == 0

    def test_usage_all_returns_every_recorded_key(self, store):
        first = store.create("one", {"namespaces": ["alpha"]})
        second = store.create("two", {"namespaces": ["alpha"]})
        store.record_usage(first.record.id, "ok")
        store.record_usage(second.record.id, "denied")

        usage = store.usage_all()

        assert usage[first.record.id].ok == 1
        assert usage[second.record.id].denied == 1

    def test_an_unknown_outcome_is_rejected(self, store):
        created = store.create("bot", {"namespaces": ["alpha"]})

        with pytest.raises(ValueError, match="outcome"):
            store.record_usage(created.record.id, "timeout")

    def test_usage_survives_revoke_and_reopen(self, tmp_path, clock):
        path = tmp_path / "keys.db"
        with ApiKeyStore(path, clock=clock) as database:
            created = database.create("bot", {"namespaces": ["alpha"]})
            database.record_usage(created.record.id, "ok")
            database.record_usage(created.record.id, "denied")
            database.revoke(created.record.id)

        with ApiKeyStore(path, clock=clock) as database:
            usage = database.usage(created.record.id)
        assert usage.calls == 2
        assert usage.ok == 1
        assert usage.denied == 1
