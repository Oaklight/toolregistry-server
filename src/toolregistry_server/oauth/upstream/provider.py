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
import re
import secrets
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn, Protocol
from urllib.parse import urlencode, urlsplit

from toolregistry.config import ToolSource

from ..verifier import _metadata_url_candidates
from .config import (
    DEFAULT_EXPIRY_SKEW,
    DEFAULT_HTTP_TIMEOUT,
    OAuthClientConfig,
    default_token_store_path,
    selector_matches_source,
)
from .store import PendingAuthorization, TokenRecord, TokenStore, utcnow

__all__ = [
    "AuthorizationRequest",
    "OAuthClientError",
    "OAuthClientProvider",
    "OAuthHTTPError",
    "OAuthStateError",
    "OAuthTransport",
    "ReauthorizationRequired",
    "TransientOAuthError",
    "UrllibTransport",
    "redact",
]

#: How long an authorization request may wait for its callback.
AUTHORIZATION_STATE_TTL = 600.0

#: ``scheme://user:password@`` in a URL, inside arbitrary text.
_URL_USERINFO = re.compile(r"\b([A-Za-z][A-Za-z0-9+.\-]*://)[^/\s]+@")

#: Credential-shaped query or form parameters in arbitrary text.
_TOKEN_PARAM = re.compile(
    r"(?i)\b(access_token|refresh_token|id_token|client_secret|code)=([^&\s]+)"
)


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
    :mod:`toolregistry_server.oauth.verifier`, and screening both is tracked
    separately.
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
        if secret:
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
    ) -> None:
        self._declarations = tuple(declarations)
        self._store = store
        self._default_token_store = default_token_store or default_token_store_path()
        self._transport = transport or UrllibTransport()
        self._clock = clock or utcnow
        self._timeout = timeout
        self._skew = skew
        self._lock = threading.RLock()
        self._stores: dict[str, TokenStore] = {}
        self._token_locks: dict[str, threading.Lock] = {}
        self._metadata: dict[str, dict[str, Any]] = {}

    # -- declaration lookup ------------------------------------------------

    @property
    def declarations(self) -> tuple[OAuthClientConfig, ...]:
        """Return the current declarations."""
        with self._lock:
            return self._declarations

    def replace_declarations(self, declarations: tuple[OAuthClientConfig, ...]) -> None:
        """Replace the declarations after a config reload."""
        with self._lock:
            self._declarations = tuple(declarations)

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
        record = self.access_token(declaration)
        if not record.access_token:
            raise ReauthorizationRequired(
                f"{declaration.source} has no access token; authorize it"
            )
        token_type = record.token_type or "Bearer"
        return {"Authorization": f"{token_type} {record.access_token}"}

    def access_token(self, declaration: OAuthClientConfig) -> TokenRecord:
        """Return a usable token, refreshing or fetching one as needed.

        An expired token is never returned.  A stored ``needs_reauth`` flag
        short-circuits every attempt until a new authorization succeeds, or,
        for the ``client_credentials`` grant, until the next attempt: that
        grant has no interactive authorization, so a corrected client secret
        gets a fresh fetch instead of a permanent failure.  A stored record
        minted for another ``issuer`` or ``client_id`` is ignored.

        Raises:
            ReauthorizationRequired: When the stored grant is unusable and
                cannot be refreshed.
            TransientOAuthError: When the authorization server could not be
                reached or answered with a retryable error.
        """
        store = self._store_for(declaration)
        with self._token_lock(declaration.source):
            record = store.get(declaration.source)
            if record is not None and not self._record_matches(declaration, record):
                store.clear(declaration.source)
                record = None
            now = self._clock()
            if record is not None:
                if record.needs_reauth and declaration.grant != "client_credentials":
                    raise ReauthorizationRequired(
                        record.error
                        or f"{declaration.source} requires re-authorization"
                    )
                if record.access_token and not record.is_expiring(now, self._skew):
                    return record
                if record.refresh_token:
                    return self._refresh(declaration, store, record)
            if declaration.grant == "client_credentials":
                return self._client_credentials(declaration, store)
        raise ReauthorizationRequired(
            f"{declaration.source} has no usable token; authorize it"
        )

    def _record_matches(
        self, declaration: OAuthClientConfig, record: TokenRecord
    ) -> bool:
        """Whether *record* was minted for the declaration's current client.

        The store is keyed by the selector, which survives an ``issuer`` or
        ``client_id`` edit.  A token minted for the old authorization server
        must not be presented to the new one, so a mismatch reads as no
        record at all.
        """
        if record.issuer != self._issuer_for(declaration):
            return False
        return not record.client_id or record.client_id == declaration.client_id

    def _client_credentials(
        self, declaration: OAuthClientConfig, store: TokenStore
    ) -> TokenRecord:
        """Fetch a client-credentials token and persist it."""
        fields = {"grant_type": "client_credentials"}
        if declaration.scopes:
            fields["scope"] = " ".join(declaration.scopes)
        return self._exchange(declaration, store, fields)

    def _refresh(
        self,
        declaration: OAuthClientConfig,
        store: TokenStore,
        record: TokenRecord,
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
            fallback_refresh=record.refresh_token,
        )

    def _exchange(
        self,
        declaration: OAuthClientConfig,
        store: TokenStore,
        fields: dict[str, str],
        *,
        fallback_refresh: str | None = None,
    ) -> TokenRecord:
        """POST one token request, persist the result, and map failures."""
        try:
            payload = self._request_token(declaration, fields)
        except OAuthHTTPError as exc:
            self._record_failure(declaration, store, exc, fields=fields)
        record = self._record_from_payload(
            declaration, payload, fallback_refresh=fallback_refresh
        )
        return store.put(record)

    def _request_token(
        self, declaration: OAuthClientConfig, fields: dict[str, str]
    ) -> dict[str, Any]:
        """Perform the token-endpoint request."""
        endpoint = self._token_endpoint(declaration)
        headers: dict[str, str] = {}
        body = dict(fields)
        secret = declaration.client_secret or ""
        if declaration.client_auth == "client_secret_basic":
            credential = base64.b64encode(
                f"{declaration.client_id}:{secret}".encode()
            ).decode("ascii")
            headers["Authorization"] = f"Basic {credential}"
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
        if 300 <= exc.status < 400 or exc.status == 429 or exc.status >= 500:
            raise TransientOAuthError(message) from None
        if declaration.grant == "client_credentials":
            # No interactive grant to wait for: the operator can fix the
            # client secret and the next connect tries it.  Clearing the row
            # keeps the corrected attempt from hitting a stale error.
            store.clear(declaration.source)
            raise ReauthorizationRequired(message) from None
        store.mark_reauth(
            declaration.source,
            message,
            issuer=self._issuer_for(declaration),
            client_id=declaration.client_id,
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
                It must match the one the panel's callback uses.

        Returns:
            ``{"authorization_url", "state", "expires_at"}``.  The PKCE
            verifier is stored with the state and never returned.

        Raises:
            OAuthClientError: If the declaration uses client credentials, or
                no declaration selects *source*.
            TransientOAuthError: If the authorization endpoint cannot be
                discovered.
        """
        declaration = self._resolve_declaration(source)
        if declaration.grant != "authorization_code":
            raise OAuthClientError(
                f"{declaration.source} uses the client-credentials grant; "
                "no interactive authorization is available"
            )
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
                expired.
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
        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.code_verifier,
        }
        record = self._exchange(declaration, self._store_for(declaration), fields)
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

        The cache has no TTL: one document is kept per discovery URL or
        issuer for the life of the provider, so a metadata change at the
        authorization server is only seen after a restart or a config reload
        that creates a new provider.  Discovery is not on the hot path, so a
        TTL can be added later without changing this signature.
        """
        key = declaration.discovery_url or declaration.issuer or ""
        with self._lock:
            cached = self._metadata.get(key)
        if cached is not None:
            return cached
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
            if isinstance(document, dict):
                with self._lock:
                    self._metadata[key] = document
                return document
        raise TransientOAuthError(
            f"could not discover OAuth metadata for {redact(key)}"
        )

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


def _as_int(value: Any) -> int | None:
    """Return *value* as a non-negative integer, or ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
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
