#!/usr/bin/env python
"""T97 DoD browser check — the log drawer's per-level hide filter,
measured.

Follows ``scripts/t96_heartbeat_browser_check.py``'s shape: the same
``_seed``/``_wait_for_port`` reuse from ``t81_board_browser_check.py``,
reading ``GET /api/settings`` through the page (the server authenticates
with a session cookie; an outside request answers 401), and the same
``_SlowStubOrchestrator``-style bootstrap technique — a stub orchestrator
monkeypatched in before ``jobfinder.launcher`` boots, so a real scan
cycle emits real log records at every level with no network call and no
file under ``jobfinder/`` modified to make this work.

Asserts, per this task's DoD:

  * hiding a level removes exactly those lines from *view* — checked via
    ``getBoundingClientRect``/``getComputedStyle``, never by counting
    ``querySelectorAll`` results alone, since a row still in the DOM but
    hidden by CSS must count as hidden too (this project has been bitten
    by that difference six times; this implementation happens to remove
    the row from the DOM entirely, but the check does not assume that);
  * the hidden-count readout matches the number of lines actually hidden;
  * the choice (which levels are hidden) survives a reload;
  * ERROR/CRITICAL are not hidden in the *default* state — read from
    ``GET /api/settings``'s ``ui.console_hidden_levels`` on a fresh
    profile, not merely asserted in unit tests;
  * no horizontal scroll at 2560 with the filter row visible.

Usage::

    .venv/Scripts/python.exe scripts/t97_log_filter_browser_check.py [outdir]

Requires ``npx vite build`` to have produced ``frontend/dist`` already,
and Playwright's Chromium (``playwright install chromium``) installed.
Every Playwright call below carries an explicit timeout — an unbounded
run on this project once hung for three hours.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from t81_board_browser_check import _seed, _wait_for_port  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
STATE_TRANSITION_TIMEOUT_MS = 8000
VIEWPORT_DESKTOP = {"width": 2560, "height": 1400}

# Distinct, grep-able markers — one per level — so the test can tell
# exactly which synthetic line survived a filter rather than merely
# counting how many lines are present.
MARKERS = {
    "DEBUG": "T97_MARK_DEBUG",
    "INFO": "T97_MARK_INFO",
    "WARNING": "T97_MARK_WARNING",
    "ERROR": "T97_MARK_ERROR",
    "CRITICAL": "T97_MARK_CRITICAL",
}

# A stub orchestrator whose `run_scan` emits one log record at every
# level through a dedicated logger, each with its own level explicitly
# set to DEBUG so the jobfinder-package-wide INFO floor
# (jobfinder/web/logging_setup.py) never swallows the DEBUG line before
# BroadcastLogHandler ever sees it. No real network call, no file under
# jobfinder/ touched to make this observable.
_BOOTSTRAP_TEMPLATE = """
import sys
sys.path.insert(0, __REPO_ROOT__)

import logging

import jobfinder.web.scan as scan_module

_stub_logger = logging.getLogger("jobfinder.t97stub")
_stub_logger.setLevel(logging.DEBUG)


class _LoggingStubOrchestrator:
    \"\"\"No network, no real scrape — just one real log record per level,
    through the real jobfinder logger hierarchy, through the real
    BroadcastLogHandler/EventBroadcaster path.\"\"\"

    def __init__(self, events) -> None:
        self._events = events

    def run_scan(self) -> None:
        self._events.put({"type": "scan_start"})
        _stub_logger.debug(__MARK_DEBUG__)
        _stub_logger.info(__MARK_INFO__)
        _stub_logger.warning(__MARK_WARNING__)
        _stub_logger.error(__MARK_ERROR__)
        _stub_logger.critical(__MARK_CRITICAL__)
        self._events.put({"type": "scan_complete", "new_jobs": 0, "matches": 0, "best_score": None})


scan_module._build_orchestrator = lambda db, config, events: _LoggingStubOrchestrator(events)

from jobfinder.launcher import main

