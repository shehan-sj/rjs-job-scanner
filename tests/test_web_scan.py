"""Tests for jobfinder.web.scan (T19) — scan control + state snapshot.

The real scan cycle builds a real ``jobfinder.orchestrator.Orchestrator``,
which does network I/O (``jobfinder/sources``). Every test here that
triggers a scan therefore monkeypatches ``jobfinder.web.scan.
_build_orchestrator`` with a stub — no network, no real scrape — per the
T19 DoD.
"""

from __future__ import annotations

import importlib
import threading
import time

import pytest
from fastapi.testclient import TestClient

PORT = 8762
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


class _StubOrchestrator:
    """No network, no real scrape — just a synchronous no-op standing in
    for the real ``Orchestrator.run_scan()`` for T19's DoD.
    """

    def __init__(self, *, delay: float = 0.0) -> None:
        self._delay = delay

    def run_scan(self) -> None:
        if self._delay:
            time.sleep(self._delay)


def _stub_orchestrator_builder(*, delay: float = 0.0):
    def _build(db, config, events):
        return _StubOrchestrator(delay=delay)

    return _build


# ── POST /api/scan → 202; status running then idle ──────────────────────


def test_trigger_scan_returns_202_and_status_reports_running_then_idle(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module

    app, c = authed_client
    started = threading.Event()
    release = threading.Event()

    class _BlockingOrchestrator:
        def run_scan(self) -> None:
            started.set()
            release.wait(timeout=5)

    monkeypatch.setattr(scan_module, "_build_orchestrator", lambda db, config, events: _BlockingOrchestrator())

    resp = c.post("/api/scan")
    assert resp.status_code == 202, resp.text

    assert started.wait(timeout=5), "scan never started"
    status_while_running = c.get("/api/scan/status").json()
    assert status_while_running["running"] is True

    release.set()

    deadline = time.monotonic() + 5
    status_after = status_while_running
    while time.monotonic() < deadline:
        status_after = c.get("/api/scan/status").json()
        if status_after["running"] is False:
            break
        time.sleep(0.02)

    assert status_after["running"] is False
    assert status_after["last_scan_completed_at"] is not None


def test_scan_status_tracks_new_jobs_and_matches_counts(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module
    from jobfinder.matcher.models import JobPosting, MatchResult

    app, c = authed_client

    def _build(db_arg, config, events):
        job = JobPosting(
            job_id="scan-job-1",
            title="QA Engineer",
            company="Acme",
            location="Remote",
            description="desc",
            url="https://example.com/scan-job-1",
            source="test",
        )

        class _FakeOrchestrator:
            def run_scan(self) -> None:
                db_arg.mark_seen([job])
                db_arg.save_match(MatchResult(job=job, score=80, reasoning="stub"))

        return _FakeOrchestrator()

    monkeypatch.setattr(scan_module, "_build_orchestrator", _build)

    resp = c.post("/api/scan")
    assert resp.status_code == 202, resp.text

    deadline = time.monotonic() + 5
    status = {}
    while time.monotonic() < deadline:
        status = c.get("/api/scan/status").json()
        if not status["running"]:
            break
        time.sleep(0.02)

    assert status["last_matches"] == 1
    assert status["last_new_jobs"] == 1


# ── T65: the sparse-state DoD needs the highest score the scan actually
#    found, even when nothing cleared the threshold. `scan_complete`
#    (Orchestrator.run_scan) now carries a `best_score` field; ScanState
#    must capture it off that event (not off the before/after match-count
#    diff this module already uses for new_jobs/matches, since a
#    below-threshold score never gets a saved match row to diff against).


def test_scan_status_reports_best_score_from_scan_complete_event(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module

    app, c = authed_client

    def _build(db_arg, config, events):
        class _FakeOrchestrator:
            def run_scan(self) -> None:
                events.put({"type": "scan_start"})
                # Nothing clears the threshold -- no save_match call at
                # all -- but the scan still found a 62% candidate.
                events.put({"type": "scan_complete", "new_jobs": 3, "matches": 0, "best_score": 62})

        return _FakeOrchestrator()

    monkeypatch.setattr(scan_module, "_build_orchestrator", _build)

    resp = c.post("/api/scan")
    assert resp.status_code == 202, resp.text

    deadline = time.monotonic() + 5
    status = {}
    while time.monotonic() < deadline:
        status = c.get("/api/scan/status").json()
        if not status["running"]:
            break
        time.sleep(0.02)

    assert status["last_best_score"] == 62

    state = c.get("/api/state").json()
    assert state["last_best_score"] == 62


def test_scan_status_best_score_is_null_when_scan_complete_omits_it(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module

    app, c = authed_client
    monkeypatch.setattr(scan_module, "_build_orchestrator", lambda db, config, events: _StubOrchestrator())

    resp = c.post("/api/scan")
    assert resp.status_code == 202, resp.text

    deadline = time.monotonic() + 5
    status = {}
    while time.monotonic() < deadline:
        status = c.get("/api/scan/status").json()
        if not status["running"]:
            break
        time.sleep(0.02)

    assert status["last_best_score"] is None


# ── pause / play ─────────────────────────────────────────────────────────


def test_pause_then_play_round_trips_scheduler_running_state(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module

    app, c = authed_client
    monkeypatch.setattr(scan_module, "_build_orchestrator", lambda db, config, events: _StubOrchestrator())

    resp = c.post("/api/scan/play")
    assert resp.status_code == 200, resp.text
    assert app.state.scheduler.is_running is True

    resp = c.post("/api/scan/pause")
    assert resp.status_code == 200, resp.text
    assert app.state.scheduler.is_running is False


# ── GET /api/state ────────────────────────────────────────────────────


def test_state_reports_scheduler_health_and_last_scan_timestamp(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module

    app, c = authed_client
    monkeypatch.setattr(scan_module, "_build_orchestrator", lambda db, config, events: _StubOrchestrator())

    initial_state = c.get("/api/state").json()
    assert initial_state["scheduler_alive"] is True
    assert initial_state["last_scan_completed_at"] is None

    resp = c.post("/api/scan")
    assert resp.status_code == 202, resp.text

    deadline = time.monotonic() + 5
    state = initial_state
    while time.monotonic() < deadline:
        state = c.get("/api/state").json()
        if state["last_scan_completed_at"] is not None:
            break
        time.sleep(0.02)

    assert state["scheduler_alive"] is True
    assert state["last_scan_completed_at"] is not None


def test_state_reflects_dead_scheduler(authed_client, monkeypatch) -> None:
    app, c = authed_client
    scheduler = app.state.scheduler

    # Kill the underlying APScheduler engine directly, bypassing
    # JobScheduler.shutdown() — the lifespan's own teardown calls
    # .shutdown() again on TestClient exit, which would raise
    # SchedulerNotRunningError on an already-stopped engine. Stubbing
    # .shutdown() to a no-op here is test plumbing only: it isolates
    # "is the engine actually alive" (what this test asserts) from
    # "did teardown run cleanly twice" (not what this test is about).
    scheduler._scheduler.shutdown(wait=False)
    monkeypatch.setattr(scheduler, "shutdown", lambda: None)

    resp = c.get("/api/state")
    assert resp.status_code == 200, resp.text
    assert resp.json()["scheduler_alive"] is False


# ── POST /api/reset ──────────────────────────────────────────────────────


def test_reset_empties_db_and_removes_profile_cache(authed_client) -> None:
    from jobfinder import paths
    from jobfinder.matcher.models import JobPosting, MatchResult

    app, c = authed_client
    db = app.state.db

    job = JobPosting(
        job_id="reset-job-1",
        title="QA Engineer",
        company="Acme",
        location="Remote",
        description="desc",
        url="https://example.com/reset-job-1",
        source="test",
    )
    db.mark_seen([job])
    db.save_match(MatchResult(job=job, score=80, reasoning="stub"))
    assert db.get_recent_matches()

    cache = paths.resume_profile_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}", encoding="utf-8")
    assert cache.exists()

    resp = c.post("/api/reset")
    assert resp.status_code == 200, resp.text

    assert db.get_recent_matches() == []
    assert not cache.exists()


# ── Auth enforcement across every endpoint ───────────────────────────────


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/api/scan"),
        ("post", "/api/scan/pause"),
        ("post", "/api/scan/play"),
        ("get", "/api/scan/status"),
        ("post", "/api/reset"),
        ("get", "/api/state"),
    ],
)
def test_endpoint_rejects_request_with_no_session_cookie(client, method, path) -> None:
    app, c = client
    resp = getattr(c, method)(path)
    assert resp.status_code == 401
