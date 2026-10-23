"""FillerBot session model and lifecycle (T30).

T29 converted ``FillerEngine`` from a ``QThread`` into a plain coroutine.
This module is what actually *owns* that coroutine as an ``asyncio.Task``
on the server loop, and turns its 8 tagged events / 9 inbound controls
into a small HTTP+SSE surface:

  inbound control        → endpoint
  ---------------------- -----------------------------------------
  start_session           POST   /api/fillerbot/session
  pause                    POST   /api/fillerbot/pause
  resume                   POST   /api/fillerbot/resume
  execute_fill             POST   /api/fillerbot/fill
  stop_session              POST   /api/fillerbot/stop
  accept_upload             POST   /api/fillerbot/upload/accept
  skip_upload               POST   /api/fillerbot/upload/skip
  click_next                POST   /api/fillerbot/next
  rescan_page                POST   /api/fillerbot/rescan

  Added by T31 (no Qt-era counterpart — the Qt window raised
  Chrome by owning it):

  bring_to_front          POST   /api/fillerbot/front
  (preflight)             GET    /api/fillerbot/profile
  (preflight)             PATCH  /api/fillerbot/profile/extras

The preflight pair ports the ``user_extras`` flow the Qt entry point ran
*before* constructing the engine (``main.py``'s ``_open_fillerbot`` →
``load_extras`` → overlay → ``missing_fields`` → ``MissingFieldsDialog`` →
``save_extras`` → overlay). Without it the web session started the engine on
a profile missing every supplemental field the user had ever typed, with no
missing-fields preflight at all. ``POST /session`` now performs the same
overlay itself, so the extras are applied whether or not the UI bothered to
call the preflight.

Since T50 that overlay is ``derived_profile.overlay_user_values``, and a
user's value **wins over a non-empty resume value** instead of only filling
gaps. ``PATCH /profile/extras`` itself is unchanged: same nine
promptable keys, same bounds, same PATCH-merge semantics.

  GET  /api/fillerbot/session   — {"active": bool, "session_id": str|None}
  GET  /api/fillerbot/events    — SSE stream of the active session's
                                   tagged events (status/analysis/
                                   action_executed/fill_complete/
                                   needs_human/resume_upload_prompt/
                                   error/finished_session), replaying the
                                   bounded ring buffer on connect exactly
                                   like ``jobfinder/web/stream.py``'s scan
                                   stream.

Session lifecycle decisions (DoD for T30):

* **One session at a time.** ``POST /api/fillerbot/session`` while one is
  already active (and its task hasn't finished) returns 409, not a queued
  second session — spinning up a second headed Chrome instance while the
  first is mid-fill would fight the same user for the same screen.

* **Tab close.** There is no websocket or keepalive here (SSE is
  one-directional; control is plain POSTs), so the server has no direct
  signal that a tab went away. It instead treats *silence* as the signal:
  every control call, every ``GET /session``, and every new SSE
  subscriber touches the session's ``last_activity`` clock. A background
  reaper (below) ends any session that's gone quiet past
  ``idle_timeout_seconds`` — this is also what "an abandoned session is
  reaped after a timeout" (T30 DoD) means in practice.

* **Refresh.** A refreshed tab just re-subscribes to ``GET /events``
  (replays the ring buffer so it doesn't lose anything already emitted)
  and re-polls ``GET /session`` for the current session id — the session
  and its browser are untouched by a refresh.

* **A second tab.** Opening fillerbot in a second tab while a session is
  active sees the same 409 from ``POST /session`` a retry would, and can
  instead call ``GET /session`` to discover the existing ``session_id``
  and ``GET /events`` to watch the same stream read-only — there is still
  only one browser, one engine, one source of truth.

* **What closes Chrome.** Exactly one place: ``FillerEngine.run()``'s
  ``finally`` block (``jobfinder/fillerbot/engine.py``), which always
  calls ``FillerBrowser.close()`` no matter how the engine's coroutine
  ends — a clean finish, an exception, an explicit ``stop_session()``, or
  a cancellation from :meth:`FillerSessionManager.stop` after a reap
  timeout. With no owning ``QThread`` anymore (T29), that ``finally`` is
  the *only* guaranteed teardown path, which is exactly why T30 exists:
  nothing else in this module ever calls ``_chrome_proc`` or the browser
  directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, field_validator

from ..config import Config
from ..derived_profile import FIELD_SHAPES, overlay_user_values, user_edits
from ..fillerbot.browser import find_chrome_exe
from ..fillerbot.engine import FillerEngine
from ..fillerbot.profiler import load_filler_profile
from ..fillerbot.user_extras import (
    PROMPTABLE_FIELDS,
    load_extras,
    missing_fields,
    save_extras,
)
from .stream import EventBroadcaster

logger = logging.getLogger("jobfinder.web.fillerbot")

router = APIRouter(prefix="/api/fillerbot")

# How long a session can go with no control call / status poll / SSE
# subscriber before the reaper ends it and closes its Chrome. Generous
# because a real human reviewing a page before clicking Fill can easily
# take minutes, but still finite — see module docstring.
_DEFAULT_IDLE_TIMEOUT_SECONDS = 15 * 60
_DEFAULT_REAP_INTERVAL_SECONDS = 30
# How long to wait for the engine's task to finish on its own (browser
# close included) after asking it to stop, before cancelling it outright.
_STOP_GRACE_SECONDS = 30

# Bound on a single supplemental value. These fields are a street address,
# a phone number, a URL — nothing legitimate is near this long, and the
# values end up inside the prompts sent to Anthropic, so an
# unbounded one is both a cost and an injection-surface problem. The
# allowlist (``PROMPTABLE_FIELDS``) bounds how *many* there can be.
_MAX_EXTRA_VALUE_LEN = 500

_PROMPTABLE_KEYS = {key for key, _label in PROMPTABLE_FIELDS}


@dataclass
class _ActiveSession:
    id: str
    engine: FillerEngine
    task: "asyncio.Task[None]"
    broadcaster: EventBroadcaster
    last_activity: float = field(default_factory=time.monotonic)


class FillerSessionManager:
    """Owns the single active :class:`FillerEngine` task, if any.

    One instance lives on ``app.state.filler_sessions`` for the lifetime
    of the app (``jobfinder/web/app.py``'s lifespan), so there is exactly
    one of these per server process — matching "one session at a time".
    """

    def __init__(
        self,
        *,
        idle_timeout_seconds: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
        reap_interval_seconds: float = _DEFAULT_REAP_INTERVAL_SECONDS,
    ) -> None:
        self._idle_timeout_seconds = idle_timeout_seconds
        self._reap_interval_seconds = reap_interval_seconds
        self._active: _ActiveSession | None = None
        self._reaper_task: "asyncio.Task[None] | None" = None

    @property
    def active(self) -> _ActiveSession | None:
        active = self._active
        if active is not None and active.task.done():
            # The engine finished (or crashed) on its own without anyone
            # calling stop() — don't keep reporting it as active.
            self._active = None
            return None
        return active

    def touch(self) -> None:
        if self._active is not None:
            self._active.last_activity = time.monotonic()

    async def start(
        self,
        *,
        url: str,
        api_key: str,
        model: str,
        profile,
        resume_path: str,
        chrome_exe: str,
    ) -> str:
        if self.active is not None:
            raise HTTPException(status_code=409, detail="a FillerBot session is already active")

        broadcaster = EventBroadcaster()
        engine = FillerEngine(
            api_key,
            model,
            profile,
            resume_path=resume_path,
            chrome_exe=chrome_exe,
            emit=broadcaster.publish,
        )
        engine.start_session(url)
        task = asyncio.create_task(engine.run())
        session_id = uuid.uuid4().hex
        self._active = _ActiveSession(id=session_id, engine=engine, task=task, broadcaster=broadcaster)

        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reap_loop())

        return session_id

    def require_active(self) -> _ActiveSession:
        active = self.active
        if active is None:
            raise HTTPException(status_code=404, detail="no active FillerBot session")
        self.touch()
        return active

    async def stop(self) -> None:
        """Idempotent: ends the active session if there is one, and always
        waits for (or forces) the engine's own teardown, which is the only
        place Chrome actually gets closed (see module docstring).
        """
        active = self._active
        self._active = None
        if active is None or active.task.done():
            return
        active.engine.stop_session()
        try:
            await asyncio.wait_for(active.task, timeout=_STOP_GRACE_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("FillerBot session %s did not stop in time — cancelling.", active.id)
            active.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await active.task
        except Exception as exc:
            logger.warning("FillerBot session %s ended with an error: %s", active.id, exc)

    async def _reap_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._reap_interval_seconds)
                active = self._active
                if active is None:
                    continue
                idle = time.monotonic() - active.last_activity
                if idle >= self._idle_timeout_seconds:
                    logger.warning("Reaping abandoned FillerBot session %s after %.0fs idle.", active.id, idle)
                    await self.stop()
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        """Called from the app lifespan's teardown so a process shutdown
        never leaves an orphaned Chrome behind either."""
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        await self.stop()


def _manager(request: Request) -> FillerSessionManager:
    return request.app.state.filler_sessions


# ── Request bodies ────────────────────────────────────────────────────────


class StartSessionIn(BaseModel):
    url: str


class FillIn(BaseModel):
    overrides: dict[int, str] = {}


class UploadReplyIn(BaseModel):
    request_id: str | None = None


class ExtrasIn(BaseModel):
    """A partial update of the supplemental-profile values.

    ``dict[str, str]`` is doing real work here: pydantic v2 does not coerce
    ints/bools/nulls into ``str``, so a non-string payload is rejected
    before any of this reaches ``save_extras`` (and therefore before it
    could reach an Anthropic prompt). The key allowlist below is the other
    half — this is deliberately *not* a mass assignment of arbitrary keys
    onto the stored YAML.
    """

    model_config = {"extra": "forbid"}

    values: dict[str, str]

    @field_validator("values")
    @classmethod
    def _only_promptable_bounded_values(cls, values: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(values) - _PROMPTABLE_KEYS)
        if unknown:
            raise ValueError(f"unknown profile field(s): {', '.join(unknown)}")
        for key, value in values.items():
            if len(value) > _MAX_EXTRA_VALUE_LEN:
                raise ValueError(f"{key} exceeds {_MAX_EXTRA_VALUE_LEN} characters")
            # Control characters would survive into a prompt and into the
            # YAML file; none of these fields has any use for them.
            if any(ch < " " and ch not in "\t" for ch in value):
                raise ValueError(f"{key} contains control characters")
        return {key: value.strip() for key, value in values.items()}


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/session")
async def session_status(request: Request) -> dict:
    manager = _manager(request)
    active = manager.active
    if active is None:
        return {"active": False, "session_id": None}
    manager.touch()
    return {"active": True, "session_id": active.id}


def _gather_session_inputs(request: Request) -> tuple[str, str, str, str]:
    """Synchronous, non-blocking gathering of config + Chrome discovery
    (``find_chrome_exe`` just lists a couple of directories). The
    genuinely blocking step — extracting/loading the FillerProfile, which
    can make an Anthropic call — is awaited separately via
    ``run_in_threadpool`` in :func:`start_session` below.
    """
    config: Config = request.app.state.config
    api_key = config.anthropic_api_key
    model = config.anthropic.model
    resume_path = config.resume.path
    # The Qt entry point refused to even open FillerBot on three
    # conditions (``main.py``'s ``_open_fillerbot``): the feature switch
    # off, no API key, no Chrome. The last two were already enforced here;
    # the switch was not, so a client that simply POSTed to this endpoint
    # could start a session the user had turned off. Enforced server-side
    # rather than by the UI hiding a button.
    if not config.fillerbot.enabled:
        raise HTTPException(status_code=400, detail="FillerBot is disabled in settings")
    if not api_key:
        raise HTTPException(status_code=400, detail="Anthropic API key is not configured")
    chrome_exe = find_chrome_exe() or ""
    if not chrome_exe:
        raise HTTPException(status_code=400, detail="no headed Chrome executable found")
    return api_key, model, resume_path, chrome_exe


@router.post("/session", status_code=201)
async def start_session(body: StartSessionIn, request: Request) -> dict:
    manager = _manager(request)
    if manager.active is not None:
        raise HTTPException(status_code=409, detail="a FillerBot session is already active")

    api_key, model, resume_path, chrome_exe = _gather_session_inputs(request)

    # Server-managed resume only (6) — resume_path
    # always comes from config.resume.path, never from this request body.
    profile = await run_in_threadpool(_load_profile_with_extras, resume_path, api_key, model)

    session_id = await manager.start(
        url=body.url,
        api_key=api_key,
        model=model,
        profile=profile,
        resume_path=resume_path,
        chrome_exe=chrome_exe,
    )
    return {"session_id": session_id}


def _load_profile_with_extras(resume_path: str, api_key: str, model: str):
    """Blocking. The engine's profile, with the user's saved values overlaid.

    **Behaviour change, T50.** This used to call ``merge_into_profile``,
    which wrote only onto *empty* fields — gap-filling, on the reasoning that
    "the resume stays authoritative where it actually said something". The product spec
    3.6 settles it the other way round: a user edit is never silently
    overwritten, not by a re-parse and not by a better extraction. So a user
    who corrected their phone number now gets the corrected one typed into
    forms, which is the entire point of letting them correct it.
    """
    profile = load_filler_profile(resume_path, api_key, model)
    overlay_user_values(profile, user_edits())
    return profile


def _profile_preflight_payload(resume_path: str, api_key: str, model: str) -> dict:
    """Blocking. What the missing-fields dialog needs to render."""
    profile = _load_profile_with_extras(resume_path, api_key, model)
    # Scoped to the promptable contact fields this endpoint has always been
    # about. T50 put the user's *matching*-field edits in the same YAML file;
    # they belong to the derived-profile surface, not to this dialog.
    extras = {key: value for key, value in load_extras(shapes=FIELD_SHAPES).items() if key in _PROMPTABLE_KEYS}
    return {
        "missing": [{"key": key, "label": label} for key, label in missing_fields(profile)],
        "promptable": [{"key": key, "label": label} for key, label in PROMPTABLE_FIELDS],
        # Saved values only. The resume-derived values are not echoed:
        # this endpoint exists to let the user top up what the resume
        # lacked, not to hand the whole extracted profile back over HTTP.
        "extras": extras,
        "full_name": profile.full_name,
    }


@router.get("/profile")
async def profile_preflight(request: Request) -> dict:
    """Which promptable fields are still missing, *before* a session
    starts. Gated identically to ``POST /session`` so a disabled/unconfigured
    FillerBot reports one consistent reason instead of passing the preflight
    and then failing at start.
    """
    api_key, model, resume_path, _chrome_exe = _gather_session_inputs(request)
    return await run_in_threadpool(_profile_preflight_payload, resume_path, api_key, model)


@router.patch("/profile/extras")
async def update_profile_extras(body: ExtrasIn, request: Request) -> dict:
    """Persist supplemental values via ``save_extras`` and report what is
    still missing.

    PATCH, not PUT: the body is merged over the stored values, so a dialog
    that only prompted for the two missing fields cannot wipe the seven the
    user filled in last month. Keys are allowlisted to ``PROMPTABLE_FIELDS``
    and values are bounded — see :class:`ExtrasIn`.
    """
    api_key, model, resume_path, _chrome_exe = _gather_session_inputs(request)

    def _save_and_report() -> dict:
        # ``shapes`` on both halves of this read-modify-write is load-bearing
        # since T50 put list-valued matching fields in the same file: without
        # it the read stringifies a stored ``skills`` list into "['a', 'b']"
        # and the write persists that, destroying a user edit to a field this
        # endpoint does not even touch.
        merged = {**load_extras(shapes=FIELD_SHAPES), **body.values}
        # ``save_extras`` already drops empty/whitespace values, which is
        # also how a field gets cleared: PATCH it to "".
        save_extras(merged, shapes=FIELD_SHAPES)
        return _profile_preflight_payload(resume_path, api_key, model)

    return await run_in_threadpool(_save_and_report)


@router.post("/pause")
async def pause(request: Request) -> dict:
    session = _manager(request).require_active()
    session.engine.pause()
    return {"status": "ok"}


@router.post("/resume")
async def resume(request: Request) -> dict:
    session = _manager(request).require_active()
    session.engine.resume()
    return {"status": "ok"}


@router.post("/fill")
async def fill(body: FillIn, request: Request) -> dict:
    session = _manager(request).require_active()
    session.engine.execute_fill(body.overrides)
    return {"status": "ok"}


@router.post("/stop")
async def stop_session(request: Request) -> dict:
    manager = _manager(request)
    if manager.active is None:
        return {"status": "ok"}
    manager.touch()
    await manager.stop()
    return {"status": "ok"}


@router.post("/upload/accept")
async def upload_accept(body: UploadReplyIn, request: Request) -> dict:
    session = _manager(request).require_active()
    if not session.engine.accept_upload(body.request_id):
        raise HTTPException(status_code=409, detail="no matching pending resume-upload prompt")
    return {"status": "ok"}


@router.post("/upload/skip")
async def upload_skip(body: UploadReplyIn, request: Request) -> dict:
    session = _manager(request).require_active()
    if not session.engine.skip_upload(body.request_id):
        raise HTTPException(status_code=409, detail="no matching pending resume-upload prompt")
    return {"status": "ok"}


@router.post("/next")
async def click_next(request: Request) -> dict:
    session = _manager(request).require_active()
    session.engine.click_next()
    return {"status": "ok"}


@router.post("/rescan")
async def rescan_page(request: Request) -> dict:
    session = _manager(request).require_active()
    session.engine.rescan_page()
    return {"status": "ok"}


@router.post("/front")
async def bring_to_front(request: Request) -> dict:
    """Raise the automated Chrome window above the user's other windows
    (T31). Together with the distinct window position/size/title prefix
    from ``fillerbot/browser.py``, this is the answer to "which of these
    two Chromes is the robot's?".
    """
    session = _manager(request).require_active()
    try:
        await session.engine.bring_to_front()
    except RuntimeError as exc:
        # The session exists but its browser window doesn't yet — a
        # transient state during launch, not a bad request.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok"}


async def _event_stream(broadcaster: EventBroadcaster) -> AsyncIterator[ServerSentEvent]:
    q, replay = broadcaster.subscribe()
    try:
        for event in replay:
            yield ServerSentEvent(data=event, id=str(event["seq"]))
        while True:
            event = await q.get()
            yield ServerSentEvent(data=event, id=str(event["seq"]))
    finally:
        broadcaster.unsubscribe(q)


def _require_active_dep(request: Request) -> _ActiveSession:
    """A dependency, not a plain call inside the generator body: an SSE
    path operation's response has already started streaming by the time
    its generator body runs its first line, so raising 404 from *inside*
    the generator surfaces as a mid-stream server error instead of a clean
    404. Resolving it as a FastAPI dependency runs — and can raise —
    before that response begins.
    """
    return _manager(request).require_active()


@router.get("/events", response_class=EventSourceResponse)
async def events(session: _ActiveSession = Depends(_require_active_dep)) -> AsyncIterator[ServerSentEvent]:
    async for event in _event_stream(session.broadcaster):
        yield event
