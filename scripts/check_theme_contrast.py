#!/usr/bin/env python
"""Compute WCAG contrast ratios for text-on-surface token pairs (T56 DoD).

Retargeted by T56: the palette's canonical home used to be
a Python dataclass module this script imported its DARK/LIGHT values
from directly. That module is retired — CSS is now the source of
truth, so this script reads the colour values straight out of
`frontend/src/theme/tokens.css` instead of a Python import, and computes
the WCAG relative-luminance contrast ratio for each (foreground,
background) token pair the new palette actually puts together
(docs/DESIGN.md's "Palette" section), reporting every ratio — passing or
not.

The old `PAIRS` table named pairs after the Qt stylesheet rules that used
them (`QFrame#card`, `QPushButton#primary`, ...); that stylesheet was
deleted in T33, and the table survived only as "the pair table happened to
be named after dead widgets". T56 re-expresses it in terms of the actual
CSS custom-property pairs docs/DESIGN.md measured, which is also why the
ratchet (`KNOWN_FAILING`) drops to 0: those six-and-a-half inherited
failures were never chosen, and the redesign picks colours that clear AA
outright rather than re-inheriting them (the product spec / the task ledger, which
this task supersedes).

This script also carries the missing-token safety net (the task ledger DoD,
third bullet): a CSS custom property referenced by the UI with no
`var(--x, fallback)` escape hatch, but not declared in a given theme
block, resolves to nothing and the element renders unstyled or inherits
rather than failing loudly. `find_var_references` / `missing_required_tokens`
below are reused, unmodified, by tests/test_theme_css_tokens.py so the
parsing logic lives in exactly one place.

Usage:
    .venv/Scripts/python.exe scripts/check_theme_contrast.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = _REPO_ROOT / "frontend" / "src" / "theme" / "tokens.css"
FRONTEND_SRC = _REPO_ROOT / "frontend" / "src"

AA_NORMAL = 4.5
AA_LARGE = 3.0

# Which CSS block (by label) holds which theme's values. Four blocks exist
# in tokens.css because of the host precedence contract documented at its
# head: explicit `data-theme` attribute > OS `prefers-color-scheme` > the
# bare `:root` fallback. Dark is now the default (docs/DESIGN.md), so the
# bare `:root` block carries the dark palette and the media-query block
# carries the light override — the inverse of the pre-T56 layout.
CSS_BLOCK_PATTERNS: list[tuple[str, str]] = [
    (r":root\s*{", "root_default"),
    (
        r'@media \(prefers-color-scheme: light\)\s*{\s*:root:not\(\[data-theme="dark"\]\)\s*{',
        "os_light_override",
    ),
    (r':root\[data-theme="dark"\]\s*{', "explicit_dark"),
    (r':root\[data-theme="light"\]\s*{', "explicit_light"),
]

# (fg var, bg var, usage note) — one entry per row of docs/DESIGN.md's
# "Palette" tables, in the same order. Light has no documented "raised"
# elevation tier (only dark's worst pair, dim-on-raised at 6.24, needed
# one), so light's table has one fewer row than dark's — that is a
# property of the design doc, not a pair dropped here to dodge a failure.
DARK_PAIRS: list[tuple[str, str, str]] = [
    ("--jf-text", "--jf-ground", "primary text on page background"),
    ("--jf-text", "--jf-surface", "primary text on card/panel surface"),
    ("--jf-dim", "--jf-surface", "secondary/meta text on card/panel surface"),
    ("--jf-dim", "--jf-raised", "secondary/meta text on an elevated surface"),
    ("--jf-accent", "--jf-surface", "accent text/icon on card/panel surface"),
    ("--jf-good", "--jf-surface", "positive status text on card/panel surface"),
    ("--jf-warn", "--jf-surface", "warning status text on card/panel surface"),
    ("--jf-bad", "--jf-surface", "negative/error status text on card/panel surface"),
]

LIGHT_PAIRS: list[tuple[str, str, str]] = [
    ("--jf-text", "--jf-ground", "primary text on page background"),
    ("--jf-text", "--jf-surface", "primary text on card/panel surface"),
    ("--jf-dim", "--jf-surface", "secondary/meta text on card/panel surface"),
    ("--jf-accent", "--jf-surface", "accent text/icon on card/panel surface"),
    ("--jf-good", "--jf-surface", "positive status text on card/panel surface"),
    ("--jf-warn", "--jf-surface", "warning status text on card/panel surface"),
    ("--jf-bad", "--jf-surface", "negative/error status text on card/panel surface"),
]

# Inherited AA failures used to be tolerated here (the product spec / the task ledger
# T44): six pairs, carried verbatim from theme.py, never chosen. T56 picks
# colours while retargeting, so there is nothing left to tolerate.
KNOWN_FAILING = 0

_CUSTOM_PROP_RE = re.compile(r"(--jf-[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;")
_VAR_REF_RE = re.compile(r"var\(\s*(--jf-[a-z0-9-]+)\s*(,[^)]*)?\)")


def extract_css_blocks(css_text: str) -> dict[str, str]:
    """Return {label: block_body} for each of CSS_BLOCK_PATTERNS, found by
    locating the opening brace of the (possibly nested) selector and
    matching it to its closing brace. Good enough for tokens.css's shape:
    one declaration block per selector, at most one level of @media
    nesting."""
    blocks: dict[str, str] = {}
    for pattern, label in CSS_BLOCK_PATTERNS:
        m = re.search(pattern, css_text)
        if not m:
            blocks[label] = ""
            continue
        start = m.end()  # just past the opening "{"
        depth = 1
        i = start
        while i < len(css_text) and depth > 0:
            if css_text[i] == "{":
                depth += 1
            elif css_text[i] == "}":
                depth -= 1
            i += 1
        blocks[label] = css_text[start : i - 1]
    return blocks


def parse_custom_properties(block_text: str) -> dict[str, str]:
    """Return {var_name: lowercase hex value} for every `--jf-*: #rrggbb;`
    declaration found directly in *block_text*."""
    return {name: value.lower() for name, value in _CUSTOM_PROP_RE.findall(block_text)}


def find_var_references(root: Path) -> tuple[set[str], set[str]]:
    """Scan every .css/.tsx/.ts file under *root* for `var(--jf-...)` usage.

    Returns (required, has_fallback): *required* is every var name used at
    least once with no `var(--x, fallback)` escape hatch (so an undefined
    token would resolve to nothing); *has_fallback* is every var name used
    at least once *with* one (so it degrades gracefully even if undefined,
    which is not the silent failure this check exists to catch).
    """
    required: set[str] = set()
    has_fallback: set[str] = set()
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".css", ".tsx", ".ts") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for name, fallback in _VAR_REF_RE.findall(text):
            if fallback:
                has_fallback.add(name)
            else:
                required.add(name)
    return required, has_fallback


def missing_required_tokens(required: set[str], blocks: dict[str, str]) -> dict[str, set[str]]:
    """For each CSS block, return the subset of *required* not declared in
    it (checked as a simple `f"{var}:"` substring — good enough for this
    file's shape, and it is what actually catches a dropped declaration)."""
    missing: dict[str, set[str]] = {}
    for label, body in blocks.items():
        missing[label] = {var for var in required if f"{var}:" not in body}
    return missing


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def report(theme_label: str, token_map: dict[str, str], pairs: list[tuple[str, str, str]]) -> bool:
    print(f"--- {theme_label} ---")
    all_pass_normal = True
    for fg_var, bg_var, usage in pairs:
        fg = token_map[fg_var]
        bg = token_map[bg_var]
        ratio = contrast(fg, bg)
        # WCAG 1.4.3 requires 4.5:1 for normal text and relaxes to 3:1 ONLY
        # for large text (>= 24px, or >= 18.66px bold). Every pair here is
        # body/meta/status text, well under that, so the 3:1 allowance does
        # not apply to any of them.
        if ratio >= AA_NORMAL:
            status = "PASS (AA normal text)"
        elif ratio >= AA_LARGE:
            status = f"FAIL (AA small text needs {AA_NORMAL}:1; {ratio:.2f} would only pass as large text)"
            all_pass_normal = False
        else:
            status = f"FAIL (below AA large-text {AA_LARGE}:1 as well)"
            all_pass_normal = False
        print(f"  {fg_var:14} on {bg_var:14} = {ratio:5.2f}  {status:55} ({usage})")
    return all_pass_normal


def _count_failures(token_map: dict[str, str], pairs: list[tuple[str, str, str]]) -> int:
    n = 0
    for fg_var, bg_var, _usage in pairs:
        if contrast(token_map[fg_var], token_map[bg_var]) < AA_NORMAL:
            n += 1
    return n


def main() -> int:
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    blocks = extract_css_blocks(css_text)
    dark_map = parse_custom_properties(blocks["explicit_dark"])
    light_map = parse_custom_properties(blocks["explicit_light"])

    dark_ok = report("dark", dark_map, DARK_PAIRS)
    print()
    light_ok = report("light", light_map, LIGHT_PAIRS)
    print()

    failures = _count_failures(dark_map, DARK_PAIRS) + _count_failures(light_map, LIGHT_PAIRS)

    if dark_ok and light_ok:
        print("PASS: every checked text-on-surface pair meets AA normal-text contrast (>= 4.5:1).")
    else:
        print(
            f"FAIL: {failures} text-on-surface pair(s) do not meet WCAG AA for small text (4.5:1).\n"
            f"Known-failing baseline is {KNOWN_FAILING} (the product spec / the task ledger superseded T44's "
            "six-pair ratchet) — the new palette was chosen to clear AA outright, so any failure here "
            "is new, not inherited."
        )

    required, _has_fallback = find_var_references(FRONTEND_SRC)
    missing = missing_required_tokens(required, blocks)
    offenders = {label: sorted(names) for label, names in missing.items() if names}
    if offenders:
        print(f"\nFAIL: token(s) referenced with no fallback are undefined in a theme block: {offenders}")
        return 1

    # Ratchet: exits non-zero only if a new failure appears beyond the
    # (now zero) tolerated baseline.
    return 0 if failures <= KNOWN_FAILING else 1


if __name__ == "__main__":
    sys.exit(main())
