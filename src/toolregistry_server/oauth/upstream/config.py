"""Server-owned OAuth client declarations for outbound access points.

The core configuration loader owns the ``tools`` list and rejects keys it does
not understand.  Upstream OAuth is a server concern, so its declaration lives
in the server-owned top-level ``oauth`` section, under ``clients``, beside the
resource-server settings::

    oauth:
      introspection_url: https://idp.example.com/introspect
      clients:
        - source: "namespace:reports"
          issuer: https://idp.example.com
          client_id: toolregistry-reports
          client_secret_env: MCP_REPORTS_CLIENT_SECRET
          scopes: [mcp:read]
          grant: client_credentials
          token_endpoint: https://idp.example.com/oauth/token
          token_store: /var/lib/toolregistry/oauth-clients.db

``source`` selects exactly one HTTP(S) MCP source.  Two selector forms are
accepted:

* ``namespace:<name>`` matches the source whose ``namespace`` is ``<name>``;
* ``url:<url>`` matches the source whose ``url`` is ``<url>`` (a trailing
  slash is ignored).

The selector resolves MCP sources today.  Its form is deliberately generic so
the other access points can gain upstream credentials under the same section
without a second selector vocabulary.

A declaration that matches no source, more than one source, or a stdio
source is rejected at load time.  So is an unknown key: a mistyped
``client_secret_env`` would otherwise leave the source unauthenticated with
no diagnostic.

Secrets are write-only.  :meth:`OAuthClientConfig.to_dict` never returns the
resolved client secret; the environment variable *name* is returned instead,
and the diagnostics surface has no other copy.  The config file itself is the
operator's, so it may carry a literal secret; this module does not echo it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from toolregistry.config import ConfigError, MCPSource, ToolConfig, ToolSource

from ..config import OAUTH_SECTION, _parse

__all__ = [
    "DEFAULT_EXPIRY_SKEW",
    "DEFAULT_HTTP_TIMEOUT",
    "OAUTH_CLIENTS_KEY",
    "OAUTH_CLIENT_KEYS",
    "OAuthClientConfig",
    "default_token_store_path",
    "load_oauth_clients",
    "selector_matches_source",
]

#: Key inside :data:`OAUTH_SECTION` that holds the outbound client entries.
OAUTH_CLIENTS_KEY = "clients"

#: Keys accepted inside one declaration.  Anything else is rejected.
OAUTH_CLIENT_KEYS: frozenset[str] = frozenset(
    {
        "source",
        "issuer",
        "discovery_url",
        "client_id",
        "client_secret",
        "client_secret_env",
        "scopes",
        "grant",
        "token_endpoint",
        "client_auth",
        "token_store",
        "redirect_uris",
    }
)

#: Seconds before expiry at which a token is refreshed instead of reused.
DEFAULT_EXPIRY_SKEW = 60.0

#: Default ceiling on one OAuth HTTP request, in seconds.
DEFAULT_HTTP_TIMEOUT = 10.0

_GRANTS = frozenset({"client_credentials", "authorization_code"})
_CLIENT_AUTH = frozenset({"client_secret_post", "client_secret_basic"})
_JSON_SUFFIXES = frozenset({".json", ".jsonc"})
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


@dataclass(frozen=True)
class OAuthClientConfig:
    """OAuth settings for one protected MCP source.

    Attributes:
        source: Selector for the source, ``namespace:<name>`` or
            ``url:<url>``.
        issuer: Authorization-server issuer URL.  Discovery uses the
            issuer's RFC 8414 and OIDC metadata documents.
        discovery_url: Explicit metadata URL.  Takes precedence over
            *issuer* when both are set.
        client_id: OAuth client identifier.
        client_secret: Resolved client secret.  Never included in ``repr``
            or :meth:`to_dict`.
        client_secret_env: Environment variable the secret was read from.
        scopes: Scopes requested from the authorization server.
        grant: OAuth grant the provider uses.
        token_endpoint: Token endpoint override.  When set, discovery is
            not needed to exchange or refresh a token.
        client_auth: How the client secret is presented: in the form body
            (``client_secret_post``) or as HTTP Basic authentication
            (``client_secret_basic``).
        token_store: SQLite token-store path for this source.  ``None``
            uses :func:`default_token_store_path`.
        redirect_uris: Callback URLs an authorization-code flow may use,
            compared exactly.  Empty refuses every flow, so a caller that can
            start one cannot have the code delivered to a host it controls.
    """

    source: str
    client_id: str
    issuer: str | None = None
    discovery_url: str | None = None
    client_secret: str | None = field(default=None, repr=False, compare=False)
    client_secret_env: str | None = None
    scopes: tuple[str, ...] = ()
    grant: Literal["client_credentials", "authorization_code"] = "client_credentials"
    token_endpoint: str | None = None
    client_auth: Literal["client_secret_post", "client_secret_basic"] = (
        "client_secret_post"
    )
    token_store: str | None = None
    redirect_uris: tuple[str, ...] = ()

    @property
    def store_path(self) -> str:
        """Return the token-store path this declaration uses.

        ``token_store`` is validated as absolute at load time; the default
        path is absolute too, so the store never depends on the working
        directory.
        """
        return self.token_store or default_token_store_path()

    def to_dict(self) -> dict[str, Any]:
        """Return the declaration with the secret removed.

        The resolved secret is never returned.  When the operator supplied
        ``client_secret_env``, its name is returned so the declaration stays
        editable; a literal secret is omitted entirely.
        """
        data: dict[str, Any] = {"source": self.source}
        if self.issuer:
            data["issuer"] = self.issuer
        if self.discovery_url:
            data["discovery_url"] = self.discovery_url
        data["client_id"] = self.client_id
        if self.client_secret_env:
            data["client_secret_env"] = self.client_secret_env
        if self.scopes:
            data["scopes"] = list(self.scopes)
        data["grant"] = self.grant
        if self.token_endpoint:
            data["token_endpoint"] = self.token_endpoint
        data["client_auth"] = self.client_auth
        if self.token_store:
            data["token_store"] = self.token_store
        if self.redirect_uris:
            data["redirect_uris"] = list(self.redirect_uris)
        return data


def default_token_store_path() -> str:
    """Return the default token-store location.

    Uses ``$XDG_STATE_HOME`` when set and ``~/.local/state`` otherwise, so
    the database lands in a per-user state directory rather than beside the
    configuration file.  The directory is created owner-only by
    :class:`~toolregistry_server.oauth.upstream.store.TokenStore`.
    """
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return str(base / "toolregistry" / "oauth-clients.db")


def load_oauth_clients(
    config_path: str | None,
    config: ToolConfig,
) -> tuple[OAuthClientConfig, ...]:
    """Read and validate ``oauth.clients``.

    The section is parsed independently of the core loader, exactly like the
    rest of the server-owned ``oauth`` section, so a mistake here cannot
    change how tools are registered.

    Args:
        config_path: Path to the JSONC or YAML configuration file, or
            ``None`` when the caller has no file.
        config: The parsed tool configuration the declarations must match.

    Returns:
        One declaration per entry, in file order.  An absent section or an
        absent ``clients`` key yields an empty tuple.

    Raises:
        ConfigError: If the section is malformed, ``clients`` is not a list
            of mappings, a key is unknown, a selector matches no source or
            several sources, the matched source is not an HTTP(S) MCP source,
            or the source already sets an ``Authorization`` header.
    """
    if not config_path:
        return ()
    path = Path(config_path)
    if not path.is_file():
        return ()
    suffix = path.suffix.lower()
    if suffix not in _JSON_SUFFIXES | _YAML_SUFFIXES:
        return ()
    try:
        data = _parse(path.read_text(encoding="utf-8"), suffix)
    except Exception:
        # The registry loader parses the same file and reports the syntax
        # error; this helper must not replace that message.
        return ()
    if not isinstance(data, dict):
        return ()
    section = data.get(OAUTH_SECTION)
    if section is None:
        return ()
    if not isinstance(section, dict):
        raise ConfigError(f"'{OAUTH_SECTION}' must be a mapping")
    entries = section.get(OAUTH_CLIENTS_KEY)
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise ConfigError(f"'{OAUTH_SECTION}.{OAUTH_CLIENTS_KEY}' must be a list")
    declarations = tuple(
        _build_declaration(entry, index) for index, entry in enumerate(entries)
    )
    _validate_declarations(declarations, config.tools)
    return declarations


def _build_declaration(entry: Any, index: int) -> OAuthClientConfig:
    """Parse and validate one declaration entry.

    Args:
        entry: Raw mapping for one entry.
        index: Entry index, used in error messages.

    Returns:
        The typed declaration.

    Raises:
        ConfigError: If the entry is malformed.
    """
    where = f"{OAUTH_SECTION}.{OAUTH_CLIENTS_KEY}[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a mapping")
    unknown = sorted(set(entry) - OAUTH_CLIENT_KEYS)
    if unknown:
        names = ", ".join(repr(key) for key in unknown)
        known = ", ".join(sorted(OAUTH_CLIENT_KEYS))
        raise ConfigError(f"{where}: unrecognised key(s): {names}; valid keys: {known}")

    source = _required_string(entry, "source", where)
    if not _is_selector(source):
        raise ConfigError(
            f"{where}.source must be 'namespace:<name>' or 'url:<url>', got {source!r}"
        )
    client_id = _required_string(entry, "client_id", where)
    secret, secret_env = _resolve_secret(entry, where)

    issuer = _optional_url(entry.get("issuer"), f"{where}.issuer")
    discovery_url = _optional_url(entry.get("discovery_url"), f"{where}.discovery_url")
    if issuer is None and discovery_url is None:
        raise ConfigError(f"{where} requires 'issuer' or 'discovery_url'")

    grant = entry.get("grant", "client_credentials")
    if grant not in _GRANTS:
        raise ConfigError(
            f"{where}.grant must be one of {sorted(_GRANTS)}, got {grant!r}"
        )
    client_auth = entry.get("client_auth", "client_secret_post")
    if client_auth not in _CLIENT_AUTH:
        raise ConfigError(
            f"{where}.client_auth must be one of {sorted(_CLIENT_AUTH)}, "
            f"got {client_auth!r}"
        )

    return OAuthClientConfig(
        source=source,
        client_id=client_id,
        issuer=issuer,
        discovery_url=discovery_url,
        client_secret=secret,
        client_secret_env=secret_env,
        scopes=_parse_scopes(entry.get("scopes"), where),
        grant=grant,
        token_endpoint=_optional_url(
            entry.get("token_endpoint"), f"{where}.token_endpoint"
        ),
        client_auth=client_auth,
        token_store=_token_store_path(entry.get("token_store"), where),
        redirect_uris=_parse_redirect_uris(entry.get("redirect_uris"), where),
    )


def _token_store_path(value: Any, where: str) -> str | None:
    """Return an absolute token-store path, or ``None`` for the default.

    A relative path is rejected: it would resolve against the process working
    directory, so the same configuration would use a different store under a
    different launcher and could silently create a second database.
    """
    text = _optional_string(value, f"{where}.token_store")
    if text is None:
        return None
    if not os.path.isabs(text):
        raise ConfigError(f"{where}.token_store must be an absolute path, got {text!r}")
    return text


def _required_string(entry: dict[str, Any], key: str, where: str) -> str:
    """Return a required, non-empty string field."""
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def _optional_string(value: Any, where: str) -> str | None:
    """Return an optional, non-empty string field."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} must be a non-empty string")
    return value.strip()


