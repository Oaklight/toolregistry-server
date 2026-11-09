"""Tests for the OpenAPI adapter module.

Contract tests run against both backends through ``make_client``.  Tests
that are genuinely about the FastAPI assembly (``create_openapi_app``, the
ASGI middleware, ``TestClient``-only loop semantics) stay FastAPI-only; they
are removed with the assembly when workstream 4 retires it.
"""

import asyncio
import json as _json
import threading
import time
import urllib.error
import urllib.request
from io import StringIO
from urllib.parse import urlencode

import pytest
from toolregistry import ToolRegistry
from toolregistry.tool import Tool, ToolMetadata

from toolregistry_server import RouteTable
from toolregistry_server._vendor.structlog import (
    BoundLogger,
    ConsoleRenderer,
    PrintLogger,
    TimeStamper,
    add_log_level,
)
from toolregistry_server.adapters.openapi import create_openapi_app
from toolregistry_server.adapters.openapi.adapter import (
    _validate_arguments,
    add_tools_endpoint,
    route_table_to_router,
    setup_dynamic_openapi,
)
from toolregistry_server.adapters.openapi.middleware import add_etag_middleware
from toolregistry_server.adapters.openapi.server_app import create_httpserver_app


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


# ============== Backend-neutral client ==============


class _Headers(dict):
    """Case-insensitive response headers, like ``httpx.Headers``."""

    def __init__(self, headers=None):
        super().__init__()
        # ``dict(mapping)`` bypasses the override below, so copy explicitly.
        for key, value in (headers or {}).items():
            self[key] = value

    def __getitem__(self, key):
        return super().__getitem__(str(key).lower())

    def __setitem__(self, key, value):
        super().__setitem__(str(key).lower(), value)

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)

    def __contains__(self, key):
        return super().__contains__(str(key).lower())


class _Reply:
    """The subset of ``httpx.Response`` the contract tests use."""

    def __init__(self, status_code: int, headers, content: bytes) -> None:
        self.status_code = status_code
        self.headers = _Headers(headers)
        self.content = content

    def json(self):
        return _json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode()


class _FastAPIClient:
    """Drive the FastAPI app in process through ``TestClient``."""

    def __init__(self, table, **kwargs) -> None:
        from fastapi.testclient import TestClient

        self._client = TestClient(
            create_openapi_app(table, **kwargs), raise_server_exceptions=False
        )

    def close(self) -> None:
        self._client.close()

    def get(self, path, headers=None) -> _Reply:
        response = self._client.get(path, headers=headers)
        return _Reply(response.status_code, dict(response.headers), response.content)

    def post(self, path, json=None, content=None, data=None, headers=None) -> _Reply:
        kwargs = {}
        if json is not None:
            kwargs["json"] = json
        if content is not None:
            kwargs["content"] = content
        if data is not None:
            kwargs["data"] = data
        response = self._client.post(path, headers=headers, **kwargs)
        return _Reply(response.status_code, dict(response.headers), response.content)


class _HttpServerClient:
    """Drive the httpserver app over a real socket on an ephemeral port."""

    def __init__(self, table, **kwargs) -> None:
        self._app = create_httpserver_app(table, **kwargs)
        self._thread = threading.Thread(
            target=self._app.run,
            kwargs={"host": "127.0.0.1", "port": 0},
            daemon=True,
        )
        self._thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not self._app.port:
            time.sleep(0.02)
        if not self._app.port:
            raise RuntimeError("the httpserver app did not start")
        self._base = f"http://127.0.0.1:{self._app.port}"

    def close(self) -> None:
        self._app.shutdown()
        self._thread.join(timeout=5)

    def _request(self, request) -> _Reply:
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return _Reply(response.status, dict(response.headers), response.read())
        except urllib.error.HTTPError as error:
            return _Reply(error.code, dict(error.headers), error.read())

    def get(self, path, headers=None) -> _Reply:
        return self._request(
            urllib.request.Request(
                self._base + path, headers=dict(headers or {}), method="GET"
            )
        )

    def post(self, path, json=None, content=None, data=None, headers=None) -> _Reply:
        merged = dict(headers or {})
        body = content
        if json is not None:
            body = _json.dumps(json).encode()
            merged.setdefault("Content-Type", "application/json")
        elif data is not None:
            body = urlencode(data).encode()
            merged.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return self._request(
            urllib.request.Request(
                self._base + path, data=body, headers=merged, method="POST"
            )
        )


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """``App.run`` installs signal handlers, which only work in the main thread."""
    loop_classes = [asyncio.BaseEventLoop]
    unix = getattr(asyncio, "unix_events", None)
    if unix is not None:
        loop_classes.append(unix._UnixSelectorEventLoop)  # noqa: SLF001 - test shim
    for loop_class in loop_classes:
        monkeypatch.setattr(loop_class, "add_signal_handler", lambda *a, **k: None)


