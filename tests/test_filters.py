"""Behaviour-pinning tests for jobfinder/filters.py (T7).

Extracted from jobfinder/ui/widget.py's ``_card_hidden_by_tags`` and
``_parse_company_size``. Covers tag_visibility, role_filter, quality_filter
separately, every salary interval, and the no-salary-data rule.
"""

from __future__ import annotations

import pytest

from jobfinder.config import (
    Config,
    QualityFilterConfig,
    RoleFilterConfig,
    TagVisibilityConfig,
)
from jobfinder.filters import _card_hidden_by_tags, _parse_company_size


def _cfg(**overrides) -> Config:
    cfg = Config()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _card(**fields) -> dict:
    return dict(fields)


# ── cfg is None ────────────────────────────────────────────────────────────


def test_no_config_never_hides():
    assert _card_hidden_by_tags(_card(employment_type="Internship"), None) is False


# ── tag_visibility: employment type + work arrangement ──────────────────────


@pytest.mark.parametrize(
    "field_name, value, flag, expected",
    [
        ("employment_type", "Full-time", "hide_full_time", True),
        ("employment_type", "Part-time", "hide_part_time", True),
        ("employment_type", "Contract", "hide_contract", True),
        ("employment_type", "Temporary", "hide_temporary", True),
        ("employment_type", "Internship", "hide_internship", True),
        ("work_arrangement", "Remote", "hide_remote", True),
        ("work_arrangement", "Hybrid", "hide_hybrid", True),
        ("work_arrangement", "On-site", "hide_onsite", True),
    ],
)
def test_tag_visibility_hides_when_flag_set(field_name, value, flag, expected):
    tv = TagVisibilityConfig(**{flag: True})
    cfg = _cfg(tag_visibility=tv)
    card = _card(**{field_name: value})
    assert _card_hidden_by_tags(card, cfg) is expected


@pytest.mark.parametrize(
    "field_name, value, flag",
    [
        ("employment_type", "Full-time", "hide_full_time"),
        ("work_arrangement", "Remote", "hide_remote"),
    ],
)
def test_tag_visibility_shows_when_flag_unset(field_name, value, flag):
    tv = TagVisibilityConfig(**{flag: False})
    cfg = _cfg(tag_visibility=tv)
    card = _card(**{field_name: value})
    assert _card_hidden_by_tags(card, cfg) is False


def test_tag_visibility_is_case_insensitive():
    tv = TagVisibilityConfig(hide_remote=True)
    cfg = _cfg(tag_visibility=tv)
    card = _card(work_arrangement="REMOTE")
    assert _card_hidden_by_tags(card, cfg) is True


# ── role_filter ───────────────────────────────────────────────────────────


def test_role_filter_empty_lists_accept_all():
    cfg = _cfg(role_filter=RoleFilterConfig())
    card = _card(job_function="Engineering", job_level="Mid-Senior level")
    assert _card_hidden_by_tags(card, cfg) is False


def test_role_filter_job_function_mismatch_hides():
    cfg = _cfg(role_filter=RoleFilterConfig(job_functions=["Sales"]))
    card = _card(job_function="Engineering")
    assert _card_hidden_by_tags(card, cfg) is True


def test_role_filter_job_function_substring_match_shows():
    cfg = _cfg(role_filter=RoleFilterConfig(job_functions=["Information Technology"]))
    card = _card(job_function="Information Technology Services")
    assert _card_hidden_by_tags(card, cfg) is False


def test_role_filter_blank_card_field_not_hidden():
    # An empty card field never triggers the mismatch branch (falsy guard).
    cfg = _cfg(role_filter=RoleFilterConfig(job_functions=["Engineering"]))
    card = _card(job_function="")
    assert _card_hidden_by_tags(card, cfg) is False


def test_role_filter_seniority_mismatch_hides():
    cfg = _cfg(role_filter=RoleFilterConfig(seniority_levels=["Director"]))
    card = _card(job_level="Entry level")
    assert _card_hidden_by_tags(card, cfg) is True


def test_role_filter_seniority_match_shows():
    cfg = _cfg(role_filter=RoleFilterConfig(seniority_levels=["Mid-Senior level"]))
    card = _card(job_level="Mid-Senior level")
    assert _card_hidden_by_tags(card, cfg) is False


# ── quality_filter: staffing agencies / promoted ────────────────────────────


def test_quality_filter_hides_staffing_agency():
    cfg = _cfg(quality_filter=QualityFilterConfig(hide_staffing_agencies=True))
    card = _card(company_industry="Staffing and Recruiting")
    assert _card_hidden_by_tags(card, cfg) is True


def test_quality_filter_shows_non_staffing_industry():
    cfg = _cfg(quality_filter=QualityFilterConfig(hide_staffing_agencies=True))
    card = _card(company_industry="Software Development")
    assert _card_hidden_by_tags(card, cfg) is False


