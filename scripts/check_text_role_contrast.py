#!/usr/bin/env python
"""Every token used as text must be legible as text.

Why this exists, specifically:

T88 re-pointed ``--jf-score-strong/good/fair/weak`` onto the fit ramp's
*background* steps. Ten component rules use those tokens as a text
``color:`` — log warnings and errors, three onboarding messages,
FillerBot status, JobCardView, SettingsView — and a fill designed to sit
*behind* text measures **1.17–1.75:1** when used *as* text. Ten rules
would have shipped invisible, and every gate was green:
``check_theme_contrast.py`` grades a fixed list of pairs and knew
nothing about ``--jf-score-*``; ``check_token_consumers.py`` only asks
whether a token is consumed, not how.

So this gate asks the question the others cannot: *for every token this
codebase actually uses as a foreground, is it legible on the surfaces it
can sit on?* It derives its list from the CSS rather than from a
hand-written table, so a new ``color: var(--jf-…)`` is covered the day
it is written.

The surfaces are ``--jf-surface`` (cards, panels) and ``--jf-raised``
(hover, menus, inputs) — an approximation, deliberately. A token sitting
on something else entirely is possible; what matters is that no token
used as text is anywhere near the floor, and a value that clears both of
these is not going to be invisible on a third.

T99 adds a new surface this approximation does not cover: the board
column header's tinted band (``--jf-stage-*-band``, tokens.css), which
carries the existing ``--jf-text``/``--jf-dim`` header text. Grading
those two tokens against only the fixed ``--jf-surface``/``--jf-raised``
pair would leave the band ungraded — a check that passes because it
never reaches the thing it names, the exact failure mode the product spec
already lists six cases of; this would be a seventh.

The extension is deliberately *scoped*, not blanket: folding every
``--jf-*-band`` token into ``SURFACE_TOKENS`` for every ordinary-text
token project-wide would grade e.g. ``--jf-warn`` (used for the tier
filter's "unconfigured" chip, nowhere near the header band) against a
surface it never actually sits on, and fail on a coincidence rather
than a real defect. ``band_consumer_tokens`` instead finds which
selectors actually declare a ``background`` pointed at a ``-band``
token, then — using this codebase's own block-element class naming
(``.board-column-header`` / ``.board-column-label`` share the
``board-column`` block) — collects the text tokens used by *that*
component family only. A future ``--jf-*-band`` consumer is picked up
the same way, by its own CSS, not by a hand-maintained list of
component names.

Usage::

    .venv/Scripts/python.exe scripts/check_text_role_contrast.py
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = _REPO_ROOT / "frontend" / "src" / "theme" / "tokens.css"
FRONTEND_SRC = _REPO_ROOT / "frontend" / "src"

AA_NORMAL = 4.5
SURFACE_TOKENS = ("--jf-surface", "--jf-raised")

# Any token matching this pattern is a self-contained "band" surface —
# a tint pre-blended to a literal hex (T99's module docstring note above
# on why it must be a literal, not an `rgba()`) that ordinary page text
# can end up sitting on. Discovered by name rather than hand-listed, so
# a future `--jf-*-band` token is picked up without touching this file.
_BAND_TOKEN_RE = re.compile(r"--jf-[a-z0-9-]+-band")

# `color: var(--jf-x)`, `color: var(--jf-x, var(--jf-y))`, and the
# `-webkit-text-fill-color` / `caret-color` variants, but not
# `background-color` or `border-color` (those are fills, judged against
# whatever sits on them, which is a different question).
_TEXT_DECL_RE = re.compile(
    r"(?<![-\w])(?:color|caret-color|-webkit-text-fill-color)\s*:\s*([^;]+);",
    re.IGNORECASE,
)
_VAR_RE = re.compile(r"var\(\s*(--jf-[a-z0-9-]+)")

# A loose, non-nesting-aware (selector, body) splitter — good enough for
# this codebase's CSS, same caveat `check_theme_contrast.extract_css_blocks`
# already states about its own shape-specific parsing.
_SELECTOR_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_CLASS_TOKEN_RE = re.compile(r"\.([a-zA-Z0-9-]+)")
_BAND_BG_DECL_RE = re.compile(r"background(?:-color)?\s*:\s*var\(\s*--jf-[a-z0-9-]+-band\s*\)")


def _load_check_theme_contrast():
    spec = importlib.util.spec_from_file_location("ctc", _REPO_ROOT / "scripts" / "check_theme_contrast.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ctc"] = module
    spec.loader.exec_module(module)
    return module


ctc = _load_check_theme_contrast()


_DECL_RE = re.compile(r"(--jf-[a-z0-9-]+)\s*:\s*([^;]+);")


def all_declarations(block_text: str) -> dict[str, str]:
    """Every custom property in a theme block, aliases included.

    `check_theme_contrast.parse_custom_properties` keeps only the
    hex-valued ones — right for its job, useless for following
    `--jf-score-strong: var(--jf-good)` to a colour, which is the whole
    point here.
    """
    return {name: value.strip() for name, value in _DECL_RE.findall(block_text)}


def band_surfaces_in(token_map: dict[str, str]) -> tuple[str, ...]:
    """Every `--jf-*-band` token declared in this theme block (T99).

    Discovered by name, not hand-listed, matching the rest of this
    module's "derive the list from the CSS" approach — see the module
    docstring's T99 paragraph for why a band surface cannot be left out
    of `backgrounds_for`'s reach the way `--jf-score-*` originally was.
    """
    return tuple(sorted(name for name in token_map if _BAND_TOKEN_RE.fullmatch(name)))


def _rightmost_class(selector: str) -> str | None:
    """The last `.foo` class token in a (possibly compound/descendant)
    selector — e.g. `.board-column[data-stage="new"] .board-column-header`
    -> `board-column-header`. Good enough for this codebase's shape: no
    rule here targets the band-bearing element through anything other
    than a plain trailing class."""
    classes = _CLASS_TOKEN_RE.findall(selector)
    return classes[-1] if classes else None


def band_consumer_tokens() -> set[str]:
    """Every text-colour token actually painted onto a `--jf-*-band`
    surface (T99), scoped to the component family that declares the
    band in the first place.

    Finds every selector whose block sets `background`/`background-
    color` to a `-band` token, takes its rightmost class's block-element
    prefix (`board-column-header` -> `board-column`), then collects
    `color:`-family tokens from every selector — in any CSS file — whose
    rightmost class is that same prefix or starts with `{prefix}-`. This
    is what keeps `--jf-warn` (the tier filter's unrelated "unconfigured"
    chip) and `--jf-accent` (the undo toast's button, same file, same
    file-wide grep a naive version of this would have used) out of the
    band's reach — they are never actually painted on it.
    """
    all_blocks: list[tuple[str, str]] = []
    band_prefixes: set[str] = set()

    for path in sorted(FRONTEND_SRC.rglob("*.css")):
        if path.resolve() == TOKENS_CSS.resolve():
            continue
        text = path.read_text(encoding="utf-8")
        for selector, body in _SELECTOR_BLOCK_RE.findall(text):
            all_blocks.append((selector, body))
            if _BAND_BG_DECL_RE.search(body):
                cls = _rightmost_class(selector)
                if cls and "-" in cls:
                    band_prefixes.add(cls.rsplit("-", 1)[0])

    if not band_prefixes:
        return set()

    tokens: set[str] = set()
    for selector, body in all_blocks:
        cls = _rightmost_class(selector)
        if not cls:
            continue
        if cls in band_prefixes or any(cls.startswith(prefix + "-") for prefix in band_prefixes):
            for decl in _TEXT_DECL_RE.findall(body):
                tokens.update(_VAR_RE.findall(decl))
    return tokens


def backgrounds_for(token: str, token_map: dict[str, str], band_consumers: set[str] = frozenset()) -> tuple[str, ...]:
    """Which backgrounds this foreground is actually judged against.

    A `*-fg` token sits on its own fill, never on the page surface:
    `--jf-btn-primary-fg` is the text *inside* the primary button, so
    grading it against `--jf-surface` would report white-on-white and be
    wrong every time. Pair it with its fill instead — `--jf-btn-primary`,
    or `--jf-tag-warn-bg` for the `-bg`/`-fg` chip convention.

    Everything else is ordinary text on a panel, and gets the two fixed
    surfaces, plus every `--jf-*-band` token this theme block declares
    (T99) *if* `token` is one `band_consumer_tokens` found actually
    painted on a band — not a blanket grading of every unrelated token
    against a surface it never touches (module docstring's T99
    paragraph on why that would be the wrong kind of "thorough").
    """
    if token.endswith("-fg"):
        stem = token[: -len("-fg")]
        for candidate in (stem, f"{stem}-bg"):
            if candidate in token_map:
                return (candidate,)
    extra = band_surfaces_in(token_map) if token in band_consumers else ()
    return SURFACE_TOKENS + extra


def tokens_used_as_text() -> dict[str, list[str]]:
    """Map each token used as a foreground to the files that do it."""
    found: dict[str, list[str]] = {}
    for path in sorted(FRONTEND_SRC.rglob("*.css")):
        if path.resolve() == TOKENS_CSS.resolve():
            continue
        text = path.read_text(encoding="utf-8")
        for decl in _TEXT_DECL_RE.findall(text):
            for token in _VAR_RE.findall(decl):
                found.setdefault(token, []).append(str(path.relative_to(_REPO_ROOT)))
    return found


def resolve(token: str, token_map: dict[str, str], _seen: set[str] | None = None) -> str | None:
    """Follow `--a: var(--b)` to a literal value, or None if it is not a hex."""
    seen = _seen or set()
    if token in seen or token not in token_map:
        return None
    seen.add(token)
    value = token_map[token].strip()
    match = _VAR_RE.match(value)
    if match:
        return resolve(match.group(1), token_map, seen)
    return value if value.startswith("#") else None


def main() -> int:
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    blocks = ctc.extract_css_blocks(css_text)
    modes = {"dark": "explicit_dark", "light": "explicit_light"}

    used = tokens_used_as_text()
    print(f"{len(used)} tokens are used as a text colour somewhere under frontend/src\n")

    band_consumers = band_consumer_tokens()
    if band_consumers:
        print(f"Band-surface text tokens (T99): {sorted(band_consumers)}\n")

    failures: list[str] = []
    skipped: list[str] = []

    for mode, label in modes.items():
        token_map = all_declarations(blocks[label])
        for token in sorted(used):
            fg = resolve(token, token_map)
            if fg is None:
                if token not in skipped:
                    skipped.append(token)
                continue
            for surface_token in backgrounds_for(token, token_map, band_consumers):
                bg = resolve(surface_token, token_map)
                if bg is None:
                    continue
                ratio = ctc.contrast(fg, bg)
                ok = ratio >= AA_NORMAL
                status = "PASS" if ok else "FAIL"
                print(f"  [{mode}] {token:26} {fg}  on {surface_token:14} = {ratio:5.2f}  {status}")
                if not ok:
                    where = ", ".join(sorted(set(used[token]))[:3])
                    failures.append(
                        f"[{mode}] {token} ({fg}) on {surface_token} = {ratio:.2f} — used as text in {where}"
                    )
        print()

    if skipped:
        print("Not graded (non-hex value, e.g. an rgba tint or a keyword):")
        for token in skipped:
            print(f"  {token}")
        print()

    if failures:
        print(f"FAIL: {len(failures)} token/surface pairs are used as text but are not legible as text:")
        for line in failures:
            print(f"  {line}")
        print(
            "\nA token used as a foreground has to clear AA as a foreground. If this is a\n"
            "fill that was never meant to be text, the component is wrong; if the component\n"
            "is right, the token is pointed at the wrong value."
        )
        return 1

    print(f"PASS: every token used as text clears AA ({AA_NORMAL}:1) on both surfaces, in both modes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
