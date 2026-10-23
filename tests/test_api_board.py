"""T80: one board payload, one stage write path.

``GET /api/board`` returns all five columns (new, shortlist, applied,
replied, out) in one response. ``POST /api/jobs/{job_id}/stage`` is the
single idempotent write path; the five pre-existing endpoints
(``/matches/{id}/apply``, ``/matches/{id}/remove``, ``/matches/{id}/restore``,
``/applied/{id}/unapply``) become thin wrappers over it (the product spec rule
1 — "a job is in exactly one stage").

The field list is asserted as an explicit enumeration (T62's lesson: a
single SQL omission silently killed five user-visible capabilities) rather
than "whatever keys happen to be present", so a future omission fails this
test instead of rendering blank.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from jobfinder.dto import MATCH_CARD_FIELDS
from jobfinder.matcher.models import JobPosting, MatchResult

PORT = 8761
BASE_URL = f"http://127.0.0.1:{PORT}"

STAGES = ("new", "shortlist", "applied", "replied", "out")
STAGE_COLUMN = {
    "new": "new",
    "shortlist": "shortlist",
    "applied": "applied",
    "replied": "replied",
    "out": "out",
}

# The 34 MatchCardDTO/HistoryCardDTO fields plus the five board-specific
# additions the product spec's brief enumerates: stage, stage_entered_at,
# removed_by, reply_note, tier.
BOARD_CARD_FIELDS = set(MATCH_CARD_FIELDS) | {"stage", "stage_entered_at", "removed_by", "reply_note", "tier"}


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
def client(isolated_app):
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        yield app, c


@pytest.fixture
def authed_client(client):
    app, c = client
    token = app.state.launch_token.value
    resp = c.post("/api/auth/exchange", json={"token": token})
    assert resp.status_code == 200, resp.text
    return app, c


def _seed_job(
    db,
    *,
    job_id: str,
    company: str = "Globex",
    score: int = 70,
    city: str = "",
    region: str = "",
    country: str = "",
    scan_id: str | None = None,
) -> JobPosting:
    job = JobPosting(
        job_id=job_id,
        title="QA Engineer",
        company=company,
        location="Remote",
        description="A job posting.",
        url=f"https://example.com/{job_id}",
        source="test",
        city=city,
        region=region,
        country=country,
    )
    db.mark_seen([job])
    db.save_match(MatchResult(job=job, score=score, reasoning="test reasoning", scan_id=scan_id))
    return job


def _board_by_job_id(board: dict) -> dict:
    """Flatten GET /api/board's five columns into {job_id: card}."""
    flat: dict = {}
    for column in STAGES:
        for card in board.get(column, []):
            flat[card["job_id"]] = card
    return flat


def _stage_timestamp_column(stage: str) -> tuple[str, str] | None:
    return {
        "shortlist": ("shortlisted_jobs", "shortlisted_at"),
        "applied": ("applied_jobs", "applied_at"),
        "replied": ("replied_jobs", "replied_at"),
        "out": ("removed_jobs", "removed_at"),
    }.get(stage)


