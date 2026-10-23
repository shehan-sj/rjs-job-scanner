"""Behaviour-pinning tests for jobfinder/dto.py (T13).

``MatchCardDTO`` / ``HistoryCardDTO`` are the implicit API contract between
the scan/storage layer and any UI (desktop widget today, web frontend from
Phase 2 on). These were extracted from ``jobfinder/ui/widget.py``'s private
``_result_to_card`` / ``_dict_to_card`` functions. Nothing pinned their shape
before this test, so a future field rename or drop on either path would
silently change the API contract instead of failing a test.

Covers:
  - jobfinder/dto.py never imports PySide6 or anything from jobfinder/ui
    (it is Qt-free — scripts/smoke.py's import sweep also guards this).
  - Both input shapes (a live-scan ``MatchResult`` and a
    ``get_recent_matches()`` history row) serialize to their field list
    exactly as committed in tests/fixtures/dto/card_fields.json.
  - ``agent_verified`` is present on both shapes (T41 added the matching
    ``match_history`` column and collapsed the asymmetry this test used to
    pin) — a history row written before that migration still defaults the
    field to falsy rather than inventing a value.
  - Value-preserving behaviour carried over from the original mappers:
    ``None``/missing-field defaulting, the 8/12-item skill truncation, the
    ``linkedin_skills`` vs ``skills_matched`` display preference, and
    ``date_posted`` always being "" on the history path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from jobfinder.dto import (
    HistoryCardDTO,
    MatchCardDTO,
    dict_to_card,
    result_to_card,
)
from jobfinder.matcher.models import JobPosting, MatchResult

_FIXTURE = Path(__file__).parent / "fixtures" / "dto" / "card_fields.json"


def _expected_fields() -> dict:
    return json.loads(_FIXTURE.read_text())


def _job(**overrides) -> JobPosting:
    base = dict(
        job_id="job-1",
        title="Senior Backend Engineer",
        company="Acme Inc.",
        location="Toronto, ON",
        description="...",
        url="https://boards.example/job-1",
        source="linkedin",
        work_arrangement="Remote",
        employment_type="Full-time",
        country="Canada",
        region="Ontario",
        city="Toronto",
        job_level="Mid-Senior level",
        job_function="Engineering",
        experience_range="5-7 years",
        listing_type="organic",
        job_url_direct="https://acme.example/careers/job-1",
        linkedin_skills=["Python", "SQL"],
        company_url="https://acme.example",
        company_logo="https://acme.example/logo.png",
        company_industry="Software",
        company_num_employees="1,001-5,000",
        compensation_min=120000.0,
        compensation_max=150000.0,
        compensation_currency="CAD",
        compensation_interval="yearly",
    )
    base.update(overrides)
    return JobPosting(**base)


def _match_result(**overrides) -> MatchResult:
    base = dict(
        job=_job(),
        score=82,
        reasoning="Strong skills overlap.",
        skills_matched=["Python", "SQL", "Django"],
        skills_missing=["Kubernetes"],
        geo_warning=None,
        tier="preferred",
        agent_verified=True,
        signals={"title": 85, "skills": 70, "seniority": 60, "function": 100},
    )
    base.update(overrides)
    return MatchResult(**base)


# ── Qt-free guard ───────────────────────────────────────────────────────────


def test_dto_module_does_not_import_qt_or_ui():
    """jobfinder/dto.py must stay importable with no PySide6 / jobfinder.ui
    on sys.modules — it is the mapper the web API depends on, and widget.py
    (which did import PySide6) was deleted in T33. The assertion is kept now
    that Qt is gone: it is what stops it coming back."""
    import jobfinder.dto as dto_module

    source = Path(dto_module.__file__).read_text()
    assert "PySide6" not in source
    assert "jobfinder.ui" not in source
    assert "from .ui" not in source and "from ..ui" not in source


def test_dto_module_importable_in_isolated_subprocess_without_qt():
    """Import jobfinder.dto in a fresh interpreter and assert PySide6 was
    never loaded — the real guarantee scripts/smoke.py's import sweep relies
    on, verified independently of whatever else this test session already
    imported."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import jobfinder.dto; "
            "assert not any(n == 'PySide6' or n.startswith('PySide6.') for n in sys.modules)",
        ],
        cwd=str(Path(__file__).parent.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ── Field-set snapshot ───────────────────────────────────────────────────────


def test_match_card_field_list_matches_committed_snapshot():
    expected = _expected_fields()["match_card_fields"]
    assert list(MatchCardDTO.model_fields.keys()) == expected
    assert list(result_to_card(_match_result()).keys()) == expected


def test_history_card_field_list_matches_committed_snapshot():
    expected = _expected_fields()["history_card_fields"]
    assert list(HistoryCardDTO.model_fields.keys()) == expected
    assert list(dict_to_card({"job_id": "job-1"}).keys()) == expected


def test_agent_verified_present_on_both_cards_no_asymmetry():
    """T41: ``agent_verified`` used to be the one field the two input shapes
    did not share — a live ``MatchResult`` carried it, but a
    ``get_recent_matches()`` row had no such column. T41 added the matching
    ``match_history.agent_verified`` column, so the two field sets are now
    identical and a pre-migration row (no stored value) defaults to
    falsy rather than being omitted."""
    match_fields = set(result_to_card(_match_result()).keys())
    history_fields = set(dict_to_card({"job_id": "job-1"}).keys())
    assert "agent_verified" in match_fields
    assert "agent_verified" in history_fields
    assert match_fields == history_fields
    assert dict_to_card({"job_id": "job-1"})["agent_verified"] is False
    assert set(MatchCardDTO.model_fields) == set(HistoryCardDTO.model_fields)


# ── Value-preserving behaviour from the original mappers ───────────────────


def test_result_to_card_preserves_original_field_values():
    r = _match_result()
    card = result_to_card(r)
    assert card["job_id"] == "job-1"
    assert card["score"] == 82
    assert card["url_direct"] == "https://acme.example/careers/job-1"
    # linkedin_skills present -> preferred over skills_matched, capped at 8
    assert card["skills"] == ["Python", "SQL"]
    assert card["matched_skills"] == ["Python", "SQL", "Django"]
    assert card["agent_verified"] is True
    assert card["signals"] == {"title": 85, "skills": 70, "seniority": 60, "function": 100}


def test_result_to_card_falls_back_to_skills_matched_when_no_linkedin_skills():
    r = _match_result(job=_job(linkedin_skills=[]))
    card = result_to_card(r)
    assert card["skills"] == ["Python", "SQL", "Django"][:8]


def test_result_to_card_date_posted_isoformat_or_empty_string():
    dt = datetime(2026, 3, 1, 12, 0, 0)
    r = _match_result(job=_job(date_posted=dt))
    assert result_to_card(r)["date_posted"] == dt.isoformat()

    r_none = _match_result(job=_job(date_posted=None))
    assert result_to_card(r_none)["date_posted"] == ""


def test_dict_to_card_defaults_missing_fields():
    card = dict_to_card({"job_id": "job-2", "title": "QA"})
    assert card["job_id"] == "job-2"
    assert card["title"] == "QA"
    assert card["company"] == ""
    assert card["location"] == ""
    assert card["score"] == 0
    assert card["skills"] == []
    assert card["matched_skills"] == []
    assert card["signals"] == {}
    assert card["geo_warning"] is None
    assert card["work_arrangement"] == "Unknown"
    assert card["employment_type"] == "Unknown"
    assert card["date_posted"] == ""


# ── T57: matched_at carried through to every card ───────────────────────


def test_result_to_card_matched_at_is_isoformat_string():
    """T57: ``matched_at`` (the scan timestamp) must reach the live-scan
    card as a parseable ISO-8601 string — it is how a client determines
    when a job was found at all, independent of whichever batching
    approach groups it with its scan-mates."""
    dt = datetime(2026, 3, 1, 12, 30, 45)
    r = _match_result(matched_at=dt)
    card = result_to_card(r)
    assert card["matched_at"] == dt.isoformat()
    # Round-trips through the standard library — "parseable", not just a string.
    assert datetime.fromisoformat(card["matched_at"]) == dt


def test_dict_to_card_matched_at_passes_through_history_row():
    """T57: a history row's ``matched_at`` (already a TEXT ISO-8601 column,
    ``jobfinder/storage/database.py``) reaches the card unchanged."""
    row = {"job_id": "job-4", "matched_at": "2026-01-01T00:00:01"}
    card = dict_to_card(row)
    assert card["matched_at"] == "2026-01-01T00:00:01"
    assert datetime.fromisoformat(card["matched_at"]) == datetime(2026, 1, 1, 0, 0, 1)


def test_dict_to_card_defaults_missing_matched_at_to_empty_string():
    card = dict_to_card({"job_id": "job-5"})
    assert card["matched_at"] == ""


def test_dict_to_card_parses_json_skill_columns():
    row = {
        "job_id": "job-3",
        "skills_matched": json.dumps(["Python", "Go"]),
        "linkedin_skills": json.dumps(["Python", "Go", "Rust"]),
    }
    card = dict_to_card(row)
    assert card["matched_skills"] == ["Python", "Go"]
    # linkedin_skills present -> preferred for display, capped at 8
    assert card["skills"] == ["Python", "Go", "Rust"]


def test_dict_to_card_tolerates_malformed_json_skill_columns():
    row = {"job_id": "job-4", "skills_matched": "not json", "linkedin_skills": "{"}
    card = dict_to_card(row)
    assert card["matched_skills"] == []
    assert card["skills"] == []


def test_dict_to_card_passes_through_compensation_none():
    row = {"job_id": "job-5"}
    card = dict_to_card(row)
    assert card["compensation_min"] is None
    assert card["compensation_max"] is None


# ── T58: scan_id carried through to the history card, for batch grouping ──


def test_dict_to_card_passes_through_scan_id():
    """T58: the Matches view groups cards by scan batch (DESIGN.md Variation
    B / the product spec). ``get_recent_matches()`` rows already carry
    ``scan_id`` (T57's column); this is the one place that gets dropped
    before reaching the client, same shape of bug ``matched_at`` had before
    T57 fixed it."""
    row = {"job_id": "job-6", "scan_id": "scan-abc123"}
    card = dict_to_card(row)
    assert card["scan_id"] == "scan-abc123"


def test_dict_to_card_defaults_missing_scan_id_to_none():
    """A pre-migration row (no ``scan_id`` column value) must not be merged
    into another batch by accident — it has to come through as ``None``,
    matching ``group_matches_by_scan``'s singleton-batch behaviour for a
    ``None`` scan_id (jobfinder/storage/database.py)."""
    card = dict_to_card({"job_id": "job-7"})
    assert card["scan_id"] is None
