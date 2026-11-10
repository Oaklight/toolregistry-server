"""Per-client isolation for a shared gateway (#145).

A hub serves several clients at once, so three things must stay per client:

* **Identity** comes only from the bearer credential.  A request that claims
  an identity, working directory or environment through a header is refused:
  a shared hub must not let a client name itself, and the shim cannot vouch
  for headers it forwards.
* **Quota** is one in-flight call budget per principal, so one client cannot
  consume every worker.  A client key may carry a lower ``max_concurrency``;
  the process default stays the ceiling.
* **Policy** is the process permission policy.  A key scope can narrow it
  (an allow requires both) but never widen it: the policy is evaluated on
  every call, and ``ASK`` without a handler is a refusal, not an allow.

Counters are kept per principal for the gateway admin extension.  They are
plain integers updated on the event loop, so no lock is needed.

Nothing here reads local files: the shim connects without the hub's lock, so
every decision is made from the authenticated connection.
"""

from __future__ import annotations

import asyncio
import json
import math
import weakref
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from ._vendor.structlog import get_logger
from .scoped_discovery import CALL_DEFERRED_TOOL_NAME, TARGET_ARGUMENT, find_tool

logger = get_logger()

#: Policy types already reported as unenforceable.  A type object stays
#: alive while the policy class does, so a second call is silent.
_untyped_policy_types: weakref.WeakSet[type] = weakref.WeakSet()

#: In-flight calls one principal may have when its key sets no override.
DEFAULT_CLIENT_CONCURRENCY = 8
#: Seconds a call waits for a slot before it is refused.
DEFAULT_CONCURRENCY_TIMEOUT = 30.0
#: JSON-RPC server-error code used for "too many concurrent calls".
BUSY_ERROR_CODE = -32000

#: Headers that claim an identity, working directory or environment.  They
#: are refused rather than ignored or forwarded: the credential is the only
#: identity a shared hub accepts.  The list is exact, not a prefix rule, so
#: an unrelated ``x-client-version`` keeps working.
RESERVED_CLIENT_HEADERS = frozenset(
    {
        "x-client-cwd",
        "x-client-env",
        "x-client-environment",
        "x-client-id",
        "x-client-identity",
        "x-forwarded-user",
        "x-remote-user",
    }
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_concurrency(value: Any, *, label: str = "client concurrency") -> int:
    """Return *value* as a positive concurrency limit.

    Args:
        value: The configured limit.
        label: Name used in the error message.

    Returns:
        The limit as an ``int``.

    Raises:
        ValueError: If *value* is not an integer of at least one.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer, got {value!r}")
    if value < 1:
        raise ValueError(f"{label} must be at least 1, got {value!r}")
    return value


def validate_timeout(value: Any, *, label: str = "concurrency timeout") -> float:
    """Return *value* as a positive, finite number of seconds.

    Args:
        value: The configured timeout.
        label: Name used in the error message.

    Returns:
        The timeout as a ``float``.

    Raises:
        ValueError: If *value* is not a finite number greater than zero.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number of seconds, got {value!r}")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{label} must be a positive number of seconds, got {value!r}")
    return seconds


# ---------------------------------------------------------------------------
# Identity headers
# ---------------------------------------------------------------------------


def identity_header(headers: Any) -> str | None:
    """Return the reserved header a request carries, lowercased, if any.

    Args:
        headers: A case-insensitive mapping (Starlette), a plain mapping, or
            the raw ``(name, value)`` pairs of an ASGI scope.

    Returns:
        The first reserved header name found, or ``None``.
    """
    if not headers:
        return None
    items = headers.items() if hasattr(headers, "items") else headers
    try:
        iterator = iter(items)
    except TypeError:
        return None
    for name, _value in iterator:
        if isinstance(name, bytes):
            name = name.decode("latin-1", "replace")
        lowered = str(name).lower()
        if lowered in RESERVED_CLIENT_HEADERS:
            return lowered
    return None


# ---------------------------------------------------------------------------
# Principal helpers
# ---------------------------------------------------------------------------


def principal_key(principal: Any) -> str:
    """Return the accounting key for *principal*.

    A stored key is identified by its id.  An OAuth principal is identified
    by its client and label together, so two clients acting for the same
    user never share a concurrency budget or a counter.  A static token (or
    any principal without a key row or client) falls back to its label, so
    operators and anonymous callers each get their own budget.

    Args:
        principal: The authenticated caller.

    Returns:
        A stable key for the limiter and the counters.
    """
    key_id = getattr(principal, "key_id", None)
    if key_id:
        return str(key_id)
    client_id = getattr(principal, "client_id", None)
    label = getattr(principal, "label", None)
    if client_id:
        return f"oauth:{client_id}:{label or '-'}"
    return f"label:{label}" if label else "anonymous"


