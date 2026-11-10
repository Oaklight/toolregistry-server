"""OAuth resource-server verification tests.

Every test builds its own RSA keypair and serves the JWKS through a fake
``urlopen``; nothing reaches the network.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
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

    def test_a_trailing_slash_issuer_does_not_double_the_fallback_slash(
        self, monkeypatch
    ):
        """#246: the last-resort URL must strip the issuer's trailing slash."""
        issuer = ISSUER + "/"
        fallback = f"{ISSUER}/.well-known/jwks.json"
        server = _RoutedServer({fallback: {"keys": [JWK1]}})
        monkeypatch.setattr(urllib.request, "urlopen", server)

        verifier = OAuthTokenVerifier(OAuthSettings(issuer=issuer, audience=AUDIENCE))

        assert verifier.verify(_token(KEY1, "key-1", iss=issuer)) is not None
        assert fallback in server.urls
        assert all("//.well-known" not in url for url in server.urls)


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

    def test_static_tokens_survive_an_oauth_only_deployment(self, jwks):
        resolver = build_resolver(None, ["api-secret"], _settings())

        assert isinstance(resolver, CompositePrincipalResolver)
        assert resolver.resolve("api-secret").unrestricted is True
        assert resolver.resolve(_token(KEY1, "key-1")).label == "user-1"
        assert resolver.has_keys is False

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
            "--oauth-authorization-url",
            "https://idp.example.com/realms/dev/authorize",
            "--oauth-token-url",
            "https://idp.example.com/realms/dev/token",
            "--oauth-refresh-url",
            "https://idp.example.com/realms/dev/refresh",
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
        assert settings.authorization_url.endswith("/authorize")
        assert settings.token_url.endswith("/token")
        assert settings.refresh_url.endswith("/refresh")

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


# ---------------------------------------------------------------------------
# RFC 7662 introspection (#253)
# ---------------------------------------------------------------------------

INTROSPECTION_URL = "https://idp.example.com/introspect"


class _IntrospectionEndpoint:
    """A fake JWKS + introspection endpoint sharing one ``urlopen`` patch."""

    def __init__(self, active: bool = True) -> None:
        self.active = active
        self.calls: list[urllib.request.Request] = []
        self.failure: Exception | None = None

    def __call__(self, request, timeout: float | None = None):
        url = getattr(request, "full_url", request)
        if url == INTROSPECTION_URL:
            self.calls.append(request)
            if self.failure is not None:
                raise self.failure
            return _Response({"active": self.active})
        return _Response({"keys": [JWK1]})


def _introspection_settings(**overrides) -> OAuthSettings:
    values = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_uri": JWKS_URL,
        "introspection_url": INTROSPECTION_URL,
        "introspection_client_id": "toolregistry",
        "introspection_client_secret": "s3cret",
    }
    values.update(overrides)
    return OAuthSettings(**values)


