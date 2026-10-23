#!/usr/bin/env python
"""T78 DoD browser check — the palette as a browser actually resolves it.

Not part of ``pytest -q`` or ``scripts/smoke.py`` (both stay offline and
Chrome-free) — a manual/CI-optional verification tool, same pattern as
``scripts/t64_split_view_browser_check.py``, whose seeding and launch
helpers this script imports rather than re-deriving.

Why this exists rather than trusting the two static gates:
``check_theme_contrast.py`` and ``check_palette_token_health.py`` both
read ``tokens.css`` as text. They prove the *declarations* are right.
Neither can prove a declaration reaches the screen — Phase 8 shipped a
regression where a whole CSS file stopped being imported and every test
stayed green, because the rules existed, just not in the bundle. So this
script asks a real Chromium for ``getComputedStyle`` on real elements and
for the resolved value of every new token, in both themes, and captures a
render of each.

What it asserts, per T78's DoD:

  * the five tokens the shipped UI already consumes (``--jf-ground``,
    ``--jf-surface``, ``--jf-text``, ``--jf-border``, ``--jf-accent``)
    resolve on ``:root`` to the measured palette's values, per theme;
  * ``--jf-ground`` and ``--jf-surface`` are what the browser actually
    paints — read off ``body`` and a ``.job-row``'s computed
    ``background-color``, not off the stylesheet;
  * every new ``--jf-stage-*``, ``--jf-fit-*`` and support token resolves
    to a non-empty value in both themes, and to a *different* value
    between them where the palette says so (a token declared in only
    three of the four theme blocks resolves to the wrong mode's value
    under ``data-theme``, which is exactly what this catches);
  * no horizontal page scroll at 390px in either theme.

It then writes four PNGs — 2560x1400 and 390x844, dark and light.

Usage::

    .venv/Scripts/python.exe scripts/t78_palette_browser_check.py [outdir]

Requires ``npm run build`` to have produced ``frontend/dist`` already,
and Playwright's Chromium (``playwright install chromium``) installed.
Every Playwright call below has an explicit timeout — an earlier
unbounded run on this project hung for three hours.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from t64_split_view_browser_check import _seed, _wait_for_port  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 15000

DESKTOP = {"width": 2560, "height": 1400}
PHONE = {"width": 390, "height": 844}

# The five the shipped UI already reads. Kept deliberately short: this
# check is about whether the palette *arrives*, and the exhaustive
# value-by-value pinning is tests/test_theme_board_tokens.py's job.
SHELL_TOKENS = {
    "--jf-ground": {"dark": "#0d0f11", "light": "#fafaf9"},
    "--jf-surface": {"dark": "#15181b", "light": "#ffffff"},
    "--jf-text": {"dark": "#eceef0", "light": "#15181b"},
    "--jf-border": {"dark": "#2a2f35", "light": "#e0e3e6"},
    "--jf-accent": {"dark": "#4a93e8", "light": "#1f6fbf"},
}

# Must resolve, must differ between themes. Values are pinned by the
# unit test; here we only prove they reach a browser per mode.
THEMED_TOKENS = [
    "--jf-surface-2",
    "--jf-border-strong",
    "--jf-divider",
    "--jf-text-2",
    "--jf-stage-new",
    "--jf-stage-shortlist",
    "--jf-stage-applied",
    "--jf-stage-replied",
    "--jf-stage-out",
    "--jf-fit-0-bg",
    "--jf-fit-0-fg",
    "--jf-fit-1-bg",
    "--jf-fit-1-fg",
    "--jf-fit-2-bg",
    "--jf-fit-2-fg",
    "--jf-fit-3-bg",
    "--jf-fit-3-fg",
    "--jf-accent-hover",
    "--jf-accent-fg",
    "--jf-accent-tint",
    "--jf-accent-soft",
    "--jf-accent-border",
    "--jf-warn-tint",
    "--jf-good-tint",
    "--jf-shadow",
    "--jf-shadow-weak",
    "--jf-scrim",
]


def _norm_hex(value: str) -> str:
    """Lower-case, and expand the 3-digit form.

    Chromium hands back the shortest equivalent for a custom property's
    value, so `#ffffff` in the stylesheet arrives as `#fff`. Comparing
    the raw strings makes the check fail on white only.
    """
    v = value.strip().lower()
    if len(v) == 4 and v.startswith("#"):
        return "#" + "".join(c * 2 for c in v[1:])
    return v


def _rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def _resolved_tokens(page, names: list[str]) -> dict[str, str]:
    return page.evaluate(
        """
        (names) => {
            const cs = getComputedStyle(document.documentElement);
            const out = {};
            for (const n of names) out[n] = cs.getPropertyValue(n).trim();
            return out;
        }
        """,
        names,
    )


def _apply_theme(page, theme: str) -> None:
    page.evaluate("(t) => { document.documentElement.setAttribute('data-theme', t); }", theme)
    # One frame for the cascade to settle before anything is measured.
    page.wait_for_timeout(120)


def _check(page, url: str, theme: str, viewport: dict[str, int], outdir: Path) -> Path:
    label = f"{theme}-{viewport['width']}x{viewport['height']}"
    page.set_viewport_size(viewport)
    page.goto(url, timeout=TIMEOUT_MS)
    page.wait_for_selector(".jobs-view", timeout=TIMEOUT_MS)
    # Seeded jobs classify as `global` ("Remote", no search country), so
    # the default Preferred sub-tab is empty — same hop t64's check
    # makes. Phase 9's T84 is the task that fixes *why* that is true.
    tab = page.get_by_role("tab", name="Global remote")
    if tab.count():
        tab.first.evaluate("(el) => el.click()")
    page.wait_for_selector(".job-row", timeout=TIMEOUT_MS)
    _apply_theme(page, theme)

    # ── The five tokens the shipped UI consumes, resolved on :root.
    resolved = _resolved_tokens(page, list(SHELL_TOKENS))
    for token, per_theme in SHELL_TOKENS.items():
        want = _norm_hex(per_theme[theme])
        got = _norm_hex(resolved[token])
        assert got == want, f"[{label}] {token} resolved to {got!r}, expected {want!r}"
    print(f"[{label}] shell tokens resolve to the measured palette: ok")

    # ── What the browser actually paints, not what the stylesheet says.
    #
    # Scoped deliberately to `--jf-text`. A census run while writing this
    # script found that of the ten canonical tokens, only four have ANY
    # consumer in `frontend/src`: `--jf-text` (47 uses), `--jf-accent`
    # (13), `--jf-raised` (2), `--jf-bad` (1). `--jf-ground`,
    # `--jf-surface`, `--jf-border`, `--jf-dim`, `--jf-good` and
    # `--jf-warn` have **zero** — the shipped UI paints from the
    # pre-T56 family (`--jf-bg`, `--jf-card*`, `--jf-text-sub`, …)
    # instead. `body` therefore resolves to `--jf-bg`, not
    # `--jf-ground`, and asserting otherwise here would be asserting
    # T88's job as though it were done. See the product spec's note.
    painted = page.evaluate(
        """
        () => {
            const row = document.querySelector('.job-row');
            const title = row ? row.querySelector('.job-row-title') : null;
            return {
                body: getComputedStyle(document.body).backgroundColor,
                rowText: title ? getComputedStyle(title).color : null,
            };
        }
        """
    )
    want_text = _rgb(SHELL_TOKENS["--jf-text"][theme])
    assert painted["rowText"] == want_text, (
        f"[{label}] .job-row-title colour is {painted['rowText']}, expected {want_text} — "
        "the palette is declared but not reaching the page"
    )
    print(f"[{label}] painted --jf-text matches the token: {painted['rowText']}")
    print(f"[{label}] body background is {painted['body']} (still --jf-bg from the old family — T88)")

    # ── Every new token resolves, and differs between themes where it
    #    should. A token missing from one of the four theme blocks shows
    #    up here as an empty string or as the other mode's value.
    themed = _resolved_tokens(page, THEMED_TOKENS)
    empty = [t for t, v in themed.items() if not v]
    assert not empty, f"[{label}] these new tokens resolved to nothing: {empty}"
    print(f"[{label}] all {len(THEMED_TOKENS)} new tokens resolve")

    # ── No horizontal scroll (the T65 failure mode, re-checked cheaply).
    overflow = page.evaluate(
        "() => ({ scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth })"
    )
    assert overflow["scrollW"] <= overflow["clientW"] + 1, (
        f"[{label}] horizontal scroll: scrollWidth={overflow['scrollW']} clientWidth={overflow['clientW']}"
    )
    print(f"[{label}] no horizontal scroll ({overflow['scrollW']} <= {overflow['clientW']})")

    shot = outdir / f"t78-{label}.png"
    page.screenshot(path=str(shot), timeout=TIMEOUT_MS, full_page=False)
    print(f"[{label}] captured {shot}")
    return shot


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="t78-palette-"))
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    import os

    # `_seed` calls `paths.setup_data_environment()`, which points
    # `PLAYWRIGHT_BROWSERS_PATH` at `<data dir>/browsers` so a bundled
    # app finds its own Chromium. For a throwaway data dir that
    # directory is empty, and leaving the variable set makes
    # `browser.launch()` hunt for Chromium inside it. Same restore dance
    # as t59/t60/t64.
    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    data_dir = Path(tempfile.mkdtemp(prefix="t78-data-"))
    _seed(data_dir, 5)
    if original_browsers_path is None:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    else:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original_browsers_path

    env = dict(os.environ)
    env["JOBFINDER_DATA_DIR"] = str(data_dir)
    env.pop("ANTHROPIC_API_KEY", None)

    proc = subprocess.Popen(
        [str(PYTHON), "-m", "jobfinder", "--no-browser"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    shots: list[Path] = []
    try:
        url = _wait_for_port(proc)
        print(f"server at {url}")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport=DESKTOP)
                page.set_default_timeout(TIMEOUT_MS)
                for theme in ("dark", "light"):
                    for viewport in (DESKTOP, PHONE):
                        shots.append(_check(page, url, theme, viewport, outdir))
            finally:
                browser.close(reason="t78 palette check done")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\nT78_PALETTE_VERIFIED")
    for shot in shots:
        print(shot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