def _optional_url(value: Any, where: str) -> str | None:
    """Return an optional absolute HTTP(S) URL."""
    text = _optional_string(value, where)
    if text is None:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"{where} must be an absolute http(s) URL, got {text!r}")
    return text


def _resolve_secret(entry: dict[str, Any], where: str) -> tuple[str | None, str | None]:
    """Return the client secret and the environment variable it came from.

    Exactly one of ``client_secret`` and ``client_secret_env`` is required.
    The environment variable must be set: a missing secret is a startup
    error, not a source that silently connects unauthenticated.
    """
    literal = entry.get("client_secret")
    variable = entry.get("client_secret_env")
    if literal is not None and variable is not None:
        raise ConfigError(
            f"{where}: 'client_secret' and 'client_secret_env' are mutually exclusive"
        )
    if variable is not None:
        if not isinstance(variable, str) or not variable.strip():
            raise ConfigError(f"{where}.client_secret_env must be a non-empty string")
        value = os.environ.get(variable)
        if not value:
            raise ConfigError(
                f"{where}: environment variable {variable!r} is not set "
                "(referenced by client_secret_env)"
            )
        return value, variable
    if literal is not None:
        if not isinstance(literal, str) or not literal:
            raise ConfigError(f"{where}.client_secret must be a non-empty string")
        return literal, None
    raise ConfigError(
        f"{where}: one of 'client_secret' or 'client_secret_env' is required"
    )


