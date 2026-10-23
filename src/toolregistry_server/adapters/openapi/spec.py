"""Build an OpenAPI document from a RouteTable without FastAPI.

The served ``/openapi.json`` used to be derived from the FastAPI routers,
which tied the document to the framework: ``get_openapi`` plus an uncached
``app.openapi`` monkeypatch, and ``_filter_disabled_paths`` to remove
disabled tools after the fact.  :class:`~toolregistry_server.RouteTable`
already owns the contract, so the document is built directly from it —
disabled routes are simply not emitted — and the adapter serves the result.

The builder emits one ``POST`` operation per enabled route, with the same
identity the FastAPI adapter produced:

- ``operationId`` — the tool name,
- ``summary`` — the first 120 characters of the tool description,
- ``tags`` — the top-level segment of the namespace,
- ``requestBody`` — the tool's parameter schema, inlined as an object
  (FastAPI published the same shape through a ``$ref``),
- ``responses`` — ``200`` carrying the tool's declared
  ``outputSchema`` (or an empty schema), and ``422`` with the
  validation-error envelope inlined,
- ``security`` / ``components.securitySchemes`` — caller-supplied, so
  bearer authentication stays described when the adapter is ported,
- ``x-read-only`` / ``x-destructive`` / ``x-idempotent`` /
  ``x-open-world`` / ``x-title`` — the tool's execution hints.

One deliberate divergence: the full description is emitted as
``description`` as well as the truncated ``summary``, where FastAPI kept
only the summary.

Example:
    ```python
    from toolregistry_server import RouteTable
    from toolregistry_server.adapters.openapi.spec import build_openapi_schema

    document = build_openapi_schema(RouteTable(registry))
    ```
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...route_table import RouteEntry, RouteTable

__all__ = ["build_openapi_schema"]

#: Characters FastAPI kept from the description for the operation summary.
_SUMMARY_LIMIT = 120

#: The 422 body, inlined so the document needs no ``components`` section.
#: It is the shape FastAPI published for ``HTTPValidationError`` with the
#: pydantic ``title`` keys dropped; the server returns this envelope on a
#: validation failure.
_VALIDATION_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "detail": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "loc": {
                        "type": "array",
                        "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                    },
                    "msg": {"type": "string"},
                    "type": {"type": "string"},
                    "input": {},
                    "ctx": {"type": "object"},
                },
                "required": ["loc", "msg", "type"],
            },
        },
    },
}


def _operation_extensions(route: RouteEntry) -> dict[str, Any]:
    """Map a route's execution hints onto ``x-`` operation extensions.

    Mirrors the FastAPI adapter's mapping so the document does not change
    when the server moves off FastAPI.

    Args:
        route: Route whose tool metadata should be serialized.

    Returns:
        Extension mapping; empty when the tool declares nothing.
    """
    extensions: dict[str, Any] = {
        f"x-{hint.removesuffix('_hint').replace('_', '-')}": value
        for hint, value in route.execution_hints.items()
    }
    if route.title is not None:
        extensions["x-title"] = route.title
    return extensions


def _request_schema(route: RouteEntry) -> dict[str, Any]:
    """Return the parameter schema with its declared defaults preserved."""
    schema = route.parameters_schema
    return dict(schema)


def _meta_operations() -> dict[str, dict[str, Any]]:
    """Operations for the server's own endpoints, matching the app.

    ``/tools`` lists the available tools with the current ETag and
    ``/events`` streams change events as SSE.  They are part of the served
    API today, so the document keeps them; ``include_meta=False`` omits
    them for a tools-only description.
    """
    return {
        "/tools": {
            "get": {
                "tags": ["meta"],
                "summary": "List Tools",
                "description": (
                    "List all available tools with their metadata.\n\n"
                    "Returns a JSON object containing:\n"
                    "- tools: List of tool information "
                    "(name, namespace, method, path, description)\n"
                    "- etag: Current ETag for cache validation\n\n"
                    "Supports conditional requests via If-None-Match header.\n"
                    "Returns 304 Not Modified if the ETag matches."
                ),
                "operationId": "list_tools_tools_get",
                "responses": {
                    "200": {
                        "description": "Successful Response",
                        "content": {"application/json": {"schema": {}}},
                    }
                },
            }
        },
        "/events": {
            "get": {
                "tags": ["meta"],
                "summary": "Sse Events",
                "description": "Stream tool change events via Server-Sent Events.",
                "operationId": "sse_events_events_get",
                "responses": {
                    "200": {
                        "description": "Successful Response",
                        "content": {"application/json": {"schema": {}}},
                    }
                },
            }
        },
    }


def _operation(route: RouteEntry) -> dict[str, Any]:
    """Build the operation object for one route."""
    tags = []
    namespace = route.namespace
    if namespace:
        tags = [namespace.split("/")[0]]

    # The success body is the tool's declared result shape when it has one,
    # so a client generator sees the real payload instead of FastAPI's
    # ``{"title": "Response Greet"}`` placeholder.
    success_schema = route.output_schema if route.output_schema else {}

    operation: dict[str, Any] = {
        "operationId": route.tool_name,
        "summary": (route.description or "")[:_SUMMARY_LIMIT],
        "tags": tags,
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _request_schema(route)}},
        },
        "responses": {
            "200": {
                "description": "Successful Response",
                "content": {"application/json": {"schema": success_schema}},
            },
            "422": {
                "description": "Validation Error",
                "content": {"application/json": {"schema": _VALIDATION_ERROR_SCHEMA}},
            },
        },
    }
    if route.description:
        operation["description"] = route.description
    operation.update(_operation_extensions(route))
    return operation


def build_openapi_schema(
    route_table: RouteTable,
    *,
    title: str = "ToolRegistry Server",
    version: str = "1.0.0",
    description: str = "OpenAPI server for ToolRegistry tools",
    servers: list[dict[str, str]] | None = None,
    include_meta: bool = True,
    security_schemes: dict[str, Any] | None = None,
    security: list[dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Build an OpenAPI 3.1 document from a route table.

    Only enabled routes are emitted, so disabling or re-enabling a tool is
    reflected the next time the document is built — no post-filtering step
    is needed.

    Args:
        route_table: The route table to describe.
        title: Value for ``info.title``.
        version: Value for ``info.version``.
        description: Value for ``info.description``.
        servers: Optional ``servers`` array.
        include_meta: Emit the server's own ``/tools`` and ``/events``
            operations. Defaults to ``True``, matching the served app.
        security_schemes: Optional ``components.securitySchemes`` mapping,
            e.g. ``{"HTTPBearer": {"type": "http", "scheme": "bearer"}}``.
        security: Optional document-level ``security`` requirement list,
            e.g. ``[{"HTTPBearer": []}]``.  Both are needed to keep bearer
            authentication described once the adapter is ported off FastAPI.

    Returns:
        A JSON-serializable OpenAPI document.
    """
    paths: dict[str, dict[str, Any]] = {}
    for route in route_table.list_routes(enabled_only=True):
        paths.setdefault(route.path, {})["post"] = _operation(route)

    if include_meta:
        paths.update(_meta_operations())

    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": version,
            "description": description,
        },
        "paths": paths,
    }
    if servers:
        document["servers"] = servers
    if security_schemes:
        document["components"] = {"securitySchemes": security_schemes}
    if security:
        document["security"] = security
    return document
