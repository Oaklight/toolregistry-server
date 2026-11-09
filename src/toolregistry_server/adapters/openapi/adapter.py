"""Automatic route generation from a RouteTable.

Converts a :class:`~toolregistry_server.RouteTable` into a FastAPI
:class:`~fastapi.APIRouter` by introspecting each registered route
and dynamically creating Pydantic request models and route handlers.
"""

import asyncio
import traceback
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any

from toolregistry import ValidationError

from ..._vendor.jsonschema import SchemaValidationError, schema_validate
from ..._vendor.structlog import get_logger
from ...regex_guard import PatternMatcher, SpecPatternError
from ...route_table import RouteEntry, RouteTable, _allows_additional_properties
from ...scoped_discovery import (
    DISCOVERY_TOOL_NAME,
    filter_discovery_result,
    refusing_target,
)
from .serialisation import encode_tool_result

logger = get_logger(__name__)

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI, Response

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
    if _allows_additional_properties(schema):
        return []
    declared = set((schema.get("properties") or {}).keys())
    return [
        {"loc": ["body", name], "msg": message, "type": error_type}
        for name in sorted(set(payload) - declared)
    ]


#: Keywords whose value maps names to sub-schemas; their keys are names,
#: not schema keywords, so the walker must recurse into the values only.
_SCHEMA_MAP_KEYWORDS = frozenset(
    {"properties", "patternProperties", "dependentSchemas", "$defs", "definitions"}
)


def _without_spec_regex(node: Any) -> Any:
    """Return a validation-only copy of *node* without spec regexes.

    ``pattern`` and ``patternProperties`` come from the OpenAPI document,
    not from the operator.  Running one against a request body can backtrack
    catastrophically -- ``(a+)+$`` is six characters and takes exponential
    time on a 30-character subject -- and Python's ``re`` has no timeout.
    The server must stay dependency-free, so validation ignores the
    expressions instead of executing them; the upstream API remains the
    authority for the format.  The served schema is never modified, so
    clients still see the published contract.

    Dropping ``patternProperties`` would make the keys those patterns used
    to cover look like unknown keys, so ``additionalProperties: false`` at
    that same level is relaxed to ``true``.  The relaxation is local to the
    level that carried the patterns and never descends: a nested schema
    keeps its own ``additionalProperties``.

    Args:
        node: A JSON Schema fragment, or any nested value.

    Returns:
        A new fragment.  *node* and everything under it are left untouched.
    """
    if isinstance(node, list):
        return [_without_spec_regex(item) for item in node]
    if not isinstance(node, dict):
        return node

    stripped: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("pattern", "patternProperties"):
            continue
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            stripped[key] = {
                name: _without_spec_regex(sub) for name, sub in value.items()
            }
        else:
            stripped[key] = _without_spec_regex(value)

    if "patternProperties" in node and stripped.get("additionalProperties") is False:
        stripped["additionalProperties"] = True
    return stripped


