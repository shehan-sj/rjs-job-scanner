"""Playwright browser controller for FillerBot.

Launches a **headed** Chrome window so the user can watch and edit in
real time, then connects via CDP for programmatic form-filling.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import tempfile
import subprocess
import time
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .models import FillAction

logger = logging.getLogger(__name__)

_VIEWPORT = {"width": 1280, "height": 900}
_TIMEOUT = 10_000  # ms

# The automated Chrome must be visually distinguishable from the user's own
# browser (T31: "the two-windows problem"). Three cheap, independent signals:
#
#  * ``--window-size`` (already passed) — a fixed 1280x900 that matches the
#    CDP viewport.
#  * ``--window-position`` — a deliberate offset rather than Chrome's
#    default cascade, which lands a new window roughly on top of an existing
#    one. 220/120 is far enough that the user's own window stays readable
#    behind it, and small enough to stay on-screen on a 1366x768 laptop
#    panel (220+1280 overflows 1366 horizontally, but Chrome clamps a
#    window that would be placed off-screen, so the worst case is the
#    default placement, never an invisible window).
#  * a window-title prefix — see ``_TITLE_PREFIX_SCRIPT``.
_WINDOW_POSITION = {"x": 220, "y": 120}

# Chrome has no CLI flag for the window title: the OS window title is
# ``document.title``. Prefixing it is therefore the only available
# mechanism, and doing it naively would break the page — so this shim is
# deliberately one-way:
#
#   * the *setter* writes the prefixed string (what Chrome shows in the
#     window/tab title),
#   * the *getter* strips the prefix back off, so any script running in the
#     page sees exactly the value the page set. Page code that branches on
#     its own ``document.title`` is unaffected.
#
# The prefix is therefore visible only to observers *outside* the page:
# Chrome's own window/tab chrome (the point of the exercise) and anything
# reading the title off the CDP frame tree rather than the DOM — notably
# Playwright's ``page.title()``, which returns the prefixed string.
# Verified against a real Chromium; nothing in this project reads a page
# title (``grep -rn 'page.title()\|document.title' jobfinder/`` finds only
# this module), so no caller sees the prefix today. A future caller that
# needs the page's own title should read ``document.title`` via
# ``page.evaluate``, which is shimmed, rather than ``page.title()``.
#
# It is installed with ``add_init_script``, so it re-applies on every
# navigation (the engine navigates, and a SPA can rewrite its own title
# later) before any page script runs. A MutationObserver on <title> covers
# the other route to the same state: code that edits the <title> element's
# text directly instead of assigning ``document.title``. The observer's
# sync is a no-op when the raw title already carries the prefix, so writing
# the prefix cannot re-trigger it into a loop.
#
# One subtlety the loop guard has to know about: HTML strips trailing
# whitespace from a title, so setting an *empty* page title stores the
# prefix minus its trailing space. A guard that only tested
# ``startsWith(PREFIX)`` therefore missed it and prefixed a second time,
# and ``strip`` likewise handed the bare prefix back to the page as if the
# page had set it. Both now recognise that trimmed form.
_WINDOW_TITLE_PREFIX = "[JobFinder FillerBot] "

_TITLE_PREFIX_SCRIPT = """
(() => {
  const PREFIX = %s;
  const desc = Object.getOwnPropertyDescriptor(Document.prototype, "title");
  if (!desc || !desc.get || !desc.set) return;
  if (document.__jobfinderTitleShim) return;
  Object.defineProperty(document, "__jobfinderTitleShim", { value: true });

  // HTML strips trailing whitespace from a title, so when the page's own
  // title is empty the DOM keeps the prefix WITHOUT its trailing space.
  // Both the strip and the loop guard below have to recognise that form,
  // or an empty page title double-prefixes and leaks the marker back to
  // the page as if it were the page's own title.
  const BARE = PREFIX.trimEnd();

  const rawTitle = () => {
    const t = desc.get.call(document);
    return typeof t === "string" ? t : "";
  };
  const hasPrefix = (t) =>
    typeof t === "string" && (t.startsWith(PREFIX) || t === BARE);
  const strip = (t) => {
    if (typeof t !== "string") return t;
    if (t.startsWith(PREFIX)) return t.slice(PREFIX.length);
    if (t === BARE) return "";
    return t;
  };
  const write = (value) =>
    desc.set.call(document, PREFIX + (value == null ? "" : String(value)));

  Object.defineProperty(Document.prototype, "title", {
    configurable: true,
    enumerable: desc.enumerable,
    get() {
      return this === document ? strip(desc.get.call(this)) : desc.get.call(this);
    },
    set(value) {
      if (this === document) write(value);
      else desc.set.call(this, value);
    },
  });

  // Idempotent: returns immediately once the prefix is present, so our own
  // write can never bounce back through the observer below.
  const sync = () => {
    const raw = rawTitle();
    if (hasPrefix(raw)) return;
    write(raw);
  };

  const observe = () => {
    sync();
    const el = document.querySelector("title");
    if (!el) return;
    new MutationObserver(sync).observe(el, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  };

  sync();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", observe, { once: true });
  } else {
    observe();
  }
})();
""" % ('"' + _WINDOW_TITLE_PREFIX + '"')

# Defense-in-depth bounds applied before any value reaches CDP (the product spec).
# These are not meant to be hit in normal use — they exist so
# a model-returned action (ultimately derived from untrusted page content)
# can't smuggle an oversized payload or a CSS-selector-breaking value into
# a Playwright call.
_MAX_VALUE_LEN = 5_000
_MAX_SELECTOR_LEN = 500

# Action shapes that look like a final submission. Checked against both the
# action's field_label and its selector_value, case-insensitively. This is a
# server-side backstop — the model is *also* told never to return these in
# the system prompt, but a prompt is not a control, so
# FillerBrowser.execute() refuses to dispatch them regardless of what the
# model returns.
_SUBMIT_KEYWORDS = (
    "submit",
    "send application",
    "finish application",
    "complete application",
    "finalize application",
    "apply now",
)


def _looks_like_submit(action: FillAction) -> bool:
    """True if an action's label/selector reads like a final-submit control."""
    if action.action_type != "click":
        return False
    haystack = f"{action.field_label} {action.selector_value}".lower()
    return any(kw in haystack for kw in _SUBMIT_KEYWORDS)