@pytest.fixture(params=["fastapi", "httpserver"])
def make_client(request):
    """Build a client for one backend; close every client at teardown."""
    clients = []

    def factory(table, **kwargs):
        if request.param == "fastapi":
            client = _FastAPIClient(table, **kwargs)
        else:
            client = _HttpServerClient(table, **kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


# ============== Request Validation Tests ==============


class TestValidateArguments:
    """Bodies are validated against the tool's own schema, not a model."""

    def test_signature_tool_coerces_and_drops_unknown_keys(self, route_table):
        route = route_table.get_route("add")
        args = _validate_arguments(route, {"a": "2", "b": 3, "extra": True})
        assert args == {"a": 2, "b": 3}

    def test_signature_tool_rejects_a_bad_value(self, route_table):
        from fastapi import HTTPException

        route = route_table.get_route("add")
        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, {"a": "not-a-number", "b": 1})
        assert excinfo.value.status_code == 422
        detail = excinfo.value.detail
        assert isinstance(detail, list) and detail
        assert detail[0]["loc"][0] == "body"

    def test_proxy_tool_uses_json_schema(self, route_table):
        from fastapi import HTTPException
        from toolregistry.tool import Tool

        def proxy(**kwargs):
            return kwargs

        tool = Tool(
            name="proxy",
            description="proxy tool",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            callable=proxy,
        )
        route_table._registry.register(tool)  # noqa: SLF001 - test fixture
        route_table.refresh_all()
        route = route_table.get_route("proxy")

        assert _validate_arguments(route, {"q": "ok", "junk": 1}) == {"q": "ok"}
        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, {})
        assert excinfo.value.status_code == 422

    @pytest.mark.parametrize(
        "additional",
        [{}, {"type": "integer"}, True],
        ids=["empty-schema", "typed-schema", "true"],
    )
    def test_proxy_tool_keeps_extras_when_additional_properties_allows(
        self, route_table, additional
    ):
        """``{}`` and other schema objects permit extras, not just ``True``."""

        def proxy(**kwargs):
            return kwargs

        tool = Tool(
            name="proxy_open",
            description="proxy tool",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
                "additionalProperties": additional,
            },
            callable=proxy,
        )
        route_table._registry.register(tool)  # noqa: SLF001 - test fixture
        route_table.refresh_all()
        route = route_table.get_route("proxy_open")

        assert _validate_arguments(route, {"q": "ok", "extra": 1}) == {
            "q": "ok",
            "extra": 1,
        }

    def test_proxy_tool_filters_extras_when_additional_properties_is_absent(
        self, route_table
    ):
        """An absent keyword keeps the historical silent filtering."""

        def proxy(**kwargs):
            return kwargs

        tool = Tool(
            name="proxy_absent",
            description="proxy tool",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            callable=proxy,
        )
        route_table._registry.register(tool)  # noqa: SLF001 - test fixture
        route_table.refresh_all()
        route = route_table.get_route("proxy_absent")

        assert _validate_arguments(route, {"q": "ok", "extra": 1}) == {"q": "ok"}

    def test_proxy_tool_rejects_extras_when_additional_properties_is_false(
        self, route_table
    ):
        """An explicit ``False`` is enforced by ``schema_validate`` first."""
        from fastapi import HTTPException

        def proxy(**kwargs):
            return kwargs

        tool = Tool(
            name="proxy_closed",
            description="proxy tool",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
                "additionalProperties": False,
            },
            callable=proxy,
        )
        route_table._registry.register(tool)  # noqa: SLF001 - test fixture
        route_table.refresh_all()
        route = route_table.get_route("proxy_closed")

        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, {"q": "ok", "extra": 1})
        assert excinfo.value.status_code == 422
        assert excinfo.value.detail[0]["type"] == "additionalProperties"

    def test_proxy_tool_does_not_execute_spec_regexes(self, route_table, monkeypatch):
        """A spec-supplied regex never runs against the request body (#384).

        The validator receives a copy without ``pattern`` and
        ``patternProperties``; the served schema keeps both.
        """
        import toolregistry_server.adapters.openapi.adapter as adapter

        def proxy(**kwargs):
            return kwargs

        tool = Tool(
            name="patterned_proxy",
            description="proxy with spec regexes",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string", "pattern": "(a+)+$"}},
                "patternProperties": {"^x-": {"type": "integer"}},
                "additionalProperties": False,
            },
            callable=proxy,
        )
        route_table._registry.register(tool)  # noqa: SLF001 - test fixture
        route_table.refresh_all()
        route = route_table.get_route("patterned_proxy")

        seen = {}

        def spy(instance, schema, **kwargs):
            seen["schema"] = schema

        monkeypatch.setattr(adapter, "schema_validate", spy)
        _validate_arguments(route, {"q": "a" * 40 + "!", "x-extra": 1})

        validated = seen["schema"]
        assert "pattern" not in validated["properties"]["q"]
        assert "patternProperties" not in validated
        # Keys the patterns used to cover are not treated as unknown.
        assert validated["additionalProperties"] is True

        served = route.served_parameters_schema
        assert served["properties"]["q"]["pattern"] == "(a+)+$"
        assert served["patternProperties"] == {"^x-": {"type": "integer"}}

    def test_override_schema_still_enforces_patterns(self, route_table):
        """An override is operator code, so its regexes still run."""
        from fastapi import HTTPException

        def proxy(**kwargs):
            return kwargs

        tool = Tool(
            name="override_proxy",
            description="proxy",
            parameters={"type": "object", "properties": {}},
            callable=proxy,
        )
        route_table._registry.register(tool)  # noqa: SLF001 - test fixture
        route_table.refresh_all()
        route_table.override_parameters(
            "override_proxy",
            {
                "type": "object",
                "properties": {"q": {"type": "string", "pattern": "^[0-9]+$"}},
            },
        )
        route = route_table.get_route("override_proxy")

        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, {"q": "abc"})
        assert excinfo.value.status_code == 422
        assert _validate_arguments(route, {"q": "123"}) == {"q": "123"}

    def test_without_spec_regex_leaves_nested_additional_properties_alone(self):
        """Relaxing one level must not descend into nested schemas."""
        from toolregistry_server.adapters.openapi.adapter import _without_spec_regex

        schema = {
            "type": "object",
            "patternProperties": {"^x-": {"type": "string"}},
            "additionalProperties": False,
            "properties": {
                "nested": {"type": "object", "additionalProperties": False},
                "pat": {"type": "string", "pattern": "^[a-z]+$"},
            },
            "items": {"type": "string", "pattern": "^[0-9]+$"},
        }

        stripped = _without_spec_regex(schema)

        assert stripped["additionalProperties"] is True
        assert stripped["properties"]["nested"]["additionalProperties"] is False
        assert "pattern" not in stripped["properties"]["pat"]
        assert "pattern" not in stripped["items"]
        # The input is untouched.
        assert schema["patternProperties"] == {"^x-": {"type": "string"}}
        assert schema["properties"]["pat"]["pattern"] == "^[a-z]+$"

    @pytest.mark.parametrize("body", [[], "str", 123, None, True])
    def test_non_object_body_is_a_422(self, route_table, body):
        """Scalars, arrays and null are rejected, not crashed on."""
        from fastapi import HTTPException

        route = route_table.get_route("add")
        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, body)
        assert excinfo.value.status_code == 422
        detail = excinfo.value.detail
        assert isinstance(detail, list) and detail
        assert detail[0]["loc"] == ["body"]
        assert detail[0]["type"] == "model_attributes_type"


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


