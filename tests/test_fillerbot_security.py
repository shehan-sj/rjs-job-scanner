"""Security regression tests for FillerBot (T32).

No live job site and no real Anthropic key is used anywhere here:
- browser.py tests use lightweight fakes for Page/Locator/subprocess, or a
  local ``file://`` fixture page served from a temp HTML file.
- vision.py tests monkeypatch ``anthropic.Anthropic`` entirely.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from jobfinder.fillerbot import browser as browser_mod
from jobfinder.fillerbot import vision as vision_mod
from jobfinder.fillerbot.browser import FillerBrowser
from jobfinder.fillerbot.models import FillAction, FillerProfile
from jobfinder.fillerbot.vision import PageAnalyzer


def run(coro):
    return asyncio.run(coro)


# ── Fakes for unit-testing FillerBrowser.execute() without a real page ──────


class FakeLocator:
    def __init__(self, calls: list):
        self._calls = calls

    async def scroll_into_view_if_needed(self, timeout=None):
        return None

    async def set_input_files(self, path):
        self._calls.append(("set_input_files", path))

    async def click(self, force=False):
        self._calls.append(("click", None))

    async def fill(self, value):
        self._calls.append(("fill", value))


class FakePage:
    def __init__(self, calls: list):
        self._calls = calls

    def locator(self, selector):
        return FakeLocator(self._calls)

    def get_by_label(self, *a, **k):
        return FakeLocator(self._calls)

    def get_by_placeholder(self, *a, **k):
        return FakeLocator(self._calls)

    def get_by_text(self, *a, **k):
        calls = self._calls

        class _First:
            first = FakeLocator(calls)

        return _First()

    async def evaluate(self, *a, **k):
        return None


def _browser_with_fake_page() -> tuple[FillerBrowser, list]:
    b = FillerBrowser(chrome_exe="fake-chrome")
    calls: list = []
    b._page = FakePage(calls)  # noqa: SLF001 — intentional unit-test seam
    return b, calls


# ── Fix 1: malicious action.value must never reach set_input_files ─────────


def test_upload_never_uses_action_value_as_path(tmp_path):
    """A malicious action.value must never be passed to set_input_files,
    even when a legitimate resume_path is also available."""
    browser, calls = _browser_with_fake_page()
    real_resume = tmp_path / "resume.pdf"
    real_resume.write_bytes(b"%PDF-1.4 fake resume")

    malicious_action = FillAction(
        action_type="upload",
        selector_strategy="id",
        selector_value="resume-upload",
        value="C:/Users/victim/.ssh/id_rsa",  # attacker-controlled page content
        field_label="Resume",
    )

    ok = run(browser.execute(malicious_action, resume_path=str(real_resume)))

    assert ok is True
    upload_calls = [c for c in calls if c[0] == "set_input_files"]
    assert upload_calls == [("set_input_files", str(real_resume))]
    assert all(path != "C:/Users/victim/.ssh/id_rsa" for _, path in upload_calls)


def test_upload_with_no_resume_fails_clearly_instead_of_uploading_value():
    """With no resume available, the upload action must fail — never fall
    back to action.value as an upload source."""
    browser, calls = _browser_with_fake_page()

    malicious_action = FillAction(
        action_type="upload",
        selector_strategy="id",
        selector_value="resume-upload",
        value="/etc/passwd",
        field_label="Resume",
    )

    ok = run(browser.execute(malicious_action, resume_path=""))

    assert ok is False
    assert not any(c[0] == "set_input_files" for c in calls)


def test_upload_to_file_input_rejects_missing_resume():
    """upload_to_file_input() (the other upload path, used for vision-found
    upload buttons) also refuses to proceed without a real resume file."""
    browser, calls = _browser_with_fake_page()

    ok = run(browser.upload_to_file_input("", index=0))

    assert ok is False
    assert calls == []


# ── Fix 4: submit-shaped actions are rejected server-side ──────────────────


def test_submit_shaped_click_is_rejected_even_if_model_returns_it():
    browser, calls = _browser_with_fake_page()

    submit_action = FillAction(
        action_type="click",
        selector_strategy="text",
        selector_value="Submit Application",
        value="",
        field_label="Submit",
    )

    ok = run(browser.execute(submit_action))

    assert ok is False
    assert calls == []  # locator.click() must never have been invoked


def test_non_submit_click_is_not_blocked():
    """Sanity check: the submit guard doesn't block ordinary clicks like
    'Next' / 'Continue' pagination buttons."""
    browser, calls = _browser_with_fake_page()

    next_action = FillAction(
        action_type="click",
        selector_strategy="text",
        selector_value="Continue",
        value="",
        field_label="Next page",
    )

    ok = run(browser.execute(next_action))

    assert ok is True
    assert ("click", None) in calls


@pytest.mark.parametrize(
    "label,selector_value",
    [
        ("Submit", ""),
        ("", "Submit Application"),
        ("Send Application", ""),
        ("Finish Application", ""),
        ("Apply Now", ""),
    ],
)
def test_looks_like_submit_matches_known_shapes(label, selector_value):
    action = FillAction(
        action_type="click",
        selector_strategy="text",
        selector_value=selector_value,
        field_label=label,
    )
    assert browser_mod._looks_like_submit(action) is True


# ── Fix 2: CDP bound to 127.0.0.1 ───────────────────────────────────────────


def test_launch_binds_cdp_to_loopback_address(monkeypatch):
    """Assert the launch arguments explicitly bind CDP to 127.0.0.1, and
    that the probed/attached port matches what was launched with."""
    captured = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args

    def fake_create_connection(addr, timeout=None):
        class _Sock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Sock()

    class FakeBrowserObj:
        contexts = []

        async def new_context(self, **kwargs):
            class _Ctx:
                pages = []

                async def new_page(self):
                    class _Page:
                        def set_default_timeout(self, *_a, **_k):
                            return None

                    return _Page()

            return _Ctx()

    class FakeChromium:
        async def connect_over_cdp(self, url):
            captured["cdp_url"] = url
            return FakeBrowserObj()

    class FakePW:
        chromium = FakeChromium()

        async def stop(self):
            return None

    class FakePlaywrightCM:
        async def start(self):
            return FakePW()

    monkeypatch.setattr(browser_mod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(browser_mod.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(browser_mod, "async_playwright", lambda: FakePlaywrightCM())

    b = FillerBrowser(chrome_exe="fake-chrome.exe")
    run(b.launch())

    args = captured["args"]
    assert "--remote-debugging-address=127.0.0.1" in args
    port_args = [a for a in args if a.startswith("--remote-debugging-port=")]
    assert len(port_args) == 1
    port = port_args[0].split("=", 1)[1]
    assert captured["cdp_url"] == f"http://127.0.0.1:{port}"


# ── Fix 3: ephemeral profile directory is removed after the session ────────


def test_profile_directory_removed_after_close():
    b = FillerBrowser(chrome_exe="fake-chrome.exe")
    profile_dir = tempfile.mkdtemp(prefix="fillerbot-profile-test-")
    (Path(profile_dir) / "Default" / "cookies").parent.mkdir(parents=True, exist_ok=True)
    (Path(profile_dir) / "Default" / "cookies").write_text("fake cookie jar")
    b._profile_dir = profile_dir  # noqa: SLF001 — simulate post-launch state

    assert Path(profile_dir).exists()
    run(b.close())
    assert not Path(profile_dir).exists()


# ── password values stripped from what's sent to the vision call ─────


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self):
        self.last_call_kwargs = None

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        return _FakeResponse(
            '{"page_state": "form", "page_description": "", "actions": [], "next_step": "fill", "human_note": ""}'
        )


class _FakeAnthropicClient:
    def __init__(self, *a, **k):
        self.messages = _FakeMessages()


def test_password_field_values_excluded_from_vision_payload(monkeypatch):
    monkeypatch.setattr(vision_mod.anthropic, "Anthropic", _FakeAnthropicClient)

    analyzer = PageAnalyzer(api_key="fake-key", model="fake-model", profile=FillerProfile())
    dom_fields = [
        {
            "tag": "input",
            "type": "password",
            "id": "pwd",
            "name": "password",
            "label": "Password",
            "placeholder": "",
            "currentValue": "SuperSecret123!",
        },
        {
            "tag": "input",
            "type": "text",
            "id": "email",
            "name": "email",
            "label": "Email",
            "placeholder": "",
            "currentValue": "jane@example.com",
        },
    ]

    analyzer.analyze(screenshot_png=b"fake-png-bytes", dom_fields=dom_fields)

    sent_kwargs = analyzer._client.messages.last_call_kwargs
    user_content = sent_kwargs["messages"][0]["content"]
    text_blocks = [c["text"] for c in user_content if c.get("type") == "text"]
    full_text = "\n".join(text_blocks)

    assert "SuperSecret123!" not in full_text
    assert "value=(redacted)" in full_text
    # the non-sensitive field's value should still be present
    assert "jane@example.com" in full_text


def test_screenshot_uses_viewport_not_full_page(monkeypatch):
    """Prefer a viewport capture over a full-page capture."""
    b = FillerBrowser(chrome_exe="fake-chrome.exe")
    captured = {}

    class FakePage:
        async def screenshot(self, **kwargs):
            captured.update(kwargs)
            return b"fake-png"

    b._page = FakePage()
    run(b.screenshot())

    assert captured.get("full_page") is False
