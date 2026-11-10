"""OAuth 2.0 resource-server verification.

The server accepts bearer tokens minted by an external authorization server.
It verifies the token locally against the issuer's JWKS: signature,
algorithm allowlist, exact ``iss``, ``aud``/RFC 8707 ``resource``, and
``exp``/``nbf`` with a small leeway.  Nothing is introspected at request
time and no token is ever logged.

The default scope mapping is least privilege: a token whose scopes are not
listed in :attr:`OAuthSettings.scope_map` maps to an empty
:class:`~toolregistry_server.keystore.ScopeSpec`, which allows no tool.  A
deployment must map each scope it wants to grant explicitly.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

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


@dataclass(frozen=True)
class OAuthSettings:
    """Configuration for resource-server token verification.

    Attributes:
        issuer: Exact ``iss`` value the token must carry.  Trailing slashes
            are stripped.
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
        scopes_supported: Scopes advertised in the protected-resource
            metadata.  ``None`` and an empty tuple both omit the field; when
            an issuer is configured the field is omitted unless this is set
            explicitly, because an external authorization server may reject
            the generic ``mcp`` scope.
    """

    issuer: str
    audience: str
    jwks_uri: str | None = None
    scope_map: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    leeway: float = DEFAULT_LEEWAY
    cache_ttl: float = DEFAULT_CACHE_TTL
    timeout: float = DEFAULT_TIMEOUT
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    scopes_supported: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.issuer.strip():
            raise ValueError("OAuth issuer must not be empty")
        if not self.audience.strip():
            raise ValueError("OAuth audience must not be empty")
        object.__setattr__(self, "issuer", self.issuer.rstrip("/"))
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
        if not self.algorithms:
            raise ValueError("OAuth algorithm allowlist must not be empty")
        if self.scopes_supported is not None:
            object.__setattr__(
                self,
                "scopes_supported",
                tuple(str(scope) for scope in self.scopes_supported if str(scope)),
            )

    @property
    def jwks_url(self) -> str:
        """Return the JWKS endpoint the verifier fetches."""
        return self.jwks_uri or f"{self.issuer}/.well-known/jwks.json"


class _JwksCache:
    """A small in-memory JWKS cache with TTL and forced refetch on rotation."""

    def __init__(self, url: str, ttl: float, timeout: float) -> None:
        self._url = url
        self._ttl = ttl
        self._timeout = timeout
        self._keys: dict[str, Any] = {}
        self._expires = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> None:
        """Fetch the JWKS document and replace the cached keys."""
        from urllib.request import urlopen

        import jwt

        with urlopen(self._url, timeout=self._timeout) as response:  # noqa: S310
            document = json.loads(response.read().decode("utf-8"))
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

        An unknown ``kid`` forces one refetch, which is how a key rotation
        is picked up without waiting for the TTL.
        """
        with self._lock:
            fresh = bool(self._keys) and time.monotonic() < self._expires
            if fresh and kid is not None and kid in self._keys:
                return self._keys[kid]
            if fresh and kid is None:
                return next(iter(self._keys.values()), None)
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
        self._cache = _JwksCache(
            settings.jwks_url, settings.cache_ttl, settings.timeout
        )

    @property
    def settings(self) -> OAuthSettings:
        """The settings this verifier was built with."""
        return self._settings

    def warm(self) -> None:
        """Prefetch the JWKS.  Failures are logged, not raised."""
        self._cache.warm()

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
        key = self._cache.key_for(str(kid) if kid else None)
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
        return claims

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

    The principal is always scoped: its namespaces come from the explicit
    scope mapping, and an unmapped token maps to an empty
    :class:`ScopeSpec`, which allows nothing.

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
        label = claims.get("sub") or claims.get("client_id") or "oauth"
        return Principal(label=str(label), scopes=self._scopes(claims))

    def _scopes(self, claims: Mapping[str, Any]) -> ScopeSpec:
        """Map the token's scopes to namespaces through the explicit table."""
        raw = claims.get("scope", "")
        if isinstance(raw, str):
            scopes = raw.split()
        elif isinstance(raw, Sequence):
            scopes = [str(scope) for scope in raw]
        else:
            scopes = []
        namespaces: list[str] = []
        for scope in scopes:
            for namespace in self._verifier.settings.scope_map.get(scope, ()):
                if namespace not in namespaces:
                    namespaces.append(namespace)
        return ScopeSpec(namespaces=tuple(namespaces))


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
    if store is not None:
        api_keys = PrincipalResolver(store, operator_tokens=tuple(operator_tokens))
    elif keys_db and transport != "stdio":
        from ..keystore import ApiKeyStore

        api_keys = PrincipalResolver(
            ApiKeyStore(keys_db), operator_tokens=tuple(operator_tokens)
        )
    oauth_resolver = build_oauth_resolver(oauth)
    if api_keys is not None and oauth_resolver is not None:
        return CompositePrincipalResolver(api_keys, oauth_resolver)
    return api_keys or oauth_resolver


def settings_from_args(parsed: Any, *, default_audience: str) -> OAuthSettings | None:
    """Build settings from parsed CLI arguments.

    Args:
        parsed: The argparse namespace.
        default_audience: Resource URL used when ``--oauth-audience`` is
            absent.

    Returns:
        The settings, or ``None`` when no OAuth option was given.

    Raises:
        ValueError: If an OAuth option is given without ``--oauth-issuer``,
            or ``--oauth-scope`` is malformed.
    """
    issuer = getattr(parsed, "oauth_issuer", None)
    audience = getattr(parsed, "oauth_audience", None)
    jwks_uri = getattr(parsed, "oauth_jwks", None)
    raw_scopes = getattr(parsed, "oauth_scope", None) or []
    raw_supported = getattr(parsed, "oauth_scopes", None)
    if issuer is None:
        if audience or jwks_uri or raw_scopes or raw_supported is not None:
            raise ValueError(
                "--oauth-issuer is required when other OAuth options are set"
            )
        return None

    scope_map: dict[str, tuple[str, ...]] = {}
    for entry in raw_scopes:
        if "=" not in entry:
            raise ValueError(
                f"--oauth-scope expects SCOPE=NAMESPACE[,NAMESPACE], got {entry!r}"
            )
        scope, namespaces = entry.split("=", 1)
        scope = scope.strip()
        values = tuple(name.strip() for name in namespaces.split(",") if name.strip())
        if not scope or not values:
            raise ValueError(
                f"--oauth-scope expects SCOPE=NAMESPACE[,NAMESPACE], got {entry!r}"
            )
        scope_map[scope] = values

    scopes_supported = None
    if raw_supported is not None:
        scopes_supported = tuple(raw_supported.split())

    return OAuthSettings(
        issuer=issuer,
        audience=audience or default_audience,
        jwks_uri=jwks_uri,
        scope_map=scope_map,
        scopes_supported=scopes_supported,
    )


__all__ = [
    "CompositePrincipalResolver",
    "OAuthResolver",
    "OAuthSettings",
    "OAuthTokenVerifier",
    "build_oauth_resolver",
    "build_resolver",
    "settings_from_args",
]
