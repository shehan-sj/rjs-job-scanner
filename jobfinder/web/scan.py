"""Scan control + state snapshot (T19).

``POST /api/scan``        trigger an immediate scan cycle (one-shot — it
                          does **not** arm the periodic cycle)
``POST /api/scan/pause``  stop periodic scanning; persists
                          ``auto_scan_enabled: false``
``POST /api/scan/play``   (re)start periodic scanning + fire an immediate
                          scan; persists ``auto_scan_enabled: true``
``GET  /api/scan/status`` ``{"running": bool, ...}``
``POST /api/reset``       wipe job tables + the resume-profile cache
                          (ports ``main.py``'s ``_clear_db_and_profile``,
                          ``main.py:205-215``: ``db.clear_all()`` + unlink
                          the profile cache)
``GET  /api/state``       a full snapshot — scan running, last scan
                          time/counts, when the next periodic scan fires
                          (``next_scan_at``), and whether the scheduler
                          engine itself is alive

``GET /api/state`` exists because a client that loads mid-scan, or
reconnects after a dropped connection, otherwise sees nothing — free when
the widget *was* the process, not free now. The product spec
also requires the heartbeat this feeds: "a silently dead scheduler is a
defect, not a display state."

The real scan cycle (built by :func:`make_scan_fn`) runs on APScheduler's
background thread (``jobfinder/scheduler.py``), not the Uvicorn event
loop, so :class:`ScanState` guards its fields with a lock — the
status/state routes read it from the event loop thread concurrently with
a scan cycle writing to it from the scheduler thread.

:func:`_build_orchestrator` is a separate, patchable seam: tests replace
it with a stub that updates counts with no network access at all (T19's
DoD: "orchestrator stubbed — no network, no real scrape"), while
production wiring (``jobfinder/web/app.py``'s lifespan) uses the real
``Orchestrator``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from .. import paths
from ..config import Config, save_config
from ..orchestrator import Orchestrator
from ..scheduler import JobScheduler
from ..storage.database import Database
from .stream import BroadcastLogHandler, EventBroadcaster, ThreadsafeEventSink

logger = logging.getLogger("jobfinder.web.scan")

router = APIRouter(prefix="/api")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ScanState:
    """Thread-safe snapshot of scan activity, written from the
    APScheduler background thread and read from the Uvicorn event loop.
    """

    running: bool = False
    last_scan_started_at: str | None = None
    last_scan_completed_at: str | None = None
    last_new_jobs: int = 0
    last_matches: int = 0
    # T65 — the sparse-state DoD ("nothing above the threshold" must name
    # the highest score the scan actually found): the highest score among
    # every job *scored* this cycle, not just the ones that cleared
    # ``match_threshold`` and got a saved match row — those never had a
    # row to diff against, which is why this rides in on the
    # ``scan_complete`` event itself (see ``_BestScoreCapturingSink``
    # below) rather than being derived from ``db.get_recent_matches()``
    # the way ``last_new_jobs``/``last_matches`` are. ``None`` means no
    # job was scored at all this cycle (nothing fetched, or everything had
    # already been seen).
    last_best_score: int | None = None
    last_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def mark_started(self) -> None:
        with self._lock:
            self.running = True
            self.last_scan_started_at = _now_iso()
            self.last_error = None

    def mark_completed(self, *, new_jobs: int = 0, matches: int = 0, best_score: int | None = None) -> None:
        with self._lock:
            self.running = False
            self.last_scan_completed_at = _now_iso()
            self.last_new_jobs = new_jobs
            self.last_matches = matches
            self.last_best_score = best_score

    def mark_failed(self, error: str) -> None:
        with self._lock:
            self.running = False
            self.last_scan_completed_at = _now_iso()
            self.last_error = error

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "last_scan_started_at": self.last_scan_started_at,
                "last_scan_completed_at": self.last_scan_completed_at,
                "last_new_jobs": self.last_new_jobs,
                "last_matches": self.last_matches,
                "last_best_score": self.last_best_score,
                "last_error": self.last_error,
            }


def _build_orchestrator(db: Database, config: Config, events: ThreadsafeEventSink) -> Orchestrator:
    """Separate, patchable seam — see module docstring. ``events`` is the
    ``queue.Queue``-shaped adapter (T20, ``jobfinder/web/stream.py``) that
    bridges ``Orchestrator``'s tagged ``scan_start``/``scan_complete``/
    ``matches`` events onto the SSE broadcaster via
    ``loop.call_soon_threadsafe``.
    """
    return Orchestrator(config=config, db=db, tk_queue=events)


class _BestScoreCapturingSink:
    """Wraps the real ``ThreadsafeEventSink`` so ``_scan_fn`` below can read
    back the ``best_score`` the just-finished scan reported on its own
    ``scan_complete`` event (T65) — forwards every event unchanged, same
    shape ``Orchestrator`` already expects (``.put``/``.put_nowait``), and
    records nothing else.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.last_best_score: int | None = None

    def put(self, event: dict) -> None:
        if event.get("type") == "scan_complete":
            self.last_best_score = event.get("best_score")
        self._inner.put(event)

    def put_nowait(self, event: dict) -> None:
        self.put(event)


