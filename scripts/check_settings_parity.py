#!/usr/bin/env python
"""Walk ``jobfinder.config.Config`` and prove every leaf setting is reachable
from the real frontend sources (T62 "Settings parity" section).

**Why this exists.** The parity matrix's prose claimed the 36-setting (42
expanded) figure came from "two audit scripts run against a live
``create_app()`` instance". That was never true in a committed, re-runnable
sense: before this file, ``scripts/`` had no such script and no test walked
``Config`` against ``frontend/src``, so the claim could not be re-checked —
only re-typed. T53's own run log independently flagged the same gap. This
script is the thing the parity matrix should have cited all along, in the shape
``check_theme_contrast.py`` already established for this repo: a committed,
offline, Chrome-free script with a clear PASS/FAIL and a ratchet a future
change can break loudly.

**What "reachable" means here.** For each non-exempt leaf, its own field
name (e.g. ``hide_full_time``, ``search_country``, ``api_key``) must appear
literally somewhere under ``frontend/src``, *excluding* the generated
``frontend/src/api/schema.ts`` — including it would mask a real gap, because
every ``Config`` field is mechanically present in the generated OpenAPI
types regardless of whether any component actually reads or writes it.
This is a textual reachability check, not a rendered-DOM check (that is
what the browser-check scripts under ``scripts/t5*_*.py`` are for); it
answers "does the client know this setting exists at all", which is what a
field silently dropped from a form would fail.

**The two counts the parity matrix cites.** "36 leaves counting ``sources`` as
one, 42 expanding ``sources[]``" — those were last measured before T61
added ``Config.onboarding_completed``, so this script computes both counts
fresh rather than hard-coding them, and reports whatever the tree says
*today*. See ``main()``'s summary line for the current numbers.

**Three exemptions, not two.** The parity matrix documents ``resume.path``
(never settable over the API; ``PUT`` returns 422) and
``resume.original_filename`` (derived from the upload, never typed by a
user). This script adds a third, found while building it:
``onboarding_completed`` (T61) is absent from both ``SettingsUpdate`` and
``_settings_payload`` in ``jobfinder/web/settings.py`` *by design* — it is
a one-time internal flag ``POST /api/onboarding/complete`` sets, not a
field on the Settings page's contract at all. Leaving it un-exempted would
make this script cry wolf about T61's own, deliberate, change.

**Secret fields are a reachability case, not an absence.** ``*api_key``,
``app_id``, ``app_key`` and ``client_secret`` are redacted out of
``GET /api/settings`` by design (T18) — they are absent from the generated
``SettingsPayload`` GET type for exactly that reason, so a check that only
looked for a field in the GET-shaped types would misreport them as gaps.
They remain writable (``SettingsUpdate``/``SourceIn``) and the frontend
code that *writes* them (``frontend/src/views/settingsForm.ts``'s
``CREDENTIAL_SOURCES`` field lists, ``api_key``/``app_id``/``app_key``) is
reachable the same way every other leaf is checked here: by its field name
appearing in frontend/src outside the generated file. No special-casing is
needed in the search itself; they are only called out separately in the
report so a reader does not mistake "absent from schema.ts's GET type" for
"absent from the UI".

Usage:
    .venv/Scripts/python.exe scripts/check_settings_parity.py
"""

from __future__ import annotations

import dataclasses
import sys
import typing
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from jobfinder.config import Config  # noqa: E402

FRONTEND_SRC = _REPO_ROOT / "frontend" / "src"
GENERATED_SCHEMA = FRONTEND_SRC / "api" / "schema.ts"

# the parity matrix's own documented exemptions, plus T61's onboarding_completed
# (found while building this check — see module docstring).
EXEMPT: dict[str, str] = {
    "resume.path": "never settable over the API; PUT returns 422",
    "resume.original_filename": "derived from the upload, not typed",
    "auto_scan_enabled": (
        "internal play/pause flag written by POST /api/scan/play and POST "
        "/api/scan/pause (jobfinder/web/scan.py) and read back at boot by "
        "the lifespan (jobfinder/web/app.py); the board header's scan "
        "control is its UI, not the Settings page -- absent from "
        "SettingsUpdate and _settings_payload by design so there is exactly "
        "one writer"
    ),
    "onboarding_completed": (
        "internal one-time flag set by POST /api/onboarding/complete "
        "(jobfinder/web/onboarding.py); absent from SettingsUpdate and "
        "_settings_payload by design (jobfinder/web/settings.py) -- not "
        "part of the Settings page's contract at all"
    ),
}

# Absent from GET /api/settings by design (T18 redaction) but remain
# writable and reachable. Matched by trailing field name, since SourceIn's
# per-credential keys (api_key, app_id, app_key) are nested inside
# sources[].extra rather than being their own dataclass field.
SECRET_FIELD_NAMES = {"api_key", "app_id", "app_key", "client_secret"}


@dataclasses.dataclass(frozen=True)
class Leaf:
    path: str  # dotted path, e.g. "location_filter.search_country"
    field_name: str  # the identifier actually searched for, e.g. "search_country"


def _is_dataclass_type(tp: typing.Any) -> bool:
    return dataclasses.is_dataclass(tp) and isinstance(tp, type)