raise SystemExit(main(["--no-browser"]))
"""


def _settings(page) -> dict:
    return page.evaluate(
        "async () => { const r = await fetch('/api/settings', { credentials: 'same-origin' });"
        " if (!r.ok) throw new Error('GET /api/settings -> ' + r.status); return r.json(); }"
    )


def _state(page) -> dict:
    return page.evaluate(
        "async () => { const r = await fetch('/api/state', { credentials: 'same-origin' });"
        " if (!r.ok) throw new Error('GET /api/state -> ' + r.status); return r.json(); }"
    )


def _trigger(page, path: str) -> None:
    page.evaluate(
        "async (p) => { const r = await fetch(p, { method: 'POST', credentials: 'same-origin' });"
        " if (!r.ok) throw new Error(p + ' -> ' + r.status); }",
        path,
    )


def _wait_for_running(page, expected: bool) -> dict:
    """Polled from Python, deliberately — a `wait_for_function` async
    predicate hands back a handle to the Promise, not its value (same
    lesson t95/t96 already paid for)."""
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    last: dict | None = None
    while time.monotonic() < deadline:
        last = _state(page)
        if last["running"] == expected:
            return last
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for running=={expected}; last seen: {last}")


def _wait_for_scan_completed(page, before: str | None) -> dict:
    """Wait until a scan completes, by watching `last_scan_completed_at`.

    Independent of whether the `running: true` window was ever observable
    — with a fast stub it is not. `before` must be sampled *before* the
    scan is triggered: the stub finishes in single-digit milliseconds, so
    sampling it afterwards reads the value the scan already wrote and then
    waits forever for it to change again.
    """
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    last: dict = {}
    while time.monotonic() < deadline:
        last = _state(page)
        if last.get("last_scan_completed_at") and last["last_scan_completed_at"] != before:
            return last
        page.wait_for_timeout(100)
    raise AssertionError(f"no scan completed within the timeout; last seen: {last}")


def _wait_for_line_count(page, *, at_least: int) -> int:
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    last = 0
    while time.monotonic() < deadline:
        last = page.evaluate("() => document.querySelectorAll('.log-drawer-line').length")
        if last >= at_least:
            return last
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for at least {at_least} rendered log lines; last saw {last}")


def _visible_marker_texts(page) -> set[str]:
    """Every marker string currently **visible** in the drawer — checked
    by bounding-rect size and computed `display`/`visibility`, not by
    whether a `.log-drawer-line` element merely exists. This
    implementation happens to remove hidden lines from the DOM outright,
    but the check is written to also catch a CSS-only hide (`display:
    none`, zero-size, `visibility: hidden`) as "hidden" too, since a row
    left in the DOM but invisible must count the same way."""
    return set(
        page.evaluate(
            """() => {
                const out = [];
                for (const el of document.querySelectorAll('.log-drawer-line')) {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    const visible = rect.width > 0 && rect.height > 0
                        && style.display !== 'none' && style.visibility !== 'hidden';
                    if (visible) out.push(el.textContent);
                }
                return out;
            }"""
        )
    )


def _hidden_count_readout(page) -> int | None:
    text = page.evaluate(
        "() => { const el = document.querySelector('.log-drawer-hidden-count'); return el ? el.textContent : null; }"
    )
    if text is None:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


def _toggle_level(page, level: str) -> None:
    page.click(f"label.log-drawer-filter-chip:has-text('{level}') input[type=checkbox]", timeout=TIMEOUT_MS)


def main() -> int:
    outdir = (
        pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t97-log-filter-"))
    )
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    # `jobfinder.paths.setup_data_environment` (called inside `_seed`, in
    # *this* process) sets `PLAYWRIGHT_BROWSERS_PATH` to
    # `<data dir>/browsers` so a packaged build finds Chromium inside its
    # own data root (see `build_app.py`). That data dir is a fresh tmp
    # directory with no Chromium in it, so if left in place the
    # `pw.chromium.launch` call below — in this same process — looks for
    # Chromium there instead of wherever `playwright install chromium`
    # actually put it. Same restore-after-seed shape as
    # t81/t82/t83/t85/t88/t89/t95's browser checks.
    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")

    with tempfile.TemporaryDirectory(prefix="jf-t97-", ignore_cleanup_errors=True) as tmp:
        data_dir = pathlib.Path(tmp) / "data"
        _seed(data_dir)
        if original_browsers_path is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original_browsers_path

        env = dict(os.environ)
        env["JOBFINDER_DATA_DIR"] = str(data_dir)
        env.pop("ANTHROPIC_API_KEY", None)

        bootstrap_dir = pathlib.Path(tempfile.mkdtemp(prefix="t97-bootstrap-"))
        bootstrap_path = bootstrap_dir / "bootstrap.py"
        bootstrap_source = textwrap.dedent(_BOOTSTRAP_TEMPLATE)
        bootstrap_source = bootstrap_source.replace("__REPO_ROOT__", repr(str(REPO_ROOT)))
        for level, marker in MARKERS.items():
            bootstrap_source = bootstrap_source.replace(f"__MARK_{level}__", repr(marker))
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
                browser = pw.chromium.launch(headless=True, timeout=TIMEOUT_MS)
                try:
                    page = browser.new_page(viewport=VIEWPORT_DESKTOP)
                    page.set_default_timeout(TIMEOUT_MS)
                    page.goto(url, timeout=TIMEOUT_MS)

                    # DoD: ERROR/CRITICAL are not hidden by *default* —
                    # read the setting itself, before the drawer is ever
                    # touched, on a fresh profile.
                    fresh_settings = _settings(page)
                    default_hidden = fresh_settings["ui"]["console_hidden_levels"]
                    print(f"default_console_hidden_levels={default_hidden!r}")
                    assert "ERROR" not in default_hidden, f"ERROR hidden by default: {default_hidden!r}"
                    assert "CRITICAL" not in default_hidden, f"CRITICAL hidden by default: {default_hidden!r}"
                    assert default_hidden == [], f"expected nothing hidden by default, got {default_hidden!r}"
                    print("default_hidden_excludes_error_and_critical=ok")

                    # Fire a real scan cycle through the stubbed
                    # orchestrator — emits one log record per level.
                    completed_before = _state(page).get("last_scan_completed_at")
                    _trigger(page, "/api/scan/play")
                    # Wait for the scan to have *finished*, not for its
                    # running window to be observed. The stub emits its five
                    # records and returns in single-digit milliseconds, so
                    # `_wait_for_running(True)` is a race this check loses on
                    # a fast machine — it passed for its author and timed out
                    # on the Lead's first run. What this test needs is the log
                    # records, and `last_scan_completed_at` moving is the
                    # signal that they have been emitted.
                    _wait_for_scan_completed(page, completed_before)

                    # Expand the drawer and let it pick up the replayed
                    # ring-buffer events.
                    page.wait_for_selector(".log-drawer-toggle", timeout=TIMEOUT_MS)
                    page.click(".log-drawer-toggle", timeout=TIMEOUT_MS)
                    page.wait_for_selector(".log-drawer-body", timeout=TIMEOUT_MS)
                    _wait_for_line_count(page, at_least=len(MARKERS))

                    all_visible = _visible_marker_texts(page)
                    print(
                        f"all_visible_markers_present={[m for m in MARKERS.values() if any(m in t for t in all_visible)]}"
                    )
                    for level, marker in MARKERS.items():
                        assert any(marker in t for t in all_visible), f"[baseline] {level} marker missing from view"
                    baseline_hidden = _hidden_count_readout(page)
                    print(f"baseline_hidden_count_readout={baseline_hidden!r}")
                    assert baseline_hidden in (0, None), (
                        f"expected nothing hidden at baseline, readout={baseline_hidden!r}"
                    )

                    shot_baseline = outdir / "t97-log-filter-baseline.png"
                    page.screenshot(path=str(shot_baseline), timeout=TIMEOUT_MS)
                    print(f"captured={shot_baseline}")

                    # Hide WARNING: exactly that marker disappears from
                    # view, the other four stay, and the hidden-count
                    # readout says 1.
                    _toggle_level(page, "WARNING")
                    page.wait_for_timeout(300)  # local filtering is synchronous; this covers the re-render + PUT.

                    visible_after_hide = _visible_marker_texts(page)
                    warning_marker = MARKERS["WARNING"]
                    assert not any(warning_marker in t for t in visible_after_hide), (
                        f"WARNING marker still visible after hiding WARNING: {visible_after_hide!r}"
                    )
                    for level, marker in MARKERS.items():
                        if level == "WARNING":
                            continue
                        assert any(marker in t for t in visible_after_hide), (
                            f"hiding WARNING should not have hidden {level}: {visible_after_hide!r}"
                        )
                    hidden_after_warning = _hidden_count_readout(page)
                    print(f"hidden_count_after_hiding_warning={hidden_after_warning!r}")
                    assert hidden_after_warning == 1, f"expected hidden count 1, got {hidden_after_warning!r}"
                    print("hiding_one_level_removes_exactly_those_lines=ok hidden_count_matches=ok")

                    # Reload: the choice must survive. The drawer's own
                    # collapsed/expanded state also persists (localStorage,
                    # LogDrawer.tsx's `STORAGE_KEY`) across a reload, so
                    # it comes back already expanded from the earlier
                    # click — toggling unconditionally here would
                    # re-collapse it instead of opening it.
                    page.reload(timeout=TIMEOUT_MS)
                    page.wait_for_selector(".log-drawer-toggle", timeout=TIMEOUT_MS)
                    if page.get_attribute(".log-drawer-toggle", "aria-expanded") != "true":
                        page.click(".log-drawer-toggle", timeout=TIMEOUT_MS)
                    page.wait_for_selector(".log-drawer-body", timeout=TIMEOUT_MS)
                    _wait_for_line_count(page, at_least=len(MARKERS) - 1)

                    visible_after_reload = _visible_marker_texts(page)
                    assert not any(warning_marker in t for t in visible_after_reload), (
                        f"WARNING re-appeared after reload: {visible_after_reload!r}"
                    )
                    for level, marker in MARKERS.items():
                        if level == "WARNING":
                            continue
                        assert any(marker in t for t in visible_after_reload), (
                            f"{level} missing after reload: {visible_after_reload!r}"
                        )
                    hidden_after_reload = _hidden_count_readout(page)
                    print(f"hidden_count_after_reload={hidden_after_reload!r}")
                    assert hidden_after_reload == 1, f"filter choice did not survive reload: {hidden_after_reload!r}"
                    print("filter_choice_survives_reload=ok")

                    reloaded_settings = _settings(page)
                    reloaded_hidden = reloaded_settings["ui"]["console_hidden_levels"]
                    print(f"persisted_console_hidden_levels={reloaded_hidden!r}")
                    assert reloaded_hidden == ["WARNING"], f"persisted value drifted: {reloaded_hidden!r}"

                    # No horizontal scroll at 2560 with the filter row
                    # visible (same measurement shape as t81/t85/t96: a
                    # real DOM comparison, not eyeballed).
                    overflow = page.evaluate(
                        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
                    )
                    print(f"horizontal_overflow_at_2560={overflow!r}")
                    assert not overflow, "page has horizontal scroll at 2560 with the log filter row visible"
                    print("no_horizontal_scroll_at_2560=ok")

                    shot_filtered = outdir / "t97-log-filter-warning-hidden.png"
                    page.screenshot(path=str(shot_filtered), timeout=TIMEOUT_MS)
                    print(f"captured={shot_filtered}")

                    # Un-hide WARNING and confirm it comes back (sanity
                    # on the toggle being reversible, not one-way).
                    _toggle_level(page, "WARNING")
                    page.wait_for_timeout(300)
                    visible_after_unhide = _visible_marker_texts(page)
                    assert any(warning_marker in t for t in visible_after_unhide), (
                        "WARNING did not return after un-hiding it"
                    )
                    print("toggle_is_reversible=ok")

                    page.close()
                finally:
                    browser.close(reason="t97 log filter check done")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("\nT97_LOG_FILTER_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
