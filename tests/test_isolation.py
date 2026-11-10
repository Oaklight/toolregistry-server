"""Per-client isolation primitives (#145).

The hub's rules live here so both frontends share one decision:

* identity headers are refused, never trusted;
* a call waits for a slot and fails with a typed timeout;
* the process permission policy applies to direct calls and to the tool a
  ``call_deferred`` will invoke.
"""

from __future__ import annotations

import math

import pytest
from toolregistry import ToolRegistry
from toolregistry.permissions import (
    PermissionPolicy,
    PermissionResult,
    PermissionRule,
)

from toolregistry_server import RouteTable
from toolregistry_server.isolation import (
    BUSY_ERROR_CODE,
    ClientBusyTimeout,
    ClientLimiter,
    configure_execution_log,
    identity_header,
    operator_refusal,
    policy_refusal,
    policy_refusal_sync,
    principal_key,
    record_tool_call,
    validate_concurrency,
    validate_timeout,
)
from toolregistry_server.keystore import Principal


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def echo(text: str) -> str:
    """Echo text."""
    return text


def _route(table: RouteTable, tool_name: str):
    route = table.get_route(tool_name)
    assert route is not None
    return route


@pytest.fixture
def table() -> RouteTable:
    registry = ToolRegistry()
    registry.register(add, namespace="alpha")
    registry.register(echo, namespace="beta")
    return RouteTable(registry)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_concurrency_accepts_positive_integers(self):
        assert validate_concurrency(1) == 1
        assert validate_concurrency(8) == 8

    @pytest.mark.parametrize("value", [0, -1, True, False, 2.5, "4", None])
    def test_concurrency_rejects_nonsense(self, value):
        with pytest.raises(ValueError):
            validate_concurrency(value)

    def test_timeout_accepts_a_positive_finite_float(self):
        assert validate_timeout(30) == 30.0
        assert validate_timeout(0.5) == 0.5

    @pytest.mark.parametrize("value", [0, -0.1, math.inf, math.nan, True, "5"])
    def test_timeout_rejects_nonsense(self, value):
        with pytest.raises(ValueError):
            validate_timeout(value)


# ---------------------------------------------------------------------------
# Identity headers
# ---------------------------------------------------------------------------


class TestIdentityHeader:
    @pytest.mark.parametrize(
        "name",
        [
            "x-client-id",
            "X-Client-Identity",
            "x-client-cwd",
            "x-client-env",
            "x-client-environment",
            "X-Forwarded-User",
            "x-remote-user",
        ],
    )
    def test_every_reserved_name_is_refused_case_insensitively(self, name):
        assert identity_header({name: "value"}) == name.lower()

    def test_asgi_byte_pairs_are_understood(self):
        pairs = [(b"host", b"example.test"), (b"X-Client-CWD", b"/tmp")]
        assert identity_header(pairs) == "x-client-cwd"

    def test_an_unrelated_client_header_is_left_alone(self):
        assert identity_header({"x-client-version": "1.2.3"}) is None

    def test_no_headers_is_not_a_refusal(self):
        assert identity_header(None) is None
        assert identity_header({}) is None


# ---------------------------------------------------------------------------
# Principals
# ---------------------------------------------------------------------------


class TestPrincipalHelpers:
    def test_a_key_is_identified_by_its_id(self):
        principal = Principal(key_id="key_1", label="build-bot")
        assert principal_key(principal) == "key_1"

    def test_a_static_token_falls_back_to_its_label(self):
        principal = Principal.operator("operator")
        assert principal_key(principal) == "label:operator"

    def test_without_a_principal_there_is_one_anonymous_bucket(self):
        assert principal_key(None) == "anonymous"

    def test_operator_tokens_are_refused_when_keys_are_in_use(self):
        operator = Principal.operator()
        assert operator_refusal(operator, allow_operator=False) is not None
        assert operator_refusal(operator, allow_operator=True) is None

    def test_scoped_keys_are_not_operator_principals(self):
        scoped = Principal(key_id="key_1", label="bot")
        assert operator_refusal(scoped, allow_operator=False) is None


# ---------------------------------------------------------------------------
# Concurrency quota
# ---------------------------------------------------------------------------


