"""Tests for jobfinder.storage.database (T16) — WAL mode, connection closure,
and safe concurrency under multiple writers.

the product spec: 22 ``with self._connect()`` sites, zero ``.close()`` calls.
``Database._session()`` now owns both the transaction (commit/rollback) and
the closure (``finally: conn.close()``). These tests pin that behaviour
directly, rather than relying on refcounting ever happening to run in time.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
from pathlib import Path

from jobfinder.matcher.models import JobPosting, MatchResult
from jobfinder.storage.database import Database


def _job(job_id: str, company: str = "Acme Inc.") -> JobPosting:
    return JobPosting(
        job_id=job_id,
        title="QA Engineer",
        company=company,
        location="Remote",
        description="A job posting.",
        url=f"https://example.com/{job_id}",
        source="test",
    )


def test_journal_mode_is_wal(tmp_path) -> None:
    db = Database(str(tmp_path / "wal.db"))
    with db._session() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_connections_are_closed_tmpdir_rmtree_succeeds_without_ignore_errors() -> None:
    """The real proof connections are closed: a strict (non-ignore_errors)
    ``shutil.rmtree`` on the directory holding the db file must succeed.
    Before T16, this reliably raised ``WinError 32`` on Windows because the
    sqlite3 connection opened by the last ``with self._connect()`` call was
    still open (closure was left to refcounting).
    """
    tmp = tempfile.mkdtemp(prefix="jobfinder-db-close-test-")
    try:
        db_path = str(Path(tmp) / "closed.db")
        db = Database(db_path)

        job = _job("close-test-1")
        db.mark_seen([job])
        db.save_match(MatchResult(job=job, score=80, reasoning="r"))
        db.mark_applied(job.job_id)
        db.get_recent_matches()
        db.unmark_applied(job.job_id)
        db.mark_removed(job.job_id)
        db.unmark_removed(job.job_id)
        db.add_skipped_company("Acme Inc.")
        db.get_skipped_companies()
        db.clear_all()
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    # No `try/finally` around this call on purpose — a non-ignore_errors
    # rmtree failing *is* the test failure we're guarding against.
    shutil.rmtree(tmp)
    assert not Path(tmp).exists()


def test_concurrent_apply_remove_during_active_writer_has_no_locking_errors(tmp_path) -> None:
    """~20 concurrent apply/remove operations while a scan-like writer is
    continuously inserting new jobs. Zero ``database is locked`` errors.
    """
    db = Database(str(tmp_path / "concurrent.db"))

    seed_jobs = [_job(f"concurrent-job-{i}") for i in range(25)]
    db.mark_seen(seed_jobs)

    errors: list[BaseException] = []
    stop = threading.Event()

    def scan_writer() -> None:
        counter = 0
        while not stop.is_set():
            try:
                db.mark_seen([_job(f"writer-job-{counter}")])
                counter += 1
            except BaseException as exc:  # noqa: BLE001 - capture, assert later
                errors.append(exc)

    def apply_remove_worker(job: JobPosting) -> None:
        try:
            db.mark_applied(job.job_id)
            db.unmark_applied(job.job_id)
            db.mark_removed(job.job_id)
            db.unmark_removed(job.job_id)
        except BaseException as exc:  # noqa: BLE001 - capture, assert later
            errors.append(exc)

    writer_thread = threading.Thread(target=scan_writer, name="scan-writer")
    writer_thread.start()

    workers = [threading.Thread(target=apply_remove_worker, args=(job,)) for job in seed_jobs]
    for t in workers:
        t.start()
    for t in workers:
        t.join(timeout=30)

    stop.set()
    writer_thread.join(timeout=30)

    locking_errors = [e for e in errors if isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower()]
    assert locking_errors == [], f"database is locked errors occurred: {locking_errors!r}"
    assert errors == [], f"unexpected errors during concurrent access: {errors!r}"
