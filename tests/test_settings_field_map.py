"""T53: each relocated setting is rendered by exactly one settings tab.

The owner's report was "duplication between Basic setup and Role". The
mechanism was not duplication but **one concept split across two homes**:
the job titles you search for lived in Basic while the job functions and
seniority that filter them lived in Role, and "location" meant two
different things under similar labels in two different tabs
(``location_filter.*`` = where I will accept a job; ``sources[].location``
= where to point the search), with ``remote_ok_worldwide`` — part of the
first concept — sitting in Filters.

These tests assert the fix **mechanically, against the sources**, not
against a restatement of it:

* ``frontend/src/views/settingsFieldMap.ts`` is the registry the tab
  components render from. Asserting over it is asserting over the thing
  that actually drives the UI, not a parallel list that can rot.
* The JSX scan (:func:`test_each_relocated_field_is_bound_in_exactly_one_tab`)
  is the drift guard: it counts ``form.<field>`` bindings per tab
  component in the real .tsx files, so re-adding a stray duplicate input
  fails even if the registry is left untouched.

The seven-tab modal these tabs live in is scheduled for deletion in
Phase 8 (T59/T60 — see docs/DESIGN.md). The *grouping* asserted here is
the part meant to survive that: it carries into the configuration page
the navigation rail opens. The tab container is not.

The config-preservation half of the DoD is in
``tests/test_config_roundtrip_pre_t53.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND_VIEWS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "views"
FIELD_MAP_TS = FRONTEND_VIEWS / "settingsFieldMap.ts"
SETTINGS_VIEW_TSX = FRONTEND_VIEWS / "SettingsView.tsx"
SETTINGS_TABS_TSX = FRONTEND_VIEWS / "SettingsTabs.tsx"

# The seven settings named by the T53 DoD, keyed by their *form* field name
# (what the.tsx binds) and valued by their config path (what the task ledger
# calls them). Both vocabularies are asserted below.
RELOCATED_FIELDS: dict[str, str] = {
    "preferredRegionsText": "location_filter.preferred_regions",
    "searchCountry": "location_filter.search_country",
    "remoteOkWorldwide": "location_filter.remote_ok_worldwide",
    "searchLocation": "sources[].location",
    "jobTitlesText": "sources[].search_terms",
    "jobFunctionsText": "role_filter.job_functions",
    "seniorityLevelsText": "role_filter.seniority_levels",
}

# The exported React component that renders each tab, by tab key. A field
# "appears in a tab" iff it is bound inside one of these functions.
TAB_COMPONENTS: dict[str, tuple[Path, str]] = {
    "basic": (SETTINGS_VIEW_TSX, "BasicTab"),
    "sources": (SETTINGS_VIEW_TSX, "SourcesTab"),
    "role": (SETTINGS_TABS_TSX, "RoleTab"),
    "location": (SETTINGS_TABS_TSX, "LocationTab"),
    "filters": (SETTINGS_TABS_TSX, "FiltersTab"),
    "ai": (SETTINGS_TABS_TSX, "AiVerificationTab"),
    "appearance": (SETTINGS_TABS_TSX, "AppearanceTab"),
    "advanced": (SETTINGS_TABS_TSX, "AdvancedTab"),
}


def _read(path: Path) -> str:
    assert path.is_file(), f"missing source file: {path}"
    return path.read_text(encoding="utf-8")


def _component_body(path: Path, name: str) -> str:
    """The source text of one ``function <name>(...) { ... }``'s body.

    Brace-counted rather than regex-matched to the next ``function``, so a
    nested helper or an object literal inside the body cannot truncate or
    over-extend the slice.

    The parameter list has to be skipped by *paren* counting first. Every
    tab component here takes a destructured props object, so the first
    ``{`` after the function name opens the parameters
    (``function RoleTab({ form, patch }: {...})``), not the body — reading
    from it yields ``{ form, patch }`` and every field then looks absent.
    """
    text = _read(path)
    match = re.search(rf"\bfunction\s+{re.escape(name)}\s*\(", text)
    assert match, f"no function {name} in {path.name}"

    # Walk the parameter list to its closing paren.
    depth = 0
    params_end = None
    for i in range(match.end() - 1, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                params_end = i
                break
    assert params_end is not None, f"unbalanced parens in {name} ({path.name})"

    start = text.index("{", params_end)
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name} ({path.name})")


@pytest.fixture(scope="module")
def tab_bodies() -> dict[str, str]:
    return {key: _component_body(path, name) for key, (path, name) in TAB_COMPONENTS.items()}


@pytest.fixture(scope="module")
def field_map_source() -> str:
    return _read(FIELD_MAP_TS)


_STRING_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _string_expression(expr: str, constants: dict[str, str]) -> str | None:
    """Evaluate a TS expression made only of string literals, ``+`` and
    references to module-level string constants.

    Returns None for anything else, so a value this parser cannot read
    safely is reported as absent rather than guessed at. Tokenised rather
    than regex-matched as a whole, because the hints in settingsFieldMap.ts
    legitimately contain ``+``-free apostrophes, quoted phrases and line
    continuations, and a single monolithic pattern for that is how a test
    parser turns into a liability.
    """
    tokens = [t for t in (part.strip() for part in expr.strip().rstrip(",").split("+")) if t]
    if not tokens:
        return None
    out: list[str] = []
    for token in tokens:
        literal = _STRING_LITERAL.fullmatch(token)
        if literal:
            out.append(literal.group(1))
        elif token in constants:
            out.append(constants[token])
        else:
            return None
    return "".join(out)


def _module_string_constants(source: str) -> dict[str, str]:
    """Top-level ``const NAME = "..." + "...";`` string constants.

    settingsFieldMap.ts shares the group headings through constants rather
    than repeating each literal per entry — the right call, since fields in
    a group then *cannot* drift apart — but it means this parser has to
    dereference them.
    """
    constants: dict[str, str] = {}
    pattern = r"^const (\w+)\s*(?::[^=\n]*)?=\s*(.*?);\s*$"
    for match in re.finditer(pattern, source, re.MULTILINE | re.DOTALL):
        value = _string_expression(match.group(2), {})
        if value is not None:
            constants[match.group(1)] = value
    return constants


def _registry_entries(source: str) -> dict[str, dict[str, str]]:
    """Parse ``SETTINGS_FIELDS`` out of settingsFieldMap.ts.

    Parsing the real module, rather than restating its contents here, is
    what makes this test track the UI instead of a copy of it.
    """
    constants = _module_string_constants(source)
    block = re.search(r"export const SETTINGS_FIELDS[^=]*=\s*\{(.*?)\n\};", source, re.DOTALL)
    assert block, "settingsFieldMap.ts must export a SETTINGS_FIELDS object literal"

    entries: dict[str, dict[str, str]] = {}
    record = r"\n  (\w+):\s*\{(.*?)\n  \},"
    for name, body in re.findall(record, block.group(1) + "\n  },", re.DOTALL):
        fields: dict[str, str] = {}
        # Split the record on its own keys: a key is `    word:` at the
        # record's indent, and its value is everything up to the next one.
        key_positions = [(m.group(1), m.start(), m.end()) for m in re.finditer(r"^    (\w+):", body, re.MULTILINE)]
        for i, (key, _start, value_from) in enumerate(key_positions):
            value_to = key_positions[i + 1][1] if i + 1 < len(key_positions) else len(body)
            value = _string_expression(body[value_from:value_to], constants)
            if value is not None:
                fields[key] = value
        entries[name] = fields
    return entries


# --------------------------------------------------------------------------
# The DoD clause: exactly one tab, each.
# --------------------------------------------------------------------------


def test_registry_covers_all_seven_relocated_fields(field_map_source: str) -> None:
    entries = _registry_entries(field_map_source)
    missing = sorted(set(RELOCATED_FIELDS) - set(entries))
    assert not missing, f"not in SETTINGS_FIELDS: {missing}"


def test_registry_assigns_each_relocated_field_exactly_one_tab(field_map_source: str) -> None:
    entries = _registry_entries(field_map_source)
    for form_field, config_path in RELOCATED_FIELDS.items():
        entry = entries[form_field]
        assert entry.get("configPath") == config_path, (
            f"{form_field} should record configPath {config_path!r}, got {entry.get('configPath')!r}"
        )
        tab = entry.get("tab")
        assert tab in TAB_COMPONENTS, f"{form_field} has unknown tab {tab!r}"


def test_each_relocated_field_is_bound_in_exactly_one_tab(tab_bodies: dict[str, str]) -> None:
    """The drift guard — counted off the rendered tree, not the registry.

    This is the assertion that would catch someone re-introducing the
    original defect by pasting a second ``form.jobTitlesText`` input into
    Basic setup without touching settingsFieldMap.ts.
    """
    for form_field in RELOCATED_FIELDS:
        pattern = re.compile(rf"\bform\.{re.escape(form_field)}\b")
        homes = sorted(key for key, body in tab_bodies.items() if pattern.search(body))
        assert len(homes) == 1, (
            f"{form_field} is rendered by {len(homes)} tabs ({homes or 'none'}); T53 requires exactly one"
        )


def test_registry_tab_matches_where_the_field_is_actually_bound(
    field_map_source: str, tab_bodies: dict[str, str]
) -> None:
    """Registry and JSX must agree, or the registry is decoration."""
    entries = _registry_entries(field_map_source)
    for form_field in RELOCATED_FIELDS:
        declared = entries[form_field]["tab"]
        pattern = re.compile(rf"\bform\.{re.escape(form_field)}\b")
        actual = [key for key, body in tab_bodies.items() if pattern.search(body)]
        assert actual == [declared], f"{form_field}: registry says tab {declared!r}, JSX binds it in {actual}"


# --------------------------------------------------------------------------
# Role intent gets one home.
# --------------------------------------------------------------------------


def test_role_intent_lives_in_one_tab(field_map_source: str) -> None:
    """Titles, functions and seniority are one concept, so one tab.

    Job titles decide *what is searched for*; job functions and seniority
    decide *which of those results survive*. Splitting them across Basic
    and Role is what the owner saw as duplication.
    """
    entries = _registry_entries(field_map_source)
    tabs = {entries[f]["tab"] for f in ("jobTitlesText", "jobFunctionsText", "seniorityLevelsText")}
    assert len(tabs) == 1, f"role intent is still split across {sorted(tabs)}"


# --------------------------------------------------------------------------
# Location gets one home, with the two concepts named.
# --------------------------------------------------------------------------

LOCATION_FIELDS = ("preferredRegionsText", "searchCountry", "remoteOkWorldwide", "searchLocation")

# Fields describing where the user will *accept* a job, vs. the single
# field describing where the *search* is pointed.
ACCEPTANCE_FIELDS = ("preferredRegionsText", "searchCountry", "remoteOkWorldwide")
SEARCH_SCOPE_FIELD = "searchLocation"


def test_location_lives_in_one_tab(field_map_source: str) -> None:
    entries = _registry_entries(field_map_source)
    tabs = {entries[f]["tab"] for f in LOCATION_FIELDS}
    assert len(tabs) == 1, f"location is still split across {sorted(tabs)}"


def test_the_two_location_concepts_are_labelled_as_different_things(
    field_map_source: str,
) -> None:
    """Same tab is not enough — the labels must state the difference.

    Co-locating ``search_country`` and ``sources[].location`` without
    distinguishing them would make the confusion *worse*, not better:
    they would then sit side by side under near-identical names. So each
    relocated location field declares which of the two concepts it
    belongs to, and the group headings say so in words.
    """
    entries = _registry_entries(field_map_source)

    for form_field in ACCEPTANCE_FIELDS:
        assert entries[form_field].get("concept") == "acceptance", (
            f"{form_field} should be declared concept=acceptance "
            f"(where the user will take a job), got {entries[form_field].get('concept')!r}"
        )
    assert entries[SEARCH_SCOPE_FIELD].get("concept") == "search-scope", (
        "searchLocation is sources[].location — where the search is pointed, not where the user will accept work"
    )

    # The two group headings must be distinct strings, and each must be
    # specific enough to disambiguate on its own.
    acceptance_group = entries[ACCEPTANCE_FIELDS[0]].get("groupLabel", "")
    search_group = entries[SEARCH_SCOPE_FIELD].get("groupLabel", "")
    assert acceptance_group and search_group, "both location groups need a groupLabel"
    assert acceptance_group != search_group, "the two location concepts share a heading — that is the defect T53 fixes"
    # Every acceptance field sits under the same heading.
    for form_field in ACCEPTANCE_FIELDS:
        assert entries[form_field].get("groupLabel") == acceptance_group

    accept_text = (acceptance_group + " " + entries[ACCEPTANCE_FIELDS[0]].get("groupHint", "")).lower()
    search_text = (search_group + " " + entries[SEARCH_SCOPE_FIELD].get("groupHint", "")).lower()
    assert any(w in accept_text for w in ("accept", "willing", "take a job", "work")), (
        f"the acceptance heading does not say it is about accepting work: {accept_text!r}"
    )
    assert any(w in search_text for w in ("search", "look", "where we query")), (
        f"the search-scope heading does not say it is about searching: {search_text!r}"
    )
    # And each must explicitly contrast itself with the other, so a user
    # reading one field alone can tell which of the two it is.
    assert "search" in accept_text or "search" in entries[ACCEPTANCE_FIELDS[0]].get("groupHint", "").lower(), (
        "the acceptance group should name the other concept to contrast with it"
    )


def test_location_labels_reach_the_rendered_tab(tab_bodies: dict[str, str]) -> None:
    """The registry's labels are what the tab renders.

    Without this, the labels asserted above could be true of the registry
    and false of the screen.
    """
    body = tab_bodies["location"]
    assert "SETTINGS_FIELDS" in body, (
        "LocationTab should render its labels from SETTINGS_FIELDS so the registry cannot drift from the screen"
    )


# --------------------------------------------------------------------------
# Reachability: a move must not become a loss.
# --------------------------------------------------------------------------


def test_every_relocated_field_is_still_reachable(tab_bodies: dict[str, str]) -> None:
    """The parity matrix's settings (37 leaves as of T62, see scripts/check_settings_parity.py) must stay reachable; moving is fine, losing is not."""
    all_bodies = "\n".join(tab_bodies.values())
    for form_field in RELOCATED_FIELDS:
        assert f"form.{form_field}" in all_bodies, f"{form_field} is no longer rendered anywhere"


def test_every_relocated_field_is_still_writable(tab_bodies: dict[str, str]) -> None:
    """Rendered read-only would also be a loss — each needs a patch call."""
    all_bodies = "\n".join(tab_bodies.values())
    for form_field in RELOCATED_FIELDS:
        assert re.search(rf"\b{re.escape(form_field)}:", all_bodies), (
            f"{form_field} is rendered but never patched — it cannot be edited"
        )


def test_every_tab_in_the_registry_is_rendered_by_the_settings_view() -> None:
    """A tab a field is assigned to must be a tab the user can open."""
    view = _read(SETTINGS_VIEW_TSX)
    for tab_key in TAB_COMPONENTS:
        assert re.search(rf'activeTab === "{tab_key}"', view), (
            f'SettingsView renders no branch for activeTab === "{tab_key}"'
        )
        assert re.search(rf'key: "{tab_key}"', view), f'no TAB_ITEMS entry for "{tab_key}"'