# ============== Integration Tests ==============


class TestOpenAPIIntegration:
    """Integration tests, run against both backends."""

    def test_sync_endpoint(self, route_table: RouteTable, make_client):
        """Test calling a sync endpoint."""
        client = make_client(route_table)

        response = client.post(
            "/tools/default/add",
            json={"a": 2, "b": 3},
        )
        assert response.status_code == 200
        assert response.json() == 5

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"json": []},
            {"json": "str"},
            {"json": 123},
            {"data": {"a": 1}},
        ],
        ids=["array", "string", "number", "form"],
    )
    def test_non_object_body_returns_422_not_500(
        self, route_table: RouteTable, make_client, kwargs
    ):
        """Malformed bodies are client errors, never internal server errors."""
        client = make_client(route_table)
        response = client.post("/tools/default/add", **kwargs)
        assert response.status_code == 422
        detail = response.json()["detail"]
        # The second loc element is parser-specific: a form body is a JSON
        # decode error on the httpserver, a body-type error on FastAPI.
        assert detail[0]["loc"][0] == "body"

    def test_async_endpoint(self, route_table: RouteTable, make_client):
        """Test calling an async endpoint."""
        client = make_client(route_table)

        response = client.post(
            "/tools/default/async_greet",
            json={"name": "World"},
        )
        assert response.status_code == 200
        assert response.json() == "Hello async, World!"

    def test_disabled_tool_returns_503(self, route_table: RouteTable, make_client):
        """Test that disabled tools return 503."""
        client = make_client(route_table)

        # Disable the tool
        route_table.disable("add", reason="Testing")

        response = client.post(
            "/tools/default/add",
            json={"a": 2, "b": 3},
        )
        assert response.status_code == 503
        assert "disabled" in response.json()["detail"].lower()

    def test_openapi_schema_excludes_disabled(
        self, route_table: RouteTable, make_client
    ):
        """Test that OpenAPI schema excludes disabled tools."""
        client = make_client(route_table)

        # Get schema before disabling
        response = client.get("/openapi.json")
        schema_before = response.json()
        assert "/tools/default/add" in schema_before["paths"]

        # Disable the tool
        route_table.disable("add", reason="Testing")

        # Get schema after disabling
        response = client.get("/openapi.json")
        schema_after = response.json()
        assert "/tools/default/add" not in schema_after["paths"]

    def test_events_cap_rejects_new_subscribers(
        self, route_table: RouteTable, make_client
    ):
        """A capped ``/events`` returns 503 instead of subscribing (#149)."""
        client = make_client(route_table, max_sse_subscribers=0)
        response = client.get("/events")
        assert response.status_code == 503
        assert response.json()["detail"] == "Too many event subscribers"

    def test_negative_events_cap_is_rejected(
        self, route_table: RouteTable, make_client
    ):
        with pytest.raises(ValueError, match="subscribers"):
            make_client(route_table, max_sse_subscribers=-1)


