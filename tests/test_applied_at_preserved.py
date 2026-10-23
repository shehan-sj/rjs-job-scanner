"""T42: re-applying to a job must not rewrite the date it was first applied.

``mark_applied`` used ``INSERT OR REPLACE``, which deletes the existing row
and inserts a new one with a fresh ``applied_at``. The row count stays 1, so
it looked idempotent -- it is idempotent in *count* but not in *data*, and the
datum it destroys is the one the user actually cares about.

A web UI makes this far likelier to fire than the desktop click it was ported
from: double-submits, retries and reconnect logic can all re-send.
"""

from __future__ import annotations

import sqlite3

from jobfinder.matcher.models import JobPosting
from jobfinder.storage.database import Database


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


def _applied_at(db_path: str, job_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT applied_at FROM applied_jobs WHERE job_id = ?", (job_id,)).fetchone()
    assert row is not None, "the job was not recorded as applied at all"
    return row[0]


def test_reapplying_does_not_reset_the_tracked_application_date(tmp_path) -> None:
    db_path = str(tmp_path / "jf.db")
    db = Database(db_path)
    job = _job("t42-reapply")
    db.mark_seen([job])

    db.mark_applied(job.job_id)
    first = _applied_at(db_path, job.job_id)

    # Force a different timestamp. Without it, two calls inside the same
    # second produce identical strings and the test would pass against the
    # unfixed code -- proving nothing.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE applied_jobs SET applied_at = ? WHERE job_id = ?",
            ("2020-01-01T00:00:00", job.job_id),
        )
    backdated = _applied_at(db_path, job.job_id)
    assert backdated == "2020-01-01T00:00:00"

    db.mark_applied(job.job_id)

    assert _applied_at(db_path, job.job_id) == backdated, (
        "re-applying overwrote applied_at; the original application date is lost"
    )
    assert first != backdated  # guards the backdating step itself


def test_reapplying_still_leaves_exactly_one_row(tmp_path) -> None:
    """The fix must not trade data loss for duplicate rows."""
    db_path = str(tmp_path / "jf.db")
    db = Database(db_path)
    job = _job("t42-single-row")
    db.mark_seen([job])

    db.mark_applied(job.job_id)
    db.mark_applied(job.job_id)
    db.mark_applied(job.job_id)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM applied_jobs WHERE job_id = ?", (job.job_id,)).fetchone()[0]
    assert count == 1
