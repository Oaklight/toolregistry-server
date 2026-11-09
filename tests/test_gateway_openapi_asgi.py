"""Tests for the FastAPI assembly used by the gateway's OpenAPI frontend.

The assembly lives in :mod:`toolregistry_server.gateway.openapi_asgi` and
requires the ``gateway`` extra.  Everything shared with the standalone
httpserver backend is tested in ``tests/test_openapi_adapter.py``; these
tests stay FastAPI-only because they exercise ``TestClient``/ASGI wiring.
"""

import asyncio
import threading
from io import StringIO

import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server._vendor.structlog import (
    BoundLogger,
    ConsoleRenderer,
    PrintLogger,
    TimeStamper,
    add_log_level,
)
from toolregistry_server.gateway.openapi_asgi import (
    BodySizeLimitMiddleware,
    add_etag_middleware,
    add_tools_endpoint,
    create_openapi_app,
    route_table_to_router,
    setup_dynamic_openapi,
)


def _capture_logs(monkeypatch, module):
    """Route *module*'s logger into a StringIO and return the stream."""
    stream = StringIO()
    log = BoundLogger(
        logger=PrintLogger(file=stream),
        processors=[add_log_level, TimeStamper(), ConsoleRenderer(colors=False)],
        context={},
    )
    monkeypatch.setattr(module, "logger", log)
    return stream


# ============== Fixtures ==============


@pytest.fixture
def registry() -> ToolRegistry:
    """Create a ToolRegistry with sample tools."""
    reg = ToolRegistry()

    @reg.register
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    @reg.register
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @reg.register
    async def async_greet(name: str) -> str:
        """Async greeting."""
        return f"Hello async, {name}!"

    return reg


@pytest.fixture
def route_table(registry: ToolRegistry) -> RouteTable:
    """Create a RouteTable from the registry."""
    return RouteTable(registry)


# ============== Router Generation Tests ==============
# ============== Router Generation Tests ==============


class TestRouteTableToRouter:
    """Tests for route_table_to_router function."""

    def test_creates_router(self, route_table: RouteTable):
        """Test that router is created successfully."""
        from fastapi import APIRouter

        router = route_table_to_router(route_table)
        assert isinstance(router, APIRouter)

    def test_router_has_routes(self, route_table: RouteTable):
        """Test that router has routes for all tools."""
        router = route_table_to_router(route_table)
        # Should have routes for greet, add, and async_greet
        assert len(router.routes) >= 3

    def test_router_with_prefix(self, route_table: RouteTable):
        """Test router with custom prefix."""
        router = route_table_to_router(route_table, prefix="/api")
        assert router.prefix == "/api"


# ============== App Creation Tests ==============


class TestCreateOpenAPIApp:
    """Tests for create_openapi_app function."""

    def test_creates_app(self, route_table: RouteTable):
        """Test that app is created successfully."""
        from fastapi import FastAPI

        app = create_openapi_app(route_table)
        assert isinstance(app, FastAPI)

    def test_app_with_custom_metadata(self, route_table: RouteTable):
        """Test app with custom title and version."""
        app = create_openapi_app(
            route_table,
            title="Test API",
            version="2.0.0",
            description="Test description",
        )
        assert app.title == "Test API"
        assert app.version == "2.0.0"
        assert app.description == "Test description"


# ============== Dynamic OpenAPI Tests ==============


class TestDynamicOpenAPI:
    """Tests for setup_dynamic_openapi function."""

    def test_dynamic_schema_updates(self, route_table: RouteTable):
        """Test that schema updates dynamically."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        router = route_table_to_router(route_table)
        app.include_router(router)
        setup_dynamic_openapi(app, route_table)

        client = TestClient(app)

        # Initially all tools visible
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        assert "/tools/default/greet" in paths
        assert "/tools/default/add" in paths

        # Disable one tool
        route_table.disable("greet")

        # Schema should update
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        assert "/tools/default/greet" not in paths
        assert "/tools/default/add" in paths

        # Re-enable
        route_table.enable("greet")

        # Schema should update again
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        assert "/tools/default/greet" in paths


class TestAddToolsEndpoint:
    """Tests for add_tools_endpoint function."""

    def test_add_tools_endpoint_to_app(self, route_table: RouteTable):
        """Test adding /tools endpoint to an existing app."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        add_tools_endpoint(app, route_table)

        client = TestClient(app)
        response = client.get("/tools")
        assert response.status_code == 200
        assert "tools" in response.json()


class TestFastAPITracebackLogging:
    """The FastAPI assembly's traceback logging.

    The httpserver app logs the same traceback from its own handler; see
    ``tests/test_openapi_server_app.py``.
    """

    def test_sync_tool_exception_traceback_is_logged(
        self, route_table: RouteTable, monkeypatch
    ):
        """The FastAPI path keeps the traceback the client must not receive."""
        import toolregistry_server.gateway.openapi_asgi as assembly

        stream = _capture_logs(monkeypatch, assembly)

        def fail_sync() -> str:
            raise RuntimeError("boom")

        route_table._registry.register(fail_sync, name="fail_sync")
        route_table._rebuild()

        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/tools/default/fail_sync", json={})
        assert response.status_code == 500

        output = stream.getvalue()
        assert "Traceback (most recent call last)" in output
        assert "RuntimeError: boom" in output

    def test_async_tool_exception_traceback_is_logged(
        self, route_table: RouteTable, monkeypatch
    ):
        """The async endpoint logs the same traceback shape."""
        import toolregistry_server.gateway.openapi_asgi as assembly

        stream = _capture_logs(monkeypatch, assembly)

        async def fail_async() -> str:
            raise ValueError("async boom")

        route_table._registry.register(fail_async, name="fail_async")
        route_table._rebuild()

        from fastapi.testclient import TestClient

        app = create_openapi_app(route_table)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/tools/default/fail_async", json={})
        assert response.status_code == 500

        output = stream.getvalue()
        assert "Traceback (most recent call last)" in output
        assert "ValueError: async boom" in output


