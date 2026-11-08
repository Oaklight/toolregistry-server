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

# Print the tools/list response for an initialized MCP session.
mcp_tools() {
  local key="$1"
  local headers="$TMP/mcp.h"
  curl -s -D "$headers" -o "$TMP/init.json" -X POST "$BASE/mcp" \
    -H "Authorization: Bearer $key" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"walkthrough","version":"0"}}}'
  local sid
  sid="$(awk 'BEGIN{IGNORECASE=1} /^mcp-session-id:/ {print $2}' "$headers" | tr -d '\r')"
  [ -n "$sid" ] || fail "no Mcp-Session-Id header"
  curl -sf -X POST "$BASE/mcp" \
    -H "Authorization: Bearer $key" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -H "Mcp-Session-Id: $sid" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
}

echo "1. health"
code="$(curl -s -o "$TMP/health.json" -w '%{http_code}' "$BASE/health")"
[ "$code" = 200 ] || fail "health returned $code"
grep -q '"status":"ok"' "$TMP/health.json" || fail "health body: $(cat "$TMP/health.json")"

echo "2. an unauthenticated tool call is refused"
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/openapi/tools")"
[ "$code" = 401 ] || fail "unauthenticated list returned $code"

echo "3. a non-ASCII operator token is a mismatch, not a crash"
code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer démo' "$BASE/ext/gateway/keys")"
[ "$code" = 401 ] || fail "non-ASCII token returned $code"

echo "4. create a client key"
curl -sf -X POST "$BASE/ext/gateway/keys" \
  -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"walkthrough","scopes":["default"]}' > "$TMP/key.json"
KEY="$(json_field key < "$TMP/key.json")"
[ -n "$KEY" ] || fail "no key in response"

echo "5. a blank key name is rejected with 422"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/ext/gateway/keys" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"name":"   "}')"
[ "$code" = 422 ] || fail "blank key name returned $code"

echo "6. list tools over OpenAPI"
curl -sf -H "Authorization: Bearer $KEY" "$BASE/openapi/tools" > "$TMP/tools.json"
grep -q '"name":"echo"' "$TMP/tools.json" || fail "echo tool missing"

echo "7. call a tool over OpenAPI"
out="$(curl -sf -X POST "$BASE/openapi/tools/default/echo" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"text":"hello"}')"
[ "$out" = '"hello"' ] || fail "echo returned $out"

echo "8. list tools over MCP"
mcp_tools "$KEY" > "$TMP/mcp-tools.json"
grep -q '"name":"echo"' "$TMP/mcp-tools.json" || fail "echo tool missing over MCP"

echo "9. disable the OpenAPI frontend and expect 404"
curl -sf -X PUT "$BASE/ext/gateway/frontends/openapi" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"enabled":false}' > /dev/null
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" "$BASE/openapi/tools")"
[ "$code" = 404 ] || fail "disabled frontend returned $code"

echo "10. MCP keeps working while OpenAPI is disabled"
mcp_tools "$KEY" > "$TMP/mcp-disabled.json"
grep -q '"name":"echo"' "$TMP/mcp-disabled.json" || fail "MCP unavailable while OpenAPI is off"

echo "11. re-enable the frontend"
curl -sf -X PUT "$BASE/ext/gateway/frontends/openapi" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"enabled":true}' > /dev/null
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" "$BASE/openapi/tools")"
[ "$code" = 200 ] || fail "re-enabled frontend returned $code"

echo "12. revoke the key and expect 401"
curl -sf -X DELETE "$BASE/ext/gateway/keys/walkthrough" \
  -H "Authorization: Bearer $ADMIN" > /dev/null
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" "$BASE/openapi/tools")"
[ "$code" = 401 ] || fail "revoked key returned $code"

echo "PASS"
