"""Runtime tool-source management for the server.

Sources are normally read once, when the process starts.  :class:`SourceManager`
owns the live source list instead: it can add, remove and replace a source and
change the served tool set without a restart, records the result of each apply
attempt, and writes an API-originated change back to the configuration file so
a restart reproduces it.

Diff semantics match the Hub's historical hot-reload implementation:

- a source added to the config is registered;
- a source removed from the config is unregistered;
- a source that is still listed but now excluded (``enabled: false`` or
  filtered out by ``mode``/``disabled``/``enabled``) is disabled, and its
  tools are re-enabled when the source enters scope again;
- a source whose declaration changed is unregistered and registered again;
- reordering the ``tools`` list is not a change.

An OpenAPI source can also be refreshed without changing its declaration:
:meth:`SourceManager.refresh` asks the core integration to re-read the
document, which sends the stored ETag and diffs the result against the live
tools.  A ``304`` changes nothing; a failed fetch keeps the current tools.

A source whose registration raises is recorded as ``failed`` with its error and
retried on the next diff.  A source that registered successfully but produced
no tools stays ``applied`` and is not registered again on every unrelated
change.  A partial registration is rolled back, so a failed source never leaves
tools that the diff cannot see.

Concurrency: one lock serialises every mutation and every reload.  When a
:class:`~toolregistry_server.route_table.RouteTable` is attached, the manager
wraps each diff in :meth:`RouteTable.batch_changes`, so one diff produces one
route rebuild instead of one per registry event.  The registry itself is
mutated tool by tool; a request in flight during a diff can observe a subset of
the new tools, which is true for any registration and is not a regression.

Persistence is never silent: an API-originated mutation rewrites the config
file after writing ``<path>.bak``, returns whether the write succeeded, and
warns that comments are not preserved.  A watcher-originated reload never
writes.  ``write_config=False`` keeps every change in memory only.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from toolregistry.config import (
    MCPSource,
    OpenAPISource,
    PythonSource,
    ToolConfig,
    load_config,
)

from ._vendor.structlog import get_logger
from .regex_guard import SpecPatternError
from .registry_builder import (
    PROFILE_DISABLE_REASON,
    PROFILE_DISABLE_TAGS,
    _apply_ns_tags,
    _build_ns_tags,
    _invalid_patterns,
    _should_load_source,
    openapi_spec_url,
    register_mcp_source,
    register_openapi_source,
    register_python_source,
    sync_parameter_overrides,
)

if TYPE_CHECKING:
    from toolregistry import ToolRegistry
    from toolregistry.config import ToolSource
    from toolregistry.events import RefreshResult

    from .oauth.upstream.provider import OAuthClientError
    from .route_table import RouteTable

logger = get_logger()

#: Reason recorded on tools disabled because their source left the scope of
#: the config.  Only tools carrying this reason are re-enabled by a later
#: diff; tools disabled for another reason (missing API key, admin panel) stay
#: off.
CONFIG_DISABLE_REASON = "Disabled by config (runtime source change)"

#: Returned in every mutation result whose config was rewritten.  Comments
#: are not part of the parsed model in either supported format (JSONC or
#: YAML), so a rewrite drops them.
COMMENTS_WARNING = "the config file was rewritten; comments are not preserved"

#: Top-level keys the core ``ToolConfig`` owns.  A rewrite replaces these and
#: leaves every other section (``gateway``, ``oauth``) as it
#: was, so a source edit cannot erase a server-owned setting.  The list is
#: taken from the installed dataclass: a key this server knows but the
#: installed core does not (``tool_parameters`` before core 0.21) must not be
#: dropped from a file the core could not have produced.  ``source`` is the
#: parsed file path, not a schema key, so it is excluded.
_CORE_CONFIG_KEYS = frozenset(
    field.name for field in dataclasses.fields(ToolConfig) if field.name != "source"
)

#: HTTP statuses with which an MCP server rejects the credential itself.
_AUTH_REJECTION_STATUSES = frozenset({401, 403})

#: A source is identified by its declaration, not by its position, so
#: reordering the ``tools`` list is not treated as a change.  Fields that live
#: in unhashable containers (``kwargs``, ``env``, ``auth``) are not part of
#: the key; a change to them is still detected by comparing the full source
#: objects once the keys match.
SourceKey = tuple[Any, ...]

#: A source may be addressed by its opaque id (the ``id`` in
#: :meth:`SourceEntry.to_dict`) or by its internal key.  Callers outside this
#: module should use the id: the key is an internal identity and must not be
#: published.
SourceRef = SourceKey | str


def source_key(source: ToolSource) -> SourceKey:
    """Return the stable identity of a tool source declaration."""
    return (
        type(source).__name__,
        getattr(source, "namespace", None),
        getattr(source, "class_path", None),
        getattr(source, "module_path", None),
        getattr(source, "url", None),
        getattr(source, "command", None),
    )


def index_sources(sources: Iterable[ToolSource]) -> dict[SourceKey, ToolSource]:
    """Map each source declaration to its key.

    Two sources may share a key (for example, the same OpenAPI URL twice with
    different auth).  The occurrence index is appended so both survive
    indexing; the order inside the file decides which is which.
    """
    seen: dict[SourceKey, int] = {}
    indexed: dict[SourceKey, ToolSource] = {}
    for source in sources:
        base = source_key(source)
        occurrence = seen.get(base, 0)
        seen[base] = occurrence + 1
        key: SourceKey = base if occurrence == 0 else (*base, occurrence)
        indexed[key] = source
    return indexed


class SourceStatus(str, Enum):
    """Result of the last apply attempt for one source.

    ``APPLIED`` means registration succeeded, even when it produced no tool
    names.  ``EXCLUDED`` means the source was out of scope.  ``FAILED`` means
    registration raised and the source must be retried on the next diff.
    """

    APPLIED = "applied"
    EXCLUDED = "excluded"
    FAILED = "failed"


#: ``scheme://user:password@`` in a URL, inside arbitrary text.
_URL_USERINFO = re.compile(r"\b([A-Za-z][A-Za-z0-9+.\-]*://)[^/\s]+@")


def _redact_url_userinfo(text: str) -> str:
    """Remove ``user:password@`` userinfo from a URL in *text*."""
    return _URL_USERINFO.sub(r"\1", text)


def _safe_error(exc: BaseException) -> str:
    """Return an exception message safe to show in a status payload."""
    return _redact_url_userinfo(str(exc))


def _auth_rejection_status(exc: BaseException) -> int | None:
    """Return the 401/403 an MCP client raised, if that is what it was.

    The MCP client reports a resource-server rejection as an ``httpx``
    ``HTTPStatusError``, sometimes wrapped in an ``ExceptionGroup`` because it
    is raised inside a task group.  The whole cause chain and every group
    member are searched; no other failure carries a credential verdict.
    """
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        status = _status_of(current)
        if status is not None and status in _AUTH_REJECTION_STATUSES:
            return status
        pending.extend(getattr(current, "exceptions", ()) or ())
        pending.append(current.__cause__)
        pending.append(current.__context__)
    return None


def _status_of(exc: BaseException) -> int | None:
    """Return the HTTP status an exception carries, if any.

    ``httpx.HTTPStatusError`` keeps it on ``response``, an
    ``http.client``-style error puts it on ``code``, and
    ``http.HTTPStatus`` exposes ``status_code`` directly.
    """
    response = getattr(exc, "response", None)
    for value in (
        getattr(exc, "status_code", None),
        getattr(response, "status_code", None),
        getattr(exc, "code", None),
    ):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _describe_source(source: ToolSource) -> str:
    """Return a log-safe description of *source*.

    MCP sources keep credentials in ``env``/``auth``, a URL can carry
    userinfo, and stdio arguments can contain tokens.  Only the identifying,
    non-secret part is returned.
    """
    url = getattr(source, "url", None)
    command = getattr(source, "command", None)
    return (
        getattr(source, "class_path", None)
        or getattr(source, "module_path", None)
        or (_redact_url_userinfo(url) if url else None)
        or (f"{command[0]} ..." if command else None)
        or type(source).__name__
    )


def _source_kind(source: ToolSource) -> str:
    """Return a short, stable kind for *source* (``python``/``mcp``/``openapi``)."""
    name = type(source).__name__
    if name.endswith("Source"):
        name = name[: -len("Source")]
    return name.lower()


def _normalized_detail(source: ToolSource) -> str:
    """Return the redacted, normalized identifying detail of *source*."""
    detail = _describe_source(source).strip()
    if "://" in detail:
        detail = detail.rstrip("/")
    return detail


def _declaration_digest(source: ToolSource) -> str:
    """Return a canonical digest of the whole declaration.

    ``to_dict()`` is the config-file form, so it carries the fields that can
    distinguish two declarations of the same target: ``headers``, ``env``,
    ``command``, ``auth``, filters and kwargs.  It is hashed, never exposed;
    the digest only feeds :func:`_entry_id`.
    """
    import json

    canonical = json.dumps(
        source.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entry_id(source: ToolSource) -> str:
    """Return a deterministic, opaque id for a source declaration.

    The id is ``src_<16 hex>`` over the kind, the redacted detail, the
    namespace and a digest of the full declaration.  Folding the declaration
    in keeps two sources with the same target and different credentials (the
    ambiguous case for remove/replace) distinct.  The id does not depend on
    the declaration's position, so it survives restarts and reordering, and
    nothing in it is reversible: the declaration itself is never emitted.
    """
    material = "\x1f".join(
        (
            _source_kind(source),
            _normalized_detail(source),
            getattr(source, "namespace", None) or "",
            _declaration_digest(source),
        )
    )
    return "src_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SourceEntry:
    """The last known state of one source declaration.

    Attributes:
        key: Stable identity of the declaration (see :func:`source_key`).
        source: The declaration itself.
        names: Tool names the declaration produced.
        status: Result of the last apply attempt.
        error: Redacted error text when *status* is ``FAILED``.
        reauth_required: ``True`` when the upstream rejected the credential:
            a definitive token-endpoint rejection, or an HTTP 401/403 from
            the MCP server itself.  A retry will not help until the operator
            authorizes the source again.
    """

    key: SourceKey
    source: ToolSource
    names: frozenset[str]
    status: SourceStatus
    error: str | None = None
    reauth_required: bool = False

    def to_dict(self, enabled_names: Iterable[str] | None = None) -> dict[str, Any]:
        """Return a JSON-ready view for an admin/status API.

        Secrets are excluded: the declaration itself is not serialised, and
        the detail passes through :func:`_describe_source`.

        Args:
            enabled_names: Tool names that are currently enabled.  Defaults
                to every name when the caller has no registry at hand.
        """
        enabled = set(enabled_names) if enabled_names is not None else set(self.names)
        return {
            "id": _entry_id(self.source),
            "kind": _source_kind(self.source),
            "detail": _normalized_detail(self.source),
            "namespace": getattr(self.source, "namespace", None),
            "enabled": bool(getattr(self.source, "enabled", True)),
            "status": self.status.value,
            "last_error": self.error,
            "reauth_required": self.reauth_required,
            "names": sorted(self.names),
            "tool_count": {
                "enabled": len(enabled & self.names),
                "total": len(self.names),
            },
        }


@dataclass(frozen=True)
class MutationResult:
    """Outcome of an add/remove/replace call.

    Attributes:
        entry: The affected source's state after the mutation.  For
            :meth:`SourceManager.remove` this is the declaration that left.
        counts: Tool-level counters for the diff: ``registered``,
            ``unregistered``, ``enabled`` and ``disabled``.
        persisted: Whether the config file was rewritten.
        persist_error: Why the write did not happen, when it did not.
        backup_path: Path of the ``<config>.bak`` copy taken before a
            rewrite, when one was taken.
        warnings: Non-fatal notes for the caller, e.g. comment loss.
        removed: ``True`` only for :meth:`SourceManager.remove`; the entry's
            status describes the last apply attempt, not the removal.
    """

    entry: SourceEntry
    counts: dict[str, int]
    persisted: bool
    persist_error: str | None = None
    backup_path: str | None = None
    warnings: tuple[str, ...] = ()
    removed: bool = False


def _file_hash(path: Path) -> str | None:
    """Return the SHA-256 of *path*, or ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _remove_names(
    registry: ToolRegistry, names: Iterable[str], counts: dict[str, int]
) -> None:
    """Unregister *names*, counting the ones that were actually present."""
    for name in names:
        if registry._unregister(name):
            counts["unregistered"] += 1


