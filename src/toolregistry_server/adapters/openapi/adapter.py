"""Automatic route generation from a RouteTable.

Converts a :class:`~toolregistry_server.RouteTable` into a FastAPI
:class:`~fastapi.APIRouter` by introspecting each registered route
and dynamically creating Pydantic request models and route handlers.
"""

import logging
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any

from toolregistry import ValidationError

from ..._vendor.jsonschema import SchemaValidationError, schema_validate
from ...route_table import RouteEntry, RouteTable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

# ---------------------------------------------------------------------------
# Argument coercion
# ---------------------------------------------------------------------------


def _strip_framework_fields(raw: dict[str, Any], route: RouteEntry) -> dict[str, Any]:
    """Strip framework fields from request arguments before execution.

    ``tool.parameters`` is a clean schema (no framework fields), but
    ``get_schema()`` injects ``toolcall_reason`` when think_augment is
    enabled.  Clients that build requests from the augmented schema may
    include it, so we strip it before calling the handler.

    Args:
        raw: The raw arguments from the request body.
        route: The RouteEntry for the tool being called.

    Returns:
        A dict of arguments suitable for passing to the handler.
    """
    return {k: v for k, v in raw.items() if k != "toolcall_reason"}


def _validation_details(exc: Exception) -> list[dict[str, Any]]:
    """Render a validation error as the 422 ``{"detail": [...]}`` envelope."""
    details: list[dict[str, Any]] = []
    for err in getattr(exc, "errors", []) or []:
        path = getattr(err, "path", "") or ""
        loc = ["body", *(part for part in path.split(".") if part)]
        details.append(
            {
                "loc": loc,
                "msg": getattr(err, "message", str(err)),
                "type": getattr(err, "validator", None) or "value_error",
            }
        )
    if not details:
        details.append({"loc": ["body"], "msg": str(exc), "type": "value_error"})
    return details


def _undeclared_parameter_details(
    payload: dict[str, Any],
    schema: dict[str, Any],
    message: str,
    error_type: str,
) -> list[dict[str, Any]]:
    """Return a 422 detail entry for every payload key *schema* omits."""
    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        return []
    declared = set((schema.get("properties") or {}).keys())
    return [
        {"loc": ["body", name], "msg": message, "type": error_type}
        for name in sorted(set(payload) - declared)
    ]


def _validate_arguments(route: RouteEntry, raw: Any) -> dict[str, Any]:
    """Validate a request body and return the arguments for the tool call.

    Signature-derived tools go through ``Tool.validate_parameters`` so the
    body gets the same coercion and unknown-key filtering the tool applies
    when called directly. Proxy tools (MCP / OpenAPI / LangChain, which have
    no ``parameters_model``) are checked against their own JSON Schema.

    When a parameter override is active, the served schema is authoritative:
    a key it does not declare is rejected before the model runs, and a key
    the model then drops is rejected too.  Without an override the model
    keeps its historical filtering behavior.

    Args:
        route: The route being called.
        raw: The decoded JSON request body. Tool arguments are named, so
            anything other than a JSON object is rejected.

    Returns:
        Arguments to pass to the handler.

    Raises:
        fastapi.HTTPException: 422 when validation fails.
    """
    from fastapi import HTTPException

    if not isinstance(raw, dict):
        # The endpoints accept ``Annotated[Any, Body()]``, so FastAPI no
        # longer constrains the body to an object. Reject scalars, arrays
        # and null here instead of failing later inside ``.items()``.
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "loc": ["body"],
                    "msg": "Input should be a valid dictionary or object "
                    "to extract fields from",
                    "type": "model_attributes_type",
                }
            ],
        )

    payload = _strip_framework_fields(raw, route)
    tool = route.tool
    override_active = route.parameter_schema_override is not None
    served_schema = route.served_parameters_schema

    if override_active:
        # The override is the contract the client was given.  Reject a key
        # it omits before the tool's model gets a chance to filter it away.
        details = _undeclared_parameter_details(
            payload,
            served_schema,
            "Extra inputs are not permitted",
            "extra_forbidden",
        )
        if details:
            raise HTTPException(status_code=422, detail=details)

    if getattr(tool, "parameters_model", None) is not None:
        try:
            validated = tool.validate_parameters(payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=_validation_details(exc)
            ) from exc
        if override_active and (
            "additionalProperties" not in served_schema
            or served_schema["additionalProperties"] is False
        ):
            # The override advertised a key the tool cannot accept; the
            # model dropped it.  Reject rather than run with less than the
            # client sent.  When the served schema permits additional
            # properties, dropping them is consistent with that contract.
            dropped = sorted(set(payload) - set(validated))
            if dropped:
                raise HTTPException(
                    status_code=422,
                    detail=[
                        {
                            "loc": ["body", name],
                            "msg": "Parameter is not accepted by the tool",
                            "type": "unexpected_parameter",
                        }
                        for name in dropped
                    ],
                )
        return validated

    try:
        schema_validate(payload, served_schema)
    except SchemaValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_details(exc)) from exc

    if not served_schema.get("additionalProperties"):
        declared = set((served_schema.get("properties") or {}).keys())
        payload = {k: v for k, v in payload.items() if k in declared}
    return payload


