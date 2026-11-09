"""Persist gateway frontend settings in the tool configuration file.

Frontend paths and enable flags live under a ``gateway`` section::

    gateway:
      frontends:
        openapi: {path: /openapi, enabled: true}
        mcp: {path: /mcp, enabled: true}
        sse: {path: /sse, enabled: true}
        admin: {path: /admin, enabled: false}

The section is additive: the existing ``tools``, ``mode``, ``profiles`` and
``tool_metadata`` settings are preserved when the file is rewritten.

Write policy
------------

A write follows the same policy as runtime source management:

* the previous file is copied to ``<name>.bak`` before the new content is
  written;
* the new content is written to a temporary file in the same directory and
  moved into place, so a crash cannot leave a truncated configuration;
* the caller receives an explicit ``persisted`` flag and a ``warning``
  string; a skipped or failed write says why, and a successful rewrite
  carries the same JSONC comment-loss warning as runtime source management;
* ``--no-config-write`` (``write_config=False``) disables persistence
  entirely and is reported as a warning rather than an error.

Write failures never roll back the in-memory change: the running gateway
still applies the new setting, and the returned warning tells the operator
that it will not survive a restart.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from .._vendor.structlog import get_logger
from ..source_manager import COMMENTS_WARNING
from .frontends import DEFAULT_ENABLED, DEFAULT_PATHS, FRONTEND_NAMES, normalize_path

logger = get_logger()

#: Top-level configuration section that holds gateway settings.
GATEWAY_SECTION = "gateway"

#: Sub-section that holds per-frontend settings.
FRONTENDS_SECTION = "frontends"

#: Keys accepted inside one frontend's settings mapping.
_FRONTEND_KEYS: frozenset[str] = frozenset({"path", "enabled"})

#: Serialise writes made by this process.  Atomic replacement keeps readers
#: safe across processes; the lock only avoids two local writers racing.
_WRITE_LOCK = threading.Lock()

_JSON_SUFFIXES = frozenset({".json", ".jsonc"})
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


def _suffix(path: Path) -> str:
    """Return the lower-case suffix of *path*.

    Args:
        path: Configuration file path.

    Returns:
        The lower-case suffix, including the leading dot.
    """
    return path.suffix.lower()


def _read_jsonc(text: str) -> Any:
    """Parse JSONC text, with a plain-JSON fallback.

    Args:
        text: File content.

    Returns:
        The decoded value.

    Raises:
        ValueError: If the text is not valid JSON/JSONC.
    """
    try:
        from toolregistry._vendor.jsonx import loads as jsonc_loads
    except ImportError:  # pragma: no cover - core always vendors jsonx
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON configuration: {exc}") from exc
    try:
        return jsonc_loads(text)
    except Exception as exc:  # the vendored parser raises its own error type
        raise ValueError(f"invalid JSONC configuration: {exc}") from exc


def _read_yaml(text: str) -> Any:
    """Parse YAML text, with a PyYAML fallback.

    Args:
        text: File content.

    Returns:
        The decoded value.

    Raises:
        ValueError: If the text is not valid YAML.
    """
    try:
        from toolregistry._vendor.yaml import load as yaml_load
    except ImportError:  # pragma: no cover - core always vendors yaml
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ValueError(
                "YAML configuration requires the YAML parser shipped with toolregistry"
            ) from exc
        return yaml.safe_load(text)
    try:
        return yaml_load(text)
    except Exception as exc:  # the vendored parser raises its own error type
        raise ValueError(f"invalid YAML configuration: {exc}") from exc


def read_config_mapping(path: str | Path) -> dict[str, Any]:
    """Read a configuration file as a plain mapping.

    Unlike :func:`toolregistry_server.registry_builder.load_config`, this
    keeps every top-level key, including sections the core loader ignores.

    Args:
        path: Configuration file path.

    Returns:
        The decoded top-level mapping.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the extension is unsupported or the content is not a
            mapping.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    if _suffix(config_path) in _JSON_SUFFIXES:
        data = _read_jsonc(text)
    elif _suffix(config_path) in _YAML_SUFFIXES:
        data = _read_yaml(text)
    else:
        raise ValueError(
            f"Unsupported config file extension {config_path.suffix!r}; "
            "expected .json, .jsonc, .yaml, or .yml"
        )
    if not isinstance(data, dict):
        raise ValueError(
            "Config file must contain a mapping at the top level, "
            f"got {type(data).__name__}"
        )
    return data


def render_config_mapping(data: dict[str, Any], path: str | Path) -> str:
    """Serialise a configuration mapping in the file's format.

    Args:
        data: Top-level mapping.
        path: Destination path; its suffix selects JSON or YAML.

    Returns:
        The serialised text, ending with a newline.

    Raises:
        ValueError: If the extension is unsupported.
    """
    config_path = Path(path)
    suffix = _suffix(config_path)
    if suffix in _JSON_SUFFIXES:
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if suffix in _YAML_SUFFIXES:
        from toolregistry._vendor.yaml import dump as yaml_dump

        text = yaml_dump(data)
        return text if text.endswith("\n") else text + "\n"
    raise ValueError(
        f"Unsupported config file extension {config_path.suffix!r}; "
        "expected .json, .jsonc, .yaml, or .yml"
    )


