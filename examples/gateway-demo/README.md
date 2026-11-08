# Gateway demo

One process, one origin, three frontends:

| Path | What it serves |
| --- | --- |
| `/openapi` | Tools over OpenAPI: `/tools`, `/tools/{namespace}/{tool}`, `/openapi.json`, `/docs` |
| `/mcp` | The same tools over MCP streamable HTTP |
| `/ext/gateway` | A small operator surface: client keys and frontend switches |

Both tool frontends sit behind one client-key check. The demo is a
self-contained prototype of the standalone gateway epic
([#183](https://github.com/Oaklight/toolregistry-server/issues/183)): it runs
on today's `toolregistry-server` and keeps the seams where the gateway
process ([#184](https://github.com/Oaklight/toolregistry-server/issues/184)),
runtime sources ([#185](https://github.com/Oaklight/toolregistry-server/issues/185))
and SQLite keys ([#186](https://github.com/Oaklight/toolregistry-server/issues/186))
will plug in.

This directory is an example, not a published package: the wheel and sdist
only include `src/toolregistry_server`.

## Quick start with Docker

Build from the repository root:

```bash
docker build -f examples/gateway-demo/Dockerfile -t gateway-demo:local .
docker run --rm -p 8000:8000 \
  -e GATEWAY_ADMIN_TOKEN=demo-admin \
  gateway-demo:local
```

The image contains `toolregistry-server` from this checkout and the three
local demo tools (`echo`, `add`, `server_time`). Run the scripted walkthrough
from another terminal:

```bash
examples/gateway-demo/walkthrough.sh http://127.0.0.1:8000 demo-admin
```

It checks health, an unauthenticated refusal, key creation, both tool
frontends, a frontend switch and key revocation, and prints `PASS`.

## Manual walkthrough

Create a key. The secret is returned once; only its SHA-256 digest is kept
in `keys.json`:

```bash
KEY=$(curl -s -X POST http://127.0.0.1:8000/ext/gateway/keys \
  -H 'Authorization: Bearer demo-admin' \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo","scopes":["default"]}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
```

List tools over OpenAPI:

```bash
curl -s -H "Authorization: Bearer $KEY" \
  http://127.0.0.1:8000/openapi/tools
```

Call a tool:

```bash
curl -s -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"text":"hello"}' \
  http://127.0.0.1:8000/openapi/tools/default/echo
# "hello"
```

List tools over MCP streamable HTTP. The first request initializes the
session; later requests carry `Mcp-Session-Id`:

```bash
curl -s -D /tmp/mcp.h -o /tmp/init.json \
  -X POST http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'

SID=$(awk 'BEGIN{IGNORECASE=1} /^mcp-session-id:/ {print $2}' /tmp/mcp.h | tr -d '\r')

curl -s -X POST http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Disable the OpenAPI frontend and confirm the mount answers 404 (the MCP
frontend keeps working):

```bash
curl -s -X PUT http://127.0.0.1:8000/ext/gateway/frontends/openapi \
  -H 'Authorization: Bearer demo-admin' \
  -H 'Content-Type: application/json' -d '{"enabled":false}'

curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $KEY" http://127.0.0.1:8000/openapi/tools
# 404
```

Re-enable it with `{"enabled":true}`. Revoking the key makes both frontends
answer 401:

```bash
curl -s -X DELETE -H 'Authorization: Bearer demo-admin' \
  http://127.0.0.1:8000/ext/gateway/keys/demo
```

## Run without Docker

```bash
pip install -e ".[all]"
cd examples/gateway-demo
GATEWAY_ADMIN_TOKEN=demo-admin GATEWAY_TOOL_SOURCE=local \
  PYTHONPATH=. python -m gateway_demo
```

## Tool source

`GATEWAY_TOOL_SOURCE` selects where tools come from:

| Value | Behaviour |
| --- | --- |
| `auto` (default) | Use [`toolregistry-hub`](https://pypi.org/project/toolregistry-hub/) tools when importable, otherwise the local tools |
| `hub` | Require `toolregistry-hub`; fail at startup if it is missing |
| `local` | Always use `echo`, `add` and `server_time` |

`pip install toolregistry-hub` inside the environment and run with
`GATEWAY_TOOL_SOURCE=hub` to see the hub's tool set in place of the three
local tools. Tool names then follow the hub namespaces, so adjust the
`/openapi/tools/...` paths in the walkthrough.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GATEWAY_ADMIN_TOKEN` | unset | Operator token for `/ext/gateway`. Without it the admin surface answers 503 |
| `GATEWAY_KEYS_FILE` | `keys.json` | JSON file holding key digests |
| `GATEWAY_TOOL_SOURCE` | `auto` | `auto`, `hub` or `local` |
| `GATEWAY_HOST` / `GATEWAY_PORT` | `0.0.0.0` / `8000` | Bind address |
| `GATEWAY_LOG_LEVEL` | `info` | Uvicorn log level |

## What the demo stands in for

- **Frontends** — `/openapi` and `/mcp` are mounted in this script. The
  gateway process (#184) owns the same paths, plus `/sse`, with one server
  and a route-level switch; `FrontendState` is the placeholder for that
  switch.
- **Tools** — `build_registry()` loads hub tools or three local ones. The
  runtime source manager (#185) replaces it with sources an operator adds
  without a restart.
- **Keys** — `FileKeyStore` hashes secrets, shows them once, and supports
  revocation. The digest here is an unsalted SHA-256; #186 stores keys with
  a password-style KDF (scrypt), so this file is not the scheme to copy.
  Scopes are recorded but not yet enforced; expiry, `last_used` and SQLite
  storage also arrive with #186.
- **Admin** — `/ext/gateway` is a plain FastAPI router. The admin extension
  contract (#187) replaces it with pages inside the core admin panel.

The demo is single-operator and single-process. It is not a template for a
public deployment: the operator token is static, there is no TLS and no
rate limiting.
