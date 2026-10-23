"""Tests for jobfinder.web.fillerbot (T30) — session model + lifecycle.

Every test here uses a fake ``FillerEngine`` (no real browser, no real
Anthropic call) so the 409 / reap / stop-on-shutdown contracts can be
tested fast and deterministically. The one test that needs a real
Chrome process — proving a stopped session leaves no orphan behind — uses
the actual Playwright Chromium installed for this project against a
local ``file://`` fixture page, never a live job site.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from jobfinder.fillerbot.models import FillerProfile

PORT = 8771
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


@pytest.fixture
def authed_client(client):
    app, c = client
    resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
    assert resp.status_code == 200, resp.text
    return app, c


# ── Fake engine — no real browser/Anthropic for the session-layer tests ──


class _FakeEngine:
    """Stands in for ``FillerEngine`` at the FillerSessionManager layer —
    enough surface for the 9 inbound controls plus ``run()``, with no
    Playwright or Anthropic dependency at all.
    """

    instances: list["_FakeEngine"] = []

    def __init__(self, api_key, model, profile, resume_path="", chrome_exe="", *, emit) -> None:
        self._emit = emit
        self._stop_event = asyncio.Event()
        self.started_url: str | None = None
        self.stopped = False
        self.paused = False
        self.fill_overrides: dict | None = None
        self.accepted_ids: list[str | None] = []
        self.skipped_ids: list[str | None] = []
        self.clicked_next = False
        self.rescanned = False
        _FakeEngine.instances.append(self)

    def start_session(self, url: str) -> None:
        self.started_url = url

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def execute_fill(self, overrides=None) -> None:
        self.fill_overrides = overrides

    def stop_session(self) -> None:
        self.stopped = True
        self._stop_event.set()

    def accept_upload(self, request_id=None) -> bool:
        self.accepted_ids.append(request_id)
        return True

    def skip_upload(self, request_id=None) -> bool:
        self.skipped_ids.append(request_id)
        return True

    def click_next(self) -> None:
        self.clicked_next = True

    def rescan_page(self) -> None:
        self.rescanned = True

    async def run(self) -> None:
        self._emit({"type": "status", "message": "started"})
        await self._stop_event.wait()
        self._emit({"type": "finished_session"})


@pytest.fixture(autouse=True)
def _reset_fake_engine_instances():
    _FakeEngine.instances.clear()
    yield
    _FakeEngine.instances.clear()


def _patch_fake_engine(monkeypatch) -> None:
    import jobfinder.web.fillerbot as fillerbot_mod

    monkeypatch.setattr(fillerbot_mod, "FillerEngine", _FakeEngine)
    monkeypatch.setattr(fillerbot_mod, "find_chrome_exe", lambda: "fake-chrome.exe")
    monkeypatch.setattr(fillerbot_mod, "load_filler_profile", lambda *a, **k: FillerProfile())


@pytest.fixture
def fake_engine_client(authed_client, monkeypatch):
    app, c = authed_client
    _patch_fake_engine(monkeypatch)
    app.state.config.anthropic.api_key = "fake-key"
    return app, c


# ── One session at a time ────────────────────────────────────────────────


def test_second_session_while_active_returns_409(fake_engine_client):
    app, c = fake_engine_client

    r1 = c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    assert r1.status_code == 201, r1.text
    session_id = r1.json()["session_id"]
    assert session_id

    r2 = c.post("/api/fillerbot/session", json={"url": "file:///b.html"})
    assert r2.status_code == 409, r2.text

    status = c.get("/api/fillerbot/session").json()
    assert status == {"active": True, "session_id": session_id}

    stop = c.post("/api/fillerbot/stop")
    assert stop.status_code == 200

    status = c.get("/api/fillerbot/session").json()
    assert status["active"] is False


def test_session_can_be_started_again_after_stop(fake_engine_client):
    app, c = fake_engine_client

    r1 = c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    assert r1.status_code == 201
    c.post("/api/fillerbot/stop")

    r2 = c.post("/api/fillerbot/session", json={"url": "file:///b.html"})
    assert r2.status_code == 201, r2.text
    c.post("/api/fillerbot/stop")


# ── Controls reach the engine, and bump last_activity ───────────────────


def test_controls_are_rejected_with_404_when_no_session_is_active(fake_engine_client):
    app, c = fake_engine_client
    for method, path, body in [
        ("post", "/api/fillerbot/pause", None),
        ("post", "/api/fillerbot/resume", None),
        ("post", "/api/fillerbot/fill", {"overrides": {}}),
        ("post", "/api/fillerbot/upload/accept", {}),
        ("post", "/api/fillerbot/upload/skip", {}),
        ("post", "/api/fillerbot/next", None),
        ("post", "/api/fillerbot/rescan", None),
    ]:
        resp = getattr(c, method)(path, json=body)
        assert resp.status_code == 404, (path, resp.text)


def test_controls_reach_the_active_engine(fake_engine_client):
    app, c = fake_engine_client
    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    engine = _FakeEngine.instances[-1]

    assert c.post("/api/fillerbot/pause").status_code == 200
    assert engine.paused is True

    assert c.post("/api/fillerbot/resume").status_code == 200
    assert engine.paused is False

    assert c.post("/api/fillerbot/fill", json={"overrides": {"0": "Jane"}}).status_code == 200
    assert engine.fill_overrides == {0: "Jane"}

    assert c.post("/api/fillerbot/next").status_code == 200
    assert engine.clicked_next is True

    assert c.post("/api/fillerbot/rescan").status_code == 200
    assert engine.rescanned is True

    c.post("/api/fillerbot/stop")


# ── resume_upload_prompt correlated request id round-trips over HTTP ────


def test_upload_accept_and_skip_carry_the_request_id_through(fake_engine_client):
    app, c = fake_engine_client
    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    engine = _FakeEngine.instances[-1]

    resp = c.post("/api/fillerbot/upload/accept", json={"request_id": "abc123"})
    assert resp.status_code == 200
    assert engine.accepted_ids == ["abc123"]

    resp = c.post("/api/fillerbot/upload/skip", json={"request_id": "def456"})
    assert resp.status_code == 200
    assert engine.skipped_ids == ["def456"]

    c.post("/api/fillerbot/stop")


def test_upload_reply_rejected_when_engine_reports_no_match(fake_engine_client, monkeypatch):
    app, c = fake_engine_client
    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    engine = _FakeEngine.instances[-1]
    monkeypatch.setattr(engine, "accept_upload", lambda request_id=None: False)

    resp = c.post("/api/fillerbot/upload/accept", json={"request_id": "stale-id"})
    assert resp.status_code == 409

    c.post("/api/fillerbot/stop")


# ── Abandoned session is reaped after a timeout ─────────────────────────


def test_abandoned_session_is_reaped_after_idle_timeout(fake_engine_client):
    app, c = fake_engine_client
    # Reuse the real lifespan-owned manager (so its shutdown() still runs
    # at app teardown) but with a near-instant timeout for the test.
    app.state.filler_sessions._idle_timeout_seconds = 0.2  # noqa: SLF001
    app.state.filler_sessions._reap_interval_seconds = 0.05  # noqa: SLF001

    r1 = c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    assert r1.status_code == 201
    engine = _FakeEngine.instances[-1]
    assert engine.stopped is False

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not engine.stopped:
        time.sleep(0.05)

    assert engine.stopped is True, "abandoned session was never reaped"

    status = c.get("/api/fillerbot/session").json()
    assert status["active"] is False


def test_activity_resets_the_idle_clock_so_an_active_session_is_not_reaped(fake_engine_client):
    app, c = fake_engine_client
    app.state.filler_sessions._idle_timeout_seconds = 0.3  # noqa: SLF001
    app.state.filler_sessions._reap_interval_seconds = 0.05  # noqa: SLF001

    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    engine = _FakeEngine.instances[-1]

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        status = c.get("/api/fillerbot/session").json()
        assert status["active"] is True
        time.sleep(0.05)

    assert engine.stopped is False
    c.post("/api/fillerbot/stop")


# ── SSE event stream ──────────────────────────────────────────────────────


def test_events_stream_delivers_tagged_events(fake_engine_client):
    app, c = fake_engine_client
    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})

    with c.stream("GET", "/api/fillerbot/events") as resp:
        assert resp.status_code == 200, resp.text
        lines_iter = resp.iter_lines()
        deadline = time.monotonic() + 5
        saw_started = False
        while time.monotonic() < deadline:
            try:
                line = next(lines_iter)
            except StopIteration:
                break
            if line.startswith("data:"):
                event = json.loads(line[len("data:") :])
                if event["type"] == "status" and event["message"] == "started":
                    saw_started = True
                    break
        assert saw_started

    c.post("/api/fillerbot/stop")


def test_events_stream_404s_with_no_active_session(fake_engine_client):
    app, c = fake_engine_client
    resp = c.get("/api/fillerbot/events")
    assert resp.status_code == 404


# ── Auth ──────────────────────────────────────────────────────────────


def test_session_endpoint_rejects_request_with_no_session_cookie(client):
    app, c = client
    resp = c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    assert resp.status_code == 401


# ── Real Chrome: closing the session leaves no orphan process behind ────


def _count_chrome_processes(exe_path: str) -> int:
    name = os.path.basename(exe_path)
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pytest.skip("tasklist is unavailable in this environment")
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    if len(lines) == 1 and lines[0].lower().startswith('"info:'):
        return 0
    return len(lines)


def _fill_json(value: str) -> str:
    return (
        '{"page_state": "form", "page_description": "", "actions": ['
        '{"action_type": "fill", "selector_strategy": "id", "selector_value": "full_name", '
        f'"value": "{value}", "field_label": "Full name", "confidence": 1.0, "source_field": "full_name", '
        '"field_index": 0}], "next_step": "fill", "human_note": ""}'
    )


_COMPLETE_JSON = (
    '{"page_state": "complete", "page_description": "", "actions": [], "next_step": "done", "human_note": ""}'
)


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


@pytest.mark.skipif(sys.platform != "win32", reason="chrome.exe process enumeration is Windows-specific")
def test_closing_session_leaves_no_orphan_chrome_process(authed_client, monkeypatch, tmp_path):
    from jobfinder.fillerbot.browser import find_chrome_exe
    from jobfinder.fillerbot import vision as vision_mod
    import jobfinder.web.fillerbot as fillerbot_mod

    chrome_exe = find_chrome_exe()
    if not chrome_exe:
        pytest.skip("no headed Chrome executable found for Playwright")

    app, c = authed_client
    app.state.config.anthropic.api_key = "fake-key"
    monkeypatch.setattr(fillerbot_mod, "find_chrome_exe", lambda: chrome_exe)
    monkeypatch.setattr(fillerbot_mod, "load_filler_profile", lambda *a, **k: FillerProfile(full_name="Jane Doe"))

    responses = [_fill_json("Jane Doe"), _COMPLETE_JSON]

    class _Messages:
        def create(self, **kwargs):
            text = responses.pop(0) if responses else _COMPLETE_JSON
            return _FakeResponse(text)

    class _Client:
        def __init__(self, *a, **k) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(vision_mod.anthropic, "Anthropic", _Client)

    form_path = tmp_path / "form.html"
    form_path.write_text("<html><body><input id='full_name' type='text'></body></html>", encoding="utf-8")
    form_url = form_path.resolve().as_uri()

    baseline = _count_chrome_processes(chrome_exe)

    resp = c.post("/api/fillerbot/session", json={"url": form_url})
    assert resp.status_code == 201, resp.text

    saw_action_executed = False
    fill_triggered = False
    with c.stream("GET", "/api/fillerbot/events") as stream_resp:
        assert stream_resp.status_code == 200, stream_resp.text
        lines_iter = stream_resp.iter_lines()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                line = next(lines_iter)
            except StopIteration:
                break
            if not line.startswith("data:"):
                continue
            event = json.loads(line[len("data:") :])
            # The engine pauses after analysis, waiting for the user to
            # approve the pending actions — approve via the control
            # endpoint, the same way a real client would, to unblock it.
            if not fill_triggered and event["type"] == "status" and "Found 1 field" in event.get("message", ""):
                fill_triggered = True
                fill_resp = c.post("/api/fillerbot/fill", json={})
                assert fill_resp.status_code == 200, fill_resp.text
            if event["type"] == "action_executed":
                assert event["ok"] is True, event
                saw_action_executed = True
            if event["type"] == "fill_complete":
                break

    assert saw_action_executed, "fill action was never executed against the fixture form"

    stop_resp = c.post("/api/fillerbot/stop")
    assert stop_resp.status_code == 200

    status = c.get("/api/fillerbot/session").json()
    assert status["active"] is False

    final = _count_chrome_processes(chrome_exe)
    assert final == baseline, f"orphaned chrome.exe processes detected: baseline={baseline} final={final}"
