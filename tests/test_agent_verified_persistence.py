"""T41: persist ``agent_verified`` so the "✓ AI" badge survives a restart.

``claude_matcher`` sets ``MatchResult.agent_verified`` after the optional
Claude verification pass runs, but ``save_match()`` never wrote it and no
``match_history`` column existed, so a history row always defaulted it to
``False`` and the badge vanished on relaunch (the product spec, found during
T13). Signed off 2026-10-22: persist it.

This adds ``agent_verified`` via the same ``_MATCH_HISTORY_MIGRATIONS``
mechanism T57 introduced for ``scan_id``, following that task's own test
pattern: build a pre-T41 schema by hand with raw ``sqlite3`` (no
``agent_verified`` column), close it, then open it through ``Database`` to
trigger ``_migrate`` — a fresh-schema database "migrating" itself would
prove nothing, since the column would already be there.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from jobfinder.matcher.models import JobPosting, MatchResult
from jobfinder.storage.database import Database


def _job(job_id: str = "job-1") -> JobPosting:
    return JobPosting(
        job_id=job_id,
        title="Senior Backend Engineer",
        company="Acme Inc.",
        location="Toronto, ON",
        description="...",
        url="https://boards.example/job-1",
        source="linkedin",
    )


def test_existing_database_upgrades_without_data_loss(tmp_path):
    """A database created before ``agent_verified`` existed must upgrade in
    place: the column is added, existing rows are preserved exactly, and
    nothing is dropped or truncated."""
    db_path = str(tmp_path / "legacy.db")

    # Build a pre-T41 schema by hand: match_history with no agent_verified
    # column (mirrors T57's pre-scan_id schema, plus the scan_id column T57
    # already added, since T41 lands after it).
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE seen_jobs (
                job_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT,
                company TEXT,
                url TEXT,
                location TEXT,
                country TEXT,
                region TEXT,
                city TEXT,
                work_arrangement TEXT,
                employment_type TEXT,
                first_seen TEXT NOT NULL
            );
            CREATE TABLE match_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES seen_jobs(job_id),
                score INTEGER NOT NULL,
                reasoning TEXT,
                skills_matched TEXT,
                skills_missing TEXT,
                matched_at TEXT NOT NULL,
                scan_id TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO seen_jobs (job_id, source, title, company, url, location, "
            "country, region, city, work_arrangement, employment_type, first_seen) "
            "VALUES ('legacy-1', 'test', 'Old Job', 'OldCo', 'https://x', '', "
            "'', '', '', '', '', '2025-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO match_history (job_id, score, reasoning, skills_matched, "
            "skills_missing, matched_at, scan_id) VALUES "
            "('legacy-1', 77, 'legacy reasoning', '[]', '[]', '2025-01-01T00:00:01', NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    # Opening with Database() runs _migrate() — this must not lose the row.
    db = Database(db_path)
    rows = db.get_recent_matches()

    assert len(rows) == 1
    row = rows[0]
    assert row["job_id"] == "legacy-1"
    assert row["score"] == 77
    assert row["reasoning"] == "legacy reasoning"
    assert row["matched_at"] == "2025-01-01T00:00:01"
    # Pre-migration row has no stored value — must not be invented as truthy.
    assert not row["agent_verified"]

    # And the column genuinely exists now, for future rows to use.
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(match_history)").fetchall()}
    finally:
        conn.close()
    assert "agent_verified" in cols


def test_save_match_persists_agent_verified_true(tmp_path):
    """A verified match written via ``save_match()`` and re-read via
    ``get_recent_matches()`` must report ``agent_verified`` true — the
    exact round trip the "✓ AI" badge depends on surviving a restart."""
    db = Database(str(tmp_path / "jobs.db"))
    result = MatchResult(
        job=_job(),
        score=88,
        reasoning="Strong overlap, verified by Claude.",
        skills_matched=["Python"],
        skills_missing=[],
        matched_at=datetime(2026, 1, 1, 0, 0, 0),
        agent_verified=True,
    )
    # save_match() references seen_jobs via a FOREIGN KEY, so mark_seen first.
    db.mark_seen([result.job])
    db.save_match(result)

    rows = db.get_recent_matches()
    assert len(rows) == 1
    assert rows[0]["agent_verified"]


def test_save_match_persists_agent_verified_false_by_default(tmp_path):
    """A match that never went through the optional Claude verification
    pass must round-trip as falsy, not just "missing"."""
    db = Database(str(tmp_path / "jobs.db"))
    result = MatchResult(
        job=_job(job_id="job-2"),
        score=60,
        reasoning="Local heuristic match only.",
        skills_matched=[],
        skills_missing=[],
        matched_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db.mark_seen([result.job])
    db.save_match(result)

    rows = db.get_recent_matches()
    assert len(rows) == 1
    assert not rows[0]["agent_verified"]
