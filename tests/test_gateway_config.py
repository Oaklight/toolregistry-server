"""Tests for gateway frontend configuration persistence."""

import json

import pytest
from toolregistry import ToolRegistry

from toolregistry_server import RouteTable
from toolregistry_server.gateway import GatewayAdapter
from toolregistry_server.gateway.persistence import (
    load_frontend_settings,
    persist_frontend_settings,
    read_config_mapping,
)
from toolregistry_server.source_manager import COMMENTS_WARNING


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def _make_route_table():
    registry = ToolRegistry()

    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    registry.register(add)
    return RouteTable(registry)


class TestLoadFrontendSettings:
    """Stored settings are read from the gateway section only."""

    def test_missing_config_path_returns_empty(self):
        assert load_frontend_settings(None) == {}

    def test_missing_section_returns_empty(self, tmp_path):
        config = _write(tmp_path / "tools.yaml", "tools: []\n")
        assert load_frontend_settings(config) == {}

    def test_reads_yaml_settings(self, tmp_path):
        config = _write(
            tmp_path / "tools.yaml",
            "tools: []\n"
            "gateway:\n"
            "  frontends:\n"
            "    mcp:\n"
            "      path: /rpc\n"
            "      enabled: false\n",
        )
        assert load_frontend_settings(config) == {
            "mcp": {"path": "/rpc", "enabled": False}
        }

    def test_reads_jsonc_settings(self, tmp_path):
        config = _write(
            tmp_path / "tools.jsonc",
            '{\n  // comment\n  "tools": [],\n'
            '  "gateway": {"frontends": {"sse": {"path": "/events"}}}\n}\n',
        )
        assert load_frontend_settings(config) == {
            "sse": {"path": "/events", "enabled": True}
        }

    def test_admin_path_without_a_flag_stays_disabled(self, tmp_path):
        config = _write(
            tmp_path / "tools.yaml",
            "gateway:\n  frontends:\n    admin:\n      path: /panel\n",
        )
        assert load_frontend_settings(config) == {
            "admin": {"path": "/panel", "enabled": False}
        }

    def test_reads_admin_settings(self, tmp_path):
        config = _write(
            tmp_path / "tools.yaml",
            "gateway:\n  frontends:\n    admin:\n      path: /panel\n"
            "      enabled: true\n",
        )
        assert load_frontend_settings(config) == {
            "admin": {"path": "/panel", "enabled": True}
        }

    def test_rejects_unknown_frontend(self, tmp_path):
        config = _write(
            tmp_path / "tools.yaml",
            "gateway:\n  frontends:\n    grpc:\n      enabled: true\n",
        )
        with pytest.raises(ValueError, match="unknown frontend"):
            load_frontend_settings(config)

    def test_rejects_unknown_frontend_key(self, tmp_path):
        config = _write(
            tmp_path / "tools.yaml",
            "gateway:\n  frontends:\n    mcp:\n      enabled: true\n"
            "      transport: stdio\n",
        )
        with pytest.raises(ValueError, match="unrecognised key"):
            load_frontend_settings(config)

    def test_rejects_non_boolean_enabled(self, tmp_path):
        config = _write(
            tmp_path / "tools.yaml",
            'gateway:\n  frontends:\n    mcp:\n      enabled: "no"\n',
        )
        with pytest.raises(ValueError, match="boolean"):
            load_frontend_settings(config)

    def test_rejects_invalid_path(self, tmp_path):
        config = _write(
            tmp_path / "tools.yaml",
            "gateway:\n  frontends:\n    mcp:\n      path: mcp\n",
        )
        with pytest.raises(ValueError, match="start with"):
            load_frontend_settings(config)

    def test_rejects_unknown_gateway_key(self, tmp_path):
        config = _write(tmp_path / "tools.yaml", "gateway:\n  admin: true\n")
        with pytest.raises(ValueError, match="unrecognised key"):
            load_frontend_settings(config)


