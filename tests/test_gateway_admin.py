"""Tests for the gateway admin extension.

The extension targets the core admin extension contract (``ADMIN_EXTENSION_API``),
which reached core master before the 0.21.0 release.  On a released core that
predates it these tests skip, and the ``test-core-master`` CI job runs them
against core master.

Every test drives a real :class:`AdminServer` with the extension mounted; the
collaborators are fakes shaped like the gateway's key store (#186), frontend
registry (#184) and source manager (#185).
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

pytest.importorskip("toolregistry.admin.extensions")

from toolregistry import ADMIN_EXTENSION_API, AdminServer, ToolRegistry

from toolregistry_server.ext.gateway import REQUIRES_API, GatewayAdminExtension


@dataclasses.dataclass
class FakeKeyRecord:
    """Stand-in for #186's ``ApiKeyRecord``: a dataclass with ``to_dict``."""

    id: str
    label: str
    scopes: dict[str, Any]
    created_at: str = "2026-11-08T00:00:00Z"
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "scopes": dict(self.scopes),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "last_used_at": self.last_used_at,
            # Deliberately present: the extension must strip them.
            "secret_hash": "deadbeef",
            "secret_digest": "cafebabe",
        }


@dataclasses.dataclass
class FakeCreatedKey:
    """Stand-in for #186's ``CreatedKey``: record plus one-time secret."""

    record: FakeKeyRecord
    secret: str


class FakeKeyStore:
    """A key store shaped like ``toolregistry_server.keystore.ApiKeyStore``."""

    def __init__(self) -> None:
        self._records: dict[str, FakeKeyRecord] = {}
        self._created = 0

    def create(
        self,
        label: str,
        scopes: Sequence[str] | Mapping[str, Any] | None = None,
        *,
        expires_at: str | None = None,
    ) -> FakeCreatedKey:
        self._created += 1
        key_id = f"key_{self._created}"
        secret = f"gw_secret_{self._created}"
        if isinstance(scopes, Mapping):
            normalized = {
                "namespaces": list(scopes.get("namespaces", [])),
                "tools": dict(scopes.get("tools", {})),
            }
        else:
            normalized = {"namespaces": list(scopes or []), "tools": {}}
        record = FakeKeyRecord(
            id=key_id,
            label=label,
            scopes=normalized,
            expires_at=expires_at,
        )
        self._records[key_id] = record
        return FakeCreatedKey(record=record, secret=secret)

    def list(self) -> Sequence[FakeKeyRecord]:
        return list(self._records.values())

    def revoke(self, key_id: str) -> FakeKeyRecord:
        record = self._records.get(key_id)
        if record is None:
            raise KeyError(key_id)
        record.revoked_at = "2026-11-08T01:00:00Z"
        return record


class FakeFrontends:
    """The #184 frontend surface: list and update, with persistence fields."""

    def __init__(self) -> None:
        self._records = {
            "openapi": {
                "name": "openapi",
                "path": "/openapi",
                "enabled": True,
                "transport": "openapi",
            },
            "mcp": {
                "name": "mcp",
                "path": "/mcp",
                "enabled": True,
                "transport": "streamable-http",
            },
        }

    def list_frontends(self) -> Sequence[Mapping[str, Any]]:
        return [dict(record) for record in self._records.values()]

    def update_frontend(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        path: str | None = None,
    ) -> Mapping[str, Any]:
        if name not in self._records:
            raise ValueError(f"unknown frontend {name!r}")
        record = dict(self._records[name])
        if enabled is not None:
            record["enabled"] = enabled
        if path is not None:
            record["path"] = path
        self._records[name] = record
        return {**record, "persisted": True, "warning": None}


@dataclasses.dataclass
class FakeEntry:
    """Stand-in for #185's SourceEntry."""

    source_id: str
    payload: Mapping[str, Any]
    status: str = "applied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.source_id,
            "kind": self.payload.get("type"),
            "detail": self.payload.get("url") or self.payload.get("class"),
            "namespace": self.payload.get("namespace"),
            "enabled": True,
            "status": self.status,
            "last_error": None,
            "names": ["echo"],
            "tool_count": {"enabled": 1, "total": 1},
        }


@dataclasses.dataclass
class FakeMutation:
    """Stand-in for #185's MutationResult."""

    entry: FakeEntry
    counts: dict[str, int]
    persisted: bool = True
    persist_error: str | None = None
    warnings: tuple[str, ...] = ()
    removed: bool = False


class FakeRefresh:
    """Stand-in for core's ``RefreshResult``."""

    added: tuple[str, ...] = ("echo",)
    removed: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    unchanged: int = 0
    skipped: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.updated)