# ---------------------------------------------------------------------------
# Route generation
# ---------------------------------------------------------------------------


def _openapi_extensions(route: RouteEntry) -> dict[str, Any] | None:
    """Map a tool's execution hints onto ``x-`` operation extensions.

    MCP carries these claims as ``annotations``; OpenAPI has no standard
    slot, so the same resolved, transport-neutral hints are exposed as
    vendor extensions (``x-read-only``, ``x-destructive``,
    ``x-idempotent``, ``x-open-world``), plus ``x-title`` for a declared
    display title.  Returns ``None`` when the tool declares nothing, so
    unannotated operations keep their schema byte-for-byte.

    Args:
        route: The route whose tool metadata should be serialized.

    Returns:
        Extension mapping, or ``None`` when there is nothing to add.
    """
    extensions: dict[str, Any] = {
        f"x-{hint.removesuffix('_hint').replace('_', '-')}": value
        for hint, value in route.served_execution_hints.items()
    }
    if route.title is not None:
        extensions["x-title"] = route.title
    return extensions or None


def _add_route_from_entry(
    router: "APIRouter",
    route: RouteEntry,
    route_table: RouteTable,
) -> None:
    """Create and register a POST route for a single RouteEntry.

    The route path is taken from the RouteEntry.path attribute.

    For async tools the handler is an ``async def``; for sync tools a plain
    ``def`` is used. A closure is used to correctly capture the loop
    variable.

    Each endpoint checks at request time whether the tool is still enabled
    via ``route_table.get_route()``. If the tool has been disabled at runtime,
    the endpoint returns HTTP 503 Service Unavailable.

    Args:
        router: The :class:`~fastapi.APIRouter` to add the route to.
        route: The :class:`~toolregistry_server.RouteEntry` to expose.
        route_table: The :class:`~toolregistry_server.RouteTable` used for
            runtime enable/disable checks.
    """
    from fastapi import Body, HTTPException

    def _wrap_tool_error(tname: str, e: Exception) -> HTTPException:
        """Convert an unhandled tool exception into a structured HTTP 500."""
        return HTTPException(
            status_code=500,
            detail=f"Tool '{tname}' execution failed: {e}",
        )

    summary = (route.description or "")[:120]
    extensions = _openapi_extensions(route) or {}
    # FastAPI no longer derives the request schema from a Pydantic model;
    # publish the tool's own JSON Schema so /openapi.json keeps describing
    # the body exactly as the tool declares it.
    extensions["requestBody"] = {
        "required": True,
        "content": {"application/json": {"schema": route.served_parameters_schema}},
    }
    # Use the top-level segment of the namespace as the tag for grouping
    # e.g. "web/brave_search" → tag "web", "calculator" → tag "calculator"
    namespace = route.namespace
    tags: list[str | Enum] = []
    if namespace:
        tags = [namespace.split("/")[0]]

    # Capture tool_name as a string to avoid closure-over-loop-variable issues.
    tool_name = route.tool_name
    handler = route.handler

    # Use factory functions to capture the handler, route_table, and tool_name
    # per iteration via closure.

    if route.is_async:

        def _make_async_endpoint(
            h: Any = handler,
            rt: RouteTable = route_table,
            tname: str = tool_name,
        ):
            async def _endpoint(data: Annotated[Any, Body()]) -> Any:
                current_route = rt.get_route(tname)
                if current_route is None or not current_route.enabled:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Tool '{tname}' is currently disabled",
                    )
                arguments = _validate_arguments(current_route, data)
                try:
                    return await h(**arguments)
                except HTTPException:
                    raise
                except Exception as e:
                    raise _wrap_tool_error(tname, e) from e

            return _endpoint

        router.add_api_route(
            route.path,
            _make_async_endpoint(),
            methods=["POST"],
            operation_id=route.tool_name,
            summary=summary,
            tags=tags,
            openapi_extra=extensions,
        )
    else:

        def _make_sync_endpoint(
            h: Any = handler,
            rt: RouteTable = route_table,
            tname: str = tool_name,
        ):
            def _endpoint(data: Annotated[Any, Body()]) -> Any:
                current_route = rt.get_route(tname)
                if current_route is None or not current_route.enabled:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Tool '{tname}' is currently disabled",
                    )
                arguments = _validate_arguments(current_route, data)
                try:
                    return h(**arguments)
                except HTTPException:
                    raise
                except Exception as e:
                    raise _wrap_tool_error(tname, e) from e

            return _endpoint

        router.add_api_route(
            route.path,
            _make_sync_endpoint(),
            methods=["POST"],
            operation_id=route.tool_name,
            summary=summary,
            tags=tags,
            openapi_extra=extensions,
        )