class TestIntrospection:
    def test_a_locally_valid_token_is_confirmed_with_the_endpoint(self, monkeypatch):
        endpoint = _IntrospectionEndpoint(active=True)
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)
        verifier = OAuthTokenVerifier(_introspection_settings())
        token = _token(KEY1, "key-1")

        assert verifier.verify(token) is not None
        assert len(endpoint.calls) == 1
        request = endpoint.calls[0]
        form = dict(urllib.parse.parse_qsl(request.data.decode("utf-8")))
        assert form["token"] == token
        assert form["token_type_hint"] == "access_token"
        assert request.get_header("Authorization").startswith("Basic ")

    def test_an_inactive_token_is_rejected(self, monkeypatch):
        endpoint = _IntrospectionEndpoint(active=False)
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)

        assert (
            OAuthTokenVerifier(_introspection_settings()).verify(_token(KEY1, "key-1"))
            is None
        )

    def test_the_answer_is_cached_for_the_ttl(self, monkeypatch):
        endpoint = _IntrospectionEndpoint(active=True)
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)
        verifier = OAuthTokenVerifier(
            _introspection_settings(introspection_cache_ttl=60)
        )
        token = _token(KEY1, "key-1")

        assert verifier.verify(token) is not None
        assert verifier.verify(token) is not None
        assert len(endpoint.calls) == 1

    def test_a_zero_ttl_disables_the_cache(self, monkeypatch):
        endpoint = _IntrospectionEndpoint(active=True)
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)
        verifier = OAuthTokenVerifier(
            _introspection_settings(introspection_cache_ttl=0)
        )
        token = _token(KEY1, "key-1")

        assert verifier.verify(token) is not None
        assert verifier.verify(token) is not None
        assert len(endpoint.calls) == 2

    def test_an_expired_cache_entry_is_refetched(self, monkeypatch):
        endpoint = _IntrospectionEndpoint(active=False)
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)
        verifier = OAuthTokenVerifier(
            _introspection_settings(introspection_cache_ttl=60)
        )
        token = _token(KEY1, "key-1")

        assert verifier.verify(token) is None
        endpoint.active = True
        # Simulate the TTL elapsing without sleeping.
        for digest in verifier._introspection._entries:
            verifier._introspection._entries[digest] = (False, 0.0)
        assert verifier.verify(token) is not None
        assert len(endpoint.calls) == 2

    def test_an_endpoint_failure_fails_closed_by_default(self, monkeypatch):
        endpoint = _IntrospectionEndpoint()
        endpoint.failure = OSError("connection refused")
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)

        assert (
            OAuthTokenVerifier(_introspection_settings()).verify(_token(KEY1, "key-1"))
            is None
        )

    def test_a_failure_is_cached_for_the_ttl(self, monkeypatch):
        endpoint = _IntrospectionEndpoint()
        endpoint.failure = OSError("connection refused")
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)
        verifier = OAuthTokenVerifier(
            _introspection_settings(
                introspection_optional=True, introspection_cache_ttl=60
            )
        )
        token = _token(KEY1, "key-1")

        assert verifier.verify(token) is not None
        endpoint.failure = None
        # The failed probe is reused, so an outage costs one timeout per TTL
        # instead of one per request.
        assert verifier.verify(token) is not None
        assert len(endpoint.calls) == 1

    def test_a_cached_failure_expires_like_any_other_answer(self, monkeypatch):
        endpoint = _IntrospectionEndpoint()
        endpoint.failure = OSError("connection refused")
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)
        verifier = OAuthTokenVerifier(
            _introspection_settings(
                introspection_optional=True, introspection_cache_ttl=60
            )
        )
        token = _token(KEY1, "key-1")

        assert verifier.verify(token) is not None
        endpoint.failure = None
        endpoint.active = False
        for digest in verifier._introspection._entries:
            verifier._introspection._entries[digest] = (
                verifier._introspection._entries[digest][0],
                0.0,
            )
        assert verifier.verify(token) is None
        assert len(endpoint.calls) == 2

    def test_optional_mode_falls_back_to_local_verification(self, monkeypatch):
        endpoint = _IntrospectionEndpoint()
        endpoint.failure = OSError("connection refused")
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)

        verifier = OAuthTokenVerifier(
            _introspection_settings(introspection_optional=True)
        )

        assert verifier.verify(_token(KEY1, "key-1")) is not None

    def test_a_failure_log_never_contains_the_token(self, monkeypatch):
        endpoint = _IntrospectionEndpoint()
        endpoint.failure = OSError("connection refused")
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)
        logged: list[str] = []

        class _Recorder:
            def warning(self, *args, **kwargs) -> None:
                logged.append(" ".join(str(arg) for arg in args) + repr(kwargs))

        monkeypatch.setattr("toolregistry_server.oauth.verifier.logger", _Recorder())
        token = _token(KEY1, "key-1")

        OAuthTokenVerifier(_introspection_settings()).verify(token)

        assert logged
        assert token not in "\n".join(logged)

    def test_no_endpoint_means_no_introspection(self, monkeypatch):
        endpoint = _IntrospectionEndpoint()
        monkeypatch.setattr(urllib.request, "urlopen", endpoint)

        assert _verifier().verify(_token(KEY1, "key-1")) is not None
        assert endpoint.calls == []

    def test_the_client_credential_is_required(self):
        with pytest.raises(ValueError, match="client id and client secret"):
            OAuthSettings(
                issuer=ISSUER,
                audience=AUDIENCE,
                introspection_url=INTROSPECTION_URL,
            )

    def test_a_negative_ttl_is_rejected(self):
        with pytest.raises(ValueError, match="must not be negative"):
            _introspection_settings(introspection_cache_ttl=-1)


