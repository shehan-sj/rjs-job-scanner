#!/usr/bin/env python
"""T101 DoD browser check — starring a job re-sorts its column, measured.

Follows ``scripts/t99_column_colour_browser_check.py``'s shape: the same
``_seed``/``_wait_for_port`` reuse from ``t81_board_browser_check.py``,
reading ``GET /api/board`` through the page (the server authenticates
with a session cookie; an outside request answers 401), polling from
Python rather than ``wait_for_function`` (an async predicate hands back a
handle to the Promise, not its value), and sampling a baseline *before*
triggering the thing under test.

``GET /api/board`` itself returns its ``applied`` column in whatever
order the underlying SQL query happens to produce -- the re-sort is
client-side (``frontend/src/components/boardLogic.ts``'s ``sortColumn``/
``floatStarredToTop``), not something the server payload's array order
ever promises on its own. So "the order is read back from both the DOM
and ``GET /api/board``" below means: the DOM's rendered order is
independently recomputed from the API payload's own data
(``stage_entered_at`` + ``starred``, mirroring ``sortColumn``'s algorithm
in Python) and the two must agree -- not that the raw API array order
changes. That is a stronger check than comparing two copies of the same
assumption: it would fail if the DOM order and the data the API actually
persisted ever disagreed, which a bug in either the write path or the
client sort could cause independently.

Asserts, per this task's DoD:

  * a column with a known mixed order has a stable baseline, where the
    live DOM order matches the order independently computed from
    ``GET /api/board``'s data, before any star is clicked;
  * clicking the star on a card that is not already first re-sorts the
    column so the starred card is first, the rest of the column keeping
    its prior relative order -- asserted as the whole ordered list, not
    a spot check -- and the DOM again matches the order recomputed from
    the (now-updated) ``GET /api/board`` data;
  * the star, and the resulting order, survive a reload (a fresh
    ``page.goto``, not merely the in-page refetch the click itself
    already triggers);
  * a render is captured at 2560x1400.

Usage::

    .venv/Scripts/python.exe scripts/t101_star_browser_check.py [outdir]

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

from t81_board_browser_check import _no_horizontal_scroll, _wait_for_port  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
VIEWPORT_DESKTOP = {"width": 2560, "height": 1400}

# Four jobs seeded into "applied" in this order, each with a strictly
# later `applied_at` than the one before it -- `sortAppliedColumn`
# (boardLogic.ts) alone would therefore render them most-recent-first:
# d, c, b, a. Starring "b" (neither the newest nor the oldest) must move
# it to the front without disturbing d/c/a's relative order, which is
# the mixed-order case the brief specifically calls for -- a baseline
# that already isn't alphabetical or insertion order lets a bug that
# just "sorts starred jobs alphabetically" or "re-sorts the whole column"
# show up as a wrong order rather than an accidental pass.
JOB_IDS = ["t101-a", "t101-b", "t101-c", "t101-d"]
EXPECTED_BASELINE = ["t101-d", "t101-c", "t101-b", "t101-a"]
STARRED_JOB_ID = "t101-b"
EXPECTED_AFTER_STAR = ["t101-b", "t101-d", "t101-c", "t101-a"]


def _seed(data_dir: pathlib.Path) -> None:
    os.environ["JOBFINDER_DATA_DIR"] = str(data_dir)
    os.environ.pop("ANTHROPIC_API_KEY", None)

    import jobfinder.paths as paths

    paths.setup_data_environment()

    from jobfinder.config import load_config, save_config
    from jobfinder.matcher.models import JobPosting, MatchResult
    from jobfinder.storage.database import Database

    config = load_config(paths.config_path())
    config.anthropic.api_key = "sk-ant-fake-browser-check-not-a-real-key"  # gitleaks:allow -- synthetic
    config.match_threshold = 0
    resume_dir = paths.resume_uploads_dir()
    resume_dir.mkdir(parents=True, exist_ok=True)
    resume_file = resume_dir / "t101-check-resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    config.resume.path = str(resume_file)
    config.resume.original_filename = "t101-check-resume.pdf"
    save_config(config, paths.config_path())

    db = Database(str(paths.db_path()))
    now = datetime.utcnow()

    for i, job_id in enumerate(JOB_IDS):
        job = JobPosting(
            job_id=job_id,
            title=f"QA Engineer {job_id}",
            company=f"Company {job_id}",
            location="Remote",
            description="seed job",
            url=f"https://example.com/{job_id}",
            source="linkedin",
            work_arrangement="Remote",
            employment_type="Full-time",
        )
        db.mark_seen([job])
        db.save_match(MatchResult(job=job, score=70 + i, reasoning="seed", matched_at=now - timedelta(seconds=1)))
        db.set_stage(job_id, "applied")
        # `set_stage` stamps `applied_at` with `datetime.utcnow()` at call
        # time, so sequential calls already produce a strictly increasing
        # timestamp across the four jobs -- no manual backdating needed.


def _board_json(page) -> dict:
    return page.evaluate(
        "async () => { const r = await fetch('/api/board', { credentials: 'same-origin' }); return r.json(); }"
    )


def _seeded_applied_cards(board: dict) -> list[dict]:
    return [card for card in board["applied"] if card["job_id"] in JOB_IDS]


def _expected_order_from_api_data(board: dict) -> list[str]:
    """Independently recompute the order `boardLogic.ts`'s `sortColumn`
    ("applied" branch) would produce from this API payload alone:
    `sortAppliedColumn` (most-recently-applied first, by
    `stage_entered_at` descending) then `floatStarredToTop` (a stable
    partition, starred first). Python's `sorted`/`list` slicing are both
    stable, same guarantee the TS implementation relies on."""
    cards = _seeded_applied_cards(board)
    by_recency = sorted(cards, key=lambda c: c["stage_entered_at"], reverse=True)
    starred = [c for c in by_recency if c["starred"]]
    unstarred = [c for c in by_recency if not c["starred"]]
    return [c["job_id"] for c in starred + unstarred]


def _applied_order_from_dom(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('.board-column[data-stage="applied"] .board-card')]
            .map((el) => el.getAttribute('data-job-id'))
            .filter((id) => id && id.startsWith('t101-'))"""
    )


