"""T57: deciding — and pinning — how a "batch" (one scan's worth of new
matches) is derived.

the task ledger row offers two options: cluster ``match_history.matched_at``
by a gap threshold (no migration), or add a ``scan_id`` column (needs a
migration). It says to prefer clustering *unless a test shows it's
ambiguous*. ``test_clustering_by_matched_at_gap_is_unavoidably_ambiguous``
below is that test, run first: it shows no single gap threshold can be
correct, because the two timing constraints a real scan produces are in
direct conflict —

  * **within one scan**, rows are *not* simultaneous: each ``MatchResult``
    gets its own ``matched_at`` as it is scored
    (``jobfinder/matcher/local_matcher.py``), and the optional agentic
    verification pass (``jobfinder/matcher/claude_matcher.py``) makes one
    Claude API call per qualifying match, so a scan with several qualifying
    jobs can legitimately spread its own ``matched_at`` values over minutes.
  * **between scans**, nothing enforces a minimum gap: ``JobScheduler.
    trigger_now()`` (``jobfinder/scheduler.py``) adds an ``"scan_immediate"``
    job with its own id, distinct from the periodic ``"scan_cycle"`` job, so
    a user clicking "Scan Now" seconds after the periodic cycle fires (or
    finishes) is not debounced at all — the two scans' matched_at values can
    be arbitrarily close, even interleaved.

Any gap threshold large enough to keep one slow scan's rows together is
therefore also large enough to wrongly merge a fast back-to-back re-scan.
That is the ambiguity the row anticipated, so this task adds
``match_history.scan_id`` instead (``_MATCH_HISTORY_MIGRATIONS`` in
``jobfinder/storage/database.py``) and derives batches from it via
``group_matches_by_scan`` — a key, not a clock, so overlapping/interleaved
scans still separate correctly, which clustering can never guarantee.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from jobfinder.matcher.models import JobPosting, MatchResult
from jobfinder.storage.database import Database, group_matches_by_scan


def _job(job_id: str) -> JobPosting:
    return JobPosting(
        job_id=job_id,
        title="QA Engineer",
        company="Acme",
        location="Remote",
        description="",
        url=f"https://example.com/{job_id}",
        source="test",
    )


def _naive_cluster_by_gap(matched_ats: list[datetime], threshold: timedelta) -> list[list[int]]:
    """A reference implementation of the rejected approach: group a
    chronologically-sorted list of ``matched_at`` values into batches
    wherever the gap to the previous entry exceeds ``threshold``. Indices
    are returned so the test can inspect grouping without re-deriving it."""
    if not matched_ats:
        return []
    batches: list[list[int]] = [[0]]
    for i in range(1, len(matched_ats)):
        gap = matched_ats[i] - matched_ats[i - 1]
        if gap > threshold:
            batches.append([i])
        else:
            batches[-1].append(i)
    return batches


def test_clustering_by_matched_at_gap_is_unavoidably_ambiguous():
    """No single gap threshold can both (a) keep one slow scan's two
    agentic-verification-delayed matches together, and (b) keep a fast
    manual re-scan's first match separate from the prior scan's last match.
    """
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    # Scan A: two qualifying jobs, the second scored 4 minutes later because
    # the agentic verification pass made a live Claude API call in between.
    scan_a_first = t0
    scan_a_second = t0 + timedelta(minutes=4)
    # Scan B: the user clicks "Scan Now" only 1 minute after scan A's last
    # result — trigger_now() has no cooldown, so this is legal.
    scan_b_first = scan_a_second + timedelta(minutes=1)

    matched_ats = [scan_a_first, scan_a_second, scan_b_first]

    # A threshold that satisfies (a) — keeps scan A's two rows in one batch —
    # must be >= 4 minutes...
    satisfies_a = timedelta(minutes=4)
    batches_a = _naive_cluster_by_gap(matched_ats, satisfies_a)

    # ...but that same threshold also merges scan B's row into scan A's
    # batch, which is wrong — they are different scans.
    assert batches_a == [[0, 1, 2]], (
        "a threshold wide enough to keep scan A together also wrongly merges scan B's row into the same batch"
    )

    # Conversely, a threshold that correctly separates scan B (< 1 minute)
    # also splits scan A's own two rows apart, which is also wrong.
    separates_b = timedelta(seconds=30)
    batches_b = _naive_cluster_by_gap(matched_ats, separates_b)
    assert batches_b == [[0], [1], [2]], (
        "a threshold narrow enough to separate scan B also wrongly splits scan A's own two rows into separate batches"
    )

    # No threshold value satisfies both constraints simultaneously —
    # matched_at-gap clustering is ambiguous for this (entirely realistic)
    # timing shape. Hence: scan_id, not clustering.


def test_two_jobs_from_one_scan_land_in_same_batch_and_later_scan_does_not(tmp_path):
    db = Database(str(tmp_path / "jf.db"))

    job1, job2, job3 = _job("job-1"), _job("job-2"), _job("job-3")
    db.mark_seen([job1, job2, job3])

    scan_a = "scan-aaaa"
    scan_b = "scan-bbbb"

    db.save_match(MatchResult(job=job1, score=80, reasoning="r1", scan_id=scan_a))
    # Same scan, scored later (agentic pass latency) — same scan_id, even
    # though matched_at defaults to "now" and will differ slightly.
    db.save_match(MatchResult(job=job2, score=81, reasoning="r2", scan_id=scan_a))
    # A later scan — different scan_id, even if it ran moments afterward.
    db.save_match(MatchResult(job=job3, score=82, reasoning="r3", scan_id=scan_b))

    rows = db.get_recent_matches()
    batches = group_matches_by_scan(rows)

    batch_by_job = {}
    for i, batch in enumerate(batches):
        for row in batch:
            batch_by_job[row["job_id"]] = i

    assert batch_by_job["job-1"] == batch_by_job["job-2"]
    assert batch_by_job["job-3"] != batch_by_job["job-1"]


def test_group_matches_by_scan_treats_legacy_null_scan_id_rows_as_singleton_batches():
    """Rows written before this migration have ``scan_id IS NULL``. They
    must not be silently merged into one giant "legacy" batch — each is its
    own batch, same as a ``None``-grouped row always was before scan_id
    existed."""
    rows = [
        {"job_id": "old-1", "scan_id": None},
        {"job_id": "old-2", "scan_id": None},
        {"job_id": "new-1", "scan_id": "scan-x"},
        {"job_id": "new-2", "scan_id": "scan-x"},
    ]
    batches = group_matches_by_scan(rows)
    sizes = sorted(len(b) for b in batches)
    assert sizes == [1, 1, 2]


def test_existing_database_upgrades_without_data_loss(tmp_path):
    """A database created before ``scan_id`` existed must upgrade in place:
    the column is added, existing rows are preserved exactly (with
    ``scan_id`` NULL), and nothing is dropped or truncated."""
    db_path = str(tmp_path / "legacy.db")

    # Build a pre-T57 schema by hand: match_history with no scan_id column.
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
                matched_at TEXT NOT NULL
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
            "skills_missing, matched_at) VALUES "
            "('legacy-1', 77, 'legacy reasoning', '[]', '[]', '2025-01-01T00:00:01')"
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
    assert row["scan_id"] is None

    # And the column genuinely exists now, for future rows to use.
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(match_history)").fetchall()}
    finally:
        conn.close()
    assert "scan_id" in cols


def test_matches_within_a_batch_are_ranked_by_score_not_scan_order(tmp_path):
    """T62: DESIGN.md principle 2 says "Ranking still orders within a
    batch." ``get_recent_matches()`` had no score term at all in its
    ``ORDER BY`` (``mh.matched_at DESC`` only), and the client
    (``frontend/src/components/jobBatching.ts``) never sorts either — it
    only groups. In production this meant jobs within one scan batch
    rendered in scan *processing* order (whatever order the matcher
    happened to score them in), not best-match-first.

    Four jobs scored in one scan, deliberately inserted in *ascending*
    score order (78, 81, 88, 92) — the exact order that reproduced the
    bug in the built app. ``matched_at`` is identical (second-resolution
    ISO strings) for every row, reproducing the real-world case where a
    scan scores several jobs fast enough that their timestamps tie; a
    plain ``ORDER BY matched_at DESC`` then falls back to insertion
    (rowid) order, i.e. the scan's own processing order, which is the
    bug. A correct fix must return them descending by score within the
    batch regardless of tie-breaking on ``matched_at``."""
    db = Database(str(tmp_path / "jf.db"))

    jobs = [_job(f"batch-job-{i}") for i in range(4)]
    db.mark_seen(jobs)

    scan_id = "scan-ranking"
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    for i, (job, score) in enumerate(zip(jobs, [78, 81, 88, 92])):
        db.save_match(
            MatchResult(
                job=job,
                score=score,
                reasoning=f"r{i}",
                matched_at=t0,
                scan_id=scan_id,
            )
        )

    rows = db.get_recent_matches()
    batches = group_matches_by_scan(rows)
    assert len(batches) == 1
    scores_in_order = [row["score"] for row in batches[0]]
    assert scores_in_order == [92, 88, 81, 78], (
        "jobs within a single scan batch must be ranked best-match-first, "
        f"not scan-processing order; got {scores_in_order}"
    )


def test_batches_stay_newest_first_after_ranking_fix(tmp_path):
    """The within-batch score ranking must not disturb the between-batch
    ordering: an older scan's high-scoring job must never outrank a newer
    scan's job in the overall list, because batches are ordered by recency
    first and score only breaks ties *inside* a batch."""
    db = Database(str(tmp_path / "jf.db"))

    old_job = _job("old-job")
    new_job_low, new_job_high = _job("new-job-low"), _job("new-job-high")
    db.mark_seen([old_job, new_job_low, new_job_high])

    t0 = datetime(2026, 1, 1, 12, 0, 0)
    scan_old, scan_new = "scan-old", "scan-new"

    # Old scan: one very high-scoring match.
    db.save_match(MatchResult(job=old_job, score=99, reasoning="old", matched_at=t0, scan_id=scan_old))

    # New scan, much later: two lower-scoring matches, inserted low-then-high.
    t1 = t0 + timedelta(hours=1)
    db.save_match(MatchResult(job=new_job_low, score=50, reasoning="new-low", matched_at=t1, scan_id=scan_new))
    db.save_match(
        MatchResult(
            job=new_job_high,
            score=60,
            reasoning="new-high",
            matched_at=t1 + timedelta(seconds=1),
            scan_id=scan_new,
        )
    )

    rows = db.get_recent_matches()
    batches = group_matches_by_scan(rows)

    assert len(batches) == 2
    # Newest batch (scan_new) must come first, even though its scores are
    # lower than the older batch's.
    assert {row["job_id"] for row in batches[0]} == {"new-job-low", "new-job-high"}
    assert {row["job_id"] for row in batches[1]} == {"old-job"}
    # And within that newest batch, the higher score comes first despite
    # being inserted second.
    assert [row["job_id"] for row in batches[0]] == ["new-job-high", "new-job-low"]
