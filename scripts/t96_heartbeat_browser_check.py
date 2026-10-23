#!/usr/bin/env python
"""T96 DoD browser check — the heartbeat popover's stray bullets, and a
sign that a scan is running, measured.

Follows ``scripts/t95_scan_control_browser_check.py``'s shape: the same
``_seed``/``_wait_for_port`` reuse from ``t81_board_browser_check.py``,
reading ``GET /api/state`` through the page (the server authenticates
with a session cookie; an outside request answers 401), and the same
``_SlowStubOrchestrator`` bootstrap technique that makes ``running: true``
observable from outside the process with no real network call — copied
rather than imported, since ``t95``'s template is a module-local string,
not an exported helper.

Asserts, per this task's DoD:

  * the popover open at 2560 **and** 390: no line of its text equals the
    separator glyph (``·``) on its own, and the expected labels are all
    present;
  * the dot differs between idle and scanning, by class (and, for the
    reduced-motion context, also checked via computed style) — never a
    screenshot comparison;
  * with ``prefers-reduced-motion: reduce`` emulated, the scanning state
    is still distinguishable from idle and no CSS animation is running
    on the dot.

Usage::

    .venv/Scripts/python.exe scripts/t96_heartbeat_browser_check.py [outdir]

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
VIEWPORT_PHONE = {"width": 390, "height": 844}

# Same reasoning as t95_scan_control_browser_check.py's STUB_SCAN_SECONDS:
# long enough that Python-side polling of /api/state reliably observes
# running: true through at least one tick, short enough to stay fast.
STUB_SCAN_SECONDS = 1.5

SEPARATOR_GLYPH = "·"  # ·

# Same bootstrap technique as t95_scan_control_browser_check.py — a slow,
# no-network stub orchestrator, monkeypatched in before jobfinder.launcher
# boots, so a real scan has an observable running:true window without any
# network call. No file under jobfinder/ is modified to make this work.
_BOOTSTRAP_TEMPLATE = """
import sys
sys.path.insert(0, __REPO_ROOT__)

import time

import jobfinder.web.scan as scan_module


class _SlowStubOrchestrator:
    \"\"\"No network, no real scrape — only slow enough that a real
    ``running: true`` window is observable from outside the process.\"\"\"

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
    if text is None:
        return "None"
    return text.replace(SEPARATOR_GLYPH, "<sep>").replace("…", "...")


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
    """Same technique as t95_scan_control_browser_check.py's
    `_wait_for_running`: polled from Python, deliberately — a
    `wait_for_function` async predicate hands back a handle to the
    Promise, not its value, which cost real time to find there. The
    condition and the returned snapshot come from the same fetch."""
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    last: dict | None = None
    while time.monotonic() < deadline:
        last = _state(page)
        if last["running"] == expected:
            return last
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for running=={expected}; last seen: {last}")


def _dot_selector(scope: str) -> str:
    return f"{scope} .heartbeat-compact-btn .heartbeat-dot"


def _wait_for_dot_class(page, scope: str, *, contains: str) -> str:
    """Polls the dot's className from Python until it contains `contains`
    — the UI's reaction to a backend transition lags that transition by
    a render or two, not zero (same reasoning as t95's `_wait_for_label`).
    """
    selector = _dot_selector(scope)
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    last: str | None = None
    while time.monotonic() < deadline:
        last = page.evaluate(
            "(sel) => { const el = document.querySelector(sel); return el ? el.className : null; }",
            selector,
        )
        if last and contains in last:
            return last
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {selector} class to contain {contains!r}; last seen: {last!r}")


def _open_popover(page, scope: str) -> None:
    page.click(f"{scope} .heartbeat-compact-btn", timeout=TIMEOUT_MS)
    page.wait_for_selector(f"{scope} .heartbeat-compact-popover", timeout=TIMEOUT_MS)


def _popover_row_texts(page, scope: str) -> list[str]:
    return page.evaluate(
        "(sel) => [...document.querySelectorAll(sel + ' .heartbeat-compact-popover-row')]"
        ".map((el) => el.textContent.trim())",
        scope,
    )


def _assert_no_bare_separator_line(row_texts: list[str], *, moment: str) -> None:
    bare = [t for t in row_texts if t == SEPARATOR_GLYPH]
    assert not bare, f"[{moment}] a popover line is just the separator glyph: {row_texts!r}"
    print(f"no_bare_separator_line[{moment}]=ok rows={[_safe(t) for t in row_texts]}")


