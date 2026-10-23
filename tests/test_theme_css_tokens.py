"""Tests for T56: the CSS-token palette, the retargeted contrast gate, and
the missing-token safety net.

The Python-side `Theme` dataclass module and its AST-based coverage
checker are retired by this task — the dataclass that used to be the
canonical palette is gone, and ``frontend/src/theme/tokens.css`` is now
the only source of truth. ``scripts/check_theme_contrast.py`` is
retargeted to parse that CSS directly rather than importing a Python
module, and this file is what pins its behaviour:

1. The retargeted script reports zero AA failures for the new palette
   (``KNOWN_FAILING = 0``), not a tolerated inherited count.
2. The measured ratios for the documented worst pairs match
   ``docs/DESIGN.md`` (dark 6.24, light 4.97) so a future edit that quietly
   drifts a value is caught here, not just by eye.
3. A CSS custom property referenced by the UI with no ``var(--x, fallback)``
   escape hatch, but not defined in a theme block, resolves to nothing and
   the element renders unstyled — the exact silent failure the task ledger's
   DoD requires a test for. Proven first against a synthetic fixture (so
   the test doesn't rot the day someone fixes the real bug it also catches)
   and then against the actual frontend source tree.
4. The retired palette module and its retired coverage checker are both
   actually gone, and nothing outside the task ledger/the parity matrix/the product spec history
   mentions them (note: this test file necessarily builds the search
   pattern for that sweep out of concatenated string fragments, below, so
   that its own source does not trip the sweep it performs).
5. The type-scale half of the contrast fix: secondary/meta text no longer
   ships below 12px (docs/DESIGN.md's type table) — a palette fix alone
   does not close out the 11px grey label that was the worst offender.

T86 note: the 12px ratchet above only ever matched a literal
``font-size: Npx``. T64 converted the whole type scale to
``clamp(min, preferred, max)``, which made the ratchet pass vacuously —
every real declaration became a ``clamp()`` the old regex never looked
inside. The check below now reads the *minimum* (first) argument of a
``clamp()`` and applies the same 12px floor to it, while treating any
``px`` values inside the middle (``calc()``) argument as not a minimum.
A ``clamp()`` whose minimum argument is not in ``px`` (e.g. ``rem``) is
not graded silently — it raises loudly, so nobody adds one believing it
is checked.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = REPO_ROOT / "frontend" / "src" / "theme" / "tokens.css"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_theme_contrast.py"

# Files allowed to keep mentioning the retired Python theme module / gate as
# history (the task ledger's changelog row, the parity matrix's ledger, the product spec's decision
# record) — the task ledger's own DoD text names exactly these three.
ALLOWED_HISTORY_FILES = {"the task ledger", "the parity matrix", "the product spec"}


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


# ── 1 & 2: the retargeted gate itself ─────────────────────────────────────


def test_known_failing_is_zero(ctc):
    assert ctc.KNOWN_FAILING == 0


def test_main_exits_zero(ctc, capsys):
    assert ctc.main() == 0
    out = capsys.readouterr().out
    assert "PASS: every checked" in out


def test_every_pair_is_reported(ctc, capsys):
    """DoD: 'reports every pair measured' — not decoration, so assert the
    report actually names each configured pair, pass or fail."""
    ctc.main()
    out = capsys.readouterr().out
    for fg, bg, _note in ctc.DARK_PAIRS + ctc.LIGHT_PAIRS:
        assert fg in out
        assert bg in out


def test_dark_worst_pair_matches_design_doc(ctc):
    # T78 replaced the T56/docs/DESIGN.md palette (worst pair
    # 6.24) with a newly measured one (worst pair --jf-accent on
    # --jf-surface, 5.63) — still comfortably above the 4.5 AA floor, just a
    # different value than the superseded palette's.
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    blocks = ctc.extract_css_blocks(css_text)
    dark_map = ctc.parse_custom_properties(blocks["explicit_dark"])
    ratios = [ctc.contrast(dark_map[fg], dark_map[bg]) for fg, bg, _ in ctc.DARK_PAIRS]
    assert min(ratios) == pytest.approx(5.63, abs=0.01)
    assert all(r >= 4.5 for r in ratios)


def test_light_worst_pair_matches_design_doc(ctc):
    # Same supersession as the dark test above — T78's light worst pair
    # (--jf-accent on --jf-surface) is 5.14, not docs/DESIGN.md's 4.97.
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    blocks = ctc.extract_css_blocks(css_text)
    light_map = ctc.parse_custom_properties(blocks["explicit_light"])
    ratios = [ctc.contrast(light_map[fg], light_map[bg]) for fg, bg, _ in ctc.LIGHT_PAIRS]
    assert min(ratios) == pytest.approx(5.14, abs=0.01)
    assert all(r >= 4.5 for r in ratios)


# ── 3: the missing-token safety net ───────────────────────────────────────


def test_find_var_references_flags_token_with_no_fallback(ctc, tmp_path):
    (tmp_path / "ghost.css").write_text(
        ".x { color: var(--jf-ghost); background: var(--jf-also-missing, #fff); }",
        encoding="utf-8",
    )
    required, has_fallback = ctc.find_var_references(tmp_path)
    assert "--jf-ghost" in required
    assert "--jf-also-missing" not in required
    assert "--jf-also-missing" in has_fallback


def test_missing_required_tokens_detects_undefined_token(ctc):
    blocks = {"root_default": ".x { --jf-defined: #112233; }"}
    missing = ctc.missing_required_tokens({"--jf-defined", "--jf-ghost"}, blocks)
    assert missing["root_default"] == {"--jf-ghost"}


def test_real_frontend_src_has_no_missing_required_tokens(ctc):
    """Integration form of the DoD test: every token actually referenced in
    the frontend source tree without a fallback must be defined in every
    theme block of the real tokens.css."""
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    blocks = ctc.extract_css_blocks(css_text)
    required, _has_fallback = ctc.find_var_references(FRONTEND_SRC)
    missing = ctc.missing_required_tokens(required, blocks)
    offenders = {label: sorted(names) for label, names in missing.items() if names}
    assert offenders == {}, (
        f"Token(s) referenced with no var(--x, fallback) escape hatch are not defined in every theme block: {offenders}"
    )


# ── 4: the retired Python palette module and its coverage checker are
#      actually gone ───────────────────────────────────────────────────

# Built from fragments rather than written as literal strings so that this
# file's own source never contains the two patterns it searches the repo
# for — otherwise this very test would flag itself as an offending match.
_RETIRED_MODULE_PATH_PARTS = ("theme", ".py")
_RETIRED_SCRIPT_NAME_PARTS = ("check_theme", "_coverage", ".py")
_GREP_PATTERN_PARTS = ("jobfinder", ".theme", "\\|", "check_theme", "_coverage")


def test_retired_theme_module_is_removed():
    assert not (REPO_ROOT / "jobfinder" / "".join(_RETIRED_MODULE_PATH_PARTS)).exists()


def test_retired_coverage_script_is_removed():
    assert not (REPO_ROOT / "scripts" / "".join(_RETIRED_SCRIPT_NAME_PARTS)).exists()


def test_no_references_outside_history():
    result = subprocess.run(
        ["git", "grep", "-n", "".join(_GREP_PATTERN_PARTS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # git grep exits 1 when there are no matches at all, which would also be
    # a pass here, but the module name legitimately appears in the ledger /
    # spec history files; filter those out, per the task ledger's own DoD text.
    offending = [line for line in result.stdout.splitlines() if line.split(":", 1)[0] not in ALLOWED_HISTORY_FILES]
    assert offending == [], f"Unexpected references outside history: {offending}"


# ── 5: secondary text stops at 12px ───────────────────────────────────────
#
# T86: the original regex here only matched a literal ``font-size: Npx``.
# T64 converted the whole type scale to ``clamp(min, preferred, max)``, so
# the ratchet below has to read the *minimum* (first) argument of a
# ``clamp()`` and apply the same 12px floor to it — without mistaking any
# ``px`` value inside the middle (``calc()``) argument for a minimum.

_FONT_SIZE_DECL_START_RE = re.compile(r"font-size:\s*")
_PX_VALUE_RE = re.compile(r"^(\d+(?:\.\d+)?)px$")
_UNITED_VALUE_RE = re.compile(r"^(\d+(?:\.\d+)?)([a-zA-Z%]+)$")
_CLAMP_RE = re.compile(r"^clamp\(\s*(.*)\)\s*$", re.IGNORECASE | re.DOTALL)


def _iter_font_size_declarations(css_text: str):
    """Yield (start_offset, raw_value) for each ``font-size: ...`` value in
    ``css_text``. The declaration's end is found by scanning for the next
    top-level ``;`` or ``}`` so a ``clamp(...)``'s internal commas/parens
    never get mistaken for the end of the declaration."""
    for decl_match in _FONT_SIZE_DECL_START_RE.finditer(css_text):
        start = decl_match.end()
        depth = 0
        i = start
        while i < len(css_text):
            char = css_text[i]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char in ";}" and depth == 0:
                break
            i += 1
        yield start, css_text[start:i].strip()


def _split_top_level_args(text: str) -> list[str]:
    """Split a comma-separated argument list on commas that are not nested
    inside parentheses (e.g. a ``calc(...)`` argument's own commas, if any,
    stay attached to that argument)."""
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            args.append("".join(current))
            current = []
        else:
            current.append(char)
    args.append("".join(current))
    return args


_VAR_REF_WITH_FALLBACK_RE = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*(?:,\s*(.+?)\s*)?\)\s*$", re.IGNORECASE)
_FONT_TOKEN_DECL_RE = re.compile(r"(--jf-font-[a-z0-9-]+)\s*:\s*([^;]+);")


def font_token_floors() -> dict[str, float]:
    """The px floor of every ``--jf-font-*`` custom property.

    The reason this exists, and why T86 was not finished without it:
    **no component in this codebase writes a literal ``clamp()`` in a
    ``font-size``.** T64 moved the whole type scale into custom
    properties, and every component reads ``font-size:
    var(--jf-font-meta)``. So a ratchet that only inspects the text after
    ``font-size:`` — clamp-aware or not — still grades nothing, which is
    the same vacuous pass in a new disguise. Resolving the var is what
    makes the floor actually enforced.

    Where a token is declared in more than one theme block the smallest
    floor wins: the ratchet asks "how small can this ever render", and
    one block declaring 11px is an 11px rule.
    """
    text = TOKENS_CSS.read_text(encoding="utf-8")
    floors: dict[str, float] = {}
    for name, value in _FONT_TOKEN_DECL_RE.findall(text):
        floor = _font_size_floor_px(value, token_floors={})
        if floor is None:
            continue
        floors[name] = min(floor, floors.get(name, floor))
    return floors


def _font_size_floor_px(raw_value: str, token_floors: dict[str, float] | None = None) -> float | None:
    """Return the effective minimum px floor this ``font-size`` value can
    render at, or ``None`` if the value isn't a form this ratchet grades
    (e.g. a bare keyword like ``inherit``).

    For a literal ``Npx`` the floor is ``N``. For ``clamp(min, preferred,
    max)`` the floor is whatever the *first* argument evaluates to — the
    preferred/max arguments are never inspected, so a ``px`` value inside
    a ``calc()`` in the middle argument is never mistaken for a minimum.

    Raises ``ValueError`` if a ``clamp()``'s minimum argument is in a unit
    other than ``px`` (e.g. ``rem``): converting at an assumed root size
    would be a guess, and silently skipping it is exactly how this ratchet
    went blind in the first place, so this fails loudly instead.
    """
    value = raw_value.strip()

    # `font-size: var(--jf-font-meta)` — follow it to the custom property,
    # which is where every real type size in this codebase lives.
    var_match = _VAR_REF_WITH_FALLBACK_RE.match(value)
    if var_match and token_floors is not None:
        name, fallback = var_match.group(1), var_match.group(2)
        if name in token_floors:
            return token_floors[name]
        if fallback:
            return _font_size_floor_px(fallback, token_floors=token_floors)
        raise ValueError(
            f"font-size reads {name!r}, which is not a graded --jf-font-* custom property "
            f"and has no fallback — refusing to pass it silently, which is how this ratchet "
            f"went blind the first time."
        )

    clamp_match = _CLAMP_RE.match(value)
    if clamp_match:
        args = _split_top_level_args(clamp_match.group(1))
        if not args:
            return None
        minimum = args[0].strip()
        px_match = _PX_VALUE_RE.match(minimum)
        if px_match:
            return float(px_match.group(1))
        unit_match = _UNITED_VALUE_RE.match(minimum)
        if unit_match:
            raise ValueError(
                f"clamp() minimum argument {minimum!r} uses unsupported unit "
                f"{unit_match.group(2)!r} — this ratchet only grades px minimums; "
                f"refusing to guess a px-equivalent, and refusing to pass it silently."
            )
        return None

    px_match = _PX_VALUE_RE.match(value)
    if px_match:
        return float(px_match.group(1))
    return None


def _find_font_size_floor_offenders(
    css_text: str,
    label: str = "<fixture>",
    token_floors: dict[str, float] | None = None,
) -> list[str]:
    offenders: list[str] = []
    for start, raw_value in _iter_font_size_declarations(css_text):
        floor = _font_size_floor_px(raw_value, token_floors=token_floors)
        if floor is not None and floor < 12:
            line_no = css_text.count("\n", 0, start) + 1
            offenders.append(f"{label}:{line_no} ({floor}px)")
    return offenders


def test_no_css_font_size_below_12px():
    """Every `font-size:` in the tree, with `var()` resolved."""
    floors = font_token_floors()
    offenders: list[str] = []
    for css_file in FRONTEND_SRC.rglob("*.css"):
        text = css_file.read_text(encoding="utf-8")
        offenders.extend(
            _find_font_size_floor_offenders(text, str(css_file.relative_to(REPO_ROOT)), token_floors=floors)
        )
    assert offenders == [], f"Secondary/meta text below the 12px floor: {offenders}"


def test_the_type_scale_tokens_themselves_respect_the_floor():
    """Grade the custom properties directly, not only their use sites.

    A `--jf-font-*` token lowered below 12px is a 12px-floor violation
    the moment it is declared, whether or not anything reads it yet.
    """
    floors = font_token_floors()
    assert floors, "no --jf-font-* custom properties were found at all — the parser is wrong"
    too_small = {name: floor for name, floor in floors.items() if floor < 12}
    assert too_small == {}, f"type-scale tokens below the 12px floor: {too_small}"


def test_a_var_reference_resolves_to_the_token_floor():
    """The hole T86 was written to close, closed end to end.

    No component in this codebase writes a literal clamp() in a
    font-size — they all read `var(--jf-font-*)`. A ratchet that stops
    at the var reference grades nothing.
    """
    css = ".x { font-size: var(--jf-font-meta); }"
    assert _find_font_size_floor_offenders(css, token_floors={"--jf-font-meta": 11.0}), (
        "a var() pointing at an 11px token minimum must fail the floor"
    )
    assert _find_font_size_floor_offenders(css, token_floors={"--jf-font-meta": 12.0}) == []


def test_a_var_reference_falls_back_when_the_token_is_unknown():
    css = ".x { font-size: var(--jf-font-nope, 11px); }"
    offenders = _find_font_size_floor_offenders(css, token_floors={})
    assert offenders and "11.0px" in offenders[0]


def test_an_unresolvable_var_reference_raises_loudly():
    css = ".x { font-size: var(--jf-font-nope); }"
    with pytest.raises(ValueError, match="not a graded"):
        _find_font_size_floor_offenders(css, token_floors={})


def test_planted_clamp_minimum_below_floor_fails():
    """Regression test for the hole itself: a clamp() whose minimum is
    below 12px must be caught, not waved through because it's a clamp()."""
    css = ".x { font-size: clamp(11px, calc(8px + 1vw), 16px); }"
    offenders = _find_font_size_floor_offenders(css)
    assert offenders, "a clamp(11px, ...) minimum must fail the 12px floor"
    assert "11.0px" in offenders[0]


