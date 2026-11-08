"""Tests for gateway frontend state and path matching."""

import pytest

from toolregistry_server.gateway.frontends import (
    DEFAULT_PATHS,
    FRONTEND_NAMES,
    TRANSPORTS,
    Frontend,
    FrontendRegistry,
    default_frontends,
    normalize_path,
)


class TestNormalizePath:
    """A frontend path must be a stable, non-root absolute path."""

    def test_adds_no_prefix(self):
        assert normalize_path("/mcp") == "/mcp"

    def test_strips_trailing_slash(self):
        assert normalize_path("/mcp/") == "/mcp"

    def test_strips_multiple_trailing_slashes(self):
        assert normalize_path("/api/mcp///") == "/api/mcp"

    @pytest.mark.parametrize(
        "path",
        ["", "mcp", "/", " /mcp", "/mcp ", "/mcp?x=1", "/mcp#frag", "/a//b"],
    )
    def test_rejects_invalid_paths(self, path):
        with pytest.raises(ValueError):
            normalize_path(path)

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="string"):
            normalize_path(42)  # ty: ignore[invalid-argument-type]


class TestDefaultFrontends:
    """The default set serves all three transports."""

    def test_one_record_per_frontend(self):
        records = default_frontends()
        assert [record.name for record in records] == list(FRONTEND_NAMES)

    def test_all_enabled_on_default_paths(self):
        for record in default_frontends():
            assert record.enabled is True
            assert record.path == DEFAULT_PATHS[record.name]
            assert record.transport == TRANSPORTS[record.name]


class TestFrontendRegistry:
    """The registry is the per-request source of path and enable state."""

    def test_snapshot_is_ordered(self):
        registry = FrontendRegistry()
        assert [record.name for record in registry.snapshot()] == list(FRONTEND_NAMES)

    def test_get_unknown_frontend_raises(self):
        registry = FrontendRegistry()
        with pytest.raises(ValueError, match="unknown frontend"):
            registry.get("grpc")

    def test_update_enabled_takes_effect_immediately(self):
        registry = FrontendRegistry()
        record = registry.update("mcp", enabled=False)
        assert record.enabled is False
        assert registry.get("mcp").enabled is False
        assert registry.enabled_snapshot() == [
            registry.get("openapi"),
            registry.get("sse"),
        ]

    def test_update_path_takes_effect_immediately(self):
        registry = FrontendRegistry()
        registry.update("mcp", path="/rpc/")
        assert registry.get("mcp").path == "/rpc"

    def test_update_rejects_unknown_frontend(self):
        registry = FrontendRegistry()
        with pytest.raises(ValueError, match="unknown frontend"):
            registry.update("grpc", enabled=False)

    def test_update_requires_boolean_enabled(self):
        registry = FrontendRegistry()
        with pytest.raises(ValueError, match="boolean"):
            registry.update("mcp", enabled="false")  # ty: ignore[invalid-argument-type]

    def test_update_rejects_duplicate_path(self):
        registry = FrontendRegistry()
        with pytest.raises(ValueError, match="share path"):
            registry.update("mcp", path="/sse")

    def test_constructor_rejects_duplicate_path(self):
        with pytest.raises(ValueError, match="share path"):
            FrontendRegistry(
                [
                    Frontend("openapi", "/x", True, "http"),
                    Frontend("mcp", "/x", True, "streamable-http"),
                    Frontend("sse", "/sse", True, "sse"),
                ]
            )

    def test_constructor_rejects_unknown_name(self):
        with pytest.raises(ValueError, match="unknown frontend"):
            FrontendRegistry([Frontend("grpc", "/grpc", True, "grpc")])

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/mcp", "mcp"),
            ("/mcp/", "mcp"),
            ("/mcp/session", "mcp"),
            ("/openapi/tools", "openapi"),
            ("/sse/messages/", "sse"),
            ("/mcpish", None),
            ("/", None),
        ],
    )
    def test_match(self, path, expected):
        registry = FrontendRegistry()
        record = registry.match(path)
        assert (record.name if record else None) == expected

    def test_match_uses_longest_prefix(self):
        registry = FrontendRegistry(
            [
                Frontend("openapi", "/api", True, "http"),
                Frontend("mcp", "/api/mcp", True, "streamable-http"),
                Frontend("sse", "/sse", True, "sse"),
            ]
        )
        nested = registry.match("/api/mcp/x")
        outer = registry.match("/api/other")
        assert nested is not None
        assert outer is not None
        assert nested.name == "mcp"
        assert outer.name == "openapi"

    def test_disabled_frontend_still_matches(self):
        registry = FrontendRegistry()
        registry.update("mcp", enabled=False)
        matched = registry.match("/mcp")
        assert matched is not None
        assert matched.enabled is False

    def test_locked_path_cannot_change(self):
        registry = FrontendRegistry(locked_names=frozenset({"openapi"}))
        with pytest.raises(ValueError, match="cannot change"):
            registry.update("openapi", path="/api")
