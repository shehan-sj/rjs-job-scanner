"""Tests for JobScheduler.reschedule (T11).

Covers the scan-interval bug: previously `interval_minutes` was captured at
construction and `play()` only ever added the job once, so changing the
interval in settings had no effect until the app restarted. `reschedule()`
must update the *running* job's trigger in place, and the live-config path
(load_config -> sync_config -> scheduler.reschedule) must apply a new
interval without restarting anything.
"""

from __future__ import annotations

from jobfinder.config import Config, load_config, save_config, sync_config
from jobfinder.scheduler import JobScheduler


def _noop() -> None:
    pass


def test_reschedule_updates_running_job_interval() -> None:
    scheduler = JobScheduler(interval_minutes=30, scan_fn=_noop)
    scheduler.start()
    try:
        scheduler.play()

        scheduler.reschedule(5)

        job = scheduler._scheduler.get_job("scan_cycle")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 5 * 60
    finally:
        scheduler.shutdown()


def test_reschedule_before_play_updates_interval_without_adding_job() -> None:
    scheduler = JobScheduler(interval_minutes=30, scan_fn=_noop)
    scheduler.start()
    try:
        scheduler.reschedule(5)

        assert scheduler._scheduler.get_job("scan_cycle") is None
        assert scheduler._interval == 5
    finally:
        scheduler.shutdown()


def test_live_config_path_reschedules_without_restart(tmp_path) -> None:
    """Simulates main.py's `_apply_live_config`: settings are saved to disk,
    then `load_config` + `sync_config` refresh the in-memory `config`, and
    the scheduler is rescheduled off the fresh value — all without
    restarting the process or recreating the scheduler object.
    """
    config_path = tmp_path / "config.yaml"
    config = Config(check_interval_minutes=30)
    save_config(config, config_path)

    scheduler = JobScheduler(interval_minutes=config.check_interval_minutes, scan_fn=_noop)
    scheduler.start()
    try:
        scheduler.play()
        assert scheduler._scheduler.get_job("scan_cycle").trigger.interval.total_seconds() == 30 * 60

        # User changes the interval in Settings and saves.
        edited = load_config(config_path)
        edited.check_interval_minutes = 5
        save_config(edited, config_path)

        # This is exactly what main.py's _apply_live_config does on save.
        fresh = load_config(config_path)
        sync_config(config, fresh)
        scheduler.reschedule(config.check_interval_minutes)

        job = scheduler._scheduler.get_job("scan_cycle")
        assert job.trigger.interval.total_seconds() == 5 * 60
        assert config.check_interval_minutes == 5
    finally:
        scheduler.shutdown()