def _assert_expected_labels_present(row_texts: list[str], *, status_text: str, moment: str) -> None:
    joined = " | ".join(row_texts)
    expected_substrings = [
        status_text,
        "Last scan:",
        "Scanning continues when you close this tab.",
        "Notifications only fire while a JobFinder tab is open",
    ]
    for expected in expected_substrings:
        assert expected in joined, f"[{moment}] expected label {_safe(expected)!r} missing from popover: {joined!r}"
    print(f"expected_labels_present[{moment}]=ok")


def _check_popover_at_viewport(pw, outdir: pathlib.Path, *, viewport: dict, scope: str, label: str) -> None:
    """One full populated-board run at one viewport: idle popover (no
    stray separator lines, expected labels present), trigger a real scan
    through the stubbed orchestrator, and assert the dot differs between
    idle and scanning plus the running popover is still bullet-clean."""
    with tempfile.TemporaryDirectory(prefix=f"jf-t96-{label}-", ignore_cleanup_errors=True) as tmp:
        data_dir = pathlib.Path(tmp) / "data"
        _seed(data_dir)

        env = dict(os.environ)
        env["JOBFINDER_DATA_DIR"] = str(data_dir)
        env.pop("ANTHROPIC_API_KEY", None)

        bootstrap_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"t96-bootstrap-{label}-"))
        bootstrap_path = bootstrap_dir / "bootstrap.py"
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
            print(f"[{label}] server up at {url}")
            browser = pw.chromium.launch(headless=True, timeout=TIMEOUT_MS)
            try:
                page = browser.new_page(viewport=viewport)
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)

                if scope == ".rail-more-sheet":
                    # Phone layout (T96's DoD requires 390 too): the foot
                    # controls that include the heartbeat live in the
                    # "More" sheet at this width — Rail.css hides
                    # .rail-desktop below the 640px breakpoint (it stays
                    # in the DOM, just invisible — ".rail" alone resolves
                    # to both and Playwright's visibility wait picks
                    # whichever node happens to be first).
                    page.wait_for_selector(".rail-mobile-bar", timeout=TIMEOUT_MS)
                    page.click(".rail-mobile-bar .rail-mobile-btn:has-text('More')", timeout=TIMEOUT_MS)
                    page.wait_for_selector(".rail-more-sheet", timeout=TIMEOUT_MS)
                else:
                    page.wait_for_selector(scope, timeout=TIMEOUT_MS)

                page.wait_for_selector(_dot_selector(scope), timeout=TIMEOUT_MS)

                idle_state = _state(page)
                print(f"[{label}] idle_state={idle_state}")
                assert idle_state["running"] is False, f"[{label}] expected idle at boot"

                idle_class = page.evaluate("(sel) => document.querySelector(sel).className", _dot_selector(scope))
                print(f"[{label}] idle_dot_class={idle_class!r}")
                assert "heartbeat-status-idle" in idle_class, (
                    f"[{label}] expected heartbeat-status-idle on the dot, got {idle_class!r}"
                )

                _open_popover(page, scope)
                idle_rows = _popover_row_texts(page, scope)
                _assert_no_bare_separator_line(idle_rows, moment=f"{label}-idle")
                _assert_expected_labels_present(idle_rows, status_text="Scheduler idle", moment=f"{label}-idle")

                shot_idle = outdir / f"t96-heartbeat-{label}-idle.png"
                page.screenshot(path=str(shot_idle), timeout=TIMEOUT_MS)
                print(f"[{label}] captured={shot_idle}")

                # Trigger a real scan through the stubbed orchestrator —
                # same /api/scan/play as t95, which both resumes the
                # schedule and fires an immediate scan, observable here
                # as running: true for STUB_SCAN_SECONDS.
                _trigger(page, "/api/scan/play")
                running_state = _wait_for_running(page, True)
                print(f"[{label}] running_state={running_state}")

                scanning_class = _wait_for_dot_class(page, scope, contains="heartbeat-status-scanning")
                print(f"[{label}] scanning_dot_class={scanning_class!r}")
                assert scanning_class != idle_class, (
                    f"[{label}] dot class did not change between idle and scanning: {scanning_class!r}"
                )
                assert "heartbeat-status-idle" not in scanning_class

                running_rows = _popover_row_texts(page, scope)
                _assert_no_bare_separator_line(running_rows, moment=f"{label}-scanning")
                _assert_expected_labels_present(running_rows, status_text="Scanning now", moment=f"{label}-scanning")

                shot_scanning = outdir / f"t96-heartbeat-{label}-scanning.png"
                page.screenshot(path=str(shot_scanning), timeout=TIMEOUT_MS)
                print(f"[{label}] captured={shot_scanning}")

                _wait_for_running(page, False)
                page.close()
            finally:
                browser.close(reason=f"t96 heartbeat check ({label}) done")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _check_reduced_motion(pw, outdir: pathlib.Path) -> None:
    """`prefers-reduced-motion: reduce` emulated: the scanning state must
    still be distinguishable from idle (by class, asserted the same way
    as the full-motion checks above) and no CSS animation must be
    running on the dot — checked via getComputedStyle, not a screenshot.
    """
    with tempfile.TemporaryDirectory(prefix="jf-t96-reduced-motion-", ignore_cleanup_errors=True) as tmp:
        data_dir = pathlib.Path(tmp) / "data"
        _seed(data_dir)

        env = dict(os.environ)
        env["JOBFINDER_DATA_DIR"] = str(data_dir)
        env.pop("ANTHROPIC_API_KEY", None)

        bootstrap_dir = pathlib.Path(tempfile.mkdtemp(prefix="t96-bootstrap-reduced-motion-"))
        bootstrap_path = bootstrap_dir / "bootstrap.py"
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
            print(f"[reduced-motion] server up at {url}")
            browser = pw.chromium.launch(headless=True, timeout=TIMEOUT_MS)
            try:
                context = browser.new_context(viewport=VIEWPORT_DESKTOP, reduced_motion="reduce")
                page = context.new_page()
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)
                page.wait_for_selector(".rail-desktop", timeout=TIMEOUT_MS)
                page.wait_for_selector(_dot_selector(".rail-desktop"), timeout=TIMEOUT_MS)

                idle_state = _state(page)
                assert idle_state["running"] is False, "expected idle at boot"

                idle_class = page.evaluate(
                    "(sel) => document.querySelector(sel).className", _dot_selector(".rail-desktop")
                )
                idle_animation = page.evaluate(
                    "(sel) => getComputedStyle(document.querySelector(sel)).animationName",
                    _dot_selector(".rail-desktop"),
                )
                print(f"[reduced-motion] idle_dot_class={idle_class!r} idle_animation={idle_animation!r}")
                assert "heartbeat-status-idle" in idle_class
                assert idle_animation == "none", f"idle dot should never animate, got {idle_animation!r}"

                _trigger(page, "/api/scan/play")
                _wait_for_running(page, True)

                scanning_class = _wait_for_dot_class(page, ".rail-desktop", contains="heartbeat-status-scanning")
                scanning_animation = page.evaluate(
                    "(sel) => getComputedStyle(document.querySelector(sel)).animationName",
                    _dot_selector(".rail-desktop"),
                )
                print(
                    f"[reduced-motion] scanning_dot_class={scanning_class!r} scanning_animation={scanning_animation!r}"
                )
                assert scanning_class != idle_class, (
                    "under prefers-reduced-motion: reduce, scanning must still differ from idle by class, "
                    f"got {scanning_class!r} == {idle_class!r}"
                )
                assert scanning_animation == "none", (
                    f"a CSS animation is running on the dot under prefers-reduced-motion: reduce: "
                    f"{scanning_animation!r}"
                )
                print("reduced_motion_no_animation=ok reduced_motion_still_distinguishable=ok")

                _wait_for_running(page, False)
                page.close()
                context.close()
            finally:
                browser.close(reason="t96 reduced-motion check done")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t96-heartbeat-"))
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        _check_popover_at_viewport(pw, outdir, viewport=VIEWPORT_DESKTOP, scope=".rail-desktop", label="desktop-2560")
        _check_popover_at_viewport(pw, outdir, viewport=VIEWPORT_PHONE, scope=".rail-more-sheet", label="phone-390")
        _check_reduced_motion(pw, outdir)

    print("\nT96_HEARTBEAT_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
