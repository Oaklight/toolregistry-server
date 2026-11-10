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
        verifier = _verifier()
        assert verifier.verify(_token(KEY1, "key-1")) is not None
        assert jwks.calls == 1

        # Rotation: the endpoint now publishes key-2 as well.
        jwks.document = {"keys": [JWK1, JWK2]}
        assert verifier.verify(_token(KEY2, "key-2")) is not None
        assert jwks.calls == 2

    def test_a_failed_fetch_rejects_the_token(self, jwks):
        jwks.failure = OSError("connection refused")

        assert _verifier().verify(_token(KEY1, "key-1")) is None

    def test_warm_swallows_a_failed_fetch(self, jwks):
        jwks.failure = OSError("connection refused")
        _verifier().warm()  # must not raise

    def test_the_ttl_expiry_refetches(self, jwks):
        verifier = _verifier(cache_ttl=0)
        assert verifier.verify(_token(KEY1, "key-1")) is not None
        assert verifier.verify(_token(KEY1, "key-1")) is not None
        assert jwks.calls == 2


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
        )

        settings = _oauth_settings(parsed, default_audience=None)
        assert settings is not None
        assert settings.issuer == ISSUER
        assert settings.audience == AUDIENCE
        assert settings.jwks_url == JWKS_URL
        assert settings.scope_map["tools:read"] == ("calculator", "web")

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
