"""APScheduler wrapper for periodic scan cycles."""

from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class JobScheduler:
    def __init__(self, interval_minutes: int, scan_fn: Callable):
        self._scheduler = BackgroundScheduler(job_defaults={"max_instances": 1, "misfire_grace_time": 60})
        self._interval = interval_minutes
        self._scan_fn = scan_fn

    def start(self) -> None:
        """Start the APScheduler engine in paused state (no periodic job yet)."""
        self._scheduler.start()
        logger.info("Scheduler ready — press Play to begin scanning.")

    def play(self) -> None:
        """Start periodic scanning and fire an immediate scan now."""
        if not self._scheduler.get_job("scan_cycle"):
            self._scheduler.add_job(
                self._run_and_announce,
                trigger=IntervalTrigger(minutes=self._interval),
                id="scan_cycle",
                name="Job scan cycle",
            )
            logger.info("Auto-scan started — every %d minutes.", self._interval)
        self.trigger_now()

    def reschedule(self, interval_minutes: int) -> None:
        """Update the running scan cycle's interval in place.

        Settings changes call this so a new interval takes effect
        immediately instead of waiting for the next app restart (the
        job was previously only ever added once, in ``play()``, with
        the interval captured at construction time).
        """
        self._interval = interval_minutes
        if self._scheduler.get_job("scan_cycle"):
            self._scheduler.reschedule_job(
                "scan_cycle",
                trigger=IntervalTrigger(minutes=self._interval),
            )
            logger.info("Auto-scan rescheduled — every %d minutes.", self._interval)

    def pause(self) -> None:
        """Stop periodic scanning + unqueue any pending immediate scan.

        A scan that's already executing can't be cleanly aborted (it runs on
        a worker thread and does blocking HTTP calls), but we can prevent
        the next scheduled scan and drop a queued-but-not-yet-started
        immediate scan.
        """
        for job_id in ("scan_cycle", "scan_immediate"):
            try:
                if self._scheduler.get_job(job_id):
                    self._scheduler.remove_job(job_id)
            except Exception:
                pass
        logger.info("Auto-scan paused.")

    @property
    def is_running(self) -> bool:
        return self._scheduler.get_job("scan_cycle") is not None

    @property
    def is_alive(self) -> bool:
        """Whether the APScheduler engine itself is up — the product spec's
        heartbeat requirement ("the UI shows ... whether the scheduler is
        alive"; "a silently dead scheduler is a defect, not a display
        state"). Distinct from :attr:`is_running`, which only reflects
        whether the periodic scan job is currently scheduled (paused vs.
        playing) — this reflects whether the scheduler thread itself is
        functioning at all.
        """
        return bool(self._scheduler.running)

    def trigger_now(self) -> None:
        """Run a scan cycle immediately (in the scheduler thread pool)."""
        self._scheduler.add_job(
            self._run_and_announce,
            id="scan_immediate",
            name="Immediate scan",
            replace_existing=True,
        )

    def _run_and_announce(self) -> None:
        """Run the scan, then — if the scan is complete, this is the moment
        a quiet console is otherwise indistinguishable from a dead
        scheduler (the owner's report on T98: "don't see the log entry on
        the pausing loop until timer") — log when the next one will run.

        Only logs when ``scan_cycle`` is still scheduled. If automatic
        scanning has been paused, there is no next run, and claiming one
        would be worse than staying quiet.
        """
        self._scan_fn()
        job = self._scheduler.get_job("scan_cycle")
        if job is not None and job.next_run_time is not None:
            logger.info("Scan complete — next run at %s.", job.next_run_time.strftime("%Y-%m-%d %H:%M:%S"))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down.")
