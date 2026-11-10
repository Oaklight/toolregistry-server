"""Read the server-owned ``oauth`` section from a tool configuration file.

The tool configuration itself is parsed by :mod:`toolregistry.config`, which
describes tool sources and intentionally ignores sections it does not own.
The server reads its own ``oauth`` section from the same file, following the
pattern :mod:`toolregistry_server.gateway.persistence` uses for ``gateway``::

    oauth:
      issuer: https://idp.example.com/realms/dev
      audience: https://tools.example.com/mcp
      jwks: https://idp.example.com/realms/dev/jwks
      scope:
        tools:read: [calculator, +web-search, -calculator-danger]
      scopes: [openid, profile, email]
      introspection_url: https://idp.example.com/introspect
      introspection_client_id: toolregistry
      introspection_client_secret: ...
      introspection_cache_ttl: 5
      introspection_optional: false
      client_concurrency:
        reports-bot: 2

Every key is named after the ``--oauth-`` flag with the prefix removed, so
``--oauth-jwks`` maps to ``jwks`` and ``--oauth-introspection-url`` maps to
``introspection_url``.  Only values the operator did not pass on the command
line are read; the CLI wins, and an empty repeatable flag counts as absent so
the file still supplies ``scope``.  The file is parsed again, independently of
the registry loader, so a typo in this section cannot change how tools are
registered.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Top-level configuration section that holds OAuth settings.
OAUTH_SECTION = "oauth"

#: Keys :func:`settings_from_args` reads from the section.  Anything else is
#: rejected: a typo such as ``introspection_ur1`` would otherwise leave a
#: security control switched off without a diagnostic.  Each key is the
#: matching ``--oauth-`` flag with the prefix removed.
OAUTH_SECTION_KEYS = frozenset(
    {
        "issuer",
        "audience",
        "jwks",
        "scope",
        "scopes",
        "authorization_url",
        "token_url",
        "refresh_url",
        "introspection_url",
        "introspection_client_id",
        "introspection_client_secret",
        "introspection_cache_ttl",
        "introspection_optional",
        "concurrency_claim",
        "client_concurrency",
    }
)

_JSON_SUFFIXES = frozenset({".json", ".jsonc"})
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


def load_oauth_section(config_path: str | None) -> dict[str, Any]:
    """Return the ``oauth`` mapping from *config_path*, or an empty mapping.

    A missing path, an unsupported extension, or a file without the section
    all yield ``{}`` so a deployment that configures OAuth entirely through
    flags keeps working.  A file that cannot be parsed is the registry
    loader's error to report; here the section is optional.

    Args:
        config_path: Path to the JSONC or YAML configuration file, or
            ``None``.

    Returns:
        The section mapping, copied so callers cannot mutate the source.

    Raises:
        ValueError: If the ``oauth`` value is present but is not a mapping,
            or if it names a key this server does not read.
    """
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.is_file():
        return {}
    suffix = path.suffix.lower()
    if suffix not in _JSON_SUFFIXES | _YAML_SUFFIXES:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        data = _parse(text, suffix)
    except Exception:
        # The registry loader parses the same file and reports its errors;
        # this helper must not replace that message with a second one.
        return {}
    if not isinstance(data, dict):
        return {}
    section = data.get(OAUTH_SECTION)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError(f"'{OAUTH_SECTION}' must be a mapping")
    unknown = sorted(set(section) - OAUTH_SECTION_KEYS)
    if unknown:
        names = ", ".join(repr(key) for key in unknown)
        known = ", ".join(sorted(OAUTH_SECTION_KEYS))
        raise ValueError(
            f"unknown key(s) in the '{OAUTH_SECTION}' section: {names}; "
            f"expected one of {known}"
        )
    return dict(section)


def _parse(text: str, suffix: str) -> Any:
    """Parse *text* as JSONC or YAML, matching the core loader's choice."""
    if suffix in _JSON_SUFFIXES:
        try:
            from toolregistry._vendor.jsonx import loads as jsonc_loads
        except ImportError:  # pragma: no cover - core always vendors jsonx
            return json.loads(text)
        return jsonc_loads(text)
    from toolregistry._vendor.yaml import load as yaml_load

    return yaml_load(text)


__all__ = ["OAUTH_SECTION", "OAUTH_SECTION_KEYS", "load_oauth_section"]
