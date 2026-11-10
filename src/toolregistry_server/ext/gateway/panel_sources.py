"""Source inspection and connection tests for the gateway panel (#250).

The extension route surface is here rather than in ``extension.py`` so the
route handlers stay one statement each and the protocol work is testable on
its own.

Three rules shape the declarations this module returns:

* a secret never leaves the server.  ``headers``/``env`` values, an OpenAPI
  ``auth.token`` and URL userinfo (``user:password@`` in ``url`` or
  ``base_url``) are absent from the response; only the *names* of the
  secret-bearing fields are listed under ``secret_fields``, so the edit form
  can ask for them again without showing the value it already stores;
* a blank secret field in an edit keeps the stored value.  The form does not
  receive the value, so treating an absent field as "delete it" would erase a
  credential on every save.  A URL is restored when the submitted value is
  the redacted form the form was given, or when the field is absent;
* a stdio ``command`` is kept verbatim.  Its arguments are not treated as
  secret: they are the only way to edit the declaration, and a credential
  belongs in ``env``, whose values are withheld.  This is a deliberate
  trade-off, not an oversight.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from ...source_manager import _redact_url_userinfo

#: Seconds a connection test may take before it is reported as timed out.
DEFAULT_TEST_TIMEOUT = 20.0

#: HTTP methods an OpenAPI path item may declare.
_HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)

#: Tags that make the call tester ask for a stronger confirmation.
DANGEROUS_TAGS = frozenset({"destructive", "privileged"})

#: Declaration fields that hold a URL and can therefore carry userinfo.
_URL_FIELDS = ("url", "base_url")


class UnsupportedTest(ValueError):
    """The source kind has no connection test."""


def _as_dict(source: Any) -> dict[str, Any]:
    """Return a declaration as a plain mapping.

    Args:
        source: A core ``ToolSource`` (which serialises through ``to_dict``)
            or a plain mapping.

    Returns:
        A shallow copy of the declaration.
    """
    if hasattr(source, "to_dict"):
        return dict(source.to_dict())
    if isinstance(source, Mapping):
        return dict(source)
    raise TypeError(f"cannot serialise a declaration of type {type(source).__name__}")


def _redact_urls(data: dict[str, Any], secret_fields: list[str]) -> None:
    """Remove URL userinfo from *data* and mark each changed field secret.

    A URL such as ``https://user:pass@host/mcp`` embeds a credential.  The
    redacted form keeps the target editable; the marker tells the edit form
    that the stored value was withheld.

    Args:
        data: The declaration to mutate in place.
        secret_fields: The marker list to append to.
    """
    for field in _URL_FIELDS:
        value = data.get(field)
        if not isinstance(value, str):
            continue
        redacted = _redact_url_userinfo(value)
        if redacted != value:
            data[field] = redacted
            secret_fields.append(field)


def public_declaration(source: Any) -> dict[str, Any]:
    """Return a declaration with every secret value removed.

    Args:
        source: The stored declaration.

    Returns:
        The config-file form with ``headers``, ``env`` values, ``auth.token``
        and URL userinfo absent and their names listed under
        ``secret_fields``.  A stdio ``command`` is kept: it is the only way
        to edit the declaration, and its arguments are a documented
        trade-off (see the module docstring).
    """
    data = _as_dict(source)
    secret_fields: list[str] = []

    headers = data.pop("headers", None)
    if isinstance(headers, Mapping):
        secret_fields.extend(f"headers.{name}" for name in headers)

    env = data.pop("env", None)
    if isinstance(env, Mapping):
        secret_fields.extend(f"env.{name}" for name in env)

    # A Python source's constructor kwargs routinely hold API keys, so they
    # are write-only for the same reason.
    kwargs = data.pop("kwargs", None)
    if isinstance(kwargs, Mapping):
        secret_fields.extend(f"kwargs.{name}" for name in kwargs)

    _redact_urls(data, secret_fields)

    # ``_as_dict`` is a shallow copy, so the nested ``auth`` mapping is the
    # stored object's; copy it before removing the token.
    auth = data.get("auth")
    listed_token = None
    if isinstance(auth, Mapping):
        auth = dict(auth)
        data["auth"] = auth
        listed_token = auth.pop("token", None)
    if listed_token or getattr(getattr(source, "auth", None), "token", None):
        # ``AuthConfig.to_dict`` already omits the token, so the declaration
        # cannot tell the form that one is stored; the object can.
        secret_fields.append("auth.token")

    if secret_fields:
        data["secret_fields"] = secret_fields
    return data


def _stored_secrets(source: Any) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """Return the stored ``headers`` and ``env`` of *source*."""
    return (
        dict(getattr(source, "headers", None) or {}) or None,
        dict(getattr(source, "env", None) or {}) or None,
    )


def _merge_auth(payload: dict[str, Any], source: Any) -> None:
    """Keep an OpenAPI token the edit form could not show."""
    stored = getattr(source, "auth", None)
    if stored is None:
        return
    auth = payload.get("auth")
    if auth is None:
        payload["auth"] = {
            "type": getattr(stored, "type", "bearer"),
            "header_name": getattr(stored, "header_name", "Authorization"),
            "token": getattr(stored, "token", None),
            "token_env": getattr(stored, "token_env", None),
        }
    elif isinstance(auth, Mapping) and not auth.get("token"):
        merged = dict(auth)
        merged.setdefault("type", getattr(stored, "type", "bearer"))
        merged.setdefault(
            "header_name", getattr(stored, "header_name", "Authorization")
        )
        merged["token"] = getattr(stored, "token", None)
        payload["auth"] = merged


def _restore_urls(payload: dict[str, Any], source: Any) -> None:
    """Restore a stored URL whose userinfo the form could not show.

    The edit form receives the redacted URL and submits it back unchanged,
    and it does not render every URL field at all.  In both cases the stored
    URL, userinfo included, is what the operator means.  A different
    submitted value wins.

    Args:
        payload: The operator's declaration, mutated in place.
        source: The stored declaration being replaced.
    """
    stored = _as_dict(source)
    for field in _URL_FIELDS:
        stored_value = stored.get(field)
        if not isinstance(stored_value, str):
            continue
        redacted = _redact_url_userinfo(stored_value)
        if redacted == stored_value:
            continue
        submitted = payload.get(field)
        if submitted is None or submitted == redacted:
            payload[field] = stored_value


def merge_secrets(payload: dict[str, Any], source: Any) -> dict[str, Any]:
    """Carry stored secrets into an edit that left them blank.

    Args:
        payload: The operator's declaration, decoded from the request body.
        source: The stored declaration being replaced.

    Returns:
        *payload* with the stored ``headers``/``env`` (MCP), ``auth``
        (OpenAPI) or URL userinfo restored.  ``clear_secrets: true`` removes
        every stored secret instead, which is how an operator deletes one.
    """
    if payload.get("clear_secrets"):
        payload.pop("clear_secrets", None)
        payload.pop("headers", None)
        payload.pop("env", None)
        payload.pop("kwargs", None)
        if isinstance(payload.get("auth"), Mapping):
            payload["auth"] = {
                key: value for key, value in payload["auth"].items() if key != "token"
            }
        return payload
    payload.pop("clear_secrets", None)
    _restore_urls(payload, source)

    kind = payload.get("type") or payload.get("kind")
    if kind == "openapi":
        _merge_auth(payload, source)
        return payload
    if kind == "python":
        stored_kwargs = dict(getattr(source, "kwargs", None) or {})
        if stored_kwargs:
            payload["kwargs"] = {**stored_kwargs, **(payload.get("kwargs") or {})}
        return payload
    if kind != "mcp":
        return payload

    headers, env = _stored_secrets(source)
    if headers:
        # A header the operator set wins; every other stored header stays.
        payload["headers"] = {**headers, **(payload.get("headers") or {})}
    if env:
        payload["env"] = {**env, **(payload.get("env") or {})}
    return payload


def mcp_auth_to_headers(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate the form's ``auth`` field into MCP ``headers``.

    The config file stores an MCP credential as a header, so the panel writes
    it there too: a bearer token becomes ``Authorization: Bearer <token>``
    and a custom header stores the token under its own name.

    Args:
        payload: An MCP declaration from the request body.

    Returns:
        *payload* with ``auth`` replaced by the equivalent ``headers`` entry.

    Raises:
        ValueError: If the auth block is malformed.
    """
    if "auth" not in payload:
        return payload
    auth = payload.pop("auth")
    if auth is None:
        return payload
    if not isinstance(auth, Mapping):
        raise ValueError("'auth' must be an object")
    unknown = sorted(set(auth) - {"type", "token", "header_name"})
    if unknown:
        raise ValueError(f"'auth' has unknown keys: {', '.join(map(str, unknown))}")
    auth_type = auth.get("type", "bearer")
    if auth_type not in ("bearer", "header"):
        raise ValueError("'auth.type' must be 'bearer' or 'header'")
    token = auth.get("token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("'auth.token' must be a non-empty string")
    if auth_type == "bearer":
        name, value = "Authorization", f"Bearer {token.strip()}"
    else:
        name = auth.get("header_name", "X-API-Key")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("'auth.header_name' must be a non-empty string")
        name, value = name.strip(), token.strip()

    headers = dict(payload.get("headers") or {})
    if name in headers:
        raise ValueError(
            f"'headers' already sets {name!r}; pass the credential in one place"
        )
    headers[name] = value
    payload["headers"] = headers
    return payload


def source_tools(entry: Any, registry: Any) -> list[dict[str, Any]]:
    """Return the tools a source registered, with a short description.

    Args:
        entry: The source entry from the manager.
        registry: The live registry, used for the description and the
            enabled flag.

    Returns:
        One ``{"name", "description", "enabled"}`` record per tool name,
        sorted by name.  A name the registry no longer holds is reported
        disabled: it cannot be called, whatever its stored entry says.
    """
    records: list[dict[str, Any]] = []
    for name in sorted(getattr(entry, "names", ()) or ()):
        tool = registry.get_tool(name) if registry is not None else None
        description = getattr(tool, "description", "") or ""
        enabled = (
            registry is not None and tool is not None and registry.is_enabled(name)
        )
        records.append(
            {
                "name": name,
                "description": description,
                "enabled": enabled,
            }
        )
    return records


def _count_operations(spec: Mapping[str, Any]) -> int:
    """Return the number of HTTP operations an OpenAPI document declares."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        return 0
    total = 0
    for path_item in paths.values():
        if not isinstance(path_item, Mapping):
            continue
        total += sum(1 for method in path_item if str(method).lower() in _HTTP_METHODS)
    return total


def _server_label(source: Any) -> str:
    """Return an MCP server's name, version or both, as one string."""
    name = getattr(source, "name", None) or ""
    version = getattr(source, "version", None) or ""
    if name and version:
        return f"{name} {version}"
    return str(name or version or "")


def _error_fields(exc: BaseException) -> dict[str, Any]:
    """Return the verbatim, credential-redacted error fields of a failed test."""
    return {
        "ok": False,
        "error": _redact_url_userinfo(str(exc)),
        "error_type": type(exc).__qualname__,
    }


def _mcp_target(source: Any) -> Any:
    """Return the ``MCPClient`` source argument for *source*."""
    if getattr(source, "transport", None) == "stdio":
        command = tuple(getattr(source, "command", ()) or ())
        if not command:
            raise ValueError("an mcp stdio source has no command to run")
        return {
            "command": command[0],
            "args": list(command[1:]),
            "env": dict(getattr(source, "env", None) or {}) or None,
        }
    url = getattr(source, "url", None)
    if not url:
        raise ValueError("an mcp source has no url to connect to")
    return url


async def _probe_mcp(
    source: Any, *, allow_private_hosts: bool, timeout: float
) -> dict[str, Any]:
    """Open an MCP session, list its tools and close it.

    Nothing is registered: the client is a throwaway session bound to the
    declaration under test.
    """
    from toolregistry.integrations.mcp.client import MCPClient

    target = _mcp_target(source)
    headers = dict(getattr(source, "headers", None) or {}) or None

    async def _run() -> tuple[list[Any], Any]:
        client = MCPClient(
            target, headers=headers, allow_private_hosts=allow_private_hosts
        )
        async with client:
            tools = await client.list_tools()
            return tools, client.server_info

    tools, info = await asyncio.wait_for(_run(), timeout)
    records = [
        {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", "") or "",
        }
        for tool in tools
    ]
    name, version = _server_identity(info)
    return {
        "ok": True,
        "kind": "mcp",
        "tool_count": len(records),
        "server_name": name,
        "server_version": version,
        "tools": records,
        "error": None,
        "error_type": None,
    }


def _server_identity(info: Any) -> tuple[Any, Any]:
    """Return an MCP ``Implementation``'s name and version, or two ``None``.

    The mcp SDK reports the field as ``name``/``version`` on both v1 and v2,
    but an older or shimmed client may hand back a mapping instead.
    """
    if info is None:
        return None, None
    if isinstance(info, Mapping):
        return info.get("name"), info.get("version")
    return getattr(info, "name", None), getattr(info, "version", None)


def _probe_openapi(source: Any, *, allow_private_hosts: bool) -> dict[str, Any]:
    """Fetch and parse an OpenAPI document without registering its tools.

    Registration passes ``allow_local_files=True``: the source declaration
    comes from the operator's own configuration, the same file that can
    import Python modules and run stdio commands.  The test mirrors that, so
    a local spec path reports the same result a restart would produce.
    """
    from toolregistry._async_runtime import AsyncRuntime
    from toolregistry.integrations.openapi.utils import load_openapi_spec_async

    spec = AsyncRuntime.run_sync(
        load_openapi_spec_async(
            getattr(source, "url", ""),
            allow_local_files=True,
            allow_private_hosts=allow_private_hosts,
            follow_redirects=False,
        )
    )
    info = spec.get("info") if isinstance(spec, Mapping) else None
    info = info if isinstance(info, Mapping) else {}
    operations = _count_operations(spec)
    return {
        "ok": True,
        "kind": "openapi",
        "tool_count": operations,
        "server_name": info.get("title"),
        "server_version": info.get("version"),
        "tools": [],
        "error": None,
        "error_type": None,
    }


def test_connection(
    source: Any,
    *,
    allow_private_hosts: bool = False,
    timeout: float = DEFAULT_TEST_TIMEOUT,
) -> dict[str, Any]:
    """Test a source declaration without registering it.

    Args:
        source: The declaration to test.
        allow_private_hosts: Permit the connection to reach a private or
            loopback address.
        timeout: Seconds an MCP handshake may take.

    Returns:
        ``ok``, ``kind``, ``tool_count``, ``server_name``,
        ``server_version``, ``tools`` and, on failure, the verbatim
        redacted ``error`` with its ``error_type``.

    Raises:
        UnsupportedTest: For a source kind with no connection test.
    """
    kind = _kind_name(type(source).__name__)
    try:
        if kind == "mcp":
            return _run_coroutine(
                _probe_mcp(
                    source, allow_private_hosts=allow_private_hosts, timeout=timeout
                )
            )
        if kind == "openapi":
            return _probe_openapi(source, allow_private_hosts=allow_private_hosts)
    except Exception as exc:  # noqa: BLE001 - reported verbatim to the operator
        return {"kind": kind, **_error_fields(exc)}
    raise UnsupportedTest(f"a {kind} source has no connection test")


def _kind_name(class_name: str) -> str:
    """Return the lower-case source kind for a declaration class name."""
    if class_name.endswith("Source"):
        class_name = class_name[: -len("Source")]
    return class_name.lower()


def _run_coroutine(coroutine: Any) -> Any:
    """Run *coroutine* on core's shared event loop.

    The panel handler runs in a worker thread, so it may block here.  Core's
    helper is used rather than :func:`asyncio.run` to keep one persistent
    loop, the same one MCP registration uses.
    """
    from toolregistry._async_runtime import AsyncRuntime

    return AsyncRuntime.run_sync(coroutine)


__all__ = [
    "DANGEROUS_TAGS",
    "DEFAULT_TEST_TIMEOUT",
    "UnsupportedTest",
    "mcp_auth_to_headers",
    "merge_secrets",
    "public_declaration",
    "source_tools",
    "test_connection",
]