def _sanitize_value(value: str, max_len: int = _MAX_VALUE_LEN) -> str:
    """Bound text length before it's typed/filled via CDP."""
    if not isinstance(value, str):
        return ""
    return value[:max_len]


def _escape_attr_value(value: str) -> str:
    """Escape a value destined for a CSS attribute selector (e.g. [id="..."]).

    The value still ultimately comes from a model response, so this isn't
    optional hygiene — it stops a crafted id/name from breaking out of the
    quoted attribute selector and injecting arbitrary selector syntax.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_chrome_exe() -> str | None:
    """Locate the headed Chrome binary for the live browser window.

    Searches project-local ``browsers/`` first, then ``%LOCALAPPDATA%``.
    """
    project_base = os.path.join(os.getcwd(), "browsers")
    exe = _scan_for_chrome(project_base)
    if exe:
        return exe

    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        exe = _scan_for_chrome(os.path.join(localappdata, "ms-playwright"))
        if exe:
            return exe

    return None


def _scan_for_chrome(base: str) -> str | None:
    """Find a headed chrome.exe (not headless shell) in a Playwright dir."""
    try:
        dirs = sorted(os.listdir(base), reverse=True)
    except OSError:
        return None
    for d in dirs:
        if d.startswith("chromium-") and "headless" not in d:
            exe = os.path.join(base, d, "chrome-win64", "chrome.exe")
            if os.path.isfile(exe):
                return exe
    return None


class FillerBrowser:
    """Async Playwright wrapper — headed Chrome, one page."""

    def __init__(self, chrome_exe: str = "") -> None:
        self._chrome_exe = chrome_exe
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._chrome_proc: subprocess.Popen | None = None
        self._profile_dir: str | None = None

    def _launch_chrome_sync(self, exe: str) -> int:
        """Blocking: spawn Chrome and wait for its CDP port to accept
        connections. Run off the event loop via ``run_in_threadpool``
        (T29) — ``subprocess.Popen`` and the socket-connect poll below are
        both synchronous syscalls that would otherwise stall the Uvicorn
        loop for the whole 10s this can take in the worst case.
        """
        # Ephemeral per-session profile (the product spec: profile must not
        # persist across sessions). A real job-site login performed during
        # one application must not survive into the next session's browser.
        self._profile_dir = tempfile.mkdtemp(prefix="fillerbot-profile-")

        port = _free_port()
        self._chrome_proc = subprocess.Popen(
            [
                exe,
                f"--remote-debugging-port={port}",
                # CDP has no built-in authentication — binding to a
                # wildcard address would let any local process drive this
                # browser (cookies, arbitrary JS) via /json/list. Bind
                # loopback-only.
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={self._profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-infobars",
                f"--window-size={_VIEWPORT['width']},{_VIEWPORT['height']}",
                f"--window-position={_WINDOW_POSITION['x']},{_WINDOW_POSITION['y']}",
                # Without these, a FillerBot window the user has covered with
                # another window stops producing compositor frames, and every
                # screenshot then hangs until its timeout — see screenshot().
                # Playwright passes all three when it launches Chrome itself,
                # which is exactly why a plain playwright launch could capture
                # this page in 0.15s while our hand-rolled launch could not.
                "--disable-backgrounding-occluded-windows",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        for _ in range(40):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return port
            except OSError:
                time.sleep(0.25)
        raise RuntimeError(f"Chrome did not start on port {port} within 10 seconds")

    async def launch(self) -> None:
        exe = self._chrome_exe
        if not exe:
            raise RuntimeError("No Chrome executable path provided. Run: py -m playwright install")
        logger.info("Launching headed Chrome: %s", exe)

        port = await run_in_threadpool(self._launch_chrome_sync, exe)
        cdp_url = f"http://127.0.0.1:{port}"

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(cdp_url)

        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
        else:
            self._context = await self._browser.new_context(
                viewport=_VIEWPORT,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )

        pages = self._context.pages
        if pages:
            self._page = pages[0]
        else:
            self._page = await self._context.new_page()

        self._page.set_default_timeout(_TIMEOUT)
        await self._install_title_prefix()
        logger.info("FillerBot browser launched (CDP port %d).", port)

    async def _install_title_prefix(self) -> None:
        """Make the automated window's title self-identifying (T31).

        Best-effort on purpose: a failure here costs one of three
        distinguishability signals (position and size still apply) and must
        not take the session down with it.
        """
        try:
            assert self._context is not None
            await self._context.add_init_script(_TITLE_PREFIX_SCRIPT)
        except Exception as exc:
            logger.warning("Could not install the FillerBot window-title prefix: %s", exc)
            return
        # add_init_script only fires on *subsequent* navigations, and the
        # window already has its first (about:blank) document.
        try:
            assert self._page is not None
            await self._page.evaluate(_TITLE_PREFIX_SCRIPT)
        except Exception as exc:
            logger.debug("Title prefix not applied to the current document: %s", exc)

    async def bring_to_front(self) -> None:
        """Raise the automated Chrome window above the user's other windows.

        Playwright's ``page.bring_to_front()`` issues CDP
        ``Page.bringToFront``, which is the only cross-platform handle we
        have on window stacking here — there is no OS-level window call in
        this process, the window belongs to the Chrome subprocess.
        """
        if self._page is None:
            raise RuntimeError("the FillerBot browser window is not open yet")
        await self._page.bring_to_front()

    async def navigate(self, url: str) -> None:
        assert self._page is not None
        await self._page.goto(url, wait_until="domcontentloaded")
        try:
            await self._page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass  # many pages never reach networkidle — that's fine
        logger.info("Navigated to %s", url)

    async def screenshot(self) -> bytes:
        """Capture the current viewport (the product spec: minimize what's sent
        to Anthropic — a full-page capture is both a bigger payload and
        more of the page than the model needs to plan the next action).

        Activates the tab first. The real cause of capture hangs was the
        OS window, not the tab — Chrome suppresses compositing for a window
        it believes is occluded, and Playwright's default capture path
        (``fromSurface: true``) then waits for a frame that never arrives.
        That is fixed at launch with ``--disable-backgrounding-occluded-windows``
        (see ``launch()``). Diagnosed by elimination: ``page.evaluate`` kept
        working and ``document.fonts.status`` read ``loaded``, so neither the
        renderer nor font loading was stuck, and a raw
        ``Page.captureScreenshot`` with ``fromSurface: false`` returned fine
        while the default timed out at 10s.

        ``bring_to_front`` stays because it covers the other half: a
        *background tab* within the window. Best-effort — if activation
        fails the capture is still attempted, because a screenshot that
        might work beats refusing outright.
        """
        assert self._page is not None
        try:
            await self._page.bring_to_front()
        except Exception as exc:  # noqa: BLE001 — never fail a capture over this
            logger.debug("Could not activate the page before capture: %s", exc)
        return await self._page.screenshot(type="png", full_page=False)

    async def scan_form_fields(self) -> list[dict]:
        """Extract all form fields from the DOM with their attributes."""
        assert self._page is not None
        return await self._page.evaluate("""() => {
            const fields = [];
            const els = document.querySelectorAll(
                'input, select, textarea, [contenteditable="true"]'
            );
            for (const el of els) {
                const tag = el.tagName.toLowerCase();
                const type = el.getAttribute('type') || '';
                if (type === 'hidden' || type === 'submit' || type === 'button') continue;
                if (el.offsetParent === null && type !== 'file') continue;

                const id = el.id || '';
                const name = el.name || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                const placeholder = el.placeholder || '';

                // Walk up to find associated label
                let labelText = '';
                if (id) {
                    const lbl = document.querySelector(`label[for="${id}"]`);
                    if (lbl) labelText = lbl.textContent.trim();
                }
                if (!labelText) {
                    const lbl = el.closest('label');
                    if (lbl) labelText = lbl.textContent.trim();
                }
                if (!labelText && ariaLabel) labelText = ariaLabel;

                // Current value
                let currentValue = '';
                if (tag === 'select') {
                    const opt = el.options[el.selectedIndex];
                    currentValue = opt ? opt.text.trim() : '';
                } else if (type === 'checkbox' || type === 'radio') {
                    currentValue = el.checked ? 'checked' : 'unchecked';
                } else {
                    currentValue = el.value || '';
                }

                // Select options
                let options = [];
                if (tag === 'select') {
                    options = [...el.options].map(o => o.text.trim()).filter(Boolean);
                }

                const rect = el.getBoundingClientRect();
                fields.push({
                    tag, type, id, name, label: labelText,
                    placeholder, currentValue,
                    options: options.length ? options : undefined,
                    y: Math.round(rect.top + window.scrollY),
                });
            }
            fields.sort((a, b) => a.y - b.y);
            return fields;
        }""")

    async def find_file_inputs(self) -> list[dict]:
        """Find all <input type='file'> on the page, including hidden ones."""
        assert self._page is not None
        return await self._page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type="file"]');
            return [...inputs].map((el, i) => {
                const id = el.id || '';
                const name = el.name || '';
                const accept = el.accept || '';
                const label = el.closest('label')?.textContent?.trim()
                    || el.getAttribute('aria-label') || '';
                const nearby = el.parentElement?.textContent?.trim()?.slice(0, 100) || '';
                return {index: i, id, name, accept, label, nearby};
            });
        }""")

    async def upload_to_file_input(self, resume_path: str, index: int = 0) -> bool:
        """Upload the server-managed resume to the nth <input type='file'>.

        ``resume_path`` here is always the server-managed resume location
        passed in by the caller (never model/page-controlled) — but we
        still validate it exists so a missing resume fails clearly instead
        of uploading nothing or raising an opaque exception.
        """
        assert self._page is not None
        if not resume_path or not Path(resume_path).exists():
            logger.warning("Resume upload failed: no resume file available at %r", resume_path)
            return False
        try:
            locator = self._page.locator('input[type="file"]').nth(index)
            await locator.set_input_files(resume_path)
            return True
        except Exception as exc:
            logger.warning("File input upload failed: %s", exc)
            return False

    async def execute(self, action: FillAction, resume_path: str = "") -> bool:
        """Execute a single FillAction on the page. Returns True on success."""
        assert self._page is not None

        # Server-side backstop: never dispatch a submit-shaped action, even
        # if the model returned one (the product spec — "never click
        # Submit" must be enforced in code, not only in the prompt).
        if _looks_like_submit(action):
            logger.warning(
                "Refusing submit-shaped action: field_label=%r selector_value=%r",
                action.field_label,
                action.selector_value,
            )
            return False

        try:
            locator = self._resolve_locator(action)
            if locator is None:
                logger.warning("Could not resolve locator for: %s", action.field_label)
                return False

            # Scroll element into view so Playwright doesn't hang waiting
            # for visibility when the field is off-screen or window is moved.
            if action.action_type != "scroll":
                try:
                    await locator.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass

            if action.action_type == "fill":
                await locator.click(force=True)
                await locator.fill(_sanitize_value(action.value))

            elif action.action_type == "select":
                if not await self._do_select(locator, _sanitize_value(action.value)):
                    return False

            elif action.action_type == "click":
                await locator.click(force=True)

            elif action.action_type == "check":
                if action.value.lower() in ("true", "yes", "1", "checked"):
                    await locator.check(force=True)
                else:
                    await locator.uncheck(force=True)

            elif action.action_type == "upload":
                # The only path that may ever be uploaded is the one
                # server-managed resume. `action.value` comes from the
                # model's reading of page content — a prompt-injected
                # application form could set it to an arbitrary local
                # path, so it is never used as an upload source (the product spec).
                # If no resume is available, the action
                # fails with a clear reason instead of falling back to
                # anything model/page-controlled.
                if resume_path and Path(resume_path).exists():
                    await locator.set_input_files(resume_path)
                else:
                    logger.warning(
                        "Upload refused: no server-managed resume available (resume_path=%r)",
                        resume_path,
                    )
                    return False

            elif action.action_type == "scroll":
                await self._page.evaluate("window.scrollBy(0, 400)")

            else:
                logger.warning("Unknown action type: %s", action.action_type)
                return False

            await asyncio.sleep(0.3)
            return True

        except Exception as exc:
            logger.warning(
                "Action failed [%s] %s: %s",
                action.action_type,
                action.field_label,
                exc,
            )
            return False

    async def _do_select(self, locator, value: str) -> bool:
        """Select a dropdown value. Returns True if a match was found."""
        tag = await locator.evaluate("el => el.tagName.toLowerCase()")

        if tag == "select":
            return await self._select_native(locator, value)

        # Non-native dropdown (div/input/button) — click to open, then type
        await locator.click(force=True)
        await asyncio.sleep(0.3)
        await self._page.keyboard.press("Control+a")
        await self._page.keyboard.type(value, delay=30)
        await asyncio.sleep(0.5)
        await self._page.keyboard.press("Enter")
        await asyncio.sleep(0.2)
        return True

    async def _select_native(self, locator, value: str) -> bool:
        """Pick an option from a native <select> without slow timeouts."""
        options = await locator.evaluate("el => [...el.options].map(o => ({value: o.value, text: o.text.trim()}))")
        val_lower = value.lower().strip()

        # Pass 1: exact match (case-insensitive)
        for opt in options:
            if opt["text"].lower() == val_lower:
                await locator.select_option(value=opt["value"], force=True)
                return True

        # Pass 2: option contains the value or value contains the option
        for opt in options:
            opt_lower = opt["text"].lower()
            if not opt_lower:
                continue
            if val_lower in opt_lower or opt_lower in val_lower:
                await locator.select_option(value=opt["value"], force=True)
                return True

        logger.warning("No matching option for '%s' in <select>", value)
        return False

    def _resolve_locator(self, action: FillAction):
        """Map selector_strategy + selector_value to a Playwright locator."""
        page = self._page
        strategy = action.selector_strategy
        value = _sanitize_value(action.selector_value, _MAX_SELECTOR_LEN)
        idx = action.field_index

        if strategy == "id":
            return page.locator(f'[id="{_escape_attr_value(value)}"]')
        elif strategy == "name":
            return page.locator(f'[name="{_escape_attr_value(value)}"]')
        elif strategy == "label":
            loc = page.get_by_label(value, exact=True)
            if action.action_type in ("fill", "select", "check"):
                role = (
                    "textbox"
                    if action.action_type == "fill"
                    else ("combobox" if action.action_type == "select" else "checkbox")
                )
                role_loc = page.get_by_role(role, name=value)
                count = role_loc.count()
                if count == 1:
                    return role_loc
                if count > 1 and idx < count:
                    return role_loc.nth(idx)
            return self._nth_or_first(loc, idx)
        elif strategy == "placeholder":
            return self._nth_or_first(page.get_by_placeholder(value), idx)
        elif strategy == "text":
            return page.get_by_text(value).first
        elif strategy == "position":
            return page.locator(value).first
        else:
            return self._nth_or_first(page.get_by_label(value, exact=True), idx)

    @staticmethod
    def _nth_or_first(loc, idx: int):
        """Return the nth match if multiple exist, otherwise first."""
        count = loc.count()
        if count > 1 and idx < count:
            return loc.nth(idx)
        return loc.first

    async def current_url(self) -> str:
        if self._page:
            return self._page.url
        return ""

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception as exc:
            logger.debug("Browser cleanup: %s", exc)
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._pw = None
        if self._chrome_proc:
            try:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
            except Exception:
                self._chrome_proc.kill()
            self._chrome_proc = None

        # Wipe the per-session profile now that Chrome has exited, so a
        # login performed during this session can't persist into the next
        # one (the product spec — ephemeral profile).
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None
        logger.info("FillerBot browser closed.")
