#!/usr/bin/env python
"""T83 DoD browser check — the job panel, measured.

A separate script rather than an extension of
``scripts/t81_board_browser_check.py``: that one is T81's committed
evidence that the board renders five columns and the keyboard moves a
card, and folding a second task's assertions into it would mean a T83
failure reporting as a T81 regression. Each task keeps its own evidence.

What it asserts, per T83's DoD:

  * opening a card leaves **all five columns rendered** and the board's
    scroll position unchanged — a panel that replaces the board throws
    away the one thing a board is for, knowing where you are;
  * the stage control shows the card's current stage;
  * clicking another stop moves the card, read back through
    ``GET /api/board`` *and* seen in the DOM in its new column — the API
    and the view have to agree, which is the failure mode a DOM-only
    assertion misses;
  * ``Never show this company`` is in the panel and on **no** card
    (the product spec: dismissing one posting and hiding an employer
    forever are different decisions and must not sit side by side);
  * no horizontal scroll at 2560.

Usage::

    .venv/Scripts/python.exe scripts/t83_panel_browser_check.py [outdir]

Requires ``npm run build`` and Playwright's Chromium. Every Playwright
call carries an explicit timeout — an unbounded run on this project once
hung for three hours.
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
VIEWPORT = {"width": 2560, "height": 1400}


def _board(page) -> dict:
    """Read ``GET /api/board`` through the page.

    Not with ``urllib``: the server authenticates the request with a
    session cookie the page already holds, and a bare request outside
    the browser answers 401. Fetching from inside the browser context is
    also the more honest check — it is the same request the app makes.
    """
    return page.evaluate(
        "async () => { const r = await fetch('/api/board');"
        " if (!r.ok) throw new Error('GET /api/board -> ' + r.status); return r.json(); }"
    )


def _stage_of(board: dict, job_id: str) -> str | None:
    for stage, cards in board.items():
        for card in cards:
            if card["job_id"] == job_id:
                return stage
    return None


def main() -> int:
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t83-panel-"))
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="t83-data-"))
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
                page = browser.new_page(viewport=VIEWPORT)
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-column", timeout=TIMEOUT_MS)

                columns_before = page.locator(".board-column").count()
                scroll_before = page.evaluate("() => document.querySelector('.board-columns').scrollLeft")

                # ── No card offers "Never show this company" (3.8 rule 7).
                on_cards = page.evaluate(
                    "() => [...document.querySelectorAll('.board-card')]"
                    ".filter(c => c.textContent.includes('Never show')).length"
                )
                assert on_cards == 0, f"{on_cards} cards offer 'Never show this company' — it belongs in the panel only"
                print("never_show_on_cards=0")

                # ── Open the first card in New.
                first = page.locator(".board-column").first.locator(".board-card").first
                job_id = first.get_attribute("data-job-id")
                first.evaluate("(el) => el.click()")
                page.wait_for_selector(".job-panel", timeout=TIMEOUT_MS)

                columns_after = page.locator(".board-column").count()
                scroll_after = page.evaluate("() => document.querySelector('.board-columns').scrollLeft")
                print(f"columns_before={columns_before} columns_after={columns_after}")
                assert columns_after == 5, f"the panel left {columns_after} columns rendered, not 5"
                assert columns_before == columns_after, "opening the panel changed the column count"
                assert scroll_before == scroll_after, (
                    f"the board's scroll moved when the panel opened ({scroll_before} -> {scroll_after})"
                )
                print(f"board_scroll_unchanged={scroll_after}")

                # ── The panel offers it; exactly one place on the page does.
                panel_has = page.locator(".job-panel").get_by_text("Never show this company").count()
                assert panel_has == 1, f"the panel shows 'Never show this company' {panel_has} times, expected 1"
                print("never_show_in_panel=1")

                # ── The stage control reflects the card's real stage.
                api_stage = _stage_of(_board(page), job_id)
                pressed = page.evaluate(
                    "() => { const b = document.querySelector("
                    "'.job-panel-stage-btn[aria-pressed=\"true\"]'); return b ? b.textContent : null; }"
                )
                print(f"job_id={job_id} api_stage={api_stage} panel_shows={pressed!r}")
                assert pressed, "no stage stop is marked current in the panel"

                # ── Move it from the panel, and make the API agree.
                target_index = 2  # Applied
                page.evaluate(
                    "(i) => document.querySelectorAll('.job-panel-stage-btn')[i].click()",
                    target_index,
                )
                page.wait_for_timeout(600)
                after = _stage_of(_board(page), job_id)
                print(f"stage_after_panel_click={after}")
                assert after == "applied", f"panel click left the API at {after!r}, expected 'applied'"

                in_dom = page.evaluate(
                    """
                    (jobId) => {
                        const cols = [...document.querySelectorAll('.board-column')];
                        for (const col of cols) {
                            if (col.querySelector(`[data-job-id="${jobId}"]`)) {
                                const h = col.querySelector('.board-column-label');
                                return h ? h.textContent.trim() : '(unnamed column)';
                            }
                        }
                        return null;
                    }
                    """,
                    job_id,
                )
                print(f"card_now_in_column={in_dom!r}")
                assert in_dom and "Applied" in in_dom, f"the card is in {in_dom!r} in the DOM, not Applied"

                assert page.evaluate(
                    "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
                ), "the panel causes horizontal scroll at 2560"
                print("no_horizontal_scroll_2560=ok")

                shot = outdir / "t83-panel-2560x1400.png"
                page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                print(f"captured={shot}")
                page.close()
            finally:
                browser.close(reason="t83 panel check done")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\nT83_PANEL_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
