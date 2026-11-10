"""Read, render and atomically write the tool configuration file.

This module is deliberately free of gateway imports.  Runtime source
management rewrites the configuration in a base install, where the gateway
extra (FastAPI, uvicorn) is not installed; importing
:mod:`toolregistry_server.gateway.persistence` from there would pull the whole
gateway package in.

The writer keeps every top-level section it does not own, so a source edit
preserves ``gateway`` and ``oauth`` settings.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

__all__ = [
    "read_config_mapping",
    "render_config_mapping",
    "write_config_mapping",
]

#: Serialise writes made by this process.  Atomic replacement keeps readers
#: safe across processes; the lock only avoids two local writers racing.
#: Re-entrant so a caller can hold it across a read-modify-write and call
#: :func:`write_config_mapping` inside.
_WRITE_LOCK = threading.RLock()

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


def write_config_mapping(data: dict[str, Any], path: str | Path) -> None:
    """Back up *path* and atomically replace it with *data*.

    Args:
        data: Top-level mapping to write.
        path: Destination configuration file.

    Raises:
        OSError: If the backup, temporary write, or replace fails.
    """
    with _WRITE_LOCK:
        _write_config_mapping(data, Path(path))


def _write_config_mapping(data: dict[str, Any], path: Path) -> None:
    """Back up *path* and atomically replace it with *data*.

    The caller owns :data:`_WRITE_LOCK`.

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
