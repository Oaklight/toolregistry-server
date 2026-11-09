"""Route parameter overrides carried by the configuration file (#165).

The ``tool_parameters`` block reached core master ahead of the 0.21.0
release.  On a released core that predates it these tests skip; the
``test-core-master`` CI job runs them against core master.
"""

from __future__ import annotations

import dataclasses
import json
from io import StringIO
from typing import Annotated

import pytest
from pydantic import Field
from toolregistry import ToolRegistry
from toolregistry.config import ToolConfig
from toolregistry.tool import Tool

from toolregistry_server import RouteTable
from toolregistry_server._vendor.structlog import (
    BoundLogger,
    ConsoleRenderer,
    PrintLogger,
    TimeStamper,
    add_log_level,
)
from toolregistry_server.adapters.openapi.adapter import (
    RequestValidationError,
    _validate_arguments,
)
from toolregistry_server.app import App
from toolregistry_server.registry_builder import apply_parameter_overrides
from toolregistry_server.source_manager import SourceStatus

pytestmark = pytest.mark.skipif(
    "tool_parameters" not in {field.name for field in dataclasses.fields(ToolConfig)},
    reason="installed toolregistry predates ToolConfig.tool_parameters",
)


def _capture_logs(monkeypatch) -> StringIO:
    """Route the builder's logger into a StringIO and return the stream."""
    import toolregistry_server.registry_builder as builder

    stream = StringIO()
    log = BoundLogger(
        logger=PrintLogger(file=stream),
        processors=[add_log_level, TimeStamper(), ConsoleRenderer(colors=False)],
        context={},
    )
    monkeypatch.setattr(builder, "logger", log)
    return stream


def _proxy_table() -> RouteTable:
    def proxy(**kwargs):
        return kwargs

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="proxy",
            description="proxy tool",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
                "additionalProperties": False,
            },
            callable=proxy,
        )
    )
    return RouteTable(registry)


#: Defined at module scope: core resolves a postponed annotation through the
#: function's module globals, so a locally scoped ``Annotated``/``Field``
#: pair would fall back to an unconstrained schema.
def _bounded(x: Annotated[int, Field(ge=0, le=100)]) -> int:
    """Bounded."""
    return x


def _bounded_table() -> RouteTable:
    registry = ToolRegistry()
    registry.register(_bounded, name="bounded")
    return RouteTable(registry)


class TestApplyParameterOverrides:
    def test_override_changes_the_published_schema_and_validation(
        self, monkeypatch
    ) -> None:
        """A model-less route serves and enforces the override."""
        table = _proxy_table()
        _capture_logs(monkeypatch)
        config = ToolConfig(
            tool_parameters={
                "proxy": {
                    "type": "object",
                    "properties": {"q": {"type": "integer"}},
                    "required": ["q"],
                    "additionalProperties": False,
                }
            }
        )

        apply_parameter_overrides(table, config)

        route = table.get_route("proxy")
        assert route.served_parameters_schema["properties"]["q"]["type"] == ("integer")
        assert _validate_arguments(route, {"q": 3}) == {"q": 3}
        with pytest.raises(RequestValidationError) as excinfo:
            _validate_arguments(route, {"q": "text"})
        assert excinfo.value.status_code == 422
        # The registry tool is untouched.
        assert table.registry.get_tool("proxy").parameters["properties"]["q"][
            "type"
        ] == ("string")

    def test_stricter_override_is_skipped_with_an_error_log(self, monkeypatch) -> None:
        table = _bounded_table()
        stream = _capture_logs(monkeypatch)
        config = ToolConfig(
            tool_parameters={
                "bounded": {
                    "type": "object",
                    "properties": {"x": {"type": "integer", "maximum": 10}},
                    "required": ["x"],
                }
            }
        )

        apply_parameter_overrides(table, config)

        assert table.get_route("bounded").parameter_schema_override is None
        output = stream.getvalue()
        assert "Skipping incompatible parameter override" in output
        assert "maximum" in output

    def test_looser_override_applies_with_a_warning_log(self, monkeypatch) -> None:
        table = _bounded_table()
        stream = _capture_logs(monkeypatch)
        config = ToolConfig(
            tool_parameters={
                "bounded": {
                    "type": "object",
                    "properties": {"x": {"type": "integer", "maximum": 200}},
                    "required": ["x"],
                }
            }
        )

        apply_parameter_overrides(table, config)

        served = table.get_route("bounded").served_parameters_schema
        assert served["properties"]["x"]["maximum"] == 200
        assert "relaxes" in stream.getvalue()

    def test_unknown_tool_is_skipped(self, monkeypatch) -> None:
        table = _proxy_table()
        stream = _capture_logs(monkeypatch)
        config = ToolConfig(
            tool_parameters={"not-registered": {"type": "object", "properties": {}}}
        )

        apply_parameter_overrides(table, config)

        assert table.get_route("not-registered") is None
        assert "unregistered tool" in stream.getvalue()

    def test_absent_block_leaves_the_table_unchanged(self, monkeypatch) -> None:
        table = _proxy_table()
        before = table.get_route("proxy").served_parameters_schema
        stream = _capture_logs(monkeypatch)

        apply_parameter_overrides(table, ToolConfig())

        assert table.get_route("proxy").served_parameters_schema == before
        assert stream.getvalue() == ""