def principal_client_id(principal: Any) -> str | None:
    """Return the client identity for *principal*, if any.

    An OAuth principal names the client that obtained the token; an API-key
    principal names the key's label.  Keeping the OAuth client separate from
    the human ``sub`` prevents cross-client accounting.
    """
    client_id = getattr(principal, "client_id", None)
    if client_id:
        return str(client_id)
    label = getattr(principal, "label", None)
    return str(label) if label else None


def operator_refusal(principal: Any, *, allow_operator: bool) -> str | None:
    """Return why an operator token may not use MCP, or ``None``.

    Static bearer tokens are unscoped operator credentials.  Once a key
    store is in use, a shared hub accepts client keys only; an operator
    token would be a credential every client could share.  The refusal is
    configurable so a deployment that has not adopted keys keeps working.

    Args:
        principal: The resolved principal.
        allow_operator: Whether the deployment opted back in.

    Returns:
        The refusal message, or ``None``.
    """
    if allow_operator:
        return None
    if principal is not None and getattr(principal, "unrestricted", False):
        return (
            "operator tokens are not accepted on MCP requests; use a client "
            "key from the key store, or start the server with "
            "--allow-operator-token"
        )
    return None


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


class ClientBusyTimeout(RuntimeError):
    """A call waited for its client's slot longer than allowed."""

    def __init__(self, client_key: str, limit: int, timeout: float) -> None:
        self.client_key = client_key
        self.limit = limit
        self.timeout = timeout
        super().__init__(
            f"too many concurrent calls for client {client_key!r}: "
            f"{limit} in flight, waited {timeout:g}s for a slot"
        )


@dataclass
class _Counters:
    calls: int = 0
    errors: int = 0
    active: int = 0
    queued: int = 0


@dataclass
class _ClientState:
    client_id: str | None
    key_id: str | None
    limit: int
    counters: _Counters = field(default_factory=_Counters)
    semaphore: asyncio.Semaphore | None = None


class ClientLimiter:
    """One in-flight call budget per principal.

    Args:
        default_limit: Calls a principal may run at once when its key sets
            no ``max_concurrency``.
        timeout: Seconds a call waits for a slot before it fails.

    Raises:
        ValueError: If *default_limit* or *timeout* is out of range.
    """

    def __init__(
        self,
        default_limit: int = DEFAULT_CLIENT_CONCURRENCY,
        timeout: float = DEFAULT_CONCURRENCY_TIMEOUT,
    ) -> None:
        self._default = validate_concurrency(default_limit)
        self._timeout = validate_timeout(timeout)
        self._clients: dict[str, _ClientState] = {}

    @property
    def default_limit(self) -> int:
        """The limit used when the principal's key sets no override."""
        return self._default

    @property
    def timeout(self) -> float:
        """Seconds a call may wait for a slot."""
        return self._timeout

    def limit_for(self, principal: Any) -> int:
        """Return the concurrency limit that applies to *principal*.

        The process value is the ceiling.  A key's ``max_concurrency`` can
        narrow it, never raise it: an operator grants a client less of the
        shared budget, and the process flag stays the deployment's bound.
        """
        override = getattr(principal, "max_concurrency", None)
        if (
            isinstance(override, int)
            and not isinstance(override, bool)
            and override > 0
        ):
            return min(override, self._default)
        return self._default

    def _state_for(self, principal: Any) -> tuple[str, _ClientState]:
        key = principal_key(principal)
        limit = self.limit_for(principal)
        state = self._clients.get(key)
        if state is None:
            state = _ClientState(
                client_id=principal_client_id(principal),
                key_id=getattr(principal, "key_id", None),
                limit=limit,
            )
            self._clients[key] = state
        if state.limit != limit or state.semaphore is None:
            # A changed override takes effect for calls that start now.
            state.limit = limit
            state.semaphore = asyncio.Semaphore(limit)
        return key, state

    @asynccontextmanager
    async def slot(self, principal: Any) -> AsyncIterator[None]:
        """Hold one slot for *principal* for the duration of a call.

        Args:
            principal: The authenticated caller.

        Raises:
            ClientBusyTimeout: If no slot opens within the configured wait.
        """
        key, state = self._state_for(principal)
        semaphore = state.semaphore
        assert semaphore is not None  # set by _state_for
        counters = state.counters
        counters.queued += 1
        try:
            try:
                await asyncio.wait_for(semaphore.acquire(), self._timeout)
            except (asyncio.TimeoutError, TimeoutError) as exc:
                counters.errors += 1
                raise ClientBusyTimeout(key, state.limit, self._timeout) from exc
        finally:
            counters.queued -= 1
        counters.active += 1
        counters.calls += 1
        try:
            yield
        except BaseException:
            counters.errors += 1
            raise
        finally:
            counters.active -= 1
            semaphore.release()

    def snapshot(self) -> list[dict[str, Any]]:
        """Return one record per principal seen, sorted by client key."""
        return [
            {
                "client_key": key,
                "client_id": state.client_id,
                "key_id": state.key_id,
                "limit": state.limit,
                "calls": state.counters.calls,
                "errors": state.counters.errors,
                "active": state.counters.active,
                "queued": state.counters.queued,
            }
            for key, state in sorted(self._clients.items())
        ]


