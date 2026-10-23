"""Behaviour-pinning tests for jobfinder/orchestrator.py pure helpers.

Covers _title_is_relevant (:37) and _check_geo (:78).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from jobfinder.config import LocationFilterConfig
from jobfinder.matcher.models import JobPosting
from jobfinder.orchestrator import _check_geo, _title_is_relevant, agent_may_remove


def _job(title: str, source: str = "indeed", work_arrangement: str = "Unknown", location: str = "") -> JobPosting:
    return JobPosting(
        job_id="1",
        title=title,
        company="Acme",
        location=location,
        description="",
        url="https://example.com/1",
        source=source,
        work_arrangement=work_arrangement,
    )


@dataclass
class _Profile:
    job_titles: list[str] = field(default_factory=list)


# ── _title_is_relevant ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title, source, target_titles, expected",
    [
        ("Barista", "linkedin", ["QA Engineer"], True),  # linkedin always passes
        # T51 inverted the next two. They used to assert that a
        # QA title passed the gate even for a profile with NO target titles at
        # all — the bias in its clearest form, since the gate runs before
        # scoring and so decided what a non-QA user was ever shown. A profile
        # that names no target role now has no domain signal to match, rather
        # than borrowing QA's.
        ("Senior Quality Assurance Lead", "indeed", [], False),
        ("QA at Acme", "indeed", [], False),
        # ...but the same titles still pass for someone who does target them,
        # on their own profile's vocabulary rather than a hardcoded list.
        ("QA at Acme", "indeed", ["QA Engineer"], True),
        # A short acronym from the user's own titles is a signal on its own:
        # the word-overlap rule below only sees words longer than three
        # characters, so "RN" would otherwise be invisible. This is the gap the
        # deleted QA regex was really filling, now filled for every field.
        ("RN — Night Shift", "indeed", ["RN", "Registered Nurse"], True),
        # A non-QA profession's own posting passes on overlap alone.
        ("Litigation Paralegal", "indeed", ["Litigation Paralegal"], True),
        # ...and an unrelated QA posting does not pass for that profile.
        ("Senior QA Engineer", "indeed", ["Litigation Paralegal"], False),
        # Single generic word overlap alone ("engineer" is generic) is not enough.
        ("Engineer", "indeed", ["Senior Engineer"], False),
        # Two non-generic ("test"/"automation") word hits pass on their own.
        ("Test Automation Specialist", "indeed", ["Test Automation"], True),
        # One meaningful ("automation") + one generic ("engineer") hit passes.
        ("Automation Engineer", "indeed", ["Automation Engineer"], True),
        # Only one meaningful hit and no generic hit: not enough.
        ("Automation Guru", "indeed", ["Automation Specialist"], False),
        ("Warehouse Associate", "indeed", ["Automation Specialist"], False),  # no overlap at all
    ],
)
def test_title_is_relevant(title, source, target_titles, expected):
    job = _job(title, source=source)
    assert _title_is_relevant(job, _Profile(job_titles=target_titles)) is expected


# ── _check_geo ────────────────────────────────────────────────────────────────


def _geo_cfg(preferred_regions=None, search_country="", remote_ok_worldwide=False) -> LocationFilterConfig:
    return LocationFilterConfig(
        preferred_regions=preferred_regions or ["Ontario", "GTA"],
        search_country=search_country,
        remote_ok_worldwide=remote_ok_worldwide,
    )


def test_check_geo_remote_worldwide_ok_returns_none():
    job = _job("QA Engineer", work_arrangement="Remote", location="Remote")
    assert _check_geo(job, _geo_cfg(remote_ok_worldwide=True)) is None


def test_check_geo_structured_region_match_returns_none():
    job = _job("QA Engineer", work_arrangement="On-site", location="Toronto, Ontario")
    job.region = "Ontario"
    assert _check_geo(job, _geo_cfg()) is None


def test_check_geo_substring_location_match_returns_none():
    job = _job("QA Engineer", work_arrangement="Hybrid", location="Waterloo, GTA area")
    assert _check_geo(job, _geo_cfg()) is None


def test_check_geo_empty_location_returns_none():
    job = _job("QA Engineer", work_arrangement="Unknown", location="")
    assert _check_geo(job, _geo_cfg()) is None


@pytest.mark.parametrize(
    "work_arrangement, location, expected_prefix",
    [
        ("Remote", "Remote - Germany", "Remote"),
        ("On-site", "Berlin, Germany", "On-site"),
        ("Unknown", "Berlin, Germany", "Unverified location"),
    ],
)
def test_check_geo_outside_preferred_region_warns(work_arrangement, location, expected_prefix):
    job = _job("QA Engineer", work_arrangement=work_arrangement, location=location)
    warning = _check_geo(job, _geo_cfg(remote_ok_worldwide=False))
    assert warning is not None
    assert warning.startswith(expected_prefix)


# ── The agentic filter may only remove a job the user has not touched ──────
#
# the product spec. Before this guard the scan called `mark_removed`
# directly, which did not clear the other stage tables — so a re-scan that
# disqualified an already-shortlisted job left it in both `shortlisted_jobs`
# and `removed_jobs`, and `stage_of`'s precedence then hid the removal
# entirely. Found by the Lead while verifying T80.


@pytest.mark.parametrize(
    "stage, may_remove",
    [
        ("new", True),
        ("shortlist", False),
        ("applied", False),
        ("replied", False),
        ("out", False),
    ],
)
def test_agent_may_remove_only_out_of_new(stage, may_remove):
    assert agent_may_remove(stage) is may_remove


def test_agent_disqualification_leaves_a_user_moved_job_where_the_user_put_it(tmp_path):
    """The storage-level consequence, not just the predicate."""
    from jobfinder.storage.database import Database

    db = Database(str(tmp_path / "board.db"))
    job = JobPosting(
        job_id="agent-guard-1",
        title="QA Engineer",
        company="Example",
        location="Remote",
        description="",
        url="https://example.com/agent-guard-1",
        source="linkedin",
    )
    db.mark_seen([job])
    db.set_stage(job.job_id, "shortlist")

    # What the scan loop does, with the guard in place.
    if agent_may_remove(db.stage_of(job.job_id)):
        db.set_stage(job.job_id, "out", removed_by="agent")

    assert db.stage_of(job.job_id) == "shortlist"
    assert job.job_id not in db.get_removed_job_ids()
