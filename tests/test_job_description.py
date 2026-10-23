"""Tests for storing and serving a posting's own text.

The job detail panel's whole purpose is reading one job, and it could not
show the job: ``description`` was scraped on every scan, used by
``local_matcher.score_job`` as its scoring haystack, and then discarded --
``seen_jobs`` had no column for it. The wide-dossier redesign makes the
posting the left column, so it is stored now.

Deliberately NOT on the card DTOs: ``GET /api/board`` returns every card in
all five stages, and a description runs to several KB. ``GET
/api/jobs/{job_id}/description`` fetches it when a panel actually opens.
These tests pin both halves of that -- it is persisted and served, and it
stays out of the board payload.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from jobfinder.matcher.models import JobPosting, MatchResult
from jobfinder.storage.database import Database

PORT = 8773
BASE_URL = f"http://127.0.0.1:{PORT}"

BODY = (
    "We are looking for a Senior Quality Engineer to own our automation "
    "platform. You will work with Playwright, CI pipelines and a large "
    "regression suite.\n\nRequirements: 5+ years in test automation."
)


def _job(job_id: str = "desc-1", *, description: str = BODY) -> JobPosting:
    return JobPosting(
        job_id=job_id,
        title="Senior Quality Engineer",
        company="Upshop",
        location="Greater Toronto Area",
        description=description,
        url=f"https://example.com/{job_id}",
        source="linkedin",
    )


# ── Storage ──────────────────────────────────────────────────────────────


def test_mark_seen_persists_the_description(tmp_path) -> None:
    db = Database(str(tmp_path / "jobs.db"))
    db.mark_seen([_job()])

    assert db.get_job_description("desc-1") == BODY


def test_an_unknown_job_has_no_description(tmp_path) -> None:
    db = Database(str(tmp_path / "jobs.db"))
    assert db.get_job_description("nope") is None


def test_a_blank_description_reads_back_as_none(tmp_path) -> None:
    """Whitespace is not content -- the panel should say "no description",
    not render an empty box."""
    db = Database(str(tmp_path / "jobs.db"))
    db.mark_seen([_job("blank-1", description="   \n  ")])

    assert db.get_job_description("blank-1") is None


def test_a_database_written_before_the_column_existed_still_opens(tmp_path) -> None:
    """The migration is additive: an existing install must keep working and
    simply report no stored description until its next scan."""
    path = tmp_path / "jobs.db"
    db = Database(str(path))
    db.mark_seen([_job("old-1")])

    # Simulate the pre-migration shape by dropping the value, which is what
    # a row inserted before the ALTER TABLE looks like. (``Database`` owns
    # one connection per unit of work and closes it itself, so there is
    # nothing to close here first.)
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE seen_jobs SET description = NULL WHERE job_id = 'old-1'")
    conn.commit()
    conn.close()

    reopened = Database(str(path))
    assert reopened.get_job_description("old-1") is None
    # ...and the row itself is intact: the job is still known.
    assert reopened.filter_unseen([_job("old-1")]) == []


def test_the_description_survives_a_reopen(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    Database(str(path)).mark_seen([_job("persist-1")])

    assert Database(str(path)).get_job_description("persist-1") == BODY


# ── The endpoint ─────────────────────────────────────────────────────────


@pytest.fixture
def authed_client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))

    import jobfinder.paths as paths

    importlib.reload(paths)

    import jobfinder.web.app as web_app

    importlib.reload(web_app)

    app = web_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
        assert resp.status_code == 200, resp.text
        yield app, client

    importlib.reload(paths)


def _seed(app, job: JobPosting, *, score: int = 62) -> None:
    db: Database = app.state.db
    db.mark_seen([job])
    db.save_match(MatchResult(job=job, score=score, reasoning="seed"))
    # The board's New column is filtered by the live config (T89), and the
    # default threshold is 65 -- without this a score-62 seed is filtered
    # out and the board comes back empty.
    app.state.config.match_threshold = 0


def test_endpoint_returns_the_stored_description(authed_client) -> None:
    app, client = authed_client
    _seed(app, _job())

    resp = client.get("/api/jobs/desc-1/description")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"description": BODY, "stored": True}


def test_endpoint_reports_a_job_with_nothing_stored(authed_client) -> None:
    """200 with ``stored: false``, not a 404 -- "there is no text to show"
    is an answer, and a 404 would make the panel's fetch look broken."""
    app, client = authed_client

    resp = client.get("/api/jobs/never-scanned/description")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"description": None, "stored": False}


def test_the_board_payload_does_not_carry_descriptions(authed_client) -> None:
    """The reason this is a separate endpoint at all. A board response holds
    every card in all five stages; descriptions belong nowhere near it."""
    app, client = authed_client
    _seed(app, _job())

    board = client.get("/api/board").json()
    cards = [card for stage_cards in board.values() for card in stage_cards]

    assert cards, "expected the seeded job on the board"
    for card in cards:
        assert "description" not in card, "a description leaked into the board payload"


def test_a_long_description_is_served_whole(authed_client) -> None:
    """Fetched one job at a time, so there is no reason to truncate it --
    and a silently cut-off posting would be worse than no posting."""
    app, client = authed_client
    long_body = "Responsibilities. " * 2000
    _seed(app, _job("long-1", description=long_body))

    body = client.get("/api/jobs/long-1/description").json()

    assert body["stored"] is True
    assert body["description"] == long_body
