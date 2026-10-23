"""Tests for jobfinder.web.stream (T20) — SSE scan-progress and log stream.

Replaces the desktop app's ``queue.Queue`` + 300ms ``QTimer`` polling
(``orchestrator.py``, ``widget.py``) with one tagged JSON event per
message. Every scan here is driven by a stubbed ``_build_orchestrator`` —
no network, no real scrape — per the same seam T19's tests use.
"""

from __future__ import annotations

import importlib
import json
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

PORT = 8763
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


def _parse_sse_events(lines: list[str]) -> list[dict]:
    """Minimal SSE ``data:`` line parser — good enough for this module's
    own tagged-JSON events, not a general EventSource client.
    """
    events = []
    for line in lines:
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if payload:
                events.append(json.loads(payload))
    return events


class _StubOrchestrator:
    """No network, no real scrape — emits the same tagged events the real
    ``Orchestrator.run_scan()`` emits, plus a log line in between so the
    DoD's ">=1 log/progress event between scan_start and scan_complete"
    has something to observe without touching real scan internals.
    """

    def __init__(self, events, *, delay: float = 0.02) -> None:
        self._events = events
        self._delay = delay

    def run_scan(self) -> None:
        self._events.put({"type": "scan_start"})
        logging.getLogger("jobfinder.test_stub").info("fetching jobs (stub, no network)")
        if self._delay:
            time.sleep(self._delay)
        self._events.put({"type": "scan_complete", "new_jobs": 1, "matches": 1})


def _patch_stub_builder(monkeypatch, *, delay: float = 0.02) -> None:
    import jobfinder.web.scan as scan_module

    monkeypatch.setattr(
        scan_module,
        "_build_orchestrator",
        lambda db, config, events: _StubOrchestrator(events, delay=delay),
    )


# ── Ordering: scan_start -> >=1 log/progress -> scan_complete ───────────


def test_stream_delivers_scan_start_then_log_then_scan_complete_in_order(authed_client, monkeypatch) -> None:
    _patch_stub_builder(monkeypatch, delay=0.05)
    app, c = authed_client

    with c.stream("GET", "/api/stream/scan") as resp:
        assert resp.status_code == 200, resp.text

        trigger_resp = c.post("/api/scan")
        assert trigger_resp.status_code == 202, trigger_resp.text

        collected: list[dict] = []
        deadline = time.monotonic() + 10
        lines_iter = resp.iter_lines()
        while time.monotonic() < deadline:
            try:
                line = next(lines_iter)
            except StopIteration:
                break
            events = _parse_sse_events([line])
            collected.extend(events)
            types = [e.get("type") for e in collected]
            if "scan_complete" in types:
                break

    types = [e.get("type") for e in collected]
    assert "scan_start" in types, types
    assert "scan_complete" in types, types
    start_idx = types.index("scan_start")
    complete_idx = types.index("scan_complete")
    assert start_idx < complete_idx, types

    between = types[start_idx + 1 : complete_idx]
    assert any(t in ("log", "progress") for t in between), types

    complete_event = collected[complete_idx]
    assert complete_event["new_jobs"] == 1
    assert complete_event["matches"] == 1


# ── Disconnect mid-scan + reconnect: scan still completes cleanly ───────


