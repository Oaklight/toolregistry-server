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
  same ``to_dicts`` / ``add`` / ``remove`` / ``replace`` / ``retry`` /
  ``refresh`` methods.
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
#: defensively from every key record the extension returns.  ``secret_digest``
#: is the column name in the key store schema, so a store bug that names the
#: digest after its column cannot leak it through the panel.
_SECRET_FIELDS = frozenset({"secret", "secret_hash", "secret_digest", "digest", "salt"})


class KeyStore(Protocol):
    """The key lifecycle surface the Keys page needs (#186).

    This is the shape of ``toolregistry_server.keystore.ApiKeyStore``: records
    are objects that serialise through ``to_dict`` (or plain mappings), and
    ``create`` is the only call that returns the secret, inside a
    ``(record, secret)`` pair or a ``CreatedKey``-like object.
    """

    def create(
        self,
        label: str,
        scopes: Sequence[str] | Mapping[str, Any] | None = None,
        *,
        expires_at: str | None = None,
    ) -> Any:
        """Create a key and return its record together with the secret."""

    def list(self) -> Sequence[Any]:
        """Return every key record, secrets excluded."""

    def revoke(self, key_id: str) -> Any:
        """Revoke a key and return its record; raise ``KeyError`` if unknown."""


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

    def refresh(self, ref: str) -> Any:
        """Re-fetch an OpenAPI source's document and apply the diff."""


class _BadRequest(Exception):
    """The request payload is unusable."""


class _Unavailable(Exception):
    """A collaborator the page needs was not injected."""


def _error(status: int, error: str, message: str) -> tuple[dict[str, str], int]:
    """Build the panel's JSON error shape."""
    return {"error": error, "message": message}, status


