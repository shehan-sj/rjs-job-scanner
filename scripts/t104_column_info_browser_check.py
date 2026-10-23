#!/usr/bin/env python
"""T104 DoD browser check — the board's column descriptions, behind an
info control, measured.

Follows ``scripts/t99_column_colour_browser_check.py``'s shape: the same
``_seed``/``_wait_for_port`` reuse from ``t81_board_browser_check.py``,
reading ``GET /api/board`` through the page (the server authenticates
with a session cookie; an outside request answers 401), polling from
Python rather than ``wait_for_function`` (an async predicate hands back
a handle to the Promise, not its value), and sampling a baseline
*before* triggering anything rather than after.

Asserts, per this task's DoD, measured at 2560x1400 and 390x844, **in
both themes** (``data-theme`` set on ``document.documentElement``, the
same attribute ``theme.ts``'s ``applyTheme`` sets):

  * no column's description or fills-itself text is **visible** at
    rest — measured by computed style/bounding rect, never DOM
    presence alone (this project has been bitten seven times by that
    exact difference, and this task is literally about hiding things);
  * opening a column's info makes exactly that column's description/
    fills-itself text visible, and no other column's;
  * opening a second column's info closes the first — exactly one
    column's text is visible afterwards, and it is the second one's;
  * Escape dismisses whichever column's info is open;
  * the vertical distance from the top of a column to its first card
    shrinks at rest compared with the same measurement taken with that
    column's info open (2560x1400 only — the proof the clutter actually
    went, rather than moved somewhere else on screen);
  * T81's horizontal-fill floor still holds (`.board-column` bounding
    boxes span >= viewport - 220px, 2560x1400 only);
  * no horizontal scroll at 2560x1400 or 390x844.

Usage::

    .venv/Scripts/python.exe scripts/t104_column_info_browser_check.py [outdir]

Requires ``npx vite build`` to have produced ``frontend/dist`` already,
and Playwright's Chromium (``playwright install chromium``) installed.
Every Playwright call below carries an explicit timeout — an unbounded
run on this project once hung for three hours.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from t81_board_browser_check import _no_horizontal_scroll, _seed, _wait_for_port  # noqa: E402

PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TIMEOUT_MS = 20000
VIEWPORT_DESKTOP = {"width": 2560, "height": 1400}
VIEWPORT_PHONE = {"width": 390, "height": 844}

STAGE_ORDER = ["new", "shortlist", "applied", "replied", "out"]


def _set_theme(page, theme: str) -> None:
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)


def _visible_text_by_stage(page) -> dict:
    """Per column: whether its ``.board-column-desc``/``.board-column-
    fills`` are actually **visible** — a non-zero bounding rect and no
    ``visibility: hidden``/``display: none`` — never DOM presence alone.
    Absence from the DOM (this task's conditional-render approach) counts
    as not-visible the same way a zero-size/hidden element would."""
    return page.evaluate(
        """(stages) => stages.map((stage) => {
            const col = document.querySelector(`.board-column[data-stage="${stage}"]`);
            function isVisible(el) {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
            }
            const desc = col ? col.querySelector('.board-column-desc') : null;
            const fills = col ? col.querySelector('.board-column-fills') : null;
            return { stage, descVisible: isVisible(desc), fillsVisible: isVisible(fills) };
        })""",
        STAGE_ORDER,
    )


def _stages_with_visible_text(metrics: list[dict]) -> list[str]:
    return [m["stage"] for m in metrics if m["descVisible"] or m["fillsVisible"]]


def _info_button(page, stage: str):
    return page.locator(f'.board-column[data-stage="{stage}"] .board-column-info-btn')


def _column_top_to_first_card_gap(page, stage: str) -> float:
    """The vertical distance from the top of column `stage` to the top
    of its first `.board-card` — the measurement this task's DoD names
    as the proof the clutter actually went, rather than moved elsewhere
    on screen."""
    gap = page.evaluate(
        """(stage) => {
            const col = document.querySelector(`.board-column[data-stage="${stage}"]`);
            const card = col ? col.querySelector('.board-card') : null;
            if (!col || !card) return null;
            const colTop = col.getBoundingClientRect().top;
            const cardTop = card.getBoundingClientRect().top;
            return cardTop - colTop;
        }""",
        stage,
    )
    assert gap is not None, f"column {stage!r} has no first card to measure against"
    return gap


def _columns_horizontal_fill(page) -> dict:
    spread = page.evaluate(
        """
        () => {
            const cols = [...document.querySelectorAll('.board-column')];
            if (!cols.length) return null;
            const left = Math.min(...cols.map(c => c.getBoundingClientRect().left));
            const right = Math.max(...cols.map(c => c.getBoundingClientRect().right));
            return { left, right, vw: document.documentElement.clientWidth };
        }
        """
    )
    assert spread, "no .board-column elements to measure"
    return spread


def _check_theme_desktop(page, outdir: pathlib.Path, theme: str) -> None:
    _set_theme(page, theme)
    # Theme tokens are plain CSS custom properties — no network round
    # trip — but give layout/paint a tick before reading computed style.
    page.wait_for_timeout(100)

    # ── At rest: nothing is visible. ─────────────────────────────────
    baseline = _visible_text_by_stage(page)
    print(f"[{theme}] at_rest_visible_text={_stages_with_visible_text(baseline)}")
    assert _stages_with_visible_text(baseline) == [], (
        f"[{theme}] a column's description/fills-itself text is visible at rest: {baseline}"
    )

    # ── The height-above-the-first-card baseline, closed. ────────────
    gap_closed = _column_top_to_first_card_gap(page, "new")
    print(f"[{theme}] new_column_top_to_first_card_closed={gap_closed:.1f}")

    # ── Opening New's info makes exactly New's text visible. ─────────
    _info_button(page, "new").click(timeout=TIMEOUT_MS)
    page.wait_for_timeout(100)
    after_open_new = _visible_text_by_stage(page)
    visible_stages = _stages_with_visible_text(after_open_new)
    print(f"[{theme}] visible_after_opening_new={visible_stages}")
    assert visible_stages == ["new"], (
        f"[{theme}] opening New's info should make exactly New's text visible, got {visible_stages}"
    )

    # ── The height-above-the-first-card measurement, open — must be
    # strictly larger than the closed baseline (the gap the disclosure
    # panel itself now occupies).
    gap_open = _column_top_to_first_card_gap(page, "new")
    print(f"[{theme}] new_column_top_to_first_card_open={gap_open:.1f}")
    assert gap_open > gap_closed, (
        f"[{theme}] opening New's info did not grow the gap above its first card "
        f"({gap_closed:.1f} -> {gap_open:.1f}) -- the clutter did not move, or did not move here"
    )

    # ── Opening a second column's info (Shortlist) closes the first. ─
    _info_button(page, "shortlist").click(timeout=TIMEOUT_MS)
    page.wait_for_timeout(100)
    after_open_shortlist = _visible_text_by_stage(page)
    visible_stages = _stages_with_visible_text(after_open_shortlist)
    print(f"[{theme}] visible_after_opening_shortlist={visible_stages}")
    assert visible_stages == ["shortlist"], (
        f"[{theme}] opening Shortlist's info while New's was open should close New's and leave exactly "
        f"Shortlist's visible, got {visible_stages}"
    )

    # ── Escape dismisses whichever is open. ──────────────────────────
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    after_escape = _visible_text_by_stage(page)
    visible_stages = _stages_with_visible_text(after_escape)
    print(f"[{theme}] visible_after_escape={visible_stages}")
    assert visible_stages == [], f"[{theme}] Escape did not dismiss the open column's info: {visible_stages}"

    # ── The gap is back to its closed baseline post-Escape. ──────────
    gap_after_escape = _column_top_to_first_card_gap(page, "new")
    print(f"[{theme}] new_column_top_to_first_card_after_escape={gap_after_escape:.1f}")
    assert gap_after_escape == gap_closed, (
        f"[{theme}] the gap above New's first card did not return to its closed baseline after Escape "
        f"({gap_closed:.1f} -> {gap_after_escape:.1f})"
    )

    # ── T81's horizontal-fill floor still holds. ─────────────────────
    spread = _columns_horizontal_fill(page)
    used = spread["right"] - spread["left"]
    print(
        f"[{theme}] columns_left={spread['left']:.0f} right={spread['right']:.0f} used={used:.0f} of vw={spread['vw']}"
    )
    assert used >= spread["vw"] - 220, (
        f"[{theme}] the five columns use only {used:.0f}px of a {spread['vw']}px viewport "
        f"-- T81's horizontal-fill assertion no longer holds"
    )

    assert _no_horizontal_scroll(page), f"[{theme}] the board causes horizontal scroll at 2560x1400"
    print(f"[{theme}] no_horizontal_scroll_2560=ok")

    shot = outdir / f"t104-board-2560x1400-{theme}.png"
    page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
    print(f"[{theme}] captured={shot}")


def _check_theme_phone(page, outdir: pathlib.Path, theme: str) -> None:
    _set_theme(page, theme)
    page.wait_for_timeout(100)

    baseline = _visible_text_by_stage(page)
    print(f"[{theme}][phone] at_rest_visible_text={_stages_with_visible_text(baseline)}")
    assert _stages_with_visible_text(baseline) == [], (
        f"[{theme}][phone] a column's description/fills-itself text is visible at rest: {baseline}"
    )

    assert _no_horizontal_scroll(page), f"[{theme}][phone] the board causes horizontal scroll at 390x844"
    print(f"[{theme}][phone] no_horizontal_scroll_390=ok")

    shot = outdir / f"t104-board-390x844-{theme}.png"
    page.screenshot(path=str(shot), timeout=TIMEOUT_MS)
    print(f"[{theme}][phone] captured={shot}")


def main() -> int:
    outdir = (
        pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.mkdtemp(prefix="t104-column-info-"))
    )
    outdir.mkdir(parents=True, exist_ok=True)

    if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
        print("frontend/dist/index.html not found -- run `npx vite build` in frontend/ first.", file=sys.stderr)
        return 1

    from playwright.sync_api import sync_playwright

    original_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")

    with tempfile.TemporaryDirectory(prefix="jf-t104-", ignore_cleanup_errors=True) as tmp:
        data_dir = pathlib.Path(tmp) / "data"
        _seed(data_dir)

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
        try:
            url = _wait_for_port(proc)
            print(f"server up at {url}")

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, timeout=TIMEOUT_MS)
                try:
                    # ── 2560x1400, both themes ───────────────────────
                    page = browser.new_page(viewport=VIEWPORT_DESKTOP)
                    page.set_default_timeout(TIMEOUT_MS)
                    page.goto(url, timeout=TIMEOUT_MS)
                    page.wait_for_selector(".board-column", timeout=TIMEOUT_MS)

                    columns = page.locator(".board-column")
                    assert columns.count() == 5, f"expected five columns, found {columns.count()}"

                    _check_theme_desktop(page, outdir, "dark")
                    _check_theme_desktop(page, outdir, "light")

                    page.close()

                    # ── 390x844, both themes: at-rest visibility and no
                    # horizontal scroll (the phone floor
                    # t81_board_browser_check.py already asserts).
                    phone_page = browser.new_page(viewport=VIEWPORT_PHONE)
                    phone_page.set_default_timeout(TIMEOUT_MS)
                    phone_page.goto(url, timeout=TIMEOUT_MS)
                    phone_page.wait_for_selector(".board-view", timeout=TIMEOUT_MS)

                    _check_theme_phone(phone_page, outdir, "dark")
                    _check_theme_phone(phone_page, outdir, "light")

                    phone_page.close()
                finally:
                    browser.close(reason="t104 column info check done")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("\nT104_COLUMN_INFO_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
