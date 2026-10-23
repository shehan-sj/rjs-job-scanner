"""FastAPI application skeleton + lifespan (T14).

Threading model (contrast with main.py's docstring — the Qt app this
replaced, deleted in T33):

  Uvicorn event loop : serves requests and the lifespan protocol
  Daemon thread      : APScheduler worker pool (owned by ``JobScheduler``,
                        itself owned by this module's lifespan — not a
                        module-level global, and not touched by
                        ``signal.signal`` the way main.py:440-442 did for
                        the desktop app. Uvicorn already converts
                        SIGINT/SIGTERM into an ASGI ``lifespan.shutdown``
                        event, so the scheduler shuts down from this
                        lifespan's ``finally`` block with no handler of our
                        own to register or to leak.

``app.state.db`` / ``app.state.scheduler`` / ``app.state.loop`` /
``app.state.config`` are the lifespan-owned handles later tasks build on:
  - T16 routes DB access through ``run_in_threadpool`` off ``app.state.db``.
  - T17-T19 add the job/settings/scan endpoints that call into it, and T17
    reads ``app.state.config`` for the server-side tag-visibility filtering
    ``jobfinder/filters.py`` already implements (no reimplementation here).
  - T20 needs the captured event loop (``app.state.loop``) to push scan
    events from the APScheduler thread via ``loop.call_soon_threadsafe``
    (an ``asyncio.Queue`` is not thread-safe, so the producer thread can't
    just call ``put_nowait`` directly).

Serving the built SPA is wired here as of T34, via FastAPI's
``app.frontend("/", directory=..., fallback="index.html")`` (present as of
the 0.156.4 pin). It was deliberately left out until now because
``frontend/dist`` did not exist before Phase 3's Vite build, and mounting
a missing directory crashes at app creation.

Two properties of that mount are load-bearing rather than incidental:

  - It does **not** shadow the API. FastAPI checks registered path
    operations first and only then the frontend files, so ``/api/*`` keeps
    its real responses — including an honest 404 for an unknown endpoint
    and a 405 for ``GET /api/stop`` — instead of a 200 ``index.html``.
    ``tests/test_web_spa.py`` pins this rather than trusting the ordering.
  - The directory comes from ``paths.frontend_dist_dir()``, not from a
    literal relative path. The packaged app is launched from an arbitrary
    working directory, so a relative ``"frontend/dist"`` would resolve
    against wherever the user happened to be; the helper resolves through
    ``get_resource_dir()``, which is the repo root from source and
    ``sys._MEIPASS`` inside a one-dir PyInstaller bundle. This is the
    reason T33 kept ``paths.is_frozen()`` alive.

The process is started by ``jobfinder/launcher.py`` (``python -m
jobfinder``), which pre-binds the socket so ``create_app`` can be given
the real port — see that module for why the order cannot be otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .. import paths
from ..config import load_config
from ..scheduler import JobScheduler
from ..storage.database import Database
from .control import StopController
from .control import router as control_router
from .fillerbot import FillerSessionManager
from .fillerbot import router as fillerbot_router
from .jobs import router as jobs_router
from .logging_setup import configure_logging, teardown_logging
from .onboarding import router as onboarding_router
from .resume import router as resume_router
from .scan import ScanState, make_scan_fn
from .scan import router as scan_router
from .security import install_security
from .settings import router as settings_router
from .stream import EventBroadcaster
from .stream import router as stream_router

logger = logging.getLogger("jobfinder.web")

# Default port for the packaged app's single long-running server process.
# Not load-bearing for tests, which always pass an explicit port so the
# Host/Origin allowlists (jobfinder/web/security.py, T15) line up with
# whatever address TestClient/uvicorn actually binds.
DEFAULT_PORT = 8756


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    paths.setup_data_environment()
    # T43: file logging lives here now, not in main.py's Qt entrypoint —
    # before this, running the server standalone produced no log file and
    # a scan's progress lines survived only in attached SSE clients. Done
    # before load_config() so a config problem is itself logged to the
    # file. See jobfinder/web/logging_setup.py for why this configures the
    # ``jobfinder`` logger rather than root, and why it is refcounted.
    log_key = configure_logging()
    # Everything past this point is inside the logging teardown's
    # ``finally``, including the startup steps themselves: a config or DB
    # failure during boot must still release the log file handle, or on
    # Windows the open handle outlives the app and blocks deletion of the
    # data directory it lives in.
    try:
        config = load_config(paths.config_path())

        db = Database(str(paths.db_path()))
        scan_state = ScanState()
        broadcaster = EventBroadcaster()
        loop = asyncio.get_running_loop()
        # T19 wires the real scan cycle (jobfinder.orchestrator.Orchestrator)
        # in production. ``scheduler.start()`` below starts APScheduler in the
        # paused state (no job added), so this never actually fires unless
        # something calls POST /api/scan, /api/scan/play, or the periodic
        # trigger after a play — none of which happen just from booting the
        # app, which is what keeps this safe for every test that merely
        # constructs ``create_app()`` without touching those routes.
        #
        # ``loop`` is captured here, on the Uvicorn event loop thread, and
        # handed to the scan function that runs on APScheduler's *worker*
        # thread — that thread bridges tagged scan/log events back onto this
        # loop via ``loop.call_soon_threadsafe`` (jobfinder/web/stream.py),
        # since ``asyncio.Queue`` isn't thread-safe and can't be fed directly
        # from a background thread.
        scan_fn = make_scan_fn(db, config, scan_state, broadcaster, loop)
        scheduler = JobScheduler(interval_minutes=config.check_interval_minutes, scan_fn=scan_fn)
        scheduler.start()
        # Restore the schedule the user left armed. Without this the engine
        # came up with no periodic job on every single launch, so
        # `check_interval_minutes` described a cycle that only existed if
        # someone pressed "Resume automatic scanning" again by hand — and
        # "Scan now" (`POST /api/scan`) arms nothing, so the common path was
        # exactly one scan ever. `auto_scan_enabled` defaults to True, so a
        # fresh install and every config.yaml written before the flag existed
        # arm here too; only an explicit pause keeps it quiet.
        #
        # `arm()` rather than `play()`: this must not fire a scan just because
        # the process started (the app can be launched by the user opening the
        # UI, or restarted after a crash). The first periodic run lands one
        # interval out; `GET /api/state` reports `next_scan_at` so that wait
        # is visible rather than looking like nothing is scheduled.
        if config.auto_scan_enabled:
            scheduler.arm()
        else:
            logger.info("Auto-scan is paused (auto_scan_enabled: false) — press Play to resume.")

        # T30: owns the single active FillerBot session (if any) and the
        # reaper that ends an abandoned one — see jobfinder/web/fillerbot.py's
        # module docstring for the full lifecycle. One instance per app process,
        # matching "one session at a time".
        filler_sessions = FillerSessionManager()

        app.state.db = db
        app.state.scheduler = scheduler
        app.state.scan_state = scan_state
        app.state.broadcaster = broadcaster
        app.state.loop = loop
        app.state.config = config
        app.state.filler_sessions = filler_sessions

        logger.info("JobFinder web backend starting up (data dir: %s).", paths.get_data_dir())
        try:
            yield
        finally:
            scheduler.shutdown()
            # Ends any still-active FillerBot session and waits for its
            # Chrome to close — a process shutdown must not orphan it either.
            await filler_sessions.shutdown()
            logger.info("JobFinder web backend shut down.")
    finally:
        teardown_logging(log_key)


def create_app(port: int = DEFAULT_PORT, *, dev_cors_origin: str | None = None) -> FastAPI:
    """Build the FastAPI app.

    ``port`` must be the port this process will actually be served on: the
    Host/Origin allowlists installed by ``install_security`` (T15) are
    exact matches against ``127.0.0.1:<port>`` / ``localhost:<port>`` /
    ``[::1]:<port>``, so a mismatch here means every request gets rejected
    with 403 even from a legitimate same-machine browser tab.

    ``dev_cors_origin`` must stay ``None`` in a packaged build (the product spec) —
    it only exists so a local dev run can point it at the
    exact Vite dev-server origin (T21+).
    """
    from .. import __version__ as _version

    app = FastAPI(
        title="JobFinder",
        version=_version,
        lifespan=lifespan,
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    # T34: the explicit stop path. Created here, not in the
    # lifespan, because the launcher has to register its shutdown callback
    # on the app object *before* Uvicorn starts serving — by the time the
    # lifespan runs, a request could already be in flight.
    app.state.stop_controller = StopController()

    install_security(app, port=port, dev_cors_origin=dev_cors_origin)
    app.include_router(control_router)
    app.include_router(jobs_router)
    app.include_router(settings_router)
    app.include_router(resume_router)
    app.include_router(scan_router)
    app.include_router(stream_router)
    app.include_router(onboarding_router)
    app.include_router(fillerbot_router)

    # T34: the built SPA, mounted last so every API route above wins. The
    # mount is conditional because a source checkout that has never run
    # `npm run build` must still boot an API-only app — otherwise every
    # backend test would need Node installed. `check_dir=True` is then
    # safe: we have already established the directory is there, and we do
    # want a loud failure if it vanishes between the check and the mount
    # rather than a silently empty site.
    #
    # `fallback=None` rather than `fallback="index.html"`, which is a
    # deliberate choice and the opposite of the usual SPA advice. An
    # index.html fallback exists to rescue client-side deep links, and this
    # SPA has none: there is no router (no react-router in
    # frontend/package.json) and the only `history` call in the whole
    # codebase is `bootstrapAuth()` stripping the launch token off the
    # query string — the path is never rewritten. What the fallback *would*
    # do here is answer `GET /api/typo` with a 200 HTML shell for any
    # request whose Accept includes text/html, i.e. every address-bar
    # navigation, hiding genuine API 404s behind a page that looks like it
    # works. Measured, not assumed: see
    # tests/test_web_spa.py::test_html_accepting_request_to_an_unknown_api_path_is_still_a_404.
    #
    # If a client-side router is ever introduced, turn the fallback back on
    # *and* add an `/api/{rest:path}` catch-all that 404s, in the same
    # change — one without the other re-creates the masking above.
    dist_dir = paths.frontend_dist_dir()
    if (dist_dir / "index.html").is_file():
        # `/` still serves index.html with fallback off: StaticFiles'
        # directory-index lookup finds it.
        app.frontend("/", directory=dist_dir, fallback=None, check_dir=True)
    else:
        logger.warning(
            "No built frontend at %s — serving the API only. Run `npm run build` in frontend/ "
            "(or use the packaged build) to serve the UI.",
            dist_dir,
        )

    return app


def build_uvicorn_config(port: int = DEFAULT_PORT, *, dev_cors_origin: str | None = None) -> uvicorn.Config:
    """Bind 127.0.0.1 explicitly — never 0.0.0.0, never ``::`` (the product spec).
    ``port=0`` lets the OS assign a free ephemeral port,
    which is what tests use to avoid colliding with a real running
    instance; the single-instance-lock / "opens the existing instance
    instead of failing on a port collision" behaviour the product spec
    describes is a later task's job (the launcher doesn't exist yet).
    """
    return uvicorn.Config(
        create_app(port=port, dev_cors_origin=dev_cors_origin),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