def _gateway_section(data: dict[str, Any]) -> dict[str, Any]:
    """Return the gateway section, validating its shape.

    Args:
        data: Top-level configuration mapping.

    Returns:
        The gateway mapping.  A missing section yields an empty mapping.

    Raises:
        ValueError: If the section is not a mapping or holds unknown keys.
    """
    section = data.get(GATEWAY_SECTION)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError(
            f"'{GATEWAY_SECTION}' must be a mapping, got {type(section).__name__}"
        )
    unknown = sorted(set(section) - {FRONTENDS_SECTION})
    if unknown:
        raise ValueError(
            f"unrecognised key(s) under '{GATEWAY_SECTION}': "
            f"{', '.join(unknown)}; valid keys: {FRONTENDS_SECTION}"
        )
    return section


def load_frontend_settings(config_path: str | None) -> dict[str, dict[str, Any]]:
    """Read stored frontend settings from a configuration file.

    Args:
        config_path: Configuration file path, or ``None`` when the caller
            serves a pre-built registry.

    Returns:
        Mapping of frontend name to ``{"path": str, "enabled": bool}``.
        Frontends absent from the file are omitted.

    Raises:
        FileNotFoundError: If *config_path* is set and does not exist.
        ValueError: If the gateway section, a frontend name, a key, or a
            value is invalid.
    """
    if config_path is None:
        return {}
    data = read_config_mapping(config_path)
    section = _gateway_section(data)
    raw_frontends = section.get(FRONTENDS_SECTION)
    if raw_frontends is None:
        return {}
    if not isinstance(raw_frontends, dict):
        raise ValueError(
            f"'{GATEWAY_SECTION}.{FRONTENDS_SECTION}' must be a mapping, "
            f"got {type(raw_frontends).__name__}"
        )

    settings: dict[str, dict[str, Any]] = {}
    for name, entry in raw_frontends.items():
        if name not in FRONTEND_NAMES:
            raise ValueError(
                f"unknown frontend {name!r} in "
                f"'{GATEWAY_SECTION}.{FRONTENDS_SECTION}'; "
                f"expected one of {', '.join(FRONTEND_NAMES)}"
            )
        if not isinstance(entry, dict):
            raise ValueError(
                f"'{GATEWAY_SECTION}.{FRONTENDS_SECTION}.{name}' must be a "
                f"mapping, got {type(entry).__name__}"
            )
        unknown = sorted(set(entry) - _FRONTEND_KEYS)
        if unknown:
            raise ValueError(
                f"unrecognised key(s) under "
                f"'{GATEWAY_SECTION}.{FRONTENDS_SECTION}.{name}': "
                f"{', '.join(unknown)}; valid keys: "
                f"{', '.join(sorted(_FRONTEND_KEYS))}"
            )
        enabled = entry.get("enabled", DEFAULT_ENABLED[name])
        if not isinstance(enabled, bool):
            raise ValueError(
                f"'{GATEWAY_SECTION}.{FRONTENDS_SECTION}.{name}.enabled' "
                f"must be a boolean, got {type(enabled).__name__}"
            )
        settings[name] = {
            "path": normalize_path(entry.get("path", DEFAULT_PATHS[name])),
            "enabled": enabled,
        }
    return settings


def _write_config_mapping(data: dict[str, Any], path: Path) -> None:
    """Back up *path* and atomically replace it with *data*.

    Args:
        data: Top-level mapping to write.
        path: Destination configuration file.

    Raises:
        OSError: If the backup, temporary write, or replace fails.
    """
    text = render_config_mapping(data, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def persist_frontend_settings(
    config_path: str | None,
    *,
    name: str,
    path: str,
    enabled: bool,
    write_config: bool = True,
) -> tuple[bool, str | None]:
    """Store one frontend's settings in the configuration file.

    Args:
        config_path: Configuration file path, or ``None``.
        name: Frontend name.
        path: Current mount path.
        enabled: Current enable flag.
        write_config: When false, skip the write.  This backs
            ``--no-config-write``.

    Returns:
        ``(persisted, warning)``.  *persisted* is true only when the file was
        rewritten.  *warning* explains a skipped or failed write; on success
        it carries :data:`~toolregistry_server.source_manager.COMMENTS_WARNING`
        because file comments are not preserved.
    """
    if not write_config:
        return False, (
            "configuration writing is disabled (--no-config-write); "
            "the change applies to this process only"
        )
    if config_path is None:
        return False, (
            "no configuration file was provided; "
            "the change applies to this process only"
        )

    destination = Path(config_path)
    with _WRITE_LOCK:
        try:
            data = read_config_mapping(destination) if destination.exists() else {}
            section = data.setdefault(GATEWAY_SECTION, {})
            if not isinstance(section, dict):
                raise ValueError(
                    f"'{GATEWAY_SECTION}' must be a mapping, "
                    f"got {type(section).__name__}"
                )
            frontends = section.setdefault(FRONTENDS_SECTION, {})
            if not isinstance(frontends, dict):
                raise ValueError(
                    f"'{GATEWAY_SECTION}.{FRONTENDS_SECTION}' must be a "
                    f"mapping, got {type(frontends).__name__}"
                )
            frontends[name] = {"path": path, "enabled": enabled}
            _write_config_mapping(data, destination)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Could not persist gateway frontend settings",
                frontend=name,
                config=str(destination),
                error=str(exc),
            )
            return False, f"could not persist frontend settings: {exc}"
    logger.info(
        "Persisted gateway frontend settings",
        frontend=name,
        path=path,
        enabled=enabled,
        config=str(destination),
    )
    return True, COMMENTS_WARNING


__all__ = [
    "FRONTENDS_SECTION",
    "GATEWAY_SECTION",
    "load_frontend_settings",
    "persist_frontend_settings",
    "read_config_mapping",
    "render_config_mapping",
]
