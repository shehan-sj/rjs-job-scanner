"""Tests for T78: the measured palette and the pipeline
board's new tokens.

T78 changes ten existing ``--jf-*`` values to the measured palette and adds
the stage/fit/shell/support tokens the pipeline board (T81) needs. The
hard requirement this file exists to catch (the task ledger's own DoD text):
a ``--jf-stage-*`` or ``--jf-fit-*`` token declared in three of
``tokens.css``'s four theme blocks instead of all four is a silent bug —
the component falls back to nothing in exactly the one block where the
declaration is missing. Deleting one declaration from one block must fail
this file.

Amended by T88 ("Found while implementing T78"): the
pre-T56 unmigrated token family used to be pinned here as untouched
literal hex, deliberately marking T78's scope boundary ("T78 is
explicitly not T88"). T88 is that later task, and re-points every one of
those properties at the canonical/shell/support tokens by ``var()``
reference — the assertion below inverts accordingly.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = REPO_ROOT / "frontend" / "src" / "theme" / "tokens.css"
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_theme_contrast.py"

STAGE_TOKENS = [
    "--jf-stage-new",
    "--jf-stage-shortlist",
    "--jf-stage-applied",
    "--jf-stage-replied",
    "--jf-stage-out",
]

FIT_TOKENS = [
    "--jf-fit-0-bg",
    "--jf-fit-0-fg",
    "--jf-fit-1-bg",
    "--jf-fit-1-fg",
    "--jf-fit-2-bg",
    "--jf-fit-2-fg",
    "--jf-fit-3-bg",
    "--jf-fit-3-fg",
]

ALL_BOARD_TOKENS = STAGE_TOKENS + FIT_TOKENS

THEME_BLOCK_LABELS = ["root_default", "os_light_override", "explicit_dark", "explicit_light"]

# Tokens whose value must differ between dark and light per the brief's
# tables (every stage + every fit step qualifies; none of them repeat a
# value verbatim across modes).
DARK_LIGHT_DIFFER = set(ALL_BOARD_TOKENS)


def _load_check_theme_contrast():
    spec = importlib.util.spec_from_file_location("check_theme_contrast", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ctc():
    return _load_check_theme_contrast()


@pytest.fixture(scope="module")
def blocks(ctc):
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    return ctc.extract_css_blocks(css_text)


@pytest.fixture(scope="module")
def token_maps(ctc, blocks):
    return {label: ctc.parse_custom_properties(blocks[label]) for label in THEME_BLOCK_LABELS}


# ── every stage/fit token is declared in all four blocks ──────────────────


@pytest.mark.parametrize("token", ALL_BOARD_TOKENS)
@pytest.mark.parametrize("label", THEME_BLOCK_LABELS)
def test_board_token_declared_in_every_block(blocks, token, label):
    assert f"{token}:" in blocks[label], (
        f"{token} is missing from the {label!r} theme block — a stage/fit token "
        "must be declared in all four theme blocks (the task ledger DoD)."
    )


def test_all_board_tokens_present_in_dark_and_light_maps(token_maps):
    for label in ("explicit_dark", "explicit_light"):
        for token in ALL_BOARD_TOKENS:
            assert token in token_maps[label], f"{token} missing from {label}"


# ── dark vs light actually differ where the brief says they must ──────────


@pytest.mark.parametrize("token", sorted(DARK_LIGHT_DIFFER))
def test_board_token_differs_between_dark_and_light(token_maps, token):
    dark_val = token_maps["explicit_dark"][token]
    light_val = token_maps["explicit_light"][token]
    assert dark_val != light_val, f"{token} has the same value in dark and light ({dark_val})"


# ── os_light_override / root_default agree with their explicit twins ─────
# (root_default carries dark's values, os_light_override carries light's,
# same precedence contract the existing ten tokens already follow.)


@pytest.mark.parametrize("token", ALL_BOARD_TOKENS)
def test_root_default_matches_explicit_dark(token_maps, token):
    assert token_maps["root_default"][token] == token_maps["explicit_dark"][token]


@pytest.mark.parametrize("token", ALL_BOARD_TOKENS)
def test_os_light_override_matches_explicit_light(token_maps, token):
    assert token_maps["os_light_override"][token] == token_maps["explicit_light"][token]


# ── the brief's exact hex values, both modes ───────────────────────────────

EXPECTED_DARK = {
    "--jf-stage-new": "#4a93e8",
    "--jf-stage-shortlist": "#bc8430",
    "--jf-stage-applied": "#9a7be0",
    "--jf-stage-replied": "#d4708f",
    "--jf-stage-out": "#828b94",
    "--jf-fit-0-bg": "#22262b",
    "--jf-fit-0-fg": "#a0a7af",
    "--jf-fit-1-bg": "#2c3238",
    "--jf-fit-1-fg": "#c8ced4",
    "--jf-fit-2-bg": "#3a424a",
    "--jf-fit-2-fg": "#eceef0",
    "--jf-fit-3-bg": "#eceef0",
    "--jf-fit-3-fg": "#0d0f11",
}

EXPECTED_LIGHT = {
    "--jf-stage-new": "#1f6fbf",
    "--jf-stage-shortlist": "#8a5a14",
    "--jf-stage-applied": "#6b4bbf",
    "--jf-stage-replied": "#a83a63",
    "--jf-stage-out": "#626b74",
    "--jf-fit-0-bg": "#edeff1",
    "--jf-fit-0-fg": "#5a626a",
    "--jf-fit-1-bg": "#e1e5e8",
    "--jf-fit-1-fg": "#3f474e",
    "--jf-fit-2-bg": "#cfd5da",
    "--jf-fit-2-fg": "#15181b",
    "--jf-fit-3-bg": "#15181b",
    "--jf-fit-3-fg": "#ffffff",
}


@pytest.mark.parametrize("token", sorted(EXPECTED_DARK))
def test_dark_value_matches_brief(token_maps, token):
    assert token_maps["explicit_dark"][token] == EXPECTED_DARK[token]


@pytest.mark.parametrize("token", sorted(EXPECTED_LIGHT))
def test_light_value_matches_brief(token_maps, token):
    assert token_maps["explicit_light"][token] == EXPECTED_LIGHT[token]


# ── the ten changed base tokens actually carry the new measured values ────

EXPECTED_CHANGED_DARK = {
    "--jf-ground": "#0d0f11",
    "--jf-surface": "#15181b",
    "--jf-raised": "#1d2126",
    "--jf-border": "#2a2f35",
    "--jf-text": "#eceef0",
    "--jf-dim": "#a0a7af",
    "--jf-accent": "#4a93e8",
    "--jf-good": "#3fbf8e",
    "--jf-warn": "#d4a04a",
    "--jf-bad": "#e8808a",
}

EXPECTED_CHANGED_LIGHT = {
    "--jf-ground": "#fafaf9",
    "--jf-surface": "#ffffff",
    "--jf-raised": "#f1f2f3",
    "--jf-border": "#e0e3e6",
    "--jf-text": "#15181b",
    "--jf-dim": "#585f66",
    "--jf-accent": "#1f6fbf",
    "--jf-good": "#0e7a5a",
    "--jf-warn": "#8a5a14",
    "--jf-bad": "#b3261e",
}


@pytest.mark.parametrize("token", sorted(EXPECTED_CHANGED_DARK))
def test_changed_token_matches_measured_dark_value(token_maps, token):
    assert token_maps["explicit_dark"][token] == EXPECTED_CHANGED_DARK[token]


@pytest.mark.parametrize("token", sorted(EXPECTED_CHANGED_LIGHT))
def test_changed_token_matches_measured_light_value(token_maps, token):
    assert token_maps["explicit_light"][token] == EXPECTED_CHANGED_LIGHT[token]


# ── the pre-T56 unmigrated family is re-pointed at the new palette by
# T88 ("Found while implementing T78") — this section used
#    to pin T78's scope boundary ("T78 is explicitly not T88 — no
#    227-property churn") by asserting these tokens kept their literal
#    pre-T56 hex values. T88 is exactly the task that comment named, so
#    the assertion inverts: every one of these properties must now be a
#    `var()` reference onto an approved token, not a hex literal, in
#    every theme block (not just dark). ──────────────────────────────────

_VAR_DECL_RE = re.compile(r"(--jf-[a-z0-9-]+)\s*:\s*var\(\s*(--jf-[a-z0-9-]+)\s*\)\s*;")

# {old-family token: the token it must now resolve to by var() reference}
# (T88's mapping — one entry per property name, theme-independent, since
# each block's var() reference resolves inside its own block).
PRE_T56_REPOINT_MAP = {
    "--jf-bg": "--jf-ground",
    "--jf-card": "--jf-surface",
    "--jf-card-border": "--jf-border",
    "--jf-card-hover": "--jf-raised",
    "--jf-hdr": "--jf-surface",
    "--jf-hdr-line": "--jf-border",
    "--jf-text-sub": "--jf-dim",
    "--jf-text-dim": "--jf-dim",
    "--jf-input-bg": "--jf-raised",
    "--jf-input-border": "--jf-border",
    "--jf-input-focus": "--jf-accent",
    "--jf-btn-primary": "--jf-accent",
    "--jf-btn-primary-h": "--jf-accent-hover",
    "--jf-btn-primary-fg": "--jf-accent-fg",
    "--jf-btn-secondary": "--jf-raised",
    "--jf-btn-secondary-h": "--jf-border-strong",
    "--jf-btn-secondary-fg": "--jf-text",
    "--jf-btn-danger": "--jf-bad",
    "--jf-btn-danger-h": "--jf-bad-hover",
    "--jf-btn-danger-fg": "--jf-accent-fg",
    "--jf-btn-action": "--jf-accent",
    "--jf-btn-action-h": "--jf-accent-hover",
    "--jf-btn-action-fg": "--jf-accent-fg",
    "--jf-tab-active": "--jf-raised",
    "--jf-tab-active-fg": "--jf-text",
    "--jf-tab-inactive-fg": "--jf-dim",
    # score-strong is pinned to fit-2 (not the inverted top fit-3) because
    # only ScoreBadge.tsx computes its own foreground dynamically to match
    # an inverted step — every other static consumer does not, and this
    # task does not touch component CSS/TSX to fix that (reported, not
    # fixed). The consequence is score-strong and score-good share a
    # value, by design.
    "--jf-score-strong": "--jf-good",
    "--jf-score-good": "--jf-accent",
    "--jf-score-fair": "--jf-warn",
    "--jf-score-weak": "--jf-bad",
    "--jf-tag-skill-bg": "--jf-good-tint",
    "--jf-tag-skill-fg": "--jf-good",
    "--jf-tag-remote-bg": "--jf-raised",
    "--jf-tag-remote-fg": "--jf-text-2",
    "--jf-tag-hybrid-bg": "--jf-raised",
    "--jf-tag-hybrid-fg": "--jf-text-2",
    "--jf-tag-onsite-bg": "--jf-raised",
    "--jf-tag-onsite-fg": "--jf-text-2",
    "--jf-tag-warn-bg": "--jf-warn-tint",
    "--jf-tag-warn-fg": "--jf-warn",
}


@pytest.mark.parametrize("token", sorted(PRE_T56_REPOINT_MAP))
@pytest.mark.parametrize("label", THEME_BLOCK_LABELS)
def test_pre_t56_family_repointed_by_var_reference(blocks, token, label):
    """Every pre-T56 unmigrated-family token resolves to its T88-mapped
    target by `var()` reference, in all four theme blocks — not a literal
    hex of its own."""
    var_refs = dict(_VAR_DECL_RE.findall(blocks[label]))
    assert token in var_refs, f"{token} is not a var() reference in the {label!r} block (still a literal hex?)"
    assert var_refs[token] == PRE_T56_REPOINT_MAP[token], (
        f"{token} resolves to {var_refs[token]!r} in {label!r}, expected {PRE_T56_REPOINT_MAP[token]!r}"
    )


def test_pre_t56_family_has_no_remaining_hex_literal(token_maps):
    """None of the pre-T56 family tokens should appear in the hex-only
    token map at all post-T88 — if one does, it regressed to a literal
    hex instead of a var() reference."""
    for label in ("explicit_dark", "explicit_light"):
        offenders = sorted(set(PRE_T56_REPOINT_MAP) & set(token_maps[label]))
        assert offenders == [], f"{label}: pre-T56 token(s) still declared as a literal hex: {offenders}"