class FakeSources:
    """The #185 source surface, with add/remove/replace/retry."""

    def __init__(self) -> None:
        self._entries: dict[str, FakeEntry] = {}
        self._next = 0

    def to_dicts(self) -> Sequence[Mapping[str, Any]]:
        return [entry.to_dict() for entry in self._entries.values()]

    def add(self, source: Any) -> FakeMutation:
        self._next += 1
        entry = FakeEntry(f"s{self._next}", dict(source))
        self._entries[entry.source_id] = entry
        return FakeMutation(entry, {"registered": 1})

    def remove(self, ref: str) -> FakeMutation:
        entry = self._entries.pop(ref)
        return FakeMutation(entry, {"unregistered": 1}, removed=True)

    def replace(self, ref: str, source: Any) -> FakeMutation:
        previous = self._entries[ref]
        entry = FakeEntry(ref, dict(source))
        self._entries[ref] = entry
        return FakeMutation(
            entry,
            {"registered": 1, "unregistered": 1},
            warnings=(f"replaced {previous.status}",),
        )

    def retry(self, ref: str) -> FakeEntry:
        entry = self._entries[ref]
        entry.status = "applied"
        return entry

    def refresh(self, ref: str) -> FakeRefresh:
        self._entries[ref]  # KeyError for an unknown source
        return FakeRefresh()


def _request(
    url: str,
    method: str = "GET",
    data: dict | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


@pytest.fixture()
def host():
    """A running panel with the extension and every collaborator wired."""
    extension = GatewayAdminExtension(
        key_store=FakeKeyStore(),
        frontends=FakeFrontends(),
        sources=FakeSources(),
        parse_source=lambda payload: payload,
    )
    admin = AdminServer(ToolRegistry(), port=0, extensions=[extension])
    info = admin.start()
    try:
        yield info
    finally:
        admin.stop()


class TestManifest:
    def test_manifest_advertises_the_extension(self, host) -> None:
        status, body, _ = _request(f"{host.url}/api/extensions")
        assert status == 200
        payload = json.loads(body)
        assert payload["api_version"] == ADMIN_EXTENSION_API
        entry = payload["extensions"][0]
        assert entry["name"] == "gateway"
        assert entry["title"] == {"en": "Gateway", "zh": "网关"}
        assert entry["api_prefix"] == "/ext/gateway"
        assert entry["nav"] == {"group": "manage", "order": 20}
        assert entry["entry"] == "assets/ext/gateway/main.js"
        assert entry["styles"] == ["assets/ext/gateway/gateway.css"]

    def test_requires_api_matches_the_contract(self) -> None:
        assert REQUIRES_API == ADMIN_EXTENSION_API


class TestAssets:
    def test_module_and_stylesheet_are_served(self, host) -> None:
        status, body, headers = _request(f"{host.url}/assets/ext/gateway/main.js")
        assert status == 200
        assert "javascript" in headers.get("Content-Type", "")
        assert b"export async function register" in body

        status, body, headers = _request(f"{host.url}/assets/ext/gateway/gateway.css")
        assert status == 200
        assert "text/css" in headers.get("Content-Type", "")
        assert b".ext-container" in body


class TestKeys:
    def test_create_shows_the_secret_once(self, host) -> None:
        status, body, headers = _request(
            f"{host.url}/ext/gateway/keys",
            method="POST",
            data={"label": "ci", "scopes": ["default"]},
        )
        assert status == 201
        assert headers.get("Cache-Control") == "no-store"
        created = json.loads(body)
        assert created["secret"] == "gw_secret_1"
        assert created["label"] == "ci"
        assert "secret_hash" not in created
        assert "secret_digest" not in created

        status, body, _ = _request(f"{host.url}/ext/gateway/keys")
        assert status == 200
        keys = json.loads(body)["keys"]
        assert len(keys) == 1
        assert "secret" not in keys[0]
        assert "secret_hash" not in keys[0]
        assert "secret_digest" not in keys[0]

    def test_create_accepts_tool_overrides(self, host) -> None:
        status, body, _ = _request(
            f"{host.url}/ext/gateway/keys",
            method="POST",
            data={
                "label": "scoped",
                "scopes": {
                    "namespaces": ["web"],
                    "tools": {"web-dangerous": False, "calculator-add": True},
                },
            },
        )
        assert status == 201
        scopes = json.loads(body)["scopes"]
        assert scopes["namespaces"] == ["web"]
        assert scopes["tools"] == {"web-dangerous": False, "calculator-add": True}

    def test_create_accepts_the_list_shape(self, host) -> None:
        status, body, _ = _request(
            f"{host.url}/ext/gateway/keys",
            method="POST",
            data={"label": "simple", "scopes": ["default", "files"]},
        )
        assert status == 201
        assert json.loads(body)["scopes"]["namespaces"] == ["default", "files"]

    def test_null_scopes_create_a_key_that_can_call_nothing(self, host) -> None:
        status, body, _ = _request(
            f"{host.url}/ext/gateway/keys",
            method="POST",
            data={"label": "empty", "scopes": None},
        )
        assert status == 201
        assert json.loads(body)["scopes"] == {"namespaces": [], "tools": {}}

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"label": ""},
            {"label": "x", "scopes": "default"},
            {"label": "x", "scopes": [1]},
            {"label": "x", "scopes": {"namespaces": "default"}},
            {"label": "x", "scopes": {"tools": {"web": "yes"}}},
            {"label": "x", "scopes": {"namespaces": [], "extra": []}},
            {"label": "x", "expires_at": 5},
        ],
    )
    def test_invalid_create_payloads_are_refused(self, host, payload) -> None:
        status, body, _ = _request(
            f"{host.url}/ext/gateway/keys", method="POST", data=payload
        )
        assert status == 400
        assert json.loads(body)["error"] == "Bad Request"

    def test_revoke_marks_the_key_and_404s_afterwards(self, host) -> None:
        status, body, _ = _request(
            f"{host.url}/ext/gateway/keys",
            method="POST",
            data={"label": "ci", "scopes": ["default"]},
        )
        assert status == 201
        key_id = json.loads(body)["id"]

        status, body, _ = _request(
            f"{host.url}/ext/gateway/keys/{key_id}/revoke", method="POST"
        )
        assert status == 200
        revoked = json.loads(body)["key"]
        assert revoked["revoked_at"]
        assert "secret_digest" not in revoked

        status, _, _ = _request(
            f"{host.url}/ext/gateway/keys/missing/revoke", method="POST"
        )
        assert status == 404


