#!/usr/bin/env python
"""T89 DoD browser check — the board's filter control, measured.

Follows ``scripts/t83_panel_browser_check.py``'s shape: a separate script
rather than folding into ``scripts/t81_board_browser_check.py``, reusing
that script's ``_seed``/``_wait_for_port`` helpers rather than re-deriving
them, and reading ``GET /api/board`` through the page (the server
authenticates with a session cookie; an outside request answers 401).

Asserts, per this task's DoD:

  * every control `FilterPanel` offers (`BOARD_FILTER_CONTROL_TEST_IDS`,
    ``frontend/src/filters/boardFilterControls.ts`` -- itself keyed off
    `FilterPanelValues`, the same source of truth the logic test enumerates
    against) is reachable from the board -- opening the header's "Filters"
    popover counts as reachable, it does not have to be visible at rest;
  * changing the minimum score re-filters the **visible board**, asserted
    by a change in the rendered card count in the New column -- not by
    reading the control's own value back;
  * T81's horizontal-fill property still holds at 2560 (the five columns
    must still use at least ``viewport - 220px``, popover closed);
  * no horizontal scroll at 2560 or 390.

Usage::

    .venv/Scripts/python.exe scripts/t89_filters_browser_check.py [outdir]

Requires ``npx vite build`` to have produced ``frontend/dist`` already, and
Playwright's Chromium (``playwright install chromium``) installed. Every
Playwright call below carries an explicit timeout.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from t81_board_browser_check import _seed, _wait_for_port  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
VIEWPORT_DESKTOP = {"width": 2560, "height": 1400}
VIEWPORT_PHONE = {"width": 390, "height": 844}

# Mirrors frontend/src/filters/boardFilterControls.ts's
# BOARD_FILTER_CONTROL_TEST_IDS -- not re-derived from the TS source at
# run time (this script has no TS toolchain of its own). Staying in sync
# is enforced on the TS side instead: tests/boardFilterControls.test.ts
# fails if that module's keys stop matching FilterPanelValues (every
# control FilterPanel actually offers), and tsc -b fails outright if a
# field is added to or removed from FilterPanelValues without a matching
# entry in that Record -- a control dropped from this Python list without
# a matching drop upstream would surface there, not silently here.
EXPECTED_CONTROL_TEST_IDS = [
    "filter-control-match-threshold",
    "filter-control-freshness",
    "filter-control-arrangement",
    "filter-control-hide-internship",
    "filter-control-hide-contract",
    "filter-control-hide-staffing-agencies",
    "filter-control-hide-promoted",
]


def _no_horizontal_scroll(page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def _board_new_count(page) -> int:
    return page.evaluate(
        "async () => { const r = await fetch('/api/board', { credentials: 'same-origin' });"
        " const b = await r.json(); return b.new.length; }"
    )


def main() -> int:
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t89-filters-"))
    outdir.mkdir(parents=True, exist_ok=True)

    if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
        print("frontend/dist/index.html not found -- run `npx vite build` in frontend/ first.", file=sys.stderr)
        return 1

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="t89-data-"))
    _seed(data_dir)
    if original_browsers_path is None:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    else:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original_browsers_path

    env = dict(os.environ)
    env["JOBFINDER_DATA_DIR"] = str(data_dir)
    env.pop("ANTHROPIC_API_KEY", None)

    proc = subprocess.Popen(
        [str(PYTHON), "-m", "jobfinder", "--no-browser"],
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
                page = browser.new_page(viewport=VIEWPORT_DESKTOP)
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-column", timeout=TIMEOUT_MS)

                # ── Horizontal fill holds with the popover closed
                # (T81's committed assertion, scripts/t81_board_browser_check.py).
                spread = page.evaluate(
                    """
                    () => {
                        const cols = [...document.querySelectorAll('.board-column')];
                        const left = Math.min(...cols.map(c => c.getBoundingClientRect().left));
                        const right = Math.max(...cols.map(c => c.getBoundingClientRect().right));
                        return { left, right, vw: document.documentElement.clientWidth };
                    }
                    """
                )
                used = spread["right"] - spread["left"]
                print(f"columns_used={used:.0f} of vw={spread['vw']}")
                assert used >= spread["vw"] - 220, (
                    f"the five columns use only {used:.0f}px of a {spread['vw']}px viewport with the filter "
                    "popover closed -- T81's horizontal-fill regression"
                )
                print("horizontal_fill_holds_popover_closed=ok")

                assert _no_horizontal_scroll(page), "the board causes horizontal scroll at 2560x1400"
                print("no_horizontal_scroll_2560=ok")

                # ── Every control is reachable: open the popover.
                filters_btn = page.get_by_role("button", name="Filters")
                filters_btn.click(timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-filters-popover", timeout=TIMEOUT_MS)
                # The panel's own settings fetch (FilterPanel.tsx) is
                # async -- it renders "Loading filters..." until it
                # resolves, so wait for one real control rather than
                # racing the fetch.
                page.wait_for_selector(f'[data-testid="{EXPECTED_CONTROL_TEST_IDS[0]}"]', timeout=TIMEOUT_MS)

                missing = []
                for test_id in EXPECTED_CONTROL_TEST_IDS:
                    count = page.locator(f'[data-testid="{test_id}"]').count()
                    if count == 0:
                        missing.append(test_id)
                print(
                    f"controls_found={len(EXPECTED_CONTROL_TEST_IDS) - len(missing)}/{len(EXPECTED_CONTROL_TEST_IDS)}"
                )
                assert not missing, f"controls not reachable from the board: {missing}"
                print("all_controls_reachable=ok")

                # ── Horizontal fill still holds with the popover OPEN --
                # it must be out of document flow, not a sixth column.
                spread_open = page.evaluate(
                    """
                    () => {
                        const cols = [...document.querySelectorAll('.board-column')];
                        const left = Math.min(...cols.map(c => c.getBoundingClientRect().left));
                        const right = Math.max(...cols.map(c => c.getBoundingClientRect().right));
                        return { left, right, vw: document.documentElement.clientWidth };
                    }
                    """
                )
                used_open = spread_open["right"] - spread_open["left"]
                print(f"columns_used_popover_open={used_open:.0f} of vw={spread_open['vw']}")
                assert used_open >= spread_open["vw"] - 220, (
                    f"opening the filters popover shrank the five columns to {used_open:.0f}px of "
                    f"{spread_open['vw']}px -- it must not eat column width"
                )
                print("horizontal_fill_holds_popover_open=ok")

                # ── Changing the minimum score re-filters the visible
                # board -- asserted by rendered card count, not by
                # reading the control's own value back. Seeded New-column
                # scores (t81_board_browser_check._seed) are 60/61/62/63;
                # raising the threshold above 61 must drop two of them.
                before_rendered = page.locator('.board-column[data-stage="new"] .board-card').count()
                before_api = _board_new_count(page)
                print(f"new_column_before: rendered={before_rendered} api={before_api}")
                assert before_rendered == before_api == 4, (
                    f"expected the seeded New column to start at 4 cards, got rendered={before_rendered} "
                    f"api={before_api}"
                )

                score_slider = page.locator('[data-testid="filter-control-match-threshold"] input[type="range"]')
                score_slider.evaluate(
                    """
                    (el) => {
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(el, '62');
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                    """
                )

                # The write is debounced 500ms (FilterPanel.tsx's
                # WRITE_DEBOUNCE_MS); poll rather than a flat sleep.
                import time

                deadline = time.monotonic() + 8.0
                after_api = before_api
                while time.monotonic() < deadline:
                    after_api = _board_new_count(page)
                    if after_api != before_api:
                        break
                    time.sleep(0.1)

                page.wait_for_function(
                    "(expected) => document.querySelectorAll('.board-column[data-stage=\"new\"] .board-card')"
                    ".length === expected",
                    arg=after_api,
                    timeout=TIMEOUT_MS,
                )
                after_rendered = page.locator('.board-column[data-stage="new"] .board-card').count()
                print(f"new_column_after_min_score_62: rendered={after_rendered} api={after_api}")
                assert after_api == 2, f"raising the minimum score to 62 left {after_api} cards in New, expected 2"
                assert after_rendered == 2, (
                    f"the API board dropped to {after_api} cards but the DOM still rendered {after_rendered} "
                    "-- the board did not re-filter without a reload"
                )
                print("min_score_refilters_visible_board=ok")

                shot = outdir / "t89-filters-2560x1400.png"
                page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                print(f"captured={shot}")
                page.close()

                # ── 390x844: no horizontal scroll only.
                phone_page = browser.new_page(viewport=VIEWPORT_PHONE)
                phone_page.set_default_timeout(TIMEOUT_MS)
                phone_page.goto(url, timeout=TIMEOUT_MS)
                phone_page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)
                assert _no_horizontal_scroll(phone_page), "the board causes horizontal scroll at 390x844"
                print("no_horizontal_scroll_390=ok")
                phone_shot = outdir / "t89-filters-390x844.png"
                phone_page.screenshot(path=str(phone_shot), timeout=TIMEOUT_MS)
                print(f"captured={phone_shot}")
                phone_page.close()
            finally:
                browser.close(reason="t89 filters check done")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\nT89_FILTERS_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