def test_disconnect_mid_scan_then_reconnect_scan_completes_with_no_exception(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module

    app, c = authed_client

    started = threading.Event()
    release = threading.Event()
    scan_error: list[BaseException] = []

    class _BlockingStubOrchestrator:
        def __init__(self, events) -> None:
            self._events = events

        def run_scan(self) -> None:
            self._events.put({"type": "scan_start"})
            started.set()
            release.wait(timeout=5)
            self._events.put({"type": "scan_complete", "new_jobs": 0, "matches": 0})

    monkeypatch.setattr(
        scan_module,
        "_build_orchestrator",
        lambda db, config, events: _BlockingStubOrchestrator(events),
    )

    # Wrap scan_state.mark_failed so a propagated exception inside
    # _scan_fn (which swallows into scan_state.last_error) would be
    # visible to the assertion below instead of silently disappearing.
    original_mark_failed = app.state.scan_state.mark_failed

    def _recording_mark_failed(error: str) -> None:
        scan_error.append(RuntimeError(error))
        original_mark_failed(error)

    monkeypatch.setattr(app.state.scan_state, "mark_failed", _recording_mark_failed)

    # First client connects, then disconnects mid-scan.
    with c.stream("GET", "/api/stream/scan") as resp:
        assert resp.status_code == 200, resp.text
        trigger_resp = c.post("/api/scan")
        assert trigger_resp.status_code == 202, trigger_resp.text
        assert started.wait(timeout=5), "scan never started"
        # Exiting the `with` block closes this client's connection while
        # the scan is still blocked on `release`.

    # Reconnect while the scan is still running.
    with c.stream("GET", "/api/stream/scan") as resp2:
        assert resp2.status_code == 200, resp2.text
        release.set()

        deadline = time.monotonic() + 5
        status = {}
        while time.monotonic() < deadline:
            status = c.get("/api/scan/status").json()
            if not status["running"]:
                break
            time.sleep(0.02)

    assert status["running"] is False
    assert status["last_error"] is None
    assert scan_error == []


# ── Log buffer stays bounded with no client attached ────────────────────


def test_log_ring_buffer_stays_bounded_with_no_client_attached(authed_client) -> None:
    from jobfinder.web.stream import RING_BUFFER_SIZE, BroadcastLogHandler

    app, c = authed_client
    broadcaster = app.state.broadcaster
    loop = app.state.loop

    handler = BroadcastLogHandler(broadcaster, loop)
    logger = logging.getLogger("jobfinder.test_bound")

    for i in range(RING_BUFFER_SIZE * 3):
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            0,
            f"line {i}",
            (),
            None,
        )
        handler.emit(record)

    # Give the loop thread a moment to drain the call_soon_threadsafe queue.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(broadcaster._ring) < RING_BUFFER_SIZE:
        time.sleep(0.01)

    assert len(broadcaster._ring) == RING_BUFFER_SIZE


# ── GET /api/state reports correctly during and after a scan ────────────


def test_state_reports_correctly_during_and_after_scan_with_stream_active(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module
    from jobfinder.matcher.models import JobPosting, MatchResult

    app, c = authed_client

    def _build(db_arg, config, events):
        job = JobPosting(
            job_id="stream-state-job-1",
            title="QA Engineer",
            company="Acme",
            location="Remote",
            description="desc",
            url="https://example.com/stream-state-job-1",
            source="test",
        )

        class _FakeOrchestrator:
            def run_scan(self) -> None:
                events.put({"type": "scan_start"})
                time.sleep(0.1)
                db_arg.mark_seen([job])
                db_arg.save_match(MatchResult(job=job, score=80, reasoning="stub"))
                events.put({"type": "scan_complete", "new_jobs": 1, "matches": 1})

        return _FakeOrchestrator()

    monkeypatch.setattr(scan_module, "_build_orchestrator", _build)

    with c.stream("GET", "/api/stream/scan"):
        resp = c.post("/api/scan")
        assert resp.status_code == 202, resp.text

        deadline = time.monotonic() + 1
        saw_running = False
        while time.monotonic() < deadline:
            state = c.get("/api/state").json()
            if state["running"]:
                saw_running = True
                break
            time.sleep(0.01)
        assert saw_running, "never observed running=True mid-scan"

        deadline = time.monotonic() + 5
        state = {}
        while time.monotonic() < deadline:
            state = c.get("/api/state").json()
            if not state["running"]:
                break
            time.sleep(0.02)

    assert state["running"] is False
    assert state["last_new_jobs"] == 1
    assert state["last_matches"] == 1
    assert state["scheduler_alive"] is True


# ── Auth ──────────────────────────────────────────────────────────────


def test_stream_endpoint_rejects_request_with_no_session_cookie(client) -> None:
    app, c = client
    resp = c.get("/api/stream/scan")
    assert resp.status_code == 401
