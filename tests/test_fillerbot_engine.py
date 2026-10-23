"""Tests for jobfinder.fillerbot.engine (T29) — the QThread→asyncio rewrite.

No live job site anywhere: the end-to-end test drives a real headed
Chromium (installed under Playwright for this project) against a local
``file://`` fixture page; every other test here replaces the browser
with a lightweight fake, and the Anthropic call is always monkeypatched
(``vision.anthropic.Anthropic``) — never a real key, never a real
network call.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time

import pytest

from jobfinder.fillerbot import vision as vision_mod
from jobfinder.fillerbot.browser import find_chrome_exe
from jobfinder.fillerbot.engine import FillerEngine
from jobfinder.fillerbot.models import FillerProfile


def run(coro):
    return asyncio.run(coro)


# ── Fakes ─────────────────────────────────────────────────────────────────


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeBrowser:
    """Stands in for FillerBrowser for engine-logic tests that don't need
    a real browser at all — assigned directly onto ``engine._browser``
    (same test seam ``tests/test_fillerbot_security.py`` already uses for
    ``_page``).
    """

    def __init__(self) -> None:
        self.closed = False
        self.navigated_to = None
        self.executed: list = []

    async def launch(self) -> None:
        return None

    async def navigate(self, url: str) -> None:
        self.navigated_to = url

    async def screenshot(self) -> bytes:
        return b"fake-png"

    async def scan_form_fields(self) -> list:
        return []

    async def find_file_inputs(self) -> list:
        return []

    async def execute(self, action, resume_path: str = "") -> bool:
        self.executed.append((action, resume_path))
        return True

    async def upload_to_file_input(self, resume_path: str, index: int = 0) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


def _make_engine(*, resume_path: str = "", emit=None) -> tuple[FillerEngine, list[dict]]:
    events: list[dict] = []
    emit_fn = emit or events.append
    engine = FillerEngine(
        "fake-key",
        "fake-model",
        FillerProfile(full_name="Jane Doe"),
        resume_path=resume_path,
        chrome_exe="fake-chrome.exe",
        emit=emit_fn,
    )
    engine._browser = _FakeBrowser()  # noqa: SLF001 — intentional test seam
    return engine, events


def _sequential_anthropic_client(monkeypatch, responses: list[str]) -> list[dict]:
    calls: list[dict] = []
    remaining = list(responses)

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            text = remaining.pop(0) if remaining else remaining[-1] if remaining else responses[-1]
            return _FakeResponse(text)

    class _Client:
        def __init__(self, *a, **k) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(vision_mod.anthropic, "Anthropic", _Client)
    return calls


_COMPLETE_JSON = (
    '{"page_state": "complete", "page_description": "", "actions": [], "next_step": "done", "human_note": ""}'
)
_FILL_FULL_NAME_JSON = (
    '{"page_state": "form", "page_description": "", "actions": ['
    '{"action_type": "fill", "selector_strategy": "id", "selector_value": "full_name", '
    '"value": "Jane Doe", "field_label": "Full name", "confidence": 1.0, "source_field": "full_name", '
    '"field_index": 0}], "next_step": "fill", "human_note": ""}'
)


# ── grep contract (T29 DoD) ─────────────────────────────────────────────


def test_fillerbot_source_has_no_qt_imports() -> None:
    import pathlib

    root = pathlib.Path("jobfinder/fillerbot")
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "QThread" not in text, path
        assert "Signal" not in text, path


# ── Override queue (replaces the unlocked dict) ─────────────────────────


def test_execute_fill_overrides_are_applied_to_pending_actions(monkeypatch):
    _sequential_anthropic_client(monkeypatch, [_FILL_FULL_NAME_JSON, _COMPLETE_JSON])
    engine, events = _make_engine()
    engine.start_session("file:///fake.html")

    async def driver():
        task = asyncio.create_task(engine.run())
        # Wait for the "found fields, edit then Fill" pause.
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(e["type"] == "status" and "Found 1 field" in e.get("message", "") for e in events):
                break
        engine.execute_fill({0: "Jane Q. Doe"})
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(e["type"] == "fill_complete" for e in events):
                break
        engine.stop_session()
        await task

    run(driver())

    executed_values = [action.value for action, _ in engine._browser.executed]  # noqa: SLF001
    assert executed_values == ["Jane Q. Doe"]
    assert engine._browser.closed is True  # noqa: SLF001


# ── Single index space: action_executed.index == index in _pending_actions ──


_CLICK_THEN_FILL_JSON = (
    '{"page_state": "form", "page_description": "", "actions": ['
    '{"action_type": "click", "selector_strategy": "text", "selector_value": "Expand details", '
    '"value": "", "field_label": "Expand details", "confidence": 1.0, "source_field": "", '
    '"field_index": 0},'
    '{"action_type": "fill", "selector_strategy": "id", "selector_value": "full_name", '
    '"value": "Jane Doe", "field_label": "Full name", "confidence": 1.0, "source_field": "full_name", '
    '"field_index": 0}], "next_step": "fill", "human_note": ""}'
)


def test_action_executed_index_is_the_unfiltered_pending_action_index(monkeypatch):
    """A click ordered *before* a fill is what separates the two index
    spaces. The engine filters clicks out before executing, so the old
    ``enumerate(fill_actions)`` reported index 0 for the fill — while
    ``execute_fill``'s overrides dict indexes the unfiltered
    ``_pending_actions``, where that same fill is index 1. One number had
    to mean two different actions, so a UI keying its cards by the
    ``analysis`` event's ``actions`` array marked the wrong card.
    """
    _sequential_anthropic_client(monkeypatch, [_CLICK_THEN_FILL_JSON, _COMPLETE_JSON])
    engine, events = _make_engine()
    engine.start_session("file:///fake.html")

    async def driver():
        task = asyncio.create_task(engine.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(e["type"] == "status" and "Found 1 field" in e.get("message", "") for e in events):
                break
        # Override at the unfiltered index — the index space the
        # ``analysis`` event's actions array uses.
        engine.execute_fill({1: "Jane Q. Doe"})
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(e["type"] == "fill_complete" for e in events):
                break
        engine.stop_session()
        await task

    run(driver())

    # The override landed on the fill action (index 1, unfiltered) ...
    executed_values = [action.value for action, _ in engine._browser.executed]  # noqa: SLF001
    assert executed_values == ["Jane Q. Doe"]

    # ... and action_executed reports that *same* index, not 0.
    executed_events = [e for e in events if e["type"] == "action_executed"]
    assert executed_events == [{"type": "action_executed", "index": 1, "ok": True}]

    # The index is a valid cursor into the analysis event's actions array.
    analysis_event = next(e for e in events if e["type"] == "analysis")
    actions = analysis_event["analysis"]["actions"]
    assert actions[executed_events[0]["index"]]["field_label"] == "Full name"


# ── resume_upload_prompt correlated request id ───────────────────────────


def test_accept_upload_rejects_mismatched_request_id(monkeypatch, tmp_path):
    # Mocked before the engine's main loop ever reaches analyze() — the
    # loop continues past the resume-upload phase into a real analyze()
    # call, which must never hit a real Anthropic endpoint.
    _sequential_anthropic_client(monkeypatch, [_COMPLETE_JSON])
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    engine, events = _make_engine(resume_path=str(resume))

    # find_file_inputs() returns one upload-looking input so the engine
    # emits resume_upload_prompt without needing a vision call.
    async def find_file_inputs():
        return [{"index": 0, "id": "resume", "name": "", "accept": "", "label": "Resume", "nearby": ""}]

    engine._browser.find_file_inputs = find_file_inputs  # noqa: SLF001

    engine.start_session("file:///fake.html")

    async def driver():
        task = asyncio.create_task(engine.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(e["type"] == "resume_upload_prompt" for e in events):
                break
        prompt = next(e for e in events if e["type"] == "resume_upload_prompt")
        assert prompt["request_id"]

        # Wrong id: rejected, engine stays paused.
        assert engine.accept_upload("not-the-right-id") is False
        # Matching id: accepted.
        assert engine.accept_upload(prompt["request_id"]) is True

        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(
                e["type"] == "fill_complete" or e["type"] == "status" and "No fillable" in e.get("message", "")
                for e in events
            ):
                break
        engine.stop_session()
        await task

    run(driver())

    # resume_input_idx was found directly (find_file_inputs stub above), so
    # the upload goes through upload_to_file_input(), not execute() —
    # engine._browser.executed staying empty confirms that path was taken.
    assert engine._browser.executed == []  # noqa: SLF001
    assert engine._browser.closed is True  # noqa: SLF001


def test_skip_upload_with_no_pending_prompt_returns_false():
    engine, _ = _make_engine()
    assert engine.accept_upload("anything") is False
    assert engine.skip_upload(None) is False


# ── stop_session ends the loop and always closes the browser ────────────


def test_stop_session_ends_run_and_closes_browser(monkeypatch):
    _sequential_anthropic_client(monkeypatch, [_COMPLETE_JSON])
    engine, events = _make_engine()
    engine.start_session("file:///fake.html")

    async def driver():
        task = asyncio.create_task(engine.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(e["type"] == "fill_complete" for e in events):
                break
        engine.stop_session()
        await task

    run(driver())

    assert engine._browser.closed is True  # noqa: SLF001
    assert events[-1]["type"] == "finished_session"


# ── No blocking Anthropic call on the event loop (T29 DoD) ──────────────


def test_analyze_call_does_not_block_the_event_loop(monkeypatch):
    """A slow, synchronous analyze() call must not stall concurrent
    coroutines — proves it runs via run_in_threadpool, not inline.
    """

    class _SlowMessages:
        def create(self, **kwargs):
            time.sleep(0.3)  # simulate a slow synchronous Anthropic call
            return _FakeResponse(_COMPLETE_JSON)

    class _SlowClient:
        def __init__(self, *a, **k) -> None:
            self.messages = _SlowMessages()

    monkeypatch.setattr(vision_mod.anthropic, "Anthropic", _SlowClient)

    engine, events = _make_engine()
    engine.start_session("file:///fake.html")

    async def driver():
        ticks: list[float] = []

        async def heartbeat():
            start = time.monotonic()
            while time.monotonic() - start < 0.6:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        hb = asyncio.create_task(heartbeat())
        run_task = asyncio.create_task(engine.run())
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(e["type"] == "fill_complete" for e in events):
                break
        engine.stop_session()
        await run_task
        await hb
        return ticks

    ticks = run(driver())

    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert gaps, "heartbeat never ticked"
    # If analyze() ran inline, one gap would be ~0.3s. With it offloaded to
    # a threadpool, every heartbeat tick stays close to the 0.01s sleep.
    assert max(gaps) < 0.1, gaps


# ── End-to-end: a real fill session against a local fixture form ────────


_CHROME_EXE = find_chrome_exe()


@pytest.mark.skipif(_CHROME_EXE is None, reason="no headed Chrome executable found for Playwright")
def test_fill_session_runs_end_to_end_against_local_fixture_form(monkeypatch, tmp_path):
    """Real Playwright Chromium, a local file:// fixture page, and a
    mocked vision call — never a live job site, never a real Anthropic
    key.
    """
    form_path = tmp_path / "form.html"
    form_path.write_text("<html><body><input id='full_name' type='text'></body></html>", encoding="utf-8")
    form_url = form_path.resolve().as_uri()

    _sequential_anthropic_client(monkeypatch, [_FILL_FULL_NAME_JSON, _COMPLETE_JSON])

    from jobfinder.fillerbot.engine import FillerEngine as RealEngine

    events: list[dict] = []
    engine = RealEngine(
        "fake-key",
        "fake-model",
        FillerProfile(full_name="Jane Doe"),
        resume_path="",
        chrome_exe=_CHROME_EXE,
        emit=events.append,
    )
    engine.start_session(form_url)

    async def driver():
        task = asyncio.create_task(engine.run())
        deadline = time.monotonic() + 30
        failure = None
        try:
            # Engine pauses after analysis, waiting for the user to approve
            # the pending actions — approve them to unblock the fill step.
            while time.monotonic() < deadline:
                if any(e["type"] == "status" and "Found 1 field" in e.get("message", "") for e in events):
                    break
                await asyncio.sleep(0.05)
            else:
                failure = f"timed out waiting for the fields-found pause; events so far: {events}"
                return failure
            engine.execute_fill()

            while time.monotonic() < deadline:
                if any(e["type"] == "action_executed" for e in events):
                    break
                await asyncio.sleep(0.05)
            else:
                failure = f"timed out waiting for action_executed; events so far: {events}"
                return failure
        finally:
            # Teardown has to be unconditional, and bounded.
            #
            # This test drives a real Chrome. If anything above gives up —
            # a screenshot timing out while fonts load on a file:// page is
            # the flake actually observed — the engine is parked in
            # _wait_unpause with no user to unpause it. Raising straight out
            # of the coroutine used to leave the engine task to be cancelled
            # by asyncio.run's shutdown, which then awaited
            # FillerBrowser.close() on an unresponsive browser inside a
            # cancelled task: the suite hung indefinitely and leaked ~15
            # chrome.exe processes, which then made every later run hang too.
            #
            # So: ask it to stop, give it a bounded window, cancel if it
            # misses, and close the browser directly as a last resort. A
            # flaky environment now produces a failed test with a reason
            # instead of a hung CI job.
            engine.stop_session()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=20)
            except (TimeoutError, asyncio.TimeoutError):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(task, timeout=10)
            except Exception:  # noqa: BLE001 — the engine's own error path
                pass
            with contextlib.suppress(Exception):
                await engine._browser.close()  # noqa: SLF001 — belt and braces
        return None

    failure = run(driver())
    if failure:
        pytest.fail(failure)

    action_events = [e for e in events if e["type"] == "action_executed"]
    assert action_events, events
    assert action_events[0]["ok"] is True, events
    assert events[-1]["type"] == "finished_session"


# ── No synchronous subprocess/socket call on the event loop (browser) ────


@pytest.mark.skipif(sys.platform != "win32", reason="Chrome process management is Windows-specific here")
def test_browser_launch_offloads_blocking_work_from_the_event_loop(monkeypatch):
    from jobfinder.fillerbot import browser as browser_mod
    from jobfinder.fillerbot.browser import FillerBrowser

    captured = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args

        def terminate(self):
            pass

    def fake_create_connection(addr, timeout=None):
        time.sleep(0.3)  # simulate a slow synchronous connect

        class _Sock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Sock()

    class FakeBrowserObj:
        contexts = []

        async def new_context(self, **kwargs):
            class _Ctx:
                pages = []

                async def new_page(self):
                    class _Page:
                        def set_default_timeout(self, *_a, **_k):
                            return None

                    return _Page()

            return _Ctx()

    class FakeChromium:
        async def connect_over_cdp(self, url):
            return FakeBrowserObj()

    class FakePW:
        chromium = FakeChromium()

        async def stop(self):
            return None

    class FakePlaywrightCM:
        async def start(self):
            return FakePW()

    monkeypatch.setattr(browser_mod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(browser_mod.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(browser_mod, "async_playwright", lambda: FakePlaywrightCM())

    b = FillerBrowser(chrome_exe="fake-chrome.exe")

    async def driver():
        ticks: list[float] = []

        async def heartbeat():
            start = time.monotonic()
            while time.monotonic() - start < 0.6:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        hb = asyncio.create_task(heartbeat())
        await b.launch()
        await hb
        return ticks

    ticks = run(driver())
    gaps = [y - x for x, y in zip(ticks, ticks[1:])]
    assert gaps, "heartbeat never ticked"
    assert max(gaps) < 0.1, gaps


# ── Stale unpause must not skip the next pause point ────────────────────


def test_a_control_call_between_pause_points_does_not_skip_the_next_pause(monkeypatch):
    """A rescan/resume POST landing mid-analysis must not satisfy the *next*
    pause point.

    The QThread version was immune to this because it set ``_paused = True``
    immediately before every wait, discarding any earlier unpause. With a
    plain ``asyncio.Event`` the signal is sticky, so if the event were only
    cleared *after* waiting, the "Found N fields" pause would fall straight
    through and the form would be filled with values the user never saw.
    """
    _sequential_anthropic_client(monkeypatch, [_FILL_FULL_NAME_JSON, _COMPLETE_JSON])
    engine, events = _make_engine()

    # A control POST arriving while the engine is busy analysing — nowhere
    # near a pause point.
    original_screenshot = engine._browser.screenshot  # noqa: SLF001 — intentional test seam

    async def _screenshot_then_rescan() -> bytes:
        engine.rescan_page()
        return await original_screenshot()

    engine._browser.screenshot = _screenshot_then_rescan  # noqa: SLF001
    engine.start_session("https://example.test/apply")

    async def drive() -> list:
        task = asyncio.create_task(engine.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(e.get("type") == "status" and "Found 1 field" in e.get("message", "") for e in events):
                break
        else:
            raise AssertionError(f"engine never reached the fill-approval pause: {events}")
        # Sit at the pause for a while: nothing may be filled until the
        # user actually clicks Fill.
        await asyncio.sleep(0.1)
        executed_before_approval = list(engine._browser.executed)  # noqa: SLF001
        engine.stop_session()
        await asyncio.wait_for(task, timeout=5)
        return executed_before_approval

    assert run(drive()) == []


def test_stop_session_is_honoured_even_when_it_arrives_before_the_pause(monkeypatch):
    """The counterpart to the test above: clearing the event on entry must
    not swallow a ``stop_session`` that landed just before the wait, or the
    engine would block at that pause forever with Chrome still open.
    """
    _sequential_anthropic_client(monkeypatch, [_FILL_FULL_NAME_JSON, _COMPLETE_JSON])
    engine, events = _make_engine()

    original_screenshot = engine._browser.screenshot  # noqa: SLF001

    async def _screenshot_then_stop() -> bytes:
        engine.stop_session()
        return await original_screenshot()

    engine._browser.screenshot = _screenshot_then_stop  # noqa: SLF001
    engine.start_session("https://example.test/apply")

    async def drive() -> None:
        await asyncio.wait_for(engine.run(), timeout=5)

    run(drive())
    assert engine._browser.closed is True  # noqa: SLF001
    assert any(e.get("type") == "finished_session" for e in events)


# ── A background tab's renderer is throttled (screenshot regression) ────


def test_screenshot_activates_the_tab_before_capturing() -> None:
    """``screenshot()`` must call ``bring_to_front()`` first.

    Chrome throttles a background tab's renderer, so
    ``document.fonts.ready`` — which Playwright's screenshot awaits — can
    hang until the 10s timeout. The engine then reports "Could not capture
    page. Wait for it to load, then Re-scan.", so a user who simply
    alt-tabbed away while reading the form sees FillerBot fail with a
    message about page loading that has nothing to do with the cause.

    Measured on a real headed Chrome before the fix: 10.01s timeout with
    the tab in the background, 0.05s once activated. Pinned here because
    the symptom points nowhere near the cause.
    """
    from unittest.mock import AsyncMock, MagicMock

    from jobfinder.fillerbot.browser import FillerBrowser

    calls: list[str] = []
    browser = FillerBrowser.__new__(FillerBrowser)
    page = MagicMock()
    page.bring_to_front = AsyncMock(side_effect=lambda: calls.append("bring_to_front"))
    page.screenshot = AsyncMock(side_effect=lambda **kw: calls.append("screenshot") or b"png")
    browser._page = page  # noqa: SLF001 — the seam the other browser tests use

    assert run(browser.screenshot()) == b"png"
    assert calls == ["bring_to_front", "screenshot"], calls


def test_screenshot_still_captures_when_activation_fails() -> None:
    """Activation is best-effort: a screenshot that might work beats
    refusing outright because the tab could not be raised."""
    from unittest.mock import AsyncMock, MagicMock

    from jobfinder.fillerbot.browser import FillerBrowser

    browser = FillerBrowser.__new__(FillerBrowser)
    page = MagicMock()
    page.bring_to_front = AsyncMock(side_effect=RuntimeError("no window"))
    page.screenshot = AsyncMock(return_value=b"png-anyway")
    browser._page = page  # noqa: SLF001

    assert run(browser.screenshot()) == b"png-anyway"