def _raw_timestamp(db, stage: str, job_id: str) -> str | None:
    info = _stage_timestamp_column(stage)
    if info is None:
        return None
    table, col = info
    conn = sqlite3.connect(db._path)
    try:
        row = conn.execute(f"SELECT {col} FROM {table} WHERE job_id = ?", (job_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ── field enumeration ───────────────────────────────────────────────────


def test_board_carries_every_enumerated_field_in_every_column(authed_client) -> None:
    app, c = authed_client
    db = app.state.db

    _seed_job(db, job_id="board-field-new")
    _seed_job(db, job_id="board-field-shortlist")
    _seed_job(db, job_id="board-field-applied")
    _seed_job(db, job_id="board-field-replied")
    _seed_job(db, job_id="board-field-out")

    c.post("/api/jobs/board-field-shortlist/stage", json={"stage": "shortlist"})
    c.post("/api/jobs/board-field-applied/stage", json={"stage": "applied"})
    c.post("/api/jobs/board-field-replied/stage", json={"stage": "replied", "note": "they called back"})
    c.post("/api/jobs/board-field-out/stage", json={"stage": "out", "removed_by": "user"})

    resp = c.get("/api/board")
    assert resp.status_code == 200, resp.text
    board = resp.json()

    assert set(board.keys()) == set(STAGES)

    flat = _board_by_job_id(board)
    for job_id in (
        "board-field-new",
        "board-field-shortlist",
        "board-field-applied",
        "board-field-replied",
        "board-field-out",
    ):
        card = flat[job_id]
        missing = BOARD_CARD_FIELDS - set(card.keys())
        assert not missing, f"{job_id} card is missing fields: {missing}"


def test_board_new_column_has_no_stage_entered_at(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="board-new-null-ts")

    board = c.get("/api/board").json()
    card = next(card for card in board["new"] if card["job_id"] == "board-new-null-ts")
    assert card["stage"] == "new"
    assert card["stage_entered_at"] is None


def test_board_out_column_carries_removed_by(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="board-out-removed-by")
    c.post("/api/jobs/board-out-removed-by/stage", json={"stage": "out", "removed_by": "agent"})

    board = c.get("/api/board").json()
    card = next(card for card in board["out"] if card["job_id"] == "board-out-removed-by")
    assert card["removed_by"] == "agent"
    assert card["stage_entered_at"] is not None


def test_board_replied_column_carries_reply_note(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="board-replied-note")
    c.post("/api/jobs/board-replied-note/stage", json={"stage": "replied", "note": "interview scheduled"})

    board = c.get("/api/board").json()
    card = next(card for card in board["replied"] if card["job_id"] == "board-replied-note")
    assert card["reply_note"] == "interview scheduled"


def test_board_carries_tier_location_is_unresolved(authed_client) -> None:
    """T81: the board surfaces whether a card's location could not be
    parsed (jobfinder/filters.py's `tier_location_is_unresolved`, T84) as
    its own field, distinct from the `global` tier it sits alongside --
    the card flag that reads this needs real data, not a guess."""
    app, c = authed_client
    db = app.state.db
    _seed_job(
        db,
        job_id="board-tier-unresolved",
        city="Greater Winnipeg Metropolitan Area",
        region="",
        country="",
    )
    _seed_job(db, job_id="board-tier-resolved", city="Toronto", region="Ontario", country="Canada")

    board = c.get("/api/board").json()
    flat = _board_by_job_id(board)
    assert flat["board-tier-unresolved"]["tier_location_is_unresolved"] is True
    assert flat["board-tier-resolved"]["tier_location_is_unresolved"] is False


def test_board_carries_same_country_tier_configured_flag(authed_client) -> None:
    """T81/the product spec: the "same country" tier filter must distinguish
    "genuinely zero matches" from "not configured at all" (T84's
    `country_tier_is_configured`). Carried per-card rather than as a new
    top-level board key."""
    app, c = authed_client
    db = app.state.db
    app.state.config.location_filter.preferred_regions = []
    app.state.config.location_filter.search_country = ""
    _seed_job(db, job_id="board-same-country-unconfigured")

    board = c.get("/api/board").json()
    card = next(card for card in board["new"] if card["job_id"] == "board-same-country-unconfigured")
    assert card["same_country_tier_configured"] is False

    app.state.config.location_filter.search_country = "United States"
    _seed_job(db, job_id="board-same-country-configured")
    board = c.get("/api/board").json()
    card = next(card for card in board["new"] if card["job_id"] == "board-same-country-configured")
    assert card["same_country_tier_configured"] is True


def test_board_new_column_applies_match_threshold(authed_client) -> None:
    """T89: before this fix, ``/api/board`` never enforced
    ``match_threshold`` the way ``/api/matches`` already did (the product spec,
    principle 3) -- raising the minimum score had no effect on the
    board. Scoped to New only; see ``_list_board_sync``'s docstring."""
    app, c = authed_client
    db = app.state.db
    app.state.config.match_threshold = 50
    _seed_job(db, job_id="board-threshold-low", score=10)
    _seed_job(db, job_id="board-threshold-high", score=90)

    board = c.get("/api/board").json()
    new_ids = {card["job_id"] for card in board["new"]}
    assert "board-threshold-high" in new_ids
    assert "board-threshold-low" not in new_ids


def test_board_new_column_applies_tag_visibility(authed_client) -> None:
    """T89: ``hide_internship`` etc. were previously enforced only for
    ``/api/matches``, never ``/api/board``."""
    app, c = authed_client
    db = app.state.db
    app.state.config.tag_visibility.hide_remote = True

    job = JobPosting(
        job_id="board-tag-hidden",
        title="QA Engineer",
        company="Globex",
        location="Remote",
        description="A job posting.",
        url="https://example.com/board-tag-hidden",
        source="test",
        work_arrangement="Remote",
    )
    db.mark_seen([job])
    db.save_match(MatchResult(job=job, score=70, reasoning="test reasoning"))
    _seed_job(db, job_id="board-tag-visible")

    board = c.get("/api/board").json()
    new_ids = {card["job_id"] for card in board["new"]}
    assert "board-tag-visible" in new_ids
    assert "board-tag-hidden" not in new_ids


def test_board_non_new_columns_ignore_filters(authed_client) -> None:
    """T89: a job already acted on must not disappear retroactively just
    because the filter tightened afterwards -- only the New column is a
    candidate pool; the rest are history."""
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="board-shortlisted-low-score", score=10)
    c.post("/api/jobs/board-shortlisted-low-score/stage", json={"stage": "shortlist"})

    app.state.config.match_threshold = 50

    board = c.get("/api/board").json()
    shortlist_ids = {card["job_id"] for card in board["shortlist"]}
    assert "board-shortlisted-low-score" in shortlist_ids


def test_board_carries_server_computed_tier(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    app.state.config.location_filter.preferred_regions = ["California"]
    app.state.config.location_filter.search_country = "United States"
    _seed_job(db, job_id="board-tier-preferred", region="California", country="United States")

    board = c.get("/api/board").json()
    card = next(card for card in board["new"] if card["job_id"] == "board-tier-preferred")
    assert card["tier"] == "preferred"


# ── all 25 stage transitions, asserted through GET /api/board ──────────


@pytest.mark.parametrize("from_stage", STAGES)
@pytest.mark.parametrize("to_stage", STAGES)
def test_all_25_stage_transitions_via_api_asserted_through_board(authed_client, from_stage, to_stage) -> None:
    app, c = authed_client
    db = app.state.db
    job_id = f"transition-{from_stage}-{to_stage}"
    _seed_job(db, job_id=job_id)

    if from_stage != "new":
        resp = c.post(f"/api/jobs/{job_id}/stage", json={"stage": from_stage})
        assert resp.status_code == 200, resp.text

    resp = c.post(f"/api/jobs/{job_id}/stage", json={"stage": to_stage})
    assert resp.status_code == 200, resp.text

    board = c.get("/api/board").json()
    flat = _board_by_job_id(board)
    assert flat[job_id]["stage"] == to_stage

    # Absent from every other column.
    for other_stage in STAGES:
        if other_stage == to_stage:
            continue
        assert not any(card["job_id"] == job_id for card in board[other_stage]), (
            f"{job_id} unexpectedly present in column {other_stage!r} after moving to {to_stage!r}"
        )


@pytest.mark.parametrize("stage", ("shortlist", "applied", "replied", "out"))
def test_noop_transition_leaves_timestamp_untouched(authed_client, stage) -> None:
    app, c = authed_client
    db = app.state.db
    job_id = f"noop-api-{stage}"
    _seed_job(db, job_id=job_id)

    resp = c.post(f"/api/jobs/{job_id}/stage", json={"stage": stage})
    assert resp.status_code == 200, resp.text
    first_ts = _raw_timestamp(db, stage, job_id)
    assert first_ts is not None

    resp = c.post(f"/api/jobs/{job_id}/stage", json={"stage": stage})
    assert resp.status_code == 200, resp.text
    second_ts = _raw_timestamp(db, stage, job_id)
    assert second_ts == first_ts


# ── unknown stage ────────────────────────────────────────────────────────


def test_unknown_stage_is_4xx_naming_valid_stages(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="bad-stage-job")

    resp = c.post("/api/jobs/bad-stage-job/stage", json={"stage": "archived"})
    assert 400 <= resp.status_code < 500, resp.text
    detail = resp.json()["detail"]
    for stage in STAGES:
        assert stage in detail, f"error message should name valid stage {stage!r}: {detail}"


# ── wrapper endpoints produce the same end state as set_stage ──────────


def test_apply_wrapper_matches_direct_set_stage(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="wrapper-apply-direct")
    _seed_job(db, job_id="wrapper-apply-via-endpoint")

    db.set_stage("wrapper-apply-direct", "applied")
    resp = c.post("/api/matches/wrapper-apply-via-endpoint/apply")
    assert resp.status_code == 200, resp.text

    assert db.stage_of("wrapper-apply-direct") == db.stage_of("wrapper-apply-via-endpoint") == "applied"


def test_remove_wrapper_matches_direct_set_stage(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="wrapper-remove-direct")
    _seed_job(db, job_id="wrapper-remove-via-endpoint")

    db.set_stage("wrapper-remove-direct", "out", removed_by="user")
    resp = c.post("/api/matches/wrapper-remove-via-endpoint/remove")
    assert resp.status_code == 200, resp.text

    assert db.stage_of("wrapper-remove-direct") == db.stage_of("wrapper-remove-via-endpoint") == "out"


def test_restore_wrapper_matches_direct_set_stage(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="wrapper-restore-direct")
    _seed_job(db, job_id="wrapper-restore-via-endpoint")
    db.set_stage("wrapper-restore-direct", "out")
    db.set_stage("wrapper-restore-via-endpoint", "out")

    db.set_stage("wrapper-restore-direct", "new")
    resp = c.post("/api/matches/wrapper-restore-via-endpoint/restore")
    assert resp.status_code == 200, resp.text

    assert db.stage_of("wrapper-restore-direct") == db.stage_of("wrapper-restore-via-endpoint") == "new"


def test_unapply_wrapper_matches_direct_set_stage(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="wrapper-unapply-direct")
    _seed_job(db, job_id="wrapper-unapply-via-endpoint")
    db.set_stage("wrapper-unapply-direct", "applied")
    db.set_stage("wrapper-unapply-via-endpoint", "applied")

    db.set_stage("wrapper-unapply-direct", "new")
    resp = c.post("/api/applied/wrapper-unapply-via-endpoint/unapply")
    assert resp.status_code == 200, resp.text

    assert db.stage_of("wrapper-unapply-direct") == db.stage_of("wrapper-unapply-via-endpoint") == "new"


# ── the behaviour change the brief calls out explicitly ────────────────


def test_applying_a_previously_removed_job_un_removes_it(authed_client) -> None:
    """The product spec makes "applied and also removed" impossible.
    Before T80, ``mark_applied`` left ``removed_jobs`` untouched (a job
    could be in both). Routing ``apply`` through ``set_stage`` changes
    that on purpose: applying now also un-removes."""
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="apply-after-remove")

    resp = c.post("/api/matches/apply-after-remove/remove")
    assert resp.status_code == 200, resp.text
    assert db.stage_of("apply-after-remove") == "out"

    resp = c.post("/api/matches/apply-after-remove/apply")
    assert resp.status_code == 200, resp.text

    assert db.stage_of("apply-after-remove") == "applied"

    board = c.get("/api/board").json()
    assert not any(card["job_id"] == "apply-after-remove" for card in board["out"])
    assert any(card["job_id"] == "apply-after-remove" for card in board["applied"])


# ── T101: starring ───────────────────────────────────────────────────────


def test_board_card_carries_starred_field_default_false(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="star-default-false")

    board = c.get("/api/board").json()
    flat = _board_by_job_id(board)
    assert flat["star-default-false"]["starred"] is False


def test_star_endpoint_sets_starred_true_on_the_board(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="star-set-true")

    resp = c.post("/api/jobs/star-set-true/star", json={"starred": True})
    assert resp.status_code == 200, resp.text

    board = c.get("/api/board").json()
    flat = _board_by_job_id(board)
    assert flat["star-set-true"]["starred"] is True


def test_star_endpoint_toggles_back_to_false(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="star-toggle-off")

    resp = c.post("/api/jobs/star-toggle-off/star", json={"starred": True})
    assert resp.status_code == 200, resp.text
    resp = c.post("/api/jobs/star-toggle-off/star", json={"starred": False})
    assert resp.status_code == 200, resp.text

    board = c.get("/api/board").json()
    flat = _board_by_job_id(board)
    assert flat["star-toggle-off"]["starred"] is False


def test_starring_survives_a_reload_asserted_through_board(authed_client) -> None:
    """GET /api/board is called twice, like a page reload, with no write
    in between -- the star must still be there the second time."""
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="star-survives-reload")

    resp = c.post("/api/jobs/star-survives-reload/star", json={"starred": True})
    assert resp.status_code == 200, resp.text

    board_1 = c.get("/api/board").json()
    board_2 = c.get("/api/board").json()
    assert _board_by_job_id(board_1)["star-survives-reload"]["starred"] is True
    assert _board_by_job_id(board_2)["star-survives-reload"]["starred"] is True


