"""The admin proxy sets the forwarded headers from the ASGI scope.

The gateway hosts the core panel on loopback and proxies every request.  It
is the edge of the deployment, so the client address, scheme and host it
reports must be the ones it saw — a client-supplied ``X-Forwarded-*`` value
is replaced, never appended to.

These tests exercise the proxy itself, so they do not need the core admin
extension contract that ``test_gateway_admin_host.py`` waits for and run in
the regular CI matrix against the released core.
"""

from __future__ import annotations

import asyncio
from typing import Any

from toolregistry_server.gateway.admin import _forward, _request_headers


def _scope(
    client: Any,
    headers: list[tuple[bytes, bytes]],
    scheme: str = "https",
) -> dict[str, Any]:
    """Build the slice of an ASGI HTTP scope the proxy reads."""
    return {
        "type": "http",
        "method": "GET",
        "path": "/admin/api/info",
        "raw_path": b"/admin/api/info",
        "query_string": b"",
        "scheme": scheme,
        "client": client,
        "headers": headers,
    }


async def _capture_wire_headers(
    scope: dict[str, Any], body: bytes = b""
) -> tuple[int, list[tuple[bytes, bytes]]]:
    """Forward one request to a fake panel and return what it received."""
    captured: list[bytes] = []

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        captured.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    sockets = server.sockets or []
    port = sockets[0].getsockname()[1]
    try:
        status, _, _ = await _forward(port, scope, body)
    finally:
        server.close()
        await server.wait_closed()

    parsed: list[tuple[bytes, bytes]] = []
    for line in captured[0].split(b"\r\n")[1:]:
        if not line:
            continue
        name, _, value = line.partition(b":")
        parsed.append((name.strip().lower(), value.strip()))
    return status, parsed


def _values(headers: list[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    """Return every value sent for *name*."""
    return [value for header, value in headers if header == name]


class TestForwardedClientHeader:
    async def test_a_forged_chain_reaches_the_panel_as_the_socket_peer(self):
        scope = _scope(
            ("198.51.100.7", 443),
            [
                (b"host", b"panel.example:8443"),
                (b"x-forwarded-for", b"203.0.113.9, 10.0.0.1"),
            ],
        )

        status, headers = await _capture_wire_headers(scope)

        assert status == 200
        assert _values(headers, b"x-forwarded-for") == [b"198.51.100.7"]
        assert not any(b"203.0.113.9" in value for _, value in headers)
        assert not any(b"10.0.0.1" in value for _, value in headers)

    async def test_repeated_inbound_headers_do_not_produce_duplicates(self):
        scope = _scope(
            ("198.51.100.7", 443),
            [
                (b"X-Forwarded-For", b"203.0.113.9"),
                (b"x-forwarded-for", b"203.0.113.10"),
            ],
        )

        _, headers = await _capture_wire_headers(scope)

        assert _values(headers, b"x-forwarded-for") == [b"198.51.100.7"]

    async def test_the_proxy_states_the_scheme_and_host_it_saw(self):
        scope = _scope(
            ("198.51.100.7", 443),
            [
                (b"host", b"panel.example:8443"),
                (b"x-forwarded-proto", b"http"),
                (b"x-forwarded-host", b"evil.example"),
            ],
        )

        _, headers = await _capture_wire_headers(scope)

        assert _values(headers, b"x-forwarded-proto") == [b"https"]
        assert _values(headers, b"x-forwarded-host") == [b"panel.example:8443"]
        assert not any(b"evil.example" in value for _, value in headers)

    async def test_a_scope_without_a_client_forwards_no_claimed_address(self):
        scope = _scope(None, [(b"x-forwarded-for", b"203.0.113.9")])

        _, headers = await _capture_wire_headers(scope)

        assert not _values(headers, b"x-forwarded-for")

    async def test_an_inbound_forwarded_header_is_stripped(self):
        scope = _scope(
            ("198.51.100.7", 443),
            [(b"Forwarded", b"for=203.0.113.9;proto=http;host=evil.example")],
        )

        _, headers = await _capture_wire_headers(scope)

        assert not _values(headers, b"forwarded")
        assert not any(b"203.0.113.9" in value for _, value in headers)
        assert not any(b"evil.example" in value for _, value in headers)


class TestRequestHeaders:
    def test_forwarded_headers_are_replaced_not_appended(self):
        headers = _request_headers(
            _scope(
                ("198.51.100.7", 443),
                [
                    (b"x-forwarded-for", b"203.0.113.9"),
                    (b"x-forwarded-for", b"203.0.113.10"),
                ],
            ),
            b"",
        )

        assert _values(headers, b"x-forwarded-for") == [b"198.51.100.7"]

    def test_other_client_headers_still_pass_through(self):
        headers = _request_headers(
            _scope(
                ("198.51.100.7", 443),
                [(b"authorization", b"Bearer operator-token")],
            ),
            b"",
        )

        assert (b"authorization", b"Bearer operator-token") in headers
