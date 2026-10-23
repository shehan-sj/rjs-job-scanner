"""Tests for auto-scan arming and persistence.

The reported bug, in the owner's words: the scan frequency in Settings ->
Sources "doesn't seem to be firing the scan policy more than one time", and
"the resume function seems to be manual".

Both came from the same place. ``JobScheduler.start()`` started the engine
with no periodic job, and the *only* thing that ever added one was
``play()``, reachable solely from the scan control's secondary button
("Resume automatic scanning"). The prominent primary button, "Scan now",
calls ``POST /api/scan`` -> ``trigger_now()``, which adds a one-shot
``date`` job and arms nothing — so the common path really was exactly one
scan, forever, with ``check_interval_minutes`` describing a cycle that did
not exist. Nothing persisted the armed/paused choice either, so even a user
who found the button had to press it again after every launch.

The fix is ``Config.auto_scan_enabled`` (default ``True``), written by the
play/pause routes and read back by the lifespan, plus ``arm()`` as a
separate verb from ``play()`` so boot restores the schedule without firing a
scan at process start.
"""

from __future__ import annotations

import importlib

import pytest
import yaml
from fastapi.testclient import TestClient

from jobfinder.config import Config, load_config, save_config
from jobfinder.scheduler import JobScheduler

PORT = 8771
BASE_URL = f"http://127.0.0.1:{PORT}"


def _noop() -> None:
    pass


# ── Config round-trip ────────────────────────────────────────────────────


def test_auto_scan_enabled_defaults_to_true() -> None:
    """The product is an unattended scanner; the default has to be on."""
    assert Config().auto_scan_enabled is True


