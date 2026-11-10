"""OAuth 2.0 resource-server verification.

The server accepts bearer tokens minted by an external authorization server.
It verifies the token locally against the issuer's JWKS: signature,
algorithm allowlist, exact ``iss``, ``aud``/RFC 8707 ``resource``, and
``exp``/``nbf`` with a small leeway.  Nothing is introspected at request
time unless :attr:`OAuthSettings.introspection_url` is configured; with it,
an RFC 7662 ``active: false`` answer rejects a token so a revocation takes
effect within the short cache TTL.  No token is ever logged.

The default scope mapping is least privilege: a token whose scopes are not
listed in :attr:`OAuthSettings.scope_map` maps to an empty
:class:`~toolregistry_server.keystore.ScopeSpec`, which allows no tool.  A
deployment must map each scope it wants to grant explicitly; a scope may
also allow or deny individual tools through
:attr:`OAuthSettings.scope_tools`.  The scopes are read from the ``scope``
claim, falling back to the ``scp`` claim some identity platforms use.

The resolved :class:`~toolregistry_server.keystore.Principal` keeps the
OAuth client (``client_id`` or ``azp``) apart from the subject (``sub``), so
two clients acting for one user have separate scopes, concurrency budgets
and execution-log rows.  A client's in-flight limit comes from the
:attr:`OAuthSettings.concurrency_claim` claim or the static
:attr:`OAuthSettings.client_max_concurrency` table.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any
from urllib.parse import urlencode

from .._vendor.structlog import get_logger
from ..keystore import Principal, PrincipalResolver, ScopeSpec

logger = get_logger()

#: Signature algorithms the verifier accepts.  Symmetric algorithms are
#: deliberately absent, and ``none`` is rejected by PyJWT.
DEFAULT_ALGORITHMS: tuple[str, ...] = ("RS256", "ES256")

#: Default clock leeway for ``exp`` and ``nbf``, in seconds.
DEFAULT_LEEWAY: float = 60.0

#: Default JWKS cache lifetime, in seconds.
DEFAULT_CACHE_TTL: float = 300.0

#: Default JWKS request timeout, in seconds.
DEFAULT_TIMEOUT: float = 5.0

#: Minimum interval between forced JWKS refetches, in seconds.
DEFAULT_REFRESH_COOLDOWN: float = 30.0

#: Default lifetime of one RFC 7662 introspection result, in seconds.  Short
#: on purpose: a revocation at the authorization server must take effect
#: promptly, and the value bounds how long a revoked token is still accepted.
DEFAULT_INTROSPECTION_TTL: float = 5.0

#: Claim that may carry a client's in-flight call limit.
DEFAULT_CONCURRENCY_CLAIM: str = "max_concurrency"


def _positive_int(value: Any, label: str) -> int:
    """Return *value* as an integer of at least one.

    Args:
        value: The configured value.
        label: Name used in the error message.

    Returns:
        The validated integer.

    Raises:
        ValueError: If *value* is not an integer of at least one.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer, got {value!r}")
    return value