def route_table_to_router(
    route_table: RouteTable,
    prefix: str = "",
) -> "APIRouter":
    """Convert a :class:`~toolregistry_server.RouteTable` into a FastAPI router.

    Routes are generated for **all** registered tools regardless of their
    current enabled/disabled state. Each endpoint checks the route's enabled
    state at request time and returns HTTP 503 if the tool has been disabled,
    allowing runtime enable/disable without restarting the server.

    Args:
        route_table: The route table to convert.
        prefix: URL prefix for all generated routes.

    Returns:
        A :class:`~fastapi.APIRouter` with one POST route per registered tool.

    Raises:
        ImportError: If FastAPI is not installed.
    """
    try:
        from fastapi import APIRouter
    except ImportError as e:
        raise ImportError(
            "FastAPI is required for OpenAPI support. "
            "Install with: pip install toolregistry-server[openapi]"
        ) from e

    router = APIRouter(prefix=prefix)

    for route in route_table.list_routes(enabled_only=False):
        _add_route_from_entry(router, route, route_table)

    return router


# ---------------------------------------------------------------------------
# Tools list endpoint
# ---------------------------------------------------------------------------


def add_tools_endpoint(app: "FastAPI", route_table: RouteTable) -> None:
    """Add a /tools endpoint that returns the list of available tools.

    This endpoint supports ETag-based cache validation. The response includes
    the current ETag in both the response body and headers.

    Args:
        app: The FastAPI application instance.
        route_table: The RouteTable to query for tools.

    Example:
        ```python
        from fastapi import FastAPI
        from toolregistry_server import RouteTable
        from toolregistry_server.openapi.adapter import add_tools_endpoint

        app = FastAPI()
        route_table = RouteTable(registry)
        add_tools_endpoint(app, route_table)
        ```
    """
    try:
        from fastapi import Request
        from fastapi.responses import JSONResponse
    except ImportError as e:
        raise ImportError(
            "FastAPI is required for OpenAPI support. "
            "Install with: pip install toolregistry-server[openapi]"
        ) from e

    @app.get("/tools", tags=["meta"])
    async def list_tools(request: Request):
        """List all available tools with their metadata.

        Returns a JSON object containing:
        - tools: List of tool information (name, namespace, method, path, description)
        - etag: Current ETag for cache validation

        Supports conditional requests via If-None-Match header.
        Returns 304 Not Modified if the ETag matches.
        """
        # Check If-None-Match header
        if_none_match = request.headers.get("If-None-Match")
        current_etag = route_table.etag

        if if_none_match and if_none_match == current_etag:
            return JSONResponse(
                content=None,
                status_code=304,
                headers={"ETag": current_etag},
            )

        # Build tools list — deferred tools are excluded from the initial
        # listing so that LLMs discover them via discover_tools.
        tools = []
        for route in route_table.list_routes(enabled_only=True, include_deferred=False):
            tool_info: dict[str, Any] = {
                "name": route.tool_name,
                "namespace": route.namespace,
                "method": route.method_name,
                "path": route.path,
                "description": route.description,
            }
            if route.schema_hash:
                tool_info["schema_hash"] = route.schema_hash
            if route.last_refreshed_at:
                tool_info["last_refreshed_at"] = route.last_refreshed_at
            tools.append(tool_info)

        return JSONResponse(
            content={"tools": tools, "etag": current_etag},
            headers={"ETag": current_etag},
        )


