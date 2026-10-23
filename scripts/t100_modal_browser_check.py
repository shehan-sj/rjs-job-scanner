#!/usr/bin/env python
"""T100 DoD browser check — the job detail modal, measured.

Follows ``scripts/t97_log_filter_browser_check.py``'s shape: the same
``_seed``/``_wait_for_port`` reuse from ``t81_board_browser_check.py``,
reading ``GET /api/board`` through the page (the server authenticates
with a session cookie; an outside request answers 401), polling from
Python rather than ``wait_for_function`` (an async predicate hands back
a handle to the Promise, not its value), and sampling a baseline
*before* triggering something rather than after.

Asserts, per this task's DoD, measured at 2560x1400:

  * opening a second job while one is open leaves **exactly one**
    dialog *showing* — checked by ``getBoundingClientRect``/the
    dialog's own ``open`` property, never by ``querySelectorAll``
    count alone, since this component mounts exactly one ``<dialog>``
    for the lifetime of the view regardless of whether it is shown
    (this project has been bitten seven times by DOM-presence standing
    in for visibility) — and it is the second job;
  * Escape closes it, and focus returns to the card that opened it
    (``document.activeElement`` compared to that card);
  * while open, tabbing does not reach the board behind it;
  * the board's scroll position is unchanged by opening and closing;
  * the stage control still moves the card, read back through
    ``GET /api/board``;
  * no horizontal scroll at 2560.

**The one-at-a-time trigger used here.** The owner's own description —
"clicking another [job] should close the previous and open the new
one" — assumed a side panel that left the board clickable underneath
it. A true native-``<dialog>`` modal does not: `showModal()` makes the
rest of the page inert (the same inertness this script's tab-order
assertion below depends on), so a background board card cannot receive
a click while the dialog is open, by design. The concrete trigger this
script uses for "opening a second job while one is open" is therefore
`JobPanel`'s own **Next** control (`.job-panel-nav-btn` — present since
T83, unchanged by this task) rather than a background card click: it
calls the exact same `openJob` replace path (`jobModalLogic.ts`) a
second board-card click would, from a place the modal's own inertness
does not block. One finding this leaves open: a literal
background-card click while the modal is open
is untested here because it is correctly unreachable, not because it
was skipped.

Usage::

    .venv/Scripts/python.exe scripts/t100_modal_browser_check.py [outdir]

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
import time
from datetime import datetime, timedelta

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from t81_board_browser_check import _seed, _wait_for_port  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
STATE_TRANSITION_TIMEOUT_MS = 8000
VIEWPORT_DESKTOP = {"width": 2560, "height": 1400}

# Enough "new" cards that the New column's own body overflows at
# 2560x1400 — the scroll-preservation assertion is meaningless against
# a column that cannot scroll in the first place.
EXTRA_NEW_JOBS = 24


def _seed_extra_new_jobs(count: int) -> None:
    """Add more "new"-stage jobs on top of ``t81_board_browser_check``'s
    ``_seed`` baseline, in the same already-configured data dir (that
    call must run first, in this same process, so
    ``jobfinder.paths.setup_data_environment`` has already pointed
    these imports at the right place)."""
    from jobfinder.matcher.models import JobPosting, MatchResult
    from jobfinder.storage.database import Database
    import jobfinder.paths as paths

    db = Database(str(paths.db_path()))
    now = datetime.utcnow()
    for i in range(count):
        job = JobPosting(
            job_id=f"t100-new-{i}",
            title=f"Modal Check Engineer {i}",
            company=f"T100 Co {i}",
            location="Remote",
            description="t100 seed job",
            url=f"https://example.com/t100-new-{i}",
            source="linkedin",
            work_arrangement="Remote",
            employment_type="Full-time",
            compensation_min=100000,
            compensation_max=130000,
            compensation_currency="USD",
            compensation_interval="yearly",
        )
        db.mark_seen([job])
        db.save_match(MatchResult(job=job, score=50 + i, reasoning="t100 seed", matched_at=now - timedelta(seconds=1)))


def _board(page) -> dict:
    return page.evaluate(
        "async () => { const r = await fetch('/api/board', { credentials: 'same-origin' });"
        " if (!r.ok) throw new Error('GET /api/board -> ' + r.status); return r.json(); }"
    )


def _stage_of(board: dict, job_id: str) -> str | None:
    for stage, cards in board.items():
        for card in cards:
            if card["job_id"] == job_id:
                return stage
    return None


def _wait_for_stage(page, job_id: str, expected: str) -> str | None:
    deadline = time.monotonic() + STATE_TRANSITION_TIMEOUT_MS / 1000
    stage = None
    while time.monotonic() < deadline:
        stage = _stage_of(_board(page), job_id)
        if stage == expected:
            return stage
        page.wait_for_timeout(100)
    return stage


def _visible_modal_dialogs(page) -> list[str]:
    """Every ``dialog.job-modal`` currently **showing** — the dialog's
    own ``open`` boolean plus a non-zero bounding rect, never DOM
    presence alone (a ``<dialog>`` without ``[open]`` is still in the
    DOM). Returns each visible dialog's job id, read off the
    ``.job-panel``'s ``data-job-id`` it currently renders."""
    return page.evaluate(
        """() => {
            const out = [];
            for (const dlg of document.querySelectorAll('dialog.job-modal')) {
                const rect = dlg.getBoundingClientRect();
                const visible = dlg.open && rect.width > 0 && rect.height > 0;
                if (!visible) continue;
                const panel = dlg.querySelector('.job-panel');
                out.push(panel ? panel.getAttribute('data-job-id') : null);
            }
            return out;
        }"""
    )


