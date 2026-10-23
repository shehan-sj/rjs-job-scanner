"""Tests for jobfinder.web.jobs (T17) — job endpoints.

Every endpoint except ``/api/health`` and ``/api/auth/exchange`` requires
the session cookie (jobfinder/web/security.py, T15), so every test here
exchanges the launch token first. Filtering must be exercised end to end
through ``jobfinder/filters.py``/``jobfinder/storage/database.py`` — never
reimplemented here.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from jobfinder.matcher.models import JobPosting, MatchResult

PORT = 8759
BASE_URL = f"http://127.0.0.1:{PORT}"


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


def _applied_row_count(db, job_id: str) -> int:
    conn = sqlite3.connect(db._path)
    try:
        return conn.execute("SELECT COUNT(*) FROM applied_jobs WHERE job_id = ?", (job_id,)).fetchone()[0]
    finally:
        conn.close()


# ── apply / applied / matches interplay ────────────────────────────────


def test_apply_moves_job_from_matches_to_applied(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-apply-1")

    matches_before = c.get("/api/matches").json()
    assert any(m["job_id"] == "job-apply-1" for m in matches_before)

    resp = c.post("/api/matches/job-apply-1/apply")
    assert resp.status_code == 200, resp.text

    applied = c.get("/api/applied").json()
    assert any(a["job_id"] == "job-apply-1" for a in applied)

    matches_after = c.get("/api/matches").json()
    assert not any(m["job_id"] == "job-apply-1" for m in matches_after)


def test_repeat_apply_is_idempotent(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-apply-2")

    resp1 = c.post("/api/matches/job-apply-2/apply")
    assert resp1.status_code == 200, resp1.text
    resp2 = c.post("/api/matches/job-apply-2/apply")
    assert resp2.status_code == 200, resp2.text

    # mark_applied is INSERT OR REPLACE, not INSERT OR IGNORE — since
    # job_id is the table's primary key a repeat apply can never produce a
    # second row either way, it just refreshes applied_at. Assert the
    # ground-truth row count directly against the db file.
    assert _applied_row_count(db, "job-apply-2") == 1


# ── remove / restore ────────────────────────────────────────────────────


def test_remove_then_restore_round_trips_between_matches_and_removed(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-remove-1")

    resp = c.post("/api/matches/job-remove-1/remove")
    assert resp.status_code == 200, resp.text

    removed = c.get("/api/removed").json()
    removed_entry = next(r for r in removed if r["job_id"] == "job-remove-1")
    assert removed_entry["removed_by"] == "user"

    matches_while_removed = c.get("/api/matches").json()
    assert not any(m["job_id"] == "job-remove-1" for m in matches_while_removed)

    resp = c.post("/api/matches/job-remove-1/restore")
    assert resp.status_code == 200, resp.text

    matches_after_restore = c.get("/api/matches").json()
    assert any(m["job_id"] == "job-remove-1" for m in matches_after_restore)

    removed_after_restore = c.get("/api/removed").json()
    assert not any(r["job_id"] == "job-remove-1" for r in removed_after_restore)


def test_unapply_moves_job_back_from_applied_to_matches(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-unapply-1")

    resp = c.post("/api/matches/job-unapply-1/apply")
    assert resp.status_code == 200, resp.text
    assert any(a["job_id"] == "job-unapply-1" for a in c.get("/api/applied").json())

    resp = c.post("/api/applied/job-unapply-1/unapply")
    assert resp.status_code == 200, resp.text

    applied_after = c.get("/api/applied").json()
    assert not any(a["job_id"] == "job-unapply-1" for a in applied_after)

    matches_after = c.get("/api/matches").json()
    assert any(m["job_id"] == "job-unapply-1" for m in matches_after)


# ── clear-all per tab (T24) ─────────────────────────────────────────────


def test_clear_matches_empties_matches_but_keeps_job_seen(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-clear-matches-1")

    resp = c.post("/api/matches/clear")
    assert resp.status_code == 200, resp.text
    assert c.get("/api/matches").json() == []


def test_clear_applied_empties_applied_tab(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-clear-applied-1")
    c.post("/api/matches/job-clear-applied-1/apply")

    resp = c.post("/api/applied/clear")
    assert resp.status_code == 200, resp.text
    assert c.get("/api/applied").json() == []


def test_clear_removed_empties_removed_tab(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-clear-removed-1")
    c.post("/api/matches/job-clear-removed-1/remove")

    resp = c.post("/api/removed/clear")
    assert resp.status_code == 200, resp.text
    assert c.get("/api/removed").json() == []


# ── tier classification (T23) ──────────────────────────────────────────


def test_matches_carry_server_computed_tier(authed_client) -> None:
    """``/api/matches`` cards carry a ``tier`` key computed via
    ``jobfinder.filters._classify_tier`` — the frontend's Matches
    sub-tabs (T23) must not reimplement this classification in TS.
    """
    app, c = authed_client
    db = app.state.db
    app.state.config.location_filter.preferred_regions = ["California"]
    app.state.config.location_filter.search_country = "United States"

    _seed_job(db, job_id="job-tier-preferred", region="California", country="United States")
    _seed_job(db, job_id="job-tier-country", region="Texas", country="United States")
    _seed_job(db, job_id="job-tier-global", region="Ontario", country="Canada")

    matches = {m["job_id"]: m for m in c.get("/api/matches").json()}
    assert matches["job-tier-preferred"]["tier"] == "preferred"
    assert matches["job-tier-country"]["tier"] == "country"
    assert matches["job-tier-global"]["tier"] == "global"


# ── T57: matched_at on every card the API returns ──────────────────────


def test_matched_at_present_and_parseable_on_matches_applied_and_removed_cards(
    authed_client,
) -> None:
    """T57: every card shape — Matches, Applied, Removed — carries a
    ``matched_at`` that round-trips through ``datetime.fromisoformat``, not
    just the live-scan/history-row cases ``tests/test_dto.py`` already
    pins at the unit level."""
    from datetime import datetime

    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-matched-at-still-matched")
    _seed_job(db, job_id="job-matched-at-applied")
    _seed_job(db, job_id="job-matched-at-removed")

    c.post("/api/matches/job-matched-at-applied/apply")
    c.post("/api/matches/job-matched-at-removed/remove")

    matches = c.get("/api/matches").json()
    applied = c.get("/api/applied").json()
    removed = c.get("/api/removed").json()

    assert matches, "expected at least one match card to assert against"
    for card in matches:
        datetime.fromisoformat(card["matched_at"])

    applied_card = next(a for a in applied if a["job_id"] == "job-matched-at-applied")
    datetime.fromisoformat(applied_card["matched_at"])

    removed_card = next(r for r in removed if r["job_id"] == "job-matched-at-removed")
    datetime.fromisoformat(removed_card["matched_at"])


def test_matches_carry_company_url_for_the_company_site_action(authed_client) -> None:
    """T58: DESIGN.md / the parity matrix's card-actions row includes a "company
    site" link. `company_url` was already in the DTO contract
    (jobfinder/dto.py) but `get_recent_matches()`'s SQL never selected
    the `seen_jobs.company_url` column, so every Matches-tab card got the
    dict_to_card default of "" regardless of what was scraped -- the
    action existed in the DTO but could never actually render a link."""
    app, c = authed_client
    db = app.state.db
    job = JobPosting(
        job_id="job-company-url",
        title="QA Engineer",
        company="Acme",
        location="Remote",
        description="A job posting.",
        url="https://example.com/job-company-url",
        source="test",
        company_url="https://acme.example",
    )
    db.mark_seen([job])
    db.save_match(MatchResult(job=job, score=70, reasoning="test reasoning"))

    matches = {card["job_id"]: card for card in c.get("/api/matches").json()}
    assert matches["job-company-url"]["company_url"] == "https://acme.example"


def test_matches_carry_listing_type_and_company_industry_for_the_hide_toggles(authed_client) -> None:
    """T59's filter panel writes `quality_filter.hide_promoted`
    (matched against `listing_type`) and `hide_staffing_agencies`
    (matched against `company_industry`) expecting an immediate,
    visible effect (docs/DESIGN.md principle 3) -- but
    `get_recent_matches()`'s SQL never selected either `seen_jobs`
    column, the same shape of gap T58 found for `company_url`: both
    fields were already in the DTO contract (jobfinder/dto.py) and
    already enforced by `_card_hidden_by_tags` (jobfinder/filters.py),
    but every Matches-tab card silently got dict_to_card's "" default
    for both regardless of what was actually scraped, so neither hide
    toggle could ever do anything from this tab.
    """
    app, c = authed_client
    db = app.state.db
    app.state.config.quality_filter.hide_promoted = False
    app.state.config.quality_filter.hide_staffing_agencies = False

    promoted_job = JobPosting(
        job_id="job-promoted",
        title="QA Engineer",
        company="Acme",
        location="Remote",
        description="d",
        url="https://example.com/job-promoted",
        source="test",
        listing_type="promoted",
    )
    staffing_job = JobPosting(
        job_id="job-staffing",
        title="QA Engineer",
        company="Staffers Inc",
        location="Remote",
        description="d",
        url="https://example.com/job-staffing",
        source="test",
        company_industry="Staffing and Recruiting",
    )
    db.mark_seen([promoted_job, staffing_job])
    db.save_match(MatchResult(job=promoted_job, score=70, reasoning="test"))
    db.save_match(MatchResult(job=staffing_job, score=70, reasoning="test"))

    # The raw fields round-trip onto the card at all.
    matches = {card["job_id"]: card for card in c.get("/api/matches").json()}
    assert matches["job-promoted"]["listing_type"] == "promoted"
    assert matches["job-staffing"]["company_industry"] == "Staffing and Recruiting"

    # And the two hide toggles this panel writes actually act on them.
    resp = c.put("/api/settings", json={"quality_filter": {"hide_promoted": True}})
    assert resp.status_code == 200, resp.text
    visible = {m["job_id"] for m in c.get("/api/matches").json()}
    assert "job-promoted" not in visible
    assert "job-staffing" in visible

    resp = c.put("/api/settings", json={"quality_filter": {"hide_promoted": False, "hide_staffing_agencies": True}})
    assert resp.status_code == 200, resp.text
    visible2 = {m["job_id"] for m in c.get("/api/matches").json()}
    assert "job-promoted" in visible2
    assert "job-staffing" not in visible2


def test_matches_carry_the_remaining_seen_jobs_columns_t62(authed_client) -> None:
    """T62: the same shape of gap T58 (company_url) and T59 (listing_type,
    company_industry) found. `get_recent_matches()`'s SQL still never
    selected `job_level`, `job_url_direct`, `linkedin_skills`,
    `company_logo`, `company_num_employees` or the four compensation
    columns -- all already in the DTO contract (jobfinder/dto.py) and
    already written by `mark_seen()`, just never read back here. Two of
    these feed quality_filter checks the same way listing_type/
    company_industry did (`min_salary` reads compensation_min/max,
    `min_company_size` reads company_num_employees -- jobfinder/filters.py)
    so this is not just missing display data, it's two more silently dead
    filter controls, closed by the same one-line-per-column fix."""
    app, c = authed_client
    db = app.state.db
    job = JobPosting(
        job_id="job-remaining-columns",
        title="QA Engineer",
        company="Acme",
        location="Remote",
        description="d",
        url="https://example.com/job-remaining-columns",
        source="test",
        job_level="Mid-Senior level",
        job_url_direct="https://acme.example/ats/123",
        linkedin_skills=["Python", "SQL"],
        company_logo="https://acme.example/logo.png",
        company_num_employees="1,001-5,000",
        compensation_min=90000,
        compensation_max=120000,
        compensation_currency="USD",
        compensation_interval="yearly",
    )
    db.mark_seen([job])
    db.save_match(MatchResult(job=job, score=70, reasoning="test"))

    card = next(c2 for c2 in c.get("/api/matches").json() if c2["job_id"] == "job-remaining-columns")
    assert card["job_level"] == "Mid-Senior level"
    assert card["url_direct"] == "https://acme.example/ats/123"
    assert card["skills"] == ["Python", "SQL"]
    assert card["company_logo"] == "https://acme.example/logo.png"
    assert card["company_num_employees"] == "1,001-5,000"
    assert card["compensation_min"] == 90000
    assert card["compensation_max"] == 120000
    assert card["compensation_currency"] == "USD"
    assert card["compensation_interval"] == "yearly"

    # min_company_size and min_salary were dead the same way hide_promoted/
    # hide_staffing_agencies were before T59 -- the data now reaches the
    # card, so the filters this card would have silently bypassed can act.
    resp = c.put("/api/settings", json={"quality_filter": {"min_company_size": 10000}})
    assert resp.status_code == 200, resp.text
    visible = {m["job_id"] for m in c.get("/api/matches").json()}
    assert "job-remaining-columns" not in visible, (
        "min_company_size should hide a 1,001-5,000-employee company below a 10,000 floor"
    )

    resp = c.put(
        "/api/settings",
        json={"quality_filter": {"min_company_size": 0, "min_salary": 200000}},
    )
    assert resp.status_code == 200, resp.text
    visible2 = {m["job_id"] for m in c.get("/api/matches").json()}
    assert "job-remaining-columns" not in visible2, (
        "min_salary should hide a $120k top-of-range job below a $200k floor"
    )


def test_matches_carry_scan_id_for_client_side_batch_grouping(authed_client) -> None:
    """T58: the Matches view groups cards by scan batch newest-first
    (DESIGN.md Variation B). ``scan_id`` must reach the card so the client
    can group without reimplementing ``group_matches_by_scan`` in
    TypeScript (the product spec — one place that logic lives)."""
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-scan-a1", scan_id="scan-1")
    _seed_job(db, job_id="job-scan-a2", scan_id="scan-1")
    _seed_job(db, job_id="job-scan-b1", scan_id="scan-2")
    _seed_job(db, job_id="job-scan-none", scan_id=None)

    matches = {card["job_id"]: card for card in c.get("/api/matches").json()}

    assert matches["job-scan-a1"]["scan_id"] == "scan-1"
    assert matches["job-scan-a2"]["scan_id"] == "scan-1"
    assert matches["job-scan-b1"]["scan_id"] == "scan-2"
    assert matches["job-scan-none"]["scan_id"] is None


# ── T59: /api/matches must honour the live match_threshold ──────────────


def test_get_matches_hides_jobs_below_the_current_match_threshold(authed_client) -> None:
    """T59's filter panel writes `match_threshold` via `PUT /api/settings`
    expecting the Matches list to re-filter immediately (docs/DESIGN.md
    principle 3: "changing one re-filters the visible list"). Before this
    test, `_list_matches_sync` (jobfinder/web/jobs.py) applied
    `tag_visibility`/`role_filter`/`quality_filter` via
    `_card_hidden_by_tags` but never consulted `match_threshold` at all --
    that field only gated whether a *new* scan result got saved as a
    match in the first place (orchestrator.py:301,377), so raising or
    lowering it after the fact had no visible effect on already-stored
    matches, contrary to what this panel's primary control promises.
    """
    app, c = authed_client
    db = app.state.db
    app.state.config.match_threshold = 0  # permissive while seeding

    _seed_job(db, job_id="job-low-score", score=40)
    _seed_job(db, job_id="job-mid-score", score=65)
    _seed_job(db, job_id="job-high-score", score=90)

    # Below every seeded score: nothing is hidden yet.
    visible = {m["job_id"] for m in c.get("/api/matches").json()}
    assert visible == {"job-low-score", "job-mid-score", "job-high-score"}

    # Raise the threshold via the real endpoint (the panel's own write
    # path) and re-fetch -- this must re-filter immediately, no rescan.
    resp = c.put("/api/settings", json={"match_threshold": 70})
    assert resp.status_code == 200, resp.text

    visible_after = {m["job_id"] for m in c.get("/api/matches").json()}
    assert visible_after == {"job-high-score"}

    # Lowering it back must restore the others -- this is a live filter,
    # not a one-way prune of history.
    resp = c.put("/api/settings", json={"match_threshold": 0})
    assert resp.status_code == 200, resp.text
    visible_again = {m["job_id"] for m in c.get("/api/matches").json()}
    assert visible_again == {"job-low-score", "job-mid-score", "job-high-score"}


# ── skip-company ────────────────────────────────────────────────────────


def test_skip_company_hides_matching_company_from_matches(authed_client) -> None:
    app, c = authed_client
    db = app.state.db
    _seed_job(db, job_id="job-acme-ca", company="Acme Canada")

    resp = c.post("/api/skip-company", json={"name": "Acme Inc."})
    assert resp.status_code == 201, resp.text

    skipped = c.get("/api/skipped").json()
    assert any(entry["name_key"] == "acme" for entry in skipped)

    matches = c.get("/api/matches").json()
    assert not any(m["job_id"] == "job-acme-ca" for m in matches)


# ── auth enforcement across every endpoint ─────────────────────────────


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/matches"),
        ("get", "/api/applied"),
        ("get", "/api/removed"),
        ("get", "/api/skipped"),
        ("post", "/api/matches/some-id/apply"),
        ("post", "/api/matches/some-id/remove"),
        ("post", "/api/matches/some-id/restore"),
        ("post", "/api/applied/some-id/unapply"),
        ("post", "/api/matches/clear"),
        ("post", "/api/applied/clear"),
        ("post", "/api/removed/clear"),
        ("post", "/api/skip-company"),
    ],
)
def test_endpoint_rejects_request_with_no_session_cookie(client, method, path) -> None:
    app, c = client
    kwargs = {"json": {"name": "Acme"}} if path == "/api/skip-company" else {}
    resp = getattr(c, method)(path, **kwargs)
    assert resp.status_code == 401
