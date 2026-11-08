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
