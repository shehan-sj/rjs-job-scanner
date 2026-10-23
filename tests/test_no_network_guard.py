"""Tests for T63's global no-network guard.

T54 proved the suite's safety properties were conventional, not enforced: a
single mis-targeted monkeypatch sent real HTTP requests to
``api.anthropic.com`` from a test that passed in isolation, and nothing
failed loudly — the fake key just made the call 401 instead of being
billed. ``tests/conftest.py`` had no global guard; every Anthropic call
site relied on its own local patch.

This file pins down the guard itself: it must fail loudly (naming the host)
the moment any test opens a socket to a non-loopback address, loopback must
keep working (``tests/test_instance_lock.py`` binds/connects to
``127.0.0.1`` for real), and a test that genuinely needs the network must
have an explicit, visible way to say so rather than defeating the guard by
accident.
"""

from __future__ import annotations

import socket

import pytest

from tests._network_guard import NonLoopbackConnectionBlocked, is_loopback_address


# ── Unit coverage of the loopback classifier itself ─────────────────────


@pytest.mark.parametrize(
    "address",
    [
        ("127.0.0.1", 80),
        ("127.0.0.42", 9999),
        ("::1", 80, 0, 0),
        ("localhost", 80),
    ],
)
def test_loopback_addresses_are_classified_as_loopback(address):
    assert is_loopback_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        ("93.184.216.34", 443),  # example.com's old IP — any public IP works here
        ("203.0.113.5", 443),  # TEST-NET-3 (RFC 5737) — reserved, never loopback
        ("api.anthropic.com", 443),  # unresolved hostname — must not be mistaken for loopback
    ],
)
def test_non_loopback_addresses_are_not_classified_as_loopback(address):
    assert is_loopback_address(address) is False


# ── The guard itself, exercised through a real socket ───────────────────


def test_connecting_to_a_non_loopback_host_is_blocked_and_names_the_host():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NonLoopbackConnectionBlocked) as exc_info:
            sock.connect(("203.0.113.5", 443))
        message = str(exc_info.value)
        assert "203.0.113.5" in message
        assert "443" in message
    finally:
        sock.close()


def test_connecting_to_a_non_loopback_host_via_connect_ex_is_also_blocked():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NonLoopbackConnectionBlocked) as exc_info:
            sock.connect_ex(("203.0.113.5", 443))
        assert "203.0.113.5" in str(exc_info.value)
    finally:
        sock.close()


def test_loopback_connections_still_work():
    """The guard must not break the real sockets some tests legitimately
    open — ``tests/test_instance_lock.py``'s health probe is exactly this
    shape: bind a loopback listener, then connect to it.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.settimeout(2)
            # Must not raise NonLoopbackConnectionBlocked.
            client.connect(("127.0.0.1", port))
        finally:
            client.close()
    finally:
        server.close()


@pytest.mark.allow_network
def test_the_allow_network_marker_is_an_explicit_escape_hatch():
    """A marked test bypasses the guard entirely — the attempt must reach
    real socket machinery (and fail for a real network reason, e.g. a
    timeout) rather than being short-circuited by our guard's own
    exception. This is what makes reaching the network always a visible,
    deliberate choice instead of an accident: the marker is what grants it.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(0.2)
        with pytest.raises(OSError) as exc_info:
            sock.connect(("203.0.113.5", 443))
        # Specifically NOT our guard's exception — real socket machinery ran.
        assert not isinstance(exc_info.value, NonLoopbackConnectionBlocked)
    finally:
        sock.close()