# ---------------------------------------------------------------------------
# Process permission policy
# ---------------------------------------------------------------------------


async def policy_refusal(
    registry: Any, route: Any, arguments: Mapping[str, Any]
) -> str | None:
    """Return why the process permission policy refuses *route*, or ``None``.

    The key scope and the process policy both have to allow a call.  A
    configured policy therefore applies to every frontend: a rule that one
    frontend bypassed would be no rule at all.  ``ASK`` goes through the
    registry's handler when one exists; otherwise it resolves to the
    registry fallback, which defaults to ``DENY``.

    ``call_deferred`` is judged twice: once as itself, and once as the tool
    it will invoke, because core runs that tool below the route this check
    sees.

    Args:
        registry: The live ``ToolRegistry``.
        route: The route about to be called.
        arguments: The validated call arguments.

    Returns:
        A refusal message naming the tool and the reason, or ``None``.
    """
    policy = _permission_policy(registry)
    if policy is None:
        return None
    resolver = getattr(registry, "_aresolve_permission", None)
    for tool, parameters, name in _evaluations(registry, route, arguments):
        if resolver is not None:
            result = await resolver(tool, parameters)
        else:  # pragma: no cover - all supported cores expose the helper
            result = _evaluate_policy(registry, policy, tool, parameters)
        message = _denial(name, policy, tool, parameters, result)
        if message is not None:
            return message
    return None


def policy_refusal_sync(
    registry: Any, route: Any, arguments: Mapping[str, Any]
) -> str | None:
    """Synchronous counterpart to :func:`policy_refusal`.

    A synchronous frontend cannot await the registry's async helper, so this
    uses the synchronous one.  Both reach the same decision: an ``ASK`` is
    answered by the handler when one is configured, else by the fallback.
    """
    policy = _permission_policy(registry)
    if policy is None:
        return None
    resolver = getattr(registry, "_resolve_permission", None)
    for tool, parameters, name in _evaluations(registry, route, arguments):
        if resolver is not None:
            result = resolver(tool, parameters)
        else:  # pragma: no cover - all supported cores expose the helper
            result = _evaluate_policy(registry, policy, tool, parameters)
        message = _denial(name, policy, tool, parameters, result)
        if message is not None:
            return message
    return None


def _evaluations(
    registry: Any, route: Any, arguments: Mapping[str, Any]
) -> list[tuple[Any, dict[str, Any], str]]:
    """Return every ``(tool, parameters, name)`` a call must satisfy.

    A direct call has one: the route's tool.  ``call_deferred`` has two,
    because its argument names a tool that core invokes directly.
    """
    pairs: list[tuple[Any, dict[str, Any], str]] = []
    tool = getattr(route, "tool", None)
    if tool is not None:
        name = str(getattr(route, "tool_name", getattr(tool, "name", "tool")))
        pairs.append((tool, dict(arguments), name))
    proxied = _deferred_target(registry, route, arguments)
    if proxied is not None:
        pairs.append(proxied)
    return pairs


def _deferred_target(
    registry: Any, route: Any, arguments: Mapping[str, Any]
) -> tuple[Any, dict[str, Any], str] | None:
    """Return the tool ``call_deferred`` will invoke, with its arguments."""
    if getattr(route, "tool_name", None) != CALL_DEFERRED_TOOL_NAME:
        return None
    if not isinstance(arguments, Mapping):
        return None
    target_name = arguments.get(TARGET_ARGUMENT)
    if not isinstance(target_name, str) or not target_name:
        return None
    tool = find_tool(registry, target_name)
    if tool is None:
        return None
    parameters = {
        key: value for key, value in arguments.items() if key != TARGET_ARGUMENT
    }
    return tool, parameters, str(getattr(tool, "name", target_name))


