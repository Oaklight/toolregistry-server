"""HTTP-level integration tests for MCP server transports.

Tests that the Starlette apps used by ``run_sse`` and ``run_streamable_http``
return proper JSON error responses for unmatched routes. This is critical
because MCP clients (e.g. Claude Code) probe OAuth discovery endpoints
before connecting and expect parseable JSON responses.
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from toolregistry_server.adapters.mcp.server import _make_http_exception_handlers

# Paths that Claude Code probes for OAuth discovery
OAUTH_PROBE_PATHS = [
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
    "/.well-known/oauth-authorization-server/mcp",
    "/.well-known/openid-configuration/mcp",
]


def _make_stub_app() -> Starlette:
    """Create a minimal Starlette app with JSON error handlers and a stub route."""

    async def stub_endpoint(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return Starlette(
        routes=[Route("/mcp", endpoint=stub_endpoint, methods=["GET", "POST"])],
        exception_handlers=_make_http_exception_handlers(),
    )


class TestMakeHttpExceptionHandlers:
    """Tests for the _make_http_exception_handlers helper."""

    def test_returns_dict_with_expected_keys(self):
        handlers = _make_http_exception_handlers()
        assert isinstance(handlers, dict)
        assert 404 in handlers
        assert 405 in handlers

    def test_handlers_are_callable(self):
        handlers = _make_http_exception_handlers()
        assert callable(handlers[404])
        assert callable(handlers[405])


class TestStreamableHttpJsonErrors:
    """Tests that unmatched routes return JSON-RPC error responses."""

    @pytest.fixture
    def app(self):
        return _make_stub_app()

    @pytest.mark.parametrize("path", OAUTH_PROBE_PATHS)
    @pytest.mark.anyio
    async def test_oauth_probe_returns_json_404(self, app, path):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(path)

        assert response.status_code == 404
        assert "application/json" in response.headers.get("content-type", "")

        body = response.json()
        # Must NOT use OAuth error fields (error + error_description)
        # to avoid Claude Code interpreting it as "OAuth misconfigured"
        assert "error" not in body
        assert "error_description" not in body
        assert isinstance(body["detail"], str)

    @pytest.mark.anyio
    async def test_arbitrary_unknown_path_returns_json_404(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/nonexistent")

        assert response.status_code == 404
        body = response.json()
        assert "detail" in body

    @pytest.mark.anyio
    async def test_wrong_method_returns_json_405(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.delete("/mcp")

        assert response.status_code == 405
        assert "application/json" in response.headers.get("content-type", "")
        body = response.json()
        assert "detail" in body

    @pytest.mark.anyio
    async def test_valid_route_still_works(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/mcp")

        assert response.status_code == 200
        assert response.text == "ok"


# ---------------------------------------------------------------------------
# Public URL resolution for Protected Resource Metadata (#78)
# ---------------------------------------------------------------------------


class _RecordingLogger:
    """Logger stand-in that records what was logged.

    The package's vendored structlog writes through a stream captured at
    import time, so pytest's capture fixtures do not see it; recording on
    the module's logger tests the decision instead of the sink.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def _record(self, message, *args, **kwargs) -> None:
        self.messages.append(str(message))

    debug = info = warning = error = _record


@pytest.fixture
def recording_logger(monkeypatch):
    """Replace the MCP server module's logger with a recorder."""
    from toolregistry_server.adapters.mcp import server as server_mod

    recorder = _RecordingLogger()
    monkeypatch.setattr(server_mod, "logger", recorder)
    return recorder


class TestResolvePublicUrl:
    """The advertised metadata URL must be one a client can actually reach."""

    def test_uses_configured_public_url(self, recording_logger):
        """An operator-supplied URL wins over the bind address."""
        from toolregistry_server.adapters.mcp.server import resolve_public_url

        resolved = resolve_public_url(
            "https://tools.example.com/mcp", "0.0.0.0", 8000, "/mcp"
        )

        assert resolved == "https://tools.example.com/mcp"
        assert recording_logger.messages == []

    def test_derives_from_bind_address_when_direct(self, recording_logger):
        """Without a public URL, a directly reachable bind address is used."""
        from toolregistry_server.adapters.mcp.server import resolve_public_url

        resolved = resolve_public_url(None, "127.0.0.1", 9000, "/mcp")

        assert resolved == "http://127.0.0.1:9000/mcp"
        assert recording_logger.messages == []

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", ""])
    def test_warns_when_advertising_an_unreachable_bind_address(
        self, host, recording_logger
    ):
        """A wildcard bind address cannot be reached by clients — say so."""
        from toolregistry_server.adapters.mcp.server import resolve_public_url

        resolved = resolve_public_url(None, host, 8000, "/mcp")

        assert resolved == f"http://{host}:8000/mcp"
        assert len(recording_logger.messages) == 1
        warning = recording_logger.messages[0]
        assert "no client can reach" in warning
        assert "--server-url" in warning