class TestFrontends:
    def test_list_and_update(self, host) -> None:
        status, body, _ = _request(f"{host.url}/ext/gateway/frontends")
        assert status == 200
        names = [record["name"] for record in json.loads(body)["frontends"]]
        assert names == ["openapi", "mcp"]

        status, body, _ = _request(
            f"{host.url}/ext/gateway/frontends/openapi",
            method="PATCH",
            data={"enabled": False},
        )
        assert status == 200
        frontend = json.loads(body)["frontend"]
        assert frontend["enabled"] is False
        assert frontend["persisted"] is True

    def test_unknown_frontend_is_a_client_error(self, host) -> None:
        status, body, _ = _request(
            f"{host.url}/ext/gateway/frontends/sse",
            method="PATCH",
            data={"enabled": False},
        )
        assert status == 400
        assert "unknown frontend" in json.loads(body)["message"]

    def test_empty_update_is_refused(self, host) -> None:
        status, _, _ = _request(
            f"{host.url}/ext/gateway/frontends/openapi", method="PATCH", data={}
        )
        assert status == 400


class TestSources:
    def test_add_list_replace_retry_remove(self, host) -> None:
        status, body, _ = _request(
            f"{host.url}/ext/gateway/sources",
            method="POST",
            data={"type": "openapi", "url": "https://example.test/spec.json"},
        )
        assert status == 201
        added = json.loads(body)
        source_id = added["source"]["id"]
        assert added["source"]["kind"] == "openapi"
        assert added["persisted"] is True

        status, body, _ = _request(f"{host.url}/ext/gateway/sources")
        assert status == 200
        assert [record["id"] for record in json.loads(body)["sources"]] == [source_id]

        status, body, _ = _request(
            f"{host.url}/ext/gateway/sources/{source_id}",
            method="PUT",
            data={"type": "mcp", "url": "https://example.test/mcp"},
        )
        assert status == 200
        assert json.loads(body)["counts"] == {"registered": 1, "unregistered": 1}

        status, body, _ = _request(
            f"{host.url}/ext/gateway/sources/{source_id}/retry", method="POST"
        )
        assert status == 200
        assert json.loads(body)["source"]["status"] == "applied"

        status, body, _ = _request(
            f"{host.url}/ext/gateway/sources/{source_id}", method="DELETE"
        )
        assert status == 200
        assert json.loads(body)["removed"] is True

    def test_refresh_returns_the_diff(self, host) -> None:
        _, body, _ = _request(
            f"{host.url}/ext/gateway/sources",
            method="POST",
            data={"type": "openapi", "url": "https://example.test/spec.json"},
        )
        source_id = json.loads(body)["source"]["id"]

        status, body, _ = _request(
            f"{host.url}/ext/gateway/sources/{source_id}/refresh", method="POST"
        )

        assert status == 200
        refresh = json.loads(body)["refresh"]
        assert refresh["added"] == ["echo"]
        assert refresh["changed"] is True
        assert refresh["skipped"] is False

    def test_refresh_of_an_unknown_source_is_a_404(self, host) -> None:
        status, _, _ = _request(
            f"{host.url}/ext/gateway/sources/nope/refresh", method="POST"
        )
        assert status == 404

    def test_unknown_source_is_a_404(self, host) -> None:
        status, _, _ = _request(f"{host.url}/ext/gateway/sources/nope", method="DELETE")
        assert status == 404


class TestMissingCollaborators:
    def test_pages_answer_503_without_their_collaborator(self) -> None:
        extension = GatewayAdminExtension()
        admin = AdminServer(ToolRegistry(), port=0, extensions=[extension])
        info = admin.start()
        try:
            for path in ("/keys", "/frontends", "/sources"):
                status, body, _ = _request(f"{info.url}/ext/gateway{path}")
                assert status == 503
                assert "no " in json.loads(body)["message"]
        finally:
            admin.stop()
