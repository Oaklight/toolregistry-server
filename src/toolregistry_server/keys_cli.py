"""``toolregistry-server keys`` command.

A thin, headless wrapper over :class:`~toolregistry_server.keystore.ApiKeyStore`
for operators who manage keys from a terminal.  The admin panel (#187) uses
the same Python API in-process; this module adds no HTTP surface and no second
authentication path.

Usage:
    toolregistry-server keys --db keys.db create --label build-bot \\
        --namespace calculator --deny-tool calculator-dangerous
    toolregistry-server keys --db keys.db list --limit 20 --usage
    toolregistry-server keys --db keys.db get key_abc
    toolregistry-server keys --db keys.db rotate key_abc
    toolregistry-server keys --db keys.db update key_abc --label build-bot-v2 \\
        --expires 2027-01-01T00:00:00Z
    toolregistry-server keys --db keys.db revoke key_abc
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .keystore import ApiKeyStore, KeyUsage, ScopeSpec

__all__ = ["add_keys_subparser", "dispatch_keys"]


def add_keys_subparser(parser: argparse.ArgumentParser) -> None:
    """Register the ``keys`` command tree on *parser*.

    Args:
        parser: The parser created for the ``keys`` command.
    """
    parser.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help="Path to the API key SQLite database",
    )
    commands = parser.add_subparsers(dest="keys_command", required=True)

    create = commands.add_parser(
        "create", help="Create a key and print its secret once"
    )
    create.add_argument("--label", required=True, help="Human-readable label")
    create.add_argument(
        "--namespace",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Namespace to allow; may be repeated. '*' allows every namespace "
            "and 'prefix/*' allows a prefix"
        ),
    )
    create.add_argument(
        "--allow-tool",
        action="append",
        default=[],
        metavar="NAME",
        help="Allow one tool even outside the namespace list; may be repeated",
    )
    create.add_argument(
        "--deny-tool",
        action="append",
        default=[],
        metavar="NAME",
        help="Deny one tool inside an allowed namespace; may be repeated",
    )
    create.add_argument(
        "--expires-at",
        default=None,
        metavar="ISO8601",
        help="Expiry timestamp (UTC is assumed when no offset is given)",
    )
    create.add_argument(
        "--concurrency",
        type=int,
        default=None,
        metavar="N",
        help=(
            "In-flight call limit for this key (default: the server's "
            "--client-concurrency)"
        ),
    )
    create.add_argument(
        "--json",
        action="store_true",
        help="Print the record and secret as one JSON object",
    )

    listing = commands.add_parser("list", help="List keys ordered by last use")
    listing.add_argument("--limit", type=int, default=50, help="Page size (default 50)")
    listing.add_argument("--offset", type=int, default=0, help="Records to skip")
    listing.add_argument(
        "--usage",
        action="store_true",
        help="Include each key's recorded call, outcome and last-success counts",
    )
    listing.add_argument("--json", action="store_true", help="Print JSON")

    get = commands.add_parser("get", help="Show one key")
    get.add_argument("key_id", metavar="KEY_ID")
    get.add_argument("--json", action="store_true", help="Print JSON")

    rotate = commands.add_parser(
        "rotate", help="Issue a new secret for a key and print it once"
    )
    rotate.add_argument("key_id", metavar="KEY_ID")
    rotate.add_argument("--json", action="store_true", help="Print JSON")

    update = commands.add_parser(
        "update", help="Change a key's label, scopes, expiry or concurrency"
    )
    update.add_argument("key_id", metavar="KEY_ID")
    update.add_argument("--label", default=None, help="New human-readable label")
    update.add_argument(
        "--namespace",
        action="append",
        default=None,
        metavar="PATTERN",
        help=(
            "Replace the namespace allow-list; may be repeated. '*' allows "
            "every namespace and 'prefix/*' allows a prefix"
        ),
    )
    update.add_argument(
        "--allow-tool",
        action="append",
        default=None,
        metavar="NAME",
        help="Replace the tool overrides with an allow; may be repeated",
    )
    update.add_argument(
        "--deny-tool",
        action="append",
        default=None,
        metavar="NAME",
        help="Replace the tool overrides with a deny; may be repeated",
    )
    expiry = update.add_mutually_exclusive_group()
    expiry.add_argument(
        "--expires",
        default=None,
        metavar="ISO8601",
        help="New expiry timestamp (UTC is assumed when no offset is given)",
    )
    expiry.add_argument(
        "--clear-expiry",
        action="store_true",
        help="Remove the expiry so the key does not expire",
    )
    concurrency = update.add_mutually_exclusive_group()
    concurrency.add_argument(
        "--concurrency",
        type=int,
        default=None,
        metavar="N",
        help="New in-flight call limit for this key",
    )
    concurrency.add_argument(
        "--clear-concurrency",
        action="store_true",
        help="Drop the override and use the server's --client-concurrency",
    )
    update.add_argument("--json", action="store_true", help="Print JSON")

    revoke = commands.add_parser("revoke", help="Revoke one key immediately")
    revoke.add_argument("key_id", metavar="KEY_ID")
    revoke.add_argument("--json", action="store_true", help="Print JSON")

    concurrency = commands.add_parser(
        "set-concurrency", help="Change one key's in-flight call limit"
    )
    concurrency.add_argument("key_id", metavar="KEY_ID")
    limit = concurrency.add_mutually_exclusive_group(required=True)
    limit.add_argument("--value", type=int, metavar="N", help="New positive limit")
    limit.add_argument(
        "--clear",
        action="store_true",
        help="Drop the override and use the server's --client-concurrency",
    )
    concurrency.add_argument("--json", action="store_true", help="Print JSON")


def dispatch_keys(parsed: argparse.Namespace) -> int:
    """Run the selected ``keys`` subcommand.

    Args:
        parsed: The parsed CLI namespace.

    Returns:
        Process exit code: 0 on success, 1 when the key is unknown.

    Raises:
        ValueError: If the subcommand name is unknown.
    """
    handlers = {
        "create": _dispatch_create,
        "list": _dispatch_list,
        "get": _dispatch_get,
        "rotate": _dispatch_rotate,
        "update": _dispatch_update,
        "revoke": _dispatch_revoke,
        "set-concurrency": _dispatch_set_concurrency,
    }
    handler = handlers.get(parsed.keys_command)
    if handler is None:
        raise ValueError(f"unknown keys command: {parsed.keys_command}")
    return handler(parsed)


def _dispatch_create(parsed: argparse.Namespace) -> int:
    """Create one key and print its one-time secret."""
    scopes = ScopeSpec(
        tuple(parsed.namespace),
        {
            **dict.fromkeys(parsed.allow_tool, True),
            **dict.fromkeys(parsed.deny_tool, False),
        },
    )
    with ApiKeyStore(parsed.db) as store:
        try:
            created = store.create(
                parsed.label,
                scopes,
                expires_at=parsed.expires_at,
                max_concurrency=parsed.concurrency,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if parsed.json:
        print(json.dumps(created.to_dict(), sort_keys=True))
    else:
        _print_record(created.record.to_dict())
        print(f"Secret (shown once): {created.secret}")
    return 0


def _dispatch_list(parsed: argparse.Namespace) -> int:
    """Print a page of keys ordered by last use."""
    with ApiKeyStore(parsed.db) as store:
        records = [
            record.to_dict()
            for record in store.list(limit=parsed.limit, offset=parsed.offset)
        ]
        if parsed.usage:
            _attach_usage(store, records)
    if parsed.json:
        print(json.dumps(records, sort_keys=True))
        return 0
    if not records:
        print("No keys.")
    for record in records:
        _print_record(record)
        print()
    return 0


def _dispatch_get(parsed: argparse.Namespace) -> int:
    """Print one key with its usage, or exit 1 when it does not exist."""
    payload: dict[str, Any] | None = None
    with ApiKeyStore(parsed.db) as store:
        record = store.get(parsed.key_id)
        if record is not None:
            payload = {**record.to_dict(), "usage": store.usage(record.id).to_dict()}
    if payload is None:
        print(f"Key not found: {parsed.key_id}", file=sys.stderr)
        return 1
    if parsed.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        _print_record(payload)
    return 0


def _dispatch_rotate(parsed: argparse.Namespace) -> int:
    """Issue a new secret for one key and print it once."""
    with ApiKeyStore(parsed.db) as store:
        try:
            rotated = store.rotate(parsed.key_id)
        except KeyError:
            print(f"Key not found: {parsed.key_id}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if parsed.json:
        print(json.dumps(rotated.to_dict(), sort_keys=True))
    else:
        _print_record(rotated.record.to_dict())
        print(f"Secret (shown once): {rotated.secret}")
    return 0


def _dispatch_update(parsed: argparse.Namespace) -> int:
    """Change one key's label, scopes, expiry or concurrency."""
    changes: dict[str, Any] = {}
    if parsed.label is not None:
        changes["label"] = parsed.label
    if (
        parsed.namespace is not None
        or parsed.allow_tool is not None
        or parsed.deny_tool is not None
    ):
        changes["scopes"] = ScopeSpec(
            tuple(parsed.namespace or ()),
            {
                **dict.fromkeys(parsed.allow_tool or (), True),
                **dict.fromkeys(parsed.deny_tool or (), False),
            },
        )
    if parsed.clear_expiry:
        changes["expires_at"] = None
    elif parsed.expires is not None:
        changes["expires_at"] = parsed.expires
    if parsed.clear_concurrency:
        changes["max_concurrency"] = None
    elif parsed.concurrency is not None:
        changes["max_concurrency"] = parsed.concurrency
    if not changes:
        print(
            "Nothing to update; pass --label, --namespace, --allow-tool, "
            "--deny-tool, --expires, --clear-expiry, --concurrency or "
            "--clear-concurrency",
            file=sys.stderr,
        )
        return 2
    with ApiKeyStore(parsed.db) as store:
        try:
            record = store.update(parsed.key_id, **changes)
        except KeyError:
            print(f"Key not found: {parsed.key_id}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if parsed.json:
        print(json.dumps(record.to_dict(), sort_keys=True))
    else:
        _print_record(record.to_dict())
    return 0


def _dispatch_revoke(parsed: argparse.Namespace) -> int:
    """Revoke one key, or exit 1 when it does not exist."""
    with ApiKeyStore(parsed.db) as store:
        try:
            record = store.revoke(parsed.key_id)
        except KeyError:
            print(f"Key not found: {parsed.key_id}", file=sys.stderr)
            return 1
    if parsed.json:
        print(json.dumps(record.to_dict(), sort_keys=True))
    else:
        _print_record(record.to_dict())
    return 0


def _dispatch_set_concurrency(parsed: argparse.Namespace) -> int:
    """Set or clear one key's in-flight call limit."""
    value = None if parsed.clear else parsed.value
    with ApiKeyStore(parsed.db) as store:
        try:
            record = store.set_max_concurrency(parsed.key_id, value)
        except KeyError:
            print(f"Key not found: {parsed.key_id}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if parsed.json:
        print(json.dumps(record.to_dict(), sort_keys=True))
    else:
        _print_record(record.to_dict())
    return 0


def _attach_usage(store: ApiKeyStore, records: list[dict[str, Any]]) -> None:
    """Add a ``usage`` object to each record in place.

    A single ``usage_all`` query serves the page, so the CLI does not issue
    one query per key.  A key with no recorded call gets zeros.

    Args:
        store: The open key store.
        records: Record dictionaries from ``ApiKeyRecord.to_dict``.
    """
    usage = store.usage_all()
    for record in records:
        key_id = str(record.get("id"))
        key_usage = usage.get(key_id) or KeyUsage.empty(key_id)
        record["usage"] = key_usage.to_dict()


def _print_record(record: dict[str, Any]) -> None:
    """Print one record as ``field: value`` lines.

    Args:
        record: The output of :meth:`ApiKeyRecord.to_dict`.
    """
    scopes = record.get("scopes") or {}
    namespaces = ", ".join(scopes.get("namespaces") or []) or "-"
    tools = scopes.get("tools") or {}
    overrides = (
        ", ".join(
            f"{name}={'allow' if allowed else 'deny'}"
            for name, allowed in sorted(tools.items())
        )
        or "-"
    )
    print(f"id:          {record.get('id')}")
    print(f"label:       {record.get('label')}")
    print(f"namespaces:  {namespaces}")
    print(f"tool rules:  {overrides}")
    print(f"created:     {record.get('created_at')}")
    print(f"expires:     {record.get('expires_at') or '-'}")
    print(f"revoked:     {record.get('revoked_at') or '-'}")
    print(f"last used:   {record.get('last_used_at') or 'never'}")
    print(f"concurrency: {record.get('max_concurrency') or 'default'}")
    usage = record.get("usage")
    if isinstance(usage, dict):
        print(
            "calls:       {calls} (ok={ok} error={error} busy={busy} "
            "denied={denied})".format(
                calls=usage.get("calls", 0),
                ok=usage.get("ok", 0),
                error=usage.get("error", 0),
                busy=usage.get("busy", 0),
                denied=usage.get("denied", 0),
            )
        )
        print(f"last ok:     {usage.get('last_success_at') or 'never'}")
