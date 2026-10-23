#!/usr/bin/env python
"""Shared seeding/launch helpers — formerly T64's split master-detail check.

**Retired as a check, kept as a module (T87).** This file used to be a
standalone browser check for the split master-detail layout (a 580px list
beside an always-visible `.job-detail-pane`, `.jobs-view-matches-list`,
`.rail-desktop`). T81 replaced that whole shell with the five-column board
(`frontend/src/views/BoardView.tsx`); none of those selectors
exist any more, so the old `main()`/`_check_count` assertions here could
only ever fail — a committed check that cannot pass teaches people to
ignore red, so it has been removed rather than left to rot or quietly
skipped.

What's still here, and why it wasn't deleted outright: `_seed` and
`_wait_for_port` are real, still-needed test infrastructure —
``scripts/t78_palette_browser_check.py``, ``scripts/t81_board_browser_check.py``,
``scripts/t85_phone_browser_check.py`` and ``scripts/t88_palette_screens_check.py``
all import one or both of them rather than re-deriving the same seed/launch
logic a fourth and fifth time. Deleting the file would have broken all four
of those *currently accurate* checks. The board-layout assertions this file
used to make (card actions reachable, horizontal-fill width, selection
state) are re-covered by ``scripts/t81_board_browser_check.py``,
``scripts/t82_drag_browser_check.py`` and ``scripts/t83_panel_browser_check.py``
against the real board, so nothing this file used to prove is now
unproven — it's proven elsewhere, against what actually ships.

Not part of ``pytest -q`` or ``scripts/smoke.py`` (both stay offline and
Chrome-free) — this module is only ever imported by the browser checks
above, not run on its own; it has no ``main()`` and nothing to run.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def _seed(data_dir: Path, count: int) -> None:
    import os

    os.environ["JOBFINDER_DATA_DIR"] = str(data_dir)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    sys.path.insert(0, str(REPO_ROOT))

    import jobfinder.paths as paths

    paths.setup_data_environment()

    from jobfinder.config import load_config, save_config
    from jobfinder.matcher.models import JobPosting, MatchResult
    from jobfinder.storage.database import Database

    config = load_config(paths.config_path())
    config.anthropic.api_key = "sk-ant-fake-browser-check-not-a-real-key"  # gitleaks:allow -- synthetic
    config.match_threshold = 0  # permissive -- not what callers of this helper are proving
    resume_dir = paths.resume_uploads_dir()
    resume_dir.mkdir(parents=True, exist_ok=True)
    resume_file = resume_dir / "t64-check-resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    config.resume.path = str(resume_file)
    config.resume.original_filename = "t64-check-resume.pdf"
    save_config(config, paths.config_path())

    db = Database(str(paths.db_path()))
    now = datetime.utcnow()

    # Deliberately shuffled score order on insert (not already sorted) so
    # an assertion that the *rendered* order is score-descending actually
    # proves the client (or the server's ORDER BY) does the sorting,
    # rather than coincidentally matching insertion order.
    scores = [40 + (i * 53) % 60 for i in range(count)]

    for i, score in enumerate(scores):
        job_id = f"t64-job-{i}"
        matched_at = now - timedelta(seconds=i)
        job = JobPosting(
            job_id=job_id,
            title=f"QA Engineer #{i} (score {score})",
            company=f"Company {i}",
            location="Remote",
            description="seed job",
            url=f"https://example.com/{job_id}",
            source="linkedin",
            work_arrangement="Remote",
            employment_type="Full-time",
            listing_type="organic",
            company_url=f"https://example.com/company-{i}",
            job_level="Senior",
            job_function="Engineering",
            company_num_employees="500-1,000",
            compensation_min=100000,
            compensation_max=130000,
            compensation_currency="USD",
            compensation_interval="yearly",
        )
        db.mark_seen([job])
        db.save_match(
            MatchResult(
                job=job,
                score=score,
                reasoning=f"Seeded reasoning for job {i}.",
                matched_at=matched_at,
                signals={"title": 90, "skills": 55, "tools": 30},
            )
        )


def _wait_for_port(proc: subprocess.Popen, timeout_s: float = 15.0) -> str:
    deadline = time.monotonic() + timeout_s
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError(f"jobfinder server exited early (code {proc.returncode})")
            continue
        if "JobFinder is serving at:" in line:
            return line.split("JobFinder is serving at:", 1)[1].strip()
    raise TimeoutError("server did not report its URL within the timeout")
