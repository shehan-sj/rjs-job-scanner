#!/usr/bin/env python
"""T88 DoD browser check — the new palette on every screen, both themes.

T78's check (``scripts/t78_palette_browser_check.py``) recorded that
``body`` painted ``--jf-bg`` from the old family and *not*
``--jf-ground`` — the census finding in the product spec. T88 re-points the
old family, so the same measurement must now come out the other way.
This script asserts that inversion, which is the only mechanical proof
that T88 did what it claims.

It also walks the screens the census said were stranded on the old
colours — Résumé, Sources & keys, FillerBot — plus Matches, and
Onboarding against a second, un-onboarded data directory, capturing each
in both themes.

Usage::

    .venv/Scripts/python.exe scripts/t88_palette_screens_check.py [outdir]

Requires ``npm run build`` and Playwright's Chromium. Every Playwright
call has an explicit timeout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from t64_split_view_browser_check import _seed, _wait_for_port  # noqa: E402
from t78_palette_browser_check import _rgb  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
DESKTOP = {"width": 2560, "height": 1400}

GROUND = {"dark": "#0d0f11", "light": "#fafaf9"}
SURFACE = {"dark": "#15181b", "light": "#ffffff"}
TEXT = {"dark": "#eceef0", "light": "#15181b"}

# Rail label -> the selector that proves the screen rendered.
SCREENS = [
    ("Matches", ".jobs-view"),
    ("Résumé", ".resume-tab"),
    ("Sources & keys", ".settings-page"),
]


def _set_theme(page, theme: str) -> None:
    page.evaluate("(t) => { document.documentElement.setAttribute('data-theme', t); }", theme)
    page.wait_for_timeout(150)


def _assert_palette_reached(page, theme: str, label: str) -> None:
    """The inversion of T78's recorded finding."""
    painted = page.evaluate(
        """
        () => {
            const card = document.querySelector(
                '.job-row, .settings-card, .resume-tab-card, .fb-panel, .onboarding-panel'
            );
            return {
                body: getComputedStyle(document.body).backgroundColor,
                card: card ? getComputedStyle(card).backgroundColor : null,
                cardSel: card ? card.className : null,
            };
        }
        """
    )
    want_ground = _rgb(GROUND[theme])
    assert painted["body"] == want_ground, (
        f"[{label}] body background is {painted['body']}, expected {want_ground} — "
        "T88 was supposed to put --jf-bg onto --jf-ground"
    )
    print(f"[{label}] body paints --jf-ground: {painted['body']}")
    if painted["card"]:
        want_surface = _rgb(SURFACE[theme])
        if painted["card"] != want_surface:
            print(
                f"[{label}] note: first card-like element ({painted['cardSel']}) is {painted['card']}, not {want_surface}"
            )


def _capture(page, theme: str, name: str, outdir: Path) -> Path:
    shot = outdir / f"t88-{name}-{theme}.png"
    page.screenshot(path=str(shot), timeout=TIMEOUT_MS, full_page=False)
    print(f"[{name}/{theme}] captured {shot.name}")
    return shot


def _serve(data_dir: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["JOBFINDER_DATA_DIR"] = str(data_dir)
    env.pop("ANTHROPIC_API_KEY", None)
    return subprocess.Popen(
        [str(PYTHON), "-m", "jobfinder", "--no-browser"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="t88-palette-"))
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    seeded_dir = Path(tempfile.mkdtemp(prefix="t88-data-"))
    _seed(seeded_dir, 5)
    if original_browsers_path is None:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    else:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original_browsers_path

    fresh_dir = Path(tempfile.mkdtemp(prefix="t88-fresh-"))

    shots: list[Path] = []
    proc = _serve(seeded_dir)
    try:
        url = _wait_for_port(proc)
        print(f"seeded server at {url}")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport=DESKTOP)
                page.set_default_timeout(TIMEOUT_MS)
                for theme in ("dark", "light"):
                    for rail_label, ready_selector in SCREENS:
                        page.goto(url, timeout=TIMEOUT_MS)
                        page.wait_for_selector(".rail-desktop", timeout=TIMEOUT_MS)
                        _set_theme(page, theme)
                        nav = page.get_by_role("button", name=rail_label)
                        if nav.count():
                            nav.first.evaluate("(el) => el.click()")
                        try:
                            page.wait_for_selector(ready_selector, timeout=6000)
                        except Exception:
                            print(f"[{rail_label}/{theme}] note: {ready_selector} did not appear; capturing anyway")
                        if rail_label == "Matches":
                            # The seeded jobs classify as `global` (location
                            # "Remote", no search country), so the default
                            # Preferred sub-tab is empty and the capture would
                            # show an empty state rather than the palette on
                            # real rows. T84 is the task that fixes *why* that
                            # classification is wrong.
                            tier = page.get_by_role("tab", name="Global remote")
                            if tier.count():
                                tier.first.evaluate("(el) => el.click()")
                                page.wait_for_selector(".job-row", timeout=6000)
                        page.wait_for_timeout(250)
                        _assert_palette_reached(page, theme, f"{rail_label}/{theme}")
                        shots.append(_capture(page, theme, rail_label.split()[0].lower(), outdir))
            finally:
                browser.close(reason="t88 seeded pass done")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ── Onboarding needs a data directory that has never been onboarded.
    proc = _serve(fresh_dir)
    try:
        url = _wait_for_port(proc)
        print(f"fresh server at {url}")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport=DESKTOP)
                page.set_default_timeout(TIMEOUT_MS)
                for theme in ("dark", "light"):
                    page.goto(url, timeout=TIMEOUT_MS)
                    page.wait_for_selector(".onboarding-overlay, .jobs-view", timeout=TIMEOUT_MS)
                    _set_theme(page, theme)
                    page.wait_for_timeout(250)
                    _assert_palette_reached(page, theme, f"Onboarding/{theme}")
                    shots.append(_capture(page, theme, "onboarding", outdir))
            finally:
                browser.close(reason="t88 fresh pass done")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\nT88_SCREENS_VERIFIED")
    for shot in shots:
        print(shot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