def _set_scope(
    registry: ToolRegistry,
    names: Iterable[str],
    included: bool,
    counts: dict[str, int],
) -> None:
    """Enable or disable *names* for a source whose declaration is unchanged.

    Only tools disabled by this module are re-enabled, so a tool turned off
    for a missing API key or from the admin panel stays off.
    """
    for name in names:
        if included:
            if registry.get_disable_reason(name) == CONFIG_DISABLE_REASON:
                registry.enable(name)
                counts["enabled"] += 1
        elif registry.is_enabled(name):
            registry.disable(name, CONFIG_DISABLE_REASON)
            counts["disabled"] += 1


def _apply_profile_to_names(
    registry: ToolRegistry,
    names: Iterable[str],
    profile: str,
) -> None:
    """Disable profile-excluded tools among *names*.

    ``apply_profile`` walks the whole registry and can force-enable or
    force-disable namespaces, so re-running it after a diff would override an
    administrator's manual disable.  A diff only constrains the tools it just
    registered, with the same built-in tag mapping startup uses.
    """
    tags = PROFILE_DISABLE_TAGS.get(profile)
    if not tags:
        return
    for name in names:
        tool = registry._tools.get(name)
        if tool is None or tool.metadata is None:
            continue
        if not registry.is_enabled(name):
            continue
        if tool.metadata.all_tags & set(tags):
            registry.disable(name, PROFILE_DISABLE_REASON)


