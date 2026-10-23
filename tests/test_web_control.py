"""Tests for jobfinder.web.control (T34) — the explicit stop path.

the product spec: "The UI provides an explicit way to stop the server."

This is the single most attack-sensitive route in the app, because its
effect is available to an attacker who cannot read a byte of response
body. A shutdown reachable by ``GET`` is triggerable by *any* page the
user has open — ``<img src="http://127.0.0.1:8756/api/stop">`` is enough,
and the attacker never needs to see the reply to know it worked. So the
tests below are not "does stopping work" tests; they are the four
negatives:

  - ``GET`` must not reach it at all (405, so no ``<img>``/``<script>``/
    prefetch/link-preview can fire it).
  - no session cookie -> 401 and the stop handler is **not** called.
  - a foreign ``Origin`` -> 403 and the stop handler is **not** called.
  - a foreign ``Host`` -> 403 (DNS-rebinding, which is the one case where
    the attacker *can* make the browser send a plausible Origin).

Each negative asserts on the handler call count, not just the status
code: a 403 returned *after* the server already began shutting down would
pass a status-only test while still being the vulnerability.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

PORT = 8758
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


class _Recorder:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def client(isolated_app):
    app = isolated_app.create_app(port=PORT)
    recorder = _Recorder()
    app.state.stop_controller.register(recorder)
    with TestClient(app, base_url=BASE_URL) as c:
        yield app, c, recorder


def _authenticate(app, c) -> None:
    resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
    assert resp.status_code == 200, resp.text


def test_stop_is_not_reachable_by_get(client) -> None:
    """An <img src=...> from a hostile page must not be able to fire this."""
    app, c, recorder = client
    _authenticate(app, c)
    resp = c.get("/api/stop")
    assert resp.status_code == 405
    assert recorder.calls == 0


def test_authenticated_same_origin_post_stops_the_server(client) -> None:
    app, c, recorder = client
    _authenticate(app, c)
    resp = c.post("/api/stop", headers={"Origin": BASE_URL})
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "stopping"}
    assert recorder.calls == 1


def test_stop_without_a_session_cookie_is_401_and_does_not_stop(client) -> None:
    app, c, recorder = client
    resp = c.post("/api/stop", headers={"Origin": BASE_URL})
    assert resp.status_code == 401
    assert recorder.calls == 0


def test_stop_from_a_foreign_origin_is_403_and_does_not_stop(client) -> None:
    app, c, recorder = client
    _authenticate(app, c)
    resp = c.post("/api/stop", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 403
    assert recorder.calls == 0


def test_stop_with_a_rebound_host_header_is_403_and_does_not_stop(client) -> None:
    app, c, recorder = client
    _authenticate(app, c)
    resp = c.post("/api/stop", headers={"Host": "evil.example.com", "Origin": BASE_URL})
    assert resp.status_code == 403
    assert recorder.calls == 0


def test_stop_reports_unsupported_when_no_launcher_registered_a_handler(isolated_app) -> None:
    """A bare ``uvicorn jobfinder...`` dev run has no launcher to stop.

    Returning 501 rather than a cheerful 200 keeps the UI honest: the
    server really is still running.
    """
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        _authenticate(app, c)
        resp = c.post("/api/stop", headers={"Origin": BASE_URL})
    assert resp.status_code == 501


def test_stop_controller_runs_the_handler_once_even_if_asked_twice() -> None:
    from jobfinder.web.control import StopController

    recorder = _Recorder()
    controller = StopController()
    controller.register(recorder)
    assert controller.request_stop() is True
    assert controller.request_stop() is True
    assert recorder.calls == 1


def test_stop_controller_without_a_handler_reports_false() -> None:
    from jobfinder.web.control import StopController

    assert StopController().request_stop() is False