class TestPersistFrontendSettings:
    """Writes preserve the rest of the file and leave a backup."""

    def test_yaml_write_preserves_other_settings(self, tmp_path):
        destination = tmp_path / "tools.yaml"
        original = "mode: allowlist\ntools: []\n"
        _write(destination, original)

        persisted, warning = persist_frontend_settings(
            str(destination),
            name="mcp",
            path="/rpc",
            enabled=False,
        )

        assert persisted is True
        assert warning == COMMENTS_WARNING
        data = read_config_mapping(destination)
        assert data["mode"] == "allowlist"
        assert data["tools"] == []
        assert data["gateway"]["frontends"]["mcp"] == {
            "path": "/rpc",
            "enabled": False,
        }

    def test_backup_holds_the_previous_file(self, tmp_path):
        destination = tmp_path / "tools.yaml"
        original = "tools: []\n"
        _write(destination, original)

        persist_frontend_settings(
            str(destination), name="mcp", path="/mcp", enabled=False
        )

        backup = tmp_path / "tools.yaml.bak"
        assert backup.read_text(encoding="utf-8") == original

    def test_json_write_preserves_other_settings(self, tmp_path):
        destination = tmp_path / "tools.json"
        _write(destination, '{"tools": [], "mode": "denylist"}')

        persisted, warning = persist_frontend_settings(
            str(destination), name="openapi", path="/api", enabled=True
        )

        assert persisted is True
        assert warning == COMMENTS_WARNING
        data = json.loads(destination.read_text(encoding="utf-8"))
        assert data["mode"] == "denylist"
        assert data["gateway"]["frontends"]["openapi"] == {
            "path": "/api",
            "enabled": True,
        }

    def test_preserves_other_frontends(self, tmp_path):
        destination = tmp_path / "tools.yaml"
        _write(
            destination,
            "gateway:\n  frontends:\n    sse:\n      enabled: false\n",
        )

        persist_frontend_settings(
            str(destination), name="mcp", path="/mcp", enabled=True
        )

        data = read_config_mapping(destination)
        assert data["gateway"]["frontends"]["sse"]["enabled"] is False
        assert data["gateway"]["frontends"]["mcp"]["enabled"] is True

    def test_write_disabled_is_reported_not_raised(self, tmp_path):
        destination = tmp_path / "tools.yaml"
        original = "tools: []\n"
        _write(destination, original)

        persisted, warning = persist_frontend_settings(
            str(destination),
            name="mcp",
            path="/mcp",
            enabled=False,
            write_config=False,
        )

        assert persisted is False
        assert warning is not None
        assert "--no-config-write" in warning
        assert destination.read_text(encoding="utf-8") == original

    def test_missing_config_path_is_reported(self):
        persisted, warning = persist_frontend_settings(
            None, name="mcp", path="/mcp", enabled=False
        )
        assert persisted is False
        assert warning is not None
        assert "no configuration file" in warning

    def test_write_failure_keeps_in_memory_change(self, tmp_path, monkeypatch):
        destination = tmp_path / "tools.yaml"
        _write(destination, "tools: []\n")

        def _fail(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(
            "toolregistry_server.gateway.persistence._write_config_mapping",
            _fail,
        )
        persisted, warning = persist_frontend_settings(
            str(destination), name="mcp", path="/mcp", enabled=False
        )

        assert persisted is False
        assert warning is not None
        assert "disk full" in warning


class TestUpdateFrontendPersistence:
    """The gateway API reports persistence in its result."""

    def _gateway(self, monkeypatch, config_path):
        monkeypatch.setattr(GatewayAdapter, "_build_mcp_server", lambda self: object())
        return GatewayAdapter.create(
            _make_route_table(), config_path=str(config_path), tokens=[]
        )

    def test_update_persists_and_reports(self, tmp_path, monkeypatch):
        destination = tmp_path / "tools.yaml"
        _write(destination, "tools: []\n")
        gateway = self._gateway(monkeypatch, destination)

        result = gateway.update_frontend("mcp", enabled=False)

        assert result["name"] == "mcp"
        assert result["enabled"] is False
        assert result["persisted"] is True
        assert result["warning"] == COMMENTS_WARNING
        data = read_config_mapping(destination)
        assert data["gateway"]["frontends"]["mcp"] == {
            "path": "/mcp",
            "enabled": False,
        }

    def test_update_without_config_write_reports_warning(self, monkeypatch):
        monkeypatch.setattr(GatewayAdapter, "_build_mcp_server", lambda self: object())
        gateway = GatewayAdapter.create(
            _make_route_table(), write_config=False, tokens=[]
        )

        result = gateway.update_frontend("sse", enabled=False)

        assert result["enabled"] is False
        assert result["persisted"] is False
        assert "--no-config-write" in result["warning"]

    def test_unchanged_httpserver_save_leaves_the_file_alone(self, tmp_path):
        """A no-op save must not rewrite the file or report COMMENTS_WARNING.

        ``list_frontends`` reports ``path: "/"`` on the httpserver backend,
        and the admin submits the record it displayed.  Treating that as a
        path change would drop the file's comments to store the values it
        already holds (#209).
        """
        destination = tmp_path / "tools.jsonc"
        original = '{\n  // keep this comment\n  "tools": []\n}\n'
        _write(destination, original)
        gateway = GatewayAdapter.create(
            _make_route_table(),
            config_path=str(destination),
            backend="httpserver",
            openapi_enabled=True,
            mcp_enabled=False,
            sse_enabled=False,
            tokens=[],
        )

        result = gateway.update_frontend("openapi", path="/", enabled=True)

        assert result["path"] == "/"
        assert result["persisted"] is False
        assert result["warning"] is None
        assert destination.read_text(encoding="utf-8") == original
        assert not (tmp_path / "tools.jsonc.bak").exists()

    def test_unchanged_asgi_save_leaves_the_file_alone(self, tmp_path, monkeypatch):
        destination = tmp_path / "tools.jsonc"
        original = '{\n  // keep this comment\n  "tools": []\n}\n'
        _write(destination, original)
        gateway = self._gateway(monkeypatch, destination)

        result = gateway.update_frontend("mcp", enabled=True)

        assert result["persisted"] is False
        assert result["warning"] is None
        assert destination.read_text(encoding="utf-8") == original

    def test_a_real_change_still_persists(self, tmp_path, monkeypatch):
        destination = tmp_path / "tools.jsonc"
        original = '{\n  // keep this comment\n  "tools": []\n}\n'
        _write(destination, original)
        gateway = self._gateway(monkeypatch, destination)

        result = gateway.update_frontend("mcp", path="/rpc")

        assert result["path"] == "/rpc"
        assert result["persisted"] is True
        assert result["warning"] == COMMENTS_WARNING
        data = read_config_mapping(destination)
        assert data["gateway"]["frontends"]["mcp"]["path"] == "/rpc"
        assert destination.read_text(encoding="utf-8") != original