@dataclass(frozen=True)
class OAuthSettings:
    """Configuration for resource-server token verification.

    Attributes:
        issuer: Exact ``iss`` value the token must carry.  It is compared
            verbatim, because some authorization servers publish an ``iss``
            with a trailing slash.  A trailing slash is stripped only when
            composing discovery and fallback URLs.
        audience: The advertised resource URL (or another identifier) the
            token's ``aud`` or RFC 8707 ``resource`` must contain.
        jwks_uri: JWKS endpoint override.  When ``None``, the verifier uses
            ``<issuer>/.well-known/jwks.json``.
        scope_map: Explicit mapping from a token scope string to the
            namespace patterns it grants.  Unmapped scopes grant nothing.
        leeway: Allowed clock skew for ``exp`` and ``nbf``, in seconds.
        cache_ttl: How long a fetched JWKS stays usable, in seconds.
        timeout: JWKS request timeout, in seconds.
        algorithms: Accepted signature algorithms.
        refresh_cooldown: Minimum interval between forced JWKS refetches,
            in seconds.  A token with an unknown ``kid`` cannot drive one
            outbound request per call.
        authorization_url: Authorization endpoint override for the OpenAPI
            ``authorizationCode`` flow.  ``None`` reads it from the issuer's
            metadata.
        token_url: Token endpoint override for the same flow.
        refresh_url: Optional refresh endpoint override; omitted from the
            flow unless set.
        scopes_supported: Scopes advertised in the protected-resource
            metadata.  ``None`` and an empty tuple both omit the field; when
            an issuer is configured the field is omitted unless this is set
            explicitly, because an external authorization server may reject
            the generic ``mcp`` scope.
        scope_tools: Per-scope tool overrides.  The outer key is a token
            scope, the inner key a full tool name, and the value whether the
            scope allows it.  A deny wins when two granted scopes disagree.
        introspection_url: RFC 7662 introspection endpoint.  When ``None``
            the verifier only checks the token locally.  When set, an
            ``active: false`` answer rejects the token and an endpoint
            failure rejects it too unless *introspection_optional* is set.
        introspection_client_id: Client identifier for the introspection
            endpoint's HTTP Basic credential.
        introspection_client_secret: Client secret for the same credential.
        introspection_cache_ttl: How long one introspection result is
            reused, in seconds.  This bounds how long a token revoked at the
            authorization server is still accepted.
        introspection_optional: Treat an introspection endpoint failure as a
            reason to fall back to local verification instead of rejecting
            the token.  The default fails closed.
        concurrency_claim: Claim that may carry a client's in-flight call
            limit.  A non-positive or non-integer value is ignored.
        client_max_concurrency: Static per-client limits keyed by the
            ``client_id`` or ``azp`` claim.  A valid concurrency claim wins
            over this table.
    """

    issuer: str
    audience: str
    jwks_uri: str | None = None
    scope_map: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    leeway: float = DEFAULT_LEEWAY
    cache_ttl: float = DEFAULT_CACHE_TTL
    timeout: float = DEFAULT_TIMEOUT
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    refresh_cooldown: float = DEFAULT_REFRESH_COOLDOWN
    scopes_supported: tuple[str, ...] | None = None
    authorization_url: str | None = None
    token_url: str | None = None
    refresh_url: str | None = None
    scope_tools: Mapping[str, Mapping[str, bool]] = field(default_factory=dict)
    introspection_url: str | None = None
    introspection_client_id: str | None = None
    introspection_client_secret: str | None = None
    introspection_cache_ttl: float = DEFAULT_INTROSPECTION_TTL
    introspection_optional: bool = False
    concurrency_claim: str = DEFAULT_CONCURRENCY_CLAIM
    client_max_concurrency: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.issuer.strip():
            raise ValueError("OAuth issuer must not be empty")
        if not self.audience.strip():
            raise ValueError("OAuth audience must not be empty")
        object.__setattr__(
            self,
            "scope_map",
            MappingProxyType(
                {
                    str(scope): tuple(str(name) for name in namespaces)
                    for scope, namespaces in dict(self.scope_map).items()
                }
            ),
        )
        object.__setattr__(
            self,
            "scope_tools",
            MappingProxyType(
                {
                    str(scope): MappingProxyType(
                        {
                            str(tool): bool(allowed)
                            for tool, allowed in dict(overrides).items()
                        }
                    )
                    for scope, overrides in dict(self.scope_tools).items()
                }
            ),
        )
        if not self.algorithms:
            raise ValueError("OAuth algorithm allowlist must not be empty")
        if self.scopes_supported is not None:
            object.__setattr__(
                self,
                "scopes_supported",
                tuple(str(scope) for scope in self.scopes_supported if str(scope)),
            )
        self._validate_introspection()
        object.__setattr__(
            self,
            "client_max_concurrency",
            MappingProxyType(
                {
                    str(client): _positive_int(
                        limit, f"oauth.client_concurrency[{client!r}]"
                    )
                    for client, limit in dict(self.client_max_concurrency).items()
                }
            ),
        )
        if not isinstance(self.concurrency_claim, str) or not (
            self.concurrency_claim.strip()
        ):
            raise ValueError("OAuth concurrency claim must not be empty")

    def _validate_introspection(self) -> None:
        """Reject an incomplete or negative introspection configuration."""
        if self.introspection_url is not None:
            if not self.introspection_url.strip():
                raise ValueError("OAuth introspection URL must not be empty")
            if (
                not (self.introspection_client_id or "").strip()
                or not (self.introspection_client_secret or "").strip()
            ):
                raise ValueError(
                    "OAuth introspection requires a client id and client secret"
                )
        if self.introspection_cache_ttl < 0:
            raise ValueError("OAuth introspection cache TTL must not be negative")

    @property
    def jwks_url(self) -> str:
        """Return the configured JWKS endpoint, or the legacy fallback.

        When :attr:`jwks_uri` is ``None`` the verifier first tries the
        issuer's RFC 8414 and OIDC metadata; this property only names the
        final fallback.
        """
        return self.jwks_uri or (f"{self.issuer.rstrip('/')}/.well-known/jwks.json")