class TestClientLimiter:
    def test_a_key_narrows_the_process_limit_and_cannot_raise_it(self):
        limiter = ClientLimiter(default_limit=4, timeout=1)
        assert limiter.limit_for(Principal(key_id="k1")) == 4
        assert limiter.limit_for(Principal(key_id="k2", max_concurrency=2)) == 2
        assert limiter.limit_for(Principal(key_id="k3", max_concurrency=99)) == 4

    def test_constructor_rejects_invalid_knobs(self):
        with pytest.raises(ValueError):
            ClientLimiter(default_limit=0)
        with pytest.raises(ValueError):
            ClientLimiter(default_limit=1, timeout=0)

    async def test_a_call_inside_a_slot_is_counted(self):
        limiter = ClientLimiter(default_limit=1, timeout=1)
        principal = Principal(key_id="key_1", label="one")

        async with limiter.slot(principal):
            [record] = limiter.snapshot()
            assert record["active"] == 1
            assert record["queued"] == 0

        [record] = limiter.snapshot()
        assert record == {
            "client_key": "key_1",
            "client_id": "one",
            "key_id": "key_1",
            "limit": 1,
            "calls": 1,
            "errors": 0,
            "active": 0,
            "queued": 0,
        }

    async def test_an_error_inside_a_slot_is_counted(self):
        limiter = ClientLimiter(default_limit=1, timeout=1)
        principal = Principal(key_id="key_1")

        with pytest.raises(RuntimeError):
            async with limiter.slot(principal):
                raise RuntimeError("tool failed")

        [record] = limiter.snapshot()
        assert record["errors"] == 1
        assert record["active"] == 0

    async def test_waiting_past_the_timeout_is_refused_with_a_typed_error(self):
        limiter = ClientLimiter(default_limit=1, timeout=0.05)
        principal = Principal(key_id="key_1")

        async with limiter.slot(principal):
            with pytest.raises(ClientBusyTimeout) as caught:
                async with limiter.slot(principal):
                    pass

        assert "too many concurrent calls" in str(caught.value)
        assert caught.value.limit == 1
        [record] = limiter.snapshot()
        assert record["errors"] == 1
        assert record["active"] == 0
        assert record["queued"] == 0

    async def test_two_clients_do_not_share_one_budget(self):
        limiter = ClientLimiter(default_limit=1, timeout=0.2)
        first = Principal(key_id="key_1")
        second = Principal(key_id="key_2")

        async with limiter.slot(first), limiter.slot(second):
            pass

        keys = [record["client_key"] for record in limiter.snapshot()]
        assert keys == ["key_1", "key_2"]

    async def test_busy_error_code_is_a_server_error(self):
        assert BUSY_ERROR_CODE == -32000


# ---------------------------------------------------------------------------
# Process permission policy
# ---------------------------------------------------------------------------


def _deny(tool_name: str) -> PermissionPolicy:
    return PermissionPolicy(
        rules=[
            PermissionRule(
                name=f"deny-{tool_name}",
                match=lambda tool, _parameters: tool.name == tool_name,
                result=PermissionResult.DENY,
                reason="read-only host",
            )
        ],
        fallback=PermissionResult.ALLOW,
    )


class TestPolicyRefusal:
    async def test_no_policy_allows_everything(self, table):
        route = _route(table, "alpha-add")
        assert await policy_refusal(table.registry, route, {"a": 1, "b": 2}) is None
        assert policy_refusal_sync(table.registry, route, {"a": 1, "b": 2}) is None

    async def test_a_matching_deny_names_the_tool_and_the_reason(self, table):
        table.registry.set_permission_policy(_deny("beta-echo"))
        route = _route(table, "beta-echo")

        message = await policy_refusal(table.registry, route, {"text": "hi"})

        assert message == (
            "Tool 'beta-echo' is denied by the process permission policy: "
            "read-only host"
        )

    async def test_an_unmatched_rule_keeps_the_call(self, table):
        table.registry.set_permission_policy(_deny("beta-echo"))
        route = _route(table, "alpha-add")

        assert await policy_refusal(table.registry, route, {"a": 1, "b": 2}) is None

    async def test_ask_without_a_handler_resolves_to_the_registry_fallback(self, table):
        table.registry.set_permission_policy(
            PermissionPolicy(fallback=PermissionResult.ASK)
        )
        route = _route(table, "alpha-add")

        message = await policy_refusal(table.registry, route, {"a": 1, "b": 2})

        assert message is not None
        assert "alpha-add" in message

    async def test_the_sync_helper_reaches_the_same_decision(self, table):
        table.registry.set_permission_policy(_deny("beta-echo"))
        route = _route(table, "beta-echo")

        assert policy_refusal_sync(
            table.registry, route, {"text": "hi"}
        ) == await policy_refusal(table.registry, route, {"text": "hi"})

    async def test_a_deferred_target_is_judged_as_well(self):
        registry = ToolRegistry()
        registry.register(add, namespace="alpha")
        registry.enable_tool_discovery()
        registry.set_permission_policy(_deny("alpha-add"))
        table = RouteTable(registry)
        route = next(
            entry for entry in table.list_routes() if entry.tool_name == "call_deferred"
        )

        message = await policy_refusal(
            registry, route, {"_target_tool": "alpha-add", "a": 1, "b": 2}
        )

        assert message is not None
        assert "alpha-add" in message

    async def test_a_deferred_call_to_an_allowed_target_passes(self):
        registry = ToolRegistry()
        registry.register(add, namespace="alpha")
        registry.enable_tool_discovery()
        registry.set_permission_policy(_deny("beta-echo"))
        table = RouteTable(registry)
        route = next(
            entry for entry in table.list_routes() if entry.tool_name == "call_deferred"
        )

        assert (
            await policy_refusal(
                registry, route, {"_target_tool": "alpha-add", "a": 1, "b": 2}
            )
            is None
        )


