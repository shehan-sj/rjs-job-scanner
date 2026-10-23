"""Behaviour-pinning tests for jobfinder/matcher/local_matcher.py.

Covers _score_seniority (:213) with concrete ratio-band/rank-gap cases, and
score_job (:125) via invariants only (never exact scores) per the product spec.
"""

from __future__ import annotations

import pytest

from jobfinder.matcher.local_matcher import (
    _W_FUNCTION,
    _W_SENIORITY,
    _W_SKILLS,
    _W_TITLE,
    _W_TOOLS,
    _score_seniority,
    score_job,
)
from jobfinder.matcher.models import JobPosting
from jobfinder.resume.profiler import ResumeProfile


def _job(**kwargs) -> JobPosting:
    base = dict(
        job_id="1",
        title="QA Engineer",
        company="Acme",
        location="",
        description="",
        url="https://example.com/1",
        source="indeed",
    )
    base.update(kwargs)
    return JobPosting(**base)


def _profile(**kwargs) -> ResumeProfile:
    base = dict(
        skills=["test automation", "selenium"],
        tools=["selenium", "jira"],
        job_titles=["QA Engineer"],
        seniority_levels=["Senior"],
        years_experience=5,
    )
    base.update(kwargs)
    return ResumeProfile(**base)


# ── Weight sanity ──────────────────────────────────────────────────────────────


def test_weights_sum_to_100():
    assert _W_TITLE + _W_SKILLS + _W_TOOLS + _W_SENIORITY + _W_FUNCTION == 100


# ── _score_seniority ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "seniority_levels, years_experience, job_level, experience_range, expected_signal, expected_frac",
    [
        # No signal at all on either axis -> neutral 50.
        ([], 0, "", "", 50, 0.5),
        # Years-ratio bands.
        ([], 5, "", "5-7 years", 100, 1.0),  # ratio 5/7≈0.71 in [0.7,1.5] -> full credit
        ([], 4, "", "7 years", 60, 0.6),  # ratio 4/7≈0.57 in [0.5,0.7) -> 0.6 credit
        ([], 10, "", "6 years", 60, 0.6),  # ratio 10/6≈1.67 in (1.5,2.0] -> 0.6 credit
        ([], 1, "", "10 years", 20, 0.2),  # ratio 0.1, outside both bands -> 0.2 credit
        # Rank-gap bands ("mid"=4, "senior"=6, "junior"=3, "intern"=0, "executive"=13 via dict).
        (["Senior"], 0, "mid-senior level", "", 100, 1.0),  # job_level dict rank 5, gap 1
        (["Mid"], 0, "senior", "", 60, 0.6),  # gap 2
        (["Junior"], 0, "senior", "", 30, 0.3),  # gap 3
        (["Intern"], 0, "executive", "", 0, 0.0),  # gap >3
        # Has one axis but not its counterpart on the other side -> neutral.
        ([], 0, "", "5-7 years", 50, 0.5),  # range but no candidate years
        ([], 5, "director", "", 50, 0.5),  # rank but no candidate rank
        # Both axes present and agreeing -> averaged to full credit.
        (["Senior"], 5, "mid-senior level", "5-7 years", 100, 1.0),
    ],
)
def test_score_seniority(
    seniority_levels, years_experience, job_level, experience_range, expected_signal, expected_frac
):
    profile = _profile(seniority_levels=seniority_levels, years_experience=years_experience)
    job = _job(job_level=job_level, experience_range=experience_range)
    pts, signal = _score_seniority(profile, job)
    assert signal == expected_signal
    assert pts == pytest.approx(_W_SENIORITY * expected_frac)


# ── score_job invariants (the product spec — assert invariants, never exact scores) ─


def test_score_job_score_within_bounds():
    profile = _profile()
    job = _job(description="selenium test automation jira")
    result = score_job(profile, job)
    assert 0 <= result.score <= 100


def test_score_job_signals_contains_all_five_keys():
    profile = _profile()
    job = _job(description="selenium test automation jira")
    result = score_job(profile, job)
    assert set(result.signals.keys()) == {"title", "skills", "tools", "seniority", "function"}


def test_score_job_signal_values_within_0_100():
    profile = _profile()
    job = _job(description="selenium test automation jira")
    result = score_job(profile, job)
    for key, value in result.signals.items():
        assert 0 <= value <= 100, f"{key} signal out of bounds: {value}"


def test_score_job_linkedin_structured_skills_scores_at_least_as_high_as_description_only():
    # Same job/description, which never mentions the skill terms in prose —
    # only the structured linkedin_skills list carries them. The structured
    # path must score >= the description-only path for otherwise identical
    # input (the product spec invariant), since explicit LinkedIn hits are worth
    # full weight (1.0x) vs description hits (0.4x) and desc text alone
    # contributes zero matches here.
    profile = _profile(skills=["selenium", "postman"])
    description = "We build a reliable QA platform for our customers."

    desc_only_job = _job(description=description, linkedin_skills=[])
    structured_job = _job(description=description, linkedin_skills=["Selenium", "Postman"])

    desc_result = score_job(profile, desc_only_job)
    structured_result = score_job(profile, structured_job)

    assert structured_result.signals["skills"] >= desc_result.signals["skills"]
    assert structured_result.score >= desc_result.score


def test_score_job_empty_profile_and_job_does_not_crash_and_stays_in_bounds():
    profile = ResumeProfile()
    job = _job(title="", description="", linkedin_skills=[])
    result = score_job(profile, job)
    assert 0 <= result.score <= 100
    assert set(result.signals.keys()) == {"title", "skills", "tools", "seniority", "function"}