def make_scan_fn(
    db: Database,
    config: Config,
    scan_state: ScanState,
    broadcaster: EventBroadcaster,
    loop: asyncio.AbstractEventLoop,
):
    """Build the callable :class:`JobScheduler` invokes on its background
    thread. Derives new-jobs/matches counts by diffing match-history size
    before/after the scan — ``Orchestrator.run_scan`` doesn't return a
    structured result yet; this is a real, if indirect, count rather than
    a placeholder.

    Runs entirely on the APScheduler worker thread, never on the Uvicorn
    event loop — ``broadcaster``/``loop`` only cross threads via
    ``call_soon_threadsafe`` (``ThreadsafeEventSink``, ``BroadcastLogHandler``),
    which is why neither a dropped SSE client nor a scan running during
    process teardown can raise back into this function.
    """

    def _scan_fn() -> None:
        scan_state.mark_started()
        events = _BestScoreCapturingSink(ThreadsafeEventSink(broadcaster, loop))
        # Installed only for this scan cycle's duration — the broadcaster's
        # bounded ring buffer (jobfinder/web/stream.py) is the only place
        # "log" events accumulate, so there is no separate unbounded log
        # queue growing with no client attached (T20 DoD).
        log_handler = BroadcastLogHandler(broadcaster, loop)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        # T43: this used to temporarily raise the *root* logger's level for
        # the duration of the scan, because with no logging configuration
        # anywhere root was ``NOTSET`` and ``logger.info()`` got filtered by
        # the ``Logger`` before any handler saw it. The web app's lifespan
        # now configures the ``jobfinder`` logger at INFO
        # (jobfinder/web/logging_setup.py), so Orchestrator's progress
        # records pass their originating logger's level check and are then
        # handed to every ancestor handler — including this one on root —
        # irrespective of root's own level. Mutating root here is therefore
        # not merely redundant but harmful: it would stamp on a level the
        # operator may have set deliberately.
        try:
            before = len(db.get_recent_matches())
            orchestrator = _build_orchestrator(db, config, events)
            orchestrator.run_scan()
            after = len(db.get_recent_matches())
            new_matches = max(after - before, 0)
            scan_state.mark_completed(new_jobs=new_matches, matches=new_matches, best_score=events.last_best_score)
        except Exception as exc:
            logger.error("Scan cycle failed: %s", exc)
            scan_state.mark_failed(str(exc))
        finally:
            root_logger.removeHandler(log_handler)

    return _scan_fn


# ── Routes ──────────────────────────────────────────────────────────────


@router.post("/scan", status_code=202)
async def trigger_scan(request: Request) -> dict:
    scheduler: JobScheduler = request.app.state.scheduler
    await run_in_threadpool(scheduler.trigger_now)
    return {"status": "accepted"}


def _set_auto_scan_enabled(app_state, enabled: bool) -> None:
    """Persist the play/pause choice to ``config.yaml``.

    Pausing used to be in-memory only, so the pause died with the process
    and — far worse — so did the *playing* state: the schedule was armed
    only by a live click and every restart dropped back to no periodic job
    at all. The web app's lifespan reads this key back on boot
    (``jobfinder/web/app.py``), which is what makes play/pause a setting
    rather than a per-session gesture.

    Mutates the live ``Config`` in place rather than rebinding it, for the
    same reason ``config.sync_config`` exists: ``Orchestrator`` and the
    scan function hold a reference to this very object.

    A failure to write is logged, not raised. The scheduler has already
    been (un)armed by the time this runs, and refusing the user's
    play/pause with a 500 because the config file is read-only would be a
    worse outcome than a pause that does not survive restart.
    """
    config: Config | None = getattr(app_state, "config", None)
    if config is None:
        return
    config.auto_scan_enabled = enabled
    try:
        save_config(config, paths.config_path())
    except OSError as exc:
        logger.warning("Could not persist auto_scan_enabled=%s: %s", enabled, exc)


def _pause_sync(scheduler: JobScheduler, app_state) -> None:
    scheduler.pause()
    _set_auto_scan_enabled(app_state, False)


def _play_sync(scheduler: JobScheduler, app_state) -> None:
    # Persist *before* the immediate scan `play()` kicks off, so a scan that
    # takes minutes doesn't leave the flag unwritten for that whole window.
    _set_auto_scan_enabled(app_state, True)
    scheduler.play()


@router.post("/scan/pause")
async def pause_scan(request: Request) -> dict:
    scheduler: JobScheduler = request.app.state.scheduler
    await run_in_threadpool(_pause_sync, scheduler, request.app.state)
    return {"status": "ok"}


@router.post("/scan/play")
async def play_scan(request: Request) -> dict:
    scheduler: JobScheduler = request.app.state.scheduler
    await run_in_threadpool(_play_sync, scheduler, request.app.state)
    return {"status": "ok"}


@router.get("/scan/status")
async def scan_status(request: Request) -> dict:
    scan_state: ScanState = request.app.state.scan_state
    return scan_state.snapshot()


def _reset_sync(db: Database) -> None:
    db.clear_all()
    cache = paths.resume_profile_path()
    if cache.exists():
        try:
            cache.unlink()
        except OSError as exc:
            logger.warning("Could not delete profile cache: %s", exc)


@router.post("/reset")
async def reset(request: Request) -> dict:
    db: Database = request.app.state.db
    await run_in_threadpool(_reset_sync, db)
    return {"status": "ok"}


@router.get("/state")
async def state(request: Request) -> dict:
    scheduler: JobScheduler = request.app.state.scheduler
    scan_state: ScanState = request.app.state.scan_state
    snapshot = scan_state.snapshot()
    snapshot["scheduler_alive"] = scheduler.is_alive
    snapshot["scan_cycle_scheduled"] = scheduler.is_running
    # When the *next* periodic scan fires, ISO-8601, or None when nothing is
    # armed. `scan_cycle_scheduled` alone cannot distinguish "armed, first run
    # 20 minutes out" from "nothing scheduled" for a user watching a quiet
    # screen — which is the reading that made a working schedule look broken.
    next_run = scheduler.next_run_time
    snapshot["next_scan_at"] = next_run.isoformat() if next_run is not None else None
    return snapshot