# ============== Dynamic OpenAPI Tests ==============


class TestDynamicOpenAPI:
    """Tests for setup_dynamic_openapi function."""

    def test_dynamic_schema_updates(self, route_table: RouteTable):
        """Test that schema updates dynamically."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from toolregistry_server.adapters.openapi.adapter import route_table_to_router

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


# ============== Tools Endpoint Tests ==============


class TestToolsEndpoint:
    """Tests for the /tools endpoint, run against both backends."""

    def test_spec_regexes_stay_in_the_published_schema(
        self, route_table: RouteTable, make_client
    ):
        """Filtering the validation copy must not change the contract."""

        def proxy(**kwargs):
            return kwargs

        tool = Tool(
            name="published_regex",
            description="proxy with a spec regex",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string", "pattern": "^[a-z]+$"}},
                "patternProperties": {"^x-": {"type": "integer"}},
            },
            callable=proxy,
        )
        route_table._registry.register(tool)  # noqa: SLF001 - test fixture
        route_table.refresh_all()
        client = make_client(route_table)

        before = client.get("/openapi.json").json()
        etag_before = client.get("/tools").headers["etag"]

        _validate_arguments(route_table.get_route("published_regex"), {"q": "abc"})

        after = client.get("/openapi.json").json()
        assert after == before
        operation = after["paths"]["/tools/default/published_regex"]["post"]
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert schema["properties"]["q"]["pattern"] == "^[a-z]+$"
        assert schema["patternProperties"] == {"^x-": {"type": "integer"}}
        # The served schema drives the ETag; validation must not move it.
        assert client.get("/tools").headers["etag"] == etag_before

    def test_tools_endpoint_returns_list(self, route_table: RouteTable, make_client):
        """Test that /tools endpoint returns a list of tools."""
        client = make_client(route_table)

        response = client.get("/tools")
        assert response.status_code == 200

        data = response.json()
        assert "tools" in data
        assert "etag" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) == 3  # greet, add, async_greet

    def test_tools_endpoint_tool_structure(self, route_table: RouteTable, make_client):
        """Test that each tool has the expected structure."""
        client = make_client(route_table)

        response = client.get("/tools")
        data = response.json()

        for tool in data["tools"]:
            assert "name" in tool
            assert "namespace" in tool
            assert "method" in tool
            assert "path" in tool
            assert "description" in tool

    def test_tools_endpoint_etag_header(self, route_table: RouteTable, make_client):
        """Test that /tools endpoint returns ETag header."""
        client = make_client(route_table)

        response = client.get("/tools")
        assert response.status_code == 200
        assert "ETag" in response.headers

        # ETag in header should match etag in body
        data = response.json()
        assert response.headers["ETag"] == data["etag"]

    def test_tools_endpoint_conditional_request(
        self, route_table: RouteTable, make_client
    ):
        """Test that /tools endpoint supports If-None-Match."""
        client = make_client(route_table)

        # First request to get ETag
        response1 = client.get("/tools")
        assert response1.status_code == 200
        etag = response1.headers["ETag"]

        # Second request with If-None-Match
        response2 = client.get("/tools", headers={"If-None-Match": etag})
        assert response2.status_code == 304

    def test_tools_endpoint_etag_changes_on_disable(
        self, route_table: RouteTable, make_client
    ):
        """Test that ETag changes when a tool is disabled."""
        client = make_client(route_table)

        # Get initial ETag
        response1 = client.get("/tools")
        etag1 = response1.headers["ETag"]

        # Disable a tool
        route_table.disable("add")

        # Get new ETag
        response2 = client.get("/tools")
        etag2 = response2.headers["ETag"]

        # ETags should be different
        assert etag1 != etag2

    def test_tools_endpoint_excludes_disabled(
        self, route_table: RouteTable, make_client
    ):
        """Test that /tools endpoint excludes disabled tools."""
        client = make_client(route_table)

        # Initially all tools visible
        response1 = client.get("/tools")
        tools1 = response1.json()["tools"]
        tool_names1 = {t["name"] for t in tools1}
        assert "add" in tool_names1

        # Disable a tool
        route_table.disable("add")

        # Tool should be excluded
        response2 = client.get("/tools")
        tools2 = response2.json()["tools"]
        tool_names2 = {t["name"] for t in tools2}
        assert "add" not in tool_names2


# ============== ETag Middleware Tests ==============


class TestETagMiddleware:
    """Tests for ETag middleware, run against both backends."""

    def test_etag_middleware_adds_header(self, route_table: RouteTable, make_client):
        """Test that ETag middleware adds ETag header to responses."""
        client = make_client(route_table, enable_etag=True)

        response = client.get("/tools")
        assert "ETag" in response.headers

    def test_etag_middleware_304_on_match(self, route_table: RouteTable, make_client):
        """Test that middleware returns 304 when ETag matches."""
        client = make_client(route_table, enable_etag=True)

        # Get ETag
        response1 = client.get("/tools")
        etag = response1.headers["ETag"]

        # Request with matching If-None-Match
        response2 = client.get("/tools", headers={"If-None-Match": etag})
        assert response2.status_code == 304

    def test_etag_middleware_200_on_mismatch(
        self, route_table: RouteTable, make_client
    ):
        """Test that middleware returns 200 when ETag doesn't match."""
        client = make_client(route_table, enable_etag=True)

        # Request with non-matching If-None-Match
        response = client.get("/tools", headers={"If-None-Match": '"invalid"'})
        assert response.status_code == 200

    def test_etag_middleware_disabled(self, route_table: RouteTable, make_client):
        """Test that ETag middleware can be disabled."""
        # Create app without ETag middleware
        client = make_client(route_table, enable_etag=False)

        # /tools endpoint still adds ETag header (it's built into the endpoint)
        response = client.get("/tools")
        assert response.status_code == 200
        # The endpoint itself adds ETag, but middleware doesn't intercept

    def test_etag_middleware_only_on_get(self, route_table: RouteTable, make_client):
        """Test that ETag middleware only applies to GET requests."""
        client = make_client(route_table, enable_etag=True)

        # POST request should not be affected by ETag middleware
        response = client.post(
            "/tools/default/add",
            json={"a": 1, "b": 2},
            headers={"If-None-Match": route_table.etag},
        )
        # Should execute normally, not return 304
        assert response.status_code == 200
        assert response.json() == 3


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


