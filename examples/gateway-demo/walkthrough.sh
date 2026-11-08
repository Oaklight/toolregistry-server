#!/usr/bin/env bash
# End-to-end check for the gateway demo.
#
# Usage:
#   ./walkthrough.sh [BASE_URL] [ADMIN_TOKEN]
#
# Defaults: http://127.0.0.1:8000 and demo-admin. Requires curl and python3.
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
ADMIN="${2:-demo-admin}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

json_field() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

echo "1. health"
code="$(curl -s -o "$TMP/health.json" -w '%{http_code}' "$BASE/health")"
[ "$code" = 200 ] || fail "health returned $code"
grep -q '"status":"ok"' "$TMP/health.json" || fail "health body: $(cat "$TMP/health.json")"

echo "2. an unauthenticated tool call is refused"
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/openapi/tools")"
[ "$code" = 401 ] || fail "unauthenticated list returned $code"

echo "3. create a client key"
curl -sf -X POST "$BASE/ext/gateway/keys" \
  -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"walkthrough","scopes":["default"]}' > "$TMP/key.json"
KEY="$(json_field key < "$TMP/key.json")"
[ -n "$KEY" ] || fail "no key in response"

echo "4. list tools over OpenAPI"
curl -sf -H "Authorization: Bearer $KEY" "$BASE/openapi/tools" > "$TMP/tools.json"
grep -q '"name":"echo"' "$TMP/tools.json" || fail "echo tool missing"

echo "5. call a tool over OpenAPI"
out="$(curl -sf -X POST "$BASE/openapi/tools/default/echo" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"text":"hello"}')"
[ "$out" = '"hello"' ] || fail "echo returned $out"

echo "6. list tools over MCP"
curl -s -D "$TMP/mcp.h" -o "$TMP/init.json" -X POST "$BASE/mcp" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"walkthrough","version":"0"}}}'
SID="$(awk 'BEGIN{IGNORECASE=1} /^mcp-session-id:/ {print $2}' "$TMP/mcp.h" | tr -d '\r')"
[ -n "$SID" ] || fail "no Mcp-Session-Id header"
curl -sf -X POST "$BASE/mcp" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' > "$TMP/mcp-tools.json"
grep -q '"name":"echo"' "$TMP/mcp-tools.json" || fail "echo tool missing over MCP"

echo "7. disable the OpenAPI frontend and expect 404"
curl -sf -X PUT "$BASE/ext/gateway/frontends/openapi" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"enabled":false}' > /dev/null
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" "$BASE/openapi/tools")"
[ "$code" = 404 ] || fail "disabled frontend returned $code"

echo "8. re-enable the frontend"
curl -sf -X PUT "$BASE/ext/gateway/frontends/openapi" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"enabled":true}' > /dev/null
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" "$BASE/openapi/tools")"
[ "$code" = 200 ] || fail "re-enabled frontend returned $code"

echo "9. revoke the key and expect 401"
curl -sf -X DELETE "$BASE/ext/gateway/keys/walkthrough" \
  -H "Authorization: Bearer $ADMIN" > /dev/null
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" "$BASE/openapi/tools")"
[ "$code" = 401 ] || fail "revoked key returned $code"

echo "PASS"
