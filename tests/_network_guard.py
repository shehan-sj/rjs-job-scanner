"""T63: classify socket addresses as loopback or not, for the global
no-network guard wired in ``tests/conftest.py``.

Not a test module itself (no ``test_`` prefix, so pytest never collects
it) — just the small, independently-testable piece of logic the guard
fixture needs, split out so ``tests/test_no_network_guard.py`` can pin it
down directly instead of only through a real socket.
"""

from __future__ import annotations

import ipaddress

#: Hostnames that resolve to a loopback address by OS/hosts-file convention
#: rather than by being a literal IP — ``socket.connect()`` can be handed
#: either shape depending on how the caller built the address tuple.
_LOOPBACK_HOSTNAMES = frozenset({"localhost"})


class NonLoopbackConnectionBlocked(RuntimeError):
    """Raised by the guard when a test tries to open a real socket to a
    non-loopback address.

    This is exactly the failure T54 needed and did not have: a test that
    reaches out to the real network (by accident — a mis-targeted
    monkeypatch, a forgotten mock) must fail loudly and name the host it
    tried to reach, instead of quietly making a real HTTP call that
    happens to 401 on a fake key.
    """


def is_loopback_address(address: object) -> bool:
    """True if ``address`` (a socket address tuple, as passed to
    ``socket.socket.connect``/``connect_ex``) targets loopback.

    Handles IPv4 ``(host, port)``, IPv6 ``(host, port, flowinfo, scopeid)``,
    and the ``"localhost"`` hostname. Addresses that are not a literal IP
    and not ``"localhost"`` (e.g. an unresolved real hostname) are treated
    as *not* loopback — by the time ``connect()`` actually runs, DNS
    resolution has normally already turned a real hostname into an IP, so
    anything still symbolic here is not something we can vouch for.
    """
    try:
        host = address[0]
    except (TypeError, IndexError, KeyError):
        # Not a (host, port, ...) tuple at all (e.g. an AF_UNIX path, or a
        # bytes address) — not what this guard is for; let it through.
        return True

    if not isinstance(host, str):
        return True

    if host in _LOOPBACK_HOSTNAMES:
        return True

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def format_blocked_message(address: object) -> str:
    """The guard's failure message — must name the host and port so the
    failure is self-diagnosing (T63's DoD) rather than a bare traceback.
    """
    host = "?"
    port = "?"
    if isinstance(address, tuple) and address:
        host = address[0]
        if len(address) > 1:
            port = address[1]

    return (
        f"Test suite's no-network guard (T63) blocked a connection to {host}:{port}. "
        "Tests must not open real sockets to non-loopback hosts — mock the HTTP "
        "client/call this test exercises instead (see T54: a passing-in-isolation "
        "test once made real calls to api.anthropic.com this way). If a test "
        "genuinely needs the network, mark it @pytest.mark.allow_network so that "
        "reaching the network is an explicit, visible choice rather than an "
        "accident."
    )
