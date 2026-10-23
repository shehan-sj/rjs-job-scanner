"""``country_indeed`` is never guessed (assigned to T52).

``jobspy_source._infer_country`` used to end ``return "usa"``. T48 removed
every *shipped* geographic default, but this one is a runtime fallback, so it
survived: with no location configured, Indeed and Glassdoor searches were
still told ``country_indeed="usa"``. The product spec says that was left in place
only until T52 derives a real country from the resume, after which "it should
become an error rather than a guess".

The absolute constraint is that **a scan with no determinable country must not
silently search the United States.** What this implements is the gentler of
the two options the product spec offers: the sites that need a country are *skipped*,
with a warning the user can see in the scan log, and the sites that ignore it
(LinkedIn) still run. A mid-scan exception would kill a scan the user is
waiting on, and refusing the configuration up front would block a LinkedIn-only
install — the shipped template's only enabled source — over a value LinkedIn
never reads.

jobspy is not installed in this environment, so ``scrape_jobs`` is stubbed.
These tests assert on the kwargs the source *would* send, which is where the
defect lived.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from jobfinder.config import SourceConfig
from jobfinder.sources.jobspy_source import JobSpySource


class _EmptyFrame:
    empty = True
    columns: tuple = ()


@pytest.fixture
def scrape_calls(monkeypatch):
    """Install a fake ``jobspy`` module and record every ``scrape_jobs`` call."""
    calls: list[dict] = []

    def scrape_jobs(
        site_name=None,
        search_term=None,
        location=None,
        results_wanted=None,
        country_indeed=None,
        hours_old=None,
        description_format=None,
        linkedin_fetch_description=None,
        **kw,
    ):
        calls.append(
            {
                "site_name": site_name,
                "search_term": search_term,
                "location": location,
                "country_indeed": country_indeed,
                **kw,
            }
        )
        return _EmptyFrame()

    module = types.ModuleType("jobspy")
    module.scrape_jobs = scrape_jobs
    monkeypatch.setitem(sys.modules, "jobspy", module)
    return calls


def _source(**kw) -> SourceConfig:
    base = dict(
        name="linkedin",
        enabled=True,
        site_names=["linkedin"],
        search_terms=["Staff Nurse"],
        location="",
        results_wanted=5,
        hours_old=24,
    )
    base.update(kw)
    return SourceConfig(**base)


# ── The guess is gone ────────────────────────────────────────────────────────


def test_an_undeterminable_country_is_none_not_the_united_states():
    """The one-line change the product spec asks for, asserted directly: there is
    no location text on earth that should resolve to "usa" by default."""
    assert JobSpySource()._infer_country("") is None
    assert JobSpySource()._infer_country("   ") is None
    assert JobSpySource()._infer_country("Reykjavik") is None


def test_a_recognised_country_still_resolves():
    infer = JobSpySource()._infer_country
    assert infer("Toronto, Canada") == "canada"
    assert infer("Austin, United States") == "usa"
    assert infer("Berlin, Germany") == "germany"
    assert infer("Mumbai, India") == "india"
    assert infer("Remote, UK") == "uk"


def test_a_two_letter_code_is_matched_as_a_word_not_a_substring():
    """Found while writing these tests, and worse than the fallback this task
    removes: a *confidently wrong* country.

    Every key was matched as a substring, so "Dublin, Ireland" contained "in"
    and searched **India**, and "Vienna, Austria" contained "us" and searched
    the **United States**. Only the dict's insertion order kept "Austin,
    United States" out of India. Neither country is in jobspy's map, so the
    honest answer for both is "not determinable".
    """
    infer = JobSpySource()._infer_country
    assert infer("Dublin, Ireland") is None
    assert infer("Vienna, Austria") is None
    assert infer("Houston, Texas") is None


# ── Indeed/Glassdoor are skipped rather than pointed at the US ───────────────


def test_indeed_is_skipped_when_no_country_can_be_determined(scrape_calls, caplog):
    with caplog.at_level(logging.WARNING):
        jobs = JobSpySource().fetch(_source(name="indeed", site_names=["indeed"], location=""))

    assert jobs == []
    assert scrape_calls == [], "no search may be issued with a guessed country"
    assert any("indeed" in r.getMessage().lower() for r in caplog.records)
    assert any("country" in r.getMessage().lower() for r in caplog.records)


def test_a_mixed_source_keeps_linkedin_and_drops_only_the_country_hungry_sites(scrape_calls):
    """Skipping the whole source would cost the user their LinkedIn results
    over a parameter LinkedIn ignores."""
    JobSpySource().fetch(_source(site_names=["linkedin", "indeed", "glassdoor"], location=""))

    assert len(scrape_calls) == 1
    assert scrape_calls[0]["site_name"] == ["linkedin"]
    assert scrape_calls[0]["country_indeed"] is None


def test_linkedin_alone_still_searches_with_no_country(scrape_calls):
    """The shipped template enables only LinkedIn, which ignores
    ``country_indeed`` — this configuration must keep working untouched."""
    jobs = JobSpySource().fetch(_source(location=""))

    assert jobs == []
    assert len(scrape_calls) == 1
    assert scrape_calls[0]["site_name"] == ["linkedin"]
    assert scrape_calls[0]["country_indeed"] is None


def test_indeed_runs_when_the_resume_derived_country_is_configured(scrape_calls):
    """The other half of the product spec: once T52 puts a real country in
    ``sources[].location`` there is a real value to pass."""
    JobSpySource().fetch(_source(name="indeed", site_names=["indeed"], location="Toronto, Canada"))

    assert len(scrape_calls) == 1
    assert scrape_calls[0]["country_indeed"] == "canada"


def test_an_explicit_country_indeed_extra_is_still_honoured(scrape_calls):
    """The escape hatch stays: a user who names the country outright is not
    guessing, so nothing is skipped."""
    JobSpySource().fetch(_source(name="indeed", site_names=["indeed"], location="", extra={"country_indeed": "uk"}))

    assert len(scrape_calls) == 1
    assert scrape_calls[0]["country_indeed"] == "uk"


def test_the_skip_is_decided_once_not_per_search_term(scrape_calls, caplog):
    """Four titles must not produce four identical warnings in the user's
    scan log."""
    with caplog.at_level(logging.WARNING):
        JobSpySource().fetch(
            _source(
                name="indeed",
                site_names=["indeed"],
                search_terms=["Staff Nurse", "Theatre Nurse", "Charge Nurse"],
                location="",
            )
        )

    skipped = [r for r in caplog.records if "country" in r.getMessage().lower()]
    assert len(skipped) == 1
