#!/usr/bin/env python
"""Token-consumer gate for T88 ("Found while implementing T78").

`check_theme_contrast.py` grades a fixed list of (foreground, background)
token pairs (`DARK_PAIRS` / `LIGHT_PAIRS`) for WCAG AA contrast. Grading a
token says nothing about whether that token is actually rendered: a
census taken while scoping T88 found that **six of the ten canonical
tokens had zero `var(--jf-...)` consumers anywhere in `frontend/src`** —
`--jf-ground`, `--jf-surface`, `--jf-border`, `--jf-dim`, `--jf-good` and
`--jf-warn` were declared and graded, but painted nothing. The contrast
gate was passing on colours nobody could see.

This script is the "is it actually consumed" half of that problem: for
every token named in `check_theme_contrast.py`'s `DARK_PAIRS` /
`LIGHT_PAIRS`, assert it is consumed by some real `.css`/`.tsx`/`.ts`
declaration in `frontend/src`, other than `tokens.css` itself.

A token counts as consumed two ways:

1. **Direct** — some non-`tokens.css` file contains a literal
   `var(--token)` reference (a component reading the token straight off
   the canonical ten).
2. **Transitive, via `tokens.css`'s own alias chain** — T88's whole
   mechanism is re-pointing the pre-T56 token family at the canonical ten
   *inside* `tokens.css` (e.g. `--jf-bg: var(--jf-ground);`), so a
   canonical token with no direct consumer can still be genuinely
   rendered if some *other* token that aliases to it (directly, or
   through a chain of aliases) is itself consumed by a real component
   file. `tokens.css` itself is never treated as a consumer on its own —
   an alias chain that dead-ends inside `tokens.css` without reaching a
   real file does not count.

This script is expected to fail before T88's re-pointing lands — that is
the point: it is how the ungraded-but-invisible tokens above were found
in the first place. The fix is making each flagged token consumed (T88
re-points the pre-T56 token family at these tokens via `var()`
reference), never deleting the token from `check_theme_contrast.py`'s
pair list to dodge the failure.

Usage:
    .venv/Scripts/python.exe scripts/check_token_consumers.py
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = _REPO_ROOT / "frontend" / "src" / "theme" / "tokens.css"
FRONTEND_SRC = _REPO_ROOT / "frontend" / "src"
_CHECK_THEME_CONTRAST_PATH = _REPO_ROOT / "scripts" / "check_theme_contrast.py"

# `--jf-some-token: var(--jf-other-token);` (optionally with a fallback,
# which this gate still treats as an alias edge — the fallback only
# matters if `--jf-other-token` were ever undefined, which the
# missing-token safety net in check_theme_contrast.py already covers).
_ALIAS_DECL_RE = re.compile(r"(--jf-[a-z0-9-]+)\s*:\s*var\(\s*(--jf-[a-z0-9-]+)\s*(?:,[^)]*)?\)\s*;")
_VAR_REF_RE = re.compile(r"var\(\s*(--jf-[a-z0-9-]+)\s*(?:,[^)]*)?\)")


def _load_check_theme_contrast():
    spec = importlib.util.spec_from_file_location("check_theme_contrast", _CHECK_THEME_CONTRAST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


ctc = _load_check_theme_contrast()


def graded_tokens() -> set[str]:
    """Every token named (as either the fg or bg half of a pair) in
    `check_theme_contrast.py`'s `DARK_PAIRS` / `LIGHT_PAIRS`."""
    names: set[str] = set()
    for fg, bg, _usage in ctc.DARK_PAIRS + ctc.LIGHT_PAIRS:
        names.add(fg)
        names.add(bg)
    return names


def direct_consumers() -> set[str]:
    """Every token with at least one literal `var(--token)` reference in a
    real `.css`/`.tsx`/`.ts` file under FRONTEND_SRC, other than
    `tokens.css` itself."""
    consumed: set[str] = set()
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in (".css", ".tsx", ".ts") or not path.is_file():
            continue
        if path == TOKENS_CSS:
            continue
        text = path.read_text(encoding="utf-8")
        consumed.update(_VAR_REF_RE.findall(text))
    return consumed


def alias_edges() -> dict[str, str]:
    """{alias_name: target_name} parsed from tokens.css's own
    `--jf-x: var(--jf-y);` declarations, across every theme block (the
    mapping is theme-independent by construction — one `var()` reference
    per property name, see tokens.css's own T88 comment)."""
    text = TOKENS_CSS.read_text(encoding="utf-8")
    edges: dict[str, str] = {}
    for alias_name, target_name in _ALIAS_DECL_RE.findall(text):
        edges[alias_name] = target_name
    return edges


def transitively_consumed(direct: set[str], edges: dict[str, str]) -> set[str]:
    """Every token reachable by following `edges` forward from `direct`:
    if `--jf-bg` is directly consumed and `--jf-bg: var(--jf-ground)`,
    then `--jf-ground` is transitively consumed too (and so on, through
    however many alias hops it takes)."""
    consumed = set(direct)
    frontier = set(direct)
    while frontier:
        next_frontier: set[str] = set()
        for token in frontier:
            target = edges.get(token)
            if target is not None and target not in consumed:
                consumed.add(target)
                next_frontier.add(target)
        frontier = next_frontier
    return consumed


def main() -> int:
    tokens = sorted(graded_tokens())
    direct = direct_consumers()
    edges = alias_edges()
    consumed = transitively_consumed(direct, edges)

    print(f"Checking {len(tokens)} token(s) graded by check_theme_contrast.py for real consumers...")

    offenders: list[str] = []
    for token in tokens:
        if token in consumed:
            how = "direct" if token in direct else "transitive (via tokens.css alias)"
            print(f"  {token:16} PASS ({how})")
        else:
            print(f"  {token:16} FAIL (no consumer)")
            offenders.append(token)

    print()
    if offenders:
        print(
            f"FAIL: {len(offenders)} graded token(s) have zero consumers (direct or via a "
            f"tokens.css alias) under frontend/src: {offenders}\n"
            "Fix by making the token consumed (point a real declaration at it via "
            "var(), directly or by aliasing an existing consumed token), not by "
            "removing it from check_theme_contrast.py's pair list."
        )
        return 1

    print("PASS: every token graded by check_theme_contrast.py has at least one real consumer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
