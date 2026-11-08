"""Build the single-process gateway demo application.

One uvicorn process serves three frontends on the same origin:

* ``/openapi`` — the OpenAPI adapter (tools, spec and Swagger UI);
* ``/mcp`` — the MCP streamable-HTTP adapter;
* ``/ext/gateway`` — a small admin surface for keys and frontend switches.

Client keys are checked by one HTTP middleware in front of both tool
frontends.  The gateway process (#184) replaces this app shell, the SQLite
key store (#186) replaces :class:`~.keys.FileKeyStore`, and the runtime source
manager (#185) replaces :func:`~.registry.build_registry`.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send

from toolregistry_server import RouteTable
from toolregistry_server.adapters.mcp import MCPAdapter
from toolregistry_server.adapters.openapi import OpenAPIAdapter

from . import __version__
from .frontends import FrontendState
from .keys import FileKeyStore, KeyRecord
from .registry import build_registry

logger = logging.getLogger("gateway_demo")

_FRONTEND_MOUNTS = (("openapi", "/openapi"), ("mcp", "/mcp"))
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


class _McpAsgi:
    """ASGI wrapper that lets the MCP session manager be mounted."""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self._manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._manager.handle_request(scope, receive, send)


def _bearer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _frontend_for(path: str) -> str | None:
    for name, mount in _FRONTEND_MOUNTS:
        if path == mount or path.startswith(mount + "/"):
            return name
    return None


async def _admin_response(request: Request, admin_token: str, call_next: Any) -> Any:
    if not admin_token:
        return JSONResponse(
            {"detail": "set GATEWAY_ADMIN_TOKEN to enable the admin surface"},
            status_code=503,
        )
    token = _bearer(request)
    if token is None or not hmac.compare_digest(token, admin_token):
        return JSONResponse(
            {"detail": "operator token required"},
            status_code=401,
            headers=_UNAUTHORIZED,
        )
    return await call_next(request)


def _record_json(record: KeyRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "prefix": record.prefix,
        "scopes": list(record.scopes),
        "created_at": record.created_at,
    }


def _attach_admin(app: FastAPI, keys: FileKeyStore, frontends: FrontendState) -> None:
    @app.get("/ext/gateway/")
    def gateway_info() -> dict[str, Any]:
        return {
            "name": "gateway-demo",
            "version": __version__,
            "frontends": frontends.snapshot(),
        }

    @app.get("/ext/gateway/keys")
    def list_keys() -> dict[str, Any]:
        return {"keys": [_record_json(record) for record in keys.list()]}

    @app.post("/ext/gateway/keys", status_code=201)
    def create_key(payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        scopes = payload.get("scopes")
        if not isinstance(name, str):
            raise HTTPException(status_code=422, detail="'name' must be a string")
        if scopes is not None and (
            not isinstance(scopes, list)
            or not all(isinstance(item, str) for item in scopes)
        ):
            raise HTTPException(
                status_code=422, detail="'scopes' must be a list of strings"
            )
        try:
            secret, record = keys.create(name, scopes)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # The secret is returned exactly once; only its digest is stored.
        return {**_record_json(record), "key": secret}

    @app.delete("/ext/gateway/keys/{name}", status_code=204)
    def revoke_key(name: str) -> Response:
        if not keys.revoke(name):
            raise HTTPException(status_code=404, detail=f"key {name!r} not found")
        return Response(status_code=204)

    @app.get("/ext/gateway/frontends")
    def list_frontends() -> dict[str, bool]:
        return frontends.snapshot()

    @app.put("/ext/gateway/frontends/{name}")
    def set_frontend(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=422, detail="'enabled' must be a boolean")
        try:
            frontends.set_enabled(name, enabled)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"frontend {name!r} not found"
            ) from exc
        return {"name": name, "enabled": enabled}


def create_app() -> FastAPI:
    """Build the demo application.

    Returns:
        A FastAPI app with ``/openapi``, ``/mcp`` and ``/ext/gateway`` mounted
        on one origin and a single key check in front of the first two.
    """
    registry, tool_source = build_registry()
    route_table = RouteTable(registry)
    openapi_app = OpenAPIAdapter(route_table, backend="fastapi").app
    mcp_adapter = MCPAdapter(route_table, name="gateway-demo")
    session_manager = StreamableHTTPSessionManager(
        app=mcp_adapter.server, json_response=True
    )
    keys = FileKeyStore(os.environ.get("GATEWAY_KEYS_FILE", "keys.json"))
    frontends = FrontendState()
    admin_token = os.environ.get("GATEWAY_ADMIN_TOKEN", "").strip()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with session_manager.run():
            logger.info(
                "gateway demo ready: %d tool(s) from %s",
                len(route_table.list_routes()),
                tool_source,
            )
            yield

    app = FastAPI(
        title="ToolRegistry gateway demo",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def gate(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if path == "/health" or path == "/":
            return await call_next(request)
        if path == "/ext/gateway" or path.startswith("/ext/gateway/"):
            return await _admin_response(request, admin_token, call_next)
        frontend = _frontend_for(path)
        if frontend is None:
            return await call_next(request)
        if not frontends.enabled(frontend):
            return JSONResponse(
                {"detail": f"frontend {frontend!r} is disabled"}, status_code=404
            )
        if keys.verify(_bearer(request)) is None:
            return JSONResponse(
                {"detail": "invalid or missing API key"},
                status_code=401,
                headers=_UNAUTHORIZED,
            )
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "tools": len(route_table.list_routes()),
            "tool_source": tool_source,
        }

    @app.get("/")
    def index() -> dict[str, Any]:
        return {
            "service": "toolregistry gateway demo",
            "frontends": frontends.snapshot(),
            "admin": "/ext/gateway/",
        }

    _attach_admin(app, keys, frontends)
    app.mount("/openapi", openapi_app)
    # A Mount would redirect "/mcp" to "/mcp/"; the MCP endpoint is exact.
    app.add_route("/mcp", _McpAsgi(session_manager), methods=["GET", "POST", "DELETE"])
    return app
