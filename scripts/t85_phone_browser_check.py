#!/usr/bin/env python
"""T85 DoD browser check — the phone layout, and the two common states.

Reuses ``t81_board_browser_check.py``'s ``_seed`` (the populated five-
column board) and ``t64_split_view_browser_check.py``'s ``_wait_for_port``
rather than re-deriving either — same convention ``t83_panel_browser_check.py``
already established for this task family: each task keeps its own evidence
script, but the seed/launch plumbing is shared.

Three server instances, each with its own seeded data directory, per this
task's DoD:

  1. **Populated** (``t81_board_browser_check.py``'s ``_seed``) at
     390×844: exactly one column's cards are visible, the stage strip
     lists five stops, selecting another stop changes which column
     shows, tapping a card opens the sheet, the sheet's move control
     changes the stage (read back through ``GET /api/board``), there is
     no horizontal *page* scroll, and the Filters button is reachable
     without its bounding box overlapping the visible column's card
     list.
  2. **Empty database** — first run: every one of the five stage pills
     leads to a column that renders its own non-empty title/subtitle, and
     New alone carries the scan CTA button.
  3. **Populated, zero New** — the quiet day: a real ``POST /api/scan``
     is triggered (not faked) against a database with no active sources
     configured, which completes quickly with no network calls
     (``jobfinder/orchestrator.py``'s ``run_scan``: "No active sources
     configured" returns immediately, and ``jobfinder/web/scan.py``'s
     ``_scan_fn`` still calls ``scan_state.mark_completed`` once it
     returns) — a genuine, non-fabricated ``last_scan_completed_at`` with
     ``last_new_jobs=0``/``last_best_score=None``, which is exactly the
     "nothing new to read" branch of ``boardQuietDayLogic.ts``'s
     ``buildQuietDayCopy``. This is deliberately the *zero-postings*
     branch rather than one with a real score, because producing a real,
     non-zero score would require the orchestrator to actually fetch and
     score a posting — i.e. a real network call this check must not make
     (module docstring: "no network, no real scrape", same constraint
     T19's own DoD states). The score-bearing branches
     (``postingsRead > 0``, a real ``bestScore``) are exercised instead
     by ``frontend/tests/boardQuietDayLogic.test.ts``, which does not
     need a live scan to assert what the copy says once those fields
     exist.

Usage::

    .venv/Scripts/python.exe scripts/t85_phone_browser_check.py [outdir]

Requires ``npx vite build`` to have produced ``frontend/dist`` already,
and Playwright's Chromium installed. Every Playwright call below carries
an explicit timeout — an unbounded run on this project once hung for
three hours.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.t64_split_view_browser_check import _wait_for_port  # noqa: E402
from scripts.t81_board_browser_check import _seed as _seed_populated  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
VIEWPORT_PHONE = {"width": 390, "height": 844}

STAGE_ORDER = ["new", "shortlist", "applied", "replied", "out"]


def _common_setup(data_dir: Path) -> None:
    """Shared config scaffolding every seed variant below needs —
    pulled out so each one differs only in which jobs it adds, not in
    repeating this boilerplate three times."""
    os.environ["JOBFINDER_DATA_DIR"] = str(data_dir)
    os.environ.pop("ANTHROPIC_API_KEY", None)

    import jobfinder.paths as paths

    paths.setup_data_environment()

    from jobfinder.config import load_config, save_config

    config = load_config(paths.config_path())
    config.anthropic.api_key = "sk-ant-fake-browser-check-not-a-real-key"  # gitleaks:allow -- synthetic
    config.match_threshold = 0
    resume_dir = paths.resume_uploads_dir()
    resume_dir.mkdir(parents=True, exist_ok=True)
    resume_file = resume_dir / "t85-check-resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    config.resume.path = str(resume_file)
    config.resume.original_filename = "t85-check-resume.pdf"
    save_config(config, paths.config_path())
    return config, paths


def _seed_empty(data_dir: Path) -> None:
    """First run (this task's brief): config exists, but no job has ever
    been seen -- every one of the five columns is genuinely empty."""
    _common_setup(data_dir)


def _seed_quiet_day(data_dir: Path) -> None:
    """Populated, with nothing staged in New -- the quiet day (this
    task's brief). Four jobs spread across Shortlist/Applied/Replied/Out,
    same per-job shape ``t81_board_browser_check.py``'s ``_seed`` uses,
    just with the "New: 4 jobs" block left out so New starts genuinely
    empty rather than filtered down to empty."""
    _common_setup(data_dir)

    from jobfinder.matcher.models import JobPosting, MatchResult
    from jobfinder.storage.database import Database
    from jobfinder import paths

    db = Database(str(paths.db_path()))
    now = datetime.utcnow()

    def seed_job(job_id: str, score: int) -> JobPosting:
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
            compensation_min=100000,
            compensation_max=130000,
            compensation_currency="USD",
            compensation_interval="yearly",
        )
        db.mark_seen([job])
        db.save_match(MatchResult(job=job, score=score, reasoning="seed", matched_at=now - timedelta(seconds=1)))
        return job

    seed_job("t85-shortlist-0", 70)
    db.set_stage("t85-shortlist-0", "shortlist")
    seed_job("t85-applied-0", 75)
    db.set_stage("t85-applied-0", "applied")
    seed_job("t85-replied-0", 90)
    db.set_stage("t85-replied-0", "replied", note="interview scheduled")
    seed_job("t85-out-0", 55)
    db.set_stage("t85-out-0", "out", removed_by="user")


def _launch(data_dir: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["JOBFINDER_DATA_DIR"] = str(data_dir)
    env.pop("ANTHROPIC_API_KEY", None)
    return subprocess.Popen(
        [str(PYTHON), "-m", "jobfinder", "--no-browser"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _no_horizontal_page_scroll(page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


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


def _check_populated(pw, outdir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="jf-t85-populated-", ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp) / "data"
        _seed_populated(data_dir)
        proc = _launch(data_dir)
        try:
            url = _wait_for_port(proc)
            print(f"[populated] server up at {url}")
            browser = pw.chromium.launch(headless=True, timeout=TIMEOUT_MS)
            try:
                page = browser.new_page(viewport=VIEWPORT_PHONE)
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-stage-strip", timeout=TIMEOUT_MS)

                visible_columns = page.evaluate(
                    "() => [...document.querySelectorAll('.board-column')].filter(c => c.offsetParent !== null).length"
                )
                print(f"visible_columns={visible_columns}")
                assert visible_columns == 1, f"expected exactly one visible column, found {visible_columns}"

                pills = page.locator(".board-stage-pill")
                assert pills.count() == 5, f"expected five stage pills, found {pills.count()}"
                print("stage_pills=5")

                active_before = page.evaluate(
                    "() => document.querySelector('.board-column[data-active=\"true\"]').dataset.stage"
                )
                pills.nth(2).click()  # Applied
                page.wait_for_timeout(200)
                active_after = page.evaluate(
                    "() => document.querySelector('.board-column[data-active=\"true\"]').dataset.stage"
                )
                print(f"active_stage_before={active_before} active_stage_after={active_after}")
                assert active_after == "applied" and active_after != active_before, (
                    "selecting another stop did not change which column shows"
                )

                first_card = page.locator('.board-column[data-active="true"] .board-card').first
                job_id = first_card.get_attribute("data-job-id")
                first_card.evaluate("(el) => el.click()")
                page.wait_for_selector(".board-card-sheet", timeout=TIMEOUT_MS)
                print(f"sheet_opened_for={job_id}")

                before_stage = _stage_of(_board(page), job_id)
                page.evaluate(
                    "() => document.querySelectorAll('.board-card-sheet .job-panel-stage-btn')[3].click()"
                )  # Replied
                deadline = time.monotonic() + 5.0
                after_stage = before_stage
                while time.monotonic() < deadline:
                    after_stage = _stage_of(_board(page), job_id)
                    if after_stage == "replied":
                        break
                    time.sleep(0.1)
                print(f"job_id={job_id} before_stage={before_stage} after_stage={after_stage}")
                assert after_stage == "replied", (
                    f"the sheet's move control left the API at {after_stage!r}, expected 'replied'"
                )

                assert _no_horizontal_page_scroll(page), "the page scrolls horizontally at 390x844"
                print("no_horizontal_page_scroll=ok")

                filters_btn = page.locator(".board-view-filters-btn")
                assert filters_btn.count() == 1 and filters_btn.is_visible()
                btn_box = filters_btn.bounding_box()
                cards_box = page.locator('.board-column[data-active="true"] .board-column-body').bounding_box()
                print(f"filters_btn_box={btn_box} cards_box={cards_box}")
                assert btn_box is not None and cards_box is not None
                overlap = not (
                    btn_box["y"] + btn_box["height"] <= cards_box["y"]
                    or btn_box["y"] >= cards_box["y"] + cards_box["height"]
                )
                assert not overlap, "the Filters button's box overlaps the visible column's card list"
                print("filters_button_reachable_without_covering_cards=ok")

                shot = outdir / "t85-phone-populated.png"
                page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                print(f"captured={shot}")
                page.close()
            finally:
                browser.close(reason="t85 populated check done")
        finally:
            _stop(proc)


def _check_first_run(pw, outdir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="jf-t85-empty-", ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp) / "data"
        _seed_empty(data_dir)
        proc = _launch(data_dir)
        try:
            url = _wait_for_port(proc)
            print(f"[first-run] server up at {url}")
            browser = pw.chromium.launch(headless=True, timeout=TIMEOUT_MS)
            try:
                page = browser.new_page(viewport=VIEWPORT_PHONE)
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-stage-strip", timeout=TIMEOUT_MS)

                for i, stage in enumerate(STAGE_ORDER):
                    page.locator(".board-stage-pill").nth(i).click()
                    page.wait_for_timeout(100)
                    title_el = page.locator('.board-column[data-active="true"] .board-column-empty-title')
                    subtitle_el = page.locator('.board-column[data-active="true"] .board-column-empty-subtitle')
                    title = title_el.inner_text()
                    subtitle = subtitle_el.inner_text()
                    print(f"first_run_stage={stage} title={title!r} subtitle_len={len(subtitle)}")
                    assert title, f"stage {stage!r} rendered no first-run title"
                    assert subtitle, f"stage {stage!r} rendered no first-run subtitle"

                page.locator(".board-stage-pill").nth(0).click()  # New
                page.wait_for_timeout(100)
                scan_cta = page.locator('.board-column[data-active="true"] .board-column-empty-scan-btn')
                assert scan_cta.count() == 1, "New's first-run state is missing the scan CTA"
                print("first_run_new_has_scan_cta=ok")

                page.locator(".board-stage-pill").nth(1).click()  # Shortlist
                page.wait_for_timeout(100)
                other_cta = page.locator('.board-column[data-active="true"] .board-column-empty-scan-btn')
                assert other_cta.count() == 0, "Shortlist's first-run state wrongly carries the scan CTA"
                print("only_new_has_scan_cta=ok")

                shot = outdir / "t85-phone-firstrun.png"
                page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                print(f"captured={shot}")
                page.close()
            finally:
                browser.close(reason="t85 first-run check done")
        finally:
            _stop(proc)


def _check_quiet_day(pw, outdir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="jf-t85-quiet-", ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp) / "data"
        _seed_quiet_day(data_dir)
        proc = _launch(data_dir)
        try:
            url = _wait_for_port(proc)
            print(f"[quiet-day] server up at {url}")
            browser = pw.chromium.launch(headless=True, timeout=TIMEOUT_MS)
            try:
                page = browser.new_page(viewport=VIEWPORT_PHONE)
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)

                before = page.evaluate(
                    "async () => { const r = await fetch('/api/board'); const b = await r.json(); "
                    "return Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v.length])); }"
                )
                print(f"[quiet-day] column_counts={before}")
                assert before["new"] == 0, "the seed put something in New -- quiet-day needs it empty"
                assert any(before[s] > 0 for s in ("shortlist", "applied", "replied", "out")), (
                    "the seed left every other column empty too -- that's first-run, not quiet-day"
                )

                # A real scan, not a faked ScanState (module docstring):
                # no active sources are configured, so this completes
                # with no network call at all.
                page.evaluate(
                    "async () => { const r = await fetch('/api/scan', { method: 'POST' });"
                    " if (!r.ok) throw new Error('scan trigger failed: ' + r.status); }"
                )

                deadline = time.monotonic() + 10.0
                state = None
                while time.monotonic() < deadline:
                    state = page.evaluate("async () => (await fetch('/api/state')).json()")
                    if state.get("last_scan_completed_at"):
                        break
                    time.sleep(0.2)
                print(f"scan_state_after_trigger={state}")
                assert state and state.get("last_scan_completed_at"), "the triggered scan never completed"
                assert state.get("last_best_score") is None, "expected no score at all from a no-active-sources scan"

                # `BoardView.tsx` only re-fetches `/api/state`/`/api/settings`
                # from its own `reload()` (mount, a move, a filter commit,
                # Scan now) -- this check triggered the scan directly via
                # `fetch`, bypassing that, so a page reload is what a real
                # user would see on their next visit/refresh after a scan
                # completes in the background.
                page.reload(timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-stage-strip", timeout=TIMEOUT_MS)

                page.locator(".board-stage-pill").nth(0).click()  # New
                page.wait_for_timeout(300)
                title = page.locator('.board-column[data-active="true"] .board-column-empty-title').inner_text()
                subtitle = page.locator('.board-column[data-active="true"] .board-column-empty-subtitle').inner_text()
                print(f"quiet_day_title={title!r} subtitle={subtitle!r}")
                assert title == "You are caught up"
                assert "nothing new" in subtitle.lower(), (
                    f"expected the zero-postings quiet-day sentence, got {subtitle!r}"
                )
                # No score ever existed this cycle -- no action buttons,
                # per the module docstring's "If only part is available,
                # ... render only that".
                actions = page.locator('.board-column[data-active="true"] .board-column-empty-action-btn')
                assert actions.count() == 0, "an action button appeared with no real score to act on"
                print("no_actions_without_a_real_score=ok")

                shot = outdir / "t85-phone-quietday.png"
                page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                print(f"captured={shot}")
                page.close()
            finally:
                browser.close(reason="t85 quiet-day check done")
        finally:
            _stop(proc)


def main() -> int:
    outdir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t85-phone-"))
    outdir.mkdir(parents=True, exist_ok=True)

    if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
        print("frontend/dist/index.html not found -- run `npx vite build` in frontend/ first.", file=sys.stderr)
        return 1

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")

    with sync_playwright() as pw:
        _check_populated(pw, outdir)
        if original_browsers_path is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original_browsers_path

        _check_first_run(pw, outdir)
        if original_browsers_path is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original_browsers_path

        _check_quiet_day(pw, outdir)

    print("\nT85_PHONE_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
