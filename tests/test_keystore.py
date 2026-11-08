"""Tests for the inbound API key store and scope resolver (#186)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from toolregistry_server.keystore import (
    ApiKeyStore,
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

    def test_scoped_principals_cannot_reach_the_proxy_tools(self, store):
        created = store.create("all", {"namespaces": ["*"]})
        resolver = PrincipalResolver(store, operator_tokens=["bootstrap"])
        scoped = resolver.resolve(created.secret)
        operator = resolver.resolve("bootstrap")
        assert scoped is not None
        assert operator is not None

        assert not resolver.allows(scoped, "default", "discover_tools")
        assert not resolver.allows(scoped, "default", "call_deferred")
        assert resolver.allows(operator, "default", "discover_tools")
        assert resolver.allows(operator, "default", "call_deferred")


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