# Read-only: never accesses RouteTable and may run in a worker thread.
def _validate_arguments(
    route: RouteEntry,
    raw: Any,
    *,
    pattern_matcher: PatternMatcher | None = None,
) -> dict[str, Any]:
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
        pattern_matcher: Optional bounded matcher, built once per request.
            When given, spec-supplied ``pattern`` / ``patternProperties``
            are enforced under its bounds; when absent they are stripped
            from the validation copy instead.

    Returns:
        Arguments to pass to the handler.

    Raises:
        fastapi.HTTPException: 422 when validation fails; 500 when a
            spec-supplied regex times out or does not compile.

    Threading:
        Read-only, does not access ``RouteTable``, and may run in a worker
        thread. Every value comes from the ``route`` snapshot the caller
        already holds. Without an override, ``served_parameters_schema`` is
        a ``cached_property``: on Python 3.12+ concurrent first access may
        compute it twice, but both results are identical and the assignment
        is atomic, so the duplication is benign.
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
        if override_active and not _allows_additional_properties(served_schema):
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

    # An override is operator code, so its regexes are trusted.  A proxy
    # tool's schema is derived from the spec, so its regexes are executed
    # only through a bounded matcher; without one they are stripped (see
    # ``_without_spec_regex``).
    if override_active or pattern_matcher is not None:
        validation_schema = served_schema
    else:
        validation_schema = _without_spec_regex(served_schema)
    try:
        schema_validate(payload, validation_schema, pattern_matcher=pattern_matcher)
    except SchemaValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_details(exc)) from exc
    except TimeoutError as exc:
        # Exception text names the bounds, never the pattern.
        logger.warning(
            "Spec regex timed out during request validation",
            tool=route.tool_name,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Tool '{route.tool_name}' validation timed out",
        ) from exc
    except SpecPatternError as exc:
        # The message carries the pattern for troubleshooting; the client
        # only sees the stable reason below.
        logger.error(
            "Spec regex in a served schema is invalid",
            tool=route.tool_name,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Tool '{route.tool_name}' validation failed",
        ) from exc

    # ``additionalProperties`` is a JSON Schema, not a boolean: ``{}`` and
    # other non-``False`` values permit extras.  Only an explicit ``False``
    # or an absent keyword forbids them.
    if not _allows_additional_properties(served_schema):
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


def _tool_result_response(result: Any) -> "Response":
    """Render a tool result the way the httpserver backend does.

    Both OpenAPI backends call :func:`encode_tool_result`, so non-finite
    floats and ``Decimal`` values reach the wire as ``null`` instead of a
    bare ``NaN`` or the string ``"NaN"``.  FastAPI would otherwise serialise
    the raw result through pydantic, whose JSON mode keeps a non-finite
    ``Decimal`` as a string.

    Args:
        result: The value returned by the tool.

    Returns:
        A JSON response carrying the encoded body.
    """
    from fastapi import Response

    return Response(
        content=encode_tool_result(result),
        media_type="application/json; charset=utf-8",
    )


def _require_route_scope(
    request: Any, route: RouteEntry, tool_name: str, principal_resolver: Any
) -> None:
    """Refuse a route outside the caller's key scope.

    The principal was resolved by the app-level bearer dependency and stored
    on ``request.state``. A resolver without a principal denies, so a
    mis-wired app fails closed instead of open.

    Args:
        request: The incoming request carrying ``state.principal``.
        route: The route being called.
        tool_name: The route's tool name, used in the message.
        principal_resolver: The configured resolver, or ``None`` when key
            scoping is off.

    Raises:
        fastapi.HTTPException: ``403`` when the principal may not call the
            route.
    """
    if principal_resolver is None:
        return
    principal = getattr(request.state, "principal", None)
    if principal_resolver.allows_route(principal, route):
        return
    from fastapi import HTTPException

    raise HTTPException(
        status_code=403,
        detail=f"Tool '{tool_name}' is not permitted for this credential",
    )


def _filtered_result(
    tool_name: str,
    result: Any,
    principal_resolver: Any,
    principal: Any,
) -> Any:
    """Apply the discovery scope filter when the call was ``discover_tools``.

    Keeping the conditional here leaves the tool endpoints with one statement
    for every tool, so their complexity and their shared shape stay flat.

    Args:
        tool_name: The tool that ran.
        result: Its result.
        principal_resolver: The configured resolver, or ``None``.
        principal: The caller's principal, or ``None``.

    Returns:
        The result, with out-of-scope discovered tools removed.
    """
    if tool_name == DISCOVERY_TOOL_NAME:
        return filter_discovery_result(result, principal_resolver, principal)
    return result


