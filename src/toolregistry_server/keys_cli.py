"""``toolregistry-server keys`` command.

A thin, headless wrapper over :class:`~toolregistry_server.keystore.ApiKeyStore`
for operators who manage keys from a terminal.  The admin panel (#187) uses
the same Python API in-process; this module adds no HTTP surface and no second
authentication path.

Usage:
    toolregistry-server keys --db keys.db create --label build-bot \\
        --namespace calculator --deny-tool calculator-dangerous
    toolregistry-server keys --db keys.db list --limit 20
    toolregistry-server keys --db keys.db get key_abc
    toolregistry-server keys --db keys.db revoke key_abc
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .keystore import ApiKeyStore, ScopeSpec

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
        "--json",
        action="store_true",
        help="Print the record and secret as one JSON object",
    )

    listing = commands.add_parser("list", help="List keys ordered by last use")
    listing.add_argument("--limit", type=int, default=50, help="Page size (default 50)")
    listing.add_argument("--offset", type=int, default=0, help="Records to skip")
    listing.add_argument("--json", action="store_true", help="Print JSON")

    get = commands.add_parser("get", help="Show one key")
    get.add_argument("key_id", metavar="KEY_ID")
    get.add_argument("--json", action="store_true", help="Print JSON")

    revoke = commands.add_parser("revoke", help="Revoke one key immediately")
    revoke.add_argument("key_id", metavar="KEY_ID")
    revoke.add_argument("--json", action="store_true", help="Print JSON")


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
        "revoke": _dispatch_revoke,
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
        created = store.create(parsed.label, scopes, expires_at=parsed.expires_at)
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
    """Print one key, or exit 1 when it does not exist."""
    with ApiKeyStore(parsed.db) as store:
        record = store.get(parsed.key_id)
    if record is None:
        print(f"Key not found: {parsed.key_id}", file=sys.stderr)
        return 1
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
