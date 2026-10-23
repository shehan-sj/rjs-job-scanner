"""Tests for jobfinder.web.app (T14) — FastAPI skeleton + lifespan.

Scheduler and Database are owned by the lifespan context, not module
globals. These tests boot the app through
``TestClient`` as a context manager, which drives the real ASGI lifespan
protocol (startup then shutdown) exactly as Uvicorn would on SIGINT.
"""

from __future__ import annotations

import importlib
import threading

import pytest
from fastapi.testclient import TestClient

# Arbitrary fixed port for these tests: jobfinder.web.security (T15)
# allowlists the Host/Origin headers against the exact port create_app()
# is given, so TestClient's base_url below must use this same value.
PORT = 8757
BASE_URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    """A fresh app bound to an isolated, throwaway data dir (NG-4: no cleanup
    needed, pytest's tmp_path handles it), mirroring tests/test_paths.py's
    pattern for JOBFINDER_DATA_DIR isolation.
    """
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))

    import jobfinder.paths as paths

    importlib.reload(paths)

    import jobfinder.web.app as web_app

    importlib.reload(web_app)

    yield web_app

    importlib.reload(paths)


def test_health_check_returns_200(isolated_app) -> None:
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_lifespan_captures_running_loop_for_future_sse_use(isolated_app) -> None:
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as client:
        client.get("/api/health")
        assert app.state.loop is not None


def test_scheduler_shuts_down_with_no_orphan_thread_after_lifespan_exit(isolated_app) -> None:
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as client:
        client.get("/api/health")
        assert app.state.scheduler is not None
        assert any(t.name == "APScheduler" for t in threading.enumerate())

    # Lifespan shutdown has run (TestClient's __exit__ tears down the ASGI
    # lifespan) — the background scheduler thread must be gone, not merely
    # unreferenced. JobScheduler.shutdown() joins the thread before
    # returning (apscheduler.schedulers.background.BackgroundScheduler.
    # shutdown), so this assertion holds immediately, with no polling/sleep.
    assert not any(t.name == "APScheduler" for t in threading.enumerate())


def test_frontend_is_not_mounted_yet(isolated_app) -> None:
    """dist/ does not exist until T21 (Phase 3) — asserts T14 did not
    hand-roll a catch-all StaticFiles(html=True) mount that would 404 or
    misbehave for every unknown path today.
    """
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.get("/some/unmounted/frontend/route")
    assert resp.status_code == 404