def _metadata_url_candidates(issuer: str) -> tuple[str, ...]:
    """Return the authorization-server metadata URLs to try, in order.

    RFC 8414 inserts the well-known segment *before* the issuer path
    (``scheme://host/.well-known/oauth-authorization-server<issuer-path>``).
    Some deployments publish it after the issuer instead, which is the OIDC
    suffix form, so both are tried.  OIDC discovery uses the suffix form.
    Duplicates are removed while order is preserved.
    """
    from urllib.parse import urlsplit

    stripped = issuer.rstrip("/")
    parsed = urlsplit(stripped)
    candidates: list[str] = []
    if parsed.scheme and parsed.netloc and parsed.path:
        candidates.append(
            f"{parsed.scheme}://{parsed.netloc}"
            f"/.well-known/oauth-authorization-server{parsed.path}"
        )
    candidates.append(f"{stripped}/.well-known/oauth-authorization-server")
    candidates.append(f"{stripped}/.well-known/openid-configuration")
    seen: set[str] = set()
    unique: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return tuple(unique)


def _load_json(url: str, timeout: float) -> Any:
    """Fetch and decode one JSON document.

    Kept module-level so tests can patch ``urllib.request.urlopen`` once,
    and importing ``urllib`` lazily keeps a verifier that is never used from
    touching the network stack.

    Raises:
        Exception: Whatever the transport raises; callers decide whether a
            discovery document is optional.
    """
    from urllib.request import urlopen

    with urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _post_form(
    url: str,
    fields: Mapping[str, str],
    timeout: float,
    headers: Mapping[str, str],
) -> Any:
    """POST an ``application/x-www-form-urlencoded`` body and decode JSON.

    The caller owns every header, including the credential.  The request body
    is never logged; a failure is reported by its exception type only, so a
    token cannot reach the log through this path.

    Args:
        url: The endpoint.
        fields: Form fields.
        timeout: Request timeout, in seconds.
        headers: Request headers.

    Returns:
        The decoded JSON document.

    Raises:
        Exception: Whatever the transport or decoder raises.
    """
    import urllib.request

    request = urllib.request.Request(
        url,
        data=urlencode(dict(fields)).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


#: Cached answer for a token the endpoint could not confirm.  A failure is
#: cached for the TTL like any other answer, so an outage costs one probe per
#: TTL instead of one blocked request per call.
INTROSPECTION_FAILED = object()


class _IntrospectionCache:
    """A small, bounded RFC 7662 result cache.

    Only a SHA-256 digest of the token is kept, never the token itself.  A
    positive, a negative and a failed probe are cached for the same short
    TTL, which bounds both the request rate to the endpoint and how long a
    revocation stays invisible.  Caching the failure matters as much as the
    answers: without it an unreachable endpoint blocks every request for the
    full timeout, and a burst exhausts the worker threads.

    Args:
        ttl: Lifetime of one result, in seconds.  ``0`` disables caching.
        max_entries: Largest number of results retained.
    """

    def __init__(self, ttl: float, max_entries: int = 1024) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, digest: str) -> Any:
        """Return a cached answer, or ``None`` when it is absent or expired.

        Returns:
            ``True``, ``False``, :data:`INTROSPECTION_FAILED`, or ``None``.
        """
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                return None
            answer, expires = entry
            if time.monotonic() >= expires:
                self._entries.pop(digest, None)
                return None
            self._entries.move_to_end(digest)
            return answer

    def set(self, digest: str, answer: Any) -> None:
        """Cache one answer for the configured TTL."""
        if self._ttl <= 0:
            return
        with self._lock:
            self._entries[digest] = (answer, time.monotonic() + self._ttl)
            self._entries.move_to_end(digest)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


def iter_authorization_server_metadata(
    issuer: str, timeout: float
) -> Iterable[dict[str, Any]]:
    """Yield the issuer's authorization-server metadata documents.

    Tries the RFC 8414 form first (with a path-bearing issuer, the
    well-known segment precedes the issuer path), then the suffix form, then
    OIDC discovery.  Only documents that decode to an object are yielded; a
    transport or decoding failure is logged and the next candidate is tried.
    Callers pick the fields they need and stop iterating once satisfied.
    """
    for url in _metadata_url_candidates(issuer):
        try:
            document = _load_json(url, timeout)
        except Exception as exc:
            logger.info(f"OAuth metadata discovery from {url} failed: {exc}")
            continue
        if isinstance(document, dict):
            yield document


