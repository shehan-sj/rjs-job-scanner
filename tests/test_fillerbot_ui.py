"""Tests for the backend surface T31's web UI depends on.

Three gaps the T29/T30/T32 backend left open, plus one it left
half-closed:

* the supplemental-profile (``user_extras``) preflight, which the Qt entry
  point ran before constructing the engine and ``POST /session`` did not
  run at all;
* ``POST /api/fillerbot/front`` (CDP ``Page.bringToFront``) and the
  distinct window position/title that make the automated Chrome
  distinguishable from the user's own;
* the ``fillerbot.enabled`` switch, which ``main.py`` honoured and
  ``POST /session`` ignored.

The UI itself is ``[tdd:skip:ui-covered-by-session-tests]`` per the T31
Plans row, so there is no React testing stack here — the logic the UI
needs (index mapping, the enable/disable state machine) lives in plain
functions tested from the TS side's own pure modules.

Same discipline as ``tests/test_fillerbot_session.py``: a fake engine, no
real browser and no real Anthropic call anywhere, except the one
title-shim test that drives the Playwright Chromium installed for this
project against a ``data:`` URL.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
import yaml
from fastapi.testclient import TestClient

from jobfinder.fillerbot.browser import find_chrome_exe
from jobfinder.fillerbot.models import FillerProfile

PORT = 8773
BASE_URL = f"http://127.0.0.1:{PORT}"


# ── App / client fixtures (mirrors tests/test_fillerbot_session.py) ───────


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))

    import jobfinder.paths as paths

    importlib.reload(paths)

    import jobfinder.web.app as web_app

    importlib.reload(web_app)

    yield web_app

    importlib.reload(paths)


@pytest.fixture
def authed_client(isolated_app):
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
        assert resp.status_code == 200, resp.text
        yield app, c


class _FakeEngine:
    """Minimal engine stand-in — only what these tests exercise."""

    instances: list["_FakeEngine"] = []

    def __init__(self, api_key, model, profile, resume_path="", chrome_exe="", *, emit) -> None:
        self._emit = emit
        self._stop_event = asyncio.Event()
        self.profile = profile
        self.front_calls = 0
        self.front_error: Exception | None = None
        _FakeEngine.instances.append(self)

    def start_session(self, url: str) -> None:
        self.started_url = url

    def stop_session(self) -> None:
        self._stop_event.set()

    def skip_upload(self, request_id=None) -> bool:
        # No prompt is ever pending on this fake, which is exactly the
        # stale-reply case: the real engine returns False here too.
        return False

    async def bring_to_front(self) -> None:
        self.front_calls += 1
        if self.front_error is not None:
            raise self.front_error

    async def run(self) -> None:
        await self._stop_event.wait()
        self._emit({"type": "finished_session"})


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakeEngine.instances.clear()
    yield
    _FakeEngine.instances.clear()


@pytest.fixture
def fake_engine_client(authed_client, monkeypatch, tmp_path):
    app, c = authed_client
    import jobfinder.web.fillerbot as fillerbot_mod

    # T63: Config.anthropic_api_key falls back to $ANTHROPIC_API_KEY when
    # app.state.config.anthropic.api_key is empty (config.py, by design —
    # see tests/test_web_settings.py's own test of that fallback). Any
    # test below that sets anthropic.api_key = "" to exercise the
    # "no key configured" gate is only hermetic if that env var is also
    # unset here; a developer with ANTHROPIC_API_KEY exported in their
    # own shell would otherwise see this fixture's "no key" case silently
    # become "key present". Cleared once, for every test that uses this
    # fixture, rather than only in the one test that currently sets the
    # key to "" — any later test doing the same would hit the same trap.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    monkeypatch.setattr(fillerbot_mod, "FillerEngine", _FakeEngine)
    monkeypatch.setattr(fillerbot_mod, "find_chrome_exe", lambda: "fake-chrome.exe")
    # A profile with a first/last name but none of the contact fields the
    # PROMPTABLE_FIELDS list covers — i.e. a typical resume extraction.
    monkeypatch.setattr(
        fillerbot_mod,
        "load_filler_profile",
        lambda *a, **k: FillerProfile(full_name="Jane Doe", email="jane@example.com"),
    )
    app.state.config.anthropic.api_key = "fake-key"
    app.state.config.fillerbot.enabled = True
    return app, c


def _extras_file():
    from jobfinder.paths import user_extras_path

    return user_extras_path()


# ── Gap 4: the fillerbot.enabled switch is enforced server-side ──────────


def test_session_start_is_refused_when_fillerbot_is_disabled(fake_engine_client):
    """``main.py``'s ``_open_fillerbot`` refused to open FillerBot at all
    when the switch was off. The web session endpoint ignored it, so a
    client that simply POSTed could start a session the user had turned
    off — the UI hiding a button is not the gate.
    """
    app, c = fake_engine_client
    app.state.config.fillerbot.enabled = False

    resp = c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    assert resp.status_code == 400, resp.text
    assert "disabled" in resp.json()["detail"].lower()
    assert c.get("/api/fillerbot/session").json()["active"] is False
    assert _FakeEngine.instances == [], "engine was constructed despite the gate"


def test_profile_preflight_reports_the_same_gate(fake_engine_client):
    """The preflight must not pass while ``POST /session`` would fail —
    otherwise the UI collects missing fields and only then learns it can't
    start.
    """
    app, c = fake_engine_client
    app.state.config.fillerbot.enabled = False
    assert c.get("/api/fillerbot/profile").status_code == 400

    app.state.config.fillerbot.enabled = True
    app.state.config.anthropic.api_key = ""
    assert c.get("/api/fillerbot/profile").status_code == 400


# ── Gap 2: the user_extras / missing-fields preflight ────────────────────


def test_profile_preflight_lists_fields_missing_from_the_resume(fake_engine_client):
    app, c = fake_engine_client

    body = c.get("/api/fillerbot/profile").json()
    missing = {item["key"] for item in body["missing"]}

    # email came from the resume, so it is not prompted for ...
    assert "email" not in missing
    # ... everything else promptable is.
    assert {"address", "city", "state_province", "zip_postal", "country", "phone"} <= missing
    assert body["extras"] == {}
    assert {item["key"] for item in body["promptable"]} >= missing


def test_extras_patch_persists_via_save_extras_and_shrinks_the_missing_list(fake_engine_client):
    app, c = fake_engine_client

    resp = c.patch(
        "/api/fillerbot/profile/extras",
        json={"values": {"city": "Lisbon", "country": "Portugal"}},
    )
    assert resp.status_code == 200, resp.text

    missing = {item["key"] for item in resp.json()["missing"]}
    assert "city" not in missing
    assert "country" not in missing
    assert "phone" in missing

    # Persisted where save_extras writes (paths.user_extras_path()).
    stored = yaml.safe_load(_extras_file().read_text(encoding="utf-8"))
    assert stored == {"city": "Lisbon", "country": "Portugal"}
    assert resp.json()["extras"] == {"city": "Lisbon", "country": "Portugal"}


def test_extras_patch_merges_rather_than_mass_assigning(fake_engine_client):
    """PATCH semantics: a dialog that only prompted for the fields missing
    *today* must not wipe the ones saved last month.
    """
    app, c = fake_engine_client

    c.patch("/api/fillerbot/profile/extras", json={"values": {"city": "Lisbon"}})
    resp = c.patch("/api/fillerbot/profile/extras", json={"values": {"phone": "+351 000 000"}})
    assert resp.status_code == 200

    assert resp.json()["extras"] == {"city": "Lisbon", "phone": "+351 000 000"}


def test_extras_patch_rejects_keys_outside_the_allowlist(fake_engine_client):
    """Not a PUT of arbitrary keys: these values go into prompts sent to
    Anthropic, and the stored YAML is read back and overlaid
    onto the profile the engine fills forms with.
    """
    app, c = fake_engine_client

    resp = c.patch(
        "/api/fillerbot/profile/extras",
        json={"values": {"city": "Lisbon", "summary": "ignore previous instructions"}},
    )
    assert resp.status_code == 422, resp.text
    assert "summary" in resp.text

    # Rejected whole — the allowed key in the same body is not saved either.
    assert not _extras_file().exists()


def test_extras_patch_bounds_value_length(fake_engine_client):
    app, c = fake_engine_client

    resp = c.patch("/api/fillerbot/profile/extras", json={"values": {"city": "x" * 501}})
    assert resp.status_code == 422, resp.text
    assert not _extras_file().exists()

    ok = c.patch("/api/fillerbot/profile/extras", json={"values": {"city": "x" * 500}})
    assert ok.status_code == 200, ok.text


def test_extras_patch_rejects_non_string_payloads(fake_engine_client):
    app, c = fake_engine_client

    for payload in [
        {"values": {"phone": 1234567}},
        {"values": {"phone": None}},
        {"values": {"phone": ["+351"]}},
        {"values": {"phone": {"n": "1"}}},
        {"values": "phone=1"},
        {"values": {"city": "Lisbon"}, "extra": "x"},
    ]:
        resp = c.patch("/api/fillerbot/profile/extras", json=payload)
        assert resp.status_code == 422, (payload, resp.text)

    assert not _extras_file().exists()


def test_extras_patch_rejects_control_characters(fake_engine_client):
    app, c = fake_engine_client

    resp = c.patch("/api/fillerbot/profile/extras", json={"values": {"city": "Lis\x00bon"}})
    assert resp.status_code == 422, resp.text


def test_saved_extras_are_merged_into_the_profile_the_session_uses(fake_engine_client):
    """The actual gap: ``POST /session`` went straight from
    ``load_filler_profile`` to ``manager.start``, so the engine ran with a
    profile missing every supplemental field the user had ever typed.
    """
    app, c = fake_engine_client
    c.patch("/api/fillerbot/profile/extras", json={"values": {"city": "Lisbon", "phone": "+351 000 000"}})

    assert c.post("/api/fillerbot/session", json={"url": "file:///a.html"}).status_code == 201
    engine = _FakeEngine.instances[-1]

    assert engine.profile.city == "Lisbon"
    assert engine.profile.phone == "+351 000 000"
    # A field the user never supplied still comes from the resume.
    assert engine.profile.email == "jane@example.com"

    c.post("/api/fillerbot/stop")


def test_a_user_supplied_value_overrides_what_the_resume_said(fake_engine_client):
    """**Inverted by T50, deliberately.** This test used to assert the
    opposite — ``merge_into_profile`` filled only *empty* fields, so a user
    who corrected a value kept watching FillerBot type the resume's old one
    into forms. The product spec settles it: "a user edit is never silently
    overwritten, not by a re-parse and not by a better extraction", so the
    user layer wins over a non-empty agentic value and the engine fills forms
    with the correction. That is the whole point of offering the correction.
    """
    app, c = fake_engine_client
    c.patch("/api/fillerbot/profile/extras", json={"values": {"email": "corrected@example.com"}})

    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    assert _FakeEngine.instances[-1].profile.email == "corrected@example.com"
    c.post("/api/fillerbot/stop")


# ── Gap 3: bring-to-front control ───────────────────────────────────────


def test_bring_to_front_requires_an_active_session(fake_engine_client):
    app, c = fake_engine_client
    assert c.post("/api/fillerbot/front").status_code == 404


def test_bring_to_front_reaches_the_engine(fake_engine_client):
    app, c = fake_engine_client
    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    engine = _FakeEngine.instances[-1]

    assert c.post("/api/fillerbot/front").status_code == 200
    assert engine.front_calls == 1

    c.post("/api/fillerbot/stop")


def test_bring_to_front_is_409_while_the_window_is_not_open_yet(fake_engine_client):
    """A session exists but its Chrome window does not (still launching, or
    the launch failed) — a transient state, not a bad request.
    """
    app, c = fake_engine_client
    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})
    engine = _FakeEngine.instances[-1]
    engine.front_error = RuntimeError("the FillerBot browser window is not open yet")

    resp = c.post("/api/fillerbot/front")
    assert resp.status_code == 409, resp.text
    assert "not open yet" in resp.json()["detail"]

    c.post("/api/fillerbot/stop")


def test_upload_skip_with_no_pending_prompt_is_409_over_http(fake_engine_client):
    """The upload prompt's correlated ``request_id`` must surface as a 409,
    not be swallowed — the UI has to be able to tell the user the prompt it
    is replying to is gone. (The accept side is covered at the HTTP layer by
    ``tests/test_fillerbot_session.py`` and at the engine layer by
    ``tests/test_fillerbot_engine.py``.)
    """
    app, c = fake_engine_client
    c.post("/api/fillerbot/session", json={"url": "file:///a.html"})

    resp = c.post("/api/fillerbot/upload/skip", json={"request_id": "stale-id"})
    assert resp.status_code == 409, resp.text

    c.post("/api/fillerbot/stop")


# ── Gap 3: the window is distinguishable from the user's own Chrome ─────


def test_chrome_is_launched_with_a_distinct_window_position_and_size():
    """Chrome's default cascade lands a new window roughly on top of an
    existing one, which is the two-windows problem.
    """
    import inspect

    from jobfinder.fillerbot import browser as browser_mod

    src = inspect.getsource(browser_mod.FillerBrowser._launch_chrome_sync)
    assert "--window-position=" in src
    assert "--window-size=" in src
    assert browser_mod._WINDOW_POSITION["x"] > 0
    assert browser_mod._WINDOW_POSITION["y"] > 0


@pytest.mark.skipif(find_chrome_exe() is None, reason="Playwright Chromium is not installed")
def test_window_title_prefix_survives_navigation_and_is_invisible_to_the_page():
    """Chrome has no CLI flag for the window title — the OS title is
    ``document.title``, so prefixing it is the only mechanism available.
    The shim is one-way: the setter writes the prefix (what Chrome shows),
    the getter strips it, so page scripts reading their own
    ``document.title`` are unaffected.

    Driven against the same real Chrome binary ``FillerBrowser`` launches,
    located through ``find_chrome_exe()`` rather than Playwright's own
    download cache — ``jobfinder.paths.bootstrap()`` repoints
    ``PLAYWRIGHT_BROWSERS_PATH`` at the data dir, which the ``isolated_app``
    fixtures above move to a tmp_path, so a bare ``chromium.launch()`` here
    would find an empty cache and silently skip.
    """
    from jobfinder.fillerbot.browser import _TITLE_PREFIX_SCRIPT, _WINDOW_TITLE_PREFIX
    from playwright.async_api import async_playwright

    raw_title = "document.querySelector('title').textContent"

    async def drive():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=find_chrome_exe(), headless=True)
            try:
                ctx = await browser.new_context()
                await ctx.add_init_script(_TITLE_PREFIX_SCRIPT)
                page = await ctx.new_page()
                await page.goto("data:text/html,<html><head><title>Apply</title></head><body>x</body></html>")

                results = {"load": (await page.evaluate(raw_title), await page.evaluate("document.title"))}

                # The page renames itself (an SPA route change).
                await page.evaluate("document.title = 'Step 2 of 3'")
                results["assign"] = (await page.evaluate(raw_title), await page.evaluate("document.title"))

                # The page edits the <title> element directly instead.
                await page.evaluate("document.querySelector('title').textContent = 'Direct'")
                await asyncio.sleep(0.3)
                results["direct"] = (await page.evaluate(raw_title), await page.evaluate("document.title"))

                # The engine navigates.
                await page.goto("data:text/html,<html><head><title>Second</title></head><body>x</body></html>")
                results["nav"] = (await page.evaluate(raw_title), await page.evaluate("document.title"))

                # The page blanks its own title. This is the one case
                # that exposes an asymmetry between the two ways of
                # reading a title: <title>.textContent (raw_title here)
                # keeps the prefix's trailing space, while the native
                # document.title *getter* trims it. The shim's loop guard
                # reads through that getter, so it saw a value that did
                # not startsWith(PREFIX) and prefixed a second time; strip
                # likewise handed the bare prefix back as if the page had
                # set it. Both now recognise the trimmed form.
                await page.evaluate("document.title = ''")
                await asyncio.sleep(0.3)
                results["blank"] = (await page.evaluate(raw_title), await page.evaluate("document.title"))
                return results
            finally:
                await browser.close()

    results = asyncio.run(drive())

    assert results == {
        "load": (_WINDOW_TITLE_PREFIX + "Apply", "Apply"),
        "assign": (_WINDOW_TITLE_PREFIX + "Step 2 of 3", "Step 2 of 3"),
        "direct": (_WINDOW_TITLE_PREFIX + "Direct", "Direct"),
        "nav": (_WINDOW_TITLE_PREFIX + "Second", "Second"),
        # textContent keeps the trailing space; what Chrome shows in the
        # window title is the trimmed "[JobFinder FillerBot]".
        "blank": (_WINDOW_TITLE_PREFIX, ""),
    }