def _require_proxy_target(
    tool_name: str,
    arguments: Any,
    registry: Any,
    principal_resolver: Any,
    principal: Any,
) -> None:
    """Refuse ``call_deferred`` when its target is outside the scope.

    The detail names the target, exactly as a direct out-of-scope call to
    that tool does, so the two paths are indistinguishable to a client.

    Args:
        tool_name: The tool the client called.
        arguments: The validated call arguments.
        registry: The live registry behind the served routes.
        principal_resolver: The configured resolver, or ``None``.
        principal: The caller's principal, or ``None``.

    Raises:
        fastapi.HTTPException: ``403`` when the target is outside the scope.
    """
    target = refusing_target(
        tool_name, arguments, registry, principal_resolver, principal
    )
    if target is None:
        return
    from fastapi import HTTPException

    raise HTTPException(
        status_code=403,
        detail=f"Tool '{target}' is not permitted for this credential",
    )


def _add_route_from_entry(
    router: "APIRouter",
    route: RouteEntry,
    route_table: RouteTable,
    matcher_factory: "Callable[[], PatternMatcher] | None" = None,
    principal_resolver: "Any | None" = None,
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
        matcher_factory: Optional zero-argument callable returning a fresh
            bounded pattern matcher for one request. ``None`` keeps spec
            regexes out of validation.
        principal_resolver: Optional API key resolver. When set, a request
            whose principal may not call this route is refused with ``403``
            before validation or execution. ``None`` keeps the previous
            behaviour.
    """
    from fastapi import Body, HTTPException, Request

    def _wrap_tool_error(tname: str, e: Exception) -> HTTPException:
        """Convert an unhandled tool exception into a structured HTTP 500.

        The exception text stays in the server log: it can carry connection
        strings, internal paths and upstream URLs, and the client only needs
        to know which tool failed.
        """
        logger.error(
            "Tool execution failed",
            tool=tname,
            exception=traceback.format_exc(),
        )
        return HTTPException(
            status_code=500,
            detail=f"Tool '{tname}' execution failed",
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
            mf: "Callable[[], PatternMatcher] | None" = matcher_factory,
        ):
            async def _endpoint(data: Annotated[Any, Body()], request: Request) -> Any:
                # Mark the request so the app's server-error handler can tell
                # a response-rendering failure from any other 500.
                request.state.tool_name = tname
                current_route = rt.get_route(tname)
                if current_route is None or not current_route.enabled:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Tool '{tname}' is currently disabled",
                    )
                _require_route_scope(request, current_route, tname, principal_resolver)
                matcher = mf() if mf is not None else None
                # Validation is CPU-bound; keep this loop free while it runs,
                # the same way the httpserver handler does.
                arguments = await asyncio.to_thread(
                    _validate_arguments,
                    current_route,
                    data,
                    pattern_matcher=matcher,
                )
                principal = getattr(request.state, "principal", None)
                _require_proxy_target(
                    tname, arguments, rt.registry, principal_resolver, principal
                )
                try:
                    result = await h(**arguments)
                except HTTPException:
                    raise
                except Exception as e:
                    raise _wrap_tool_error(tname, e) from e
                result = _filtered_result(tname, result, principal_resolver, principal)
                return _tool_result_response(result)

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
            mf: "Callable[[], PatternMatcher] | None" = matcher_factory,
        ):
            def _endpoint(data: Annotated[Any, Body()], request: Request) -> Any:
                # Mark the request so the app's server-error handler can tell
                # a response-rendering failure from any other 500.
                request.state.tool_name = tname
                current_route = rt.get_route(tname)
                if current_route is None or not current_route.enabled:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Tool '{tname}' is currently disabled",
                    )
                _require_route_scope(request, current_route, tname, principal_resolver)
                matcher = mf() if mf is not None else None
                arguments = _validate_arguments(
                    current_route, data, pattern_matcher=matcher
                )
                principal = getattr(request.state, "principal", None)
                _require_proxy_target(
                    tname, arguments, rt.registry, principal_resolver, principal
                )
                try:
                    result = h(**arguments)
                except HTTPException:
                    raise
                except Exception as e:
                    raise _wrap_tool_error(tname, e) from e
                if tname == DISCOVERY_TOOL_NAME:
                    result = filter_discovery_result(
                        result, principal_resolver, principal
                    )
                return _tool_result_response(result)

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
    matcher_factory: "Callable[[], PatternMatcher] | None" = None,
    principal_resolver: "Any | None" = None,
) -> "APIRouter":
    """Convert a :class:`~toolregistry_server.RouteTable` into a FastAPI router.

    Routes are generated for **all** registered tools regardless of their
    current enabled/disabled state. Each endpoint checks the route's enabled
    state at request time and returns HTTP 503 if the tool has been disabled,
    allowing runtime enable/disable without restarting the server.

    Args:
        route_table: The route table to convert.
        prefix: URL prefix for all generated routes.
        matcher_factory: Optional bounded-matcher factory; see
            :func:`_add_route_from_entry`.
        principal_resolver: Optional API key resolver; see
            :func:`_add_route_from_entry`.

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
        _add_route_from_entry(
            router,
            route,
            route_table,
            matcher_factory,
            principal_resolver,
        )

    return router


# ---------------------------------------------------------------------------
# Tools list endpoint
# ---------------------------------------------------------------------------


def add_tools_endpoint(
    app: "FastAPI",
    route_table: RouteTable,
    principal_resolver: "Any | None" = None,
) -> None:
    """Add a /tools endpoint that returns the list of available tools.

    This endpoint supports ETag-based cache validation. The response includes
    the current ETag in both the response body and headers.

    Args:
        app: The FastAPI application instance.
        route_table: The RouteTable to query for tools.
        principal_resolver: Optional API key resolver. When set, the listing
            contains only the tools the caller's key may call.

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
        routes = route_table.list_routes(enabled_only=True, include_deferred=False)
        if principal_resolver is not None:
            principal = getattr(request.state, "principal", None)
            routes = principal_resolver.filter_routes(principal, routes)
        tools = []
        for route in routes:
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

#: Default ceiling on concurrent ``/events`` streams, shared by both
#: OpenAPI backends.  Each stream holds a queue and a task until the client
#: disconnects, so an unbounded set is a memory and file-descriptor leak.
DEFAULT_MAX_SSE_SUBSCRIBERS = 64


def _sse_listener(
    queues: dict[Any, Any], route_table: RouteTable, principal_resolver: Any
) -> Callable[[str, str], None]:
    """Return the RouteTable listener that fans change events out as SSE.

    A scoped client is not told about a tool outside its key's scope: a name
    alone is information it could not obtain from ``tools/list``. ``"*"`` (a
    full refresh) carries no name, so it goes to everyone.
    """
    import asyncio
    import json as _json

    def _on_change(tool_name: str, event: str) -> None:
        payload = _json.dumps({"tool": tool_name, "event": event})
        # TODO: SSE events do not include an ``id:`` field, so clients
        # cannot resume from a Last-Event-ID after reconnection.
        msg = f"event: tool_change\ndata: {payload}\n\n"
        for q, principal in list(queues.items()):
            if principal_resolver is not None and tool_name != "*":
                route = route_table.get_route(tool_name)
                if route is None or not principal_resolver.allows_route(
                    principal, route
                ):
                    continue
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                logger.debug("SSE client queue full, dropping event", tool=tool_name)

    return _on_change


def _sse_endpoint(
    queues: dict[Any, Any],
    principal_resolver: Any,
    max_subscribers: int,
):
    """Return the ``/events`` handler for the configured subscriber set."""
    import asyncio

    from fastapi import HTTPException, Request
    from starlette.responses import StreamingResponse

    keepalive = 30.0

    async def sse_events(request: Request):
        """Stream tool change events via Server-Sent Events."""
        if len(queues) >= max_subscribers:
            logger.warning(
                "SSE subscriber limit reached, rejecting a connection",
                subscribers=len(queues),
                max_subscribers=max_subscribers,
            )
            raise HTTPException(status_code=503, detail="Too many event subscribers")
        principal = getattr(request.state, "principal", None)
        if principal_resolver is not None and principal is None:
            raise HTTPException(status_code=403, detail="Not authenticated")
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        queues[q] = principal

        async def _generate():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=keepalive)
                        yield msg
                    except asyncio.TimeoutError:
                        # Send SSE comment as keepalive to prevent reverse
                        # proxies from closing idle connections.
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                return
            finally:
                queues.pop(q, None)

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return sse_events


def add_events_endpoint(
    app: "FastAPI",
    route_table: RouteTable,
    *,
    max_subscribers: int = DEFAULT_MAX_SSE_SUBSCRIBERS,
    principal_resolver: "Any | None" = None,
) -> None:
    """Add a GET /events SSE endpoint that streams tool change events.

    Each connected client receives server-sent events whenever a tool is
    registered, unregistered, enabled, disabled, or otherwise modified.

    Event format::

        event: tool_change
        data: {"tool": "search", "event": "register"}

    Args:
        app: The FastAPI application instance.
        route_table: The RouteTable whose changes are broadcast.
        max_subscribers: Largest number of concurrent streams. Further
            connections get ``503``. ``0`` disables the endpoint.
        principal_resolver: Optional API key resolver. When set, a stream
            receives change events only for tools its key may call.

    Raises:
        ValueError: If *max_subscribers* is negative.
    """
    if max_subscribers < 0:
        raise ValueError(f"max_subscribers must not be negative, got {max_subscribers}")

    queues: dict[Any, Any] = {}
    route_table.add_listener(_sse_listener(queues, route_table, principal_resolver))
    app.get("/events", tags=["meta"], include_in_schema=True)(
        _sse_endpoint(queues, principal_resolver, max_subscribers)
    )


# ---------------------------------------------------------------------------
# Dynamic OpenAPI schema generation
# ---------------------------------------------------------------------------


def setup_dynamic_openapi(
    app: "FastAPI",
    route_table: RouteTable,
    principal_resolver: "Any | None" = None,
) -> None:
    """Serve ``/openapi.json`` from the RouteTable itself.

    The document is built by
    :func:`~toolregistry_server.adapters.openapi.spec.build_openapi_schema`,
    so disabled tools are absent by construction and no post-filtering or
    ``get_openapi`` monkeypatching is needed. Nothing is cached: the schema
    is regenerated on every request, which is what makes enable/disable
    show up immediately.

    When *principal_resolver* is set, the built-in ``/openapi.json`` route is
    replaced by one that filters the document through the caller's key scope.
    The app-level bearer dependency has already stored the principal on
    ``request.state``; a missing principal yields an empty tool section, so a
    scoped document never discloses a path the key cannot call.

    Args:
        app: The FastAPI application instance.
        route_table: The route table to describe.
        principal_resolver: Optional API key resolver.
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

    if principal_resolver is None:
        return

    from fastapi import Request
    from fastapi.responses import JSONResponse

    openapi_path = getattr(app, "openapi_url", None) or "/openapi.json"
    # FastAPI registers the built-in document route during construction.
    # Remove it so the request-aware route below is the only handler for the
    # path; otherwise the first match would serve an unfiltered document.
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) != openapi_path
    ]

    @app.get(openapi_path, include_in_schema=False)
    async def _scoped_openapi(request: Request) -> JSONResponse:
        principal = getattr(request.state, "principal", None)
        document = build_openapi_schema(
            route_table,
            title=app.title,
            version=app.version,
            description=app.description,
            security_schemes={"HTTPBearer": {"type": "http", "scheme": "bearer"}},
            security=[{"HTTPBearer": []}],
            route_filter=lambda route: principal_resolver.allows_route(
                principal, route
            ),
        )
        return JSONResponse(content=document)