def main() -> int:
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t100-modal-"))
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")

    with tempfile.TemporaryDirectory(prefix="jf-t100-", ignore_cleanup_errors=True) as tmp:
        data_dir = pathlib.Path(tmp) / "data"
        _seed(data_dir)
        _seed_extra_new_jobs(EXTRA_NEW_JOBS)
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
                    page = browser.new_page(viewport=VIEWPORT_DESKTOP)
                    page.set_default_timeout(TIMEOUT_MS)
                    page.goto(url, timeout=TIMEOUT_MS)
                    page.wait_for_selector('.board-column[data-stage="new"] .board-card', timeout=TIMEOUT_MS)

                    new_column_body = page.locator('.board-column[data-stage="new"] .board-column-body')
                    cards = page.locator('.board-column[data-stage="new"] .board-card')
                    card_count = cards.count()
                    print(f"new_column_card_count={card_count}")
                    assert card_count >= 2, f"need at least two New cards, got {card_count}"

                    job_a_el = cards.nth(0)
                    job_b_el = cards.nth(1)
                    job_a_id = job_a_el.get_attribute("data-job-id")
                    job_b_id = job_b_el.get_attribute("data-job-id")
                    print(f"job_a={job_a_id} job_b={job_b_id}")

                    # ── Baseline: no dialog showing before anything is
                    # clicked, and a non-zero scroll position in the New
                    # column (otherwise "unchanged by opening/closing" is
                    # trivially true of a column that cannot scroll at
                    # all).
                    assert _visible_modal_dialogs(page) == [], "a dialog is already showing before any card was clicked"
                    new_column_body.evaluate("(el) => { el.scrollTop = 120; }")
                    scroll_before = new_column_body.evaluate("(el) => el.scrollTop")
                    print(f"new_column_scroll_before={scroll_before}")
                    assert scroll_before > 0, "could not establish a non-zero scroll baseline in the New column"

                    # ── Open job A.
                    job_a_el.evaluate("(el) => el.click()")
                    page.wait_for_selector("dialog.job-modal[open]", timeout=TIMEOUT_MS)
                    visible = _visible_modal_dialogs(page)
                    print(f"visible_after_opening_a={visible}")
                    assert visible == [job_a_id], f"expected only job A ({job_a_id}) showing, got {visible}"

                    # ── Opening a second job while one is open: driven by
                    # the panel's own Next control (module docstring above
                    # explains why a background-card click cannot be the
                    # trigger here). Exactly one dialog remains, and it now
                    # shows job B.
                    page.click(".job-panel-nav-btn[aria-label='Next job in this column']", timeout=TIMEOUT_MS)
                    page.wait_for_timeout(200)
                    visible_after_next = _visible_modal_dialogs(page)
                    print(f"visible_after_opening_b={visible_after_next}")
                    assert visible_after_next == [job_b_id], (
                        f"opening a second job while one was open should leave exactly one dialog, showing job B "
                        f"({job_b_id}); got {visible_after_next}"
                    )
                    all_job_panels = page.locator(".job-panel").count()
                    print(f"job_panel_element_count={all_job_panels}")
                    assert all_job_panels == 1, f"expected exactly one .job-panel element, found {all_job_panels}"

                    # ── Tabbing while open never reaches the board behind
                    # it (native `showModal()` inertness).
                    reached_board = False
                    for _ in range(25):
                        page.keyboard.press("Tab")
                        in_board = page.evaluate("() => !!document.activeElement.closest('.board-column')")
                        if in_board:
                            reached_board = True
                            break
                    print(f"tab_reached_board_while_open={reached_board}")
                    assert not reached_board, "Tab reached a board card while the modal was open"

                    # ── Escape closes it, and focus returns to the card
                    # that opened the dialog (job A's element — the
                    # element BoardView captured when the dialog was
                    # opened; navigating to job B via Next does not change
                    # which element re-gains focus on close).
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
                    visible_after_escape = _visible_modal_dialogs(page)
                    print(f"visible_after_escape={visible_after_escape}")
                    assert visible_after_escape == [], f"Escape did not close the dialog: {visible_after_escape}"

                    # ── Backdrop click dismisses it.
                    #
                    # With `showModal()` the page behind is inert, so this
                    # is the only pointer route out other than the close
                    # control — and it is what the owner reaches for when
                    # they say "clicking another should close the previous
                    # one". The click lands on the backdrop, never on the
                    # card beneath, so dismissing and opening the next are
                    # two clicks rather than one. Asserted here so that
                    # stays true.
                    cards.nth(0).evaluate("(el) => el.click()")
                    page.wait_for_timeout(250)
                    assert _visible_modal_dialogs(page), "the dialog did not reopen for the backdrop test"
                    page.mouse.click(8, 8)
                    page.wait_for_timeout(250)
                    visible_after_backdrop = _visible_modal_dialogs(page)
                    print(f"visible_after_backdrop_click={visible_after_backdrop}")
                    assert visible_after_backdrop == [], (
                        f"a backdrop click did not close the dialog: {visible_after_backdrop}"
                    )

                    focus_is_opener = page.evaluate(
                        '(jobId) => document.activeElement === document.querySelector(`[data-job-id="${jobId}"].board-card`)',
                        job_a_id,
                    )
                    print(f"focus_returned_to_opener_card={focus_is_opener}")
                    assert focus_is_opener, "focus did not return to the card that opened the dialog after Escape"

                    # ── The board's scroll position is unchanged by
                    # opening and closing.
                    scroll_after = new_column_body.evaluate("(el) => el.scrollTop")
                    print(f"new_column_scroll_after={scroll_after}")
                    assert scroll_after == scroll_before, (
                        f"the New column's scroll moved across open/close ({scroll_before} -> {scroll_after})"
                    )

                    # ── Re-open job A and move it with the stage control;
                    # the API has to agree.
                    job_a_el.evaluate("(el) => el.click()")
                    page.wait_for_selector("dialog.job-modal[open]", timeout=TIMEOUT_MS)
                    assert _visible_modal_dialogs(page) == [job_a_id], "re-opening job A did not show it"

                    target_index = 2  # Applied
                    page.evaluate(
                        "(i) => document.querySelectorAll('.job-panel-stage-btn')[i].click()",
                        target_index,
                    )
                    stage_after = _wait_for_stage(page, job_a_id, "applied")
                    print(f"stage_after_stage_control_click={stage_after!r}")
                    assert stage_after == "applied", (
                        f"the stage control did not move job A to Applied via GET /api/board, got {stage_after!r}"
                    )

                    # ── No horizontal scroll at 2560 (measured against
                    # the viewport, not eyeballed).
                    overflow = page.evaluate(
                        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
                    )
                    print(f"horizontal_overflow_at_2560={overflow!r}")
                    assert not overflow, "the page has horizontal scroll at 2560 with the modal available"

                    shot = outdir / "t100-modal-2560x1400.png"
                    page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                    print(f"captured={shot}")

                    page.close()
                finally:
                    browser.close(reason="t100 modal check done")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("\nT100_MODAL_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