def fetch_authorization_server_metadata(
    issuer: str, timeout: float
) -> dict[str, Any] | None:
    """Return the first usable authorization-server metadata document."""
    return next(iter(iter_authorization_server_metadata(issuer, timeout)), None)


class _JwksCache:
    """A small in-memory JWKS cache with TTL and forced refetch on rotation."""

    def __init__(
        self,
        url: str,
        ttl: float,
        timeout: float,
        cooldown: float = DEFAULT_REFRESH_COOLDOWN,
    ) -> None:
        self._url = url
        self._ttl = ttl
        self._timeout = timeout
        self._cooldown = cooldown
        self._keys: dict[str, Any] = {}
        self._expires = 0.0
        self._last_attempt = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> None:
        """Fetch the JWKS document and replace the cached keys."""
        import jwt

        self._last_attempt = time.monotonic()
        document = _load_json(self._url, self._timeout)
        keys: dict[str, Any] = {}
        for jwk in document.get("keys", ()):
            kid = jwk.get("kid")
            if not kid:
                continue
            try:
                keys[str(kid)] = jwt.PyJWK(jwk).key
            except Exception:
                # A key this PyJWT cannot use must not poison the cache.
                continue
        self._keys = keys
        self._expires = time.monotonic() + self._ttl

    def key_for(self, kid: str | None) -> Any | None:
        """Return the verification key for *kid*, refetching when needed.

        An unknown ``kid`` may force one refetch, which is how a key rotation
        is picked up without waiting for the TTL.  Forced refetches are
        rate-limited: inside the cooldown a miss is answered from the cache,
        so a client cannot turn random ``kid`` values into one outbound
        request per call.
        """
        with self._lock:
            now = time.monotonic()
            fresh = bool(self._keys) and now < self._expires
            if fresh and kid is not None and kid in self._keys:
                return self._keys[kid]
            if fresh and kid is None:
                return next(iter(self._keys.values()), None)
            if now - self._last_attempt < self._cooldown:
                return self._keys.get(kid) if kid is not None else None
            try:
                self._fetch()
            except Exception as exc:
                # Never include the token: this path only sees the URL.
                logger.warning(f"JWKS fetch from {self._url} failed: {exc}")
                return self._keys.get(kid) if kid is not None else None
            if kid is None:
                return next(iter(self._keys.values()), None)
            return self._keys.get(kid)

    def warm(self) -> None:
        """Best-effort prefetch, used at startup."""
        with self._lock:
            try:
                self._fetch()
            except Exception as exc:
                # Do not let a failed startup prefetch suppress the first
                # real request's attempt; it records its own attempt time.
                self._last_attempt = 0.0
                logger.warning(f"JWKS prefetch from {self._url} failed: {exc}")