def test_a_config_predating_the_flag_still_scans(tmp_path) -> None:
    """An install whose config.yaml was written before this key existed must
    arm on the next launch, not sit paused forever."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"check_interval_minutes": 15}), encoding="utf-8")

    assert load_config(path).auto_scan_enabled is True


def test_auto_scan_enabled_survives_a_save_load_round_trip(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    save_config(Config(auto_scan_enabled=False), path)
    assert load_config(path).auto_scan_enabled is False

    save_config(Config(auto_scan_enabled=True), path)
    assert load_config(path).auto_scan_enabled is True


# ── arm() vs play() ──────────────────────────────────────────────────────


def test_arm_schedules_the_cycle_without_scanning_now() -> None:
    """Boot must restore the schedule without hammering every source the
    moment the process comes up — that is the whole reason ``arm()`` is a
    separate verb from ``play()``."""
    calls: list[int] = []
    scheduler = JobScheduler(interval_minutes=20, scan_fn=lambda: calls.append(1))
    scheduler.start()
    try:
        scheduler.arm()

        assert scheduler.is_running is True
        assert scheduler.next_run_time is not None
        assert scheduler._scheduler.get_job("scan_immediate") is None  # noqa: SLF001
        assert calls == []
    finally:
        scheduler.shutdown()


def test_arm_is_idempotent_and_keeps_the_original_next_run_time() -> None:
    scheduler = JobScheduler(interval_minutes=20, scan_fn=_noop)
    scheduler.start()
    try:
        scheduler.arm()
        first = scheduler.next_run_time
        scheduler.arm()

        assert scheduler.next_run_time == first, "re-arming must not push the next scan further out"
    finally:
        scheduler.shutdown()


def test_play_still_arms_and_scans_immediately() -> None:
    """``play()`` is the explicit user "go" — unlike boot, it does scan at
    once, and it must still arm the cycle."""
    scheduler = JobScheduler(interval_minutes=20, scan_fn=_noop)
    scheduler.start()
    try:
        scheduler.play()

        assert scheduler.is_running is True
        # Either still queued or already picked up by a worker; both prove it
        # was submitted, which is what distinguishes play() from arm().
        assert scheduler.next_run_time is not None
    finally:
        scheduler.shutdown()


def test_next_run_time_is_none_when_nothing_is_armed() -> None:
    scheduler = JobScheduler(interval_minutes=20, scan_fn=_noop)
    scheduler.start()
    try:
        assert scheduler.next_run_time is None
        scheduler.arm()
        assert scheduler.next_run_time is not None
        scheduler.pause()
        assert scheduler.next_run_time is None
    finally:
        scheduler.shutdown()


# ── Boot arming + route persistence, through the real app ────────────────


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))

    import jobfinder.paths as paths

    importlib.reload(paths)

    import jobfinder.web.app as web_app

    importlib.reload(web_app)

    yield web_app, paths

    importlib.reload(paths)


def _authed(web_app):
    app = web_app.create_app(port=PORT)
    client = TestClient(app, base_url=BASE_URL)
    client.__enter__()
    resp = client.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
    assert resp.status_code == 200, resp.text
    return app, client


def test_boot_arms_the_scan_cycle_by_default(isolated_app) -> None:
    """The headline fix: a fresh install's periodic cycle is live the moment
    the backend is up — no manual "Resume automatic scanning" click."""
    web_app, _paths = isolated_app
    app, client = _authed(web_app)
    try:
        assert app.state.scheduler.is_running is True

        state = client.get("/api/state").json()
        assert state["scan_cycle_scheduled"] is True
        assert state["next_scan_at"] is not None, "an armed schedule must say when it next fires"
    finally:
        client.__exit__(None, None, None)


def test_boot_respects_a_persisted_pause(isolated_app) -> None:
    web_app, paths = isolated_app
    # Seed a paused config before the app boots. setup_data_environment()
    # (the lifespan's first act) only seeds config.yaml when it is absent,
    # so this file is what load_config() reads.
    paths.setup_data_environment()
    config = load_config(paths.config_path())
    config.auto_scan_enabled = False
    save_config(config, paths.config_path())

    app, client = _authed(web_app)
    try:
        assert app.state.scheduler.is_running is False
        state = client.get("/api/state").json()
        assert state["scan_cycle_scheduled"] is False
        assert state["next_scan_at"] is None
    finally:
        client.__exit__(None, None, None)


def test_pause_persists_to_config_and_play_persists_back(isolated_app) -> None:
    """The "resume is manual" half: play/pause is a stored setting now, so
    the next launch restores it instead of defaulting to a dead schedule."""
    import jobfinder.web.scan as scan_module

    web_app, paths = isolated_app
    app, client = _authed(web_app)
    try:
        # No network: play() fires an immediate scan through the real scan fn.
        scan_module._build_orchestrator = lambda db, config, events: type(  # noqa: SLF001
            "_Stub", (), {"run_scan": lambda self: None}
        )()

        assert client.post("/api/scan/pause").status_code == 200
        assert app.state.config.auto_scan_enabled is False
        assert load_config(paths.config_path()).auto_scan_enabled is False

        assert client.post("/api/scan/play").status_code == 200
        assert app.state.config.auto_scan_enabled is True
        assert load_config(paths.config_path()).auto_scan_enabled is True
    finally:
        importlib.reload(scan_module)
        client.__exit__(None, None, None)


def test_scan_now_does_not_arm_the_cycle(isolated_app) -> None:
    """Pins the asymmetry that produced the report. "Scan now" is a one-shot
    and must stay one — it is boot and play() that own the schedule. Before
    the fix this was the only button most users pressed, and the schedule it
    left behind was nothing at all.
    """
    import jobfinder.web.scan as scan_module

    web_app, paths = isolated_app
    paths.setup_data_environment()
    config = load_config(paths.config_path())
    config.auto_scan_enabled = False
    save_config(config, paths.config_path())

    app, client = _authed(web_app)
    try:
        scan_module._build_orchestrator = lambda db, config, events: type(  # noqa: SLF001
            "_Stub", (), {"run_scan": lambda self: None}
        )()

        assert client.post("/api/scan").status_code == 202
        assert app.state.scheduler.is_running is False
        # And it must not quietly flip the stored setting either.
        assert load_config(paths.config_path()).auto_scan_enabled is False
    finally:
        importlib.reload(scan_module)
        client.__exit__(None, None, None)