def _parse_scopes(value: Any, where: str) -> tuple[str, ...]:
    """Return the declared scopes, deduplicated and in order."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{where}.scopes must be a list of strings")
    scopes: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{where}.scopes[{index}] must be a non-empty string")
        scope = item.strip()
        if scope not in scopes:
            scopes.append(scope)
    return tuple(scopes)


def _parse_redirect_uris(value: Any, where: str) -> tuple[str, ...]:
    """Return the allowlisted callback URLs, deduplicated and in order."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{where}.redirect_uris must be a list of URLs")
    uris: list[str] = []
    for index, item in enumerate(value):
        uri = _optional_url(item, f"{where}.redirect_uris[{index}]")
        if uri is None:
            raise ConfigError(f"{where}.redirect_uris[{index}] must not be empty")
        if uri not in uris:
            uris.append(uri)
    return tuple(uris)


def _is_selector(value: str) -> bool:
    """Whether *value* has a supported selector prefix and a body."""
    if value.startswith("namespace:"):
        return bool(value[len("namespace:") :].strip())
    if value.startswith("url:"):
        return bool(value[len("url:") :].strip())
    return False


def _selector_kind(value: str) -> tuple[str, str]:
    """Split a selector into its kind and body."""
    kind, _, body = value.partition(":")
    return kind, body


