"""Tests for the onboarding endpoints added in T26.

T26 shipped `jobfinder/web/onboarding.py` with three routes and no automated
coverage — it was verified by hand against a TestClient. These tests make that
permanent. The Anthropic client is mocked throughout: `test-key` makes a real
round trip in production, and a test that needed a valid key plus network would
cost money on every run and fail on a machine without one.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from jobfinder import paths
from jobfinder.web.app import create_app

PORT = 8756
CANARY_KEY = "sk-ant-api03-CANARY" + "q7Xz" * 20


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"check_interval_minutes": 20, "match_threshold": 65}, sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def client(data_dir):
    app = create_app(port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
        yield c


def test_status_reports_needs_onboarding_on_a_bare_config(client):
    """No API key and no resume is the fresh-install shape main.py checks for."""
    r = client.get("/api/onboarding/status")
    assert r.status_code == 200
    assert r.json()["needs_onboarding"] is True


def test_test_key_rejects_an_empty_key_without_calling_the_api(client):
    with patch("anthropic.Anthropic") as anthropic_cls:
        r = client.post("/api/onboarding/test-key", json={"api_key": ""})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    anthropic_cls.assert_not_called()


def test_test_key_reports_success_for_a_working_key(client):
    with patch("anthropic.Anthropic") as anthropic_cls:
        anthropic_cls.return_value = MagicMock()
        r = client.post("/api/onboarding/test-key", json={"api_key": CANARY_KEY})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["error"] is None


def test_test_key_never_echoes_the_key_back_on_failure(client):
    """SDK errors can carry request detail, so the response is redacted."""
    boom = RuntimeError(f"401 unauthorized for key {CANARY_KEY}")
    with patch("anthropic.Anthropic") as anthropic_cls:
        anthropic_cls.return_value.messages.create.side_effect = boom
        r = client.post("/api/onboarding/test-key", json={"api_key": CANARY_KEY})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert CANARY_KEY not in r.text
    assert "sk-ant-" not in r.text


def test_test_key_does_not_persist_the_key(client, data_dir):
    """The key only reaches disk when the wizard finishes, via PUT /api/settings."""
    with patch("anthropic.Anthropic") as anthropic_cls:
        anthropic_cls.return_value = MagicMock()
        client.post("/api/onboarding/test-key", json={"api_key": CANARY_KEY})

    for path in data_dir.rglob("*"):
        if path.is_file():
            assert CANARY_KEY not in path.read_text(encoding="utf-8", errors="ignore"), path


def test_acknowledge_disclosure_records_a_timestamp(client):
    r = client.post("/api/onboarding/acknowledge-disclosure")
    assert r.status_code == 200
    assert r.json()["acknowledged"] is True

    marker = paths.onboarding_disclosure_path()
    assert marker.exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["acknowledged"] is True


def test_acknowledging_the_disclosure_does_not_satisfy_onboarding(client):
    """A user who quits before configuring must still see the wizard again."""
    client.post("/api/onboarding/acknowledge-disclosure")
    assert client.get("/api/onboarding/status").json()["needs_onboarding"] is True


def test_complete_sticks_even_with_no_api_key(client):
    """T61's defect: finishing without a key must not be shown the wizard
    again next load. Before the fix, `needs_onboarding` only ever asked
    "is there a key and a resume" and had no memory of a completed run."""
    r = client.post("/api/onboarding/complete")
    assert r.status_code == 200
    assert r.json()["onboarding_completed"] is True

    assert client.get("/api/onboarding/status").json()["needs_onboarding"] is False


def test_complete_persists_across_a_reload(client, data_dir):
    """The flag must actually reach config.yaml, not just the in-memory
    config this TestClient session holds — a real restart re-reads disk."""
    client.post("/api/onboarding/complete")

    on_disk = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))
    assert on_disk["onboarding_completed"] is True

    from jobfinder.config import load_config

    reloaded = load_config(data_dir / "config.yaml")
    assert reloaded.onboarding_completed is True


def test_a_fresh_install_still_sees_onboarding(client):
    """The completed flag must never be assumed true — only setting it
    explicitly clears the gate. A fresh install (no key, no resume, flag
    defaulted False) is exactly the case the wizard exists for."""
    assert client.get("/api/onboarding/status").json()["needs_onboarding"] is True


def test_an_already_configured_install_is_never_trapped(data_dir):
    """Back-compat: an install from before T61 has `onboarding_completed`
    absent from its config.yaml entirely, not merely `False`. A key and a
    resume on disk must still clear the gate exactly as before this task,
    with no need to ever call /complete."""
    import yaml as _yaml

    resume_path = data_dir / "resume.pdf"
    resume_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    (data_dir / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "check_interval_minutes": 20,
                "match_threshold": 65,
                "anthropic": {"api_key": "sk-ant-already-configured"},
                "resume": {"path": str(resume_path)},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    app = create_app(port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
        assert c.get("/api/onboarding/status").json()["needs_onboarding"] is False


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/onboarding/status"),
        ("post", "/api/onboarding/test-key"),
        ("post", "/api/onboarding/acknowledge-disclosure"),
        ("post", "/api/onboarding/complete"),
    ],
)
def test_onboarding_routes_require_the_session_cookie(data_dir, method, path):
    app = create_app(port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as bare:
        kwargs = {"json": {}} if method == "post" else {}
        r = getattr(bare, method)(path, **kwargs)
    assert r.status_code == 401
