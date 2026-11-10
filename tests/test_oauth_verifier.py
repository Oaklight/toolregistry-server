"""OAuth resource-server verification tests.

Every test builds its own RSA keypair and serves the JWKS through a fake
``urlopen``; nothing reaches the network.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from toolregistry_server.adapters.mcp.server import _prm_scopes
from toolregistry_server.cli import CLI, _oauth_settings
from toolregistry_server.keystore import PrincipalResolver
from toolregistry_server.oauth import (
    CompositePrincipalResolver,
    OAuthResolver,
    OAuthSettings,
    OAuthTokenVerifier,
    build_resolver,
    settings_from_args,
)
from toolregistry_server.oauth.verifier import _metadata_url_candidates

ISSUER = "https://idp.example.com/realms/dev"
AUDIENCE = "https://tools.example.com/mcp"
JWKS_URL = "https://idp.example.com/realms/dev/jwks"


def _keypair(kid: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return key, jwk


KEY1, JWK1 = _keypair("key-1")
KEY2, JWK2 = _keypair("key-2")


class _Response:
    def __init__(self, document: dict) -> None:
        self._payload = json.dumps(document).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _JwksServer:
    """A fake JWKS endpoint that counts fetches and can fail."""

    def __init__(self, *jwks: dict) -> None:
        self.document = {"keys": list(jwks)}
        self.calls = 0
        self.failure: Exception | None = None

    def __call__(self, url: str, timeout: float | None = None) -> _Response:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return _Response(self.document)


class _RoutedServer:
    """A fake HTTP endpoint that dispatches on the URL."""

    def __init__(self, routes: dict[str, object] | None = None) -> None:
        self.routes = dict(routes or {})
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float | None = None) -> _Response:
        self.urls.append(url)
        target = self.routes.get(url)
        if isinstance(target, Exception):
            raise target
        if target is None:
            raise OSError(f"no route for {url}")
        return _Response(target)


def _settings(**overrides) -> OAuthSettings:
    values = {"issuer": ISSUER, "audience": AUDIENCE, "jwks_uri": JWKS_URL}
    values.update(overrides)
    return OAuthSettings(**values)


def _token(key, kid: str, **claims) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(claims)
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture()
def jwks(monkeypatch) -> _JwksServer:
    server = _JwksServer(JWK1)
    monkeypatch.setattr(urllib.request, "urlopen", server)
    return server


def _verifier(**overrides) -> OAuthTokenVerifier:
    return OAuthTokenVerifier(_settings(**overrides))


class TestSignatureAndClaims:
    def test_a_valid_token_is_accepted(self, jwks):
        claims = _verifier().verify(_token(KEY1, "key-1"))

        assert claims is not None
        assert claims["sub"] == "user-1"
        assert jwks.calls == 1

    def test_the_issuer_must_match_exactly(self, jwks):
        token = _token(KEY1, "key-1", iss="https://idp.example.com/realms/other")
        assert _verifier().verify(token) is None

    def test_a_trailing_slash_issuer_is_compared_verbatim(self, jwks):
        issuer = ISSUER + "/"
        settings = OAuthSettings(issuer=issuer, audience=AUDIENCE, jwks_uri=JWKS_URL)
        verifier = OAuthTokenVerifier(settings)

        assert verifier.verify(_token(KEY1, "key-1", iss=issuer)) is not None
        assert verifier.verify(_token(KEY1, "key-1", iss=ISSUER)) is None

    def test_the_audience_must_match(self, jwks):
        token = _token(KEY1, "key-1", aud="https://other.example.com/mcp")
        assert _verifier().verify(token) is None

    def test_the_resource_claim_satisfies_the_audience(self, jwks):
        token = _token(KEY1, "key-1", aud="something-else", resource=AUDIENCE)
        assert _verifier().verify(token) is not None

    def test_expired_and_not_yet_valid_tokens_are_rejected(self, jwks):
        now = int(time.time())
        expired = _token(KEY1, "key-1", exp=now - 3600)
        future = _token(KEY1, "key-1", nbf=now + 3600)

        verifier = _verifier()
        assert verifier.verify(expired) is None
        assert verifier.verify(future) is None

    def test_the_leeway_tolerates_small_clock_skew(self, jwks):
        token = _token(KEY1, "key-1", exp=int(time.time()) - 30)
        assert _verifier().verify(token) is not None

    def test_the_none_algorithm_is_rejected(self, jwks):
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300},
            key="",
            algorithm="none",
        )
        assert _verifier().verify(token) is None

    def test_a_wrong_signature_is_rejected(self, jwks):
        token = _token(KEY2, "key-1")  # signed by key-2, claims kid key-1
        assert _verifier().verify(token) is None

    def test_a_malformed_token_is_rejected(self, jwks):
        assert _verifier().verify("not-a-jwt") is None


class TestJwksCache:
    def test_an_unknown_kid_forces_one_refetch(self, jwks):
        verifier = _verifier(refresh_cooldown=0)
        assert verifier.verify(_token(KEY1, "key-1")) is not None
        assert jwks.calls == 1

        # Rotation: the endpoint now publishes key-2 as well.
        jwks.document = {"keys": [JWK1, JWK2]}
        assert verifier.verify(_token(KEY2, "key-2")) is not None
        assert jwks.calls == 2

    def test_random_kids_cannot_force_a_fetch_per_request(self, jwks):
        verifier = _verifier()
        assert verifier.verify(_token(KEY1, "key-1")) is not None
        assert jwks.calls == 1

        now = int(time.time())
        for index in range(20):
            forged = jwt.encode(
                {
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "sub": "user-1",
                    "iat": now,
                    "exp": now + 300,
                },
                KEY1,
                algorithm="RS256",
                headers={"kid": f"bogus-{index}"},
            )
            assert verifier.verify(forged) is None

        assert jwks.calls == 1

    def test_a_rotated_key_is_accepted_once_the_window_elapses(self, jwks):
        verifier = _verifier()
        assert verifier.verify(_token(KEY1, "key-1")) is not None

        jwks.document = {"keys": [JWK1, JWK2]}
        assert verifier.verify(_token(KEY2, "key-2")) is None  # inside window
        verifier._cache._cooldown = 0  # simulate the window elapsing

        assert verifier.verify(_token(KEY2, "key-2")) is not None
        assert jwks.calls == 2

    def test_a_failed_fetch_rejects_the_token(self, jwks):
        jwks.failure = OSError("connection refused")

        assert _verifier().verify(_token(KEY1, "key-1")) is None

    def test_warm_swallows_a_failed_fetch(self, jwks):
        jwks.failure = OSError("connection refused")
        _verifier().warm()  # must not raise

    def test_the_ttl_expiry_refetches(self, jwks):
        verifier = _verifier(cache_ttl=0, refresh_cooldown=0)
        assert verifier.verify(_token(KEY1, "key-1")) is not None
        assert verifier.verify(_token(KEY1, "key-1")) is not None
        assert jwks.calls == 2


class TestMetadataCandidates:
    def test_an_origin_issuer_uses_the_suffix_forms(self):
        assert _metadata_url_candidates("https://idp.example.com") == (
            "https://idp.example.com/.well-known/oauth-authorization-server",
            "https://idp.example.com/.well-known/openid-configuration",
        )

    def test_a_path_issuer_tries_the_rfc8414_form_first(self):
        candidates = _metadata_url_candidates("https://idp.example.com/realms/dev/")

        assert candidates[0] == (
            "https://idp.example.com/.well-known/oauth-authorization-server/realms/dev"
        )
        assert candidates[1] == (
            "https://idp.example.com/realms/dev/.well-known/oauth-authorization-server"
        )
        assert candidates[2] == (
            "https://idp.example.com/realms/dev/.well-known/openid-configuration"
        )
        assert len(set(candidates)) == len(candidates)


class TestJwksDiscovery:
    def _server(self, jwks_doc: dict, *, rfc8414: dict | None, oidc: dict | None):
        routes: dict[str, object] = {
            f"{ISSUER}/.well-known/oauth-authorization-server": (
                rfc8414 if rfc8414 is not None else OSError("metadata missing")
            ),
            f"{ISSUER}/.well-known/openid-configuration": (
                oidc if oidc is not None else OSError("metadata missing")
            ),
        }
        custom = "https://idp.example.com/custom/jwks"
        routes[custom] = jwks_doc
        routes[f"{ISSUER}/.well-known/jwks.json"] = jwks_doc
        return _RoutedServer(routes), custom

    def test_rfc8414_metadata_names_the_jwks(self, monkeypatch):
        server, custom = self._server(
            {"keys": [JWK1]},
            rfc8414={"jwks_uri": "https://idp.example.com/custom/jwks"},
            oidc=None,
        )
        monkeypatch.setattr(urllib.request, "urlopen", server)

        assert _verifier(jwks_uri=None).verify(_token(KEY1, "key-1")) is not None
        assert custom in server.urls
        assert f"{ISSUER}/.well-known/jwks.json" not in server.urls

    def test_oidc_metadata_is_the_second_candidate(self, monkeypatch):
        server, custom = self._server(
            {"keys": [JWK1]},
            rfc8414=None,
            oidc={"jwks_uri": "https://idp.example.com/custom/jwks"},
        )
        monkeypatch.setattr(urllib.request, "urlopen", server)

        assert _verifier(jwks_uri=None).verify(_token(KEY1, "key-1")) is not None
        assert f"{ISSUER}/.well-known/oauth-authorization-server" in server.urls
        assert custom in server.urls

    def test_discovery_miss_falls_back_to_the_well_known_path(self, monkeypatch):
        server, _custom = self._server({"keys": [JWK1]}, rfc8414=None, oidc=None)
        monkeypatch.setattr(urllib.request, "urlopen", server)

        assert _verifier(jwks_uri=None).verify(_token(KEY1, "key-1")) is not None
        assert f"{ISSUER}/.well-known/jwks.json" in server.urls

    def test_a_path_issuer_reads_the_rfc8414_form_first(self, monkeypatch):
        issuer = "https://idp.example.com/realms/dev/"
        rfc8414 = (
            "https://idp.example.com/.well-known/oauth-authorization-server/realms/dev"
        )
        server = _RoutedServer(
            {rfc8414: {"jwks_uri": JWKS_URL}, JWKS_URL: {"keys": [JWK1]}}
        )
        monkeypatch.setattr(urllib.request, "urlopen", server)

        verifier = OAuthTokenVerifier(OAuthSettings(issuer=issuer, audience=AUDIENCE))

        token = _token(KEY1, "key-1", iss=issuer)
        assert verifier.verify(token) is not None
        assert server.urls[0] == rfc8414

    def test_an_explicit_jwks_uri_skips_discovery(self, monkeypatch):
        server = _RoutedServer({JWKS_URL: {"keys": [JWK1]}})
        monkeypatch.setattr(urllib.request, "urlopen", server)

        assert _verifier().verify(_token(KEY1, "key-1")) is not None
        assert server.urls == [JWKS_URL]


class TestOAuthResolver:
    def test_scopes_map_through_the_explicit_table(self, jwks):
        verifier = _verifier(scope_map={"tools:read": ("calculator", "web")})
        principal = OAuthResolver(verifier).resolve(
            _token(KEY1, "key-1", scope="tools:read tools:admin")
        )

        assert principal is not None
        assert principal.label == "user-1"
        assert principal.allows("calculator", "calculator-evaluate") is True
        assert principal.allows("web", "web-search") is True
        # The unmapped admin scope grants nothing.
        assert principal.allows("admin", "admin-call") is False

    def test_an_unmapped_token_is_least_privilege(self, jwks):
        principal = OAuthResolver(_verifier()).resolve(
            _token(KEY1, "key-1", scope="tools:read")
        )

        assert principal is not None
        assert principal.unrestricted is False
        assert principal.allows("calculator", "calculator-evaluate") is False

    def test_client_id_labels_the_principal_when_sub_is_absent(self, jwks):
        token = _token(KEY1, "key-1", client_id="service-a")
        payload = jwt.decode(token, options={"verify_signature": False})
        del payload["sub"]
        token = jwt.encode(payload, KEY1, algorithm="RS256", headers={"kid": "key-1"})

        principal = OAuthResolver(_verifier()).resolve(token)
        assert principal is not None
        assert principal.label == "service-a"

    def test_an_invalid_token_resolves_to_none(self, jwks):
        assert OAuthResolver(_verifier()).resolve("nope") is None


class TestComposite:
    def test_api_keys_win_and_oauth_is_second(self, jwks):
        api = PrincipalResolver(operator_tokens=["api-secret"])
        oauth = OAuthResolver(_verifier())
        composite = CompositePrincipalResolver(api, oauth)

        assert composite.resolve("api-secret").unrestricted is True
        assert composite.resolve(_token(KEY1, "key-1")).label == "user-1"
        assert composite.resolve("neither") is None
        assert composite.resolve(None) is None
        assert composite.enabled is True
        assert composite.has_keys is False

    def test_unconfigured_builds_nothing(self):
        assert build_resolver(None, [], None) is None

    def test_settings_from_empty_args_is_none(self):
        assert settings_from_args(argparse.Namespace(), default_audience="x") is None


class TestCliFlags:
    def _parsed(self, command: str, *extra: str):
        parser = CLI().create_parser()
        return parser.parse_args([command, *extra])

    @pytest.mark.parametrize("command", ["openapi", "mcp", "gateway", "local-hub"])
    def test_every_serving_command_accepts_the_flags(self, command):
        parsed = self._parsed(
            command,
            "--oauth-issuer",
            ISSUER,
            "--oauth-audience",
            AUDIENCE,
            "--oauth-jwks",
            JWKS_URL,
            "--oauth-scope",
            "tools:read=calculator,web",
            "--oauth-scopes",
            "openid profile email offline_access",
        )

        settings = _oauth_settings(parsed, default_audience=None)
        assert settings is not None
        assert settings.issuer == ISSUER
        assert settings.audience == AUDIENCE
        assert settings.jwks_url == JWKS_URL
        assert settings.scope_map["tools:read"] == ("calculator", "web")
        assert settings.scopes_supported == (
            "openid",
            "profile",
            "email",
            "offline_access",
        )

    def test_advertised_scopes_default_to_omitted(self):
        parsed = self._parsed("openapi", "--oauth-issuer", ISSUER)
        settings = _oauth_settings(parsed, default_audience=AUDIENCE)

        assert settings is not None
        assert settings.scopes_supported is None

    def test_an_empty_scope_list_omits_the_field(self):
        parsed = self._parsed("openapi", "--oauth-issuer", ISSUER, "--oauth-scopes", "")
        settings = _oauth_settings(parsed, default_audience=AUDIENCE)

        assert settings is not None
        assert settings.scopes_supported == ()

    def test_advertised_scopes_require_an_issuer(self):
        parsed = self._parsed("openapi", "--oauth-scopes", "openid")

        with pytest.raises(ValueError, match="--oauth-issuer"):
            _oauth_settings(parsed, default_audience=AUDIENCE)

    def test_gateway_options_carry_the_settings(self):
        parsed = self._parsed(
            "gateway",
            "--oauth-issuer",
            ISSUER,
            "--server-url",
            "https://tools.example.com/mcp",
        )
        options = CLI._gateway_options(None, parsed)

        assert options["oauth"].issuer == ISSUER
        assert options["oauth"].audience == "https://tools.example.com/mcp"

    def test_a_socket_hub_requires_an_audience(self):
        parsed = self._parsed(
            "local-hub", "run", "--oauth-issuer", ISSUER, "--socket", "/tmp/h.sock"
        )

        with pytest.raises(ValueError, match="--oauth-audience"):
            CLI._gateway_options(None, parsed)

    def test_a_loopback_hub_also_requires_an_audience(self):
        """An ephemeral loopback bind is never a public audience."""
        parsed = self._parsed("local-hub", "run", "--oauth-issuer", ISSUER)

        with pytest.raises(ValueError, match="--oauth-audience"):
            CLI._gateway_options(None, parsed)

    def test_a_hub_accepts_an_explicit_audience(self):
        parsed = self._parsed(
            "local-hub",
            "run",
            "--oauth-issuer",
            ISSUER,
            "--oauth-audience",
            AUDIENCE,
        )
        options = CLI._gateway_options(None, parsed)

        assert options["oauth"].audience == AUDIENCE

    def test_a_hub_takes_the_audience_from_the_server_url(self):
        parsed = self._parsed(
            "local-hub",
            "run",
            "--oauth-issuer",
            ISSUER,
            "--server-url",
            "https://tools.example.com/mcp",
        )
        options = CLI._gateway_options(None, parsed)

        assert options["oauth"].audience == "https://tools.example.com/mcp"

    def test_a_malformed_scope_map_is_rejected(self):
        parsed = self._parsed(
            "openapi", "--oauth-issuer", ISSUER, "--oauth-scope", "bad"
        )

        with pytest.raises(ValueError, match="SCOPE="):
            _oauth_settings(parsed, default_audience=AUDIENCE)

    def test_an_option_without_an_issuer_is_rejected(self):
        parsed = self._parsed("openapi", "--oauth-scope", "a=b")

        with pytest.raises(ValueError, match="--oauth-issuer"):
            _oauth_settings(parsed, default_audience=AUDIENCE)


class TestPrmScopes:
    def test_an_external_issuer_makes_the_field_opt_in(self):
        assert _prm_scopes(["https://idp.example.com"], None) is None
        assert _prm_scopes(["https://idp.example.com"], ()) is None

    def test_explicit_scopes_are_advertised(self):
        assert _prm_scopes(["https://idp.example.com"], ("openid", "profile")) == [
            "openid",
            "profile",
        ]

    def test_no_issuer_keeps_the_generic_scope(self):
        assert _prm_scopes(None, None) == ["mcp"]