def _normalized_url(value: str) -> str:
    """Return *value* without a trailing slash, for selector matching."""
    return value.rstrip("/")


def selector_matches_source(selector: str, source: ToolSource) -> bool:
    """Whether *selector* names *source*.

    Only HTTP(S) MCP sources can match: a stdio source has no remote token
    to present, so an OAuth declaration for one is a configuration error.

    Args:
        selector: ``namespace:<name>`` or ``url:<url>``.
        source: A parsed tool source.

    Returns:
        ``True`` when the selector names this source.
    """
    if not isinstance(source, MCPSource) or source.transport == "stdio":
        return False
    kind, body = _selector_kind(selector)
    if kind == "namespace":
        return source.namespace == body
    if kind == "url":
        return bool(source.url) and _normalized_url(source.url) == _normalized_url(body)
    return False


def _matches_ignoring_transport(selector: str, source: ToolSource) -> bool:
    """Whether *selector* names *source*, including a stdio source."""
    if not isinstance(source, MCPSource):
        return False
    kind, body = _selector_kind(selector)
    if kind == "namespace":
        return source.namespace == body
    if kind == "url":
        return bool(source.url) and _normalized_url(source.url) == _normalized_url(body)
    return False


def _validate_declarations(
    declarations: tuple[OAuthClientConfig, ...],
    sources: tuple[ToolSource, ...],
) -> None:
    """Require every declaration to select exactly one usable source.

    Args:
        declarations: Parsed declarations.
        sources: The configuration's source list.

    Raises:
        ConfigError: On a duplicate selector (including two selectors that
            name the same source), an unmatched or ambiguous selector, a
            stdio match, or a source that already sets an ``Authorization``
            header.
    """
    # Identity, not equality: two separate declarations that resolve to the
    # same entry of *sources* are the duplicate.  ``MCPSource`` carries a
    # ``headers`` mapping, so the instance is not hashable.
    seen: set[int] = set()
    for declaration in declarations:
        matched = [
            source
            for source in sources
            if selector_matches_source(declaration.source, source)
        ]
        if not matched:
            if any(
                _matches_ignoring_transport(declaration.source, source)
                for source in sources
            ):
                raise ConfigError(
                    f"{OAUTH_SECTION}.{OAUTH_CLIENTS_KEY}: {declaration.source!r} names a stdio "
                    "MCP source; OAuth requires an 'sse' or 'streamable-http' "
                    "transport"
                )
            raise ConfigError(
                f"{OAUTH_SECTION}.{OAUTH_CLIENTS_KEY}: no MCP source matches {declaration.source!r}"
            )
        if len(matched) > 1:
            raise ConfigError(
                f"{OAUTH_SECTION}.{OAUTH_CLIENTS_KEY}: {declaration.source!r} matches "
                f"{len(matched)} MCP sources; use a selector that names one"
            )
        matched_source = matched[0]
        assert isinstance(matched_source, MCPSource)
        # Deduplicate on the resolved source, not on the selector text:
        # ``namespace:example`` and ``url:https://mcp.example.com/mcp`` can
        # name the same source, and two declarations for it fail the source
        # at connect time.
        if id(matched_source) in seen:
            raise ConfigError(
                f"{OAUTH_SECTION}.{OAUTH_CLIENTS_KEY}: duplicate declaration for "
                f"{declaration.source!r}; more than one declaration selects "
                "the same MCP source"
            )
        seen.add(id(matched_source))
        headers = matched_source.headers or {}
        if any(name.lower() == "authorization" for name in headers):
            raise ConfigError(
                f"{OAUTH_SECTION}.{OAUTH_CLIENTS_KEY}: {declaration.source!r} already sets an "
                "'Authorization' header; remove it so OAuth can supply the "
                "credential"
            )