def _applied_order_from_api(page) -> list[str]:
    """The Applied column's seeded job ids, as the API would have them ordered.

    Mirrors `_applied_order_from_dom` so the two can be compared directly:
    if the DOM and the API disagree about the order, the view is sorting
    something the server did not send, or vice versa. The ordering itself
    is recomputed independently by `_expected_order_from_api_data` rather
    than trusting the payload's own sequence.
    """
    return _expected_order_from_api_data(_board_json(page))


def _starred_flags_from_api(page) -> dict[str, bool]:
    """Which seeded jobs the server says are starred.

    Takes the page rather than an already-fetched board, matching its
    three call sites — each wants the state *now*, and fetching inside
    keeps the read and its use in one step rather than two with a live
    board in between.
    """
    return {card["job_id"]: card["starred"] for card in _seeded_applied_cards(_board_json(page))}


def _poll_until(predicate, timeout_s: float = 5.0, interval_s: float = 0.1):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval_s)
    return last


def main() -> int:
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t101-star-"))
    outdir.mkdir(parents=True, exist_ok=True)

    if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
        print("frontend/dist/index.html not found -- run `npx vite build` in frontend/ first.", file=sys.stderr)
        return 1

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")

    with tempfile.TemporaryDirectory(prefix="jf-t101-", ignore_cleanup_errors=True) as tmp:
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
                    page = browser.new_page(viewport=VIEWPORT_DESKTOP)
                    page.set_default_timeout(TIMEOUT_MS)
                    page.goto(url, timeout=TIMEOUT_MS)
                    page.wait_for_selector(".board-column", timeout=TIMEOUT_MS)
                    page.wait_for_selector('.board-column[data-stage="applied"] .board-card', timeout=TIMEOUT_MS)

                    # ── Baseline, sampled before any star is clicked —
                    # read through both the API and the DOM, and they
                    # must already agree with each other and with the
                    # order `sortAppliedColumn` alone produces.
                    baseline_api = _applied_order_from_api(page)
                    baseline_dom = _applied_order_from_dom(page)
                    print(f"baseline_api={baseline_api}")
                    print(f"baseline_dom={baseline_dom}")
                    assert baseline_api == EXPECTED_BASELINE, (
                        f"unexpected baseline order via API: {baseline_api}, expected {EXPECTED_BASELINE}"
                    )
                    assert baseline_dom == EXPECTED_BASELINE, (
                        f"unexpected baseline order via DOM: {baseline_dom}, expected {EXPECTED_BASELINE}"
                    )
                    baseline_starred = _starred_flags_from_api(page)
                    assert not any(baseline_starred.values()), f"a seed job is already starred: {baseline_starred}"

                    # ── Click the star on a card that is neither first
                    # nor last in the baseline order (t101-b).
                    star_button = page.locator(
                        f'.board-column[data-stage="applied"] .board-card[data-job-id="{STARRED_JOB_ID}"] '
                        ".board-card-star"
                    )
                    assert star_button.count() == 1, f"expected exactly one star control for {STARRED_JOB_ID}"
                    assert star_button.get_attribute("aria-pressed") == "false", "the star control must start unpressed"
                    star_button.click(timeout=TIMEOUT_MS)

                    # ── Re-sort, polled from Python (not
                    # `wait_for_function`, which would hand back a
                    # Promise handle rather than its resolved value for
                    # an async evaluate call).
                    after_api = _poll_until(
                        lambda: (
                            _applied_order_from_api(page)
                            if _applied_order_from_api(page) == EXPECTED_AFTER_STAR
                            else None
                        )
                    )
                    print(f"after_star_api={after_api}")
                    assert after_api == EXPECTED_AFTER_STAR, (
                        f"starring {STARRED_JOB_ID} did not produce the expected order via API: "
                        f"got {_applied_order_from_api(page)}, expected {EXPECTED_AFTER_STAR}"
                    )

                    after_dom = _poll_until(
                        lambda: (
                            _applied_order_from_dom(page)
                            if _applied_order_from_dom(page) == EXPECTED_AFTER_STAR
                            else None
                        )
                    )
                    print(f"after_star_dom={after_dom}")
                    assert after_dom == EXPECTED_AFTER_STAR, (
                        f"starring {STARRED_JOB_ID} did not produce the expected order via DOM: "
                        f"got {_applied_order_from_dom(page)}, expected {EXPECTED_AFTER_STAR}"
                    )

                    after_star_button = page.locator(
                        f'.board-column[data-stage="applied"] .board-card[data-job-id="{STARRED_JOB_ID}"] '
                        ".board-card-star"
                    )
                    assert after_star_button.get_attribute("aria-pressed") == "true", (
                        "the star control did not flip to pressed after being clicked"
                    )

                    starred_flags = _starred_flags_from_api(page)
                    print(f"starred_flags_after_click={starred_flags}")
                    assert starred_flags[STARRED_JOB_ID] is True
                    assert all(not v for k, v in starred_flags.items() if k != STARRED_JOB_ID), (
                        f"an unrelated job picked up a star: {starred_flags}"
                    )
                    # Stage must be unaffected by starring.
                    board_after = _board_json(page)
                    applied_ids_after = {c["job_id"] for c in board_after["applied"]}
                    assert STARRED_JOB_ID in applied_ids_after, "starring moved the job out of Applied"

                    shot = outdir / "t101-board-2560x1400-starred.png"
                    page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                    print(f"captured={shot}")

                    assert _no_horizontal_scroll(page), "the board causes horizontal scroll at 2560x1400 after starring"
                    print("no_horizontal_scroll_2560=ok")

                    # ── Survives a reload: a fresh navigation, not the
                    # in-page refetch the click itself already does.
                    page.goto(url, timeout=TIMEOUT_MS)
                    page.wait_for_selector('.board-column[data-stage="applied"] .board-card', timeout=TIMEOUT_MS)

                    reloaded_api = _applied_order_from_api(page)
                    reloaded_dom = _applied_order_from_dom(page)
                    print(f"after_reload_api={reloaded_api}")
                    print(f"after_reload_dom={reloaded_dom}")
                    assert reloaded_api == EXPECTED_AFTER_STAR, (
                        f"the starred order did not survive a reload via API: {reloaded_api}"
                    )
                    assert reloaded_dom == EXPECTED_AFTER_STAR, (
                        f"the starred order did not survive a reload via DOM: {reloaded_dom}"
                    )
                    reloaded_starred = _starred_flags_from_api(page)
                    assert reloaded_starred[STARRED_JOB_ID] is True, "the star itself did not survive a reload"

                    page.close()
                finally:
                    browser.close(reason="t101 star check done")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("\nT101_STAR_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
