"""OpenAPI source registration coverage using a real Xquik fixture."""

import json
from pathlib import Path
from typing import Any

from toolregistry import ToolRegistry

from toolregistry_server.registry_builder import (
    load_config,
    register_openapi_source,
)

FIXTURE = Path(__file__).parent / "fixtures" / "xquik-read-api.json"


def test_xquik_openapi_config_resolves_header_auth_from_env(
    tmp_path,
    monkeypatch,
) -> None:
    """YAML config accepts a Xquik OpenAPI source with header auth."""
    monkeypatch.setenv("XQUIK_API_KEY", "test-key")
    config_file = tmp_path / "tools.yaml"
    config_file.write_text(
        "\n".join(
            [
                "tools:",
                "  - type: openapi",
                "    url: https://xquik.com/openapi.json",
                "    namespace: xquik",
                "    auth:",
                "      type: header",
                "      header_name: x-api-key",
                "      token_env: XQUIK_API_KEY",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(str(config_file))
    source = config.tools[0]

    assert source.url == "https://xquik.com/openapi.json"
    assert source.namespace == "xquik"
    assert source.auth is not None
    assert source.auth.type == "header"
    assert source.auth.header_name == "x-api-key"
    assert source.auth.token == "test-key"


def test_xquik_openapi_source_registers_server_url_and_header_auth(
    tmp_path,
    monkeypatch,
) -> None:
    """register_openapi_source uses spec servers[] and header auth."""
    spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
    captured: dict[str, Any] = {}

    class CapturedHttpClientConfig:
        def __init__(self, *, base_url: str, headers: dict[str, str]) -> None:
            captured["base_url"] = base_url
            captured["headers"] = headers

    def load_openapi_spec(url: str) -> dict[str, Any]:
        captured["spec_url"] = url
        return spec

    def register_from_openapi(
        client: CapturedHttpClientConfig,
        registered_spec: dict[str, Any],
        *,
        namespace: str | bool,
        persistent: bool,
    ) -> None:
        captured["client"] = client
        captured["spec"] = registered_spec
        captured["namespace"] = namespace
        captured["persistent"] = persistent

    monkeypatch.setattr(
        "toolregistry.integrations.openapi.HttpClientConfig",
        CapturedHttpClientConfig,
    )
    monkeypatch.setattr(
        "toolregistry.integrations.openapi.load_openapi_spec",
        load_openapi_spec,
    )

    registry = ToolRegistry()
    monkeypatch.setattr(registry, "register_from_openapi", register_from_openapi)
    config_file = tmp_path / "xquik-source.yaml"
    config_file.write_text(
        "\n".join(
            [
                "tools:",
                "  - type: openapi",
                "    url: https://xquik.com/openapi.json",
                "    namespace: xquik",
                "    auth:",
                "      type: header",
                "      header_name: x-api-key",
                "      token: test-key",
                "",
            ]
        ),
        encoding="utf-8",
    )

    source = load_config(str(config_file)).tools[0]
    register_openapi_source(registry, source)

    assert captured["spec_url"] == "https://xquik.com/openapi.json"
    assert captured["base_url"] == "https://xquik.com"
    assert captured["headers"] == {"x-api-key": "test-key"}
    assert captured["spec"] is spec
    assert captured["namespace"] == "xquik"
    assert captured["persistent"] is True
