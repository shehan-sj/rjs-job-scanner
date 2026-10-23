"""Tests for T62's mechanical settings-parity check
(``scripts/check_settings_parity.py``).

the parity matrix's Settings-parity section claimed its 36/42-setting figures came
from "two audit scripts run against a live create_app() instance". No such
script was ever committed, so the claim could not be re-run -- only
re-typed (T53's own run log flagged this independently). This file follows
``tests/test_theme_css_tokens.py``'s pattern for ``check_theme_contrast.py``:
load the script as a module, pin its structural behaviour with synthetic
fixtures, then run it for real against the actual ``Config`` tree and the
actual ``frontend/src``, so the whole thing is part of the committed
``pytest -q`` suite rather than a script nobody re-runs.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_settings_parity.py"


def _load_check_settings_parity():
    spec = importlib.util.spec_from_file_location("check_settings_parity", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def csp():
    return _load_check_settings_parity()


# ── The real tree, for real ───────────────────────────────────────────────


def test_main_exits_zero(csp, capsys):
    """Every non-exempt leaf of the real Config tree is reachable from the
    real frontend/src today. If this starts failing, a setting genuinely
    became unreachable (or a field was renamed on one side only) -- fix the
    gap, don't loosen this check."""
    assert csp.main() == 0


def test_current_leaf_counts(csp):
    """Pins today's actual counts so a change is visible in a diff rather
    than silently drifting. The parity matrix's own prose cites 36/42; T61 added
    Config.onboarding_completed after that figure was last measured, which
    is exactly the kind of drift this check exists to surface -- so this
    pins 37/44, not 36/42, and the parity matrix is updated accordingly rather than
    this test being bent to match stale prose.

    **38/45 since T97** (Phase 10), which added ``ui.console_hidden_levels``
    so the log console's per-level filter survives a reload. That is a new
    setting, not drift: the count moves because the product gained one, and
    this pin moving in the same commit as the field is the check working as
    designed rather than being bent.
    """
    counted, expanded = csp.walk_leaves(csp.Config)
    assert len(counted) == 38
    assert len(expanded) == 45


def test_exempt_paths_exist_in_the_real_tree(csp):
    """Guards EXEMPT itself against going stale: every exempt path must
    actually name a real leaf (counted or expanded), so a future rename
    that silently orphans an exemption is caught here rather than letting
    the exemption quietly stop meaning anything."""
    counted, expanded = csp.walk_leaves(csp.Config)
    all_paths = {leaf.path for leaf in counted} | {leaf.path for leaf in expanded}
    for exempt_path in csp.EXEMPT:
        assert exempt_path in all_paths, f"EXEMPT path {exempt_path!r} does not match any real leaf"


def test_onboarding_completed_is_absent_from_the_settings_contract(csp):
    """Pins *why* onboarding_completed is exempt, not just that it is:
    it must not appear in the GET payload or the PUT model, because it is
    not a Settings-page field at all (jobfinder/web/settings.py)."""
    import jobfinder.web.settings as settings_mod

    update_fields = set(settings_mod.SettingsUpdate.model_fields)
    assert "onboarding_completed" not in update_fields


# ── Fail-loudly, proven against synthetic data ────────────────────────────


def test_check_reachability_flags_a_genuinely_missing_field(csp):
    """The core ratchet property: a leaf whose field name appears nowhere
    in the frontend blob must be reported, not silently passed."""
    leaves = [
        csp.Leaf(path="quality_filter.min_salary", field_name="min_salary"),
        csp.Leaf(path="quality_filter.hide_promoted", field_name="hide_promoted"),
    ]
    blob = "this blob mentions hide_promoted but not the other one"
    missing = csp.check_reachability(leaves, blob)
    assert [leaf.path for leaf in missing] == ["quality_filter.min_salary"]


def test_check_reachability_skips_exempt_paths_even_when_missing(csp):
    """An exempt leaf must never be reported missing, even if it is
    genuinely absent from the blob -- that is the whole point of the
    exemption list."""
    leaves = [csp.Leaf(path="resume.path", field_name="path")]
    missing = csp.check_reachability(leaves, "")
    assert missing == []


def test_main_fails_loudly_when_a_real_field_is_removed(csp, monkeypatch, capsys):
    """End-to-end proof the ratchet bites: patch build_reference_index to
    return a blob with one real field's name surgically removed (string
    replace, not a frontend edit -- this does not touch the repo), and
    confirm main() now reports it and exits 1."""
    real_blob = csp.build_reference_index(csp.frontend_source_files())
    assert "hide_full_time" in real_blob  # sanity: the field really is there today
    sabotaged_blob = real_blob.replace("hide_full_time", "XXREMOVEDXX")
    assert "hide_full_time" not in sabotaged_blob

    monkeypatch.setattr(csp, "build_reference_index", lambda files: sabotaged_blob)
    exit_code = csp.main()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "tag_visibility.hide_full_time" in out


# ── sources[] expansion generalizes beyond the one hard-coded field ───────


def test_list_element_dataclass_detects_sources(csp):
    hints = {}
    for f in dataclasses.fields(csp.Config):
        if f.name == "sources":
            import typing

            hints = typing.get_type_hints(csp.Config)
            elem = csp._list_element_dataclass(hints["sources"])
            assert elem is not None
            assert elem.__name__ == "SourceConfig"
            return
    pytest.fail("Config.sources field not found")


def test_expanded_excludes_extra_bag(csp):
    """``SourceConfig.extra`` is a dynamic dict (per-source credentials,
    scraper-specific options) -- it must not appear as a fixed expanded
    leaf, since its keys vary by source and are not part of the schema."""
    _counted, expanded = csp.walk_leaves(csp.Config)
    assert not any(leaf.field_name == "extra" for leaf in expanded)
