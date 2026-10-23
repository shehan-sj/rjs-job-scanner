#!/usr/bin/env python
"""T95 DoD browser check — the board's one scan control, measured.

Follows ``scripts/t83_panel_browser_check.py``'s shape: a separate script
rather than folding into ``scripts/t81_board_browser_check.py``, reusing
that script's ``_seed``/``_wait_for_port`` helpers rather than re-deriving
them, and reading ``GET /api/state`` through the page (the server
authenticates with a session cookie; an outside request answers 401).

Asserts, per this task's DoD:

  * the control's label/action are driven by ``GET /api/state`` for a
    scan **this tab's own button did not start** — every trigger below is
    a raw ``fetch('/api/scan'...)``/``/api/scan/play``/``/api/scan/pause``
    call made from inside the page, never a click on ``.scan-control``'s
    own button;
  * pausing (``POST /api/scan/pause``) stops the next scheduled scan and
    resuming (``POST /api/scan/play``) restarts it, both read back from
    ``GET /api/state``'s ``scan_cycle_scheduled``;
  * "Scan now" still works while the automatic cycle is paused
    (``trigger_scan`` never checks ``is_running``/``is_scheduled``);
  * no visible control's text claims to stop an in-flight scan, checked
    while ``running`` is actually ``true``.

A real scan (no mocked ``/api/scan`` response) would normally complete in
single-digit milliseconds with this script's seed data (no active
sources configured), far too fast for Playwright to ever observe
``running: true`` — the race is real, not a test artifact. Rather than
mock anything over HTTP, the launched subprocess monkeypatches
``jobfinder.web.scan._build_orchestrator`` with a slow, no-network stub —
the same technique ``tests/test_web_scan.py``'s ``_BlockingOrchestrator``
uses for the identical reason, applied here at process bootstrap instead
of via pytest's ``monkeypatch`` fixture, since this check launches a real
subprocess rather than an in-process ``TestClient``. No file under
``jobfinder/`` is modified to make this work.

Usage::

    .venv/Scripts/python.exe scripts/t95_scan_control_browser_check.py [outdir]

Requires ``npx vite build`` to have produced ``frontend/dist`` already,
and Playwright's Chromium (``playwright install chromium``) installed.
Every Playwright call below carries an explicit timeout — an unbounded
run on this project once hung for three hours.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import time
import sys
import tempfile
import textwrap

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from t81_board_browser_check import _seed, _wait_for_port  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
STATE_TRANSITION_TIMEOUT_MS = 8000
VIEWPORT = {"width": 2560, "height": 1400}

# How long the stubbed orchestrator sleeps per scan — long enough that
# Playwright's `wait_for_function` polling (sub-100ms) reliably observes
# `running: true` through at least one tick, short enough that the check
# as a whole stays fast.
STUB_SCAN_SECONDS = 1.5

# Plain token substitution, not str.format — the bootstrap body below is
# real Python with its own literal `{...}` dicts, which would collide
# with format()'s brace syntax.
_BOOTSTRAP_TEMPLATE = """
import sys
sys.path.insert(0, __REPO_ROOT__)

import time

import jobfinder.web.scan as scan_module


class _SlowStubOrchestrator:
    \"\"\"No network, no real scrape — only slow enough that a real
    ``running: true`` window is observable from outside the process.
    Same intent as tests/test_web_scan.py's _BlockingOrchestrator, applied
    at process bootstrap rather than via pytest's monkeypatch fixture.

    Unlike that test's stub, this one also puts ``scan_start``/
    ``scan_complete`` onto ``events`` itself — the real
    ``Orchestrator.run_scan()`` does this (``jobfinder/orchestrator.py``),
    but ``_scan_fn`` (jobfinder/web/scan.py) never does it on the
    orchestrator's behalf, so a bare stub that only sleeps would leave
    the SSE stream silent for the whole scan.\"\"\"

    def __init__(self, events) -> None:
        self._events = events

    def run_scan(self) -> None:
        self._events.put({"type": "scan_start"})
        time.sleep(__STUB_SECONDS__)
        self._events.put({"type": "scan_complete", "new_jobs": 0, "matches": 0, "best_score": None})


