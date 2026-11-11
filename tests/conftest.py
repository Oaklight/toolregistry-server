"""Shared fixtures.

The OAuth outbound address policy (#264) resolves every host it screens.
Test hosts such as ``idp.example.com`` do not resolve, and CI may have no
DNS at all, so screening is a no-op by default.  A test that exercises the
policy requests the ``real_outbound_policy`` fixture and uses IP literals,
which the policy judges without a lookup.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def real_outbound_policy():
    """Keep the real OAuth outbound address policy for this test."""
    return None


@pytest.fixture(autouse=True)
def _skip_outbound_screening(request, monkeypatch):
    if "real_outbound_policy" in request.fixturenames:
        return
    from toolregistry_server.oauth import _outbound

    monkeypatch.setattr(_outbound, "screen_url", lambda url, **_: None)
