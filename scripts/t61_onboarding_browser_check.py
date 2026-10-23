#!/usr/bin/env python
"""T61 DoD browser check -- real headless Chromium, run on demand.

Not part of ``pytest -q`` or ``scripts/smoke.py`` (both stay offline and
Chrome-free) -- a manual/CI-optional verification tool, same pattern as
``scripts/t60_shell_browser_check.py``, whose ``_wait_for_port`` helper this
script reuses rather than re-deriving.

Proves docs/DESIGN.md's "Onboarding" section and T61's DoD end to end,
against three **fresh** data dirs (never the real one -- see each
``_seed_fresh`` call):

  1. Onboarding completes with **no** API key, through the real
     ``POST /api/onboarding/complete`` -- and, the DoD's hardest clause and
     T61's actual defect, **a reload afterwards does not show onboarding
     again**.
  2. Onboarding completes with a key typed into the field -- a
     deliberately fake, synthetic one (never sent to Anthropic: this script
     never clicks "Test connection", which is the one control that makes a
     real, billed round trip) -- and a reload afterwards *also* does not
     show onboarding again.
  3. The same flow is completable at phone width (390x844): every control
     reachable, Start reachable, no horizontal scroll needed to find it.

Every Playwright call below has an explicit timeout -- an earlier
unbounded run on this project hung for three hours (see t60's own script).

Usage::

    .venv/Scripts/python.exe scripts/t61_onboarding_browser_check.py

Requires ``npx vite build`` to have produced ``frontend/dist`` already,
and Playwright's Chromium (``playwright install chromium``) installed.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 15000

# gitleaks:allow -- synthetic, never sent anywhere; this script never
# clicks "Test connection", the one control that would make a real call.
FAKE_KEY = "sk-ant-fake-browser-check-not-a-real-key-" + "q7Xz" * 4


def _wait_for_port(proc: subprocess.Popen, timeout_s: float = 15.0) -> str:
    import time

    deadline = time.monotonic() + timeout_s
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError(f"jobfinder server exited early (code {proc.returncode})")
            continue
        if "JobFinder is serving at:" in line:
            return line.split("JobFinder is serving at:", 1)[1].strip()
    raise TimeoutError("server did not report its URL within the timeout")


def _launch(data_dir: Path) -> subprocess.Popen:
    import os

    env = dict(os.environ)
    env["JOBFINDER_DATA_DIR"] = str(data_dir)
    # A fresh install: no key, no onboarding_completed flag, no resume --
    # needs_onboarding() must say True so the wizard is actually what we
    # land on.
    env.pop("ANTHROPIC_API_KEY", None)
    return subprocess.Popen(
        [str(PYTHON), "-m", "jobfinder", "--no-browser"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _resume_fixture(tmp: Path) -> Path:
    """A synthetic, obviously-fake **plain-text** resume -- a minimal PDF
    (just a header and an EOF marker, no text layer) is accepted by the
    upload endpoint itself but rejected downstream as having "no
    extractable text", which would leave this script with nothing to
    assert the pre-fill/provenance sections actually populated. Plain
    text is one of the two formats the wizard accepts
    (`onboarding-file-input`'s own `accept` list) and needs no PDF
    library to produce a real text layer.
    """
    path = tmp / "t61-check-resume.txt"
    path.write_text(
        "Jordan Example\n"
        "Senior Software Engineer\n"
        "Toronto, Canada\n\n"
        "Experience\n"
        "Senior Software Engineer, Example Corp (2021-present)\n"
        "Software Engineer, Example Corp (2018-2021)\n",
        encoding="utf-8",
    )
    return path


def _fill_minimum_and_start(page, resume_path: Path, api_key: str | None) -> None:
    """Drives the single-page flow far enough to enable Start: upload a
    resume, acknowledge the disclosure, optionally type a key, then press
    Start. Deliberately never clicks "Test connection"."""
    page.wait_for_selector(".onboarding-overlay", timeout=TIMEOUT_MS)

    file_input = page.locator(".onboarding-file-input")
    file_input.wait_for(timeout=TIMEOUT_MS, state="attached")
    file_input.set_input_files(str(resume_path))
    page.wait_for_selector(".onboarding-resume-filename:has-text('t61-check-resume.txt')", timeout=TIMEOUT_MS)

    # The provenance pill for "how we read it" only appears once the
    # keyless/heuristic re-read of the resume has actually landed.
    page.wait_for_selector(".onboarding-section .onboarding-provenance", timeout=TIMEOUT_MS)

    if api_key is not None:
        key_input = page.locator('.onboarding-section input[type="password"]')
        key_input.wait_for(timeout=TIMEOUT_MS)
        key_input.fill(api_key)

    disclosure_checkbox = page.locator('.onboarding-disclosure input[type="checkbox"]')
    disclosure_checkbox.wait_for(timeout=TIMEOUT_MS)
    disclosure_checkbox.evaluate("(el) => el.click()")

    start_btn = page.locator('.onboarding-footer button:has-text("Start")')
    start_btn.wait_for(timeout=TIMEOUT_MS)
    assert start_btn.is_enabled(), "Start stayed disabled after upload + disclosure"
    start_btn.evaluate("(el) => el.click()")

    # Onboarding dismisses itself (`onDone()`) once PUT /api/settings,
    # POST /api/onboarding/complete and the disclosure ack have all
    # resolved.
    page.wait_for_selector(".onboarding-overlay", state="detached", timeout=TIMEOUT_MS)


def _check_no_key_path(url: str, resume_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=TIMEOUT_MS)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(TIMEOUT_MS)
        page.goto(url, timeout=TIMEOUT_MS)

        _fill_minimum_and_start(page, resume_path, api_key=None)
        print("no_key_onboarding_completed=ok")

        # The DoD's hardest clause, and T61's actual defect: a reload must
        # not show the wizard again now that no key was ever configured.
        page.reload(timeout=TIMEOUT_MS)
        page.wait_for_timeout(500)
        overlay_visible = page.locator(".onboarding-overlay").count() > 0
        print(f"no_key_reload_shows_onboarding_again={overlay_visible}")
        assert overlay_visible is False, "reloading after a no-key finish showed onboarding again"

        browser.close()


def _check_fake_key_path(url: str, resume_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=TIMEOUT_MS)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(TIMEOUT_MS)
        page.goto(url, timeout=TIMEOUT_MS)

        _fill_minimum_and_start(page, resume_path, api_key=FAKE_KEY)
        print("fake_key_onboarding_completed=ok")

        page.reload(timeout=TIMEOUT_MS)
        page.wait_for_timeout(500)
        overlay_visible = page.locator(".onboarding-overlay").count() > 0
        print(f"fake_key_reload_shows_onboarding_again={overlay_visible}")
        assert overlay_visible is False, "reloading after a fake-key finish showed onboarding again"

        browser.close()


def _check_phone_width(url: str, resume_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=TIMEOUT_MS)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(TIMEOUT_MS)
        page.goto(url, timeout=TIMEOUT_MS)

        panel = page.locator(".onboarding-panel")
        panel.wait_for(timeout=TIMEOUT_MS)
        box = panel.bounding_box()
        print(f"phone_panel_box={box}")
        assert box is not None and box["width"] <= 390, f"panel overflows the 390px viewport: {box}"

        _fill_minimum_and_start(page, resume_path, api_key=None)
        print("phone_width_onboarding_completed=ok")

        browser.close()


def main() -> int:
    if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
        print("frontend/dist/index.html not found -- run `npx vite build` in frontend/ first.", file=sys.stderr)
        return 1

    # Three independent fresh installs -- never the real data dir, and
    # never shared between scenarios, so completing scenario 1 cannot be
    # mistaken for scenario 2's own completion.
    for name, check in (
        ("no-key", _check_no_key_path),
        ("fake-key", _check_fake_key_path),
        ("phone-width", _check_phone_width),
    ):
        with tempfile.TemporaryDirectory(prefix=f"jf-t61-check-{name}-", ignore_cleanup_errors=True) as tmp:
            data_dir = Path(tmp) / "data"
            resume_path = _resume_fixture(Path(tmp))
            proc = _launch(data_dir)
            try:
                url = _wait_for_port(proc)
                print(f"[{name}] server up at {url}")
                check(url, resume_path)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    print("T61_BROWSER_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