# ---------------------------------------------------------------------------
# Claims to Principal parity (#239)
# ---------------------------------------------------------------------------


class TestPrincipalParity:
    def test_a_scope_can_override_one_tool(self, jwks):
        verifier = _verifier(
            scope_map={"tools:read": ("calculator",)},
            scope_tools={
                "tools:read": {
                    "calculator-danger": False,
                    "web-search": True,
                }
            },
        )
        principal = OAuthResolver(verifier).resolve(
            _token(KEY1, "key-1", scope="tools:read")
        )

        assert principal is not None
        assert principal.allows("calculator", "calculator-add") is True
        assert principal.allows("calculator", "calculator-danger") is False
        assert principal.allows("web", "web-search") is True

    def test_a_deny_wins_between_scopes(self, jwks):
        verifier = _verifier(
            scope_map={"tools:read": ("calculator",), "tools:admin": ("web",)},
            scope_tools={
                "tools:read": {"calculator-add": True},
                "tools:admin": {"calculator-add": False},
            },
        )
        principal = OAuthResolver(verifier).resolve(
            _token(KEY1, "key-1", scope="tools:read tools:admin")
        )

        assert principal is not None
        assert principal.allows("calculator", "calculator-add") is False

    def test_the_client_id_is_kept_apart_from_the_subject(self, jwks):
        principal = OAuthResolver(_verifier()).resolve(
            _token(KEY1, "key-1", sub="user-1", client_id="reports-bot")
        )

        assert principal is not None
        assert principal.label == "user-1"
        assert principal.client_id == "reports-bot"
        assert principal.to_dict()["client_id"] == "reports-bot"

    def test_azp_names_the_client_when_client_id_is_absent(self, jwks):
        principal = OAuthResolver(_verifier()).resolve(
            _token(KEY1, "key-1", azp="service-a")
        )

        assert principal is not None
        assert principal.client_id == "service-a"

    def test_a_concurrency_claim_sets_the_limit(self, jwks):
        principal = OAuthResolver(_verifier()).resolve(
            _token(KEY1, "key-1", max_concurrency=3)
        )

        assert principal is not None
        assert principal.max_concurrency == 3

    def test_an_invalid_concurrency_claim_is_ignored(self, jwks):
        for value in (0, -1, "many", True):
            principal = OAuthResolver(_verifier()).resolve(
                _token(KEY1, "key-1", max_concurrency=value)
            )
            assert principal is not None
            assert principal.max_concurrency is None

    def test_a_configured_claim_name_is_used(self, jwks):
        principal = OAuthResolver(
            _verifier(concurrency_claim="https://tools.example.com/limit")
        ).resolve(
            _token(
                KEY1,
                "key-1",
                **{"https://tools.example.com/limit": 2},
            )
        )

        assert principal is not None
        assert principal.max_concurrency == 2

    def test_a_static_client_registration_sets_the_limit(self, jwks):
        principal = OAuthResolver(
            _verifier(client_max_concurrency={"reports-bot": 2})
        ).resolve(_token(KEY1, "key-1", client_id="reports-bot"))

        assert principal is not None
        assert principal.max_concurrency == 2

    def test_a_valid_claim_wins_over_the_registration(self, jwks):
        principal = OAuthResolver(
            _verifier(
                client_max_concurrency={"reports-bot": 2},
            )
        ).resolve(_token(KEY1, "key-1", client_id="reports-bot", max_concurrency=5))

        assert principal is not None
        assert principal.max_concurrency == 5

    def test_two_clients_for_one_user_are_separate_principals(self, jwks):
        from toolregistry_server.isolation import principal_key

        first = OAuthResolver(_verifier()).resolve(
            _token(KEY1, "key-1", sub="user-1", client_id="reports-bot")
        )
        second = OAuthResolver(_verifier()).resolve(
            _token(KEY1, "key-1", sub="user-1", client_id="admin-bot")
        )

        assert first is not None and second is not None
        assert principal_key(first) == "oauth:reports-bot:user-1"
        assert principal_key(second) == "oauth:admin-bot:user-1"
        assert principal_key(first) != principal_key(second)

    def test_the_scp_claim_is_read_as_scopes(self, jwks):
        principal = OAuthResolver(
            _verifier(scope_map={"tools:read": ("calculator",)})
        ).resolve(_token(KEY1, "key-1", scp="tools:read"))

        assert principal is not None
        assert principal.allows("calculator", "calculator-add") is True

    def test_an_unmapped_scope_still_grants_nothing(self, jwks):
        principal = OAuthResolver(
            _verifier(scope_map={"tools:read": ("calculator",)})
        ).resolve(_token(KEY1, "key-1", scope="tools:admin"))

        assert principal is not None
        assert principal.allows("calculator", "calculator-add") is False