class OAuthTokenVerifier:
    """Verify a bearer token locally against the issuer's JWKS.

    Args:
        settings: The verification settings.

    Raises:
        ImportError: If PyJWT is not installed.
    """

    def __init__(self, settings: OAuthSettings) -> None:
        try:
            import jwt  # noqa: F401
        except ImportError as exc:  # pragma: no cover - dependency error
            raise ImportError(
                "OAuth token verification requires PyJWT. Install with: "
                "pip install 'toolregistry-server[gateway]'"
            ) from exc
        self._settings = settings
        self._cache: _JwksCache | None = None
        self._resolved_jwks_uri: str | None = None
        self._resolve_lock = threading.Lock()
        self._introspection = _IntrospectionCache(settings.introspection_cache_ttl)

    @property
    def settings(self) -> OAuthSettings:
        """The settings this verifier was built with."""
        return self._settings

    def _discover_jwks_uri(self) -> str | None:
        """Read ``jwks_uri`` from RFC 8414 or OIDC metadata.

        Returns ``None`` when neither document is available or carries a
        usable ``jwks_uri``.  Discovery failures are logged once and never
        reject a token by themselves; the caller falls back.
        """
        for document in iter_authorization_server_metadata(
            self._settings.issuer, self._settings.timeout
        ):
            jwks_uri = document.get("jwks_uri")
            if isinstance(jwks_uri, str) and jwks_uri.strip():
                return jwks_uri.strip()
        return None

    def _resolve_jwks_uri(self) -> str:
        """Return the JWKS endpoint, discovering it once."""
        if self._settings.jwks_uri:
            return self._settings.jwks_uri
        if self._resolved_jwks_uri is None:
            # ``jwks_url`` strips a trailing slash before composing the
            # fallback, so an issuer published with one (Auth0 does) does
            # not produce ``<issuer>//.well-known/jwks.json``.
            self._resolved_jwks_uri = (
                self._discover_jwks_uri() or self._settings.jwks_url
            )
        return self._resolved_jwks_uri

    def _cache_for(self) -> _JwksCache:
        """Return the JWKS cache, building it on first use."""
        if self._cache is None:
            with self._resolve_lock:
                if self._cache is None:
                    self._cache = _JwksCache(
                        self._resolve_jwks_uri(),
                        self._settings.cache_ttl,
                        self._settings.timeout,
                        self._settings.refresh_cooldown,
                    )
        return self._cache

    def warm(self) -> None:
        """Prefetch the JWKS.  Failures are logged, not raised."""
        self._cache_for().warm()

    def verify(self, token: str) -> dict[str, Any] | None:
        """Return the verified claims, or ``None`` when the token is rejected."""
        import jwt

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        if header.get("alg") not in self._settings.algorithms:
            return None
        kid = header.get("kid")
        key = self._cache_for().key_for(str(kid) if kid else None)
        if key is None:
            return None
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self._settings.algorithms),
                issuer=self._settings.issuer,
                leeway=timedelta(seconds=self._settings.leeway),
                options={"verify_aud": False, "require": ["exp", "iss"]},
            )
        except jwt.PyJWTError:
            return None
        if not self._audience_matches(claims):
            return None
        if self._settings.introspection_url is not None and not self._introspect(token):
            return None
        return claims

    def _introspect(self, token: str) -> bool:
        """Return whether the authorization server still accepts *token*.

        The local check ran first, so an introspection call only confirms
        that the token was not revoked or disabled after it was minted.  The
        answer is cached for :attr:`OAuthSettings.introspection_cache_ttl`;
        an endpoint failure rejects the token unless the deployment opted
        into the optional mode.

        Args:
            token: The presented access token.

        Returns:
            ``True`` when the token may be used.
        """
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = self._introspection.get(digest)
        if cached is INTROSPECTION_FAILED:
            return bool(self._settings.introspection_optional)
        if cached is not None:
            return bool(cached)
        try:
            active = self._call_introspection(token)
        except Exception as exc:
            # Never log the token or the form body.  The endpoint and the
            # exception type are enough to diagnose a connectivity problem.
            # The failure is cached for the TTL, so an outage costs one probe
            # per TTL rather than one blocked request per call.
            logger.warning(
                f"OAuth introspection at {self._settings.introspection_url} "
                f"failed: {type(exc).__name__}"
            )
            self._introspection.set(digest, INTROSPECTION_FAILED)
            return bool(self._settings.introspection_optional)
        self._introspection.set(digest, active)
        return active

    def _call_introspection(self, token: str) -> bool:
        """POST *token* to the RFC 7662 endpoint and read ``active``."""
        client_id = self._settings.introspection_client_id or ""
        secret = self._settings.introspection_client_secret or ""
        encoded = base64.b64encode(f"{client_id}:{secret}".encode())
        document = _post_form(
            self._settings.introspection_url or "",
            {"token": token, "token_type_hint": "access_token"},
            self._settings.timeout,
            {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {encoded.decode('ascii')}",
            },
        )
        return isinstance(document, Mapping) and document.get("active") is True

    def _audience_matches(self, claims: Mapping[str, Any]) -> bool:
        """Check ``aud`` and the RFC 8707 ``resource`` claim."""
        expected = self._settings.audience
        for key in ("aud", "resource"):
            values = claims.get(key)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, Sequence) and expected in values:
                return True
        return False


