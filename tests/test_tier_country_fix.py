"""T84 regression tests: two tier-classification defects found 2026-10-22
against the owner's real config, now that tier is a filter (the product spec's
closing paragraph) rather than a sub-tab.

(a) ``jobfinder/filters.py``'s ``_classify_tier`` guarded on
    ``if search_country and country == ...``, so an empty ``search_country``
    silently disabled the whole "same country" tier even when the user's
    country was obvious from ``preferred_regions`` (e.g. "Canada" in the
    list).
(b) ``jobfinder/matcher/location_parser.py``'s ``matches_region_list`` never
    compared country at all, so a bare country name like "Canada" in
    ``preferred_regions`` matched nothing.

Both defects used to produce a merely-empty sub-tab; as a filter (3.8) a
wrong classification silently *hides* jobs instead, which is worse.
"""

from __future__ import annotations

from jobfinder.filters import (
    _classify_tier,
    country_tier_is_configured,
    effective_search_country,
    tier_location_is_unresolved,
)
from jobfinder.matcher.models import JobPosting
from jobfinder.matcher.location_parser import matches_region_list


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


# ── The owner's real shape ──────────────────────────────────────────────────
#
# search_country='' with preferred_regions=['Toronto', 'Waterloo', 'GTA',
# 'Canada']. Markham and Ottawa are Canadian cities outside the explicit
# Toronto/Waterloo/GTA entries, but "Canada" sits in the same preferred list.
# With "Canada" itself as a preferred-region entry, `preferred` is the
# defensible answer — the user told us a whole country is acceptable, so a
# Canadian job inside that country IS a preferred-region match, the same way
# "GTA" matching a city substring is a preferred-region match rather than a
# "same country" match. That is also why this landed in `matches_region_list`
# (b) and not only in the search_country derivation (a): the fix has to make
# "Canada" matchable as a region entry at all.

OWNER_PREFERRED = ["Toronto", "Waterloo", "GTA", "Canada"]
OWNER_SEARCH_COUNTRY = ""


def test_owner_config_markham_is_not_global():
    card = _card(city="Markham", region="Ontario", country="Canada")
    tier = _classify_tier(card, OWNER_PREFERRED, OWNER_SEARCH_COUNTRY)
    assert tier != "global"
    assert tier == "preferred"  # "Canada" is itself a preferred-region entry


def test_owner_config_ottawa_is_not_global():
    job = _posting(city="Ottawa", region="Ontario", country="Canada")
    tier = _classify_tier(job, OWNER_PREFERRED, OWNER_SEARCH_COUNTRY)
    assert tier != "global"
    assert tier == "preferred"


# ── Empty search_country with no country anywhere in preferred_regions ─────


def test_empty_search_country_no_country_in_preferred_stays_global():
    card = _card(city="Berlin", region="", country="Germany")
    tier = _classify_tier(card, ["San Francisco", "GTA"], "")
    assert tier == "global"
    assert country_tier_is_configured("", ["San Francisco", "GTA"]) is False


def test_country_tier_configured_when_explicit_search_country_set():
    assert country_tier_is_configured("United States", ["San Francisco"]) is True


def test_country_tier_configured_when_derived_from_preferred_regions():
    assert country_tier_is_configured("", OWNER_PREFERRED) is True


def test_country_tier_unconfigured_when_two_countries_in_preferred_ambiguous():
    # Two distinct countries in preferred_regions is genuinely ambiguous —
    # effective_search_country must not guess one.
    assert effective_search_country("", ["Canada", "United States"]) == ""
    assert country_tier_is_configured("", ["Canada", "United States"]) is False


# ── Country matching is exact, never by substring ───────────────────────────


def test_country_match_is_exact_not_substring_us_does_not_match_australia():
    # The dangerous case this fix must avoid: "us" is a substring of
    # "australia", so a naive substring fallback applied to country would
    # make a preferred list containing "US" silently match Australian jobs.
    assert matches_region_list("", "", ["US"], country="Australia") is False
    card = _card(country="Australia")
    assert _classify_tier(card, ["US"], "") == "global"


def test_country_match_via_alias_us_matches_united_states():
    assert matches_region_list("", "", ["US"], country="United States") is True


def test_country_match_exact_canada_matches_canada():
    assert matches_region_list("", "", ["Canada"], country="Canada") is True


# ── City and region substring matching still works (GTA is load-bearing) ───


def test_gta_substring_still_matches_city():
    assert matches_region_list("Richmond Hill, GTA", "", ["GTA"]) is True


def test_gta_substring_still_matches_region():
    assert matches_region_list("", "Greater Toronto Area GTA", ["GTA"]) is True


# ── Unparseable location reads as unknown, not as global ────────────────────


def test_unparseable_location_is_flagged_unresolved_not_silently_global():
    # "Greater Winnipeg Metropolitan Area" parses to no country today (city
    # carries the raw string, country stays empty) — that must be visible as
    # "we don't know", not indistinguishable from a genuine worldwide-remote
    # job that has no location fields at all.
    card = _card(city="Greater Winnipeg Metropolitan Area", region="", country="")
    tier = _classify_tier(card, OWNER_PREFERRED, OWNER_SEARCH_COUNTRY)
    assert tier == "global"  # tier count stays at three — no fourth tier
    assert tier_location_is_unresolved(card) is True


def test_genuine_worldwide_remote_with_no_location_is_not_flagged_unresolved():
    card = _card(city="", region="", country="")
    assert tier_location_is_unresolved(card) is False