class TestSettingsFromConfig:
    def test_the_oauth_section_supplies_introspection(self, tmp_path):
        from toolregistry_server.cli import _oauth_settings
        from toolregistry_server.oauth.config import load_oauth_section

        path = tmp_path / "tools.yaml"
        path.write_text(
            "mode: denylist\n"
            "oauth:\n"
            f"  introspection_url: {INTROSPECTION_URL}\n"
            "  introspection_client_id: toolregistry\n"
            "  introspection_client_secret: s3cret\n"
            "  introspection_cache_ttl: 2\n",
            encoding="utf-8",
        )

        section = load_oauth_section(str(path))
        parsed = argparse.Namespace(oauth_issuer=ISSUER)
        settings = _oauth_settings(
            parsed, default_audience=AUDIENCE, config_path=str(path)
        )

        assert section["introspection_url"] == INTROSPECTION_URL
        assert settings is not None
        assert settings.introspection_url == INTROSPECTION_URL
        assert settings.introspection_cache_ttl == 2.0

    def test_a_cli_flag_wins_over_the_config_section(self, tmp_path):
        from toolregistry_server.cli import _oauth_settings

        path = tmp_path / "tools.yaml"
        path.write_text(
            "oauth:\n"
            f"  introspection_url: {INTROSPECTION_URL}\n"
            "  introspection_client_id: from-file\n"
            "  introspection_client_secret: file-secret\n",
            encoding="utf-8",
        )
        parsed = argparse.Namespace(
            oauth_issuer=ISSUER,
            oauth_introspection_client_id="from-flag",
        )

        settings = _oauth_settings(
            parsed, default_audience=AUDIENCE, config_path=str(path)
        )

        assert settings is not None
        assert settings.introspection_client_id == "from-flag"
        assert settings.introspection_client_secret == "file-secret"

    def test_a_missing_file_has_no_section(self, tmp_path):
        from toolregistry_server.oauth.config import load_oauth_section

        assert load_oauth_section(str(tmp_path / "missing.yaml")) == {}
        assert load_oauth_section(None) == {}

    def test_an_unknown_key_in_the_section_is_rejected(self, tmp_path):
        from toolregistry_server.oauth.config import load_oauth_section

        path = tmp_path / "tools.yaml"
        path.write_text(
            "oauth:\n"
            f"  introspection_url: {INTROSPECTION_URL}\n"
            "  introspection_ur1: typo\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="introspection_ur1"):
            load_oauth_section(str(path))

    def test_a_bad_config_limit_names_the_config_source(self, tmp_path):
        from toolregistry_server.cli import _oauth_settings

        path = tmp_path / "tools.yaml"
        path.write_text(
            "oauth:\n  client_concurrency:\n    reports-bot: 0\n",
            encoding="utf-8",
        )
        parsed = argparse.Namespace(oauth_issuer=ISSUER)

        with pytest.raises(ValueError, match=r"oauth\.client_concurrency"):
            _oauth_settings(parsed, default_audience=AUDIENCE, config_path=str(path))
