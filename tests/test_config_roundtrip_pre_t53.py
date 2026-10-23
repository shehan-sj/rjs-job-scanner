"""T53: a config.yaml written by the PRE-change build still loads with
every value preserved.

T53 reorganises *where settings are rendered*, not what they are called.
That distinction is the whole reason this file is cheap: moving a field
between tabs touches no config key, so there is no migration to write and
nothing to lose. But "there is nothing to lose" is a claim, and T46's
lesson was that the expensive failures here are silent ones — a value that
loads as its default instead of as what the user typed looks identical to
a value that was never set.

So this asserts the claim against a frozen artifact
(``tests/fixtures/config/pre_t53_config.yaml``) that nothing in the T53
change produced. If a later task *does* rename a config key, this file is
the test that must be made to pass by writing a migration — not by
updating the fixture.

See ``tests/test_settings_field_map.py`` for the other half of the DoD
(each relocated setting appears in exactly one tab).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jobfinder.config import load_config, save_config

FIXTURE = Path(__file__).parent / "fixtures" / "config" / "pre_t53_config.yaml"

# The seven settings T53 relocates, as config paths. Spelled out rather
# than derived so that this list reads as the task ledger DoD does.
RELOCATED = (
    "location_filter.preferred_regions",
    "location_filter.search_country",
    "location_filter.remote_ok_worldwide",
    "sources[].location",
    "sources[].search_terms",
    "role_filter.job_functions",
    "role_filter.seniority_levels",
)


@pytest.fixture
def pre_change_config_path(tmp_path) -> Path:
    """The fixture copied into a tmp dir, so a test may save over it."""
    dest = tmp_path / "config.yaml"
    dest.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_fixture_sets_every_relocated_setting_to_a_non_default() -> None:
    """Guard the fixture itself.

    A fixture that left any of the seven at its dataclass default would
    make the preservation test below vacuous for that key: a dropped key
    and a preserved default are indistinguishable. This catches a future
    edit that "simplifies" the fixture into uselessness.
    """
    from jobfinder.config import Config

    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    defaults = Config()

    assert raw["location_filter"]["preferred_regions"] != defaults.location_filter.preferred_regions
    assert raw["location_filter"]["search_country"] != defaults.location_filter.search_country
    assert raw["location_filter"]["remote_ok_worldwide"] != defaults.location_filter.remote_ok_worldwide
    assert raw["role_filter"]["job_functions"] != defaults.role_filter.job_functions
    assert raw["role_filter"]["seniority_levels"] != defaults.role_filter.seniority_levels
    # sources[] has no dataclass default to differ from (default is []),
    # so assert the two per-source fields T53 touches are non-empty.
    assert raw["sources"][0]["location"]
    assert raw["sources"][0]["search_terms"]

    assert len(RELOCATED) == 7


def test_pre_change_config_loads_with_every_relocated_value_preserved() -> None:
    """The DoD clause, read literally off the fixture's own YAML.

    Compares against the *raw* parsed YAML rather than against hardcoded
    expectations, so the assertion cannot drift from the fixture.
    """
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    cfg = load_config(FIXTURE)

    assert cfg.location_filter.preferred_regions == raw["location_filter"]["preferred_regions"]
    assert cfg.location_filter.search_country == raw["location_filter"]["search_country"]
    assert cfg.location_filter.remote_ok_worldwide == raw["location_filter"]["remote_ok_worldwide"]
    assert cfg.role_filter.job_functions == raw["role_filter"]["job_functions"]
    assert cfg.role_filter.seniority_levels == raw["role_filter"]["seniority_levels"]

    # sources[].location / sources[].search_terms, positionally — the
    # fixture's source order is part of what must survive, including the
    # duplicate trailing "linkedin" remote-sweep entry.
    assert [s.location for s in cfg.sources] == [s.get("location", "") for s in raw["sources"]]
    assert [s.search_terms for s in cfg.sources] == [s.get("search_terms", []) for s in raw["sources"]]


def test_pre_change_config_loads_with_every_other_value_preserved() -> None:
    """Not just the seven — nothing else in the file may shift either.

    T53 is a UI change, so the correct blast radius on config is zero.
    """
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    cfg = load_config(FIXTURE)

    assert cfg.check_interval_minutes == raw["check_interval_minutes"]
    assert cfg.match_threshold == raw["match_threshold"]
    assert cfg.filter_contracts == raw["filter_contracts"]
    assert cfg.resume.path == raw["resume"]["path"]
    assert cfg.resume.original_filename == raw["resume"]["original_filename"]
    assert cfg.anthropic.api_key == raw["anthropic"]["api_key"]
    assert cfg.anthropic.model == raw["anthropic"]["model"]
    assert cfg.notifications.auto_dismiss_seconds == raw["notifications"]["auto_dismiss_seconds"]

    for section, obj in (
        ("tag_visibility", cfg.tag_visibility),
        ("agentic_filter", cfg.agentic_filter),
        ("quality_filter", cfg.quality_filter),
        ("fillerbot", cfg.fillerbot),
        ("ui", cfg.ui),
    ):
        for key, value in raw[section].items():
            assert getattr(obj, key) == value, f"{section}.{key} changed on load"

    # Every source, field by field.
    assert len(cfg.sources) == len(raw["sources"])
    for loaded, expected in zip(cfg.sources, raw["sources"], strict=True):
        assert loaded.name == expected["name"]
        assert loaded.enabled == expected["enabled"]
        assert loaded.site_names == expected["site_names"]
        assert loaded.results_wanted == expected["results_wanted"]
        assert loaded.hours_old == expected["hours_old"]
        assert loaded.linkedin_fetch_description == expected["linkedin_fetch_description"]


def test_source_extras_survive_the_load(pre_change_config_path: Path) -> None:
    """Credentials and unmodelled per-source keys land in ``extra``.

    ``jsearch.api_key`` / ``adzuna.app_id`` are exactly the values a user
    would be angriest to lose, and they are not dataclass fields.
    """
    cfg = load_config(pre_change_config_path)

    def first(name: str):
        # Deliberately not a {s.name: s} dict: that keeps the *last*
        # match, and "linkedin" appears twice (the second is the remote
        # sweep, whose extras are empty) — which silently inverted this
        # assertion when first written.
        return next(s for s in cfg.sources if s.name == name)

    assert first("jsearch").extra["api_key"] == "rapidapi-fixture-key"
    assert first("adzuna").extra["app_id"] == "adzuna-fixture-id"
    assert first("adzuna").extra["app_key"] == "adzuna-fixture-key"
    assert first("adzuna").extra["country"] == "ca"
    assert first("linkedin").extra["some_source_specific_key"] == "keep me"


def test_save_after_load_is_lossless(pre_change_config_path: Path) -> None:
    """load -> save -> load leaves the Config identical.

    The round trip is what a user triggers by opening the reorganised
    Settings screen and pressing Save without typing anything.
    """
    first = load_config(pre_change_config_path)
    save_config(first, pre_change_config_path)
    second = load_config(pre_change_config_path)

    assert second == first

    # And the unmodelled top-level key is still on disk.
    rewritten = yaml.safe_load(pre_change_config_path.read_text(encoding="utf-8"))
    assert rewritten["some_future_key"] == 1
