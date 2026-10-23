"""Behaviour-pinning tests for jobfinder/matcher/location_parser.py.

Covers parse_location (:168), parse_employment_type (:256), and
parse_work_arrangement (:344) against the current implementation.
"""

from __future__ import annotations

import pytest

from jobfinder.matcher.location_parser import (
    parse_employment_type,
    parse_location,
    parse_work_arrangement,
)

# ── parse_location ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Toronto, Ontario, Canada", ("Canada", "Ontario", "Toronto")),
        ("London, UK", ("United Kingdom", "", "London")),
        ("Bangalore", ("India", "", "Bangalore")),  # backfilled from _CITY_COUNTRY
        ("Remote — Canada", ("Canada", "", "")),
        ("", ("", "", "")),
        (None, ("", "", "")),
        ("Remote - United States", ("United States", "", "")),
        ("Dublin", ("Ireland", "", "Dublin")),
        ("Nowheresville", ("", "", "Nowheresville")),
        ("Hybrid, Toronto, Ontario", ("Canada", "Ontario", "Toronto")),
        # State-before-country ordering: "CA"/"IN" must resolve to the US
        # state, not the country-code collision (Canada / India).
        ("Mountain View, CA", ("United States", "California", "Mountain View")),
        ("Indianapolis, IN", ("United States", "Indiana", "Indianapolis")),
        ("Waterloo, ON", ("Canada", "Ontario", "Waterloo")),
        # Unknown middle token becomes region when a country is known and a
        # city token still sits in front of it.
        ("Toronto, Greater Toronto Area, Canada", ("Canada", "Greater Toronto Area", "Toronto")),
        # Same shape but with no leading city token left — becomes the city.
        ("Springfield, Canada", ("Canada", "", "Springfield")),
    ],
)
def test_parse_location(raw, expected):
    assert parse_location(raw) == expected


# ── parse_employment_type ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "job_type, title, description, expected",
    [
        # Title is authoritative, even over a (wrong) structured field.
        ("Full-time", "QA Intern", "", "Internship"),
        ("", "Intern - QA Team", "", "Internship"),
        ("", "Software Engineering Internship", "", "Internship"),
        # Look-ahead must not false-positive on longer words.
        ("", "Internal Tools Engineer", "", "Unknown"),
        ("", "International Sales Manager", "", "Unknown"),
        ("", "Template Designer", "", "Unknown"),
        ("", "Temperature Sensor Engineer", "", "Unknown"),
        ("", "Temp QA Tester", "", "Temporary"),
        ("", "Contract QA Engineer", "", "Contract"),
        ("", "Part-time QA Analyst", "", "Part-time"),
        # Structured job_type used only when the title is silent.
        ("Contract", "QA Engineer", "", "Contract"),
        # Description fallback, and its 1500-char cutoff.
        ("", "QA Engineer", "This is a temporary position.", "Temporary"),
        ("", "QA Engineer", "x" * 1600 + " temporary position", "Unknown"),
        ("", "QA Engineer", "A great place to work.", "Unknown"),
    ],
)
def test_parse_employment_type(job_type, title, description, expected):
    assert parse_employment_type(job_type, title, description) == expected


# ── parse_work_arrangement ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "is_remote, location, title, description, expected",
    [
        # 1. LinkedIn parenthetical marker, highest precedence.
        (False, "Toronto, ON (Remote)", "QA Engineer", "", "Remote"),
        (False, "Toronto, ON (Hybrid)", "QA Engineer", "", "Hybrid"),
        (False, "Toronto, ON (On-site)", "QA Engineer", "", "On-site"),
        # 2. Title-level label beats is_remote=True.
        (True, "Toronto, ON", "Hybrid QA Engineer", "", "Hybrid"),
        (None, "Toronto, ON", "Remote QA Engineer", "", "Remote"),
        (None, "Toronto, ON", "On-site QA Engineer", "", "On-site"),
        # 3. is_remote=True is strong; False is weak (not treated as on-site).
        (True, "Toronto, ON", "QA Engineer", "", "Remote"),
        (False, "Toronto, ON", "QA Engineer", "", "Unknown"),
        # 4. Strong body phrases, hybrid checked before remote/on-site.
        (None, "Toronto, ON", "QA Engineer", "This role follows a hybrid schedule.", "Hybrid"),
        (None, "Toronto, ON", "QA Engineer", "This is a fully remote position.", "Remote"),
        (None, "Toronto, ON", "QA Engineer", "This role is on-site at our office.", "On-site"),
        (None, "Toronto, ON", "QA Engineer", "Experience with hybrid cloud a plus.", "Unknown"),
        # Negation guard: a negated keyword is excluded, but an unrelated
        # unnegated keyword elsewhere in the same text still fires.
        (None, "Toronto, ON", "QA Engineer", "This role is not fully remote.", "Unknown"),
        (None, "Toronto, ON", "QA Engineer", "This role is not hybrid. It is fully remote.", "Remote"),
        # FIXED BUG (T39). location_parser.py:524 used to OR two
        # _has_unnegated calls for the two spellings "on-site" and "onsite".
        # _has_unnegated decided negation by testing whether its `keyword`
        # appeared literally inside the matched negation phrase, so for ANY
        # spelling in the text one of the two calls was given the other
        # spelling, failed to find it, and returned True ("unnegated"). The
        # OR then passed that through, defeating the guard unconditionally
        # regardless of spelling. Fixed by passing both spellings to a single
        # _has_unnegated call. Signed off 2026-10-22: the
        # agreed result is `Unknown` — consistent with the negation behaviour
        # the parser already has for `remote` and `hybrid`, and because the
        # text rules one arrangement out without stating another.
        (None, "Toronto, ON", "QA Engineer", "This role is not on-site.", "Unknown"),
        (None, "Toronto, ON", "QA Engineer", "This role is not onsite.", "Unknown"),
        # 5. Last-ditch lone "Remote" in the location string, else Unknown.
        (None, "Remote", "QA Engineer", "", "Remote"),
        (None, "", "QA Engineer", "", "Unknown"),
    ],
)
def test_parse_work_arrangement(is_remote, location, title, description, expected):
    assert parse_work_arrangement(is_remote, location, title, description) == expected
