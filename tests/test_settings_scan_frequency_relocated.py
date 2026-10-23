"""T98 (a): "Scan frequency" was filed under Appearance.

The owner: "can't seem to find the setting to set the time delay for the
scan to restart." The control existed — `SettingsTabs.tsx`'s
`AppearanceTab`, a 1-120 minute slider — but a scanning setting living
under "how the app looks" is why it could not be found.

This asserts the move against `settingsFieldMap.ts` (the registry the tab
components actually render from) and against the rendered tab bodies in
the real `.tsx` files, the same way tests/test_settings_field_map.py
asserts T53's relocations — not by string-matching rendered console
output. It reuses that module's own TS-source parser rather than
re-implementing it, so the two files cannot read the registry two
different ways.
"""

from __future__ import annotations

import pytest

from tests.test_settings_field_map import (
    TAB_COMPONENTS,
    _component_body,
    _read,
    _registry_entries,
)
from tests.test_settings_field_map import FIELD_MAP_TS, SETTINGS_TABS_TSX

FORM_FIELD = "checkIntervalMinutes"
CONFIG_PATH = "check_interval_minutes"


@pytest.fixture(scope="module")
def field_map_source() -> str:
    return _read(FIELD_MAP_TS)


@pytest.fixture(scope="module")
def tab_bodies() -> dict[str, str]:
    return {key: _component_body(path, name) for key, (path, name) in TAB_COMPONENTS.items()}


def test_registry_declares_scan_frequency_on_sources(field_map_source: str) -> None:
    entries = _registry_entries(field_map_source)
    assert FORM_FIELD in entries, f"{FORM_FIELD} is missing from SETTINGS_FIELDS"
    entry = entries[FORM_FIELD]
    assert entry.get("configPath") == CONFIG_PATH
    assert entry.get("tab") == "sources", f"registry says tab {entry.get('tab')!r}, expected 'sources'"


def test_scan_frequency_is_bound_in_exactly_one_tab_and_it_is_sources(tab_bodies: dict[str, str]) -> None:
    import re

    pattern = re.compile(rf"\bform\.{re.escape(FORM_FIELD)}\b")
    homes = sorted(key for key, body in tab_bodies.items() if pattern.search(body))
    assert homes == ["sources"], f"{FORM_FIELD} is rendered by {homes}, expected only ['sources']"


def test_scan_frequency_is_no_longer_reachable_from_appearance(tab_bodies: dict[str, str]) -> None:
    assert "checkIntervalMinutes" not in tab_bodies["appearance"], (
        "Appearance still renders the scan-interval control — T98 moves it to Sources, it does not duplicate it"
    )


def test_registry_tab_matches_where_the_field_is_actually_bound(
    field_map_source: str, tab_bodies: dict[str, str]
) -> None:
    entries = _registry_entries(field_map_source)
    declared = entries[FORM_FIELD]["tab"]
    import re

    pattern = re.compile(rf"\bform\.{re.escape(FORM_FIELD)}\b")
    actual = [key for key, body in tab_bodies.items() if pattern.search(body)]
    assert actual == [declared], f"{FORM_FIELD}: registry says tab {declared!r}, JSX binds it in {actual}"


def test_appearance_tab_no_longer_has_a_scan_section_label(tab_bodies: dict[str, str]) -> None:
    """Belt-and-suspenders against a stray duplicate Section left behind
    with a different form-field name bound inside it."""
    assert '"Scan frequency"' not in tab_bodies["appearance"]


def test_sources_tab_source_still_references_the_field_map_hint(field_map_source: str) -> None:
    """The hint shown on screen should answer the owner's own words — "the
    time delay for the scan to restart" — not just restate the label."""
    entries = _registry_entries(field_map_source)
    hint = entries[FORM_FIELD].get("hint", "").lower()
    assert "delay" in hint or "wait" in hint, f"hint does not speak to the owner's complaint: {hint!r}"
    assert "restart" in hint or "again" in hint or "next" in hint, f"hint does not say what happens next: {hint!r}"


def test_sources_tab_renders_scan_frequency_from_the_registry() -> None:
    """The on-screen label/hint come from SETTINGS_FIELDS, not an inlined
    duplicate string, so the registry and the screen cannot drift apart."""
    view_source = _read(SETTINGS_TABS_TSX.parent / "SettingsView.tsx")
    assert "SETTINGS_FIELDS.checkIntervalMinutes" in view_source