class TestAsyncValidationOffLoop:
    """The async endpoint must not validate on the event loop."""

    async def test_async_endpoint_validation_does_not_block_the_loop(
        self, route_table: RouteTable, monkeypatch
    ):

        import httpx

        import toolregistry_server.gateway.openapi_asgi as assembly

        entered = threading.Event()
        release = threading.Event()

        def blocking_validate(route, payload, **_kwargs):
            entered.set()
            assert release.wait(5)
            return payload

        monkeypatch.setattr(assembly, "_validate_arguments", blocking_validate)

        async def async_tool() -> str:
            return "ok"

        route_table._registry.register(async_tool, name="async_tool")
        route_table._rebuild()

        app = create_openapi_app(route_table)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            tool_request = asyncio.create_task(
                client.post("/tools/default/async_tool", json={})
            )
            try:
                assert await asyncio.to_thread(entered.wait, 5)

                # If validation still ran on the loop, this could not be
                # served until ``release`` was set in the ``finally`` block.
                response = await asyncio.wait_for(client.get("/ping"), timeout=1)
                assert response.status_code == 200

                release.set()
                tool_response = await asyncio.wait_for(tool_request, timeout=5)
                assert tool_response.status_code == 200
                assert tool_response.json() == "ok"
            finally:
                release.set()
                if not tool_request.done():
                    await asyncio.wait_for(tool_request, timeout=5)


class TestAddETagMiddleware:
    """Tests for add_etag_middleware function."""

    def test_add_etag_middleware_to_app(self, route_table: RouteTable):
        """Test adding ETag middleware to an existing app."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        add_tools_endpoint(app, route_table)
        add_etag_middleware(app, route_table)

        client = TestClient(app)

        # Get ETag
        response1 = client.get("/tools")
        etag = response1.headers.get("ETag")
        assert etag is not None

        # Conditional request
        response2 = client.get("/tools", headers={"If-None-Match": etag})
        assert response2.status_code == 304


class TestFastAPIBodyLimit:
    """The fastapi backend enforces the shared request-body cap (#148)."""

    @staticmethod
    def _client(table: RouteTable, max_body_size: int):
        from fastapi.testclient import TestClient

        return TestClient(create_openapi_app(table, max_body_size=max_body_size))

    def test_default_cap_is_one_mib(self, route_table: RouteTable):
        from toolregistry_server._vendor.httpserver import DEFAULT_MAX_BODY_SIZE

        app = create_openapi_app(route_table)
        caps = [
            entry.kwargs["max_body_size"]
            for entry in app.user_middleware
            if entry.cls is BodySizeLimitMiddleware
        ]
        assert caps == [DEFAULT_MAX_BODY_SIZE]

    def test_small_request_passes(self, route_table: RouteTable):
        client = self._client(route_table, max_body_size=1024)
        response = client.post("/tools/default/add", json={"a": 2, "b": 3})
        assert response.status_code == 200
        assert response.json() == 5

    def test_body_over_the_cap_is_a_413(self, route_table: RouteTable):
        client = self._client(route_table, max_body_size=1024)
        response = client.post(
            "/tools/default/add",
            content=b"x" * 2000,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json() == {"detail": "Request body too large"}

    def test_raising_the_cap_accepts_the_body(self, route_table: RouteTable):
        client = self._client(route_table, max_body_size=8192)
        response = client.post(
            "/tools/default/add",
            content=b'{"a": 2, "b": 3}' + b" " * 2000,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json() == 5

    def test_non_positive_cap_is_rejected(self, route_table: RouteTable):
        with pytest.raises(ValueError, match="max_body_size"):
            create_openapi_app(route_table, max_body_size=0)


class TestBodySizeLimitMiddleware:
    """The ASGI guard rejects before buffering the whole body (#148)."""

    @staticmethod
    async def _read_body_and_respond(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                # What Starlette does when the client goes away mid-read.
                raise RuntimeError("client disconnected")
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def test_declared_length_is_rejected_without_reading(self):
        read = False

        async def receive():
            nonlocal read
            read = True
            return {"type": "http.request", "body": b"", "more_body": False}

        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        app = BodySizeLimitMiddleware(self._read_body_and_respond, max_body_size=1024)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/x",
            "headers": [(b"content-length", b"2048")],
        }
        await app(scope, receive, send)

        assert not read
        assert [m["type"] for m in sent] == [
            "http.response.start",
            "http.response.body",
        ]
        assert sent[0]["status"] == 413
        assert sent[1]["body"] == b'{"detail":"Request body too large"}'

    async def test_streamed_body_is_counted_message_by_message(self):
        messages = [
            {"type": "http.request", "body": b"x" * 600, "more_body": True},
            {"type": "http.request", "body": b"x" * 600, "more_body": False},
        ]

        async def receive():
            return messages.pop(0)

        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        app = BodySizeLimitMiddleware(self._read_body_and_respond, max_body_size=1024)
        scope = {"type": "http", "method": "POST", "path": "/x", "headers": []}
        await app(scope, receive, send)

        assert sent[0]["status"] == 413
        assert sent[-1]["body"] == b'{"detail":"Request body too large"}'
