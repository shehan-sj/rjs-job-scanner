"""Behaviour-pinning tests for the unified jobfinder/filters.py::_classify_tier (T8).

Before T8 this logic existed twice: orchestrator.py's _classify_tier(job,
cfg) took a JobPosting, widget.py's _classify_tier(card, preferred, country)
took a dict (history row). Both implementations were compared and found
behaviourally identical (same precedence: preferred-region match first, then
exact country match, else global) — this suite pins that single, unified
implementation across both input shapes for all three tiers.
"""

from __future__ import annotations

import pytest

from jobfinder.filters import _classify_tier
from jobfinder.matcher.models import JobPosting


def _posting(city: str = "", region: str = "", country: str = "") -> JobPosting:
    return JobPosting(
        job_id="1",
        title="Engineer",
        company="Acme",
        location="",
        description="",
        url="https://example.com/1",
        source="indeed",
        city=city,
        region=region,
        country=country,
    )


def _card(city: str = "", region: str = "", country: str = "") -> dict:
    return {"city": city, "region": region, "country": country}


PREFERRED = ["California", "New York"]
SEARCH_COUNTRY = "United States"


@pytest.mark.parametrize(
    "city, region, country, expected",
    [
        ("San Francisco", "California", "United States", "preferred"),  # region matches preferred
        ("", "", "United States", "country"),  # no preferred region, same country
        ("", "", "Canada", "global"),  # different country entirely
        ("Toronto", "Ontario", "Canada", "global"),  # no preferred match, foreign country
    ],
)
def test_classify_tier_dict_shape(city, region, country, expected):
    card = _card(city=city, region=region, country=country)
    assert _classify_tier(card, PREFERRED, SEARCH_COUNTRY) == expected


@pytest.mark.parametrize(
    "city, region, country, expected",
    [
        ("San Francisco", "California", "United States", "preferred"),
        ("", "", "United States", "country"),
        ("", "", "Canada", "global"),
        ("Toronto", "Ontario", "Canada", "global"),
    ],
)
def test_classify_tier_jobposting_shape(city, region, country, expected):
    job = _posting(city=city, region=region, country=country)
    assert _classify_tier(job, PREFERRED, SEARCH_COUNTRY) == expected


def test_classify_tier_country_match_is_case_insensitive():
    card = _card(country="united states")
    assert _classify_tier(card, PREFERRED, SEARCH_COUNTRY) == "country"
    job = _posting(country="united states")
    assert _classify_tier(job, PREFERRED, SEARCH_COUNTRY) == "country"


def test_classify_tier_empty_search_country_never_matches_country_tier():
    card = _card(country="United States")
    assert _classify_tier(card, PREFERRED, "") == "global"
    job = _posting(country="United States")
    assert _classify_tier(job, PREFERRED, "") == "global"


def test_classify_tier_preferred_takes_precedence_over_country():
    # Same country AND a preferred-region match — preferred wins.
    card = _card(city="San Francisco", region="California", country="United States")
    assert _classify_tier(card, PREFERRED, SEARCH_COUNTRY) == "preferred"
