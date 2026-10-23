#!/usr/bin/env python
"""T99 DoD browser check — the board's colour-coded columns, measured.

Follows ``scripts/t100_modal_browser_check.py``'s shape: the same
``_seed``/``_wait_for_port`` reuse from ``t81_board_browser_check.py``,
reading ``GET /api/board`` through the page (the server authenticates
with a session cookie; an outside request answers 401), polling from
Python rather than ``wait_for_function`` (an async predicate hands back
a handle to the Promise, not its value), and sampling a baseline
*before* triggering a theme switch rather than after.

Asserts, per this task's DoD, measured at 2560x1400 **in both themes**
(``data-theme`` set on ``document.documentElement``, the same attribute
``theme.ts``'s ``applyTheme`` sets):

  * each of the five columns' stage colour is present in a region
    **larger than the old 9px swatch** (`.board-column-header`'s
    computed ``background-color``, measured area = its own bounding
    box, not eyeballed against a dot that no longer exists in the DOM);
  * the five columns are distinguishable from each other — the five
    measured ``background-color`` values are five distinct strings;
  * T81's horizontal-fill property still holds (`.board-column`
    bounding boxes span >= viewport - 220px) — the band is an internal
    negative-margin treatment, not a column-width change;
  * no horizontal scroll at 2560 or 390.

Usage::

    .venv/Scripts/python.exe scripts/t99_column_colour_browser_check.py [outdir]

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

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from t81_board_browser_check import _no_horizontal_scroll, _seed, _wait_for_port  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
VIEWPORT_DESKTOP = {"width": 2560, "height": 1400}
VIEWPORT_PHONE = {"width": 390, "height": 844}

# The old swatch (`.board-column-swatch`, removed by this task) was a
# 10px circle: area = pi * 5^2 =~ 78.5px^2. The header band must measure
# comfortably larger than that per column.
OLD_SWATCH_AREA_PX2 = 3.14159 * 5 * 5

STAGE_ORDER = ["new", "shortlist", "applied", "replied", "out"]


def _set_theme(page, theme: str) -> None:
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)


def _header_band_metrics(page) -> list[dict]:
    """Per column (in `STAGE_ORDER`): the header's computed
    `background-color` and its bounding box area, read live off the
    DOM rather than assumed from the CSS source."""
    return page.evaluate(
        """(stages) => stages.map((stage) => {
            const col = document.querySelector(`.board-column[data-stage="${stage}"]`);
            const header = col ? col.querySelector('.board-column-header') : null;
            if (!header) return { stage, background: null, area: 0 };
            const rect = header.getBoundingClientRect();
            const background = getComputedStyle(header).backgroundColor;
            return { stage, background, area: rect.width * rect.height };
        })""",
        STAGE_ORDER,
    )


def _columns_horizontal_fill(page) -> dict:
    spread = page.evaluate(
        """
        () => {
            const cols = [...document.querySelectorAll('.board-column')];
            if (!cols.length) return null;
            const left = Math.min(...cols.map(c => c.getBoundingClientRect().left));
            const right = Math.max(...cols.map(c => c.getBoundingClientRect().right));
            return { left, right, vw: document.documentElement.clientWidth };
        }
        """
    )
    assert spread, "no .board-column elements to measure"
    return spread


def _check_theme(page, outdir: pathlib.Path, theme: str) -> None:
    _set_theme(page, theme)
    # Theme tokens are plain CSS custom properties — no network round
    # trip — but give layout/paint a tick before reading computed style.
    page.wait_for_timeout(100)

    metrics = _header_band_metrics(page)
    print(f"[{theme}] header_band_metrics={metrics}")

    backgrounds = []
    for m in metrics:
        assert m["background"] is not None, f"[{theme}] column {m['stage']!r} has no .board-column-header"
        assert m["area"] > OLD_SWATCH_AREA_PX2, (
            f"[{theme}] column {m['stage']!r} header band area {m['area']:.1f}px^2 is not larger than "
            f"the old swatch's {OLD_SWATCH_AREA_PX2:.1f}px^2"
        )
        backgrounds.append(m["background"])

    distinct = set(backgrounds)
    print(f"[{theme}] distinct_backgrounds={len(distinct)} of {len(backgrounds)}")
    assert len(distinct) == len(backgrounds), (
        f"[{theme}] the five columns' header bands are not all distinct: {backgrounds}"
    )

    spread = _columns_horizontal_fill(page)
    used = spread["right"] - spread["left"]
    print(
        f"[{theme}] columns_left={spread['left']:.0f} right={spread['right']:.0f} used={used:.0f} of vw={spread['vw']}"
    )
    assert used >= spread["vw"] - 220, (
        f"[{theme}] the five columns use only {used:.0f}px of a {spread['vw']}px viewport "
        f"-- T81's horizontal-fill assertion no longer holds"
    )

    assert _no_horizontal_scroll(page), f"[{theme}] the board causes horizontal scroll at 2560x1400"
    print(f"[{theme}] no_horizontal_scroll_2560=ok")

    shot = outdir / f"t99-board-2560x1400-{theme}.png"
    page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
    print(f"[{theme}] captured={shot}")


def main() -> int:
    outdir = (
        pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t99-column-colour-"))
    )
    outdir.mkdir(parents=True, exist_ok=True)

    if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
        print("frontend/dist/index.html not found -- run `npx vite build` in frontend/ first.", file=sys.stderr)
        return 1

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")

    with tempfile.TemporaryDirectory(prefix="jf-t99-", ignore_cleanup_errors=True) as tmp:
        data_dir = pathlib.Path(tmp) / "data"
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
                browser = pw.chromium.launch(headless=True, timeout=TIMEOUT_MS)
                try:
                    # ── 2560x1400, both themes ───────────────────────
                    page = browser.new_page(viewport=VIEWPORT_DESKTOP)
                    page.set_default_timeout(TIMEOUT_MS)
                    page.goto(url, timeout=TIMEOUT_MS)
                    page.wait_for_selector(".board-column", timeout=TIMEOUT_MS)

                    columns = page.locator(".board-column")
                    assert columns.count() == 5, f"expected five columns, found {columns.count()}"

                    # Baseline, sampled before either theme is forced —
                    # the headers must already resolve to five distinct,
                    # larger-than-swatch backgrounds under whatever the
                    # default (dark) theme is, before this script ever
                    # calls `_set_theme`.
                    baseline = _header_band_metrics(page)
                    print(f"baseline_before_any_theme_switch={baseline}")
                    assert all(m["background"] for m in baseline), (
                        "a column header has no background before any theme switch"
                    )

                    _check_theme(page, outdir, "dark")
                    _check_theme(page, outdir, "light")

                    page.close()

                    # ── 390x844: no horizontal scroll only (phone
                    # layout, same floor t81_board_browser_check.py
                    # already asserts; the header band is hidden under
                    # 640px (BoardView.css), which is this task's hard
                    # boundary against restyling the phone layout).
                    phone_page = browser.new_page(viewport=VIEWPORT_PHONE)
                    phone_page.set_default_timeout(TIMEOUT_MS)
                    phone_page.goto(url, timeout=TIMEOUT_MS)
                    phone_page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)
                    assert _no_horizontal_scroll(phone_page), "the board causes horizontal scroll at 390x844"
                    print("no_horizontal_scroll_390=ok")
                    phone_page.close()
                finally:
                    browser.close(reason="t99 column colour check done")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("\nT99_COLUMN_COLOUR_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
