"""Qt-free filter pipeline for the Matches tab.

Extracted from ``jobfinder/ui/widget.py`` (T7) — this module must stay the
*only* implementation of ``tag_visibility`` / ``role_filter`` /
``quality_filter`` enforcement. ``widget.py`` imported from here rather than
keeping its own copy, and was deleted along with the rest of the desktop UI
in T33; behaviour here must still match the original ``widget.py``
implementation, which ``tests/test_filters.py`` pins. This
module must not import PySide6 or anything from ``jobfinder/ui/``.
"""

from __future__ import annotations

import re

from .matcher.location_parser import (
    country_is_unresolved,
    matches_region_list,
    recognize_country,
)

_STAFFING_TOKENS = ("staffing", "recruiting", "recruitment", "search firm", "headhunt")


def _location_fields(subject) -> tuple[str, str, str]:
    """Read city/region/country off either a dict (history row) or an object
    with those attributes (e.g. ``JobPosting``). Keeps ``_classify_tier``
    parameterized over both input shapes (T8)."""
    if isinstance(subject, dict):
        return (
            subject.get("city", "") or "",
            subject.get("region", "") or "",
            subject.get("country", "") or "",
        )
    return (
        getattr(subject, "city", "") or "",
        getattr(subject, "region", "") or "",
        getattr(subject, "country", "") or "",
    )


def effective_search_country(search_country: str, preferred_regions: list[str]) -> str:
    """Return the country to use for "same country" classification.

    Prefers the explicit ``search_country``. T84 (found 2026-10-22): an
    empty ``search_country`` used to silently disable the "same country"
    tier entirely, even when the user's country was obvious from
    ``preferred_regions`` — e.g. "Canada" sitting in that list. So when
    ``search_country`` is empty, derive it from exactly one recognisable
    country name among ``preferred_regions``. Two or more recognisable
    countries is genuinely ambiguous, so this returns ``""`` rather than
    guess which one the user meant.
    """
    if search_country and search_country.strip():
        return search_country.strip()
    recognized = {recognize_country(entry) for entry in (preferred_regions or [])}
    recognized.discard("")
    if len(recognized) == 1:
        return next(iter(recognized))
    return ""


def country_tier_is_configured(search_country: str, preferred_regions: list[str]) -> bool:
    """Whether the "same country" tier is reachable at all for this config.

    T84: when it is not configured, the "same country" tier is genuinely
    unreachable — the user has not said (directly or via ``preferred_regions``)
    where they are — and that is correct. What must not happen is that this
    looks identical to an empty result. T81 should read this alongside the
    tier counts/filter and, when ``False``, tell the user the "same country"
    filter is unconfigured rather than rendering it as a filter that simply
    matched zero jobs.
    """
    return bool(effective_search_country(search_country, preferred_regions))


def tier_location_is_unresolved(subject) -> bool:
    """Whether ``subject``'s location had content but no country could be
    identified from it (T84) — e.g. "Greater Winnipeg Metropolitan Area".

    This is a companion signal to ``_classify_tier``'s "global" result, not
    a fourth tier: tier classification still only ever returns
    ``preferred``/``country``/``global``. A caller (T81) can use this to
    distinguish "we don't know where this is" from a genuine
    worldwide-remote match, which otherwise look identical once both land in
    "global". The card already carries ``geo_warning``; a caller may choose
    to fold this into that field rather than add a new one.
    """
    city, region, country = _location_fields(subject)
    return country_is_unresolved(city, region, country)


def _classify_tier(subject, preferred_regions: list[str], search_country: str) -> str:
    """Return one of ``preferred``/``country``/``global`` for the job.

    ``subject`` may be a dict (history row, as used by the former v3 widget)
    or an object exposing ``city``/``region``/``country`` attributes (e.g.
    ``JobPosting``, as used by the orchestrator).
    """
    city, region, country = _location_fields(subject)
    country = country.strip().lower()
    if matches_region_list(city, region, preferred_regions, country=country):
        return "preferred"
    eff_country = effective_search_country(search_country, preferred_regions)
    if eff_country and country == eff_country.strip().lower():
        return "country"
    return "global"


def _parse_company_size(value: str) -> int:
    """Turn LinkedIn's "1,001-5,000" style into a minimum headcount integer."""
    if not value:
        return 0
    nums = re.findall(r"[\d,]+", value)
    if not nums:
        return 0
    cleaned = [int(n.replace(",", "")) for n in nums]
    return max(cleaned) if cleaned else 0


def _card_hidden_by_tags(card: dict, cfg) -> bool:
    if cfg is None:
        return False

    # Employment-type + work-arrangement hide toggles (existing behaviour)
    tv = cfg.tag_visibility
    et = (card.get("employment_type") or "").lower()
    if et == "full-time" and tv.hide_full_time:
        return True
    if et == "part-time" and tv.hide_part_time:
        return True
    if et == "contract" and tv.hide_contract:
        return True
    if et == "temporary" and tv.hide_temporary:
        return True
    if et == "internship" and tv.hide_internship:
        return True
    arr = (card.get("work_arrangement") or "").lower()
    if arr == "remote" and tv.hide_remote:
        return True
    if arr == "hybrid" and tv.hide_hybrid:
        return True
    if arr == "on-site" and tv.hide_onsite:
        return True

    # ── Role filter ───────────────────────────────────────────────────────
    rf = getattr(cfg, "role_filter", None)
    if rf is not None:
        if rf.job_functions:
            card_fn = (card.get("job_function") or "").lower()
            wanted = [f.lower() for f in rf.job_functions]
            if card_fn and not any(w in card_fn or card_fn in w for w in wanted):
                return True
        if rf.seniority_levels:
            card_lvl = (card.get("job_level") or "").lower()
            wanted = [s.lower() for s in rf.seniority_levels]
            if card_lvl and not any(w in card_lvl or card_lvl in w for w in wanted):
                return True

    # ── Quality filter ────────────────────────────────────────────────────
    qf = getattr(cfg, "quality_filter", None)
    if qf is not None:
        if qf.hide_staffing_agencies:
            industry = (card.get("company_industry") or "").lower()
            if any(tok in industry for tok in _STAFFING_TOKENS):
                return True
        if qf.hide_promoted:
            if (card.get("listing_type") or "").lower() == "promoted":
                return True
        if qf.min_salary > 0:
            cmin = card.get("compensation_min")
            cmax = card.get("compensation_max")
            # Use the larger of min/max so a job listing a $20k–$140k range
            # isn't wrongly filtered by its low end.
            top = max(v for v in (cmin, cmax) if v is not None) if any(v is not None for v in (cmin, cmax)) else None
            interval = (card.get("compensation_interval") or "yearly").lower()
            if top is not None:
                # Normalise to an annual figure
                annual = top
                if "hour" in interval:
                    annual = top * 2080
                elif "month" in interval:
                    annual = top * 12
                elif "week" in interval:
                    annual = top * 52
                if annual < qf.min_salary:
                    return True
            # If the posting has no salary data, don't hide it — otherwise
            # we'd nuke most listings (LinkedIn rarely has comp)
        if qf.min_company_size > 0:
            size = _parse_company_size(card.get("company_num_employees") or "")
            if size and size < qf.min_company_size:
                return True
        if qf.max_post_age_days > 0:
            ago_days = card.get("post_age_days")
            if ago_days is not None and ago_days > qf.max_post_age_days:
                return True

    return False
