"""FillerBot engine — async task orchestrating browser + vision in a loop.

Converted off the Qt background-thread class and its signal/slot
mechanism by T29. Its ``run()`` method used to exist *only* to give
``browser.py``'s ``playwright.async_api`` code a private event loop off
the Qt main thread — under FastAPI that reason disappears, because the
Uvicorn event loop already exists and this engine now runs as a plain
``asyncio`` task on it (``jobfinder/web/fillerbot.py``, T30).

Because the engine and every caller of its control methods (``pause``,
``resume``, ``execute_fill``, ...) now live on the *same* event loop —
there is no second thread to cross into — the old poll-based
``_wait_unpause`` (a 0.2s ``asyncio.sleep`` loop) becomes a plain
``asyncio.Event``, and ``_override_values`` (previously mutated with no
lock from what was, in the Qt version, a different thread) becomes a
single-slot ``asyncio.Queue``. Neither needs a lock: everything here runs
on one loop, so "no lock" is now actually correct instead of merely
untested.

The 8 Qt signal/slot emissions become tagged dict events delivered through
an injected ``emit`` callback (an ``EventBroadcaster.publish``-shaped
callable — see ``jobfinder/web/fillerbot.py``) instead of being broadcast
via Qt's own mechanism:

  status                  {"type": "status", "message": str}
  analysis                {"type": "analysis", "analysis": PageAnalysis}
  action_executed          {"type": "action_executed", "index": int, "ok": bool}
                           ``index`` is the action's index in the
                           ``analysis`` event's ``actions`` array (the
                           engine's single index space, shared with the
                           ``execute_fill`` overrides dict) — *not* its
                           index among the non-click actions.
  fill_complete            {"type": "fill_complete"}
  needs_human              {"type": "needs_human", "message": str}
  resume_upload_prompt     {"type": "resume_upload_prompt", "request_id": str}
  error                    {"type": "error", "message": str}
  finished_session         {"type": "finished_session"}

``resume_upload_prompt`` is the one event that awaits a reply, so it
carries a correlated ``request_id``: :meth:`accept_upload` /
:meth:`skip_upload` must be called with that same id or they are rejected
(see their docstrings) — otherwise a stale reply to a *previous* prompt
(or a duplicate click) could silently resolve the wrong wait.

Two calls must never run on the event loop, because they are genuinely
blocking (not just "wrapped in ``async def``"): the synchronous Anthropic
SDK call ``PageAnalyzer.analyze`` and, inside ``browser.launch()``,
``subprocess.Popen`` plus the CDP socket-connect poll. Both are moved off
via ``fastapi.concurrency.run_in_threadpool`` — the former here, the
latter in ``browser.py`` itself (see that module's docstring for exactly
what changed there).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi.concurrency import run_in_threadpool

from .browser import FillerBrowser
from .models import FillAction, FillerProfile
from .vision import PageAnalyzer

logger = logging.getLogger(__name__)

EmitFn = Callable[[dict[str, Any]], None]


class FillerEngine:
    """Screenshot→analyze→fill loop, run as a single ``asyncio`` task.

    Call :meth:`start_session` once to arm it, then
    ``asyncio.create_task(engine.run())`` — see
    ``FillerSessionManager.start`` in ``jobfinder/web/fillerbot.py``, which
    owns that task and is the only thing that should construct this class
    in production.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        profile: FillerProfile,
        resume_path: str = "",
        chrome_exe: str = "",
        *,
        emit: EmitFn,
    ):
        self._emit = emit
        self._api_key = api_key
        self._model = model
        self._profile = profile
        self._resume_path = resume_path
        self._browser = FillerBrowser(chrome_exe=chrome_exe)
        self._analyzer = PageAnalyzer(api_key, model, profile)

        self._url: str = ""
        self._running = False
        self._unpause_event = asyncio.Event()
        self._pending_actions: list[FillAction] = []
        self._override_queue: asyncio.Queue[dict[int, str]] = asyncio.Queue(maxsize=1)
        self._do_next_page = False
        self._upload_accepted = False
        self._upload_skipped = False
        self._resume_uploaded = False
        self._pending_upload_request_id: str | None = None

    # ── Public control (called from the FastAPI route handlers) ──────────────
    # Every method here is synchronous and non-blocking — they only ever
    # flip flags / set the unpause event, so a route handler can call them
    # directly without an ``await`` or a threadpool hop.

    def start_session(self, url: str) -> None:
        self._url = url
        self._running = True
        self._unpause_event.clear()

    def pause(self) -> None:
        self._unpause_event.clear()
        self._emit({"type": "status", "message": "Paused"})

    def resume(self) -> None:
        self._unpause_event.set()

    def execute_fill(self, overrides: dict[int, str] | None = None) -> None:
        """User approved — execute the pending actions with optional edits."""
        if overrides:
            while not self._override_queue.empty():
                self._override_queue.get_nowait()
            self._override_queue.put_nowait(overrides)
        self._unpause_event.set()

    def stop_session(self) -> None:
        self._running = False
        self._unpause_event.set()

    def accept_upload(self, request_id: str | None = None) -> bool:
        """Returns False (no-op) if ``request_id`` doesn't match the
        currently-pending ``resume_upload_prompt`` — e.g. a stale/duplicate
        reply, or no prompt pending at all. Callers (the FastAPI route)
        should turn a False return into a 409.
        """
        if not self._upload_prompt_matches(request_id):
            return False
        self._upload_accepted = True
        self._unpause_event.set()
        return True

    def skip_upload(self, request_id: str | None = None) -> bool:
        """See :meth:`accept_upload` — same correlated-id contract."""
        if not self._upload_prompt_matches(request_id):
            return False
        self._upload_skipped = True
        self._unpause_event.set()
        return True

    def click_next(self) -> None:
        self._do_next_page = True
        self._unpause_event.set()

    def rescan_page(self) -> None:
        """User wants to re-analyze the current page."""
        self._unpause_event.set()

    # The one control that is *not* a flag flip: raising a window is a
    # round trip to the browser (CDP ``Page.bringToFront``), so unlike its
    # siblings above it has to be awaited. It is safe to await concurrently
    # with whatever the main loop is doing to the same page — both run on
    # this one event loop and ``Page.bringToFront`` touches no page state.
    async def bring_to_front(self) -> None:
        """Raise the automated Chrome window. Raises ``RuntimeError`` if the
        browser window is not open yet (the session's first moments, or
        after a launch failure)."""
        await self._browser.bring_to_front()

    def _upload_prompt_matches(self, request_id: str | None) -> bool:
        if self._pending_upload_request_id is None:
            return False
        if request_id is not None and request_id != self._pending_upload_request_id:
            return False
        return True

    # ── Task entry point ─────────────────────────────────────────────────────

    async def run(self) -> None:
        """Replaces the old Qt thread class's ``run()`` method. Owned and awaited by whatever calls
        ``asyncio.create_task(engine.run())`` (``FillerSessionManager``) —
        this coroutine no longer opens its own event loop, it just runs on
        whichever one it was scheduled on.
        """
        try:
            await self._main()
        except Exception as exc:
            logger.error("FillerBot engine error: %s", exc)
            self._emit({"type": "error", "message": str(exc)})
        finally:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._emit({"type": "finished_session"})

    async def _main(self) -> None:
        self._emit({"type": "status", "message": "Launching browser…"})
        await self._browser.launch()

        self._emit({"type": "status", "message": "Navigating…"})
        try:
            await self._browser.navigate(self._url)
        except Exception as exc:
            logger.warning("Navigation failed: %s", exc)
            self._emit(
                {
                    "type": "needs_human",
                    "message": f"Could not load page: {exc}\nFix the URL or navigate manually in the browser, then Re-scan.",
                }
            )
            await self._wait_unpause()
            if not self._running:
                return

        # ── Resume upload phase ──────────────────────────────────────────
        if self._running and self._resume_path and not self._resume_uploaded:
            await self._try_resume_upload()

        while self._running:
            # Screenshot for AI analysis (user sees the live browser)
            self._emit({"type": "status", "message": "Analyzing page…"})
            try:
                png = await self._browser.screenshot()
            except Exception as exc:
                logger.warning("Screenshot failed: %s", exc)
                self._emit(
                    {"type": "needs_human", "message": "Could not capture page. Wait for it to load, then Re-scan."}
                )
                await self._wait_unpause()
                if not self._running:
                    break
                continue

            # Read the DOM for all form fields — gives the AI unique selectors
            try:
                dom_fields = await self._browser.scan_form_fields()
            except Exception:
                dom_fields = None

            context = ""
            if self._resume_uploaded:
                context = (
                    "The candidate's resume was already uploaded and the page may have "
                    "autofilled some fields. Compare field values in the DOM field map "
                    "against the candidate's profile. ONLY return actions for fields "
                    "that are empty or have incorrect data. Skip correct fields."
                )
            # Blocking Anthropic SDK call — never on the event loop (T29).
            analysis = await run_in_threadpool(self._analyzer.analyze, png, context=context, dom_fields=dom_fields)
            # ``asdict`` — the event crosses into JSON (SSE) at the route
            # layer, which can't serialize a PageAnalysis dataclass as-is.
            self._emit({"type": "analysis", "analysis": asdict(analysis)})

            # Handle special page states
            if analysis.page_state in ("captcha", "login_required"):
                msg = (
                    f"Page requires {'CAPTCHA' if analysis.page_state == 'captcha' else 'login'}. "
                    f"Handle it in the browser window, then click Re-scan."
                )
                self._emit({"type": "needs_human", "message": msg})
                await self._wait_unpause()
                if not self._running:
                    break
                continue

            if analysis.page_state == "complete":
                self._emit({"type": "status", "message": "Application complete! Click Re-scan or Stop."})
                self._emit({"type": "fill_complete"})
                await self._wait_unpause()
                if not self._running:
                    break
                continue

            if analysis.page_state == "error":
                self._emit({"type": "status", "message": f"Error: {analysis.page_description}"})
                self._emit({"type": "fill_complete"})
                await self._wait_unpause()
                if not self._running:
                    break
                continue

            if analysis.next_step == "needs_human":
                self._emit({"type": "needs_human", "message": analysis.human_note or "Manual input needed."})
                await self._wait_unpause()
                if not self._running:
                    break
                continue

            # We have actions to perform
            self._pending_actions = analysis.actions
            fill_actions = [a for a in self._pending_actions if a.action_type != "click"]
            click_actions = [a for a in self._pending_actions if a.action_type == "click"]

            if not fill_actions:
                if analysis.next_step == "next_page" and click_actions:
                    self._emit({"type": "status", "message": "Moving to next page…"})
                    await self._browser.execute(click_actions[0])
                    await asyncio.sleep(1.5)
                    continue
                # No fields — wait for user to Re-scan or Stop
                self._emit(
                    {"type": "status", "message": "No fillable fields found. Click Re-scan after making changes."}
                )
                self._emit({"type": "fill_complete"})
                await self._wait_unpause()
                if not self._running:
                    break
                continue

            n = len(fill_actions)
            self._emit(
                {
                    "type": "status",
                    "message": f"Found {n} field{'s' if n != 1 else ''}. Edit values if needed, then click Fill.",
                }
            )
            await self._wait_unpause()
            if not self._running:
                break

            # Apply any user edits to action values (delivered via the
            # single-slot override queue — see execute_fill()).
            overrides: dict[int, str] = {}
            while not self._override_queue.empty():
                overrides.update(self._override_queue.get_nowait())
            for idx, new_val in overrides.items():
                if 0 <= idx < len(self._pending_actions):
                    self._pending_actions[idx].value = new_val

            # Re-filter after edits (fill actions are re-derived below,
            # paired with their unfiltered index).
            click_actions = [a for a in self._pending_actions if a.action_type == "click"]

            # Execute fill actions.
            #
            # ``index`` here is the action's position in
            # ``self._pending_actions`` — the *unfiltered* list — not its
            # position among the non-click actions. There is exactly one
            # index space on this engine: the ``analysis`` event's
            # ``actions`` array, the ``execute_fill`` overrides dict, and
            # ``action_executed.index`` all address the same list. Using
            # ``enumerate(fill_actions)`` instead (as the pre-T31 engine
            # and ``ui/fillerbot_window.py:404`` both did) silently
            # diverged the moment a ``click`` action was ordered before a
            # non-click one, so a UI keying its cards by the analysis
            # index got told "action 0 succeeded" when the field actually
            # filled was action 1.
            fill_items = [(i, a) for i, a in enumerate(self._pending_actions) if a.action_type != "click"]
            for position, (index, action) in enumerate(fill_items):
                if not self._running:
                    break
                self._emit({"type": "status", "message": f"Filling {position + 1}/{n}: {action.field_label}"})
                ok = await self._browser.execute(action, self._resume_path)
                self._emit({"type": "action_executed", "index": index, "ok": ok})

            # After filling, stay alive — wait for user to Re-scan, Next, or Stop
            if click_actions:
                self._emit({"type": "status", "message": "Fields filled. Click Next Page to continue, or Re-scan."})
            else:
                self._emit({"type": "status", "message": "Fields filled — review in the browser. Re-scan or Stop."})
            self._emit({"type": "fill_complete"})
            await self._wait_unpause()
            if not self._running:
                break

            # If user clicked Next Page, execute the click action
            if self._do_next_page and click_actions:
                self._do_next_page = False
                self._emit({"type": "status", "message": "Moving to next page…"})
                await self._browser.execute(click_actions[0])
                await asyncio.sleep(1.5)
            self._do_next_page = False

    async def _try_resume_upload(self) -> None:
        """Detect resume upload field, ask user, upload, wait for autofill."""
        self._emit({"type": "status", "message": "Checking for resume upload field…"})

        # Strategy 1: find <input type="file"> directly in the DOM
        file_inputs = await self._browser.find_file_inputs()
        resume_input_idx = None
        if file_inputs:
            for fi in file_inputs:
                hint = f"{fi['accept']} {fi['label']} {fi['nearby']}".lower()
                if any(kw in hint for kw in ("resume", "cv", "file", "upload", "document", "attach")):
                    resume_input_idx = fi["index"]
                    break
            if resume_input_idx is None:
                resume_input_idx = 0

        # Strategy 2: ask the AI (catches button-triggered uploads with no <input>)
        vision_upload_action = None
        if resume_input_idx is None:
            try:
                png = await self._browser.screenshot()
            except Exception:
                return
            analysis = await run_in_threadpool(
                self._analyzer.analyze,
                png,
                context=(
                    "Look for ANY UI element that lets the user upload a resume or CV. "
                    "This could be a file input, a button labeled 'Upload Resume', "
                    "'Attach CV', a drag-and-drop zone, or similar. "
                    "If you find one, return a single action: use action_type 'upload' "
                    "if it looks like a file input, or 'click' if it's a button that "
                    "would open a file dialog. "
                    "If there is no upload element at all, return an empty actions list."
                ),
            )
            upload_actions = [
                a
                for a in analysis.actions
                if a.action_type in ("upload", "click")
                and any(kw in a.field_label.lower() for kw in ("resume", "cv", "upload", "attach", "file", "document"))
            ]
            if upload_actions:
                vision_upload_action = upload_actions[0]

        if resume_input_idx is None and vision_upload_action is None:
            return

        # Ask the user — correlated with a fresh request id so a stale
        # accept_upload/skip_upload call (e.g. a duplicate click, or a
        # reply to a previous prompt) can't resolve this wait.
        request_id = uuid.uuid4().hex
        self._pending_upload_request_id = request_id
        self._emit({"type": "resume_upload_prompt", "request_id": request_id})
        await self._wait_unpause()
        self._pending_upload_request_id = None
        if not self._running:
            return

        if self._upload_skipped:
            self._upload_skipped = False
            return

        if self._upload_accepted:
            self._upload_accepted = False
            self._emit({"type": "status", "message": "Uploading resume…"})

            if resume_input_idx is not None:
                ok = await self._browser.upload_to_file_input(self._resume_path, resume_input_idx)
            else:
                ok = await self._browser.execute(vision_upload_action, self._resume_path)

            if ok:
                self._resume_uploaded = True
                self._emit({"type": "status", "message": "Resume uploaded — waiting for page to autofill…"})
                await asyncio.sleep(5)
            else:
                self._emit({"type": "status", "message": "Resume upload failed — continuing with manual fill."})

    async def _wait_unpause(self) -> None:
        """Block until some control method sets the unpause event.

        Replaces the old 0.2s ``asyncio.sleep`` poll — safe with no lock
        because the event and every caller of ``pause``/``resume``/etc.
        all run on the same event loop (T29).

        The event is cleared on *entry*, not after the wait: a control
        call that lands while the engine is between pause points
        (``rescan_page`` during "Analyzing page…", say) would otherwise
        leave it set and make the *next* pause point fall straight
        through. At the "Found N fields" pause that means filling the form
        without the user ever reviewing the values — the exact human
        check FillerBot exists to keep. The old Qt version was immune
        because it set ``_paused = True`` immediately before each wait,
        which discarded any earlier unpause; clearing here restores that.

        Nothing awaits between the clear and the wait, so no control call
        can slip in between them. ``stop_session`` is the one signal that
        must not be discarded, hence the ``_running`` check first —
        clearing the event it just set would hang the engine here.
        """
        if not self._running:
            return
        self._unpause_event.clear()
        await self._unpause_event.wait()
