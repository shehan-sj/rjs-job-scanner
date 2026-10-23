"""Card DTOs — the implicit API contract between the scan/storage layer and
any UI (desktop widget today, web frontend in Phase 2/3).

``jobfinder/ui/widget.py`` used to carry this mapping as two private
functions (``_result_to_card`` / ``_dict_to_card``), ~35 fields each, with no
test pinning their shape. That mapper *is* the contract the future web API
serializes, so it is pulled out here, Qt-free, as Pydantic models.

Field values are intentionally typed ``Any``: this module's job is to
preserve the existing mapping byte-for-byte (including its ``None`` /
missing-field defaulting), not to introduce new validation or coercion that
could change behaviour. ``model_dump()`` on either model yields the same
plain ``dict`` the old functions returned, so call sites are unaffected.

``agent_verified`` (T41) -- whether the optional Claude verification pass
ran -- used to be the one field a live-scan ``MatchResult`` carried that a
``get_recent_matches()`` history row could not, since no ``match_history``
column existed for it. T41 added that column (``jobfinder/storage/
database.py``'s ``_MATCH_HISTORY_MIGRATIONS``) and ``save_match()``/
``get_recent_matches()`` now persist and select it, so ``HistoryCardDTO``
carries the field too. A pre-migration row has no stored value and reads
back falsy, matching ``MatchResult.agent_verified``'s own ``False``
default rather than inventing a value. ``MatchCardDTO`` and
``HistoryCardDTO`` remain two distinct models (their other fields still
differ), but their field *sets* are now identical.

``matched_at`` (T57) is an ISO-8601 string, ``str.isoformat()`` of a naive
(no tzinfo) UTC ``datetime`` -- ``MatchResult.matched_at`` defaults via
``datetime.utcnow`` (``jobfinder/matcher/models.py``) and
``jobfinder/storage/database.py`` stores/reads it the same way. "Naive UTC"
is the timezone contract this module exposes as-is: callers must treat an
offset-less ``matched_at`` as UTC, not local time. Batching ("which jobs
were found together") is derived from the sibling ``scan_id`` database
column, not from clustering this timestamp -- see
``jobfinder/storage/database.py``'s module docstring for why.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from .matcher.models import MatchResult


class MatchCardDTO(BaseModel):
    """Card shape for a live scan result (``MatchResult``)."""

    model_config = ConfigDict(extra="forbid")

    job_id: Any
    title: Any
    company: Any
    location: Any
    country: Any
    region: Any
    city: Any
    score: Any
    source: Any
    url: Any
    url_direct: Any
    skills: Any
    matched_skills: Any
    reasoning: Any
    signals: Any
    geo_warning: Any
    work_arrangement: Any
    employment_type: Any
    agent_verified: Any
    job_level: Any
    job_function: Any
    experience_range: Any
    listing_type: Any
    company_url: Any
    company_logo: Any
    company_industry: Any
    company_num_employees: Any
    compensation_min: Any
    compensation_max: Any
    compensation_currency: Any
    compensation_interval: Any
    date_posted: Any
    matched_at: Any
    # T58: see HistoryCardDTO.scan_id below -- kept symmetric across both
    # card shapes rather than only adding it to one of them, since a live
    # scan's ``MatchResult`` already carries the same ``scan_id`` (T57).
    scan_id: Any

    @classmethod
    def from_match_result(cls, r: "MatchResult") -> "MatchCardDTO":
        j = r.job
        return cls(
            job_id=j.job_id,
            title=j.title,
            company=j.company,
            location=j.location,
            country=j.country,
            region=j.region,
            city=j.city,
            score=r.score,
            source=j.source,
            url=j.url,
            url_direct=j.job_url_direct,
            skills=(j.linkedin_skills or r.skills_matched)[:8],
            matched_skills=r.skills_matched[:12],
            reasoning=r.reasoning,
            signals=r.signals,
            geo_warning=r.geo_warning,
            work_arrangement=j.work_arrangement,
            employment_type=j.employment_type,
            agent_verified=r.agent_verified,
            job_level=j.job_level,
            job_function=j.job_function,
            experience_range=j.experience_range,
            listing_type=j.listing_type,
            company_url=j.company_url,
            company_logo=j.company_logo,
            company_industry=j.company_industry,
            company_num_employees=j.company_num_employees,
            compensation_min=j.compensation_min,
            compensation_max=j.compensation_max,
            compensation_currency=j.compensation_currency,
            compensation_interval=j.compensation_interval,
            date_posted=j.date_posted.isoformat() if j.date_posted else "",
            matched_at=r.matched_at.isoformat() if r.matched_at else "",
            scan_id=r.scan_id,
        )


class HistoryCardDTO(BaseModel):
    """Card shape for a ``get_recent_matches()`` database row.

    ``agent_verified`` (T41) -- see module docstring: present on both card
    shapes now that ``match_history`` has the column.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: Any
    title: Any
    company: Any
    location: Any
    country: Any
    region: Any
    city: Any
    score: Any
    url: Any
    url_direct: Any
    skills: Any
    matched_skills: Any
    reasoning: Any
    signals: Any
    geo_warning: Any
    source: Any
    work_arrangement: Any
    employment_type: Any
    agent_verified: Any
    job_level: Any
    job_function: Any
    experience_range: Any
    listing_type: Any
    company_url: Any
    company_logo: Any
    company_industry: Any
    company_num_employees: Any
    compensation_min: Any
    compensation_max: Any
    compensation_currency: Any
    compensation_interval: Any
    date_posted: Any
    matched_at: Any
    # T58: which scan found this job (``match_history.scan_id``, T57). Lets
    # the client group the Matches view into batches ("Today 09:12 · 14
    # found · ...", DESIGN.md Variation B) without reimplementing
    # ``group_matches_by_scan`` in TypeScript. ``None`` for a pre-migration
    # row -- see that function's module docstring for why that must stay a
    # singleton batch rather than merging with anything.
    scan_id: Any

    @staticmethod
    def _json_list(r: dict, key: str) -> list:
        raw = r.get(key) or "[]"
        try:
            return json.loads(raw) if isinstance(raw, str) else list(raw)
        except Exception:
            return []

    @classmethod
    def from_history_row(cls, r: dict) -> "HistoryCardDTO":
        matched = cls._json_list(r, "skills_matched")
        linkedin = cls._json_list(r, "linkedin_skills")
        display = linkedin[:8] if linkedin else matched[:8]
        return cls(
            job_id=r.get("job_id", ""),
            title=r.get("title", ""),
            company=r.get("company", ""),
            location=r.get("location", "") or "",
            country=r.get("country", "") or "",
            region=r.get("region", "") or "",
            city=r.get("city", "") or "",
            score=r.get("score", 0),
            url=r.get("url", ""),
            url_direct=r.get("job_url_direct", "") or "",
            skills=display,
            matched_skills=matched[:12],
            reasoning=r.get("reasoning", ""),
            signals={},
            geo_warning=None,
            source=r.get("source", "") or "",
            work_arrangement=r.get("work_arrangement", "") or "Unknown",
            employment_type=r.get("employment_type", "") or "Unknown",
            agent_verified=bool(r.get("agent_verified")),
            job_level=r.get("job_level", "") or "",
            job_function=r.get("job_function", "") or "",
            experience_range=r.get("experience_range", "") or "",
            listing_type=r.get("listing_type", "") or "",
            company_url=r.get("company_url", "") or "",
            company_logo=r.get("company_logo", "") or "",
            company_industry=r.get("company_industry", "") or "",
            company_num_employees=r.get("company_num_employees", "") or "",
            compensation_min=r.get("compensation_min"),
            compensation_max=r.get("compensation_max"),
            compensation_currency=r.get("compensation_currency", "") or "",
            compensation_interval=r.get("compensation_interval", "") or "",
            date_posted="",
            matched_at=r.get("matched_at", "") or "",
            scan_id=r.get("scan_id"),
        )


def result_to_card(r: "MatchResult") -> dict:
    """Map a live scan ``MatchResult`` to the card dict shape."""
    return MatchCardDTO.from_match_result(r).model_dump()


def dict_to_card(r: dict) -> dict:
    """Map a ``get_recent_matches()`` history row to the card dict shape."""
    return HistoryCardDTO.from_history_row(r).model_dump()


# Field lists pinned by tests/test_dto.py against a committed snapshot. Keep
# these in sync with the models above — a rename/drop here is the exact
# behaviour change the snapshot test exists to catch.
MATCH_CARD_FIELDS = list(MatchCardDTO.model_fields.keys())
HISTORY_CARD_FIELDS = list(HistoryCardDTO.model_fields.keys())