def test_quality_filter_hides_promoted():
    cfg = _cfg(quality_filter=QualityFilterConfig(hide_promoted=True))
    card = _card(listing_type="promoted")
    assert _card_hidden_by_tags(card, cfg) is True


def test_quality_filter_shows_organic_when_hide_promoted():
    cfg = _cfg(quality_filter=QualityFilterConfig(hide_promoted=True))
    card = _card(listing_type="organic")
    assert _card_hidden_by_tags(card, cfg) is False


# ── quality_filter: min_salary, every interval + no-salary-data rule ───────


@pytest.mark.parametrize(
    "interval, value, min_salary, expected_hidden",
    [
        # yearly (default when interval missing/omitted)
        ("yearly", 50_000, 60_000, True),
        ("yearly", 70_000, 60_000, False),
        # hourly x2080
        ("hourly", 20, 60_000, True),  # 20*2080=41600 < 60000
        ("hourly", 30, 60_000, False),  # 30*2080=62400 >= 60000
        # monthly x12
        ("monthly", 4_000, 60_000, True),  # 48000 < 60000
        ("monthly", 6_000, 60_000, False),  # 72000 >= 60000
        # weekly x52
        ("weekly", 1_000, 60_000, True),  # 52000 < 60000
        ("weekly", 1_500, 60_000, False),  # 78000 >= 60000
    ],
)
def test_quality_filter_min_salary_every_interval(interval, value, min_salary, expected_hidden):
    cfg = _cfg(quality_filter=QualityFilterConfig(min_salary=min_salary))
    card = _card(compensation_min=value, compensation_interval=interval)
    assert _card_hidden_by_tags(card, cfg) is expected_hidden


def test_quality_filter_min_salary_uses_larger_of_min_max():
    cfg = _cfg(quality_filter=QualityFilterConfig(min_salary=100_000))
    card = _card(compensation_min=20_000, compensation_max=140_000, compensation_interval="yearly")
    # top=140_000 >= 100_000 so not hidden, even though min alone would hide it
    assert _card_hidden_by_tags(card, cfg) is False


def test_quality_filter_defaults_to_yearly_when_interval_missing():
    cfg = _cfg(quality_filter=QualityFilterConfig(min_salary=60_000))
    card = _card(compensation_min=50_000)
    assert _card_hidden_by_tags(card, cfg) is True


def test_quality_filter_no_salary_data_is_never_hidden():
    cfg = _cfg(quality_filter=QualityFilterConfig(min_salary=60_000))
    card = _card()
    assert _card_hidden_by_tags(card, cfg) is False


def test_quality_filter_min_salary_disabled_when_zero():
    cfg = _cfg(quality_filter=QualityFilterConfig(min_salary=0))
    card = _card(compensation_min=1, compensation_interval="yearly")
    assert _card_hidden_by_tags(card, cfg) is False


# ── quality_filter: min_company_size ────────────────────────────────────────


def test_quality_filter_min_company_size_hides_smaller():
    cfg = _cfg(quality_filter=QualityFilterConfig(min_company_size=1000))
    card = _card(company_num_employees="51-200")
    assert _card_hidden_by_tags(card, cfg) is True


def test_quality_filter_min_company_size_shows_larger():
    cfg = _cfg(quality_filter=QualityFilterConfig(min_company_size=1000))
    card = _card(company_num_employees="1,001-5,000")
    assert _card_hidden_by_tags(card, cfg) is False


def test_quality_filter_min_company_size_unparseable_not_hidden():
    cfg = _cfg(quality_filter=QualityFilterConfig(min_company_size=1000))
    card = _card(company_num_employees="")
    assert _card_hidden_by_tags(card, cfg) is False


# ── quality_filter: max_post_age_days ────────────────────────────────────────


def test_quality_filter_max_post_age_hides_older():
    cfg = _cfg(quality_filter=QualityFilterConfig(max_post_age_days=7))
    card = _card(post_age_days=10)
    assert _card_hidden_by_tags(card, cfg) is True


def test_quality_filter_max_post_age_shows_newer():
    cfg = _cfg(quality_filter=QualityFilterConfig(max_post_age_days=7))
    card = _card(post_age_days=3)
    assert _card_hidden_by_tags(card, cfg) is False


def test_quality_filter_max_post_age_disabled_when_zero():
    cfg = _cfg(quality_filter=QualityFilterConfig(max_post_age_days=0))
    card = _card(post_age_days=9999)
    assert _card_hidden_by_tags(card, cfg) is False


def test_quality_filter_max_post_age_missing_data_not_hidden():
    cfg = _cfg(quality_filter=QualityFilterConfig(max_post_age_days=7))
    card = _card()
    assert _card_hidden_by_tags(card, cfg) is False


# ── _parse_company_size ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", 0),
        (None, 0),
        ("1,001-5,000", 5000),
        ("51-200", 200),
        ("10,001+", 10001),
        ("no digits here", 0),
    ],
)
def test_parse_company_size(value, expected):
    assert _parse_company_size(value) == expected
