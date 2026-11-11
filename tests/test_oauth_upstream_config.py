"""Tests for the ``oauth.clients`` configuration section (#254, stage 1).

Every test writes its own JSONC/YAML file and parses it with the real core
loader, so the section is checked against the same source objects the server
registers.
"""

from __future__ import annotations

import textwrap

import pytest
from toolregistry.config import ConfigError, ToolConfig, load_config

from toolregistry_server.oauth.upstream.config import (
    OAuthClientConfig,
    default_token_store_path,
    load_oauth_clients,
    selector_matches_source,
)

TOOLS = """\
mode: denylist
tools:
  - type: mcp
    transport: streamable-http
    url: https://mcp.example.com/mcp
    namespace: example
"""


def _write(tmp_path, body: str, name: str = "tools.yaml") -> str:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _declaration(tmp_path, oauth_body: str, tools: str = TOOLS):
    path = _write(
        tmp_path,
        tools
        + "oauth:\n  clients:\n"
        + textwrap.indent(textwrap.dedent(oauth_body), "    "),
    )
    return path, load_config(path)


def _load(tmp_path, oauth_body: str, tools: str = TOOLS):
    path, config = _declaration(tmp_path, oauth_body, tools)
    return load_oauth_clients(path, config)


def test_absent_section_yields_no_declarations(tmp_path):
    path = _write(tmp_path, TOOLS)
    assert load_oauth_clients(path, load_config(path)) == ()


def test_missing_path_yields_no_declarations():
    # A missing path is not an error for this helper: the core loader already
    # reported it before this is called.
    assert load_oauth_clients(None, ToolConfig()) == ()


def test_valid_client_credentials_declaration(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_SECRET", "top-secret")
    declarations = _load(
        tmp_path,
        """\
        - source: "namespace:example"
          issuer: https://idp.example.com
          client_id: toolregistry
          client_secret_env: MCP_SECRET
          scopes: [read, write, read]
          token_endpoint: https://idp.example.com/oauth/token
          token_store: /var/lib/toolregistry/oauth-clients.db
        """,
    )
    assert len(declarations) == 1
    declaration = declarations[0]
    assert isinstance(declaration, OAuthClientConfig)
    assert declaration.source == "namespace:example"
    assert declaration.issuer == "https://idp.example.com"
    assert declaration.client_secret == "top-secret"
    assert declaration.client_secret_env == "MCP_SECRET"
    assert declaration.scopes == ("read", "write")
    assert declaration.grant == "client_credentials"
    assert declaration.client_auth == "client_secret_post"
    assert declaration.token_endpoint == "https://idp.example.com/oauth/token"
    assert declaration.token_store == "/var/lib/toolregistry/oauth-clients.db"
    assert declaration.store_path == "/var/lib/toolregistry/oauth-clients.db"


def test_secret_is_never_serialised(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_SECRET", "top-secret")
    declaration = _load(
        tmp_path,
        """\
        - source: "namespace:example"
          issuer: https://idp.example.com
          client_id: toolregistry
          client_secret_env: MCP_SECRET
        """,
    )[0]
    assert "top-secret" not in repr(declaration)
    assert "top-secret" not in repr(declaration.to_dict())
    assert "client_secret" not in declaration.to_dict()
    assert declaration.to_dict()["client_secret_env"] == "MCP_SECRET"


def test_literal_secret_is_omitted_from_the_public_view(tmp_path):
    declaration = _load(
        tmp_path,
        """\
        - source: "namespace:example"
          issuer: https://idp.example.com
          client_id: toolregistry
          client_secret: literal-secret
        """,
    )[0]
    assert declaration.client_secret == "literal-secret"
    assert "literal-secret" not in repr(declaration.to_dict())
    assert "client_secret" not in declaration.to_dict()


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unrecognised key"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
              token_stroe: /tmp/wrong.db
            """,
        )


def test_secret_and_env_are_mutually_exclusive(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_SECRET", "top-secret")
    with pytest.raises(ConfigError, match="mutually exclusive"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: literal
              client_secret_env: MCP_SECRET
            """,
        )


def test_missing_secret_env_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_SECRET", raising=False)
    with pytest.raises(ConfigError, match="is not set"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret_env: MCP_SECRET
            """,
        )


def test_issuer_or_discovery_url_is_required(tmp_path):
    with pytest.raises(ConfigError, match="requires 'issuer' or 'discovery_url'"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              client_id: toolregistry
              client_secret: secret
            """,
        )


def test_invalid_grant_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="grant must be one of"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
              grant: password
            """,
        )


def test_invalid_client_auth_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="client_auth must be one of"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
              client_auth: private_key_jwt
            """,
        )