def _in_scope(source: ToolSource, config: ToolConfig) -> bool:
    """Whether *source* should be registered under *config*."""
    return bool(source.enabled) and _should_load_source(source, config)


def _register_source(
    registry: ToolRegistry,
    source: ToolSource,
    config: ToolConfig,
    *,
    strict: bool,
    oauth_headers: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Register one source and return the names it added.

    The same predicates and per-source registration functions that
    :func:`~toolregistry_server.registry_builder.apply_config` uses are called
    directly, so filtering and namespace tags match startup.  A registration
    error propagates; a partial registration is rolled back first.

    Args:
        registry: The live registry.
        source: The declaration to register.
        config: The configuration the registration is filtered by.
        strict: Fail the whole OpenAPI source on an invalid regex.
        oauth_headers: Credential the OAuth provider resolved for an MCP
            source.  It is passed to the connection, never stored on the
            declaration.

    Raises:
        Exception: Whatever the source registration raised.
    """
    if not _in_scope(source, config):
        return frozenset()

    before = set(registry._tools)
    try:
        if isinstance(source, PythonSource):
            register_python_source(registry, source)
        elif isinstance(source, MCPSource):
            register_mcp_source(registry, source, oauth_headers=oauth_headers)
        elif isinstance(source, OpenAPISource):
            register_openapi_source(registry, source, strict=strict)
        else:  # pragma: no cover - the loader only builds the three above
            raise TypeError(f"Unsupported tool source: {type(source).__name__}")

        ns_tags = _build_ns_tags(dataclasses.replace(config, tools=(source,)))
        if ns_tags:
            _apply_ns_tags(registry, ns_tags)
    except Exception:
        # Registration is per tool, so a failure can leave tools behind.  The
        # caller records an empty name set for a failed source, which would
        # make those tools invisible to the diff: a later removal or edit
        # would skip them and they would live until restart.  Roll back.
        for name in set(registry._tools) - before:
            registry._unregister(name)
        raise
    return frozenset(set(registry._tools) - before)


class SourceManager:
    """Own the live source list of a running server.

    Args:
        registry: The live registry to mutate.
        config: The configuration the registry currently reflects.
        config_path: Path the configuration was loaded from.  Defaults to
            ``config.source``.  ``None`` means runtime-only changes.
        profile: Deployment profile applied at startup, if any.  Tools
            registered by a mutation are filtered with the same tag rule.
        strict_spec_regex: Abort on an invalid regex in an OpenAPI source
            instead of dropping the affected tools.
        write_config: Rewrite the config file after an API-originated
            mutation.  ``False`` keeps changes in memory (``--no-config-write``).
        on_registered: Optional callback invoked with the registry and the
            names a registration pass added: startup, a diff or a retry.
            It runs after registration and before the profile filter.
            Downstream packages use it for metadata defaults.
        oauth_provider: Resolves upstream OAuth credentials for protected
            MCP sources.  ``None`` leaves every source on its declared
            headers.  The provider is also consulted after a config reload,
            so a declaration added to the file takes effect without a
            restart.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        config: ToolConfig,
        *,
        config_path: str | os.PathLike[str] | None = None,
        profile: str | None = None,
        strict_spec_regex: bool = False,
        write_config: bool = True,
        on_registered: Callable[[ToolRegistry, set[str]], None] | None = None,
        oauth_provider: Any | None = None,
    ) -> None:
        self.registry = registry
        self.config = config
        self.config_path: str | None = (
            os.fspath(config_path)
            if config_path is not None
            else (config.source or None)
        )
        self.profile = profile
        self.strict_spec_regex = strict_spec_regex
        self.write_config = write_config
        self.on_registered = on_registered
        self.oauth_provider = oauth_provider
        self.route_table: RouteTable | None = None

        self._lock = threading.RLock()
        self._entries: dict[SourceKey, SourceEntry] = {}
        #: The core integration that owns each OpenAPI source's spec, keyed
        #: like :attr:`_entries`.  A refresh calls the integration rather
        #: than registering the source again, so the ETag and the tool-level
        #: state core tracks survive.
        self._openapi_integrations: dict[SourceKey, Any] = {}
        self._last_written_hash: str | None = None
        #: Error from the most recent watcher reload, cleared on success.
        self.last_reload_error: str | None = None

    # ---- Lifecycle -------------------------------------------------------

    def attach_route_table(self, route_table: RouteTable) -> None:
        """Attach the route table so one diff produces one rebuild."""
        self.route_table = route_table

    def apply_initial(self) -> tuple[SourceEntry, ...]:
        """Register every source in :attr:`config` and record its status.

        Mirrors :func:`~toolregistry_server.registry_builder.apply_config`:
        a source that fails is logged and recorded as ``failed`` rather than
        aborting startup, unless ``strict_spec_regex`` is set and the failure
        is a :class:`SpecPatternError`.

        The ``on_registered`` hook runs once after the loop with every name
        the startup sources registered, in the same place a diff runs it:
        after registration and before the profile filter and ``tool_metadata``.
        It is skipped when nothing was registered, and a failure is logged
        without unregistering the tools.

        Returns:
            One entry per source, in declaration order.
        """
        with self._lock:
            entries: dict[SourceKey, SourceEntry] = {}
            registered: set[str] = set()
            self._openapi_integrations.clear()
            for key, source in index_sources(self.config.tools).items():
                entry = self._apply_source(key, source, self.config)
                entries[key] = entry
                registered.update(entry.names)
            self._notify_registered(registered)
            if self.config.tool_metadata:
                self.registry.apply_metadata_config(self.config.tool_metadata)
            self._entries = entries
        return self.list()

    # ---- Query API -------------------------------------------------------

    def list(self) -> tuple[SourceEntry, ...]:
        """Return the current source entries in declaration order."""
        with self._lock:
            order = index_sources(self.config.tools)
            return tuple(self._entries[key] for key in order if key in self._entries)

    def to_dicts(self) -> tuple[dict[str, Any], ...]:
        """Return the JSON-ready status list for an admin/status API.

        Enabled counts are read from the live registry, so a tool disabled
        from the admin panel is reflected here.
        """
        with self._lock:
            return tuple(
                entry.to_dict(
                    enabled_names=[
                        name for name in entry.names if self.registry.is_enabled(name)
                    ]
                )
                for entry in self.list()
            )

    def get(self, ref: SourceRef) -> SourceEntry:
        """Return one source entry.

        Args:
            ref: The opaque id or the internal key.

        Raises:
            KeyError: If *ref* is not a currently declared source.
        """
        with self._lock:
            return self._entries[self.resolve_key(ref)]

    def resolve_key(self, ref: SourceRef) -> SourceKey:
        """Return the internal key for a source id or key.

        Args:
            ref: The opaque ``id`` from :meth:`SourceEntry.to_dict`, or an
                internal key returned by this method earlier.

        Returns:
            The internal key.

        Raises:
            KeyError: If *ref* matches no declared source.
        """
        with self._lock:
            if not isinstance(ref, str):
                if ref not in self._entries:
                    raise KeyError(ref)
                return ref
            for key, entry in self._entries.items():
                if _entry_id(entry.source) == ref:
                    return key
        raise KeyError(ref)

    # ---- Mutations -------------------------------------------------------

    def add(self, source: ToolSource) -> MutationResult:
        """Append *source* to the config, register it, and persist.

        Returns:
            The mutation result for the new source.
        """
        with self._lock:
            tools = (*self.config.tools, source)
            new_config = dataclasses.replace(self.config, tools=tools)
            counts = self._apply_diff(new_config)
            key = self._key_at(tools, len(tools) - 1)
            return self._finish(self._entries[key], counts)

    def remove(self, ref: SourceRef) -> MutationResult:
        """Unregister the source's tools, drop its declaration, and persist.

        Args:
            ref: The opaque id or the internal key.

        Returns:
            The mutation result; ``entry`` is the declaration that left and
            ``removed`` is ``True``.

        Raises:
            KeyError: If *ref* is not a currently declared source.
        """
        with self._lock:
            key = self.resolve_key(ref)
            position = self._position_of(key)
            removed = self._entries[key]
            tools = self.config.tools[:position] + self.config.tools[position + 1 :]
            new_config = dataclasses.replace(self.config, tools=tools)
            counts = self._apply_diff(new_config)
            return self._finish(removed, counts, removed=True)

    def replace(self, ref: SourceRef, source: ToolSource) -> MutationResult:
        """Replace a source's declaration with *source*, and persist.

        The old declaration's tools are unregistered and the new one's are
        registered, in one diff.

        Args:
            ref: The opaque id or the internal key of the declaration to
                replace.
            source: The new declaration.

        Returns:
            The mutation result for the new declaration.

        Raises:
            KeyError: If *ref* is not a currently declared source.
        """
        with self._lock:
            key = self.resolve_key(ref)
            position = self._position_of(key)
            tools = list(self.config.tools)
            tools[position] = source
            new_tools = tuple(tools)
            new_config = dataclasses.replace(self.config, tools=new_tools)
            counts = self._apply_diff(new_config)
            key_now = self._key_at(new_tools, position)
            return self._finish(self._entries[key_now], counts)

    def retry(self, ref: SourceRef) -> SourceEntry:
        """Retry a source whose last apply attempt failed.

        The declaration is unchanged, so nothing is written to the config
        file.  A source that is not ``FAILED`` is returned untouched, which
        makes this safe to call from an admin "retry" button.

        Args:
            ref: The opaque id or the internal key of the source to retry.

        Returns:
            The source's state after the attempt.

        Raises:
            KeyError: If *ref* is not a currently declared source.
        """
        with self._lock:
            key = self.resolve_key(ref)
            entry = self._entries[key]
            if entry.status is not SourceStatus.FAILED:
                return entry
            scope = (
                self.route_table.batch_changes()
                if self.route_table is not None
                else nullcontext()
            )
            with scope:
                # A failed attempt leaves no tools behind (a partial
                # registration is rolled back), so there is nothing to drop.
                entry = self._apply_source(key, entry.source, self.config)
                if self.on_registered is not None and entry.names:
                    self.on_registered(self.registry, set(entry.names))
                if self.profile and entry.names:
                    _apply_profile_to_names(self.registry, entry.names, self.profile)
                self._entries[key] = entry
            # A successful retry registers tools without a diff; apply the
            # current block to the names it brought back.
            self._sync_parameter_overrides(self.config, self.config, set(entry.names))
            return entry

    def refresh(self, ref: SourceRef) -> RefreshResult:
        """Re-fetch an OpenAPI source's document and apply the diff.

        The source is not registered again.  The core integration keeps the
        ETag and the names it tracks, so a conditional GET that returns
        ``304`` changes nothing, and a document that changed is diffed
        against the live tools: names keep their registry state (a tool
        disabled by hand stays disabled), and a path that disappeared is
        unregistered.

        A failed or refused fetch leaves the registry and the source entry
        untouched, so a source that cannot be re-read keeps its tools and the
        last good document.  The reason is logged and re-raised.

        A refreshed document passes the same spec-regex guard as a first
        registration: a tool whose ``pattern`` the ``regex`` extra cannot
        compile is unregistered, reported in the result's ``removed`` names,
        and forgotten by the integration so a later fixed document registers
        it again.  With
        ``strict_spec_regex`` the tools are still dropped — core has already
        applied the diff, so registration's all-or-nothing behaviour is not
        reachable — and :class:`SpecPatternError` is raised with the dropped
        names after the source entry is updated.

        Args:
            ref: The opaque id or the internal key of the source.

        Returns:
            The core :class:`~toolregistry.events.RefreshResult`, with the
            names that were added, removed and updated.

        Raises:
            KeyError: If *ref* is not a currently declared source.
            ValueError: If the source is not an OpenAPI source, is excluded
                by the current config, has no registered integration, or the
                document cannot be fetched or is refused by the outbound
                policy.
            SpecPatternError: If ``strict_spec_regex`` is set and the
                refreshed document contains an invalid regex.  The affected
                tools are dropped first.
        """
        with self._lock:
            key, entry, source, integration = self._refresh_target(ref)
            scope = (
                self.route_table.batch_changes()
                if self.route_table is not None
                else nullcontext()
            )
            try:
                with scope:
                    result = self._refresh_integration(source, integration)
                    if not result.skipped:
                        result, dropped = self._drop_invalid_patterns(
                            source, integration, result, entry.names
                        )
                        self._record_refresh(key, entry, result, dropped)
                        if dropped and self.strict_spec_regex:
                            raise SpecPatternError(
                                f"OpenAPI source "
                                f"{_redact_url_userinfo(source.url)!r} has "
                                f"{len(dropped)} tool(s) with an invalid regex; "
                                f"dropped {dropped}"
                            )
                    return result
            except SpecPatternError:
                raise
            except Exception as exc:
                # Core has not applied a diff, so the registry and the entry
                # still hold the last good state.  Re-raise for the caller.
                logger.warning(
                    f"Refresh of {_describe_source(source)} failed; "
                    f"keeping the current tools: {_safe_error(exc)}"
                )
                raise

    def _refresh_target(
        self, ref: SourceRef
    ) -> tuple[SourceKey, SourceEntry, OpenAPISource, Any]:
        """Resolve *ref* to a refreshable source and its integration.

        Args:
            ref: The opaque id or the internal key of the source.

        Returns:
            ``(key, entry, source, integration)``.

        Raises:
            KeyError: If *ref* is not a currently declared source.
            ValueError: If the source cannot be refreshed: it is not an
                OpenAPI source, the config excludes it, or no integration is
                stored for it.
        """
        key = self.resolve_key(ref)
        entry = self._entries[key]
        source = entry.source
        if not isinstance(source, OpenAPISource):
            raise ValueError(
                f"source {ref!r} is not an OpenAPI source; "
                "only an OpenAPI source re-reads its document"
            )
        if not _in_scope(source, self.config):
            raise ValueError(
                f"source {ref!r} is excluded by the current config; "
                "enable it before refreshing"
            )
        integration = self._openapi_integrations.get(key)
        if integration is None:
            raise ValueError(
                f"source {ref!r} has no registered OpenAPI integration; "
                "retry or re-add the source"
            )
        return key, entry, source, integration

    def _drop_invalid_patterns(
        self,
        source: OpenAPISource,
        integration: Any,
        result: RefreshResult,
        names: Iterable[str],
    ) -> tuple[RefreshResult, Sequence[str]]:
        """Drop refreshed tools whose spec regex the guard cannot compile.

        Registration checks these patterns before a tool is served; a refresh
        registers through core's diff, so the check runs here.  The log lines
        match registration's.

        Two things keep the drop honest and recoverable:

        * The returned result removes a dropped name from ``added``,
          ``updated``, ``breaking`` and ``compatible`` — a caller must not see
          a tool that is not registered — and adds it to ``removed``, because
          the refresh did take it out of this registry.  Without that, a
          drop-only refresh would report ``changed`` as false.
        * The integration forgets the name.  Core sets its tracked set from
          the new document before this check runs, so a forgotten name is
          registered again on the next refresh when the document is fixed;
          left tracked, core would count it as unchanged and never register
          it again.

        Args:
            source: The OpenAPI source declaration.
            integration: The core integration that owns the tracked set.
            result: The integration's refresh result.
            names: The source's current tool names.  They are checked as
                well when ``base_url_changed`` is set, because core rebuilt
                every wrapper from the new document even though it reports
                no added or updated names.

        Returns:
            ``(result, dropped)``, with the dropped names removed from
            ``added``, ``updated``, ``breaking`` and ``compatible`` and
            appended to ``removed``.
        """
        affected = set(result.added) | set(result.updated)
        if getattr(result, "base_url_changed", False):
            affected |= set(names)
        failures = _invalid_patterns(self.registry, affected)
        if not failures:
            return result, []
        detail = _redact_url_userinfo(source.url)
        for tool_name, schema_path, pattern, error_name in failures:
            logger.error(
                f"Dropping tool {tool_name!r} from OpenAPI source "
                f"{detail!r}: invalid regex at {schema_path} "
                f"({error_name}): {pattern!r}"
            )
        dropped = sorted({name for name, *_ in failures})
        logger.info(
            f"Refreshed OpenAPI tools from {detail}: dropped {len(dropped)} "
            f"tool(s) with an invalid regex: {dropped}"
        )
        for name in dropped:
            self.registry._unregister(name)  # ty: ignore[unresolved-attribute]
        self._forget_tracked(integration, dropped)
        gone = set(dropped)

        def keep(names: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(name for name in names if name not in gone)

        adjusted = dataclasses.replace(
            result,
            added=keep(result.added),
            updated=keep(result.updated),
            breaking=keep(result.breaking),
            compatible=keep(result.compatible),
            removed=tuple(result.removed) + tuple(dropped),
        )
        return adjusted, dropped

    @staticmethod
    def _forget_tracked(integration: Any, names: Sequence[str]) -> None:
        """Remove *names* from the integration's tracked tool set.

        ``forget_tracked_tools`` is the public core hook.  The matrix floor
        is still core 0.20.1, which predates it, so fall back to the private
        set until the floor is raised.
        """
        forget = getattr(integration, "forget_tracked_tools", None)
        if forget is not None:
            forget(names)
            return
        tracked = integration._registered_tool_names  # noqa: SLF001 - core floor
        for name in names:
            tracked.discard(name)

    def _record_refresh(
        self,
        key: SourceKey,
        entry: SourceEntry,
        result: RefreshResult,
        dropped: Sequence[str],
    ) -> None:
        """Store a refresh's names and run the post-registration steps.

        The steps mirror a registration pass, so a tool a refresh adds or
        changes ends in the same state a config reload would produce:
        namespace tags first, then ``on_registered``, the profile filter and
        ``tool_metadata``.  A refresh that moved the base URL rebuilt every
        wrapper, so every tool of the source is treated as touched even
        though core reports no added or updated names.  *dropped* names leave
        the entry even when the refresh had updated them, because the regex
        guard unregistered them.
        """
        touched = set(result.added) | set(result.updated)
        if getattr(result, "base_url_changed", False):
            # Core rebuilt every wrapper from the new document, so the tools
            # are not reported as added or updated, but their
            # post-registration state is gone.  Treat every tool of the
            # source as touched.
            touched |= set(entry.names)
        if touched:
            ns_tags = _build_ns_tags(
                dataclasses.replace(self.config, tools=(entry.source,))
            )
            if ns_tags:
                _apply_ns_tags(self.registry, ns_tags)
            if self.route_table is not None:
                # Core's rebuild and its per-tool REFRESH event replace the
                # Tool object without the tags, so the route is refreshed
                # once the tags have landed.
                for name in touched:
                    self.route_table.refresh(name)
        names = (
            entry.names - frozenset(result.removed) - frozenset(dropped)
        ) | frozenset(result.added)
        self._entries[key] = dataclasses.replace(
            entry,
            names=frozenset(names),
            status=SourceStatus.APPLIED,
            error=None,
        )
        added = set(result.added)
        if added:
            self._notify_registered(added)
        if touched:
            if self.profile:
                _apply_profile_to_names(self.registry, touched, self.profile)
            if self.config.tool_metadata:
                self.registry.apply_metadata_config(self.config.tool_metadata)
        if self.route_table is not None and added:
            # A tool this refresh added has no stored override yet: the
            # route rebuild above reads the map, which never learned the
            # config entry.  Existing tools need nothing here, because
            # ``route_table.refresh(name)`` already re-reads their entry.
            sync_parameter_overrides(
                self.route_table,
                self.config,
                previous=self.config,
                new_names=added,
            )

    @staticmethod
    def _refresh_integration(source: OpenAPISource, integration: Any) -> RefreshResult:
        """Fetch and apply one source's document through its integration.

        An HTTP(S) source refreshes through the integration, which sends the
        stored ETag and re-validates the URL against the outbound policy.  A
        local path has no ETag, so the file is read again and handed to the
        integration.

        Args:
            source: The OpenAPI source declaration.
            integration: The core integration that registered the source.

        Returns:
            The integration's refresh result.
        """
        if openapi_spec_url(source) is not None:
            return integration.refresh()
        from toolregistry.integrations.openapi import load_openapi_spec

        spec = load_openapi_spec(
            source.url,
            allow_local_files=True,
            allow_private_hosts=source.allow_private_hosts,
        )
        return integration.refresh(openapi_spec=spec)

    # ---- Watcher driver --------------------------------------------------

    def reload_from_path(self, path: str | os.PathLike[str] | None = None) -> bool:
        """Re-read the config file and apply the source-level diff.

        A watcher-originated reload never writes the file.

        Args:
            path: Config path; defaults to :attr:`config_path`.

        Returns:
            ``True`` when the file parsed and the diff was applied.
        """
        target = os.fspath(path) if path is not None else self.config_path
        if not target:
            return False
        try:
            config = load_config(target)
            self._reload_oauth_declarations(target, config)
        except Exception as exc:  # noqa: BLE001 - a bad edit must not kill the watcher
            with self._lock:
                self.last_reload_error = _safe_error(exc)
            logger.warning(
                f"Config reload skipped; keeping the current registry: "
                f"{_safe_error(exc)}"
            )
            return False
        with self._lock:
            self._apply_diff(config)
            self.last_reload_error = None
        return True

    def _reload_oauth_declarations(self, path: str, config: ToolConfig) -> None:
        """Reload ``oauth.clients`` and hand it to the provider.

        A malformed section raises, which skips the whole reload: the live
        registry keeps the previous declarations instead of half-applying a
        broken file.  A provider is created on the first reload that finds a
        declaration, so enabling OAuth does not require a restart.

        Args:
            path: The configuration file that was read.
            config: The parsed core configuration.

        Raises:
            toolregistry.config.ConfigError: If the section is invalid.
        """
        from .oauth.upstream.config import load_oauth_clients
        from .oauth.upstream.provider import OAuthClientProvider

        declarations = load_oauth_clients(path, config)
        if self.oauth_provider is None:
            if declarations:
                self.oauth_provider = OAuthClientProvider(declarations)
            return
        previous = self.oauth_provider.declarations
        # A declaration that left the file must take its stored token with it;
        # otherwise re-adding the source under the same selector reuses a
        # credential the operator has removed.  Clear before the swap: the
        # provider resolves the selector through its live declarations.
        remaining = {declaration.source for declaration in declarations}
        for declaration in previous:
            if declaration.source in remaining:
                continue
            try:
                self.oauth_provider.clear_stored_token(declaration.source)
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
                logger.warning(
                    f"Could not clear the stored OAuth token for "
                    f"{declaration.source!r}: {_safe_error(exc)}"
                )
        self.oauth_provider.replace_declarations(declarations)

    def is_own_write(self) -> bool:
        """Whether the config file still holds the content this manager wrote.

        The watcher uses this to ignore the change its own mutation caused.
        """
        if self._last_written_hash is None or not self.config_path:
            return False
        return _file_hash(Path(self.config_path)) == self._last_written_hash

    # ---- Internals -------------------------------------------------------

    def _apply_source(
        self,
        key: SourceKey,
        source: ToolSource,
        config: ToolConfig,
    ) -> SourceEntry:
        """Register one source and return its entry, recording failures."""
        if not _in_scope(source, config):
            return SourceEntry(key, source, frozenset(), SourceStatus.EXCLUDED)
        oauth_headers: Mapping[str, str] | None = None
        if isinstance(source, MCPSource) and self.oauth_provider is not None:
            from .oauth.upstream.provider import OAuthClientError

            try:
                oauth_headers = self.oauth_provider.headers_for(source)
            except OAuthClientError as exc:
                return self._oauth_failure(key, source, exc)
        before_integrations = len(self.registry._openapi_integrations)
        try:
            names = _register_source(
                self.registry,
                source,
                config,
                strict=self.strict_spec_regex,
                oauth_headers=oauth_headers,
            )
        except Exception as exc:  # noqa: BLE001 - one bad source must not abort
            # Only the strict regex switch is fatal; every other failure is
            # recorded so a later diff can retry it.
            self._openapi_integrations.pop(key, None)
            if self.strict_spec_regex and isinstance(exc, SpecPatternError):
                raise
            status = _auth_rejection_status(exc)
            if oauth_headers is not None and status is not None:
                # The resource server rejected the credential itself, so the
                # stored grant is dead even though the token endpoint accepted
                # it.  Report it the same way as a rejected token request.
                logger.warning(
                    f"MCP source {_describe_source(source)} rejected the OAuth "
                    f"credential with HTTP {status}; re-authorization required"
                )
                return SourceEntry(
                    key,
                    source,
                    frozenset(),
                    SourceStatus.FAILED,
                    f"HTTP {status} from the MCP server; the OAuth token was rejected",
                    reauth_required=True,
                )
            logger.warning(
                f"Failed to load tools from {_describe_source(source)}: "
                f"{_safe_error(exc)}"
            )
            return SourceEntry(
                key, source, frozenset(), SourceStatus.FAILED, _safe_error(exc)
            )
        self._remember_integration(key, source, before_integrations)
        return SourceEntry(key, source, names, SourceStatus.APPLIED)

    def _oauth_failure(
        self,
        key: SourceKey,
        source: ToolSource,
        exc: OAuthClientError,
    ) -> SourceEntry:
        """Record an upstream OAuth failure for one source.

        A definitive rejection sets ``reauth_required`` so the panel can
        offer Re-authorize; a transient failure leaves the stored token
        alone and is retried on the next diff.  A provider error raised for
        ``client_credentials`` is a definitive rejection too: the operator
        must correct the client secret, and the next diff fetches again.

        Args:
            key: The source's stable identity.
            source: The declaration that failed.
            exc: The provider error.

        Returns:
            A ``FAILED`` entry carrying the redacted reason.
        """
        reauth = bool(getattr(exc, "reauth_required", False))
        message = _safe_error(exc)
        logger.warning(
            f"OAuth failed for {_describe_source(source)}"
            f"{' (re-authorization required)' if reauth else ''}: {message}"
        )
        return SourceEntry(
            key,
            source,
            frozenset(),
            SourceStatus.FAILED,
            message,
            reauth_required=reauth,
        )

    def _remember_integration(
        self, key: SourceKey, source: ToolSource, before: int
    ) -> None:
        """Record the OpenAPI integration a registration pass appended.

        ``_openapi_integrations`` is the registry's own list; a registration
        appends exactly one integration per source, so the entries after
        *before* belong to this pass.  A non-OpenAPI source has no
        integration; an OpenAPI pass that appended nothing clears a stale
        entry for *key*.
        """
        if not isinstance(source, OpenAPISource):
            return
        appended = self.registry._openapi_integrations[before:]
        if appended:
            self._openapi_integrations[key] = appended[-1]
        else:
            self._openapi_integrations.pop(key, None)

    def _apply_diff(self, new_config: ToolConfig) -> dict[str, int]:
        """Apply *new_config* source by source and update the entries."""
        counts = {"registered": 0, "unregistered": 0, "enabled": 0, "disabled": 0}
        previous_config = self.config
        old_sources = index_sources(self.config.tools)
        new_sources = index_sources(new_config.tools)
        new_names: set[str] = set()
        entries: dict[SourceKey, SourceEntry] = {}

        scope = (
            self.route_table.batch_changes()
            if self.route_table is not None
            else nullcontext()
        )
        with scope:
            # Sources no longer declared: their tools leave the registry.
            for key in set(old_sources) - set(new_sources):
                self._openapi_integrations.pop(key, None)
                entry = self._entries.get(key)
                if entry is not None:
                    _remove_names(self.registry, entry.names, counts)

            for key, source in new_sources.items():
                entry, added = self._apply_one(
                    key,
                    source,
                    old_sources.get(key),
                    self._entries.get(key),
                    new_config,
                    counts,
                )
                entries[key] = entry
                new_names.update(added)

            try:
                self._after_registration(new_config, new_names)
            finally:
                # Publish the state even when a downstream hook raises: the
                # registry already holds the new tools, so leaving the
                # manager on the old config would desynchronise them.
                self.config = new_config
                self._entries = entries
        self._sync_parameter_overrides(previous_config, new_config, new_names)
        return counts

    def _sync_parameter_overrides(
        self, previous: ToolConfig, current: ToolConfig, new_names: set[str]
    ) -> None:
        """Re-apply the reloaded ``tool_parameters`` to the live table.

        Startup applies the block once, when the route table is built; a
        reload changes it without rebuilding the table, so a diff has to
        bring the routes in line (#165).  Removed entries are cleared, an
        unchanged block is a no-op unless this pass registered a tool the
        block names, and a rejected entry is logged and skipped so one bad
        entry never fails the reload.
        """
        if self.route_table is None:
            return
        sync_parameter_overrides(
            self.route_table, current, previous=previous, new_names=new_names
        )

    def _after_registration(self, new_config: ToolConfig, new_names: set[str]) -> None:
        """Run the post-registration steps for one diff.

        The ``on_registered`` hook is best-effort: a downstream metadata
        default failing must not undo a registration the registry already
        performed, so the failure is logged.  The profile filter and the
        file's ``tool_metadata`` then run in the startup order.
        """
        self._notify_registered(new_names)

        # Tools registered by this diff must obey the deployment profile
        # applied to the startup registry.
        if self.profile and new_names:
            _apply_profile_to_names(self.registry, new_names, self.profile)

        if new_config.tool_metadata:
            self.registry.apply_metadata_config(new_config.tool_metadata)

    def _notify_registered(self, names: set[str]) -> None:
        """Call the optional ``on_registered`` hook, logging a failure.

        A downstream metadata hook failing must not undo a registration the
        registry already performed, so the error is logged and the tools stay
        registered.  Startup and diffs both report their names here, so they
        share the same semantics.

        Args:
            names: Names registered by the pass.  An empty set skips the
                callback.
        """
        if self.on_registered is None or not names:
            return
        try:
            self.on_registered(self.registry, names)
        except Exception as exc:  # noqa: BLE001 - metadata hook is optional
            logger.error(
                "on_registered callback failed; the tools stay registered: "
                f"{_safe_error(exc)}"
            )

    def _apply_one(
        self,
        key: SourceKey,
        source: ToolSource,
        previous: ToolSource | None,
        entry: SourceEntry | None,
        new_config: ToolConfig,
        counts: dict[str, int],
    ) -> tuple[SourceEntry, set[str]]:
        """Apply one declaration; return its entry and its newly added names."""
        if previous is not None and previous == source and entry is not None:
            return self._apply_unchanged(key, source, entry, new_config, counts)
        # Added or changed: drop what the old declaration produced, then
        # register the new one if it is in scope.
        if entry is not None:
            _remove_names(self.registry, entry.names, counts)
        fresh = self._apply_source(key, source, new_config)
        counts["registered"] += len(fresh.names)
        return fresh, set(fresh.names)

    def _apply_unchanged(
        self,
        key: SourceKey,
        source: ToolSource,
        entry: SourceEntry,
        new_config: ToolConfig,
        counts: dict[str, int],
    ) -> tuple[SourceEntry, set[str]]:
        """Handle a declaration that did not change.

        An excluded source that never registered, and any failed source, still
        needs a full attempt; otherwise only the scope can have moved.
        """
        included = _in_scope(source, new_config)
        if entry.status is SourceStatus.FAILED or (
            entry.status is SourceStatus.EXCLUDED and not entry.names
        ):
            fresh = self._apply_source(key, source, new_config)
            counts["registered"] += len(fresh.names)
            return fresh, set(fresh.names)
        _set_scope(self.registry, entry.names, included, counts)
        return (
            dataclasses.replace(
                entry,
                status=(SourceStatus.APPLIED if included else SourceStatus.EXCLUDED),
            ),
            set(),
        )

    def _finish(
        self, entry: SourceEntry, counts: dict[str, int], *, removed: bool = False
    ) -> MutationResult:
        """Persist an API-originated mutation and build its result."""
        persisted, error, backup, warnings = self._persist()
        return MutationResult(
            entry=entry,
            counts=counts,
            persisted=persisted,
            persist_error=error,
            backup_path=backup,
            warnings=warnings,
            removed=removed,
        )

    def _persist(self) -> tuple[bool, str | None, str | None, tuple[str, ...]]:
        """Rewrite the config file, reporting every failure.

        Returns:
            ``(persisted, persist_error, backup_path, warnings)``.
        """
        if not self.write_config:
            return False, None, None, ("config writing is disabled",)
        if not self.config_path:
            return (
                False,
                None,
                None,
                ("no config file was loaded; the change is runtime-only",),
            )
        path = Path(self.config_path)
        backup: str | None = None
        try:
            from ._config_file import (
                _WRITE_LOCK,
                read_config_mapping,
                write_config_mapping,
            )

            # The whole read-modify-write runs under the same lock the gateway
            # uses for a frontend save.  Without it, a panel source edit that
            # races a frontend save would read the file first and write back
            # a version without that section.
            with _WRITE_LOCK:
                data = read_config_mapping(path) if path.exists() else {}
                serialized = self.config.to_dict()
                for key in _CORE_CONFIG_KEYS:
                    data.pop(key, None)
                data.update(serialized)
                self._prune_oauth_section(data)
                if path.exists():
                    backup = f"{path}.bak"
                write_config_mapping(data, path)
            self._last_written_hash = _file_hash(path)
            return True, None, backup, (COMMENTS_WARNING,)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            return False, _safe_error(exc), backup, ()

    def _prune_oauth_section(self, data: dict[str, Any]) -> None:
        """Drop ``oauth.clients`` entries whose source is no longer declared.

        The section is server-owned and is preserved verbatim, so a literal
        client secret is never round-tripped through ``to_dict``.  Only
        entries that no longer select a source are removed: deleting a
        source must not make the next reload reject the file.  A removed
        declaration's stored token is deleted too, so re-adding the source
        under the same selector cannot reuse a credential that was minted
        for a declaration the operator has removed.

        Args:
            data: The configuration mapping about to be written.
        """
        from .oauth.config import OAUTH_SECTION
        from .oauth.upstream.config import OAUTH_CLIENTS_KEY, selector_matches_source

        section = data.get(OAUTH_SECTION)
        if not isinstance(section, dict):
            return
        entries = section.get(OAUTH_CLIENTS_KEY)
        if not isinstance(entries, list):
            return
        kept = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("source"), str)
            and any(
                selector_matches_source(entry["source"], source)
                for source in self.config.tools
            )
        ]
        kept_ids = {id(entry) for entry in kept}
        pruned = [
            entry["source"]
            for entry in entries
            if id(entry) not in kept_ids
            and isinstance(entry, dict)
            and isinstance(entry.get("source"), str)
        ]
        if pruned:
            self._clear_stored_tokens(pruned)
        if kept:
            section[OAUTH_CLIENTS_KEY] = kept
        else:
            section.pop(OAUTH_CLIENTS_KEY, None)
        if not section:
            # Only drop the whole section when nothing else was configured;
            # ``issuer`` and the other resource-server keys belong to it too.
            data.pop(OAUTH_SECTION, None)

    def _clear_stored_tokens(self, selectors: Sequence[str]) -> None:
        """Delete the stored token for each removed OAuth declaration.

        A store that cannot be opened is not an error: the row is stale, and
        the next authorization overwrites it.  The write must not fail
        because of a cleanup.

        Args:
            selectors: Selectors whose declaration left the configuration.
        """
        provider = self.oauth_provider
        if provider is None:
            return
        clearer = getattr(provider, "clear_stored_token", None)
        if clearer is None:  # pragma: no cover - a stand-in provider has none
            return
        for selector in selectors:
            try:
                clearer(selector)
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
                logger.warning(
                    f"Could not clear the stored OAuth token for {selector!r}: "
                    f"{_safe_error(exc)}"
                )

    def _position_of(self, key: SourceKey) -> int:
        """Return the index of *key* in the declared source list."""
        order = index_sources(self.config.tools)
        if key not in order:
            raise KeyError(key)
        for position, candidate in enumerate(order):
            if candidate == key:
                return position
        raise KeyError(key)  # pragma: no cover - dict order guarantees a hit

    @staticmethod
    def _key_at(tools: tuple[ToolSource, ...], position: int) -> SourceKey:
        """Return the key :func:`index_sources` assigns to *position*."""
        seen: dict[SourceKey, int] = {}
        for index, source in enumerate(tools):
            base = source_key(source)
            occurrence = seen.get(base, 0)
            seen[base] = occurrence + 1
            key: SourceKey = base if occurrence == 0 else (*base, occurrence)
            if index == position:
                return key
        raise IndexError(position)


