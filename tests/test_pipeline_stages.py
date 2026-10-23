"""T79: ``shortlisted_jobs`` / ``replied_jobs`` persistence and the single
resolved stage that makes the product spec ("a job is in exactly one
stage") true in code rather than by convention.

Covers:
  - migrating a pre-T79 database (built from an explicit old-schema fixture,
    not ``Database`` itself) preserves every existing row, by count and
    content;
  - ``stage_of``'s precedence when a job is deliberately inserted into more
    than one stage table at once, asserted pairwise;
  - ``set_stage`` across all 25 ordered transitions, each asserting the
    resulting ``stage_of`` *and* absence from the previous stage's table;
  - ``set_stage`` to the stage a job already occupies is a no-op on the
    stored timestamp;
  - a job with no row anywhere reads as ``new``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from jobfinder.matcher.models import JobPosting
from jobfinder.storage.database import Database

STAGES = ("new", "shortlist", "applied", "replied", "out")

# The schema as it existed immediately before T79 — every column already
# migrated in by prior tasks (seen_jobs' rich fields, match_history's
# scan_id/agent_verified, removed_jobs.removed_by), but with neither of the
# two tables this task adds. Built explicitly here (not by importing
# Database's old schema, which no longer exists) so this fixture keeps
# pinning the pre-T79 shape even as the current schema moves on.
_PRE_T79_SCHEMA = """
CREATE TABLE seen_jobs (
    job_id           TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    title            TEXT,
    company          TEXT,
    url              TEXT,
    location         TEXT,
    country          TEXT,
    region           TEXT,
    city             TEXT,
    work_arrangement TEXT,
    employment_type  TEXT,
    first_seen       TEXT NOT NULL
);

CREATE TABLE match_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES seen_jobs(job_id),
    score           INTEGER NOT NULL,
    reasoning       TEXT,
    skills_matched  TEXT,
    skills_missing  TEXT,
    matched_at      TEXT NOT NULL
);

CREATE TABLE applied_jobs (
    job_id      TEXT PRIMARY KEY REFERENCES seen_jobs(job_id),
    applied_at  TEXT NOT NULL
);

CREATE TABLE removed_jobs (
    job_id      TEXT PRIMARY KEY REFERENCES seen_jobs(job_id),
    removed_at  TEXT NOT NULL,
    removed_by  TEXT NOT NULL DEFAULT 'user'
);