def test_planted_literal_below_floor_fails():
    """The original literal-px case must still fail."""
    css = ".x { font-size: 10px; }"
    offenders = _find_font_size_floor_offenders(css)
    assert offenders, "a literal 10px declaration must fail the 12px floor"
    assert "10.0px" in offenders[0]


def test_planted_clamp_calc_inner_px_is_not_treated_as_minimum():
    """The subtle half of the bug: the 9.14px inside the calc() preferred
    argument is not the clamp's minimum and must not be read as one."""
    css = ".x { font-size: clamp(12px, calc(9.14px + 0.268vw), 16px); }"
    offenders = _find_font_size_floor_offenders(css)
    assert offenders == []


def test_planted_clamp_spacing_and_no_space_variants_both_parse():
    """Spaced (``clamp( 12px , ... )``) and tight
    (``font-size:clamp(12px,...)``) forms are both read the same way."""
    spaced = ".x { font-size: clamp( 11px , calc(8px + 1vw) , 16px ) ; }"
    tight = ".x{font-size:clamp(11px,calc(8px + 1vw),16px);}"
    assert _find_font_size_floor_offenders(spaced)
    assert _find_font_size_floor_offenders(tight)


def test_planted_clamp_non_px_minimum_raises_loudly():
    """A clamp() minimum in a unit this ratchet doesn't grade (e.g. rem)
    must fail loudly instead of silently passing."""
    css = ".x { font-size: clamp(1rem, calc(1rem + 0.5vw), 2rem); }"
    with pytest.raises(ValueError, match="unsupported unit"):
        _find_font_size_floor_offenders(css)


def test_real_tokens_css_font_size_declarations_pass():
    """The real type scale (clamp()-based since T64) must pass the fixed
    ratchet, not just fixtures."""
    text = TOKENS_CSS.read_text(encoding="utf-8")
    offenders = _find_font_size_floor_offenders(text, "tokens.css")
    assert offenders == [], f"Real tokens.css violates the 12px floor: {offenders}"