class TestToolExecutionErrorHandling:
    """Tests for tool execution error handling, run against both backends."""

    def test_sync_tool_exception_returns_500(
        self, route_table: RouteTable, make_client
    ):
        """Sync tool that raises should return structured 500."""

        def fail_sync() -> str:
            raise RuntimeError("boom")

        route_table._registry.register(fail_sync, name="fail_sync")
        route_table._rebuild()
        client = make_client(route_table)

        response = client.post("/tools/default/fail_sync", json={})
        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail == "Tool 'fail_sync' execution failed"
        assert "boom" not in detail

    def test_async_tool_exception_returns_500(
        self, route_table: RouteTable, make_client
    ):
        """Async tool that raises should return structured 500."""

        async def fail_async() -> str:
            raise ValueError("async boom")

        route_table._registry.register(fail_async, name="fail_async")
        route_table._rebuild()
        client = make_client(route_table)

        response = client.post("/tools/default/fail_async", json={})
        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail == "Tool 'fail_async' execution failed"
        assert "async boom" not in detail


class TestFastAPITracebackLogging:
    """The FastAPI assembly's traceback logging.

    The httpserver app logs the same traceback from its own handler; see
    ``tests/test_openapi_server_app.py``.
    """

    def test_sync_tool_exception_traceback_is_logged(
        self, route_table: RouteTable, monkeypatch
    ):
        """The FastAPI path keeps the traceback the client must not receive."""
        import toolregistry_server.adapters.openapi.adapter as adapter

        stream = _capture_logs(monkeypatch, adapter)

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
        import toolregistry_server.adapters.openapi.adapter as adapter

        stream = _capture_logs(monkeypatch, adapter)

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
        import threading

        import httpx

        import toolregistry_server.adapters.openapi.adapter as adapter

        entered = threading.Event()
        release = threading.Event()

        def blocking_validate(route, payload, **_kwargs):
            entered.set()
            assert release.wait(5)
            return payload

        monkeypatch.setattr(adapter, "_validate_arguments", blocking_validate)

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