class OAuthResolver(PrincipalResolver):
    """Map a verified OAuth token to a :class:`Principal`.

    The principal is always scoped: its namespaces and tool overrides come
    from the explicit scope mapping, and an unmapped token maps to an empty
    :class:`ScopeSpec`, which allows nothing.  The default is least
    privilege on purpose; a deployment must map each scope it wants to grant
    with ``--oauth-scope``.

    The principal's ``client_id`` is the OAuth client that obtained the
    token, so two clients acting for the same user remain separate
    principals for accounting, concurrency and the execution log.

    Args:
        verifier: The token verifier.
    """

    def __init__(self, verifier: OAuthTokenVerifier) -> None:
        super().__init__()
        self._verifier = verifier

    @property
    def enabled(self) -> bool:
        """OAuth authentication is always active once constructed."""
        return True

    def resolve(self, token: str | None) -> Principal | None:
        """Resolve an OAuth access token, or ``None`` when it is rejected."""
        if not token:
            return None
        claims = self._verifier.verify(token)
        if claims is None:
            return None
        client_id = _string_claim(claims, "client_id") or _string_claim(claims, "azp")
        label = _string_claim(claims, "sub") or client_id or "oauth"
        return Principal(
            label=label,
            scopes=self._scopes(claims),
            max_concurrency=self._max_concurrency(claims, client_id),
            client_id=client_id,
        )

    def _scopes(self, claims: Mapping[str, Any]) -> ScopeSpec:
        """Map the token's scopes to namespaces and tool overrides.

        The explicit table is the whole policy: a scope that is not mapped
        grants nothing.  When two granted scopes disagree about one tool,
        the deny wins, so adding a scope cannot widen an existing deny.
        """
        namespaces: list[str] = []
        tools: dict[str, bool] = {}
        settings = self._verifier.settings
        for scope in _token_scopes(claims):
            for namespace in settings.scope_map.get(scope, ()):
                if namespace not in namespaces:
                    namespaces.append(namespace)
            for tool, allowed in settings.scope_tools.get(scope, {}).items():
                tools[tool] = tools.get(tool, True) and bool(allowed)
        return ScopeSpec(namespaces=tuple(namespaces), tools=tools)

    def _max_concurrency(
        self, claims: Mapping[str, Any], client_id: str | None
    ) -> int | None:
        """Return the per-client call limit from a claim or the registration.

        A valid claim wins over the static table.  An invalid claim is
        ignored rather than rejecting the token: the process
        ``--client-concurrency`` stays the ceiling, and ``ClientLimiter``
        only ever narrows it.
        """
        raw = claims.get(self._verifier.settings.concurrency_claim)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
        if client_id:
            return self._verifier.settings.client_max_concurrency.get(client_id)
        return None


def _string_claim(claims: Mapping[str, Any], name: str) -> str | None:
    """Return a non-empty string claim, or ``None``."""
    value = claims.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _token_scopes(claims: Mapping[str, Any]) -> list[str]:
    """Return the scopes a token carries, from ``scope`` or ``scp``.

    ``scope`` is the RFC 6749/8693 spelling with space-separated values.
    Azure AD and some Microsoft identity platforms use ``scp``, and both a
    string and a JSON array are accepted.
    """
    for name in ("scope", "scp"):
        raw = claims.get(name)
        if isinstance(raw, str):
            scopes = raw.split()
        elif isinstance(raw, Sequence):
            scopes = [str(scope) for scope in raw]
        else:
            scopes = []
        if scopes:
            return scopes
    return []


class CompositePrincipalResolver(PrincipalResolver):
    """Try API keys first, then OAuth tokens.

    Args:
        api_keys: The API-key/operator-token resolver, or ``None``.
        oauth: The OAuth resolver, or ``None``.
    """

    def __init__(
        self,
        api_keys: PrincipalResolver | None,
        oauth: PrincipalResolver | None,
    ) -> None:
        super().__init__()
        self._api_keys = api_keys
        self._oauth = oauth

    @property
    def enabled(self) -> bool:
        """Whether either resolver accepts a credential."""
        return bool(
            (self._api_keys is not None and self._api_keys.enabled)
            or (self._oauth is not None and self._oauth.enabled)
        )

    @property
    def has_keys(self) -> bool:
        """Whether an API-key store is attached."""
        return bool(self._api_keys is not None and self._api_keys.has_keys)

    def resolve(self, token: str | None) -> Principal | None:
        """Resolve through the API-key resolver first, then OAuth."""
        if not token:
            return None
        if self._api_keys is not None:
            principal = self._api_keys.resolve(token)
            if principal is not None:
                return principal
        if self._oauth is not None:
            return self._oauth.resolve(token)
        return None


def build_oauth_resolver(settings: OAuthSettings | None) -> OAuthResolver | None:
    """Build an OAuth resolver, warming the JWKS cache best-effort."""
    if settings is None:
        return None
    verifier = OAuthTokenVerifier(settings)
    verifier.warm()
    return OAuthResolver(verifier)


