"""Gateway admin extension.

Implements the pages described in
[#187](https://github.com/Oaklight/toolregistry-server/issues/187): client
keys, frontend switches and runtime tool sources.  The extension is a thin
HTTP surface over the Python objects the gateway process owns; it never
talks to another HTTP endpoint and it adds no authentication of its own.

The host (``toolregistry`` core) mounts it through the admin extension
contract v1 (``ADMIN_EXTENSION_API``): routes under ``/ext/gateway``, assets
under ``assets/ext/gateway/`` and one operator auth level.  The shell loads
``main.js`` and calls ``register({container, api, i18n})``.

Collaborators are injected so that the gateway supplies its live objects and
tests can supply fakes:

* ``key_store`` — inbound client keys (#186).
* ``frontends`` — the frontend registry (#184), or any object with the same
  ``list_frontends`` / ``update_frontend`` methods.
* ``sources`` — the runtime source manager (#185), or any object with the
  same ``to_dicts`` / ``add`` / ``remove`` / ``replace`` / ``retry`` methods.
* ``parse_source`` — converts a JSON declaration into the declaration type
  ``sources.add`` accepts.  Core owns the config schema, so the gateway
  supplies this rather than the extension guessing it.

A missing collaborator answers 503 for its pages and leaves the others
working.

Example:
    >>> extension = GatewayAdminExtension(
    ...     key_store=store,
    ...     frontends=gateway_adapter,
    ...     sources=source_manager,
    ...     parse_source=config_source_from_payload,
    ... )
    >>> AdminServer(registry, extensions=[extension])
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

#: Extension name: the URL segment and the asset directory key.
GATEWAY_EXTENSION_NAME = "gateway"

#: Contract version this extension targets.  Kept as a literal so the module
#: imports on a core release that predates the contract; the host rejects a
#: mismatch at startup, and a test pins this to ``ADMIN_EXTENSION_API``.
REQUIRES_API = 1

_ASSETS_DIR = Path(__file__).with_name("assets")

#: Fields a key store must never expose after creation.  They are stripped
#: defensively from every key record the extension returns.
_SECRET_FIELDS = frozenset({"secret", "secret_hash", "digest", "salt"})


class KeyStore(Protocol):
    """The key lifecycle surface the Keys page needs (#186).

    Records are JSON-ready mappings without the secret; ``create`` is the
    only call that returns one.
    """

    def create(
        self, label: str, scopes: Sequence[str], expires_at: str | None = None
    ) -> tuple[str, Mapping[str, Any]]:
        """Create a key and return ``(secret, record)``."""

    def list(self) -> Sequence[Mapping[str, Any]]:
        """Return every key record, secrets excluded."""

    def revoke(self, key_id: str) -> Mapping[str, Any] | None:
        """Revoke a key and return its record, or ``None`` when unknown."""


class FrontendControl(Protocol):
    """The frontend surface the Frontends page needs (#184)."""

    def list_frontends(self) -> Sequence[Mapping[str, Any]]:
        """Return ``name``, ``path``, ``enabled`` and ``transport`` records."""

    def update_frontend(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        path: str | None = None,
    ) -> Mapping[str, Any]:
        """Change a frontend and return the record with persistence fields."""


class SourceControl(Protocol):
    """The runtime source surface the Sources page needs (#185)."""

    def to_dicts(self) -> Sequence[Mapping[str, Any]]:
        """Return one status record per declared source."""

    def add(self, source: Any) -> Any:
        """Add a declaration and return its mutation result."""

    def remove(self, ref: str) -> Any:
        """Remove a declaration by its opaque id."""

    def replace(self, ref: str, source: Any) -> Any:
        """Replace a declaration by its opaque id."""

    def retry(self, ref: str) -> Any:
        """Re-apply a failed declaration."""


class _BadRequest(Exception):
    """The request payload is unusable."""


class _Unavailable(Exception):
    """A collaborator the page needs was not injected."""


def _error(status: int, error: str, message: str) -> tuple[dict[str, str], int]:
    """Build the panel's JSON error shape."""
    return {"error": error, "message": message}, status


def _guard(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Map extension exceptions onto the panel's error responses."""

    @functools.wraps(handler)
    def wrapped(request: Any, **params: Any) -> Any:
        try:
            return handler(request, **params)
        except _BadRequest as exc:
            return _error(400, "Bad Request", str(exc))
        except _Unavailable as exc:
            return _error(503, "Service Unavailable", str(exc))
        except KeyError as exc:
            detail = exc.args[0] if exc.args else "not found"
            return _error(404, "Not Found", str(detail))
        except ValueError as exc:
            return _error(400, "Bad Request", str(exc))

    return wrapped


def _json_body(request: Any) -> dict[str, Any]:
    """Parse a request body that must be a JSON object."""
    if not request.body:
        raise _BadRequest("Request body is required")
    try:
        data = request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise _BadRequest(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _BadRequest("Body must be a JSON object")
    return data


def _public_key(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a key record with any secret-bearing field removed."""
    if not isinstance(record, Mapping):
        raise ValueError("key store returned a record that is not a mapping")
    return {key: value for key, value in record.items() if key not in _SECRET_FIELDS}


def _record_dict(value: Any) -> Any:
    """Serialise a record that may expose ``to_dict``."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _mutation_json(result: Any) -> dict[str, Any]:
    """Serialise a source mutation result without assuming one concrete type."""
    return {
        "source": _record_dict(getattr(result, "entry", None)),
        "counts": dict(getattr(result, "counts", {}) or {}),
        "persisted": bool(getattr(result, "persisted", False)),
        "persist_error": getattr(result, "persist_error", None),
        "backup_path": getattr(result, "backup_path", None),
        "warnings": list(getattr(result, "warnings", ()) or ()),
        "removed": bool(getattr(result, "removed", False)),
    }


class GatewayAdminExtension:
    """Admin extension for keys, frontends and runtime sources.

    Args:
        key_store: Inbound key lifecycle (#186), or ``None`` to answer 503
            for the Keys page.
        frontends: Frontend registry (#184), or ``None`` for the Frontends
            page.
        sources: Runtime source manager (#185), or ``None`` for the Sources
            page.
        parse_source: Callable turning a JSON declaration into the type
            ``sources.add`` accepts.  Required together with ``sources``.
    """

    name = GATEWAY_EXTENSION_NAME
    title = {"en": "Gateway", "zh": "网关"}
    requires_api = REQUIRES_API
    entry = f"assets/ext/{GATEWAY_EXTENSION_NAME}/main.js"
    styles = (f"assets/ext/{GATEWAY_EXTENSION_NAME}/gateway.css",)
    assets = _ASSETS_DIR
    nav = {"group": "manage", "order": 20}
    icon = (
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' "
        "stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
        "<path d='M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z'/>"
        "<path d='M14 2v5h6'/></svg>"
    )

    def __init__(
        self,
        *,
        key_store: KeyStore | None = None,
        frontends: FrontendControl | None = None,
        sources: SourceControl | None = None,
        parse_source: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self._key_store = key_store
        self._frontends = frontends
        self._sources = sources
        self._parse_source = parse_source

    def register(self, ctx: Any) -> None:
        """Register the extension routes on *ctx*.

        Args:
            ctx: The host's ``AdminContext`` for this extension.
        """
        ctx.get("/keys", self.list_keys)
        ctx.post("/keys", self.create_key)
        ctx.post("/keys/<key_id>/revoke", self.revoke_key)
        ctx.get("/frontends", self.list_frontends)
        ctx.patch("/frontends/<name>", self.update_frontend)
        ctx.get("/sources", self.list_sources)
        ctx.post("/sources", self.add_source)
        ctx.put("/sources/<source_id>", self.replace_source)
        ctx.delete("/sources/<source_id>", self.remove_source)
        ctx.post("/sources/<source_id>/retry", self.retry_source)

    # ---- collaborators ---------------------------------------------------

    def _require(self, collaborator: Any, label: str) -> Any:
        if collaborator is None:
            raise _Unavailable(
                f"the gateway extension has no {label}; "
                "start the server in gateway mode with this page wired"
            )
        return collaborator

    # ---- keys ------------------------------------------------------------

    @_guard
    def list_keys(self, request: Any) -> dict[str, Any]:
        """Return every key record, never a secret."""
        store = self._require(self._key_store, "key store")
        return {"keys": [_public_key(record) for record in store.list()]}

    @_guard
    def create_key(self, request: Any) -> Any:
        """Create a key and return its secret exactly once."""
        store = self._require(self._key_store, "key store")
        data = _json_body(request)
        label = data.get("label")
        if not isinstance(label, str) or not label.strip():
            raise _BadRequest("'label' must be a non-empty string")
        scopes = data.get("scopes", [])
        if not isinstance(scopes, list) or not all(
            isinstance(item, str) for item in scopes
        ):
            raise _BadRequest("'scopes' must be a list of strings")
        expires_at = data.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, str):
            raise _BadRequest("'expires_at' must be an ISO 8601 string or null")

        secret, record = store.create(label.strip(), scopes, expires_at)
        # A secret in a response body must not be stored by a cache.
        return (
            {**_public_key(record), "secret": secret},
            201,
            {"Cache-Control": "no-store"},
        )

    @_guard
    def revoke_key(self, request: Any, key_id: str) -> Any:
        """Revoke a key; the next request that presents it is refused."""
        store = self._require(self._key_store, "key store")
        record = store.revoke(key_id)
        if record is None:
            raise KeyError(f"key {key_id!r} not found")
        return {"key": _public_key(record)}

    # ---- frontends -------------------------------------------------------

    @_guard
    def list_frontends(self, request: Any) -> dict[str, Any]:
        """Return every frontend with its path, enable flag and transport."""
        frontends = self._require(self._frontends, "frontend registry")
        return {"frontends": [dict(record) for record in frontends.list_frontends()]}

    @_guard
    def update_frontend(self, request: Any, name: str) -> dict[str, Any]:
        """Change a frontend's enable flag, path or both."""
        frontends = self._require(self._frontends, "frontend registry")
        data = _json_body(request)
        enabled = data.get("enabled")
        path = data.get("path")
        if enabled is None and path is None:
            raise _BadRequest("'enabled' or 'path' is required")
        if enabled is not None and not isinstance(enabled, bool):
            raise _BadRequest("'enabled' must be a boolean")
        if path is not None and (not isinstance(path, str) or not path):
            raise _BadRequest("'path' must be a non-empty string")
        record = frontends.update_frontend(name, enabled=enabled, path=path)
        return {"frontend": dict(record)}

    # ---- sources ---------------------------------------------------------

    @_guard
    def list_sources(self, request: Any) -> dict[str, Any]:
        """Return the source manager's status view."""
        sources = self._require(self._sources, "source manager")
        return {"sources": [dict(record) for record in sources.to_dicts()]}

    def _declaration(self, request: Any) -> Any:
        sources = self._require(self._sources, "source manager")
        parse_source = self._require(self._parse_source, "source declaration parser")
        return sources, parse_source(_json_body(request))

    @_guard
    def add_source(self, request: Any) -> Any:
        """Add a source declaration and persist it."""
        sources, source = self._declaration(request)
        return _mutation_json(sources.add(source)), 201

    @_guard
    def replace_source(self, request: Any, source_id: str) -> dict[str, Any]:
        """Replace a source declaration by id."""
        sources, source = self._declaration(request)
        return _mutation_json(sources.replace(source_id, source))

    @_guard
    def remove_source(self, request: Any, source_id: str) -> dict[str, Any]:
        """Remove a source declaration and unregister its tools."""
        sources = self._require(self._sources, "source manager")
        return _mutation_json(sources.remove(source_id))

    @_guard
    def retry_source(self, request: Any, source_id: str) -> dict[str, Any]:
        """Re-apply a source whose last attempt failed."""
        sources = self._require(self._sources, "source manager")
        return {"source": _record_dict(sources.retry(source_id))}


__all__ = ["GATEWAY_EXTENSION_NAME", "GatewayAdminExtension", "REQUIRES_API"]
