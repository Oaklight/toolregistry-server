"""Upstream OAuth client for protected MCP sources.

The provider owns three flows:

* **client credentials** — fetch a token for a machine-to-machine source,
  cache it, and refresh it before expiry;
* **authorization code + PKCE** — the start/complete pair the panel will
  drive in stage 2.  :meth:`OAuthClientProvider.start_authorization` returns the
  authorization URL, the opaque ``state`` and an expiry; the PKCE verifier
  stays on disk.  :meth:`OAuthClientProvider.complete_authorization` exchanges
  the code and stores the token;
* **refresh** — both grants reuse the same exchange path, with a skew so a
  token that is about to expire is replaced before it is handed out.

Failure classification is deliberate:

* a definitive rejection (``invalid_grant``, ``invalid_client``, another
  4xx) raises :class:`ReauthorizationRequired`, clears the stored tokens and
  records the redacted reason.  A ``client_credentials`` source is still
  retried on the next connect: its grant is the client secret, which the
  operator can correct at any time, and there is no interactive
  authorization to wait for.  An ``authorization_code`` source keeps the
  latch until the operator authorizes it again;
* a transport failure, a 3xx or a 5xx/429 raises :class:`TransientOAuthError`
  and leaves the stored record alone, so a retry can use it.  Every redirect
  is refused: a token request carries the client secret in its body or in an
  ``Authorization`` header, and following a 3xx would send it to whatever
  host the response names;
* an expired token is never returned.  If it cannot be refreshed, the caller
  gets an error, not the stale value.  A token whose response carried no
  ``expires_in`` has no expiry at all: it is returned until it is replaced or
  until the resource server rejects it.

No token, secret or PKCE verifier is written to a log or returned in a
dictionary.  Error messages pass through :func:`redact`.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn, Protocol
from urllib.parse import urlencode, urlsplit

from toolregistry.config import ToolSource

from ..._vendor.structlog import get_logger
from ..verifier import _metadata_url_candidates
from .config import (
    DEFAULT_EXPIRY_SKEW,
    DEFAULT_HTTP_TIMEOUT,
    OAuthClientConfig,
    default_token_store_path,
    selector_matches_source,
)
from .store import (
    DEFAULT_ASSUMED_TTL,
    PendingAuthorization,
    TokenRecord,
    TokenStore,
    utcnow,
)

logger = get_logger()

__all__ = [
    "AuthorizationRequest",
    "OAuthClientError",
    "OAuthClientProvider",
    "OAuthHTTPError",
    "OAuthStateError",
    "OAuthStoreError",
    "OAuthTransport",
    "ReauthorizationRequired",
    "TransientOAuthError",
    "UrllibTransport",
    "redact",
]

#: How long an authorization request may wait for its callback.
AUTHORIZATION_STATE_TTL = 600.0

#: How long an authorization-server metadata document is trusted before it is
#: fetched again.  Discovery is not on the hot path, so a short TTL costs one
#: request per source per window and lets a metadata change take effect
#: without a restart.
DEFAULT_METADATA_TTL = 300.0

#: Statuses that mean a discovered endpoint has moved rather than refused us.
#: They are retried once after the metadata cache is dropped.
_MOVED_ENDPOINT_STATUSES = frozenset({404, 410})

#: Consecutive resource-server rejections after which retrying stops.
MAX_REJECTIONS = 2

#: ``scheme://user:password@`` in a URL, inside arbitrary text.
_URL_USERINFO = re.compile(r"\b([A-Za-z][A-Za-z0-9+.\-]*://)[^/\s]+@")

#: Credential-shaped query or form parameters in arbitrary text.
_TOKEN_PARAM = re.compile(
    r"(?i)\b(access_token|refresh_token|id_token|client_secret|code)=([^&\s]+)"
)

#: A secret shorter than this is not replaced in free text: replacing a
#: one-character secret would rewrite every occurrence of that character,
#: including inside the diagnostic word.  Parameter-shaped occurrences are
#: still replaced by :data:`_TOKEN_PARAM` regardless of length.
_MIN_REDACT_LENGTH = 6


#: PBKDF2 work factor for the client-credential fingerprint.  The cost is
#: paid only when a token is minted and when a latched row is compared, both
#: of which are rare; it makes an offline guess of a low-entropy client
#: secret expensive rather than instant.
_SECRET_FINGERPRINT_ITERATIONS = 120_000

_SECRET_FINGERPRINT_ALGORITHM = "pbkdf2_sha256"


def _secret_material(declaration: OAuthClientConfig) -> bytes:
    """Return the credential material a fingerprint covers.

    The environment-variable name, the client authentication method and the
    resolved secret all participate, so correcting any of them changes the
    fingerprint.
    """
    return "\0".join(
        (
            declaration.client_secret_env or "",
            declaration.client_auth,
            declaration.client_secret or "",
        )
    ).encode("utf-8")


def _secret_digest(declaration: OAuthClientConfig) -> str:
    """Return a salted, iterated fingerprint of the client credential.

    The secret itself is never stored.  A plain hash would be recoverable by
    an offline dictionary attack on a human-chosen secret; a per-row random
    salt plus a real PBKDF2 work factor makes that expensive.  The protection
    is still bounded by the store's file permissions.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        _secret_material(declaration),
        salt,
        _SECRET_FINGERPRINT_ITERATIONS,
    )
    return "$".join(
        (
            _SECRET_FINGERPRINT_ALGORITHM,
            str(_SECRET_FINGERPRINT_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def _secret_digest_matches(declaration: OAuthClientConfig, stored: str) -> bool:
    """Whether *stored* was derived from the declaration's current credential.

    An empty value means the row predates the column, so there is nothing to
    compare and the caller treats it as a match.
    """
    if not stored:
        return True
    try:
        algorithm, iterations, salt_text, digest_text = stored.split("$")
        if algorithm != _SECRET_FINGERPRINT_ALGORITHM:
            return False
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256", _secret_material(declaration), salt, int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(actual, expected)


def _basic_credential(declaration: OAuthClientConfig) -> str:
    """Return the base64 credential sent for ``client_secret_basic``."""
    return base64.b64encode(
        f"{declaration.client_id}:{declaration.client_secret or ''}".encode()
    ).decode("ascii")


def _source_url(source: ToolSource) -> str:
    """Return the URL an MCP source connects to, or an empty string."""
    url = getattr(source, "url", "")
    return url if isinstance(url, str) else ""


def _is_http_url(value: Any) -> bool:
    """Whether *value* is an absolute ``http`` or ``https`` URL."""
    if not isinstance(value, str) or not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class OAuthClientError(Exception):
    """Base error for an upstream OAuth failure.

    ``reauth_required`` tells the source manager whether the operator must
    authorize the source again or a later retry may succeed.
    """

    reauth_required = False


class ReauthorizationRequired(OAuthClientError):
    """The stored grant was rejected; the source must be authorized again."""

    reauth_required = True


class TransientOAuthError(OAuthClientError):
    """A transport, timeout or server-side failure worth retrying."""


class OAuthStoreError(TransientOAuthError):
    """The token store could not be read or written.

    The caller keeps the stored grant and retries, so this is transient, but
    it is a distinct type so a panel can say "token store" rather than
    "authorization server" that failed.
    """


class OAuthStateError(ReauthorizationRequired):
    """The authorization callback presented an unknown or expired state."""


class OAuthHTTPError(Exception):
    """An HTTP response that carried an OAuth error document.

    Attributes:
        status: HTTP status code.
        error: OAuth ``error`` value, when the body carried one.
        description: OAuth ``error_description`` value, when present.
    """

    def __init__(
        self,
        status: int,
        error: str | None = None,
        description: str | None = None,
    ) -> None:
        super().__init__(f"HTTP {status}: {error or 'no error code'}")
        self.status = status
        self.error = error
        self.description = description


class OAuthTransport(Protocol):
    """The HTTP surface the provider needs, so tests can avoid the network."""

    def get_json(self, url: str, timeout: float) -> Any:
        """Fetch and decode one JSON document."""

    def post_form(
        self,
        url: str,
        fields: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> Any:
        """POST a form body and decode the JSON response."""


class UrllibTransport:
    """Default transport built on :mod:`urllib.request`.

    Redirects are refused for every request.  The default opener follows a
    301/302/303 by re-issuing the request as a ``GET`` to the host named in
    ``Location`` and keeps the ``Authorization`` header, so a token POST that
    carries ``client_secret_basic`` would hand the client secret to that
    host.  A redirect is reported as :class:`OAuthHTTPError` with its own
    status, which the provider classifies as transient.

    The transport does not apply an outbound address policy.  The operator
    supplies every URL it dials, but the URLs are not screened by
    :mod:`toolregistry.netpolicy`: discovery and the token POST can reach a
    loopback or link-local host if the configuration names one.  That matches
    :mod:`toolregistry_server.oauth.verifier`.  Screening is tracked as #264;
    nothing implements it here.
    """

    def get_json(self, url: str, timeout: float) -> Any:
        """Fetch and decode one JSON document."""
        request = urllib.request.Request(
            url, headers={"Accept": "application/json"}, method="GET"
        )
        return self._open(request, timeout)

    def post_form(
        self,
        url: str,
        fields: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> Any:
        """POST a form body and decode the JSON response."""
        request = urllib.request.Request(
            url,
            data=urlencode(dict(fields)).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                **dict(headers),
            },
            method="POST",
        )
        return self._open(request, timeout)

    @staticmethod
    def _open(request: Any, timeout: float) -> Any:
        """Open *request* and decode JSON, mapping transport errors."""
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload: Any = None
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = None
            error = payload.get("error") if isinstance(payload, dict) else None
            description = (
                payload.get("error_description") if isinstance(payload, dict) else None
            )
            raise OAuthHTTPError(exc.code, error, description) from None
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
            http.client.HTTPException,
        ) as exc:
            raise TransientOAuthError(
                f"OAuth request failed: {type(exc).__name__}"
            ) from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so no request is replayed to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Return ``None`` so urllib raises the 3xx as an ``HTTPError``."""
        return None


def redact(text: str, *secrets: str | None) -> str:
    """Remove URL userinfo and any known secret from *text*.

    Args:
        text: Text that may contain a credential.
        *secrets: Values to replace with ``[redacted]``.

    Returns:
        The sanitised text.
    """
    cleaned = _URL_USERINFO.sub(r"\1", text)
    cleaned = _TOKEN_PARAM.sub(r"\1=[redacted]", cleaned)
    for secret in secrets:
        if secret and len(secret) >= _MIN_REDACT_LENGTH:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


@dataclass(frozen=True)
class AuthorizationRequest:
    """Result of :meth:`OAuthClientProvider.start_authorization`.

    Attributes:
        authorization_url: URL the operator's browser must open.
        state: Opaque value the callback must present.  It is also the
            handle for the stored PKCE verifier.
        expires_at: ISO 8601 instant after which the request is refused.
    """

    authorization_url: str
    state: str
    expires_at: str

    def to_dict(self) -> dict[str, str]:
        """Return the JSON-ready form.  The verifier is not included."""
        return {
            "authorization_url": self.authorization_url,
            "state": self.state,
            "expires_at": self.expires_at,
        }


class OAuthClientProvider:
    """Resolve, refresh and store OAuth tokens for MCP sources.

    Args:
        declarations: Per-source OAuth declarations.
        store: A single store used for every declaration.  Intended for
            tests and embedders; production leaves it ``None`` so each
            declaration's ``token_store`` is honoured.
        default_token_store: Store used for authorization-code state and
            for declarations without a ``token_store``.
        transport: HTTP transport.  Defaults to :class:`UrllibTransport`.
        clock: Callable returning the current aware datetime.
        timeout: Per-request timeout, in seconds.
        skew: Seconds before expiry at which a token is refreshed.
        assumed_ttl: Seconds a token is trusted when its response stated no
            expiry.  ``None`` keeps such a token until something replaces it.
        metadata_ttl: Seconds an authorization-server metadata document is
            cached.  Expiry re-fetches it; a failed re-fetch keeps serving
            the cached document and retries after a full TTL, so an
            unreachable identity provider does not stall every connect.
    """

    def __init__(
        self,
        declarations: tuple[OAuthClientConfig, ...] = (),
        *,
        store: TokenStore | None = None,
        default_token_store: str | None = None,
        transport: OAuthTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        skew: float = DEFAULT_EXPIRY_SKEW,
        assumed_ttl: float | None = DEFAULT_ASSUMED_TTL,
        metadata_ttl: float = DEFAULT_METADATA_TTL,
    ) -> None:
        self._declarations = tuple(declarations)
        self._store = store
        self._default_token_store = default_token_store or default_token_store_path()
        self._transport = transport or UrllibTransport()
        self._clock = clock or utcnow
        self._timeout = timeout
        self._skew = skew
        self._assumed_ttl = assumed_ttl
        self._metadata_ttl = metadata_ttl
        self._lock = threading.RLock()
        self._stores: dict[str, TokenStore] = {}
        self._token_locks: dict[str, threading.Lock] = {}
        self._metadata: dict[str, tuple[dict[str, Any], datetime]] = {}

    # -- declaration lookup ------------------------------------------------

    @property
    def declarations(self) -> tuple[OAuthClientConfig, ...]:
        """Return the current declarations."""
        with self._lock:
            return self._declarations

    def replace_declarations(self, declarations: tuple[OAuthClientConfig, ...]) -> None:
        """Replace the declarations after a config reload.

        The stored-token reconciliation runs after the swap, so a declaration
        that left the file takes its row with it.
        """
        with self._lock:
            self._declarations = tuple(declarations)
        try:
            self.reconcile_stored_tokens()
        except OAuthStoreError as exc:
            # The swap is what a caller asked for; the cleanup is
            # best-effort, and a locked store must not fail a panel mutation
            # after its config was already written.
            logger.warning(f"Could not reconcile stored OAuth tokens: {exc}")

    def declaration_for(self, source: ToolSource) -> OAuthClientConfig | None:
        """Return the declaration that selects *source*, if any.

        Raises:
            OAuthClientError: If more than one declaration selects the source.
                A config load rejects that state; a runtime edit can create
                it, and failing the source is safer than picking one.
        """
        matches = [
            declaration
            for declaration in self.declarations
            if selector_matches_source(declaration.source, source)
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise OAuthClientError(
                f"{len(matches)} OAuth declarations select the same MCP source"
            )
        return matches[0]

    def declaration_by_selector(self, selector: str) -> OAuthClientConfig | None:
        """Return the declaration written under *selector*."""
        for declaration in self.declarations:
            if declaration.source == selector:
                return declaration
        return None

    def reject_at_resource_server(self, source: ToolSource) -> bool:
        """Record that the resource server rejected the token for *source*.

        The access token is always cleared, so the next connect refreshes or
        re-fetches instead of presenting the rejected value again.  The
        refresh token is kept because it may still be good.

        Args:
            source: The MCP source whose credential was rejected.

        Returns:
            ``True`` when a stored row was marked, ``False`` when no
            declaration selects *source*.
        """
        declaration = self.declaration_for(source)
        if declaration is None:
            return False
        with self._token_lock(declaration.source):
            store = self._store_for(declaration)
            if store.get(declaration.source) is None:
                return False
            store.mark_rejected(declaration.source)
        return True

    def accept_at_resource_server(self, source: ToolSource) -> None:
        """Clear the rejection counter after the resource server accepted it.

        The connect path is the only place that knows the credential worked
        end to end, so it owns the reset; a successful token-endpoint call
        alone does not prove the resource server will take the token.

        Args:
            source: The MCP source that just registered successfully.
        """
        declaration = self.declaration_for(source)
        if declaration is None:
            return
        with self._token_lock(declaration.source):
            self._store_for(declaration).reset_rejections(declaration.source)

    def clear_stored_token(self, selector: str) -> None:
        """Delete the stored token for *selector* when a declaration uses it.

        Called before a declaration leaves the configuration.  The store is
        keyed by the selector, so leaving the row behind would attach an old
        credential to a source that is later re-added under the same
        selector.  A selector with no declaration is ignored: nothing this
        provider owns can be bound to it.
        """
        declaration = self.declaration_by_selector(selector)
        if declaration is None:
            return
        with self._token_lock(declaration.source):
            self._store_for(declaration).clear(declaration.source)
        # The selector is gone, so its lock can never be taken again; keeping
        # it would grow ``_token_locks`` for every declaration ever removed.
        with self._lock:
            self._token_locks.pop(declaration.source, None)

    def _token_lock(self, source_id: str) -> threading.Lock:
        """Return the per-source lock that serialises token acquisition."""
        with self._lock:
            lock = self._token_locks.get(source_id)
            if lock is None:
                lock = threading.Lock()
                self._token_locks[source_id] = lock
            return lock

    # -- token acquisition -------------------------------------------------

    def headers_for(self, source: ToolSource) -> dict[str, str] | None:
        """Return the ``Authorization`` header for *source*.

        ``None`` means the source has no OAuth declaration and should keep
        whatever headers it already declares.

        Raises:
            OAuthClientError: If the token cannot be obtained.  The subclass
                says whether re-authorization is required.
        """
        declaration = self.declaration_for(source)
        if declaration is None:
            return None
        record = self.access_token(declaration, source_url=_source_url(source))
        if not record.access_token:
            raise ReauthorizationRequired(
                f"{declaration.source} has no access token; authorize it"
            )
        token_type = record.token_type or "Bearer"
        return {"Authorization": f"{token_type} {record.access_token}"}

    def access_token(
        self, declaration: OAuthClientConfig, *, source_url: str = ""
    ) -> TokenRecord:
        """Return a usable token, refreshing or fetching one as needed.

        Args:
            declaration: The declaration to resolve.
            source_url: URL of the source asking for the token.  A stored
                token minted for another URL is discarded, so repointing a
                source cannot present the old host's credential.

        An expired token is never returned.  A stored ``needs_reauth`` flag
        short-circuits every attempt until a new authorization succeeds, or,
        for the ``client_credentials`` grant, until the next attempt: that
        grant has no interactive authorization, so a corrected client secret
        gets a fresh fetch instead of a permanent failure.  A stored record
        minted for another ``issuer`` or ``client_id`` is ignored.

        A token whose response stated no expiry is trusted for
        :attr:`_assumed_ttl` seconds from when it was stored, so an immortal
        token cannot pin a source to a credential forever.

        Repeated resource-server rejections escalate: at
        :data:`MAX_REJECTIONS` an ``authorization_code`` source is marked for
        re-authorization, and a ``client_credentials`` source has its row
        dropped so the next attempt starts clean.  Until then the rejected
        access token has already been cleared by the rejection itself, so the
        next attempt refreshes or re-fetches.

        Raises:
            ReauthorizationRequired: When the stored grant is unusable and
                cannot be refreshed.
            TransientOAuthError: When the authorization server could not be
                reached or answered with a retryable error.
        """
        store = self._store_call(self._store_for, declaration)
        with self._token_lock(declaration.source):
            # Another process sharing the store may be refreshing the same
            # source; wait for it, then the row read below sees its result.
            release = self._store_call(store.acquire_refresh_lock, declaration.source)
            try:
                record = self._live_record(declaration, store, source_url)
                if record is not None:
                    return record
                if declaration.grant == "client_credentials":
                    return self._client_credentials(declaration, store, source_url)
            finally:
                release()
        raise ReauthorizationRequired(
            f"{declaration.source} has no usable token; authorize it"
        )

    def _live_record(
        self,
        declaration: OAuthClientConfig,
        store: TokenStore,
        source_url: str,
    ) -> TokenRecord | None:
        """Return a stored record that can be used as it stands.

        Args:
            declaration: The declaration being resolved.
            store: The store holding the row.

        Returns:
            A record to hand out, or ``None`` when one must be fetched.

        Raises:
            ReauthorizationRequired: When the row says a new authorization is
                needed, or repeated rejections exhaust the retry bound.
        """
        record = self._store_call(store.get, declaration.source)
        if record is not None and not self._record_compatible(
            declaration, record, source_url
        ):
            self._store_call(store.clear, declaration.source)
            record = None
        if record is not None and record.needs_reauth:
            if declaration.grant == "client_credentials":
                # No interactive path: a corrected secret is retried.
                pass
            elif self._credential_changed(declaration, record):
                # The verdict was reached with the old credential.  Clearing
                # the latch (not the row) lets the refresh token recover it.
                self._store_call(store.clear_latch, record.source_id)
                record = self._store_call(store.get, record.source_id)
            else:
                raise ReauthorizationRequired(
                    record.error or f"{declaration.source} requires re-authorization"
                )
        if record is None:
            return None
        if record.rejections >= MAX_REJECTIONS:
            return self._escalate_rejections(declaration, store, record, source_url)
        if record.access_token and not record.is_expiring(
            self._clock(), self._skew, assumed_ttl=self._assumed_ttl
        ):
            return record
        if record.refresh_token:
            return self._refresh(declaration, store, record, source_url)
        return None

    def _escalate_rejections(
        self,
        declaration: OAuthClientConfig,
        store: TokenStore,
        record: TokenRecord,
        source_url: str,
    ) -> TokenRecord:
        """Stop retrying a credential the resource server keeps rejecting.

        Args:
            declaration: The declaration being resolved.
            store: The store holding the row.
            record: The row, whose counter reached :data:`MAX_REJECTIONS`.

        Returns:
            A freshly fetched record, for ``client_credentials`` only.

        Raises:
            ReauthorizationRequired: For ``authorization_code``, which has an
                interactive path the operator can use.
        """
        if declaration.grant == "client_credentials":
            # No interactive authorization exists for this grant, so the only
            # escape is a clean row: the operator may have fixed the secret.
            self._store_call(store.clear, declaration.source)
            return self._client_credentials(declaration, store, source_url)
        message = (
            f"{declaration.source} was rejected by the MCP server "
            f"{record.rejections} times; authorize it again"
        )
        self._store_call(
            store.mark_reauth,
            declaration.source,
            message,
            issuer=self._issuer_for(declaration),
            client_id=declaration.client_id,
            **self._credential_fields(declaration),
        )
        raise ReauthorizationRequired(message)

    def _credential_fields(self, declaration: OAuthClientConfig) -> dict[str, Any]:
        """Return the declaration fields a latch records with its verdict."""
        return {
            "grant": declaration.grant,
            "token_endpoint": declaration.token_endpoint or "",
            "requested_scopes": tuple(declaration.scopes),
            "secret_digest": _secret_digest(declaration),
        }

    def _record_compatible(
        self,
        declaration: OAuthClientConfig,
        record: TokenRecord,
        source_url: str,
    ) -> bool:
        """Whether *record* belongs to this client and this source.

        These fields identify the credential's audience: a change means the
        old token must not be presented at all, so a mismatch reads as no
        record.
        """
        if record.issuer != self._issuer_for(declaration):
            return False
        if record.client_id and record.client_id != declaration.client_id:
            return False
        # An empty column means the row predates it; only a recorded value is
        # compared, so an upgrade does not discard a live token.
        if record.source_url and record.source_url != source_url:
            return False
        return not (record.grant and record.grant != declaration.grant)

    def _credential_changed(
        self, declaration: OAuthClientConfig, record: TokenRecord
    ) -> bool:
        """Whether a credential-affecting setting changed since *record*.

        The set is the token endpoint, the requested scopes and the client
        credential (env-var name, authentication method and a digest of the
        secret).  A change to one of these makes a stored re-authorization
        verdict stale, so the latch is cleared and the refresh token is
        retried.  An unrelated edit cannot reach here: the source's other
        fields are not part of the declaration.
        """
        if record.token_endpoint and record.token_endpoint != (
            declaration.token_endpoint or ""
        ):
            return True
        if record.requested_scopes and tuple(record.requested_scopes) != tuple(
            declaration.scopes
        ):
            return True
        return bool(
            record.secret_digest
            and not _secret_digest_matches(declaration, record.secret_digest)
        )

    def _client_credentials(
        self, declaration: OAuthClientConfig, store: TokenStore, source_url: str
    ) -> TokenRecord:
        """Fetch a client-credentials token and persist it."""
        fields = {"grant_type": "client_credentials"}
        if declaration.scopes:
            fields["scope"] = " ".join(declaration.scopes)
        return self._exchange(declaration, store, fields, source_url=source_url)

    def _refresh(
        self,
        declaration: OAuthClientConfig,
        store: TokenStore,
        record: TokenRecord,
        source_url: str,
    ) -> TokenRecord:
        """Refresh *record* and persist the result."""
        fields = {
            "grant_type": "refresh_token",
            "refresh_token": record.refresh_token or "",
        }
        return self._exchange(
            declaration,
            store,
            fields,
            source_url=source_url,
            fallback_refresh=record.refresh_token,
        )

    def _exchange(
        self,
        declaration: OAuthClientConfig,
        store: TokenStore,
        fields: dict[str, str],
        *,
        source_url: str = "",
        fallback_refresh: str | None = None,
    ) -> TokenRecord:
        """POST one token request, persist the result, and map failures.

        A 404/410 from an endpoint that came from discovery is not treated as
        a rejection: the endpoint may have moved, so the cached metadata is
        dropped, the endpoint re-discovered and the request retried once.  A
        second 404 is definitive.  An endpoint the operator set explicitly is
        never re-discovered, because nothing else could replace it.
        """
        try:
            payload = self._request_token(declaration, fields)
        except OAuthHTTPError as exc:
            payload = self._retry_moved_endpoint(declaration, store, fields, exc)
        record = self._record_from_payload(
            declaration,
            payload,
            source_url=source_url,
            fallback_refresh=fallback_refresh,
        )
        return self._store_call(store.put, record)

    def _retry_moved_endpoint(
        self,
        declaration: OAuthClientConfig,
        store: TokenStore,
        fields: dict[str, str],
        exc: OAuthHTTPError,
    ) -> dict[str, Any]:
        """Re-discover and retry once when a discovered endpoint has moved.

        Args:
            declaration: The declaration being resolved.
            store: The store the failure would be recorded on.
            fields: The token-request form body.
            exc: The first failure.

        Returns:
            The retried response.

        Raises:
            ReauthorizationRequired: If the retried request is also rejected.
            TransientOAuthError: If the retried request fails transiently.
        """
        if exc.status not in _MOVED_ENDPOINT_STATUSES or declaration.token_endpoint:
            self._record_failure(declaration, store, exc, fields=fields)
        if not self._forget_metadata(declaration):
            # Nothing was cached, so discovery produced this endpoint and a
            # retry would ask the same server the same question.
            self._record_failure(declaration, store, exc, fields=fields)
        logger.info(
            f"OAuth endpoint for {declaration.source} answered "
            f"HTTP {exc.status}; re-discovering once"
        )
        try:
            return self._request_token(declaration, fields)
        except OAuthHTTPError as retry_exc:
            self._record_failure(declaration, store, retry_exc, fields=fields)

    def _forget_metadata(self, declaration: OAuthClientConfig) -> bool:
        """Drop the cached authorization-server metadata for a declaration.

        Args:
            declaration: The declaration whose metadata is stale.

        Returns:
            ``True`` when something was cached and is now gone.
        """
        key = declaration.discovery_url or declaration.issuer or ""
        with self._lock:
            return self._metadata.pop(key, None) is not None

    def _request_token(
        self, declaration: OAuthClientConfig, fields: dict[str, str]
    ) -> dict[str, Any]:
        """Perform the token-endpoint request."""
        endpoint = self._token_endpoint(declaration)
        headers: dict[str, str] = {}
        body = dict(fields)
        secret = declaration.client_secret or ""
        if declaration.client_auth == "client_secret_basic":
            headers["Authorization"] = f"Basic {_basic_credential(declaration)}"
        else:
            body["client_id"] = declaration.client_id
            body["client_secret"] = secret
        payload = self._transport.post_form(endpoint, body, headers, self._timeout)
        if not isinstance(payload, dict):
            raise OAuthClientError("OAuth token endpoint returned a non-object body")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise OAuthClientError("OAuth token endpoint returned no access_token")
        return payload

    def _record_from_payload(
        self,
        declaration: OAuthClientConfig,
        payload: Mapping[str, Any],
        *,
        source_url: str = "",
        fallback_refresh: str | None,
    ) -> TokenRecord:
        """Build a :class:`TokenRecord` from a token response."""
        now = self._clock()
        expires_in = _as_int(payload.get("expires_in"))
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            refresh_token = fallback_refresh
        scope = payload.get("scope")
        scopes = (
            tuple(scope.split())
            if isinstance(scope, str) and scope.strip()
            else declaration.scopes
        )
        token_type = payload.get("token_type")
        return TokenRecord(
            source_id=declaration.source,
            issuer=self._issuer_for(declaration),
            client_id=declaration.client_id,
            scopes=scopes,
            access_token=_as_token(payload.get("access_token")),
            refresh_token=refresh_token,
            token_type=(
                token_type if isinstance(token_type, str) and token_type else "Bearer"
            ),
            expires_at=(
                now + timedelta(seconds=expires_in) if expires_in is not None else None
            ),
            updated_at=now,
            source_url=source_url,
            grant=declaration.grant,
            token_endpoint=declaration.token_endpoint or "",
            requested_scopes=tuple(declaration.scopes),
            secret_digest=_secret_digest(declaration),
        )

    def _record_failure(
        self,
        declaration: OAuthClientConfig,
        store: TokenStore,
        exc: OAuthHTTPError,
        *,
        fields: Mapping[str, str] | None = None,
    ) -> NoReturn:
        """Classify a token-endpoint rejection and raise the matching error.

        A redirect is refused by the transport and arrives here as its 3xx
        status; it is transient, because the endpoint may have moved and the
        stored grant is still good.

        Raises:
            TransientOAuthError: For 3xx, 429 and 5xx responses.
            ReauthorizationRequired: For every other rejection.  The store
                is marked so a later connect does not retry the rejected
                grant silently, except for ``client_credentials``, whose
                next connect fetches a new token with the current secret.
        """
        message = self._error_message(declaration, exc, fields=fields)
        # 404/410 on an endpoint the operator set explicitly is a wrong
        # endpoint, not a verdict on the grant: treating it as definitive
        # would latch an authorization-code source that a config fix should
        # recover.
        undeclared_move = (
            exc.status in _MOVED_ENDPOINT_STATUSES and declaration.token_endpoint
        )
        if (
            300 <= exc.status < 400
            or exc.status in (408, 425, 429)
            or exc.status >= 500
            or undeclared_move
        ):
            raise TransientOAuthError(message) from None
        if declaration.grant == "client_credentials":
            # No interactive grant to wait for: the operator can fix the
            # client secret and the next connect tries it.  Clearing the row
            # keeps the corrected attempt from hitting a stale error.
            self._store_call(store.clear, declaration.source)
            raise ReauthorizationRequired(message) from None
        self._store_call(
            store.mark_reauth,
            declaration.source,
            message,
            issuer=self._issuer_for(declaration),
            client_id=declaration.client_id,
            **self._credential_fields(declaration),
        )
        raise ReauthorizationRequired(message) from None

    def _error_message(
        self,
        declaration: OAuthClientConfig,
        exc: OAuthHTTPError,
        *,
        fields: Mapping[str, str] | None = None,
    ) -> str:
        """Build a redacted error message from an OAuth error response.

        The replayed refresh token is redacted as well as the client secret:
        an authorization server that echoes the request in
        ``error_description`` must not write it into the source record.
        """
        code = exc.error or f"HTTP {exc.status}"
        detail = f": {exc.description}" if exc.description else ""
        secrets = [declaration.client_secret]
        if declaration.client_auth == "client_secret_basic":
            # The Authorization header carries base64, not the raw secret.
            secrets.append(_basic_credential(declaration))
        if fields:
            secrets.append(fields.get("refresh_token"))
        return redact(f"OAuth token request rejected ({code}{detail})", *secrets)

    # -- authorization code + PKCE ----------------------------------------

    def start_authorization(
        self,
        source: str | ToolSource,
        redirect_uri: str,
    ) -> AuthorizationRequest:
        """Begin an authorization-code + PKCE flow.

        Args:
            source: The source selector, or the parsed MCP source.
            redirect_uri: URI the authorization server will redirect to.
                It must appear verbatim in the declaration's
                ``redirect_uris``.

        Returns:
            ``{"authorization_url", "state", "expires_at"}``.  The PKCE
            verifier is stored with the state and never returned.

        Raises:
            OAuthClientError: If the declaration uses client credentials,
                no declaration selects *source*, or *redirect_uri* is not in
                the declaration's ``redirect_uris``.
            TransientOAuthError: If the authorization endpoint cannot be
                discovered.
        """
        declaration = self._resolve_declaration(source)
        source_url = "" if isinstance(source, str) else _source_url(source)
        if declaration.grant != "authorization_code":
            raise OAuthClientError(
                f"{declaration.source} uses the client-credentials grant; "
                "no interactive authorization is available"
            )
        _require_allowed_redirect(declaration, redirect_uri)
        endpoint = self._authorization_endpoint(declaration)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        state = secrets.token_urlsafe(32)
        now = self._clock()
        expires_at = now + timedelta(seconds=AUTHORIZATION_STATE_TTL)
        control = self._control_store()
        # Drop requests whose callback never came, so an abandoned flow does
        # not leave its PKCE verifier on disk for the life of the store.
        control.purge_expired_pending(now)
        control.put_pending(
            PendingAuthorization(
                state=state,
                source_id=declaration.source,
                code_verifier=verifier,
                redirect_uri=redirect_uri,
                created_at=now,
                expires_at=expires_at,
                source_url=source_url,
            )
        )
        params = {
            "response_type": "code",
            "client_id": declaration.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if declaration.scopes:
            params["scope"] = " ".join(declaration.scopes)
        separator = "&" if "?" in endpoint else "?"
        request = AuthorizationRequest(
            authorization_url=f"{endpoint}{separator}{urlencode(params)}",
            state=state,
            expires_at=expires_at.isoformat(),
        )
        return request

    def complete_authorization(self, code: str, state: str) -> dict[str, Any]:
        """Finish an authorization-code flow and store the token.

        Args:
            code: The authorization code from the callback.
            state: The opaque state returned by
                :meth:`start_authorization`.

        Returns:
            A public token view with no token material.

        Raises:
            OAuthStateError: If the state is unknown, already used or
                expired, or its redirect URI is no longer allowlisted.
            ReauthorizationRequired: If the authorization server rejects the
                code or the client.
            TransientOAuthError: On a retryable server or transport error.

        The pending request is consumed before the exchange, so a transient
        failure means the operator must start a new authorization: an
        authorization code is single-use, and a second exchange of the same
        code would be rejected anyway.
        """
        pending = self._control_store().pop_pending(state)
        if pending is None:
            raise OAuthStateError("unknown or already used authorization state")
        if pending.expires_at <= self._clock():
            raise OAuthStateError("authorization request expired; start again")
        declaration = self.declaration_by_selector(pending.source_id)
        if declaration is None:
            raise OAuthStateError(
                f"no OAuth declaration for {pending.source_id!r}; reload the config"
            )
        if pending.redirect_uri not in declaration.redirect_uris:
            # The allowlist may have changed between start and completion.
            raise OAuthStateError(
                "the authorization request's redirect URI is no longer "
                "allowed; start again"
            )
        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.code_verifier,
        }
        store = self._store_call(self._store_for, declaration)
        with self._token_lock(declaration.source):
            # The exchange and the write must not interleave with a concurrent
            # access_token, in this process or another one sharing the store:
            # a refresh holding the old refresh token could otherwise write
            # after this completion and clobber the new grant.
            release = self._store_call(store.acquire_refresh_lock, declaration.source)
            try:
                record = self._exchange(
                    declaration, store, fields, source_url=pending.source_url
                )
            finally:
                release()
        return record.to_public_dict()

    def _resolve_declaration(self, source: str | ToolSource) -> OAuthClientConfig:
        """Return the declaration for a selector or a parsed source."""
        if isinstance(source, str):
            declaration = self.declaration_by_selector(source)
        else:
            declaration = self.declaration_for(source)
        if declaration is None:
            raise OAuthClientError("no OAuth declaration selects this source")
        return declaration

    # -- discovery ---------------------------------------------------------

    def _token_endpoint(self, declaration: OAuthClientConfig) -> str:
        """Return the token endpoint, discovering it when necessary."""
        if declaration.token_endpoint:
            return declaration.token_endpoint
        endpoint = self._metadata_for(declaration).get("token_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise OAuthClientError(
                "OAuth metadata has no token_endpoint; set token_endpoint explicitly"
            )
        return endpoint

    def _authorization_endpoint(self, declaration: OAuthClientConfig) -> str:
        """Return the authorization endpoint from discovery."""
        endpoint = self._metadata_for(declaration).get("authorization_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise OAuthClientError(
                "OAuth metadata has no authorization_endpoint; the "
                "authorization-code flow cannot start"
            )
        return endpoint

    def _metadata_for(self, declaration: OAuthClientConfig) -> dict[str, Any]:
        """Return and cache the authorization-server metadata.

        One document is kept per discovery URL or issuer for
        :attr:`metadata_ttl` seconds.  An expired entry is fetched again, so
        a metadata change at the authorization server takes effect without a
        restart.  When that fetch fails, the cached document is served for
        one more TTL and a warning is logged: an identity provider that is
        briefly unreachable must not stop a token that still works from being
        used.
        """
        key = declaration.discovery_url or declaration.issuer or ""
        with self._lock:
            cached = self._metadata.get(key)
        if cached is not None:
            document, fetched_at = cached
            if self._clock() - fetched_at < timedelta(seconds=self._metadata_ttl):
                return document
        if declaration.discovery_url:
            urls = (declaration.discovery_url,)
        elif declaration.issuer:
            urls = _metadata_url_candidates(declaration.issuer)
        else:  # pragma: no cover - config validation requires one
            raise OAuthClientError("no issuer or discovery_url to discover")
        for url in urls:
            try:
                document = self._transport.get_json(url, self._timeout)
            except (OAuthHTTPError, TransientOAuthError):
                continue
            usable = self._usable_metadata(declaration, document)
            if usable is not None:
                with self._lock:
                    self._metadata[key] = (usable, self._clock())
                return usable
        if cached is not None:
            logger.warning(
                f"OAuth metadata for {redact(key)} could not be refreshed; "
                "serving the cached document until the next attempt"
            )
            with self._lock:
                self._metadata[key] = (cached[0], self._clock())
            return cached[0]
        raise TransientOAuthError(
            f"could not discover OAuth metadata for {redact(key)}"
        )

    def _usable_metadata(
        self, declaration: OAuthClientConfig, document: Any
    ) -> dict[str, Any] | None:
        """Return *document* when it can drive a token exchange, else ``None``.

        RFC 8414 section 3.3 requires the document's ``issuer`` to equal the
        configured issuer; a bare ``discovery_url`` has no configured issuer
        to compare, so it must still name one.  Endpoints must be absolute
        ``http``/``https`` URLs, so a metadata document cannot point a token
        request at ``file:`` or a relative path.  A document missing a usable
        ``token_endpoint`` is not cached, so the next discovery candidate can
        still supply one.
        """
        if not isinstance(document, dict):
            return None
        issuer = document.get("issuer")
        if declaration.issuer:
            if issuer != declaration.issuer:
                logger.warning(
                    f"OAuth metadata for {redact(declaration.issuer)} names "
                    f"issuer {redact(str(issuer))}; ignoring it"
                )
                return None
        elif not isinstance(issuer, str) or not issuer:
            return None
        if not _is_http_url(document.get("token_endpoint")):
            return None
        endpoint = document.get("authorization_endpoint")
        if endpoint is not None and not _is_http_url(endpoint):
            return None
        return document

    def _issuer_for(self, declaration: OAuthClientConfig) -> str:
        """Return the issuer to record, without forcing discovery."""
        if declaration.issuer:
            return declaration.issuer
        if declaration.discovery_url:
            parsed = urlsplit(declaration.discovery_url)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
            return declaration.discovery_url
        return ""  # pragma: no cover - config validation requires one

    # -- stores ------------------------------------------------------------

    def reconcile_stored_tokens(self) -> tuple[str, ...]:
        """Delete token rows for declarations this configuration no longer has.

        The default store records which selector was declared against which
        store path.  A recorded selector that is not declared now has its row
        deleted from the store it named.  That covers removing one entry and
        removing the whole ``oauth.clients`` section, including while the
        server was stopped: the next start rebuilds the manifest and deletes
        what it recorded before.  Startup calls this once; a declaration
        replacement calls it again.

        Only rows this manifest recorded are touched, so a row written by
        another configuration that shares the database is left alone.

        Returns:
            The selectors whose rows were deleted, sorted.
        """
        current = {
            declaration.source: declaration.store_path
            for declaration in self._declarations
        }
        store = self._manifest_store()
        if store is None:
            return ()
        try:
            previous = store.declared_targets()
            removed: list[str] = []
            for selector, path in sorted(previous.items()):
                if selector in current:
                    continue
                target = store if path == store.path else self._open_store(path)
                if target.clear(selector):
                    removed.append(selector)
            if previous != current:
                store.record_declared_targets(current)
        except (sqlite3.Error, OSError, json.JSONDecodeError, RuntimeError) as exc:
            raise OAuthStoreError(
                f"token store unavailable ({type(exc).__name__}); "
                "the stored grants were kept"
            ) from exc
        if removed:
            logger.info(
                "Removed OAuth token rows for declarations that left the "
                f"configuration: {', '.join(removed)}"
            )
        return tuple(removed)

    def _manifest_store(self) -> TokenStore | None:
        """Return the store that records declared targets, or ``None``.

        The manifest is only needed when there is something to record or a
        database that may already hold a manifest.  A configuration that has
        never used OAuth therefore does not create the default database.
        """
        if self._store is not None:
            return self._store
        if not self._declarations and not os.path.exists(self._default_token_store):
            return None
        return self._open_store(self._default_token_store)

    def _store_call(
        self, operation: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Run a store operation, mapping an I/O failure to a typed error.

        The grant is kept: a locked database, a missing directory or a corrupt
        ``scopes`` cell says nothing about the credential, so the next attempt
        may succeed.  ``RuntimeError`` covers ``Path.home`` when the process
        has no resolvable home directory.
        """
        try:
            return operation(*args, **kwargs)
        except (sqlite3.Error, OSError, json.JSONDecodeError, RuntimeError) as exc:
            raise OAuthStoreError(
                f"token store unavailable ({type(exc).__name__}); "
                "the stored grant was kept"
            ) from exc

    def _store_for(self, declaration: OAuthClientConfig) -> TokenStore:
        """Return the store for *declaration*."""
        if self._store is not None:
            return self._store
        return self._open_store(declaration.store_path)

    def _control_store(self) -> TokenStore:
        """Return the store that holds pending authorization state."""
        if self._store is not None:
            return self._store
        return self._open_store(self._default_token_store)

    def _open_store(self, path: str) -> TokenStore:
        """Open and cache one store by path."""
        with self._lock:
            store = self._stores.get(path)
            if store is None:
                store = TokenStore(path, clock=self._clock)
                self._stores[path] = store
            return store

    def close(self) -> None:
        """Close every store this provider opened.

        An injected store is left open: the caller owns it.
        """
        with self._lock:
            stores = list(self._stores.values())
            self._stores.clear()
        for store in stores:
            store.close()


def _require_allowed_redirect(
    declaration: OAuthClientConfig, redirect_uri: str
) -> None:
    """Refuse a callback URL the declaration does not list verbatim.

    Raises:
        OAuthClientError: If *redirect_uri* is not in ``redirect_uris``.
    """
    if redirect_uri in declaration.redirect_uris:
        return
    if not declaration.redirect_uris:
        raise OAuthClientError(
            f"{declaration.source} lists no redirect_uris; add the callback "
            "URL to its oauth.clients entry"
        )
    raise OAuthClientError(
        f"redirect URI {redact(redirect_uri)} is not in the redirect_uris of "
        f"{declaration.source}"
    )


def _as_int(value: Any) -> int | None:
    """Return *value* as a non-negative whole number of seconds, or ``None``.

    A JSON float is accepted and rounded down: several identity providers
    send ``"expires_in": 3599.5`` or ``3600.0``.  A non-finite, negative or
    boolean value is treated as absent, which is the same as a response that
    stated no expiry at all.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0:
            return None
        return math.floor(value)
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _as_token(value: Any) -> str | None:
    """Return *value* when it is a non-empty token string."""
    return value if isinstance(value, str) and value else None
