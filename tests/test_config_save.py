"""Tests for jobfinder.config.save_config (T9).

Covers the single most important property: a load_config -> save_config ->
load_config round-trip must preserve every top-level key the input file had
— including keys no dataclass here models — and must not silently reorder
the file.
"""

from __future__ import annotations

import yaml

from jobfinder.config import Config, SourceConfig, load_config, save_config, sync_config


def test_round_trip_default_config_is_equal(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    save_config(Config(), path)

    reloaded = load_config(path)

    assert reloaded == Config()


def test_round_trip_preserves_unknown_key_and_order(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """\
check_interval_minutes: 15
match_threshold: 55
filter_contracts: true
some_future_key: 1
resume:
  path: resume.pdf
anthropic:
  api_key: sk-test-key
  model: claude-opus-4-6
sources:
  - name: indeed
    enabled: true
    site_names: [indeed]
    search_terms: [qa engineer]
    location: Remote
    results_wanted: 20
    hours_old: 24
    linkedin_fetch_description: false
    some_source_specific_key: "keep me"
""",
        encoding="utf-8",
    )

    original_order = list(yaml.safe_load(path.read_text(encoding="utf-8")).keys())

    cfg = load_config(path)
    save_config(cfg, path)
    reloaded = load_config(path)

    # (1) Round-trip equality for the Config the dataclasses model.
    assert reloaded == cfg
    assert cfg.check_interval_minutes == 15
    assert cfg.match_threshold == 55
    assert cfg.filter_contracts is True

    raw_after = yaml.safe_load(path.read_text(encoding="utf-8"))

    # (2) A key no dataclass models survives untouched.
    assert raw_after["some_future_key"] == 1

    # (3) An unknown per-source key survives in `extra` and round-trips back
    #     into the YAML alongside the modeled source fields.
    assert cfg.sources[0].extra == {"some_source_specific_key": "keep me"}
    assert raw_after["sources"][0]["some_source_specific_key"] == "keep me"

    # (4) Keys that existed before keep their relative order; save_config
    #     must not silently reorder the file. New keys Config models that
    #     were not present in the original file (notifications,
    #     location_filter, ...) are appended after, which is expected.
    after_order = list(raw_after.keys())
    original_prefix = [k for k in after_order if k in original_order]
    assert original_prefix == original_order


def test_save_config_preserves_source_extra_fields(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    cfg = Config(
        sources=[
            SourceConfig(
                name="adzuna",
                extra={"app_id": "abc123", "app_key": "def456"},
            )
        ]
    )

    save_config(cfg, path)
    reloaded = load_config(path)

    assert reloaded.sources[0].extra == {"app_id": "abc123", "app_key": "def456"}
    assert reloaded == cfg


def test_save_config_writes_fresh_file_when_missing(tmp_path) -> None:
    path = tmp_path / "does-not-exist-yet.yaml"
    assert not path.exists()

    save_config(Config(check_interval_minutes=45), path)

    assert path.exists()
    reloaded = load_config(path)
    assert reloaded.check_interval_minutes == 45


# ── sync_config (T10) ───────────────────────────────────────────────────────


def test_sync_config_updates_dst_in_place_from_src() -> None:
    """A setting change (e.g. match_threshold) must reach `dst` without
    `dst` itself being rebound — this is what lets a live settings update
    reach every object elsewhere already holding a reference to `dst`."""
    dst = Config()
    src = Config(match_threshold=85, check_interval_minutes=5)
    src.tag_visibility.hide_remote = True

    sync_config(dst, src)

    assert dst == src
    assert dst.match_threshold == 85
    assert dst.check_interval_minutes == 5
    assert dst.tag_visibility.hide_remote is True


def test_sync_config_preserves_nested_object_identity() -> None:
    """A caller holding a reference to `dst.tag_visibility` before the sync
    must observe the update through that same reference afterwards —
    sync_config must mutate nested dataclasses in place, not replace them."""
    dst = Config()
    held_tag_visibility = dst.tag_visibility
    held_location_filter = dst.location_filter

    src = Config()
    src.tag_visibility.hide_onsite = True
    src.location_filter.search_country = "Canada"

    sync_config(dst, src)

    assert dst.tag_visibility is held_tag_visibility
    assert dst.location_filter is held_location_filter
    assert held_tag_visibility.hide_onsite is True
    assert held_location_filter.search_country == "Canada"


def test_sync_config_via_settings_roundtrip_reaches_a_held_reference(tmp_path) -> None:
    """End-to-end: write new settings to config.yaml (as the settings
    dialog does), re-read it with load_config, sync_config it onto a live
    Config, and confirm a pre-existing holder of a nested sub-config sees
    the change — this is the exact mechanism `main.py::_apply_live_config`
    now uses in place of hand-parsing `raw` a second time."""
    path = tmp_path / "config.yaml"
    live_config = Config()
    held_quality_filter = live_config.quality_filter

    save_config(Config(match_threshold=42), path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\nquality_filter:\n  min_salary: 90000\n")

    fresh = load_config(path)
    sync_config(live_config, fresh)

    assert live_config.match_threshold == 42
    assert live_config.quality_filter is held_quality_filter
    assert held_quality_filter.min_salary == 90000
