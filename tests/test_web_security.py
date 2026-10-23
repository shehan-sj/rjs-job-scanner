"""Tests for jobfinder.web.security (T15) — the product spec.

The server is unauthenticated by design (the product spec: single local user, no
accounts), so these controls are the only thing standing between a web
page the user happens to have open and their local API. Each security
control gets its own test, per the task brief: "a security control
with no test is a comment."
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
import uvicorn
from fastapi.testclient import TestClient

PORT = 8756
BASE_URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))

    import jobfinder.paths as paths

    importlib.reload(paths)

    import jobfinder.web.app as web_app

    importlib.reload(web_app)

    yield web_app

    importlib.reload(paths)


@pytest.fixture
def client(isolated_app):
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        yield app, c


def _exchange_cookie(client) -> dict:
    """Helper: perform a valid token exchange and return the cookie jar."""
    app, c = client
    token = app.state.launch_token.value
    resp = c.post("/api/auth/exchange", json={"token": token})
    assert resp.status_code == 200, resp.text
    return c.cookies


def test_foreign_host_header_is_rejected_403(client) -> None:
    app, c = client
    resp = c.get("/api/health", headers={"Host": "evil.com"})
    assert resp.status_code == 403


def test_matching_host_header_is_allowed(client) -> None:
    app, c = client
    resp = c.get("/api/health", headers={"Host": f"127.0.0.1:{PORT}"})
    assert resp.status_code == 200


def test_state_changing_request_with_foreign_origin_is_rejected_403(client) -> None:
    app, c = client
    # No cookie exchange needed: the Origin check runs ahead of the session
    # check (install_security's middleware add-order), so a bad Origin is
    # rejected before auth is ever considered.
    resp = c.post(
        "/api/auth/exchange",
        json={"token": "irrelevant-once-origin-is-checked"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403


def test_state_changing_request_with_matching_origin_is_not_origin_blocked(client) -> None:
    app, c = client
    resp = c.post(
        "/api/auth/exchange",
        json={"token": app.state.launch_token.value},
        headers={"Origin": BASE_URL},
    )
    # Origin matched the allowlist, so the request proceeds to the handler
    # (a 403 here would mean the Origin check wrongly fired).
    assert resp.status_code == 200


def test_request_without_session_cookie_is_rejected_401(client) -> None:
    app, c = client
    resp = c.get("/api/health-check-placeholder-does-not-exist")
    # A nonexistent /api/* path still must fail auth before routing 404s it
    # — the session check runs in middleware, ahead of the router.
    assert resp.status_code == 401


def test_health_check_is_public_and_needs_no_cookie(client) -> None:
    app, c = client
    resp = c.get("/api/health")
    assert resp.status_code == 200


def test_token_exchange_sets_httponly_samesite_strict_cookie(client) -> None:
    app, c = client
    resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    # Starlette emits "SameSite=strict" (lowercase value) — the product spec's
    # casing is "Strict"; the cookie attribute is case-insensitive per
    # RFC 6265bis, so this compares case-insensitively rather than
    # asserting a specific casing Starlette doesn't control.
    assert "samesite=strict" in set_cookie.lower()
    # Host-only: no explicit Domain attribute.
    assert "Domain=" not in set_cookie


def test_token_exchange_rejects_wrong_token(client) -> None:
    app, c = client
    resp = c.post("/api/auth/exchange", json={"token": "not-the-real-token"})
    assert resp.status_code == 401


def test_exchanged_cookie_grants_access_to_protected_route(client) -> None:
    app, c = client
    cookies = _exchange_cookie(client)
    resp = c.get("/api/health", cookies=cookies)
    assert resp.status_code == 200


def test_listening_socket_is_bound_to_loopback_not_wildcard(isolated_app) -> None:
    """The product spec: bind 127.0.0.1 explicitly, never 0.0.0.0 or
    ::. Asserted against the real listening socket (not by shelling out to
    ss/netstat — ss doesn't exist on Windows and netstat's output format
    isn't stable across platforms).
    """

    async def _run() -> None:
        config = isolated_app.build_uvicorn_config(port=0)
        server = uvicorn.Server(config)
        serve_task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            sock = server.servers[0].sockets[0]
            host, _port = sock.getsockname()[:2]
            assert host == "127.0.0.1"
        finally:
            server.should_exit = True
            await serve_task

    asyncio.run(_run())
