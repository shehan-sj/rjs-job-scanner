"""T98 (b): the scheduler never logged that it was waiting.

The owner's complaint: "don't see the log entry on the pausing loop until
timer." ``scheduler.py`` logged when a scan ran and when the cycle was
paused or rescheduled, but nothing when the loop went back to sleep
between scans — so a quiet console was indistinguishable from a dead
scheduler.

This asserts against the log records the scheduler itself emits
(``caplog``), not against a rendered console: after a scan completes with
the cycle scheduled, exactly one INFO record names the next run time; with
automatic scanning paused, no such record appears — saying there's a next
run when there isn't would be worse than silence.
"""

from __future__ import annotations

import logging

from jobfinder.scheduler import JobScheduler


def _noop() -> None:
    pass


def _next_run_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if "next run" in r.getMessage().lower()]


def test_next_run_is_logged_after_a_scan_completes_with_the_cycle_scheduled(caplog) -> None:
    scheduler = JobScheduler(interval_minutes=30, scan_fn=_noop)
    scheduler.start()
    try:
        scheduler.play()
        caplog.set_level(logging.INFO, logger="jobfinder.scheduler")
        caplog.clear()

        # Exercise the same scan-completion path a real "scan_cycle" /
        # "scan_immediate" job fires, without waiting on the background
        # thread's real-time schedule.
        scheduler._run_and_announce()

        matches = _next_run_records(caplog.records)
        assert len(matches) == 1, f"expected exactly one 'next run' log line, got {caplog.records}"
        assert matches[0].levelno == logging.INFO
    finally:
        scheduler.shutdown()


def test_next_run_is_not_logged_when_automatic_scanning_is_paused(caplog) -> None:
    scheduler = JobScheduler(interval_minutes=30, scan_fn=_noop)
    scheduler.start()
    try:
        scheduler.play()
        scheduler.pause()
        caplog.set_level(logging.INFO, logger="jobfinder.scheduler")
        caplog.clear()

        scheduler._run_and_announce()

        matches = _next_run_records(caplog.records)
        assert not matches, f"paused scheduler should not claim a next run, got {matches}"
    finally:
        scheduler.shutdown()


def test_next_run_is_not_logged_before_the_cycle_is_ever_played(caplog) -> None:
    """Before ``play()`` there is no ``scan_cycle`` job either — same
    "no next run exists" case as paused, reached a different way."""
    scheduler = JobScheduler(interval_minutes=30, scan_fn=_noop)
    scheduler.start()
    try:
        caplog.set_level(logging.INFO, logger="jobfinder.scheduler")
        caplog.clear()

        scheduler._run_and_announce()

        matches = _next_run_records(caplog.records)
        assert not matches
    finally:
        scheduler.shutdown()
