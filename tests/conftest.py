"""Shared pytest fixtures for the JobFinder test suite.

T63 added the one enforcement mechanism that was missing: a global,
autouse no-network guard (below), so an escaped mock is a loud failure
naming the host instead of a real, silently-swallowed HTTP call (T54's
incident). Everything else here is still intentionally minimal.
"""

from __future__ import annotations

import socket

import pytest

from tests._network_guard import NonLoopbackConnectionBlocked, format_blocked_message, is_loopback_address

# The real, unpatched bound-to-the-class methods — captured once at import
# time, before any test can have monkeypatched them, so the guard always
# has the genuine implementation to delegate to (and to restore exactly).
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def pytest_addoption(parser):
    # Consumed by tests/test_scoring_golden.py (T4). Regenerating the golden
    # file is a deliberate act, not a way to make a failing test pass — always
    # inspect `git diff tests/fixtures/golden/expected.json` before committing
    # a regeneration, and explain *why* the scoring output changed.
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help=(
            "Regenerate tests/fixtures/golden/expected.json from the current "
            "score_job() output instead of asserting against the committed "
            "expectation. Review the resulting diff before committing — a "
            "clean run after this flag only proves the file matches whatever "
            "the code just produced, not that the new values are correct."
        ),
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_network: explicitly permit this test to open a real socket to a "
        "non-loopback address. Undocumented/accidental network access is blocked "
        "everywhere else (T63) — use this marker to make reaching the network a "
        "visible, deliberate choice instead of an accident.",
    )


@pytest.fixture(autouse=True)
def _no_network_guard(request):
    """Fail any test that opens a socket to a non-loopback address.

    T54: a single mis-targeted monkeypatch let a "passing in isolation"
    test make real HTTP requests to ``api.anthropic.com`` — nothing
    failed loudly because the fake key just turned it into a 401 instead
    of a billed call. Every Anthropic (and any other HTTP) call site
    relied on its own local mock; this is the one guard that does not.

    Loopback keeps working — ``tests/test_instance_lock.py`` binds and
    connects to ``127.0.0.1`` for real, and ``scripts/smoke.py`` boots the
    real FastAPI app. ``fastapi.testclient.TestClient`` calls the ASGI app
    in-process and never touches a socket at all, so it is unaffected
    either way.

    A test that genuinely needs the network opts in with
    ``@pytest.mark.allow_network`` — an explicit, visible choice rather
    than something that happens to work because a mock was missing.
    """
    allow_network = request.node.get_closest_marker("allow_network") is not None

    def _guarded_connect(self, address):
        if not allow_network and not is_loopback_address(address):
            raise NonLoopbackConnectionBlocked(format_blocked_message(address))
        return _real_connect(self, address)

    def _guarded_connect_ex(self, address):
        if not allow_network and not is_loopback_address(address):
            raise NonLoopbackConnectionBlocked(format_blocked_message(address))
        return _real_connect_ex(self, address)

    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    try:
        yield
    finally:
        socket.socket.connect = _real_connect
        socket.socket.connect_ex = _real_connect_ex
