# Local Hub

A local hub is one resident gateway that serves many MCP clients on the same
machine. Without it, every client that launches a stdio server starts its own
process with its own registry, tool schemas and caches; with it, the registry
is built once and each client keeps only a thin stdio shim.

## Why

- **One registry.** Tool sources are registered once, so every session sees
  the same tools with the same schemas.
- **One set of caches and connections.** OpenAPI documents, MCP sessions and
  tool state are shared instead of rebuilt per client.
- **Restarts do not disconnect clients.** The shim answers `initialize`
  locally and reconnects in the background when the hub comes back.

## Quick start

Start the hub once, in the foreground or detached:

```bash
# Detached: starts the hub if none is running, prints its endpoint
toolregistry-server local-hub ensure --config tools.yaml

# Foreground, for a terminal you keep open
toolregistry-server local-hub run --config tools.yaml
```

Point a stdio-only client at the shim:

```json
{
  "mcpServers": {
    "toolregistry": {
      "command": "toolregistry-server",
      "args": ["hub-shim", "--name", "default", "--config", "/path/to/tools.yaml"]
    }
  }
}
```

`--config` on the shim is only used when the shim has to start the hub; without
it the shim waits for a hub that an operator starts, and `--no-spawn` forbids
starting one entirely.

## Lifecycle

```bash
toolregistry-server local-hub status --name default   # exit 0 running, 3 not
toolregistry-server local-hub stop   --name default   # exit 3 if none runs
```

`run` serves in the foreground; `ensure` adopts a live hub or starts a
detached one and prints its endpoint; `status` reports it; `stop` signals the
owner and waits for it to exit. All four accept `--name`, so differently named
hubs coexist on one machine.

Ownership is an exclusive lock, and the owner pid is published next to the
endpoint document. `stop` signals only when the lock is held, the pid file and
the endpoint agree on one process, and the published address accepts a
connection; a document left behind by a crashed hub therefore cannot make it
signal an unrelated process. `status` is a pure read: it never deletes the
documents it inspects.

## State directory

The state root is chosen in this order:

1. `$TOOLREGISTRY_HOME`, when set (it names the root, not the `hub` directory).
2. `$XDG_RUNTIME_DIR/toolregistry` on POSIX.
3. `~/.local/state/toolregistry`.
4. `%LOCALAPPDATA%\toolregistry` on Windows.

Under it, `hub/` holds `<name>.json` (endpoint document), `<name>.pid` (owner),
`<name>.lock` (exclusive lock), `<name>.sock` (default Unix socket) and
`<name>.log` (detached hub output). The directory is `0700` and every file is
`0600`. Readers refuse a document that is a symlink, is not a regular file, is
owned by another user, or is readable by group or others.

## Connecting a client

- `initialize` is answered by the shim itself, so a client starts even while
  the hub is down. `tools/list` is empty in that state.
- A tool call without a hub fails once with MCP error `-32000`
  (`hub '<name>' is unavailable`) and schedules a background reconnect; the
  next call after the reconnect succeeds.
- The shim reconnects with 0.25 s doubling to a 5 s ceiling, and caps one
  connect + initialize attempt at `--connect-timeout` (5 s by default).
- When the hub's tool list differs from the last one the client was served,
  the client receives `notifications/tools/list_changed` and can refresh. The
  comparison covers names, descriptions and input schemas; an unchanged list
  is silent.
- A document whose address accepts no connection is treated as "no hub", not
  as an error, so a stale document never blocks a start.

## Keys and isolation

`local-hub` requires `--keys-db`: a shared hub serves many clients, so each
one presents its own client key, and the hub refuses a static operator token
on any MCP request (including `tools/list`) unless it is started with
`--allow-operator-token`. Create one key per client and hand it to that
client's shim:

```bash
toolregistry-server keys create --label laptop --keys-db hub-keys.db   # printed once
```

```json
{
  "mcpServers": {
    "toolregistry": {
      "command": "toolregistry-server",
      "args": ["hub-shim", "--name", "default", "--config", "/path/to/tools.yaml",
               "--key-file", "/home/me/.config/toolregistry/hub.key"]
    }
  }
}
```

`--key-file` (or `TOOLREGISTRY_HUB_KEY`) carries the client key; `--keys-db`
is needed only when the shim has to start the hub itself. Each call is
counted per key: `--client-concurrency` caps in-flight calls, and a key can
narrow that with `max_concurrency` but never raise it. A request that claims
an identity, working directory or environment in a header (`x-client-id`,
`x-client-identity`, `x-client-cwd`, `x-client-env`, `x-client-environment`,
`x-forwarded-user`, `x-remote-user`) is refused with `400`, so strip any such
header at a proxy in front of the hub. The panel's Clients tab and
`/ext/gateway/clients` show the live per-client counters.

## Limits

- `local-hub` and `hub-shim` are POSIX-only for now. On Windows the commands
  refuse and point at `gateway`: byte-range locks are mandatory there and a
  terminated process cannot retire its endpoint, and no Windows CI verifies
  the lifecycle.
- `--idle-timeout` defaults to `0`, so a hub stays resident until `stop`.
  A non-zero value exits after that many seconds without a request or an open
  MCP/SSE session.
- Client identity and tool scopes come from the inbound API keys on the hub,
  not from the shim; see [Authentication](authentication.md). A client that
  connects directly with streamable HTTP gets the same scope enforcement.