class ConfigWatcher(threading.Thread):
    """Daemon thread that polls the config file and drives the manager.

    The watcher keeps no diff logic of its own: a changed file is handed to
    :meth:`SourceManager.reload_from_path`, which serialises against other
    mutations.  The manager's own config writes are recognised by content hash
    and do not count as an external change.

    Args:
        manager: The source manager to drive.
        interval: Seconds between polls.
    """

    def __init__(self, manager: SourceManager, *, interval: float = 2.0) -> None:
        super().__init__(name="toolregistry-server-config-reload", daemon=True)
        self.manager = manager
        self.interval = interval
        self._stop_event = threading.Event()
        self._poll_lock = threading.Lock()
        # Start from a sentinel, not from the stat at construction: the
        # registry was built before this object existed, so an edit in that
        # window would otherwise be adopted as the baseline.  The first poll
        # always runs; the diff is idempotent, so an unchanged file does no
        # work.
        self._last_seen: tuple[int, int] | None = (0, 0)

    def _stat_key(self) -> tuple[int, int] | None:
        """Return ``(mtime_ns, size)``, or ``None`` when the file is absent.

        A missing file is not an error: editors that write through a temporary
        file and rename leave the path absent for a moment.
        """
        path = self.manager.config_path
        if not path:
            return None
        try:
            info = os.stat(path)
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def poll_once(self) -> bool:
        """Reload if the file changed since the last poll.

        Returns:
            ``True`` when a reload was applied.
        """
        current = self._stat_key()
        if current is None:
            return False
        with self._poll_lock:
            if current == self._last_seen:
                return False
            # Remember the observed state before reloading: a parse failure
            # must not retry until the file changes again.
            self._last_seen = current
            if self.manager.is_own_write():
                return False
            return self.manager.reload_from_path()

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - a reload bug must not kill the watcher
                logger.warning(
                    f"Config reload failed; watcher continues: {_safe_error(exc)}"
                )

    def stop(self, timeout: float | None = None) -> None:
        """Ask the thread to exit.

        Args:
            timeout: When given, wait this many seconds for the thread to
                finish.  ``None`` returns immediately.
        """
        self._stop_event.set()
        if timeout is not None and self.is_alive():
            self.join(timeout)