# ---------------------------------------------------------------------------
# SSE event stream
# ---------------------------------------------------------------------------


def add_events_endpoint(app: "FastAPI", route_table: RouteTable) -> None:
    """Add a GET /events SSE endpoint that streams tool change events.

    Each connected client receives server-sent events whenever a tool is
    registered, unregistered, enabled, disabled, or otherwise modified.

    Event format::

        event: tool_change
        data: {"tool": "search", "event": "register"}

    Args:
        app: The FastAPI application instance.
        route_table: The RouteTable whose changes are broadcast.
    """
    import asyncio
    import json as _json

    try:
        from starlette.responses import StreamingResponse
    except ImportError as e:
        raise ImportError(
            "Starlette is required for SSE support. "
            "Install with: pip install toolregistry-server[openapi]"
        ) from e

    # All currently connected SSE client queues
    _queues: set[asyncio.Queue[str]] = set()

    def _on_change(tool_name: str, event: str) -> None:
        """Push a change event to every connected SSE client."""
        payload = _json.dumps({"tool": tool_name, "event": event})
        # TODO: SSE events do not include an ``id:`` field, so clients
        # cannot resume from a Last-Event-ID after reconnection.
        msg = f"event: tool_change\ndata: {payload}\n\n"
        for q in list(_queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                logger.debug("SSE client queue full, dropping event: %s", tool_name)

    route_table.add_listener(_on_change)

    _KEEPALIVE_INTERVAL: float = 30.0  # seconds

    @app.get("/events", tags=["meta"], include_in_schema=True)
    async def sse_events():
        """Stream tool change events via Server-Sent Events."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        _queues.add(q)

        async def _generate():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(
                            q.get(), timeout=_KEEPALIVE_INTERVAL
                        )
                        yield msg
                    except asyncio.TimeoutError:
                        # Send SSE comment as keepalive to prevent reverse
                        # proxies from closing idle connections.
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                return
            finally:
                _queues.discard(q)

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


# ---------------------------------------------------------------------------
# Dynamic OpenAPI schema generation
# ---------------------------------------------------------------------------


def setup_dynamic_openapi(app: "FastAPI", route_table: RouteTable) -> None:
    """Serve ``/openapi.json`` from the RouteTable itself.

    The document is built by
    :func:`~toolregistry_server.adapters.openapi.spec.build_openapi_schema`,
    so disabled tools are absent by construction and no post-filtering or
    ``get_openapi`` monkeypatching is needed. Nothing is cached: the schema
    is regenerated on every request, which is what makes enable/disable
    show up immediately.

    Args:
        app: The FastAPI application instance.
        route_table: The route table to describe.
    """
    from .spec import build_openapi_schema

    def custom_openapi() -> dict[str, Any]:
        return build_openapi_schema(
            route_table,
            title=app.title,
            version=app.version,
            description=app.description,
        )

    app.openapi = custom_openapi  # ty: ignore[invalid-assignment]