def build_resolver(
    keys_db: str | None,
    operator_tokens: Iterable[str],
    oauth: OAuthSettings | None,
    *,
    transport: str = "http",
    store: Any | None = None,
) -> PrincipalResolver | None:
    """Build the resolver used by an adapter.

    Args:
        keys_db: SQLite key-store path, or ``None``.
        operator_tokens: Static bearer tokens with full access.
        oauth: OAuth settings, or ``None`` when OAuth is not configured.
        transport: The serving transport.  ``stdio`` carries no request
            credential, so the key store is ignored there (the caller warns).
        store: An already-open key store.  The gateway keeps one for its
            admin panel and passes it here so both share the instance.

    Returns:
        The composite resolver, one of its parts, or ``None``.
    """
    api_keys: PrincipalResolver | None = None
    static_tokens = tuple(operator_tokens)
    if store is not None:
        api_keys = PrincipalResolver(store, operator_tokens=static_tokens)
    elif keys_db and transport != "stdio":
        from ..keystore import ApiKeyStore

        api_keys = PrincipalResolver(
            ApiKeyStore(keys_db), operator_tokens=static_tokens
        )
    elif static_tokens:
        # OAuth may be configured without a key store; static tokens stay
        # the bootstrap/legacy credential and are tried first.
        api_keys = PrincipalResolver(None, operator_tokens=static_tokens)
    oauth_resolver = build_oauth_resolver(oauth)
    if api_keys is not None and oauth_resolver is not None:
        return CompositePrincipalResolver(api_keys, oauth_resolver)
    return api_keys or oauth_resolver


def settings_from_args(
    parsed: Any,
    *,
    default_audience: str,
    config_section: Mapping[str, Any] | None = None,
) -> OAuthSettings | None:
    """Build settings from parsed CLI arguments.

    Args:
        parsed: The argparse namespace.
        default_audience: Resource URL used when ``--oauth-audience`` is
            absent.
        config_section: The ``oauth`` mapping from the configuration file.
            Only the introspection keys are read here; a CLI flag wins over
            the file.

    Returns:
        The settings, or ``None`` when no OAuth option was given.

    Raises:
        ValueError: If an OAuth option is given without ``--oauth-issuer``,
            ``--oauth-scope`` is malformed, or an introspection option lacks
            its client credential.
    """
    section = dict(config_section or {})
    issuer = getattr(parsed, "oauth_issuer", None)
    audience = getattr(parsed, "oauth_audience", None)
    jwks_uri = getattr(parsed, "oauth_jwks", None)
    raw_scopes = getattr(parsed, "oauth_scope", None) or []
    raw_supported = getattr(parsed, "oauth_scopes", None)
    authorization_url = getattr(parsed, "oauth_authorization_url", None)
    token_url = getattr(parsed, "oauth_token_url", None)
    refresh_url = getattr(parsed, "oauth_refresh_url", None)
    introspection_url = _config_value(parsed, "oauth_introspection_url", section)
    introspection_client_id = _config_value(
        parsed, "oauth_introspection_client_id", section
    )
    introspection_client_secret = _config_value(
        parsed, "oauth_introspection_client_secret", section
    )
    introspection_ttl = _config_value(parsed, "oauth_introspection_cache_ttl", section)
    introspection_optional = _config_value(
        parsed, "oauth_introspection_optional", section
    )
    concurrency_claim = _config_value(parsed, "oauth_concurrency_claim", section)
    raw_client_limits = getattr(parsed, "oauth_client_concurrency", None) or []
    if issuer is None:
        if (
            audience
            or jwks_uri
            or raw_scopes
            or raw_supported is not None
            or authorization_url
            or token_url
            or refresh_url
            or introspection_url
            or introspection_client_id
            or introspection_client_secret
            or introspection_ttl is not None
            or introspection_optional
            or concurrency_claim
            or raw_client_limits
            or section.get("client_concurrency")
        ):
            raise ValueError(
                "--oauth-issuer is required when other OAuth options are set"
            )
        return None

    scope_map, scope_tools = _parse_scope_entries(raw_scopes)

    scopes_supported = None
    if raw_supported is not None:
        scopes_supported = tuple(raw_supported.split())

    client_max_concurrency = _parse_client_limits(
        raw_client_limits, section.get("client_concurrency")
    )

    return OAuthSettings(
        issuer=issuer,
        audience=audience or default_audience,
        jwks_uri=jwks_uri,
        scope_map=scope_map,
        scope_tools=scope_tools,
        scopes_supported=scopes_supported,
        authorization_url=authorization_url,
        token_url=token_url,
        refresh_url=refresh_url,
        introspection_url=introspection_url,
        introspection_client_id=introspection_client_id,
        introspection_client_secret=introspection_client_secret,
        introspection_cache_ttl=(
            DEFAULT_INTROSPECTION_TTL
            if introspection_ttl is None
            else float(introspection_ttl)
        ),
        introspection_optional=bool(introspection_optional),
        concurrency_claim=concurrency_claim or DEFAULT_CONCURRENCY_CLAIM,
        client_max_concurrency=client_max_concurrency,
    )