class TestAppAppliesConfig:
    def test_make_route_table_applies_the_loaded_block(self, tmp_path) -> None:
        """The block loaded from disk reaches the table built by ``App``."""
        path = tmp_path / "tools.json"
        path.write_text(
            json.dumps(
                {
                    "mode": "denylist",
                    "tool_parameters": {
                        "proxy": {
                            "type": "object",
                            "properties": {"q": {"type": "integer"}},
                            "required": ["q"],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        app = App()
        registry = app.prepare_registry(config_path=str(path))

        def proxy(**kwargs):
            return kwargs

        registry.register(
            Tool(
                name="proxy",
                description="proxy tool",
                parameters={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
                callable=proxy,
            )
        )

        table = app._make_route_table(registry)  # noqa: SLF001 - test fixture

        assert table.get_route("proxy").served_parameters_schema["properties"]["q"][
            "type"
        ] == ("integer")

    def test_no_block_keeps_the_tool_schema(self, tmp_path) -> None:
        path = tmp_path / "tools.json"
        path.write_text('{"mode":"denylist"}', encoding="utf-8")
        app = App()
        registry = app.prepare_registry(config_path=str(path))

        @registry.register
        def plain(q: str) -> str:
            return q

        table = app._make_route_table(registry)  # noqa: SLF001 - test fixture

        route = table.get_route("plain")
        assert route.parameter_schema_override is None
        assert route.served_parameters_schema["properties"]["q"]["type"] == "string"


def _write_parameter_config(path, block) -> None:
    """Write a config file carrying *block* as ``tool_parameters``."""
    payload: dict = {"mode": "denylist"}
    if block is not None:
        payload["tool_parameters"] = block
    path.write_text(json.dumps(payload), encoding="utf-8")


def _integer_override() -> dict:
    return {
        "proxy": {
            "type": "object",
            "properties": {"q": {"type": "integer"}},
            "required": ["q"],
            "additionalProperties": False,
        }
    }


def _boolean_override() -> dict:
    return {
        "proxy": {
            "type": "object",
            "properties": {"q": {"type": "boolean"}},
            "required": ["q"],
            "additionalProperties": False,
        }
    }


#: A python source module written into the test's temporary directory.
_LATE_MODULE = '''
class Late:
    def one(self, q: str = "x") -> str:
        """Only late tool."""
        return "late-one"
'''

_LATE_SOURCE = [{"class": "late.Late", "namespace": "late"}]


def _write_late_module(tmp_path, monkeypatch) -> None:
    (tmp_path / "late.py").write_text(_LATE_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))


def _late_override() -> dict:
    """An override that changes an annotation, not a constraint."""
    return {
        "late-one": {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "overridden"}},
        }
    }


def _write_source_config(path, sources, block) -> None:
    payload: dict = {"mode": "denylist", "tools": sources}
    if block is not None:
        payload["tool_parameters"] = block
    path.write_text(json.dumps(payload), encoding="utf-8")


def _source_app(tmp_path, sources, block):
    """Build an app whose config declares *sources* and carries *block*."""
    path = tmp_path / "tools.json"
    _write_source_config(path, sources, block)
    app = App()
    registry = app.prepare_registry(config_path=str(path))
    table = app._make_route_table(registry)  # noqa: SLF001 - test fixture
    app._attach_source_manager(table)  # noqa: SLF001 - test fixture
    return app, table, path


def _pet_spec(*operation_ids: str) -> dict:
    """A minimal OpenAPI document with one GET operation per id."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Pet API", "version": "1"},
        "paths": {
            f"/{operation_id}": {
                "get": {
                    "operationId": operation_id,
                    "responses": {"200": {"description": "ok"}},
                }
            }
            for operation_id in operation_ids
        },
    }


def _reloading_app(tmp_path, block):
    """Build an app, a live table and the manager that can reload the file."""
    path = tmp_path / "tools.json"
    _write_parameter_config(path, block)
    app = App()
    registry = app.prepare_registry(config_path=str(path))

    def proxy(**kwargs):
        return kwargs

    registry.register(
        Tool(
            name="proxy",
            description="proxy tool",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
                "additionalProperties": False,
            },
            callable=proxy,
        )
    )
    table = app._make_route_table(registry)  # noqa: SLF001 - test fixture
    app._attach_source_manager(table)  # noqa: SLF001 - test fixture
    return app, table, path


class TestReloadReappliesParameterOverrides:
    """A config reload updates the served route schema (#165).

    ``apply_parameter_overrides`` used to run only when the route table was
    built, so with ``--config-reload`` an added, changed or removed
    ``tool_parameters`` entry stayed stale until restart.
    """

    @staticmethod
    def _reload(app) -> bool:
        manager = app.source_manager
        assert manager is not None
        return manager.reload_from_path()

    def test_a_reload_applies_a_new_entry(self, tmp_path) -> None:
        app, table, path = _reloading_app(tmp_path, None)
        assert table.get_route("proxy").parameter_schema_override is None

        _write_parameter_config(path, _integer_override())
        assert self._reload(app) is True

        route = table.get_route("proxy")
        assert route.served_parameters_schema["properties"]["q"]["type"] == "integer"
        assert _validate_arguments(route, {"q": 3}) == {"q": 3}
        with pytest.raises(RequestValidationError) as excinfo:
            _validate_arguments(route, {"q": "text"})
        assert excinfo.value.status_code == 422

    def test_a_reload_replaces_a_changed_entry(self, tmp_path) -> None:
        app, table, path = _reloading_app(tmp_path, _integer_override())
        assert (
            table.get_route("proxy").served_parameters_schema["properties"]["q"]["type"]
            == "integer"
        )

        _write_parameter_config(path, _boolean_override())
        assert self._reload(app) is True

        route = table.get_route("proxy")
        assert route.served_parameters_schema["properties"]["q"]["type"] == "boolean"
        assert _validate_arguments(route, {"q": True}) == {"q": True}
        with pytest.raises(RequestValidationError):
            _validate_arguments(route, {"q": 3})

    def test_a_reload_clears_a_removed_entry(self, tmp_path) -> None:
        app, table, path = _reloading_app(tmp_path, _integer_override())

        _write_parameter_config(path, None)
        assert self._reload(app) is True

        route = table.get_route("proxy")
        assert route.parameter_schema_override is None
        assert route.served_parameters_schema["properties"]["q"]["type"] == "string"
        assert _validate_arguments(route, {"q": "text"}) == {"q": "text"}

    def test_an_unchanged_reload_does_not_disturb_the_route(self, tmp_path) -> None:
        app, table, path = _reloading_app(tmp_path, _integer_override())
        route = table.get_route("proxy")
        before = route.parameter_schema_override
        events: list[tuple[str, str]] = []
        table.add_listener(lambda name, event: events.append((name, event)))

        _write_parameter_config(path, _integer_override())
        assert self._reload(app) is True

        assert table.get_route("proxy") is route
        assert route.parameter_schema_override is before
        assert events == []

    def test_an_unchanged_block_reaches_a_late_registered_tool(self) -> None:
        """Startup skips an entry whose tool is missing; a later pass applies it."""
        from toolregistry_server.registry_builder import sync_parameter_overrides

        table = _proxy_table()
        config = ToolConfig(tool_parameters=_integer_override())
        assert table.get_route("proxy").parameter_schema_override is None

        sync_parameter_overrides(table, config, previous=config, new_names={"proxy"})

        served = table.get_route("proxy").served_parameters_schema
        assert served["properties"]["q"]["type"] == "integer"

    def test_removing_and_readding_a_source_drops_the_stale_override(
        self, tmp_path, monkeypatch
    ) -> None:
        _write_late_module(tmp_path, monkeypatch)
        app, table, path = _source_app(tmp_path, _LATE_SOURCE, _late_override())
        route = table.get_route("late-one")
        assert route is not None
        assert (
            route.served_parameters_schema["properties"]["q"].get("description")
            == "overridden"
        )

        # The source and its entry leave together, so the route is gone when
        # the entry is dropped.
        _write_source_config(path, [], None)
        assert self._reload(app) is True
        assert table.get_route("late-one") is None

        # The same name comes back with no entry.  A stored override left
        # behind would be re-attached to the new tool by ``_tool_to_route``.
        _write_source_config(path, _LATE_SOURCE, None)
        assert self._reload(app) is True

        route = table.get_route("late-one")
        assert route is not None
        assert route.parameter_schema_override is None
        assert (
            route.served_parameters_schema["properties"]["q"].get("description")
            != "overridden"
        )

    def test_a_successful_retry_applies_the_entry(self, tmp_path, monkeypatch) -> None:
        import toolregistry_server.source_manager as source_manager

        _write_late_module(tmp_path, monkeypatch)
        real = source_manager.register_python_source
        attempts = 0

        def flaky(registry, source):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient failure")
            return real(registry, source)

        monkeypatch.setattr(source_manager, "register_python_source", flaky)
        app, table, _ = _source_app(tmp_path, _LATE_SOURCE, _late_override())

        manager = app.source_manager
        assert manager is not None
        entry = next(e for e in manager.list() if e.source.namespace == "late")
        assert entry.status is SourceStatus.FAILED
        assert table.get_route("late-one") is None

        entry = manager.retry(entry.key)

        assert entry.status is SourceStatus.APPLIED
        route = table.get_route("late-one")
        assert route is not None
        assert route.parameter_schema_override is not None
        assert (
            route.served_parameters_schema["properties"]["q"].get("description")
            == "overridden"
        )

    def test_an_openapi_refresh_reaches_a_tool_it_adds(self, tmp_path) -> None:
        spec_path = tmp_path / "pets.json"
        spec_path.write_text(json.dumps(_pet_spec("list_pets")), encoding="utf-8")
        app, table, _ = _source_app(
            tmp_path,
            [{"type": "openapi", "url": str(spec_path), "namespace": "pet"}],
            {"pet-list_owners": {"type": "object", "properties": {}}},
        )
        manager = app.source_manager
        assert manager is not None
        assert table.get_route("pet-list_pets") is not None
        assert table.get_route("pet-list_owners") is None

        spec_path.write_text(
            json.dumps(_pet_spec("list_pets", "list_owners", "list_visits")),
            encoding="utf-8",
        )
        result = manager.refresh(manager.list()[0].to_dict()["id"])

        assert set(result.added) == {"pet-list_owners", "pet-list_visits"}
        # The entry names ``pet-list_owners`` only; the refresh applies it
        # without a config reload and leaves the unnamed tool alone.
        assert table.get_route("pet-list_owners").parameter_schema_override is not None
        assert table.get_route("pet-list_visits").parameter_schema_override is None