# ============== Execution hint extensions (#280) ==============


def _core_has_execution_hints() -> bool:
    """Whether the installed toolregistry defines the hint metadata fields."""
    from dataclasses import fields

    return "read_only_hint" in {f.name for f in fields(ToolMetadata)}


@pytest.mark.skipif(
    not _core_has_execution_hints(),
    reason="installed toolregistry predates execution hints",
)
class TestOperationExtensions:
    """Resolved hints are exposed as ``x-`` extensions on operations."""

    @pytest.fixture
    def annotated_table(self) -> RouteTable:
        registry = ToolRegistry()

        def current_time() -> str:
            """Return the current time."""
            return "now"

        def remove_file(path: str) -> str:
            """Remove a file."""
            return path

        def plain(value: int) -> int:
            """Echo a value."""
            return value

        registry.register(
            Tool.from_function(
                current_time,
                metadata=ToolMetadata(
                    title="Current Time",
                    read_only_hint=True,
                    idempotent_hint=True,
                    open_world_hint=False,
                ),
            )
        )
        registry.register(
            Tool.from_function(
                remove_file,
                metadata=ToolMetadata(
                    destructive_hint=True,
                    idempotent_hint=False,
                ),
            )
        )
        registry.register(Tool.from_function(plain))
        return RouteTable(registry)

    @staticmethod
    def _operation(table: RouteTable, tool_name: str, make_client) -> dict:
        schema = make_client(table).get("/openapi.json").json()
        return schema["paths"][f"/tools/default/{tool_name}"]["post"]

    def test_read_only_extensions(
        self, annotated_table: RouteTable, make_client
    ) -> None:
        op = self._operation(annotated_table, "current_time", make_client)
        assert op["x-read-only"] is True
        assert op["x-destructive"] is False
        assert op["x-idempotent"] is True
        assert op["x-open-world"] is False
        assert op["x-title"] == "Current Time"

    def test_destructive_extensions(
        self, annotated_table: RouteTable, make_client
    ) -> None:
        op = self._operation(annotated_table, "remove_file", make_client)
        assert op["x-destructive"] is True
        assert op["x-idempotent"] is False
        # Unset stays unset; the extension is absent, not fabricated.
        assert "x-read-only" not in op

    def test_unannotated_operation_has_no_extensions(
        self, annotated_table: RouteTable, make_client
    ) -> None:
        op = self._operation(annotated_table, "plain", make_client)
        assert not [key for key in op if key.startswith("x-")]


