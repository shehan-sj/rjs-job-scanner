#!/usr/bin/env python
"""Regenerate tests/fixtures/golden/expected.json (T4).

This is a thin, non-pytest wrapper around the same regeneration path used by
``pytest tests/test_scoring_golden.py --update-golden``. Use whichever you
find more convenient — they write the identical file.

Regenerating this file is a DELIBERATE act, not a way to make a failing
golden test pass. After running this script:

  1. Run `git diff tests/fixtures/golden/expected.json`.
  2. For every job_id whose score or signals changed, explain *why* in your
     commit message — e.g. "T12: seniority weight rebalance intentionally
     shifts golden-07/golden-18 by one signal point".
  3. Commit the regenerated file as its own, reviewable change — never bundle
     it into an unrelated diff.

Usage:
    .venv/Scripts/python.exe scripts/regen_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from jobfinder.matcher.local_matcher import score_job  # noqa: E402
from jobfinder.matcher.models import JobPosting  # noqa: E402
from jobfinder.resume.profiler import ResumeProfile  # noqa: E402

_GOLDEN_DIR = _REPO_ROOT / "tests" / "fixtures" / "golden"
_JOBS_PATH = _GOLDEN_DIR / "jobs.json"
_PROFILE_PATH = _GOLDEN_DIR / "profile.json"
_EXPECTED_PATH = _GOLDEN_DIR / "expected.json"


def main() -> int:
    with open(_PROFILE_PATH, encoding="utf-8") as fh:
        profile = ResumeProfile(**json.load(fh))

    with open(_JOBS_PATH, encoding="utf-8") as fh:
        jobs = [JobPosting(**job) for job in json.load(fh)]

    expected: dict[str, dict] = {}
    for job in jobs:
        result = score_job(profile, job)
        expected[job.job_id] = {"score": result.score, "signals": result.signals}

    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with open(_EXPECTED_PATH, "w", encoding="utf-8") as fh:
        json.dump(expected, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"Wrote {len(expected)} entries to {_EXPECTED_PATH}")
    print("Review the diff before committing: git diff tests/fixtures/golden/expected.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
