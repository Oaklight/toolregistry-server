"""Outbound address screening for OAuth requests (#264).

Every request the server makes on its own behalf for OAuth — discovery,
JWKS, introspection, and the upstream token POST — passes through
:func:`screen_url` first.  The policy is :mod:`toolregistry.netpolicy`, the
same one MCP and OpenAPI sources use, so a configured ``issuer`` or a
discovered ``token_endpoint`` cannot reach a loopback, link-local or private
address (such as a cloud metadata service) unless the operator opts in with
``allow_private_hosts``.
"""

from __future__ import annotations

import urllib.request
from typing import Any

__all__ = ["OutboundRefused", "open_url", "screen_url"]


class OutboundRefused(ValueError):
    """An OAuth endpoint names a host the outbound policy refuses."""


def screen_url(url: str, *, allow_private_hosts: bool = False) -> None:
    """Refuse *url* unless the outbound address policy allows it.

    Args:
        url: The endpoint about to be dialed.
        allow_private_hosts: Accept a non-public address.

    Raises:
        OutboundRefused: If the URL is malformed, its host does not resolve,
            or it resolves to an address the policy refuses.  The message
            never names a resolved address.
    """
    from toolregistry.netpolicy import UnsafeAddressError, validate_outbound_url

    try:
        validate_outbound_url(url, allow_private_hosts=allow_private_hosts)
    except UnsafeAddressError as exc:
        raise OutboundRefused(str(exc)) from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so a screened request cannot be re-aimed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Return ``None`` so urllib raises the 3xx as an ``HTTPError``."""
        return None


def open_url(request: str | urllib.request.Request, timeout: float) -> Any:
    """Open *request* without following redirects.

    :func:`screen_url` checks only the URL it is given.  The default opener
    would follow a 3xx to any host, including a private one, and would replay
    an ``Authorization`` header there, so every screened request is opened
    through this function instead.  A redirect surfaces as
    :class:`urllib.error.HTTPError` with its 3xx status.

    Args:
        request: A URL or a prepared request.
        timeout: Seconds before the request is abandoned.

    Returns:
        The response object, usable as a context manager.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    return opener.open(request, timeout=timeout)
