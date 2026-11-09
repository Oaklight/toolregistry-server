"""Backend-neutral request validation and route metadata.

This module is the shared half of the two OpenAPI servers.  The vendored
``httpserver`` backend and the gateway's FastAPI assembly both call
:func:`_validate_arguments` and :func:`_openapi_extensions`, so neither
implementation depends on the other's framework.  The errors raised here
carry a status code and a JSON-ready detail; each backend renders its own
wire envelope.

The FastAPI assembly itself lives in
:mod:`toolregistry_server.gateway.openapi_asgi` and requires the ``gateway``
extra.
"""

from typing import Any

from toolregistry import ValidationError

from ..._vendor.jsonschema import SchemaValidationError, schema_validate
from ..._vendor.structlog import get_logger
from ...regex_guard import PatternMatcher, SpecPatternError
from ...route_table import RouteEntry, _allows_additional_properties

logger = get_logger(__name__)

#: Default ceiling on concurrent ``/events`` streams, shared by both
#: OpenAPI backends.  Each stream holds a queue and a task until the client
#: disconnects, so an unbounded set is a memory and file-descriptor leak.
DEFAULT_MAX_SSE_SUBSCRIBERS = 64


class RequestValidationError(Exception):
    """A request rejected before the tool runs.

    The exception is framework-neutral: it carries the HTTP status and the
    JSON-ready ``detail`` each server renders.  The httpserver backend wraps
    it in its ``{"detail": ...}`` envelope; the FastAPI assembly maps it with
    an exception handler.

    Args:
        status_code: HTTP status to answer with.
        detail: JSON-ready detail payload.
    """

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


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
        RequestValidationError: 422 when validation fails; 500 when a
            spec-supplied regex times out or does not compile.

    Threading:
        Read-only, does not access ``RouteTable``, and may run in a worker
        thread. Every value comes from the ``route`` snapshot the caller
        already holds. Without an override, ``served_parameters_schema`` is
        a ``cached_property``: on Python 3.12+ concurrent first access may
        compute it twice, but both results are identical and the assignment
        is atomic, so the duplication is benign.
    """
    if not isinstance(raw, dict):
        # The endpoints accept ``Annotated[Any, Body()]``, so FastAPI no
        # longer constrains the body to an object. Reject scalars, arrays
        # and null here instead of failing later inside ``.items()``.
        raise RequestValidationError(
            422,
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
            raise RequestValidationError(status_code=422, detail=details)

    if getattr(tool, "parameters_model", None) is not None:
        try:
            validated = tool.validate_parameters(payload)
        except ValidationError as exc:
            raise RequestValidationError(422, detail=_validation_details(exc)) from exc
        if override_active and not _allows_additional_properties(served_schema):
            # The override advertised a key the tool cannot accept; the
            # model dropped it.  Reject rather than run with less than the
            # client sent.  When the served schema permits additional
            # properties, dropping them is consistent with that contract.
            dropped = sorted(set(payload) - set(validated))
            if dropped:
                raise RequestValidationError(
                    422,
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
        raise RequestValidationError(
            status_code=422, detail=_validation_details(exc)
        ) from exc
    except TimeoutError as exc:
        # Exception text names the bounds, never the pattern.
        logger.warning(
            "Spec regex timed out during request validation",
            tool=route.tool_name,
            error=str(exc),
        )
        raise RequestValidationError(
            500,
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
        raise RequestValidationError(
            500,
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