def _guard(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Map extension exceptions onto the panel's error responses.

    The wrapped callable is a method: the router passes the request as the
    first argument after ``self``, so the wrapper keeps that shape.

    ``ValueError`` from a collaborator is reported as 400: the store and the
    frontend registry raise it for invalid operator input (an unknown
    frontend, an expiry in the past).  A collaborator bug that also raises
    ``ValueError`` is therefore reported as a client error; the alternative,
    a blanket 500, would misreport the common case.
    """

    @functools.wraps(handler)
    def wrapped(self: Any, request: Any, **params: Any) -> Any:
        try:
            return handler(self, request, **params)
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


def _validated_scopes(value: Any) -> Any:
    """Validate the ``scopes`` field of a create request.

    Two shapes are accepted.  A list of namespace patterns keeps the simple
    form; a mapping ``{"namespaces": [...], "tools": {name: bool}}`` adds the
    per-tool overrides the key store already supports, where an override
    always wins over the namespace list.  ``None`` means the same as an empty
    list: a key that can call nothing, matching ``ScopeSpec.coerce(None)``.

    Args:
        value: The decoded JSON value.

    Returns:
        The value to pass to the key store, in the same shape as the input.

    Raises:
        _BadRequest: If the value is neither shape or a member has the wrong
            type.
    """
    if value is None:
        return []
    if isinstance(value, list):
        if not all(isinstance(item, str) and item.strip() for item in value):
            raise _BadRequest("'scopes' must be a list of non-empty namespace strings")
        return [item.strip() for item in value]
    if isinstance(value, Mapping):
        unknown = set(value) - {"namespaces", "tools"}
        if unknown:
            raise _BadRequest(f"'scopes' has unknown keys: {sorted(map(str, unknown))}")
        namespaces = value.get("namespaces", [])
        tools = value.get("tools", {})
        if not isinstance(namespaces, list) or not all(
            isinstance(item, str) and item.strip() for item in namespaces
        ):
            raise _BadRequest("'scopes.namespaces' must be a list of non-empty strings")
        if not isinstance(tools, Mapping) or not all(
            isinstance(name, str) and name.strip() and isinstance(allowed, bool)
            for name, allowed in tools.items()
        ):
            raise _BadRequest(
                "'scopes.tools' must map non-empty tool names to true or false"
            )
        return {
            "namespaces": [item.strip() for item in namespaces],
            "tools": {name.strip(): allowed for name, allowed in tools.items()},
        }
    raise _BadRequest(
        "'scopes' must be a list of namespaces or "
        "an object with 'namespaces' and 'tools'"
    )


def _record_dict(value: Any) -> Any:
    """Serialise a record that may expose ``to_dict``."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _public_key(record: Any) -> dict[str, Any]:
    """Return a key record with any secret-bearing field removed."""
    data = _record_dict(record)
    if not isinstance(data, Mapping):
        raise ValueError("key store returned a record that is not a mapping")
    return {key: value for key, value in data.items() if key not in _SECRET_FIELDS}


def _created_parts(created: Any) -> tuple[str, dict[str, Any]]:
    """Return ``(secret, public record)`` from a store's create result.

    Accepts both shapes the key store may use: a ``(record, secret)`` tuple,
    or an object with ``record`` and ``secret`` attributes (the shape of
    ``ApiKeyStore.create``'s ``CreatedKey``).
    """
    if isinstance(created, tuple) and len(created) == 2:
        first, second = created
        # A tuple may be (secret, record) or (record, secret); the secret is
        # the value that is a string.
        if isinstance(first, str) and not isinstance(second, str):
            secret, record = first, second
        else:
            record, secret = first, second
    else:
        secret = getattr(created, "secret", None)
        record = getattr(created, "record", None)
        if secret is None or record is None:
            raise ValueError("key store create() must return a record and a secret")
    if not isinstance(secret, str):
        raise ValueError("key store returned a non-string secret")
    return secret, _public_key(record)


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


def _refresh_json(result: Any) -> dict[str, Any]:
    """Serialise a refresh result without assuming one concrete type.

    The core result is a frozen dataclass with no ``to_dict``; every field is
    read defensively so a stand-in with a subset of them still serialises.
    """
    return {
        "source": getattr(result, "source", ""),
        "source_detail": getattr(result, "source_detail", ""),
        "added": list(getattr(result, "added", ()) or ()),
        "removed": list(getattr(result, "removed", ()) or ()),
        "updated": list(getattr(result, "updated", ()) or ()),
        "breaking": list(getattr(result, "breaking", ()) or ()),
        "compatible": list(getattr(result, "compatible", ()) or ()),
        "unchanged": int(getattr(result, "unchanged", 0) or 0),
        "skipped": bool(getattr(result, "skipped", False)),
        "overridden": list(getattr(result, "overridden", ()) or ()),
        "changed": bool(getattr(result, "changed", False)),
        "base_url_changed": bool(getattr(result, "base_url_changed", False)),
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
        ctx.post("/sources/<source_id>/refresh", self.refresh_source)

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
        scopes = _validated_scopes(data.get("scopes", []))
        expires_at = data.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, str):
            raise _BadRequest("'expires_at' must be an ISO 8601 string or null")

        secret, record = _created_parts(
            store.create(label.strip(), scopes, expires_at=expires_at)
        )
        # A secret in a response body must not be stored by a cache.
        return ({**record, "secret": secret}, 201, {"Cache-Control": "no-store"})

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

    @_guard
    def refresh_source(self, request: Any, source_id: str) -> dict[str, Any]:
        """Re-fetch an OpenAPI source's document and apply the diff.

        A ``304`` is reported as ``skipped``; the source's tools and their
        ``last_refreshed_at`` are left alone.  A refused or failed fetch is
        reported as 400 and keeps the current tools.
        """
        sources = self._require(self._sources, "source manager")
        return {"refresh": _refresh_json(sources.refresh(source_id))}


__all__ = ["GATEWAY_EXTENSION_NAME", "GatewayAdminExtension", "REQUIRES_API"]