def test_starring_survives_a_stage_move_asserted_through_board(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="star-survives-move")

    resp = c.post("/api/jobs/star-survives-move/star", json={"starred": True})
    assert resp.status_code == 200, resp.text

    resp = c.post("/api/jobs/star-survives-move/stage", json={"stage": "shortlist"})
    assert resp.status_code == 200, resp.text

    board = c.get("/api/board").json()
    flat = _board_by_job_id(board)
    assert flat["star-survives-move"]["stage"] == "shortlist"
    assert flat["star-survives-move"]["starred"] is True, "a stage move must not clear the star"


def test_starring_does_not_affect_stage_of_or_set_stage_via_api(authed_client) -> None:
    """Star a job, move it, star it again -- the stage machinery must
    behave identically to an unstarred job at every step, asserted
    through the same API surface a real client uses."""
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="star-vs-stage-api-starred")
    _seed_job(db, job_id="star-vs-stage-api-plain")

    assert db.stage_of("star-vs-stage-api-starred") == db.stage_of("star-vs-stage-api-plain") == "new"

    c.post("/api/jobs/star-vs-stage-api-starred/star", json={"starred": True})
    assert db.stage_of("star-vs-stage-api-starred") == db.stage_of("star-vs-stage-api-plain")

    c.post("/api/jobs/star-vs-stage-api-starred/stage", json={"stage": "applied"})
    c.post("/api/jobs/star-vs-stage-api-plain/stage", json={"stage": "applied"})
    assert db.stage_of("star-vs-stage-api-starred") == db.stage_of("star-vs-stage-api-plain") == "applied"

    c.post("/api/jobs/star-vs-stage-api-starred/star", json={"starred": True})
    assert db.stage_of("star-vs-stage-api-starred") == db.stage_of("star-vs-stage-api-plain") == "applied"


def test_clear_all_via_scan_clears_starred_jobs(authed_client) -> None:
    """jobfinder/web/scan.py's factory-reset path calls `db.clear_all()`
    directly (no dedicated HTTP endpoint exists for it) -- asserted here
    at the `Database` level through the same `app.state.db` the API
    routes use, so this stays an API-surface test rather than a
    `tests/test_pipeline_stages.py` duplicate."""
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="star-clear-all")
    c.post("/api/jobs/star-clear-all/star", json={"starred": True})

    board = c.get("/api/board").json()
    assert _board_by_job_id(board)["star-clear-all"]["starred"] is True

    db.clear_all()

    assert db.get_starred_job_ids() == set()
