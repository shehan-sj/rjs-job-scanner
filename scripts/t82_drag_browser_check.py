#!/usr/bin/env python
"""T82 DoD browser check — pointer drag, the drop slot, and undo, measured.

A separate script rather than an extension of
``scripts/t81_board_browser_check.py`` or ``scripts/t83_panel_browser_check.py``,
for the same reason T83's own docstring gives: each task keeps its own
evidence, so a T82 regression reports as a T82 failure rather than as a
T81/T83 one. Reuses T81's seed/launch helpers (``_seed``/``_wait_for_port``)
rather than re-deriving them, same pattern T83 already established.

What it asserts, per T82's DoD:

  * a real pointer drag (``page.mouse.move``/``down``/``up``, with
    intermediate moves — a single jump often does not trigger the pointer
    handlers) moves a card to another column, read back through
    ``GET /api/board`` *and* seen in the DOM in its new column;
  * the drop slot appears mid-drag, before the pointer is released, and
    names the target stage;
  * undo (clicking the toast's "Undo") returns the card, and the API
    agrees;
  * no horizontal scroll at 2560.

Usage::

    .venv/Scripts/python.exe scripts/t82_drag_browser_check.py [outdir]

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
    the browser answers 401 (same note T83's check leaves).
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


def _column_label_of(page, job_id: str) -> str | None:
    return page.evaluate(
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


def main() -> int:
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t82-drag-"))
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="t82-data-"))
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

                # ── Drag the first New card to Shortlist.
                source = page.locator('.board-column[data-stage="new"] .board-card').first
                job_id = source.get_attribute("data-job-id")
                stage_before = _stage_of(_board(page), job_id)
                print(f"job_id={job_id} stage_before={stage_before!r}")
                assert stage_before == "new", f"the seeded card started at {stage_before!r}, not 'new'"

                source_box = source.bounding_box()
                assert source_box is not None, "the source card did not render"
                target_column = page.locator('.board-column[data-stage="shortlist"]')
                target_box = target_column.bounding_box()
                assert target_box is not None, "the Shortlist column did not render"

                start_x = source_box["x"] + source_box["width"] / 2
                start_y = source_box["y"] + source_box["height"] / 2
                end_x = target_box["x"] + target_box["width"] / 2
                end_y = target_box["y"] + target_box["height"] / 2

                # A real pointer drag with intermediate moves — a single
                # jump often does not trigger the pointermove handlers.
                page.mouse.move(start_x, start_y, steps=5)
                page.mouse.down()
                steps = 8
                for i in range(1, steps + 1):
                    x = start_x + (end_x - start_x) * i / steps
                    y = start_y + (end_y - start_y) * i / steps
                    page.mouse.move(x, y, steps=2)

                # ── The drop slot must be visible *before* the pointer is
                # released, naming the target stage.
                page.wait_for_selector(".board-drop-slot", timeout=TIMEOUT_MS)
                slot_text = page.locator(".board-drop-slot").inner_text()
                print(f"drop_slot_mid_drag={slot_text!r}")
                assert "shortlist" in slot_text.lower(), f"the drop slot {slot_text!r} doesn't name 'shortlist'"

                page.mouse.up()
                page.wait_for_timeout(600)

                # ── The move: both the API and the DOM must agree.
                after_drag = _stage_of(_board(page), job_id)
                print(f"stage_after_drag={after_drag!r}")
                assert after_drag == "shortlist", f"the drag left the API at {after_drag!r}, expected 'shortlist'"

                column_after = _column_label_of(page, job_id)
                print(f"card_now_in_column={column_after!r}")
                assert column_after and "Shortlist" in column_after, (
                    f"the card is in {column_after!r} in the DOM, not Shortlist"
                )

                assert page.locator(".board-drop-slot").count() == 0, "the drop slot is still showing after the drop"

                # ── Undo returns the card; the API agrees.
                page.wait_for_selector(".board-undo-toast", timeout=TIMEOUT_MS)
                toast_text = page.locator(".board-undo-toast-text").inner_text()
                print(f"undo_toast_text={toast_text!r}")
                assert "Shortlist" in toast_text, f"the undo toast {toast_text!r} doesn't name the destination"

                page.locator(".board-undo-toast-btn").click()
                page.wait_for_timeout(600)

                after_undo = _stage_of(_board(page), job_id)
                print(f"stage_after_undo={after_undo!r}")
                assert after_undo == "new", f"undo left the API at {after_undo!r}, expected 'new'"

                column_after_undo = _column_label_of(page, job_id)
                print(f"card_now_in_column_after_undo={column_after_undo!r}")
                assert column_after_undo and "New" in column_after_undo, (
                    f"after undo the card is in {column_after_undo!r} in the DOM, not New"
                )

                assert page.evaluate(
                    "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
                ), "dragging/undo causes horizontal scroll at 2560"
                print("no_horizontal_scroll_2560=ok")

                shot = outdir / "t82-drag-2560x1400.png"
                page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                print(f"captured={shot}")
                page.close()
            finally:
                browser.close(reason="t82 drag check done")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\nT82_DRAG_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
