"""Behaviour-pinning tests for company normalization / skip-list matching.

jobfinder/storage/database.py:24 (normalize_company), :38 (company_matches_skip)
"""

from __future__ import annotations

import pytest

from jobfinder.storage.database import company_matches_skip, normalize_company


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Acme Inc.", "acme"),
        ("Acme, LLC", "acme"),
        ("Acme Ltd", "acme"),
        ("Acme Corp", "acme"),
        ("Acme GmbH", "acme"),
        ("ACME", "acme"),
        ("  Acme   Canada  ", "acme canada"),
        ("Acme-Canada", "acme canada"),
        ("", ""),
        (None, ""),
        ("Inc.", ""),  # normalizes away to nothing once the suffix is stripped
        ("Acme Group Holdings", "acme"),  # multiple trailing suffixes stripped
    ],
)
def test_normalize_company(raw, expected):
    assert normalize_company(raw) == expected


@pytest.mark.parametrize(
    "company, skip_keys, expected",
    [
        ("Acme Inc.", {"acme"}, True),  # exact match after normalization
        ("Acme Canada", {"acme"}, True),  # whole-word prefix of key
        ("Acme", {"north acme"}, True),  # whole-word suffix of a skip entry
        ("Acme", {"acme canada"}, True),  # whole-word prefix of a skip entry
        ("Acmeco", {"acme"}, False),  # NOT a whole-word match — must not hide
        ("Globex", {"acme"}, False),  # unrelated company, no match
        ("", {"acme"}, False),  # empty company name never matches
        ("Inc.", {"acme"}, False),  # normalizes to "" — never matches
        ("IBM", {"bm"}, False),  # both short (<4 chars): fuzzy match disabled
        ("BM", {"ibm"}, False),  # key shorter than 4 chars: fuzzy match disabled
        ("IBM", {"ibm"}, True),  # short names still match when exactly equal
    ],
)
def test_company_matches_skip(company, skip_keys, expected):
    assert company_matches_skip(company, skip_keys) is expected