CREATE TABLE skipped_companies (
    name_key    TEXT PRIMARY KEY,
    display     TEXT NOT NULL,
    added_at    TEXT NOT NULL
);
"""


def _job(job_id: str) -> JobPosting:
    return JobPosting(
        job_id=job_id,
        title="QA Engineer",
        company="Acme Inc.",
        location="Remote",
        description="A job posting.",
        url=f"https://example.com/{job_id}",
        source="test",
    )


def test_pre_migration_database_keeps_every_row(tmp_path) -> None:
    db_path = str(tmp_path / "old.db")

    with sqlite3.connect(db_path) as conn:
        conn.executescript(_PRE_T79_SCHEMA)
        conn.execute(
            "INSERT INTO seen_jobs (job_id, source, title, company, url, location, first_seen) "
            "VALUES ('old-1', 'test', 'Old Job', 'OldCo', 'https://x/old-1', 'Remote', '2020-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO seen_jobs (job_id, source, title, company, url, location, first_seen) "
            "VALUES ('old-2', 'test', 'Older Job', 'OlderCo', 'https://x/old-2', 'Remote', '2020-01-02T00:00:00')"
        )
        conn.execute(
            "INSERT INTO match_history (job_id, score, reasoning, matched_at) "
            "VALUES ('old-1', 77, 'a good fit', '2020-01-01T01:00:00')"
        )
        conn.execute("INSERT INTO applied_jobs (job_id, applied_at) VALUES ('old-1', '2020-01-01T02:00:00')")
        conn.execute(
            "INSERT INTO removed_jobs (job_id, removed_at, removed_by) VALUES ('old-2', '2020-01-02T02:00:00', 'agent')"
        )
        conn.execute(
            "INSERT INTO skipped_companies (name_key, display, added_at) VALUES ('badco', 'BadCo', '2020-01-01T03:00:00')"
        )
        conn.commit()

    # Opening with Database runs _init_schema -> _migrate against this
    # pre-existing file.
    Database(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        seen_rows = conn.execute("SELECT * FROM seen_jobs ORDER BY job_id").fetchall()
        assert len(seen_rows) == 2
        assert seen_rows[0]["job_id"] == "old-1"
        assert seen_rows[0]["title"] == "Old Job"
        assert seen_rows[1]["job_id"] == "old-2"
        assert seen_rows[1]["company"] == "OlderCo"

        mh_rows = conn.execute("SELECT * FROM match_history").fetchall()
        assert len(mh_rows) == 1
        assert mh_rows[0]["job_id"] == "old-1"
        assert mh_rows[0]["score"] == 77
        assert mh_rows[0]["reasoning"] == "a good fit"

        applied_rows = conn.execute("SELECT * FROM applied_jobs").fetchall()
        assert len(applied_rows) == 1
        assert applied_rows[0]["job_id"] == "old-1"
        assert applied_rows[0]["applied_at"] == "2020-01-01T02:00:00"

        removed_rows = conn.execute("SELECT * FROM removed_jobs").fetchall()
        assert len(removed_rows) == 1
        assert removed_rows[0]["job_id"] == "old-2"
        assert removed_rows[0]["removed_by"] == "agent"

        skipped_rows = conn.execute("SELECT * FROM skipped_companies").fetchall()
        assert len(skipped_rows) == 1
        assert skipped_rows[0]["name_key"] == "badco"

        # The two new tables now exist (empty, since the old schema predates
        # them).
        new_tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('shortlisted_jobs', 'replied_jobs')"
            ).fetchall()
        }
        assert new_tables == {"shortlisted_jobs", "replied_jobs"}


def test_job_with_no_row_anywhere_is_new(tmp_path) -> None:
    db = Database(str(tmp_path / "jf.db"))
    db.mark_seen([_job("nowhere")])
    assert db.stage_of("nowhere") == "new"


def test_unknown_job_id_is_also_new(tmp_path) -> None:
    db = Database(str(tmp_path / "jf.db"))
    assert db.stage_of("never-seen") == "new"


def test_stage_of_precedence_pairwise(tmp_path) -> None:
    """replied > applied > shortlist > out > new, asserted for every ordered
    pair by inserting a job into exactly two tables at once."""
    db_path = str(tmp_path / "jf.db")
    db = Database(db_path)

    # Precedence order, highest first.
    ordered = ["replied", "applied", "shortlist", "out", "new"]

    def _insert(conn: sqlite3.Connection, job_id: str, stage: str) -> None:
        now = datetime.utcnow().isoformat()
        if stage == "replied":
            conn.execute("INSERT INTO replied_jobs (job_id, replied_at, note) VALUES (?, ?, NULL)", (job_id, now))
        elif stage == "applied":
            conn.execute("INSERT INTO applied_jobs (job_id, applied_at) VALUES (?, ?)", (job_id, now))
        elif stage == "shortlist":
            conn.execute("INSERT INTO shortlisted_jobs (job_id, shortlisted_at) VALUES (?, ?)", (job_id, now))
        elif stage == "out":
            conn.execute(
                "INSERT INTO removed_jobs (job_id, removed_at, removed_by) VALUES (?, ?, 'user')", (job_id, now)
            )
        # "new" means no row anywhere — nothing to insert.

    pair_idx = 0
    for i, higher in enumerate(ordered):
        for lower in ordered[i + 1 :]:
            job_id = f"pair-{pair_idx}"
            pair_idx += 1
            db.mark_seen([_job(job_id)])
            with sqlite3.connect(db_path) as conn:
                _insert(conn, job_id, higher)
                _insert(conn, job_id, lower)
                conn.commit()
            assert db.stage_of(job_id) == higher, f"{higher} should win over {lower}"


def test_set_stage_all_25_transitions(tmp_path) -> None:
    db = Database(str(tmp_path / "jf.db"))

    stage_tables = {
        "shortlist": "shortlisted_jobs",
        "applied": "applied_jobs",
        "replied": "replied_jobs",
        "out": "removed_jobs",
    }

    def _assert_absent_from_other_tables(job_id: str, current_stage: str) -> None:
        for stage, table in stage_tables.items():
            if stage == current_stage:
                continue
            with db._session() as conn:
                row = conn.execute(f"SELECT 1 FROM {table} WHERE job_id = ?", (job_id,)).fetchone()
            assert row is None, f"{job_id} still present in {table} after moving to {current_stage}"

    transition_idx = 0
    for from_stage in STAGES:
        for to_stage in STAGES:
            job_id = f"transition-{transition_idx}"
            transition_idx += 1
            db.mark_seen([_job(job_id)])

            if from_stage != "new":
                db.set_stage(job_id, from_stage)
                assert db.stage_of(job_id) == from_stage

            db.set_stage(job_id, to_stage)
            assert db.stage_of(job_id) == to_stage
            _assert_absent_from_other_tables(job_id, to_stage)

    assert transition_idx == 25


def test_set_stage_to_same_stage_is_a_timestamp_noop(tmp_path) -> None:
    db_path = str(tmp_path / "jf.db")
    db = Database(db_path)

    for stage, table, col in [
        ("shortlist", "shortlisted_jobs", "shortlisted_at"),
        ("applied", "applied_jobs", "applied_at"),
        ("replied", "replied_jobs", "replied_at"),
        ("out", "removed_jobs", "removed_at"),
    ]:
        job_id = f"noop-{stage}"
        db.mark_seen([_job(job_id)])
        db.set_stage(job_id, stage)

        with sqlite3.connect(db_path) as conn:
            original = conn.execute(f"SELECT {col} FROM {table} WHERE job_id = ?", (job_id,)).fetchone()[0]
            conn.execute(f"UPDATE {table} SET {col} = ? WHERE job_id = ?", ("2020-01-01T00:00:00", job_id))
            conn.commit()
            backdated = conn.execute(f"SELECT {col} FROM {table} WHERE job_id = ?", (job_id,)).fetchone()[0]
        assert backdated == "2020-01-01T00:00:00"
        assert original != backdated

        db.set_stage(job_id, stage)

        with sqlite3.connect(db_path) as conn:
            after = conn.execute(f"SELECT {col} FROM {table} WHERE job_id = ?", (job_id,)).fetchone()[0]
        assert after == backdated, f"re-setting stage {stage!r} reset the stored timestamp"


def test_set_stage_rejects_unknown_stage(tmp_path) -> None:
    db = Database(str(tmp_path / "jf.db"))
    db.mark_seen([_job("bad-stage-job")])
    try:
        db.set_stage("bad-stage-job", "archived")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unrecognised stage")


def test_replied_jobs_table_has_note_column(tmp_path) -> None:
    db_path = str(tmp_path / "jf.db")
    db = Database(db_path)
    db.mark_seen([_job("replied-with-note")])
    db.set_stage("replied-with-note", "replied")

    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(replied_jobs)").fetchall()}
    assert {"job_id", "replied_at", "note"} <= cols


def test_clear_all_wipes_every_stage_table(tmp_path):
    """A factory reset must leave nothing behind in any stage.

    `clear_all` listed its tables by hand, so T79's two new stages
    survived a reset that told the user it erases "every job record
    JobFinder has seen". Worse than the stale rows: `seen_jobs` *was*
    cleared, so the same job resurfacing on a later scan rejoined its
    orphaned stage row and came back already shortlisted or replied.
    Found by Phase 9's independent review.
    """
    from jobfinder.storage.database import _STAGE_TABLES, Database

    db = Database(str(tmp_path / "reset.db"))
    for i, stage in enumerate(("shortlist", "applied", "replied", "out")):
        job = _job(f"reset-{i}")
        db.mark_seen([job])
        db.set_stage(job.job_id, stage)
        assert db.stage_of(job.job_id) == stage

    db.clear_all()

    for i, stage in enumerate(("shortlist", "applied", "replied", "out")):
        assert db.stage_of(f"reset-{i}") == "new", f"{stage} survived clear_all()"

    # Asserted against the table list itself, so a stage added later is
    # covered without anyone remembering to extend this test.
    with db._session() as conn:
        for table, _col in _STAGE_TABLES.values():
            remaining = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            assert remaining == 0, f"{table} still holds {remaining} rows after clear_all()"


# ── T101: starring is orthogonal to the stage machinery ─────────────────


def test_starring_does_not_touch_set_stage_or_stage_of(tmp_path) -> None:
    """A star must not be a sixth stage. Star a job, move it, star it
    again, and assert `stage_of`/`set_stage` behave identically to an
    unstarred job at every step -- the exact comparison the brief asks
    for, not just "starring doesn't crash"."""
    db = Database(str(tmp_path / "jf.db"))

    starred_job = _job("star-vs-stage-starred")
    plain_job = _job("star-vs-stage-plain")
    db.mark_seen([starred_job])
    db.mark_seen([plain_job])

    # Both start as "new".
    assert db.stage_of(starred_job.job_id) == db.stage_of(plain_job.job_id) == "new"

    db.set_starred(starred_job.job_id, True)
    # Starring must not change stage.
    assert db.stage_of(starred_job.job_id) == "new"
    assert db.stage_of(starred_job.job_id) == db.stage_of(plain_job.job_id)

    # Move both jobs through the same transition; the starred one must
    # behave identically.
    db.set_stage(starred_job.job_id, "shortlist")
    db.set_stage(plain_job.job_id, "shortlist")
    assert db.stage_of(starred_job.job_id) == db.stage_of(plain_job.job_id) == "shortlist"

    db.set_stage(starred_job.job_id, "applied")
    db.set_stage(plain_job.job_id, "applied")
    assert db.stage_of(starred_job.job_id) == db.stage_of(plain_job.job_id) == "applied"

    # Star it again post-move; stage must still be unaffected.
    db.set_starred(starred_job.job_id, True)
    assert db.stage_of(starred_job.job_id) == "applied"
    assert db.stage_of(starred_job.job_id) == db.stage_of(plain_job.job_id)

    # And the starred job never appeared in any stage table it shouldn't
    # have (the "not a sixth stage" assertion, against the actual tables).
    from jobfinder.storage.database import _STAGE_TABLES

    with db._session() as conn:
        for table, _col in _STAGE_TABLES.values():
            if table == "applied_jobs":
                continue
            row = conn.execute(f"SELECT 1 FROM {table} WHERE job_id = ?", (starred_job.job_id,)).fetchone()
            assert row is None, f"starring put {starred_job.job_id} into {table}, a stage table"


def test_starring_survives_a_stage_move(tmp_path) -> None:
    db = Database(str(tmp_path / "jf.db"))
    job = _job("star-survives-move")
    db.mark_seen([job])

    db.set_starred(job.job_id, True)
    assert db.get_starred_job_ids() == {job.job_id}

    db.set_stage(job.job_id, "shortlist")
    assert db.get_starred_job_ids() == {job.job_id}, "a stage move must not unstar a job"
    assert db.stage_of(job.job_id) == "shortlist"

    db.set_stage(job.job_id, "applied")
    assert db.get_starred_job_ids() == {job.job_id}, "a second stage move must not unstar a job"


def test_set_starred_false_unstars(tmp_path) -> None:
    db = Database(str(tmp_path / "jf.db"))
    job = _job("star-toggle-off")
    db.mark_seen([job])

    db.set_starred(job.job_id, True)
    assert db.get_starred_job_ids() == {job.job_id}

    db.set_starred(job.job_id, False)
    assert db.get_starred_job_ids() == set()


def test_set_starred_true_is_idempotent_on_timestamp(tmp_path) -> None:
    """Mirrors `test_set_stage_to_same_stage_is_a_timestamp_noop` --
    re-starring an already-starred job must not refresh `starred_at`."""
    db_path = str(tmp_path / "jf.db")
    db = Database(db_path)
    job = _job("star-timestamp-noop")
    db.mark_seen([job])
    db.set_starred(job.job_id, True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE starred_jobs SET starred_at = ? WHERE job_id = ?", ("2020-01-01T00:00:00", job.job_id))
        conn.commit()

    db.set_starred(job.job_id, True)

    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT starred_at FROM starred_jobs WHERE job_id = ?", (job.job_id,)).fetchone()[0]
    assert after == "2020-01-01T00:00:00", "re-starring reset the stored starred_at"


def test_starred_jobs_not_in_stage_tables_dict(tmp_path) -> None:
    """`starred_jobs` must never be added to `_STAGE_TABLES` -- that dict
    is the pipeline-stage machinery, and a star is orthogonal to it."""
    from jobfinder.storage.database import _STAGE_TABLES

    stage_tables = {table for table, _col in _STAGE_TABLES.values()}
    assert "starred_jobs" not in stage_tables


def test_clear_all_wipes_starred_jobs(tmp_path) -> None:
    """A factory reset must also clear starred_jobs -- the exact T79
    lesson `test_clear_all_wipes_every_stage_table` exists to pin,
    applied to the new, non-stage table this task adds."""
    db = Database(str(tmp_path / "reset-star.db"))
    job = _job("reset-star-1")
    db.mark_seen([job])
    db.set_starred(job.job_id, True)
    assert db.get_starred_job_ids() == {job.job_id}

    db.clear_all()

    assert db.get_starred_job_ids() == set()
    with db._session() as conn:
        remaining = conn.execute("SELECT COUNT(*) AS n FROM starred_jobs").fetchone()["n"]
        assert remaining == 0


def test_clear_all_wipes_every_job_keyed_table_discovered_from_schema(tmp_path) -> None:
    """Generic against a *sixth* table: discovers every table with a
    foreign key into ``seen_jobs(job_id)`` straight from the live schema
    (``PRAGMA foreign_key_list``) rather than naming tables by hand, so a
    future table referencing ``seen_jobs`` that nobody wires into
    ``clear_all``'s table list fails this test even if nobody remembers
    to update this file too.
    """
    from jobfinder.storage.database import _NON_STAGE_JOB_TABLES, _STAGE_TABLES, Database

    db_path = str(tmp_path / "reset-discovery.db")
    db = Database(db_path)

    known_tables = {table for table, _col in _STAGE_TABLES.values()} | set(_NON_STAGE_JOB_TABLES) | {"match_history"}

    with sqlite3.connect(db_path) as conn:
        all_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        job_keyed_tables = set()
        for table in all_tables:
            fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            if any(fk[2] == "seen_jobs" for fk in fks):
                job_keyed_tables.add(table)

    missing = job_keyed_tables - known_tables
    assert not missing, (
        f"table(s) {missing} reference seen_jobs(job_id) but are not covered by clear_all's "
        "known table list (_STAGE_TABLES / _NON_STAGE_JOB_TABLES / match_history) -- wire them in"
    )

    # Behavioural half: seed every discovered table, then prove clear_all
    # actually empties all of them (not just that the list is complete).
    job = _job("reset-discovery-1")
    db.mark_seen([job])
    db.set_stage(job.job_id, "applied")
    db.set_starred(job.job_id, True)

    db.clear_all()

    with db._session() as conn:
        for table in known_tables:
            remaining = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            assert remaining == 0, f"{table} still holds {remaining} rows after clear_all()"