def test_non_http_issuer_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="absolute http"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: file:///etc/passwd
              client_id: toolregistry
              client_secret: secret
            """,
        )


def test_invalid_selector_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="namespace:<name>"):
        _load(
            tmp_path,
            """\
            - source: example
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            """,
        )


def test_url_selector_matches_ignoring_a_trailing_slash(tmp_path):
    declarations = _load(
        tmp_path,
        """\
        - source: "url:https://mcp.example.com/mcp/"
          issuer: https://idp.example.com
          client_id: toolregistry
          client_secret: secret
        """,
    )
    assert declarations[0].source == "url:https://mcp.example.com/mcp/"


def test_unmatched_selector_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="no MCP source matches"):
        _load(
            tmp_path,
            """\
            - source: "namespace:missing"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            """,
        )


def test_ambiguous_selector_is_rejected(tmp_path):
    tools = """\
    mode: denylist
    tools:
      - type: mcp
        transport: streamable-http
        url: https://a.example.com/mcp
        namespace: dup
      - type: mcp
        transport: sse
        url: https://b.example.com/sse
        namespace: dup
    """
    with pytest.raises(ConfigError, match="matches 2 MCP sources"):
        _load(
            tmp_path,
            """\
            - source: "namespace:dup"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            """,
            tools=textwrap.dedent(tools),
        )


def test_stdio_source_is_rejected(tmp_path):
    tools = """\
    mode: denylist
    tools:
      - type: mcp
        transport: stdio
        command: ["python", "-m", "example"]
        namespace: local
    """
    with pytest.raises(ConfigError, match="stdio"):
        _load(
            tmp_path,
            """\
            - source: "namespace:local"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            """,
            tools=textwrap.dedent(tools),
        )


def test_existing_authorization_header_is_rejected(tmp_path):
    tools = """\
    mode: denylist
    tools:
      - type: mcp
        transport: streamable-http
        url: https://mcp.example.com/mcp
        namespace: example
        headers:
          Authorization: Bearer static
    """
    with pytest.raises(ConfigError, match="already sets an 'Authorization'"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            """,
            tools=textwrap.dedent(tools),
        )


def test_duplicate_selector_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="duplicate declaration"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            - source: "namespace:example"
              issuer: https://other.example.com
              client_id: other
              client_secret: secret
            """,
        )


def test_two_selector_forms_for_one_source_are_rejected(tmp_path):
    # ``namespace:example`` and ``url:https://mcp.example.com/mcp`` name the
    # same source in TOOLS.  Comparing the selector text would let both
    # through, and the source would then fail at connect time.
    with pytest.raises(ConfigError, match="duplicate declaration"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            - source: "url:https://mcp.example.com/mcp"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            """,
        )


def test_a_url_selector_with_a_trailing_slash_duplicates_the_plain_one(tmp_path):
    with pytest.raises(ConfigError, match="duplicate declaration"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            - source: "url:https://mcp.example.com/mcp/"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
            """,
        )


def test_relative_token_store_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="must be an absolute path"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
              token_store: state/oauth-clients.db
            """,
        )


def test_clients_must_be_a_list(tmp_path):
    path = _write(
        tmp_path,
        TOOLS + "oauth:\n  clients:\n    source: namespace:example\n",
    )
    with pytest.raises(ConfigError, match="must be a list"):
        load_oauth_clients(path, load_config(path))


def test_section_must_be_a_mapping(tmp_path):
    path = _write(tmp_path, TOOLS + "oauth: not-a-mapping\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_oauth_clients(path, load_config(path))


def test_clients_absent_yields_no_declarations(tmp_path):
    path = _write(tmp_path, TOOLS + "oauth:\n  introspection_optional: false\n")
    assert load_oauth_clients(path, load_config(path)) == ()


def test_selector_matches_source_requires_an_http_transport(tmp_path):
    path = _write(tmp_path, TOOLS)
    source = load_config(path).tools[0]
    assert selector_matches_source("namespace:example", source)
    assert selector_matches_source("url:https://mcp.example.com/mcp", source)
    assert not selector_matches_source("url:https://other.example.com", source)
    assert not selector_matches_source("namespace:other", source)


def test_default_token_store_honours_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_token_store_path() == str(
        tmp_path / "toolregistry" / "oauth-clients.db"
    )


def test_redirect_uris_are_parsed_and_deduplicated(tmp_path):
    declarations = _load(
        tmp_path,
        """\
        - source: "namespace:example"
          issuer: https://idp.example.com
          client_id: toolregistry
          client_secret: secret
          grant: authorization_code
          redirect_uris:
            - https://gateway.example.com/callback
            - https://gateway.example.com/callback
        """,
    )
    assert declarations[0].redirect_uris == ("https://gateway.example.com/callback",)
    assert declarations[0].to_dict()["redirect_uris"] == [
        "https://gateway.example.com/callback"
    ]


@pytest.mark.parametrize(
    "value",
    ["https://gateway.example.com/callback", "[relative/callback]", "['']"],
)
def test_malformed_redirect_uris_are_rejected(tmp_path, value):
    with pytest.raises(ConfigError, match="redirect_uris"):
        _load(
            tmp_path,
            f"""\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
              redirect_uris: {value}
            """,
        )


def test_allow_private_hosts_is_parsed(tmp_path):
    declarations = _load(
        tmp_path,
        """\
        - source: "namespace:example"
          issuer: https://idp.example.com
          client_id: toolregistry
          client_secret: secret
          allow_private_hosts: true
        """,
    )
    assert declarations[0].allow_private_hosts is True
    assert declarations[0].to_dict()["allow_private_hosts"] is True


def test_a_non_boolean_allow_private_hosts_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="allow_private_hosts"):
        _load(
            tmp_path,
            """\
            - source: "namespace:example"
              issuer: https://idp.example.com
              client_id: toolregistry
              client_secret: secret
              allow_private_hosts: "yes"
            """,
        )
