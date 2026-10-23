#!/usr/bin/env python
"""Approved-hex gate for T88 ("Found while implementing T78").

Before T88, `tokens.css` held two palettes: the canonical ten (plus T78's
shell/stage/fit/support additions), and a pre-T56 token family of 40
properties that still carried its own literal hex values instead of
pointing at the new palette. T88 re-points every one of those 40 at the
canonical/shell/support tokens by `var()` reference, so after this task
no old-family property should declare a literal hex at all.

This script is the gate that keeps it that way: every `--jf-*: #hex;`
declaration in `tokens.css` must name a token in `APPROVED_HEX_TOKENS`
below. A property not in that set is, by definition, either part of the
retired old family re-introducing a literal hex (a regression this gate
exists to catch) or a genuinely new token nobody added to the approved
list — either way, a human needs to look at it before it ships.

`APPROVED_HEX_TOKENS` is the canonical ten, T78's shell/stage/fit/support
additions, and T88's one new token, `--jf-bad-hover`. `rgba(...)`
declarations (the soft/tint/shadow/scrim support tokens) are untouched by
this check — they are not hex literals, and T78 already owns their
shape.

Usage:
    .venv/Scripts/python.exe scripts/check_approved_hex_palette.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = _REPO_ROOT / "frontend" / "src" / "theme" / "tokens.css"

# Canonical ten (T56 / docs/DESIGN.md).
_CANONICAL = {
    "--jf-ground",
    "--jf-surface",
    "--jf-raised",
    "--jf-border",
    "--jf-text",
    "--jf-dim",
    "--jf-accent",
    "--jf-good",
    "--jf-warn",
    "--jf-bad",
}

# Shell additions (T78).
_SHELL = {
    "--jf-surface-2",
    "--jf-border-strong",
    "--jf-divider",
    "--jf-text-2",
}

# Pipeline stage colours (T78).
_STAGE = {
    "--jf-stage-new",
    "--jf-stage-shortlist",
    "--jf-stage-applied",
    "--jf-stage-replied",
    "--jf-stage-out",
}

# Fit ramp — four neutral steps, bg and fg each (T78).
_FIT = {f"--jf-fit-{n}-{half}" for n in range(4) for half in ("bg", "fg")}

# Support tokens that are literal hex (T78) — the rgba(...)-valued support
# tokens (accent-tint/soft/border, warn-tint, good-tint, shadow,
# shadow-weak, scrim) are not hex literals and so are never found by this
# script's regex in the first place; they do not need to be listed here.
_SUPPORT_HEX = {
    "--jf-accent-hover",
    "--jf-accent-fg",
}

# T88's one new token: the pre-T56 family had a hover step for danger
# buttons (`--jf-btn-danger-h`) that the canonical ten has no equivalent
# of, so T88 adds a dedicated hover step for `--jf-bad` rather than
# inventing a value by eye.
_T88_NEW = {
    "--jf-bad-hover",
}

APPROVED_HEX_TOKENS = _CANONICAL | _SHELL | _STAGE | _FIT | _SUPPORT_HEX | _T88_NEW

_HEX_DECL_RE = re.compile(r"^\s*(--jf-[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", re.MULTILINE)


def find_hex_declarations(css_text: str) -> list[tuple[str, str, int]]:
    """Every `(token, hex_value, line_number)` for a literal hex
    declaration anywhere in *css_text* (every theme block included —
    this is intentionally not scoped to one block, since the whole point
    is that no old-family token should carry a hex in *any* of the four
    blocks)."""
    out: list[tuple[str, str, int]] = []
    for match in _HEX_DECL_RE.finditer(css_text):
        token, value = match.group(1), match.group(2)
        line_no = css_text.count("\n", 0, match.start()) + 1
        out.append((token, value, line_no))
    return out


def main() -> int:
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    declarations = find_hex_declarations(css_text)

    offenders = [(token, value, line_no) for token, value, line_no in declarations if token not in APPROVED_HEX_TOKENS]

    print(
        f"Checked {len(declarations)} literal hex declaration(s) in tokens.css against {len(APPROVED_HEX_TOKENS)} approved token(s)."
    )

    if offenders:
        print(f"\nFAIL: {len(offenders)} literal hex declaration(s) outside the approved set:")
        for token, value, line_no in offenders:
            print(f"  tokens.css:{line_no}  {token}: {value};")
        print(
            "\nEvery pre-T56 token family property must resolve to a var() reference "
            "onto an approved token (T88), not a literal hex of its own. If this is a "
            "genuinely new token, add it to APPROVED_HEX_TOKENS in this script "
            "deliberately, not as a side effect of fixing this failure."
        )
        return 1

    print("PASS: every literal hex declaration in tokens.css names an approved token.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
