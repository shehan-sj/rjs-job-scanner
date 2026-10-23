"""Tests for the scan cycle's misfire policy.

The bug: ``JobScheduler`` was built with ``misfire_grace_time: 60``. Sixty
seconds is nothing next to the default twenty-minute interval on a machine
that sleeps — a laptop closed for an hour wakes up with the scan cycle's
``next_run_time`` an hour in the past, APScheduler declares the run missed,
*skips it*, and schedules the next one a full interval out. The skip is
announced on the ``apscheduler`` logger, which ``jobfinder/web/logging_setup.py``
never configures (it configures ``jobfinder``), so it reached neither the log
file nor the UI's log console: the scan silently did not happen.

These tests assert the policy on the real ``JobScheduler``'s engine and then
exercise it end to end by planting an already-overdue ``next_run_time``,
which is exactly the state a wake-from-sleep produces.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.interval import IntervalTrigger

from jobfinder.scheduler import JobScheduler


def _noop() -> None:
    pass


def test_job_defaults_allow_a_late_run_rather_than_dropping_it() -> None:
    """``misfire_grace_time`` must be ``None`` (infinite) and ``coalesce``
    on: a fire time that slipped should run *late*, once, not be skipped."""
    scheduler = JobScheduler(interval_minutes=20, scan_fn=_noop)
    defaults = scheduler._scheduler._job_defaults  # noqa: SLF001

    assert defaults["misfire_grace_time"] is None, (
        "a finite grace window silently drops the scan after a laptop sleeps past it"
    )
    assert defaults["coalesce"] is True, "a backlog of missed fire times must collapse into one scan, not N"
    assert defaults["max_instances"] == 1


def test_an_overdue_scan_cycle_still_runs() -> None:
    """The wake-from-sleep case, end to end: a ``next_run_time`` well in the
    past must still fire. With the old 60s grace this ran zero times."""
    ran = threading.Event()
    scheduler = JobScheduler(interval_minutes=20, scan_fn=ran.set)
    scheduler.start()
    try:
        # Bypass arm()/play() to plant the overdue state directly — this is
        # the post-sleep condition, not something a caller would ever ask for.
        scheduler._scheduler.add_job(  # noqa: SLF001
            scheduler._run_and_announce,  # noqa: SLF001
            trigger=IntervalTrigger(minutes=20),
            id="scan_cycle",
            name="Job scan cycle",
            next_run_time=datetime.now(timezone.utc) - timedelta(minutes=47),
        )
        assert ran.wait(timeout=5), "an overdue scan cycle was skipped instead of running late"
    finally:
        scheduler.shutdown()