# ============== Backend Selection Tests ==============


class TestBackendSelection:
    """The adapter can serve on FastAPI (default) or the vendored server."""

    def test_default_backend_is_fastapi(self, route_table: RouteTable) -> None:
        from toolregistry_server.adapters.openapi import OpenAPIAdapter

        adapter = OpenAPIAdapter(route_table)
        assert adapter._backend == "fastapi"  # noqa: SLF001 - test fixture

    def test_httpserver_backend_builds_the_vendored_app(
        self, route_table: RouteTable
    ) -> None:
        from toolregistry_server._vendor.httpserver import App
        from toolregistry_server.adapters.openapi import OpenAPIAdapter

        adapter = OpenAPIAdapter(route_table, backend="httpserver")
        assert isinstance(adapter.app, App)

    def test_unknown_backend_is_rejected(self, route_table: RouteTable) -> None:
        from toolregistry_server.adapters.openapi import OpenAPIAdapter

        with pytest.raises(ValueError, match="Unknown OpenAPI backend"):
            OpenAPIAdapter(route_table, backend="gunicorn")

    def test_cli_parses_the_backend_flag(self) -> None:
        from toolregistry_server.cli import CLI

        parser = CLI().create_parser()
        assert parser.parse_args(["openapi"]).backend == "fastapi"
        assert parser.parse_args(["openapi", "--backend", "httpserver"]).backend == (
            "httpserver"
        )

    def test_cli_rejects_an_unknown_backend(self) -> None:
        from toolregistry_server.cli import CLI

        parser = CLI().create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["openapi", "--backend", "gunicorn"])

    def test_tokens_path_reaches_the_httpserver_backend(
        self, route_table: RouteTable, tmp_path, monkeypatch
    ) -> None:
        """The CLI's tokens_path → tokens seam must leave auth switched on."""
        import asyncio

        # App.run installs signal handlers, which require the main thread.
        import asyncio.unix_events
        import threading
        import time
        import urllib.error
        import urllib.request

        from toolregistry_server.adapters.openapi import OpenAPIAdapter

        for loop_class in (
            asyncio.BaseEventLoop,
            asyncio.unix_events._UnixSelectorEventLoop,
        ):
            monkeypatch.setattr(loop_class, "add_signal_handler", lambda *a, **k: None)

        token_file = tmp_path / "tokens.txt"
        token_file.write_text("secret\n", encoding="utf-8")

        captured: dict = {}
        original_run = OpenAPIAdapter.run

        def fake_run(self, **kwargs):
            captured["app"] = self._app
            captured["backend"] = self._backend

        OpenAPIAdapter.run = fake_run
        try:
            OpenAPIAdapter.create_and_run(
                route_table,
                backend="httpserver",
                tokens_path=str(token_file),
                host="127.0.0.1",
                port=0,
            )
        finally:
            OpenAPIAdapter.run = original_run

        assert captured["backend"] == "httpserver"
        app = captured["app"]

        thread = threading.Thread(
            target=app.run, kwargs={"host": "127.0.0.1", "port": 0}, daemon=True
        )
        thread.start()
        for _ in range(100):
            if app.port:
                break
            time.sleep(0.05)
        base = f"http://127.0.0.1:{app.port}"
        try:
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(base + "/tools", timeout=5)
            assert excinfo.value.code == 401

            request = urllib.request.Request(
                base + "/tools", headers={"Authorization": "Bearer secret"}
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200
        finally:
            app.shutdown()


class TestOverrideIsAuthoritative:
    """A model-backed tool also enforces the served override (#138 review)."""

    def test_override_rejects_an_argument_it_omits(self, route_table):
        from fastapi import HTTPException

        route = route_table.get_route("add")
        route_table.override_parameters(
            "add", {"type": "object", "properties": {"a": {"type": "integer"}}}
        )
        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, {"a": 1, "b": 2})
        assert excinfo.value.status_code == 422
        assert excinfo.value.detail[0]["type"] == "extra_forbidden"

    def test_override_rejects_an_argument_the_model_drops(self, route_table):
        from fastapi import HTTPException

        route = route_table.get_route("add")
        route_table.override_parameters(
            "add",
            {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                    "extra": {"type": "string"},
                },
            },
        )
        with pytest.raises(HTTPException) as excinfo:
            _validate_arguments(route, {"a": 1, "b": 2, "extra": "x"})
        assert excinfo.value.status_code == 422
        assert excinfo.value.detail[0]["type"] == "unexpected_parameter"

    def test_override_matching_the_model_still_coerces(self, route_table):
        route = route_table.get_route("add")
        route_table.override_parameters(
            "add",
            {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        )
        assert _validate_arguments(route, {"a": "2", "b": 3}) == {"a": 2, "b": 3}

    @pytest.mark.parametrize("additional", [True, {}])
    def test_override_allowing_additional_properties_accepts_extras(
        self, route_table, additional
    ):
        route = route_table.get_route("add")
        route_table.override_parameters(
            "add",
            {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
                "additionalProperties": additional,
            },
        )
        assert _validate_arguments(route, {"a": 1, "b": 2, "extra": 3}) == {
            "a": 1,
            "b": 2,
        }


class TestFastAPIBodyLimit:
    """The fastapi backend enforces the shared request-body cap (#148)."""

    @staticmethod
    def _client(table: RouteTable, max_body_size: int):
        from fastapi.testclient import TestClient

        return TestClient(create_openapi_app(table, max_body_size=max_body_size))

    def test_default_cap_is_one_mib(self, route_table: RouteTable):
        from toolregistry_server._vendor.httpserver import DEFAULT_MAX_BODY_SIZE
        from toolregistry_server.adapters.openapi.middleware import (
            BodySizeLimitMiddleware,
        )

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
        from toolregistry_server.adapters.openapi.middleware import (
            BodySizeLimitMiddleware,
        )

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
        from toolregistry_server.adapters.openapi.middleware import (
            BodySizeLimitMiddleware,
        )

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
