#!/usr/bin/env python
"""Palette token health gate for T78 (the product spec steps 3 and 4).

Two checks, both modes:

1. **Lightness band** (3.9 step 3) — every one of the four chromatic
   stage colours (``--jf-stage-new/shortlist/applied/replied``; ``-out`` is
   deliberately neutral grey and excluded, same as 3.9's own "four
   chromatic stages") sits inside its mode's OKLCH ``L`` band: dark
   0.48-0.67, light 0.43-0.77. sRGB -> OKLab is the standard two-matrix
   pipeline (Björn Ottosson's published constants) kept to plain
   arithmetic — no colour library dependency is introduced for this.

2. **Contrast at real sizes** (3.9 step 4) — every stage colour against
   both ``--jf-surface`` and ``--jf-raised``, and every ``--jf-fit-N-fg``
   against its own ``--jf-fit-N-bg``, must clear WCAG AA (>= 4.5:1). The
   WCAG relative-luminance contrast formula is reused, unmodified, from
   ``check_theme_contrast.py`` so there is exactly one implementation of it
   in the repo.

Deliberately **not** attempted here: a colour-vision-deficiency simulator
for the ΔE/protan/deutan numbers in the product spec step 2. Those are recorded
as measured once, at decision time, and are out of scope for this gate
(the task ledger's own task text).

Usage:
    .venv/Scripts/python.exe scripts/check_palette_token_health.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = _REPO_ROOT / "frontend" / "src" / "theme" / "tokens.css"
_CHECK_THEME_CONTRAST_PATH = _REPO_ROOT / "scripts" / "check_theme_contrast.py"


def _load_check_theme_contrast():
    spec = importlib.util.spec_from_file_location("check_theme_contrast", _CHECK_THEME_CONTRAST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


ctc = _load_check_theme_contrast()

AA_NORMAL = 4.5

# the product spec step 3's OKLCH lightness bands, per mode.
LIGHTNESS_BAND = {
    "dark": (0.48, 0.67),
    "light": (0.43, 0.77),
}

# The four *chromatic* stages — "New"/"Shortlist"/"Applied"/"Replied".
# "--jf-stage-out" is deliberately neutral grey (not a hue), so 3.9 step 3's
# categorical-hue lightness band does not apply to it.
CHROMATIC_STAGE_TOKENS = [
    "--jf-stage-new",
    "--jf-stage-shortlist",
    "--jf-stage-applied",
    "--jf-stage-replied",
]

# All five stages are still checked for contrast (step 4 applies to every
# stage colour, chromatic or not).
ALL_STAGE_TOKENS = CHROMATIC_STAGE_TOKENS + ["--jf-stage-out"]

FIT_PAIRS = [
    ("--jf-fit-0-fg", "--jf-fit-0-bg"),
    ("--jf-fit-1-fg", "--jf-fit-1-bg"),
    ("--jf-fit-2-fg", "--jf-fit-2-bg"),
    ("--jf-fit-3-fg", "--jf-fit-3-bg"),
]

THEME_BLOCK_LABEL = {
    "dark": "explicit_dark",
    "light": "explicit_light",
}


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_oklch_l(hex_color: str) -> float:
    """Return OKLCH ``L`` (== OKLab ``L``) for a ``#rrggbb`` colour.

    sRGB -> linear sRGB -> LMS -> OKLab, Björn Ottosson's published
    constants (https://bottosson.github.io/posts/oklab/) — the standard
    matrix pair, arithmetic only, no dependency.
    """
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)

    # Named `lms_*` rather than the paper's `l`/`m`/`s`: a bare `l` is
    # ruff's E741 (ambiguous with `1` and `I` in most fonts).
    lms_l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    lms_m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    lms_s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

    cube_l, cube_m, cube_s = lms_l ** (1 / 3), lms_m ** (1 / 3), lms_s ** (1 / 3)

    return 0.2104542553 * cube_l + 0.7936177850 * cube_m - 0.0040720468 * cube_s


def _load_token_maps() -> dict[str, dict[str, str]]:
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    blocks = ctc.extract_css_blocks(css_text)
    return {mode: ctc.parse_custom_properties(blocks[label]) for mode, label in THEME_BLOCK_LABEL.items()}


def check_lightness_band(token_maps: dict[str, dict[str, str]]) -> list[str]:
    failures: list[str] = []
    for mode, (lo, hi) in LIGHTNESS_BAND.items():
        tokens = token_maps[mode]
        for token in CHROMATIC_STAGE_TOKENS:
            hexval = tokens[token]
            lightness = hex_to_oklch_l(hexval)
            status = "PASS" if lo <= lightness <= hi else "FAIL"
            print(f"  [{mode}] {token:24} {hexval}  OKLCH L={lightness:.4f}  band=[{lo}, {hi}]  {status}")
            if status == "FAIL":
                failures.append(f"{mode}:{token} OKLCH L={lightness:.4f} is outside [{lo}, {hi}]")
    return failures


def check_contrast(token_maps: dict[str, dict[str, str]]) -> list[str]:
    failures: list[str] = []
    for mode in ("dark", "light"):
        tokens = token_maps[mode]
        surface = tokens["--jf-surface"]
        raised = tokens["--jf-raised"]
        for stage_token in ALL_STAGE_TOKENS:
            stage_hex = tokens[stage_token]
            for bg_name, bg_hex in (("--jf-surface", surface), ("--jf-raised", raised)):
                ratio = ctc.contrast(stage_hex, bg_hex)
                status = "PASS" if ratio >= AA_NORMAL else "FAIL"
                print(f"  [{mode}] {stage_token:24} on {bg_name:14} = {ratio:5.2f}  {status}")
                if status == "FAIL":
                    failures.append(f"{mode}:{stage_token} on {bg_name} = {ratio:.2f} (< {AA_NORMAL})")
        for fg_token, bg_token in FIT_PAIRS:
            fg_hex = tokens[fg_token]
            bg_hex = tokens[bg_token]
            ratio = ctc.contrast(fg_hex, bg_hex)
            status = "PASS" if ratio >= AA_NORMAL else "FAIL"
            print(f"  [{mode}] {fg_token:14} on {bg_token:14} = {ratio:5.2f}  {status}")
            if status == "FAIL":
                failures.append(f"{mode}:{fg_token} on {bg_token} = {ratio:.2f} (< {AA_NORMAL})")
    return failures


def main() -> int:
    token_maps = _load_token_maps()

    print("--- Lightness band (the product spec step 3) ---")
    lightness_failures = check_lightness_band(token_maps)
    print()

    print("--- Contrast (the product spec step 4, AA >= 4.5:1) ---")
    contrast_failures = check_contrast(token_maps)
    print()

    all_failures = lightness_failures + contrast_failures
    if all_failures:
        print(f"FAIL: {len(all_failures)} palette health check(s) failed:")
        for failure in all_failures:
            print(f"  - {failure}")
        return 1

    print(
        "PASS: every chromatic stage colour is inside its mode's OKLCH lightness band, "
        "and every stage/fit contrast pair clears AA (>= 4.5:1)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