def _denial(
    name: str, policy: Any, tool: Any, parameters: dict[str, Any], result: Any
) -> str | None:
    """Turn a permission result into a refusal message, or ``None``."""
    if _is_allow(result):
        return None
    message = f"Tool {name!r} is denied by the process permission policy"
    reason = _policy_reason(policy, tool, parameters)
    return f"{message}: {reason}" if reason else message


def _permission_policy(registry: Any) -> Any:
    """Return the registry's policy, or ``None`` when it has none.

    The type check matters: a test double for the registry answers every
    attribute with a fresh mock, and a mock policy would turn every call
    into a decision it never made.  Core's policy type is the contract.  A
    policy of any other type is ignored with one warning, so an operator who
    installed a duck-typed policy learns that it is not enforced.
    """
    from toolregistry.permissions import PermissionPolicy

    getter = getattr(registry, "get_permission_policy", None)
    if not callable(getter):
        return None
    policy = getter()
    if policy is None:
        return None
    if not isinstance(policy, PermissionPolicy):
        _warn_untyped_policy(policy)
        return None
    return policy


def _warn_untyped_policy(policy: Any) -> None:
    """Warn once per type about a policy this server will not enforce."""
    policy_type = type(policy)
    try:
        if policy_type in _untyped_policy_types:
            return
        _untyped_policy_types.add(policy_type)
    except TypeError:  # pragma: no cover - every type object is weakrefable
        pass
    logger.warning(
        "ignoring a permission policy that is not a PermissionPolicy; "
        "tool calls are not checked",
        policy_type=policy_type.__name__,
    )


def _is_allow(result: Any) -> bool:
    """Whether a permission result is an allow."""
    return getattr(result, "value", result) == "allow"


def _evaluate_policy(
    registry: Any, policy: Any, tool: Any, parameters: dict[str, Any]
) -> Any:
    """Evaluate *policy* without core's handler bridge (fallback path)."""
    from toolregistry.permissions import PermissionResult

    outcome = policy.evaluate(tool, parameters)
    result = outcome if isinstance(outcome, PermissionResult) else outcome.result
    if result is not PermissionResult.ASK:
        return result
    handler = policy.handler or registry.get_permission_handler()
    if handler is not None:  # pragma: no cover - handled by the registry helper
        return result
    fallback = getattr(registry, "permission_fallback", PermissionResult.DENY)
    return PermissionResult.DENY if fallback is PermissionResult.ASK else fallback


def _policy_reason(policy: Any, tool: Any, parameters: dict[str, Any]) -> str:
    """Return the matched rule's reason, if the policy has one."""
    try:
        outcome = policy.evaluate(tool, parameters)
    except Exception:  # pragma: no cover - a policy must not break a refusal
        return ""
    return str(getattr(outcome, "reason", "") or "")


# ---------------------------------------------------------------------------
# Durable attribution (#224)
# ---------------------------------------------------------------------------


def configure_execution_log(
    registry: Any, *, enabled: bool | None, has_keys: bool
) -> None:
    """Turn the registry execution log on for a serving process.

    The server calls a route handler directly instead of core's ``invoke``,
    so core never writes an entry on its own.  Attribution below therefore
    records nothing unless this switch turned the log on.

    ``None`` means "on when a client identity is configured": a key store,
    or OAuth access tokens.  That is the deployment where a call has a
    stable client worth retaining.  An explicit ``True`` enables it without
    either, and ``False`` leaves it off.  A log the operator already enabled
    keeps its own capacity and settings.

    Args:
        registry: The live ``ToolRegistry`` the frontends serve.
        enabled: The configured switch, or ``None`` for the client default.
        has_keys: Whether a key store or an OAuth issuer is configured.
    """
    if enabled is None:
        enabled = has_keys
    if not enabled:
        return
    getter = getattr(registry, "get_execution_log", None)
    if getter is not None and getter() is not None:
        return
    registry.enable_logging()