class TestUntypedPolicy:
    """A policy core's untyped setter accepted is ignored with one warning."""

    def test_a_duck_typed_policy_is_ignored_and_reported(self, table, monkeypatch):
        from io import StringIO

        from toolregistry_server import isolation
        from toolregistry_server._vendor.structlog import (
            BoundLogger,
            ConsoleRenderer,
            PrintLogger,
            TimeStamper,
            add_log_level,
        )

        stream = StringIO()
        logger = BoundLogger(
            logger=PrintLogger(file=stream),
            processors=[add_log_level, TimeStamper(), ConsoleRenderer(colors=False)],
            context={},
        )
        monkeypatch.setattr(isolation, "logger", logger)

        class FakePolicy:
            def evaluate(self, tool, parameters):  # pragma: no cover - never used
                raise AssertionError("an untyped policy must not be evaluated")

        table.registry.set_permission_policy(FakePolicy())
        route = _route(table, "alpha-add")

        assert policy_refusal_sync(table.registry, route, {"a": 1, "b": 2}) is None
        assert policy_refusal_sync(table.registry, route, {"a": 1, "b": 2}) is None

        output = stream.getvalue()
        assert output.count("not a PermissionPolicy") == 1
        assert "FakePolicy" in output


# ---------------------------------------------------------------------------
# Durable attribution (#224)
# ---------------------------------------------------------------------------


class TestExecutionLogWiring:
    def test_off_by_default_without_a_key_store(self, table):
        configure_execution_log(table.registry, enabled=None, has_keys=False)

        assert table.registry.get_execution_log() is None

    def test_on_by_default_with_a_key_store(self, table):
        configure_execution_log(table.registry, enabled=None, has_keys=True)

        assert table.registry.get_execution_log() is not None

    def test_an_explicit_off_wins(self, table):
        configure_execution_log(table.registry, enabled=False, has_keys=True)

        assert table.registry.get_execution_log() is None

    def test_an_explicit_on_does_not_need_keys(self, table):
        configure_execution_log(table.registry, enabled=True, has_keys=False)

        assert table.registry.get_execution_log() is not None

    def test_an_existing_log_keeps_its_capacity(self, table):
        existing = table.registry.enable_logging(max_entries=5)

        configure_execution_log(table.registry, enabled=True, has_keys=True)

        assert table.registry.get_execution_log() is existing

    def test_a_disabled_log_records_nothing(self, table):
        record_tool_call(table.registry, "alpha-add", {"a": 1, "b": 2}, result=3)

        assert table.registry.get_execution_log() is None

    def test_a_disabled_log_still_charges_key_usage(self, table, tmp_path):
        from toolregistry_server.keystore import ApiKeyStore

        with ApiKeyStore(tmp_path / "keys.db") as store:
            created = store.create("bot", {"namespaces": ["alpha"]})
            principal = store.verify(created.secret)
            assert principal is not None
            assert table.registry.get_execution_log() is None

            record_tool_call(
                table.registry,
                "alpha-add",
                {"a": 1, "b": 2},
                principal=principal,
                result=3,
                outcome="denied",
                error=ValueError("refused"),
            )

            assert table.registry.get_execution_log() is None
            usage = store.usage(created.record.id)
            assert usage.calls == 1
            assert usage.denied == 1

    def test_a_successful_call_keeps_the_identity_out_of_arguments(self, table):
        from toolregistry.admin import ExecutionStatus

        table.registry.enable_logging()
        record_tool_call(
            table.registry,
            "alpha-add",
            {"a": 1, "b": 2},
            principal=Principal(key_id="key_1", label="build-bot"),
            result=3,
            duration_ms=1.5,
        )

        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.tool_name == "alpha-add"
        assert entry.status is ExecutionStatus.SUCCESS
        assert entry.arguments == {"a": 1, "b": 2}
        assert entry.result == "3"
        assert entry.metadata == {"key_id": "key_1", "client_id": "build-bot"}

    def test_a_failed_call_records_the_error_and_a_null_identity(self, table):
        from toolregistry.admin import ExecutionStatus

        table.registry.enable_logging()
        record_tool_call(
            table.registry,
            "beta-echo",
            {"text": "hi"},
            error=ValueError("boom"),
            duration_ms=2.0,
        )

        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.status is ExecutionStatus.ERROR
        assert entry.error == "boom"
        assert entry.exception_type == "ValueError"
        assert entry.result is None
        assert entry.metadata == {"key_id": None, "client_id": None}

    def test_a_structured_result_is_stored_as_json(self, table):
        table.registry.enable_logging()
        record_tool_call(table.registry, "alpha-add", {}, result={"sum": 3})

        [entry] = table.registry.get_execution_log().get_entries()
        assert entry.result == '{"sum": 3}'
