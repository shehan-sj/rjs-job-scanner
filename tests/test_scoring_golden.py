"""Golden-file test for jobfinder/matcher/local_matcher.py::score_job (T4).

Pins the full `(score, signals)` output of `score_job` for ~30 committed
`JobPosting` fixtures against a committed `ResumeProfile`, so any unintended
change to scoring shows up as a concrete, reviewable diff instead of silently
shifting match rankings.

Fixtures live under tests/fixtures/golden/ as plain JSON (not pickled Python)
so a diff on either the inputs or the expectation is human-reviewable:
  - profile.json   — one committed ResumeProfile, built directly (no
                      Anthropic calls, no network — see ResumeProfile in
                      jobfinder/resume/profiler.py).
  - jobs.json       — ~30 realistic JobPosting fixtures covering different
                      seniorities, remote/hybrid/on-site/unknown arrangement,
                      strong/weak/no skill overlap, missing fields, and both
                      LinkedIn-structured and description-only postings.
  - expected.json   — the committed `(score, signals)` tuple for every job_id
                      in jobs.json, keyed by job_id so fixture reordering
                      doesn't produce a spurious diff.

Is scoring date-dependent? ``score_job`` (and everything it calls —
``_score_seniority``, ``_score_function``, ``_normalize_terms``, the regex
title/skill matching) never reads the wall clock. The *only* call to
``datetime.utcnow()`` in local_matcher.py sets ``MatchResult.matched_at``,
which does not feed into ``score`` or ``signals`` at all. So this golden file
cannot rot on a calendar boundary — but to keep that true even if someone
later threads a date into scoring, this test deliberately compares only
``score`` and ``signals`` (never ``matched_at``), and asserts that
``matched_at`` stays a ``datetime`` without pinning its value.

Regenerating the expectation is deliberate, not a way to make a failing test
pass:

    .venv/Scripts/python.exe -m pytest tests/test_scoring_golden.py --update-golden
    # or: .venv/Scripts/python.exe scripts/regen_golden.py

Either way, inspect `git diff tests/fixtures/golden/expected.json` and explain
*why* the scoring output changed before committing.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from jobfinder.matcher.local_matcher import score_job
from jobfinder.matcher.models import JobPosting
from jobfinder.resume.profiler import ResumeProfile

_GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
_JOBS_PATH = _GOLDEN_DIR / "jobs.json"
_PROFILE_PATH = _GOLDEN_DIR / "profile.json"
_EXPECTED_PATH = _GOLDEN_DIR / "expected.json"


def _load_profile() -> ResumeProfile:
    with open(_PROFILE_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    return ResumeProfile(**data)


def _load_jobs() -> list[JobPosting]:
    with open(_JOBS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    return [JobPosting(**job) for job in data]


def _compute_actual() -> dict[str, dict]:
    """Score every golden job and return {job_id: {"score": ..., "signals": ...}}."""
    profile = _load_profile()
    jobs = _load_jobs()
    actual: dict[str, dict] = {}
    for job in jobs:
        result = score_job(profile, job)
        assert isinstance(result.matched_at, datetime.datetime)
        actual[job.job_id] = {"score": result.score, "signals": result.signals}
    return actual


def test_golden_fixture_has_at_least_thirty_jobs():
    jobs = _load_jobs()
    assert len(jobs) >= 30
    ids = [j.job_id for j in jobs]
    assert len(ids) == len(set(ids)), "duplicate job_id in tests/fixtures/golden/jobs.json"


def test_scoring_golden(request):
    """Compare score_job output for every golden fixture against expected.json.

    Run with --update-golden to regenerate expected.json instead of asserting.
    """
    actual = _compute_actual()

    if request.config.getoption("--update-golden"):
        _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        with open(_EXPECTED_PATH, "w", encoding="utf-8") as fh:
            json.dump(actual, fh, indent=2, sort_keys=True)
            fh.write("\n")
        pytest.skip(
            "tests/fixtures/golden/expected.json regenerated — review the "
            "diff (`git diff tests/fixtures/golden/expected.json`) and "
            "explain why scoring changed before committing, then rerun "
            "without --update-golden to confirm it now passes."
        )

    assert _EXPECTED_PATH.exists(), (
        "tests/fixtures/golden/expected.json is missing. Generate it once with "
        "`pytest tests/test_scoring_golden.py --update-golden` and review the "
        "diff before committing."
    )
    with open(_EXPECTED_PATH, encoding="utf-8") as fh:
        expected = json.load(fh)

    assert set(actual.keys()) == set(expected.keys()), (
        "job_id set in jobs.json no longer matches expected.json — regenerate with --update-golden and review the diff."
    )
    for job_id in sorted(actual):
        assert actual[job_id] == expected[job_id], (
            f"scoring changed for {job_id!r}: expected {expected[job_id]}, got {actual[job_id]}"
        )


def test_scoring_golden_is_deterministic_across_runs():
    """Running the suite twice in a row must give identical results."""
    first = _compute_actual()
    second = _compute_actual()
    for job_id in first:
        assert first[job_id] == second[job_id]