scan_module._build_orchestrator = lambda db, config, events: _SlowStubOrchestrator(events)

from jobfinder.launcher import main

raise SystemExit(main(["--no-browser"]))
"""


def _safe(text: str | None) -> str:
    """ASCII-safe spelling for ``print()`` only — this console has mangled
    the real ellipsis character in "Scanning now…" into a replacement
    character before. Every assertion still compares against the real
    string; only the logged line goes through this."""
    if text is None:
        return "None"
    return text.replace("…", "...")


def _state(page) -> dict:
    """Read ``GET /api/state`` through the page — see module docstring."""
    return page.evaluate(
        "async () => { const r = await fetch('/api/state', { credentials: 'same-origin' });"
        " if (!r.ok) throw new Error('GET /api/state -> ' + r.status); return r.json(); }"
    )


def _trigger(page, path: str) -> None:
    """``POST`` a scan-control endpoint from inside the page — never a
    click on `.scan-control`'s own button, which is the whole point of
    this check: proving the control reflects state it did not cause
    itself."""
    page.evaluate(
        "async (p) => { const r = await fetch(p, { method: 'POST', credentials: 'same-origin' });"
        " if (!r.ok) throw new Error(p + ' -> ' + r.status); }",
        path,
    )


def _wait_for_state(page, predicate, *, what: str) -> dict:
    """Polls `GET /api/state` from Python until `predicate(snapshot)` is
    true, returning that same snapshot — the shared primitive
    `_wait_for_running` below also follows; see its docstring for why the
    condition and the returned value have to come from the same fetch."""
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    last: dict | None = None
    while time.monotonic() < deadline:
        last = _state(page)
        if predicate(last):
            return last
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {what}; last seen: {last}")


def _wait_for_running(page, expected: bool, *, completed: bool = False) -> dict:
    """Wait for `running === expected`, and return the state that satisfied it.

    The state is captured **inside** the wait, not re-fetched after it.
    Fetching again afterwards is a race this check already lost once: the
    schedule is live during the test, so between `running` going false and
    a second `GET /api/state`, the next cycle can start and the caller
    sees `running: true` from a wait that asked for false.

    `completed=True` additionally requires `last_scan_completed_at`, for
    the "a scan has actually finished" case — again in the same predicate,
    so the two facts are true at the same instant rather than in sequence.
    """
    # Polled from Python, deliberately.
    #
    # Two `wait_for_function` shapes were tried here and both returned a
    # snapshot that did not satisfy the predicate: an async predicate
    # hands back a handle to the *Promise* rather than its value, and
    # stashing the snapshot on `window` then reading it afterwards is
    # still two steps with a live scheduler in between. The condition and
    # the value this returns have to come from the same fetch, and the
    # simplest way to guarantee that is to do the loop here and return
    # the very snapshot that passed.
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    last: dict | None = None
    while time.monotonic() < deadline:
        last = _state(page)
        if last["running"] == expected and (not completed or last["last_scan_completed_at"]):
            return last
        page.wait_for_timeout(250)
    raise AssertionError(
        f"timed out waiting for running=={expected}"
        + (" with a completed scan" if completed else "")
        + f"; last seen: {last}"
    )


def _scan_control_text(page) -> str:
    return page.evaluate(
        "() => { const el = document.querySelector('.scan-control'); return el ? el.textContent : ''; }"
    )


def _primary_label(page) -> str:
    return page.evaluate(
        "() => { const b = document.querySelector('.scan-control .board-view-scan-now-btn');"
        " return b ? b.textContent.trim() : null; }"
    )


def _secondary_label(page) -> str | None:
    return page.evaluate(
        "() => { const b = document.querySelector('.scan-control .scan-control-secondary-btn');"
        " return b ? b.textContent.trim() : null; }"
    )


def _wait_for_label(page, reader, expected: str, *, what: str) -> str:
    """Polls ``reader(page)`` from Python until it reads ``expected`` —
    same technique as `_wait_for_running`, for the same reason:
    `wait_for_function`'s async-predicate return value and a follow-up
    synchronous read are two separate moments, and the UI's reaction to a
    backend transition (SSE `scan_start` push -> `refreshScanStateNow()`
    -> its own `/api/state` fetch -> React re-render) lags that
    transition by a render or two, not zero.

    On timeout this raises with whatever the DOM actually showed last —
    a bare `wait_for_function` timeout here would say nothing about what
    the control was actually displaying, which is the one fact worth
    knowing when this fails.
    """
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    last: str | None = None
    while time.monotonic() < deadline:
        last = reader(page)
        if last == expected:
            return last
        page.wait_for_timeout(100)
    raise AssertionError(
        f"timed out waiting for the {what} to read {_safe(expected)!r}; last seen on screen: {_safe(last)!r}"
    )


def _wait_for_primary_label(page, expected: str) -> str:
    return _wait_for_label(page, _primary_label, expected, what="primary label")


def _wait_for_secondary_label(page, expected: str) -> str:
    return _wait_for_label(page, _secondary_label, expected, what="secondary label")


def _assert_no_stop_claim(page, moment: str) -> None:
    text = _scan_control_text(page).lower()
    assert "stop" not in text, f"[{moment}] the scan control's text claims to stop a scan: {text!r}"
    print(f"no_stop_claim[{moment}]=ok ({text!r})")


def main() -> int:
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t95-scan-"))
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="t95-data-"))
    _seed(data_dir)
    if original_browsers_path is None:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    else:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original_browsers_path

    env = dict(os.environ)
    env["JOBFINDER_DATA_DIR"] = str(data_dir)
    env.pop("ANTHROPIC_API_KEY", None)

    bootstrap_path = pathlib.Path(tempfile.mkdtemp(prefix="t95-bootstrap-")) / "bootstrap.py"
    bootstrap_source = textwrap.dedent(_BOOTSTRAP_TEMPLATE)
    bootstrap_source = bootstrap_source.replace("__REPO_ROOT__", repr(str(REPO_ROOT)))
    bootstrap_source = bootstrap_source.replace("__STUB_SECONDS__", repr(STUB_SCAN_SECONDS))
    bootstrap_path.write_text(bootstrap_source, encoding="utf-8")

    proc = subprocess.Popen(
        [str(PYTHON), str(bootstrap_path)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        url = _wait_for_port(proc)
        print(f"server up at {url}")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport=VIEWPORT)
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)
                page.wait_for_selector(".scan-control", timeout=TIMEOUT_MS)

                # ── (a) Initial state: scheduler alive, idle, automatic
                # scanning off (JobScheduler.start() boots paused — see
                # jobfinder/web/app.py's lifespan).
                state = _state(page)
                print(f"initial_state={state}")
                assert state["scheduler_alive"] is True, "scheduler reports dead at boot"
                assert state["running"] is False
                assert state["scan_cycle_scheduled"] is False
                primary = _primary_label(page)
                secondary = _secondary_label(page)
                print("initial_primary=" + _safe(primary) + " secondary=" + _safe(secondary))
                assert primary == "Scan now", f"expected 'Scan now' at boot, got {primary!r}"
                assert secondary == "Resume automatic scanning", (
                    f"expected 'Resume automatic scanning' at boot, got {secondary!r}"
                )

                shot_idle = outdir / "t95-scan-control-idle.png"
                page.screenshot(path=str(shot_idle), timeout=TIMEOUT_MS)
                print(f"captured={shot_idle}")

                # ── (b) Resume automatic scanning through the API
                # (POST /api/scan/play), not the button. JobScheduler.play()
                # also fires an immediate scan, so this exercises both the
                # resume write path and a scan this tab did not click for.
                _trigger(page, "/api/scan/play")
                running_state = _wait_for_running(page, True)
                print(f"after_play_running_state={running_state}")
                assert running_state["scan_cycle_scheduled"] is True, (
                    "POST /api/scan/play did not resume the schedule, per /api/state"
                )
                primary = _wait_for_primary_label(page, "Scanning now…")
                secondary = _secondary_label(page)
                print("running_scheduled_primary=" + _safe(primary) + " secondary=" + _safe(secondary))
                assert secondary == "Pause automatic scanning"
                _assert_no_stop_claim(page, "running-scheduled")
                shot_running = outdir / "t95-scan-control-running.png"
                page.screenshot(path=str(shot_running), timeout=TIMEOUT_MS)
                print(f"captured={shot_running}")

                idle_state = _wait_for_running(page, False, completed=True)
                print(f"after_play_idle_state={idle_state}")
                assert idle_state["scan_cycle_scheduled"] is True
                assert idle_state["last_scan_completed_at"] is not None
                primary = _wait_for_primary_label(page, "Scan now")
                secondary = _secondary_label(page)
                assert secondary == "Pause automatic scanning"

                # ── (c) Pause through the API, not the button. Confirms
                # pausing stops the *next* scheduled scan, read back from
                # /api/state's scan_cycle_scheduled. Same Python-side poll
                # as `_wait_for_running` — the condition and the value
                # returned come from the same fetch, not a follow-up one.
                _trigger(page, "/api/scan/pause")
                paused_state = _wait_for_state(
                    page,
                    lambda s: s["scan_cycle_scheduled"] is False,
                    what="scan_cycle_scheduled to go false after pause",
                )
                print(f"after_pause_state={paused_state}")
                assert paused_state["scan_cycle_scheduled"] is False, (
                    "POST /api/scan/pause did not stop the next scheduled scan, per /api/state"
                )
                assert paused_state["running"] is False
                primary = _wait_for_primary_label(page, "Scan now")
                secondary = _wait_for_secondary_label(page, "Resume automatic scanning")
                print("paused_primary=" + _safe(primary) + " secondary=" + _safe(secondary))

                # ── (d) "Scan now" through the API while the cycle is
                # paused — trigger_scan (jobfinder/web/scan.py) never
                # checks is_running/is_scheduled, so this is a legitimate
                # one-off scan with automatic scanning off. The
                # "impossible-looking" combination this task's brief calls
                # out: running=true while scan_cycle_scheduled=false.
                _trigger(page, "/api/scan")
                running_paused_state = _wait_for_running(page, True)
                print(f"scan_now_while_paused_running_state={running_paused_state}")
                assert running_paused_state["scan_cycle_scheduled"] is False, (
                    "triggering a one-off scan must not silently resume the automatic cycle"
                )
                primary = _wait_for_primary_label(page, "Scanning now…")
                secondary = _wait_for_secondary_label(page, "Resume automatic scanning")
                print("running_unscheduled_primary=" + _safe(primary) + " secondary=" + _safe(secondary))
                _assert_no_stop_claim(page, "running-unscheduled")
                shot_running_paused = outdir / "t95-scan-control-running-while-paused.png"
                page.screenshot(path=str(shot_running_paused), timeout=TIMEOUT_MS)
                print(f"captured={shot_running_paused}")

                final_state = _wait_for_running(page, False)
                print(f"scan_now_while_paused_final_state={final_state}")
                assert final_state["scan_cycle_scheduled"] is False, (
                    "a one-off 'Scan now' while paused must not have resumed the automatic cycle"
                )
                primary = _wait_for_primary_label(page, "Scan now")
                secondary = _wait_for_secondary_label(page, "Resume automatic scanning")
                print("after_scan_now_while_paused_primary=" + _safe(primary) + " secondary=" + _safe(secondary))

                print("scan_now_works_while_cycle_paused=true")
                page.close()
            finally:
                browser.close(reason="t95 scan control check done")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\nT95_SCAN_CONTROL_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
