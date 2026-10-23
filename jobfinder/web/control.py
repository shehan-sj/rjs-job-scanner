"""The explicit stop path (T34) — the product spec.

"The UI provides an explicit way to stop the server."

The scheduler keeps scanning after the browser tab closes (the product spec,
and the UI says so), so quitting has to be a deliberate action rather than
a side effect of closing a window. That makes this the one route whose
*effect* is valuable to an attacker who can never read the response: a
denial-of-service against the user's own scanning, triggerable from any
page they happen to have open.

Hence three deliberate choices, each with a test in
``tests/test_web_control.py``:

1. **POST, never GET.** A ``GET /api/stop`` would be reachable by
   ``<img src="http://127.0.0.1:8756/api/stop">`` on any website in the
   world — no CORS, no response read, no user interaction required, and
   browsers do not block sub-resource requests to loopback. Declaring only
   ``POST`` means a ``GET`` gets 405 before any handler runs.

2. **Behind the same two gates as every other mutating route** —
   ``jobfinder/web/security.py`` (T15). Because the path starts with
   ``/api/`` and is *not* in ``PUBLIC_PATHS``, ``SessionAuthMiddleware``
   requires the HttpOnly/SameSite=Strict/host-only session cookie; because
   ``POST`` is in ``STATE_CHANGING_METHODS``, ``OriginAllowlistMiddleware``
   requires an exact-match ``Origin``; and ``HostAllowlistMiddleware``
   rejects a rebound ``Host`` outright. No per-route decorator here can be
   forgotten, which is the point of the middleware being positional
   (``/api/*``) rather than opt-in.

   What that composes to, against a hostile page at ``https://evil.test``:
   the cookie is ``SameSite=Strict``, so a cross-site ``fetch``/form POST
   carries no cookie at all and dies at 401; if the attacker somehow had
   the cookie, the ``Origin`` header the browser attaches is
   ``https://evil.test`` and dies at 403; a ``no-cors`` form POST still
   sends ``Origin``, so it dies the same way; and a DNS-rebinding attack,
   which is the one way to make ``Origin`` look same-origin, is caught by
   the ``Host`` allowlist. The attacker's remaining option is to run code
   on the machine already, at which point they do not need this route.

3. **Shut down after the response, not during it.** The handler returns
   202 and schedules the shutdown as a Starlette background task.
   Flipping ``should_exit`` inline would race the response out the door
   and the UI would see a connection reset rather than a confirmation —
   indistinguishable, from the user's side, from a crash.

The actual shutdown mechanism is injected rather than imported: the
launcher (``jobfinder/launcher.py``) registers a callback that sets
``uvicorn.Server.should_exit``, which makes Uvicorn run the ASGI lifespan
shutdown — which is what stops the APScheduler thread and closes any live
FillerBot Chrome (``jobfinder/web/app.py``'s lifespan ``finally``). That
is what keeps the stop path from leaving an orphan process. A bare
``uvicorn`` dev run registers nothing and the route reports 501 instead of
claiming to have stopped a server that is still running.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

logger = logging.getLogger("jobfinder.web.control")

router = APIRouter(prefix="/api")


class StopController:
    """Holds the process-shutdown callback the launcher provides.

    Exists so this module never imports Uvicorn and the route never knows
    *how* the process dies: the same endpoint works under the packaged
    launcher, under a plain ``uvicorn`` invocation (reporting 501), and in
    tests (recording the call) with no branching in the handler.
    """

    def __init__(self) -> None:
        self._handler: Callable[[], None] | None = None
        self._requested = False

    def register(self, handler: Callable[[], None]) -> None:
        self._handler = handler

    @property
    def is_registered(self) -> bool:
        return self._handler is not None

    @property
    def stop_requested(self) -> bool:
        return self._requested

    def request_stop(self) -> bool:
        """Invoke the shutdown callback at most once; report whether one exists.

        Idempotent because the UI's button can be double-clicked and a
        second ``should_exit`` flip during an in-progress shutdown is at
        best noise and at worst a second teardown of the scheduler.
        """
        if self._handler is None:
            logger.warning("Stop requested but no shutdown handler is registered (not started via the launcher).")
            return False
        if self._requested:
            return True
        self._requested = True
        logger.info("Stop requested — shutting down.")
        self._handler()
        return True


@router.post("/stop", status_code=202)
async def stop_server(request: Request, background: BackgroundTasks) -> dict:
    """Stop this server process. POST only — see the module docstring."""
    controller: StopController = request.app.state.stop_controller
    if not controller.is_registered:
        raise HTTPException(
            status_code=501,
            detail="this server was not started by the JobFinder launcher and cannot stop itself",
        )
    background.add_task(controller.request_stop)
    return {"status": "stopping"}