def record_tool_call(
    registry: Any,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    principal: Any = None,
    result: Any = None,
    error: BaseException | None = None,
    duration_ms: float = 0.0,
    outcome: str | None = None,
) -> None:
    """Append one attributed entry to the registry log, if it is enabled.

    The entry keeps core's shape and adds ``key_id``/``client_id`` under
    ``metadata``; the identity stays out of ``arguments``.  A refusal
    (``busy`` or ``denied``) also stores its outcome in ``metadata`` so the
    Keys page can separate it from a handler error.  An executed call derives
    ``ok`` or ``error`` from *error* and keeps the historical metadata shape.

    The same call updates the key's persisted usage counters through the
    principal's store, so the Keys page and ``keys list --usage`` agree
    without scanning the log on every request.

    A disabled log skips the entry but still charges the usage counter: the
    counters are cheap, and an operator who turned the log off to save memory
    keeps the Keys page working.

    Args:
        registry: The live ``ToolRegistry`` the frontends serve.
        tool_name: The tool the client called.
        arguments: The validated call arguments.
        principal: The authenticated caller, or ``None`` when anonymous.
        result: The handler's result, kept only for a successful call.
        error: The exception that ended the call, if it failed.
        duration_ms: Wall-clock duration of the handler call.
        outcome: ``"ok"``, ``"error"``, ``"busy"`` or ``"denied"``.  ``None``
            derives ``ok`` or ``error`` from *error*.
    """
    getter = getattr(registry, "get_execution_log", None)
    log = getter() if getter is not None else None
    resolved = outcome or ("error" if error is not None else "ok")
    if log is not None:
        metadata: dict[str, Any] = {
            "key_id": getattr(principal, "key_id", None),
            "client_id": principal_client_id(principal),
        }
        if resolved in ("busy", "denied"):
            metadata["outcome"] = resolved
        from toolregistry.admin import ExecutionLogEntry, ExecutionStatus

        log.add(
            ExecutionLogEntry.create(
                tool_name=tool_name,
                status=(
                    ExecutionStatus.ERROR
                    if error is not None
                    else ExecutionStatus.SUCCESS
                ),
                duration_ms=duration_ms,
                arguments=dict(arguments),
                result=None if error is not None else _result_text(result),
                error=None if error is None else str(error),
                exception_type=None if error is None else type(error).__qualname__,
                metadata=metadata,
            )
        )
    _record_usage(principal, resolved)


def record_refused_call(
    registry: Any,
    tool_name: str,
    *,
    principal: Any = None,
    outcome: str,
    reason: str | None = None,
    duration_ms: float = 0.0,
) -> None:
    """Append one refusal to the registry log, if it is enabled.

    A denied or busy call never reaches the tool handler, so an executed
    call would not record it.  The row is written through
    :func:`record_tool_call` with *outcome* set, so the Keys page and the
    usage counters treat a refusal like any other attributed outcome.  A
    refusal keeps ``arguments`` empty on purpose: the payload of a call that
    was not allowed is not needed to audit the decision.

    Args:
        registry: The live ``ToolRegistry`` the frontends serve.
        tool_name: The tool the client named.
        principal: The authenticated caller, or ``None`` when anonymous.
        outcome: ``"denied"`` or ``"busy"``.
        reason: The refusal message.
        duration_ms: Wall-clock duration before the refusal.
    """
    message = f"{outcome}: {reason}" if reason else outcome
    record_tool_call(
        registry,
        tool_name,
        {},
        principal=principal,
        error=ValueError(message),
        duration_ms=duration_ms,
        outcome=outcome,
    )


def _record_usage(principal: Any, outcome: str) -> None:
    """Charge one call to the principal's key, if it has one.

    A usage write must never fail the call it describes: a locked or
    read-only database keeps the tool serving and only loses a counter.
    """
    store = getattr(principal, "store", None)
    key_id = getattr(principal, "key_id", None)
    record = getattr(store, "record_usage", None) if store is not None else None
    if not key_id or record is None:
        return
    try:
        record(key_id, outcome)
    except Exception:  # pragma: no cover - a broken store must not break a call
        logger.warning("Failed to record key usage", key_id=key_id, exc_info=True)


def _result_text(result: Any) -> str | None:
    """Return a bounded, JSON-safe rendering of a handler result."""
    if result is None:
        return None
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - default=str covers most
        return repr(result)


__all__ = [
    "BUSY_ERROR_CODE",
    "DEFAULT_CLIENT_CONCURRENCY",
    "DEFAULT_CONCURRENCY_TIMEOUT",
    "RESERVED_CLIENT_HEADERS",
    "ClientBusyTimeout",
    "ClientLimiter",
    "configure_execution_log",
    "identity_header",
    "operator_refusal",
    "policy_refusal",
    "policy_refusal_sync",
    "principal_client_id",
    "principal_key",
    "record_refused_call",
    "record_tool_call",
    "validate_concurrency",
    "validate_timeout",
]