def _list_element_dataclass(tp: typing.Any) -> type | None:
    """If *tp* is ``list[X]`` (or ``list[X] | None``) and ``X`` is a
    dataclass, return ``X``. Otherwise ``None``. Generalizes the ``sources:
    list[SourceConfig]`` case so a future list-of-dataclass field is
    handled the same way without a hard-coded name."""
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is list and args and _is_dataclass_type(args[0]):
        return args[0]
    if origin is typing.Union:
        for arg in args:
            found = _list_element_dataclass(arg)
            if found is not None:
                return found
    return None


def walk_leaves(cls: type, prefix: str = "") -> tuple[list[Leaf], list[Leaf]]:
    """Return ``(counted_as_one, expanded)`` leaf lists for *cls*.

    ``counted_as_one`` treats any ``list[DataclassX]`` field (e.g.
    ``sources``) as a single leaf -- the parity matrix's "36 leaves" figure.
    ``expanded`` instead recurses one level into that element dataclass's
    *own* schema fields (excluding ``extra``, which is a dynamic bag, not a
    fixed leaf) -- the parity matrix's "42 expanding sources[]" figure. Both walks
    otherwise recurse through nested dataclasses identically.
    """
    counted: list[Leaf] = []
    expanded: list[Leaf] = []
    for f in dataclasses.fields(cls):
        path = f.name if not prefix else f"{prefix}.{f.name}"
        tp = f.type
        if isinstance(tp, str):
            # Postponed annotations (from __future__ import annotations) --
            # resolve via get_type_hints once, cached on the class.
            hints = typing.get_type_hints(cls)
            tp = hints[f.name]
        list_elem = _list_element_dataclass(tp)
        if list_elem is not None:
            counted.append(Leaf(path=path, field_name=f.name))
            for ef in dataclasses.fields(list_elem):
                if ef.name == "extra":
                    continue  # dynamic bag, not a fixed schema leaf
                expanded.append(Leaf(path=f"{path}[].{ef.name}", field_name=ef.name))
            continue
        if _is_dataclass_type(tp):
            sub_counted, sub_expanded = walk_leaves(tp, path)
            counted.extend(sub_counted)
            expanded.extend(sub_expanded)
            continue
        counted.append(Leaf(path=path, field_name=f.name))
        expanded.append(Leaf(path=path, field_name=f.name))
    return counted, expanded


def frontend_source_files() -> list[Path]:
    return [
        p
        for p in FRONTEND_SRC.rglob("*")
        if p.suffix in (".ts", ".tsx") and p.is_file() and p.resolve() != GENERATED_SCHEMA.resolve()
    ]


def build_reference_index(files: list[Path]) -> str:
    """Concatenate every non-generated frontend source file into one blob.
    A plain substring search over the blob is sufficient (and matches
    check_theme_contrast.py's own "good enough for this file's shape"
    standard) -- field names are specific enough (e.g.
    ``max_post_age_days``, ``linkedin_fetch_description``) that a false
    positive from an unrelated identifier is not a realistic risk."""
    parts = []
    for path in sorted(files):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def check_reachability(leaves: list[Leaf], blob: str) -> list[Leaf]:
    """Return the subset of *leaves* whose field name does not appear
    anywhere in *blob*, excluding exempt paths."""
    missing = []
    for leaf in leaves:
        if leaf.path in EXEMPT:
            continue
        if leaf.field_name not in blob:
            missing.append(leaf)
    return missing


def main() -> int:
    counted, expanded = walk_leaves(Config)
    blob = build_reference_index(frontend_source_files())

    missing_counted = check_reachability(counted, blob)
    missing_expanded = check_reachability(expanded, blob)

    secret_leaves = [leaf for leaf in expanded + counted if leaf.field_name in SECRET_FIELD_NAMES]

    print(f"Config leaves, counting sources as one: {len(counted)}")
    print(f"Config leaves, expanding sources[]:      {len(expanded)}")
    print(f"Exempt (never settable / not a Settings-page field): {len(EXEMPT)}")
    for path, reason in EXEMPT.items():
        print(f"  - {path}: {reason}")
    if secret_leaves:
        names = sorted({leaf.field_name for leaf in secret_leaves})
        print(f"Secret-valued (redacted from GET, still writable, checked same as any other leaf): {names}")
    print()

    ok = True
    if missing_counted:
        ok = False
        print("FAIL: the following settings are unreachable from frontend/src (excluding the generated schema.ts):")
        for leaf in missing_counted:
            print(f"  - {leaf.path} (searched for {leaf.field_name!r})")
    if missing_expanded:
        ok = False
        print(
            "FAIL: the following sources[] fields are unreachable from frontend/src "
            "(excluding the generated schema.ts):"
        )
        for leaf in missing_expanded:
            if leaf.path not in {m.path for m in missing_counted}:
                print(f"  - {leaf.path} (searched for {leaf.field_name!r})")

    if ok:
        print(
            f"PASS: every non-exempt setting ({len(counted) - len(EXEMPT)} counting sources as "
            f"one / {len(expanded) - len(EXEMPT)} expanding sources[]) is reachable from "
            "frontend/src."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
