#!/usr/bin/env python
"""T81 DoD browser check — real headless Chromium, run on demand.

Not part of ``pytest -q`` or ``scripts/smoke.py`` (both stay offline and
Chrome-free) — a manual/CI-optional verification tool, same pattern as
``scripts/t64_split_view_browser_check.py``, whose seeding/launch/URL
helpers (``_seed``/``_wait_for_port``) this script reuses rather than
re-deriving.

Asserts, per this task's DoD:

  * at 2560x1400: all five columns are present, each column's rendered
    card count equals the payload's (read live off ``GET /api/board``,
    not assumed), there is no horizontal scroll, and the board fills the
    viewport height with no dead strip below it (measured against the
    viewport, not eyeballed — the lesson T64/T65 each had to relearn);
  * at 390x844: no horizontal scroll (the phone layout itself is T85's
    scope — this is only the "doesn't overflow" floor);
  * Tab order starts on the board (the rail's first/default destination),
    and pressing a digit key on a focused card moves it to that stage,
    read back through ``GET /api/board`` rather than only the DOM.

Usage::

    .venv/Scripts/python.exe scripts/t81_board_browser_check.py

Requires ``npx vite build`` to have produced ``frontend/dist`` already,
and Playwright's Chromium (``playwright install chromium``) installed.
Every Playwright call below has an explicit timeout — an earlier
unbounded run on this project hung for three hours.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 15000
VIEWPORT_DESKTOP = {"width": 2560, "height": 1400}
VIEWPORT_PHONE = {"width": 390, "height": 844}

sys.path.insert(0, str(REPO_ROOT))
from scripts.t64_split_view_browser_check import _wait_for_port  # noqa: E402


def _seed(data_dir: Path) -> None:
    import os

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
    resume_file = resume_dir / "t81-check-resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    config.resume.path = str(resume_file)
    config.resume.original_filename = "t81-check-resume.pdf"
    save_config(config, paths.config_path())

    db = Database(str(paths.db_path()))
    now = datetime.utcnow()

    def seed_job(job_id: str, score: int, **kwargs) -> JobPosting:
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
            **kwargs,
        )
        db.mark_seen([job])
        db.save_match(MatchResult(job=job, score=score, reasoning="seed", matched_at=now - timedelta(seconds=1)))
        return job

    # New: 4 jobs, two scan batches.
    for i in range(4):
        seed_job(f"t81-new-{i}", 60 + i)

    # Shortlist: 2 jobs.
    for i in range(2):
        seed_job(f"t81-shortlist-{i}", 70 + i)
        db.set_stage(f"t81-shortlist-{i}", "shortlist")

    # Applied: 3 jobs.
    for i in range(3):
        seed_job(f"t81-applied-{i}", 75 + i)
        db.set_stage(f"t81-applied-{i}", "applied")

    # Replied: 1 job.
    seed_job("t81-replied-0", 90)
    db.set_stage("t81-replied-0", "replied", note="interview scheduled")

    # Not for me: 1 job.
    seed_job("t81-out-0", 55)
    db.set_stage("t81-out-0", "out", removed_by="user")


def _board_counts(page) -> dict:
    return page.evaluate(
        "async () => { const r = await fetch('/api/board', { credentials: 'same-origin' }); const b = await r.json(); "
        "return Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v.length])); }"
    )


def _no_horizontal_scroll(page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


OUTDIR = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t81-board-"))


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUTDIR.mkdir(parents=True, exist_ok=True)

    if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
        print("frontend/dist/index.html not found -- run `npx vite build` in frontend/ first.", file=sys.stderr)
        return 1

    import os

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")

    with tempfile.TemporaryDirectory(prefix="jf-t81-check-", ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp) / "data"
        _seed(data_dir)

        env = dict(os.environ)
        env["JOBFINDER_DATA_DIR"] = str(data_dir)
        env.pop("ANTHROPIC_API_KEY", None)

        if original_browsers_path is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original_browsers_path

        proc = subprocess.Popen(
            [str(PYTHON), "-m", "jobfinder", "--no-browser"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            url = _wait_for_port(proc)
            print(f"server up at {url}")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, timeout=TIMEOUT_MS)

                # ── 2560x1400 ───────────────────────────────────────────
                page = browser.new_page(viewport=VIEWPORT_DESKTOP)
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)
                page.wait_for_selector(".board-column", timeout=TIMEOUT_MS)

                columns = page.locator(".board-column")
                assert columns.count() == 5, f"expected five columns, found {columns.count()}"
                print("columns_present=5")

                payload_counts = _board_counts(page)
                print(f"payload_counts={payload_counts}")
                stage_order = ["new", "shortlist", "applied", "replied", "out"]
                for i, stage in enumerate(stage_order):
                    rendered = columns.nth(i).locator(".board-card").count()
                    expected = payload_counts[stage]
                    print(f"column={stage} rendered={rendered} expected={expected}")
                    assert rendered == expected, (
                        f"column {stage!r} rendered {rendered} cards but the payload carried {expected}"
                    )

                assert _no_horizontal_scroll(page), "the board causes horizontal scroll at 2560x1400"
                print("no_horizontal_scroll_2560=ok")

                # ── No dead strip: the board's content fills the
                # viewport height, measured against the viewport, not
                # eyeballed (the T64/T65 lesson).
                board_box = page.locator(".board-view").bounding_box()
                assert board_box is not None, "the board did not render"
                viewport_h = VIEWPORT_DESKTOP["height"]
                dead_strip = viewport_h - (board_box["y"] + board_box["height"])
                print(
                    f"board_bottom={board_box['y'] + board_box['height']:.0f} viewport_h={viewport_h} dead_strip={dead_strip:.0f}"
                )
                assert dead_strip <= 40, f"the board leaves a {dead_strip:.0f}px dead strip below it at 2560x1400"

                # ── Tab order starts on the board (the rail's default
                # destination).
                page.keyboard.press("Tab")
                first_label = page.evaluate("() => document.activeElement.getAttribute('aria-label')")
                print(f"first_tab_stop={first_label!r}")
                assert first_label is not None and first_label.startswith("Board"), (
                    f"expected the rail's Board destination to be the first Tab stop, got {first_label!r}"
                )

                # ── Keyboard 1-5 moves the focused card; the resulting
                # stage is read back through GET /api/board, not only the
                # DOM.
                first_new_card = page.locator('.board-column[data-stage="new"] .board-card').first
                job_id = first_new_card.get_attribute("data-job-id")
                first_new_card.evaluate("(el) => el.focus()")
                page.keyboard.press("3")  # 1=new 2=shortlist 3=applied 4=replied 5=out

                def _stage_of(job_id: str) -> str | None:
                    board = page.evaluate(
                        "async () => { const r = await fetch('/api/board', { credentials: 'same-origin' }); return r.json(); }"
                    )
                    for stage, cards in board.items():
                        if any(c["job_id"] == job_id for c in cards):
                            return stage
                    return None

                import time

                deadline = time.monotonic() + 5.0
                stage = None
                while time.monotonic() < deadline:
                    stage = _stage_of(job_id)
                    if stage == "applied":
                        break
                    time.sleep(0.1)
                print(f"job_id={job_id} stage_after_keyboard_3={stage!r}")
                assert stage == "applied", (
                    f"pressing '3' on a New card did not move it to Applied via the API, got {stage!r}"
                )

                # ── Horizontal fill. "No horizontal scroll" is trivially
                # true when the content is too NARROW, so it is not evidence
                # that the width is used -- which is the complaint T64 and
                # T65 each had to answer. Measure the columns against the
                # viewport instead.
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
                used = spread["right"] - spread["left"]
                margin = spread["vw"] - spread["right"]
                print(
                    f"columns_left={spread['left']:.0f} right={spread['right']:.0f} "
                    f"used={used:.0f} of vw={spread['vw']} right_margin={margin:.0f}"
                )
                assert used >= spread["vw"] - 220, (
                    f"the five columns use only {used:.0f}px of a {spread['vw']}px viewport "
                    f"-- the dead-width regression T64 and T65 each had to fix"
                )

                shot = OUTDIR / "t81-board-2560x1400.png"
                page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
                print(f"captured={shot}")
                page.close()

                # ── 390x844: no horizontal scroll only (phone layout is T85).
                phone_page = browser.new_page(viewport=VIEWPORT_PHONE)
                phone_page.set_default_timeout(TIMEOUT_MS)
                phone_page.goto(url, timeout=TIMEOUT_MS)
                phone_page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)
                assert _no_horizontal_scroll(phone_page), "the board causes horizontal scroll at 390x844"
                print("no_horizontal_scroll_390=ok")
                phone_shot = OUTDIR / "t81-board-390x844.png"
                phone_page.screenshot(path=str(phone_shot), timeout=TIMEOUT_MS)
                print(f"captured={phone_shot}")
                phone_page.close()

                browser.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("T81_BROWSER_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