def _config_value(parsed: Any, argument: str, section: Mapping[str, Any]) -> Any:
    """Return a CLI option, or the config-file value when the flag is absent.

    The argparse default for every introspection flag is ``None``, so a
    present flag is distinguishable from an absent one.

    Args:
        parsed: The argparse namespace.
        argument: The destination name without the leading dashes.
        section: The ``oauth`` mapping from the configuration file.

    Returns:
        The flag value, the file value, or ``None``.
    """
    value = getattr(parsed, argument, None)
    if value is not None:
        return value
    key = argument.removeprefix("oauth_")
    return section.get(key)


def _parse_scope_entries(
    raw_scopes: Sequence[str],
) -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, bool]]]:
    """Parse ``--oauth-scope`` values into namespace and tool tables.

    Each entry is ``SCOPE=ENTRY[,ENTRY]``.  An entry is a namespace pattern,
    ``+tool-name`` to allow one tool, or ``-tool-name`` to deny it.  The
    prefix form keeps the existing syntax for namespaces unchanged.

    Args:
        raw_scopes: The repeated flag values.

    Returns:
        ``(scope_map, scope_tools)``.

    Raises:
        ValueError: If an entry is malformed or grants nothing.
    """
    scope_map: dict[str, tuple[str, ...]] = {}
    scope_tools: dict[str, dict[str, bool]] = {}
    for entry in raw_scopes:
        scope, namespaces, tools = _parse_scope_entry(entry)
        scope_map[scope] = namespaces
        if tools:
            scope_tools[scope] = tools
    return scope_map, scope_tools


def _parse_scope_entry(
    entry: str,
) -> tuple[str, tuple[str, ...], dict[str, bool]]:
    """Parse one ``SCOPE=ENTRY[,ENTRY]`` value.

    Args:
        entry: One ``--oauth-scope`` value.

    Returns:
        ``(scope, namespaces, tool_overrides)``.

    Raises:
        ValueError: If the value is malformed or grants nothing.
    """
    message = (
        f"--oauth-scope expects SCOPE=ENTRY[,ENTRY], got {entry!r}; "
        "an entry is a namespace, +tool or -tool"
    )
    if "=" not in entry:
        raise ValueError(message)
    scope, values = entry.split("=", 1)
    scope = scope.strip()
    namespaces: list[str] = []
    tools: dict[str, bool] = {}
    for value in (item.strip() for item in values.split(",")):
        if not value:
            continue
        if value.startswith("+") and len(value) > 1:
            tools[value[1:]] = True
        elif value.startswith("-") and len(value) > 1:
            tools[value[1:]] = False
        else:
            namespaces.append(value)
    if not scope or (not namespaces and not tools):
        raise ValueError(message)
    return scope, tuple(namespaces), tools


def _parse_client_limits(
    raw_limits: Sequence[str], config_limits: Any
) -> dict[str, int]:
    """Parse ``--oauth-client-concurrency`` values or the config mapping.

    Args:
        raw_limits: Repeated ``CLIENT=LIMIT`` flag values.
        config_limits: The ``oauth.client_concurrency`` mapping, used only
            when no flag is present.

    Returns:
        Limits keyed by OAuth client id.

    Raises:
        ValueError: If an entry is malformed or the limit is not positive.
    """
    if not raw_limits:
        if isinstance(config_limits, Mapping):
            # The label names the source, so a bad value in the file does
            # not send the operator to a flag they never passed.
            return {
                str(client): _positive_int(
                    limit, f"oauth.client_concurrency[{client!r}]"
                )
                for client, limit in config_limits.items()
            }
        return {}
    limits: dict[str, int] = {}
    for entry in raw_limits:
        if "=" not in entry:
            raise ValueError(
                f"--oauth-client-concurrency expects CLIENT=LIMIT, got {entry!r}"
            )
        client, limit = entry.split("=", 1)
        client = client.strip()
        try:
            value = int(limit.strip())
        except ValueError as exc:
            raise ValueError(
                f"--oauth-client-concurrency expects CLIENT=LIMIT, got {entry!r}"
            ) from exc
        if not client:
            raise ValueError(
                f"--oauth-client-concurrency expects CLIENT=LIMIT, got {entry!r}"
            )
        limits[client] = _positive_int(value, "--oauth-client-concurrency")
    return limits


__all__ = [
    "CompositePrincipalResolver",
    "OAuthResolver",
    "OAuthSettings",
    "OAuthTokenVerifier",
    "build_oauth_resolver",
    "build_resolver",
    "settings_from_args",
]
