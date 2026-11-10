"""Tests for the ``toolregistry-server keys`` command (#186)."""

from __future__ import annotations

import json

from toolregistry_server.cli import CLI
from toolregistry_server.keys_cli import dispatch_keys
from toolregistry_server.keystore import ApiKeyStore


def _parse(*args: str):
    return CLI().create_parser().parse_args(list(args))


def test_create_prints_the_secret_once(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse(
        "keys",
        "--db",
        database,
        "create",
        "--label",
        "build-bot",
        "--namespace",
        "alpha",
    )

    assert dispatch_keys(parsed) == 0
    output = capsys.readouterr().out
    assert "Secret (shown once): trk_" in output
    assert "namespaces:  alpha" in output

    secret = output.split("Secret (shown once): ", 1)[1].strip()
    with ApiKeyStore(database) as store:
        principal = store.verify(secret)
        assert principal is not None
        assert principal.allows("alpha", "alpha-add")
        assert not principal.allows("beta", "beta-echo")


def test_json_create_has_the_secret_and_the_record(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse(
        "keys",
        "--db",
        database,
        "create",
        "--label",
        "build-bot",
        "--namespace",
        "alpha",
        "--deny-tool",
        "alpha-dangerous",
        "--json",
    )

    assert dispatch_keys(parsed) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["secret"].startswith("trk_")
    assert payload["label"] == "build-bot"
    assert payload["scopes"]["namespaces"] == ["alpha"]
    assert payload["scopes"]["tools"] == {"alpha-dangerous": False}


def test_list_get_and_revoke(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "one", "--json")
    assert dispatch_keys(create) == 0
    created = json.loads(capsys.readouterr().out)
    key_id = created["id"]

    listing = _parse("keys", "--db", database, "list", "--json")
    assert dispatch_keys(listing) == 0
    records = json.loads(capsys.readouterr().out)
    assert [record["id"] for record in records] == [key_id]
    assert "secret" not in records[0]

    get = _parse("keys", "--db", database, "get", key_id, "--json")
    assert dispatch_keys(get) == 0
    assert json.loads(capsys.readouterr().out)["id"] == key_id

    revoke = _parse("keys", "--db", database, "revoke", key_id, "--json")
    assert dispatch_keys(revoke) == 0
    assert json.loads(capsys.readouterr().out)["revoked_at"] is not None

    with ApiKeyStore(database) as store:
        assert store.verify(created["secret"]) is None


def test_missing_key_exits_non_zero(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse("keys", "--db", database, "get", "key_missing")

    assert dispatch_keys(parsed) == 1
    assert "Key not found" in capsys.readouterr().err


def test_create_without_a_namespace_denies_everything(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse("keys", "--db", database, "create", "--label", "empty", "--json")

    assert dispatch_keys(parsed) == 0
    payload = json.loads(capsys.readouterr().out)

    with ApiKeyStore(database) as store:
        principal = store.verify(payload["secret"])
    assert principal is not None
    assert not principal.allows("alpha", "alpha-add")


def test_create_accepts_a_concurrency_limit(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse(
        "keys",
        "--db",
        database,
        "create",
        "--label",
        "slow-bot",
        "--namespace",
        "alpha",
        "--concurrency",
        "3",
        "--json",
    )

    assert dispatch_keys(parsed) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["max_concurrency"] == 3

    with ApiKeyStore(database) as store:
        principal = store.verify(payload["secret"])
    assert principal is not None
    assert principal.max_concurrency == 3


def test_create_refuses_an_invalid_concurrency_limit(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse(
        "keys", "--db", database, "create", "--label", "bad", "--concurrency", "0"
    )

    assert dispatch_keys(parsed) == 2
    assert "max_concurrency" in capsys.readouterr().err


def test_set_concurrency_changes_and_clears_the_override(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]

    set_limit = _parse(
        "keys", "--db", database, "set-concurrency", key_id, "--value", "2", "--json"
    )
    assert dispatch_keys(set_limit) == 0
    assert json.loads(capsys.readouterr().out)["max_concurrency"] == 2

    clear = _parse(
        "keys", "--db", database, "set-concurrency", key_id, "--clear", "--json"
    )
    assert dispatch_keys(clear) == 0
    assert json.loads(capsys.readouterr().out)["max_concurrency"] is None


def test_set_concurrency_reports_an_unknown_key(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse(
        "keys", "--db", database, "set-concurrency", "key_missing", "--value", "2"
    )

    assert dispatch_keys(parsed) == 1
    assert "Key not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Rotation and editing (#248)
# ---------------------------------------------------------------------------


def test_rotate_prints_the_new_secret_once(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse(
        "keys",
        "--db",
        database,
        "create",
        "--label",
        "bot",
        "--namespace",
        "alpha",
        "--json",
    )
    assert dispatch_keys(create) == 0
    created = json.loads(capsys.readouterr().out)

    rotate = _parse("keys", "--db", database, "rotate", created["id"])
    assert dispatch_keys(rotate) == 0
    output = capsys.readouterr().out
    assert f"id:          {created['id']}" in output
    secret = output.split("Secret (shown once): ", 1)[1].strip()
    assert secret.startswith("trk_")
    assert secret != created["secret"]

    with ApiKeyStore(database) as store:
        assert store.verify(created["secret"]) is None
        principal = store.verify(secret)
        assert principal is not None
        assert principal.key_id == created["id"]


def test_rotate_json_has_the_record_and_the_secret(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]

    rotate = _parse("keys", "--db", database, "rotate", key_id, "--json")
    assert dispatch_keys(rotate) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["id"] == key_id
    assert payload["secret"].startswith("trk_")
    assert "secret_hash" not in payload


def test_rotate_reports_an_unknown_key(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse("keys", "--db", database, "rotate", "key_missing")

    assert dispatch_keys(parsed) == 1
    assert "Key not found" in capsys.readouterr().err


def test_rotate_refuses_a_revoked_key(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]
    revoke = _parse("keys", "--db", database, "revoke", key_id)
    assert dispatch_keys(revoke) == 0
    capsys.readouterr()

    rotate = _parse("keys", "--db", database, "rotate", key_id)

    assert dispatch_keys(rotate) == 2
    err = capsys.readouterr().err
    assert "revoked" in err
    assert "Traceback" not in err


def test_update_changes_the_named_fields(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "old", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]

    update = _parse(
        "keys",
        "--db",
        database,
        "update",
        key_id,
        "--label",
        "new",
        "--namespace",
        "beta",
        "--deny-tool",
        "beta-echo",
        "--concurrency",
        "2",
        "--json",
    )
    assert dispatch_keys(update) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["label"] == "new"
    assert payload["scopes"]["namespaces"] == ["beta"]
    assert payload["scopes"]["tools"] == {"beta-echo": False}
    assert payload["max_concurrency"] == 2

    with ApiKeyStore(database) as store:
        record = store.get(key_id)
    assert record is not None
    assert record.label == "new"
    assert record.max_concurrency == 2


def test_update_can_clear_expiry_and_concurrency(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse(
        "keys",
        "--db",
        database,
        "create",
        "--label",
        "bot",
        "--concurrency",
        "3",
        "--expires-at",
        "2030-01-01T00:00:00Z",
        "--json",
    )
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]

    update = _parse(
        "keys",
        "--db",
        database,
        "update",
        key_id,
        "--clear-expiry",
        "--clear-concurrency",
        "--json",
    )
    assert dispatch_keys(update) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["expires_at"] is None
    assert payload["max_concurrency"] is None


def test_update_merges_scope_categories(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse(
        "keys",
        "--db",
        database,
        "create",
        "--label",
        "bot",
        "--namespace",
        "web",
        "--deny-tool",
        "web-dangerous",
        "--json",
    )
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]

    only_namespace = _parse(
        "keys",
        "--db",
        database,
        "update",
        key_id,
        "--namespace",
        "web2",
        "--json",
    )
    assert dispatch_keys(only_namespace) == 0
    merged = json.loads(capsys.readouterr().out)
    assert merged["scopes"]["namespaces"] == ["web2"]
    assert merged["scopes"]["tools"] == {"web-dangerous": False}

    only_tool = _parse(
        "keys",
        "--db",
        database,
        "update",
        key_id,
        "--deny-tool",
        "other-tool",
        "--json",
    )
    assert dispatch_keys(only_tool) == 0
    merged = json.loads(capsys.readouterr().out)
    assert merged["scopes"]["namespaces"] == ["web2"]
    assert merged["scopes"]["tools"] == {"other-tool": False}


def test_update_refuses_a_revoked_key(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]
    revoke = _parse("keys", "--db", database, "revoke", key_id)
    assert dispatch_keys(revoke) == 0
    capsys.readouterr()

    update = _parse("keys", "--db", database, "update", key_id, "--label", "again")

    assert dispatch_keys(update) == 2
    assert "revoked" in capsys.readouterr().err


def test_update_refuses_an_empty_change_set(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]

    update = _parse("keys", "--db", database, "update", key_id)

    assert dispatch_keys(update) == 2
    assert "Nothing to update" in capsys.readouterr().err


def test_update_reports_an_unknown_key(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    parsed = _parse("keys", "--db", database, "update", "key_missing", "--label", "x")

    assert dispatch_keys(parsed) == 1
    assert "Key not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Usage visibility (#249)
# ---------------------------------------------------------------------------


def test_list_with_usage_reports_counts(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]

    with ApiKeyStore(database) as store:
        store.record_usage(key_id, "ok")
        store.record_usage(key_id, "error")
        store.record_usage(key_id, "denied")

    listing = _parse("keys", "--db", database, "list", "--usage", "--json")
    assert dispatch_keys(listing) == 0
    [record] = json.loads(capsys.readouterr().out)

    assert record["usage"]["calls"] == 3
    assert record["usage"]["ok"] == 1
    assert record["usage"]["error"] == 1
    assert record["usage"]["denied"] == 1
    assert record["usage"]["last_success_at"]
    assert "secret" not in json.dumps(record)


def test_list_without_usage_does_not_add_it(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    capsys.readouterr()

    listing = _parse("keys", "--db", database, "list", "--json")
    assert dispatch_keys(listing) == 0
    [record] = json.loads(capsys.readouterr().out)

    assert "usage" not in record


def test_get_always_reports_usage(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]
    with ApiKeyStore(database) as store:
        store.record_usage(key_id, "busy")

    get = _parse("keys", "--db", database, "get", key_id, "--json")
    assert dispatch_keys(get) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["usage"]["calls"] == 1
    assert payload["usage"]["busy"] == 1


def test_get_text_shows_the_usage_line(tmp_path, capsys):
    database = str(tmp_path / "keys.db")
    create = _parse("keys", "--db", database, "create", "--label", "bot", "--json")
    assert dispatch_keys(create) == 0
    key_id = json.loads(capsys.readouterr().out)["id"]
    with ApiKeyStore(database) as store:
        store.record_usage(key_id, "ok")

    get = _parse("keys", "--db", database, "get", key_id)
    assert dispatch_keys(get) == 0
    output = capsys.readouterr().out
    assert "calls:       1 (ok=1 error=0 busy=0 denied=0)" in output
    assert "last ok:" in output
