"""SQLite persistence layer for seen jobs, match history, and applied jobs.

T57: batches ("which jobs were found by the same scan") are derived from
``match_history.scan_id``, not from clustering ``matched_at`` by a gap
threshold. A gap threshold was tried first and rejected —
``tests/test_match_batching.py``'s
``test_clustering_by_matched_at_gap_is_unavoidably_ambiguous`` pins why: a
scan's own rows are not simultaneous (each ``MatchResult.matched_at`` is set
as it is scored, and the optional agentic-verification pass makes one Claude
API call per qualifying match, so one scan can spread over minutes), while
``JobScheduler.trigger_now()`` (``jobfinder/scheduler.py``) has no cooldown
between scans at all. Any threshold wide enough to keep one slow scan's rows
together is also wide enough to wrongly merge a fast back-to-back re-scan —
there is no value that satisfies both. ``scan_id`` sidesteps this entirely:
it is a key stamped once per ``Orchestrator.run_scan()`` call
(``jobfinder/orchestrator.py``), so it groups correctly even if two scans'
``matched_at`` values are interleaved.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..matcher.models import JobPosting, MatchResult

logger = logging.getLogger(__name__)

# Legal-entity suffixes stripped before comparing company names, so
# "Acme Inc." and "Acme" resolve to the same skip-list entry.
_LEGAL_SUFFIXES = (
    "inc",
    "incorporated",
    "llc",
    "llp",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "co",
    "plc",
    "gmbh",
    "sa",
    "nv",
    "bv",
    "ag",
    "pty",
    "group",
    "holdings",
)


def normalize_company(name: str) -> str:
    """Return a comparison key for a company name.

    Lowercases, drops punctuation, collapses whitespace, and strips trailing
    legal-entity suffixes. Returns ``""`` for anything that normalises away
    to nothing — callers should treat that as "not a usable name".
    """
    cleaned = re.sub(r"[^\w\s]", " ", (name or "").lower())
    words = cleaned.split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def company_matches_skip(company: str, skip_keys: set[str]) -> bool:
    """True if *company* should be hidden given a set of normalised skip keys.

    Matches exactly, or when one name contains the other as a whole-word
    prefix/suffix — so "Acme" on the skip list also catches "Acme Canada",
    without "Acme" matching an unrelated "Acmeco". Names shorter than four
    characters only ever match exactly, to avoid stray hits on acronyms.
    """
    key = normalize_company(company)
    if not key:
        return False
    if key in skip_keys:
        return True
    for skip in skip_keys:
        if len(skip) < 4 or len(key) < 4:
            continue
        if key.startswith(skip + " ") or key.endswith(" " + skip):
            return True
        if skip.startswith(key + " ") or skip.endswith(" " + key):
            return True
    return False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
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

CREATE TABLE IF NOT EXISTS match_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES seen_jobs(job_id),
    score           INTEGER NOT NULL,
    reasoning       TEXT,
    skills_matched  TEXT,
    skills_missing  TEXT,
    matched_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applied_jobs (
    job_id      TEXT PRIMARY KEY REFERENCES seen_jobs(job_id),
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS removed_jobs (
    job_id      TEXT PRIMARY KEY REFERENCES seen_jobs(job_id),
    removed_at  TEXT NOT NULL,
    removed_by  TEXT NOT NULL DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS shortlisted_jobs (
    job_id          TEXT PRIMARY KEY REFERENCES seen_jobs(job_id),
    shortlisted_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replied_jobs (
    job_id      TEXT PRIMARY KEY REFERENCES seen_jobs(job_id),
    replied_at  TEXT NOT NULL,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS skipped_companies (
    name_key    TEXT PRIMARY KEY,
    display     TEXT NOT NULL,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS starred_jobs (
    job_id      TEXT PRIMARY KEY REFERENCES seen_jobs(job_id),
    starred_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_match_score ON match_history(score DESC);
CREATE INDEX IF NOT EXISTS idx_seen_first  ON seen_jobs(first_seen DESC);
"""

# Indexes that depend on columns added by ``_migrate`` must be created after
# migration has run.
_POST_MIGRATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_seen_country ON seen_jobs(country)",
]

# Columns added after initial schema — applied via ``_migrate`` on startup
_SEEN_JOBS_MIGRATIONS: list[tuple[str, str]] = [
    ("location", "TEXT"),
    ("country", "TEXT"),
    ("region", "TEXT"),
    ("city", "TEXT"),
    ("work_arrangement", "TEXT"),
    ("employment_type", "TEXT"),
    # Stage-1 rich fields
    ("job_level", "TEXT"),
    ("job_function", "TEXT"),
    ("experience_range", "TEXT"),
    ("listing_type", "TEXT"),
    ("job_url_direct", "TEXT"),
    ("linkedin_skills", "TEXT"),  # JSON-encoded list
    ("company_url", "TEXT"),
    ("company_logo", "TEXT"),
    ("company_industry", "TEXT"),
    ("company_num_employees", "TEXT"),
    ("compensation_min", "REAL"),
    ("compensation_max", "REAL"),
    ("compensation_currency", "TEXT"),
    ("compensation_interval", "TEXT"),
]

# Columns added to match_history after the initial schema (T57, T41) —
# applied via ``_migrate`` on startup, same pattern as
# ``_SEEN_JOBS_MIGRATIONS``. Nullable: pre-existing rows get
# ``scan_id IS NULL`` / ``agent_verified IS NULL`` rather than being
# rewritten or dropped (no data loss — see module docstring).
#
# ``agent_verified`` (T41): whether the optional Claude verification pass
# ran for this match (``jobfinder/matcher/claude_matcher.py`` sets it on
# ``MatchResult``). Stored as INTEGER (sqlite3 has no native bool) --
# ``save_match()`` writes 0/1, ``get_recent_matches()``/``HistoryCardDTO``
# read it back as falsy/truthy. A pre-migration row has no stored value and
# must read back falsy, matching ``MatchResult.agent_verified``'s own
# ``False`` default rather than inventing a value.
_MATCH_HISTORY_MIGRATIONS: list[tuple[str, str]] = [
    ("scan_id", "TEXT"),
    ("agent_verified", "INTEGER"),
]

# Pipeline stage -> (table, timestamp column) for every stage
# that has a row to write. "new" is deliberately absent — it means "no row
# in any of these tables" and ``set_stage``/``stage_of`` handle it directly.
_STAGE_TABLES: dict[str, tuple[str, str]] = {
    "shortlist": ("shortlisted_jobs", "shortlisted_at"),
    "applied": ("applied_jobs", "applied_at"),
    "replied": ("replied_jobs", "replied_at"),
    "out": ("removed_jobs", "removed_at"),
}

# Reverse lookup used by ``stage_of``.
_STAGE_BY_TABLE: dict[str, str] = {table: stage for stage, (table, _col) in _STAGE_TABLES.items()}

# Job-keyed tables that are *not* part of the pipeline stage machinery
# (the product spec is only about stage) but still hold per-job state a
# factory reset must wipe. Kept as its own explicit tuple rather than
# folded into ``_STAGE_TABLES`` -- a star is not a stage, so it must never
# flow through ``set_stage``/``stage_of``'s mutual-exclusion logic -- but
# ``clear_all`` still derives its table list from this plus
# ``_STAGE_TABLES`` rather than a hand-written list, for the exact reason
# ``clear_all``'s own docstring gives for the stage tables: T79 added two
# stage tables and a hand-written list in ``clear_all`` forgot both of
# them.
_NON_STAGE_JOB_TABLES: tuple[str, ...] = ("starred_jobs",)


class Database:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            from jobfinder.paths import db_path as _default_db_path

            db_path = str(_default_db_path())
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        """Open a brand-new connection with WAL and a busy timeout.

        the product spec: under Uvicorn, request handlers (via
        ``run_in_threadpool``) and the scan thread now open connections
        concurrently, which the single-threaded desktop app never did.
        WAL (Write-Ahead Logging) lets readers proceed while a writer holds
        the lock; ``busy_timeout`` makes a writer that still collides retry
        for up to 30s instead of raising ``sqlite3.OperationalError:
        database is locked`` immediately. Both pragmas are connection-scoped
        in sqlite3, so they are (re)applied here on every new connection,
        not just once at schema-init time. ``timeout=`` is sqlite3's own
        Python-level lock-wait (kept in sync with ``busy_timeout`` for
        belt-and-suspenders consistency).
        """
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextlib.contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """Own one connection for exactly one unit of work.

        the product spec / the task ledger: sqlite3's own connection
        context-manager protocol (``with conn:``) governs only the
        *transaction* — commit on success, rollback on exception — it does
        **not** close the connection. Every one of this class's 22 call
        sites used to rely on that alone, leaving closure to refcounting,
        which already caused a real ``WinError 32`` (file still in use)
        failure during T5's temp-dir cleanup. This wrapper owns both the
        transaction (delegated to ``with conn:``) and the closure
        (``finally: conn.close()``), so every caller below gets both for
        free just by swapping ``self._connect()`` for ``self._session()``.
        """
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._session() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            for stmt in _POST_MIGRATE_INDEXES:
                conn.execute(stmt)
        logger.debug("Database ready at %s", self._path)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema (idempotent)."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(seen_jobs)").fetchall()}
        for col, coltype in _SEEN_JOBS_MIGRATIONS:
            if col not in existing:
                conn.execute(f"ALTER TABLE seen_jobs ADD COLUMN {col} {coltype}")
                logger.info("Migrated seen_jobs: added column '%s'", col)

        existing_mh = {row["name"] for row in conn.execute("PRAGMA table_info(match_history)").fetchall()}
        for col, coltype in _MATCH_HISTORY_MIGRATIONS:
            if col not in existing_mh:
                conn.execute(f"ALTER TABLE match_history ADD COLUMN {col} {coltype}")
                logger.info("Migrated match_history: added column '%s'", col)

        # removed_jobs.removed_by ('user' | 'agent') — added with agentic filter
        removed_cols = {row["name"] for row in conn.execute("PRAGMA table_info(removed_jobs)").fetchall()}
        if "removed_by" not in removed_cols:
            conn.execute("ALTER TABLE removed_jobs ADD COLUMN removed_by TEXT NOT NULL DEFAULT 'user'")
            logger.info("Migrated removed_jobs: added column 'removed_by'")

    # ── Seen-jobs ─────────────────────────────────────────────────────────────

    def filter_unseen(self, jobs: list[JobPosting]) -> list[JobPosting]:
        if not jobs:
            return []
        ids = [j.job_id for j in jobs]
        placeholders = ",".join("?" * len(ids))
        with self._session() as conn:
            rows = conn.execute(f"SELECT job_id FROM seen_jobs WHERE job_id IN ({placeholders})", ids).fetchall()
        seen_ids = {r["job_id"] for r in rows}
        return [j for j in jobs if j.job_id not in seen_ids]

    def mark_seen(self, jobs: list[JobPosting]) -> None:
        if not jobs:
            return
        now = datetime.utcnow().isoformat()
        rows = [
            (
                j.job_id,
                j.source,
                j.title,
                j.company,
                j.url,
                j.location,
                j.country,
                j.region,
                j.city,
                j.work_arrangement,
                j.employment_type,
                j.job_level,
                j.job_function,
                j.experience_range,
                j.listing_type,
                j.job_url_direct,
                json.dumps(j.linkedin_skills or []),
                j.company_url,
                j.company_logo,
                j.company_industry,
                j.company_num_employees,
                j.compensation_min,
                j.compensation_max,
                j.compensation_currency,
                j.compensation_interval,
                now,
            )
            for j in jobs
        ]
        with self._session() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_jobs "
                "(job_id, source, title, company, url, "
                " location, country, region, city, "
                " work_arrangement, employment_type, "
                " job_level, job_function, experience_range, "
                " listing_type, job_url_direct, linkedin_skills, "
                " company_url, company_logo, "
                " company_industry, company_num_employees, "
                " compensation_min, compensation_max, "
                " compensation_currency, compensation_interval, "
                " first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    # ── Match history ─────────────────────────────────────────────────────────

    def save_match(self, result: MatchResult) -> None:
        with self._session() as conn:
            conn.execute(
                "INSERT INTO match_history "
                "(job_id, score, reasoning, skills_matched, skills_missing, matched_at, "
                " scan_id, agent_verified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.job.job_id,
                    result.score,
                    result.reasoning,
                    json.dumps(result.skills_matched),
                    json.dumps(result.skills_missing),
                    result.matched_at.isoformat(),
                    result.scan_id,
                    int(bool(result.agent_verified)),
                ),
            )

    def get_recent_matches(self, limit: int | None = None, *, exclude_removed: bool = True) -> list[dict]:
        """Return match history, newest first. ``limit=None`` returns all rows.

        ``sj.company_url`` (T58): the "company site" card action
        (the parity matrix's card-actions row / DESIGN.md) reads this through
        ``dict_to_card`` -- it was already a ``HistoryCardDTO`` field but
        this SELECT never fetched the column, so every Matches-tab card
        silently got ``dict_to_card``'s "" default regardless of what a
        scraper actually captured.

        ``sj.listing_type``/``sj.company_industry`` (T59): same gap,
        found the same way -- the filter panel's "Promoted listings"/
        "Staffing agencies" hide toggles are already enforced by
        ``_card_hidden_by_tags`` (jobfinder/filters.py), but neither
        column was ever selected here, so a Matches-tab card always got
        "" for both regardless of what was scraped and neither toggle
        could ever act on a history row.

        ``sj.job_level``/``job_function``/``experience_range``/
        ``job_url_direct``/``linkedin_skills``/``company_logo``/
        ``company_num_employees``/the four ``compensation_*`` columns
        (T62): same gap, same fix. Two of these are a third and fourth
        instance of a *dead filter*, not just missing display data --
        ``quality_filter.min_company_size`` reads
        ``card["company_num_employees"]`` and ``min_salary`` reads
        ``card["compensation_min"]``/``compensation_max``
        (``jobfinder/filters.py``), so both silently never hid anything
        on the Matches tab for exactly the reason ``hide_promoted``/
        ``hide_staffing_agencies`` didn't before T59.

        **Not fixed here, a different kind of gap:** ``date_posted`` has
        no ``seen_jobs`` column at all -- it is never persisted, only
        ever present on the in-memory ``JobPosting`` during a live scan
        (``MatchCardDTO.from_match_result``), so a History-tab card's
        ``date_posted`` is always ``""`` by construction, not by a missed
        SELECT. That also means ``quality_filter.max_post_age_days`` is
        dead on *every* card, live or historical: ``_card_hidden_by_tags``
        reads ``card.get("post_age_days")``, a key nothing in this
        codebase ever sets (not even the live DTO, which carries
        ``date_posted`` under a different key with no conversion to a
        day-count anywhere). Fixing it needs a new persisted column plus
        a `post_age_days` computation at read time, not a one-line SELECT
        addition like the others above -- flagged for a dedicated task
        rather than folded into this fix silently.

        ``mh.agent_verified`` (T41): collapses the ``MatchCardDTO`` /
        ``HistoryCardDTO`` field asymmetry -- ``save_match()`` now persists
        the flag ``claude_matcher`` sets, so the "✓ AI" badge a live scan
        shows survives into history instead of defaulting to falsy on
        every relaunch. A pre-migration row has ``agent_verified IS NULL``,
        which reads back falsy here and in ``HistoryCardDTO``, same as a
        match that was never agent-verified.

        **Ordering (T62):** ``docs/DESIGN.md`` principle 2 requires
        batches newest-first *and* ranking within a batch. Before this fix
        the ``ORDER BY`` had no score term at all, so rows within one scan
        tied on ``matched_at`` (several jobs scored within the same second
        is the common case) fell back to SQLite's rowid/insertion order --
        i.e. whatever order the matcher happened to process them in, not
        best-match-first. The client
        (``frontend/src/components/jobBatching.ts``) only groups by
        ``scan_id``; it never re-sorts, so this had to be fixed server-side,
        the one place that already decides "which jobs were
        found together". The subquery computes each scan's most recent
        ``matched_at`` so every row in a batch shares one sort key for the
        *between*-batch ordering (batches still newest-first), while
        ``mh.score DESC`` breaks ties *within* that key for the
        *within*-batch ranking. A null/legacy ``scan_id`` row (pre-T57) has
        no batch-mates to rank against, so it falls back to its own
        ``matched_at`` via ``COALESCE`` -- unchanged behaviour for it.

        ``exclude_removed`` (T80): every pre-existing caller wants the
        historical "Matches tab" behaviour (default ``True``) of hiding a
        removed job. ``GET /api/board`` (``jobfinder/web/jobs.py``) is the
        one caller that needs the opposite -- it must see every job with
        match history, including ones already in a later pipeline stage,
        so it can bulk-resolve ``stage_of`` for all of them (T80's "four
        queries, not 200" requirement) and route each to its one column
        rather than silently dropping the staged ones from the dataset.
        """
        sql = """
            SELECT mh.score, mh.reasoning, mh.skills_matched, mh.skills_missing,
                   mh.matched_at, mh.scan_id, mh.agent_verified, sj.job_id, sj.title,
                   sj.company, sj.url, sj.source,
                   sj.location, sj.country, sj.region, sj.city,
                   sj.work_arrangement, sj.employment_type, sj.company_url,
                   sj.listing_type, sj.company_industry,
                   sj.job_level, sj.job_function, sj.experience_range,
                   sj.job_url_direct, sj.linkedin_skills, sj.company_logo,
                   sj.company_num_employees, sj.compensation_min,
                   sj.compensation_max, sj.compensation_currency,
                   sj.compensation_interval
            FROM match_history mh
            JOIN seen_jobs sj ON sj.job_id = mh.job_id
            LEFT JOIN (
                SELECT scan_id, MAX(matched_at) AS batch_latest
                FROM match_history
                WHERE scan_id IS NOT NULL
                GROUP BY scan_id
            ) batch ON batch.scan_id = mh.scan_id
        """
        if exclude_removed:
            sql += " WHERE sj.job_id NOT IN (SELECT job_id FROM removed_jobs)\n"
        sql += " ORDER BY COALESCE(batch.batch_latest, mh.matched_at) DESC, mh.score DESC"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Applied jobs ──────────────────────────────────────────────────────────

    def mark_applied(self, job_id: str) -> None:
        """Record that the user has applied to this job.

        ``INSERT OR IGNORE``, not ``OR REPLACE`` (T42): ``REPLACE`` deletes the
        existing row and inserts a fresh one, which resets ``applied_at`` to
        now. The row count stays 1 either way, so it reads as idempotent --
        but the datum it destroys is the date the user actually applied, and a
        web UI re-sends far more readily than the desktop click this was ported
        from (double-submits, retries, T20's reconnect logic).
        """
        with self._session() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO applied_jobs (job_id, applied_at) VALUES (?, ?)",
                (job_id, datetime.utcnow().isoformat()),
            )

    def unmark_applied(self, job_id: str) -> None:
        """Remove a job from the applied list."""
        with self._session() as conn:
            conn.execute("DELETE FROM applied_jobs WHERE job_id = ?", (job_id,))

    def clear_applied(self) -> None:
        """Delete all applied jobs."""
        with self._session() as conn:
            conn.execute("DELETE FROM applied_jobs")

    def get_applied_job_ids(self) -> set[str]:
        """Return the set of all job_ids the user has applied to."""
        with self._session() as conn:
            rows = conn.execute("SELECT job_id FROM applied_jobs").fetchall()
        return {r["job_id"] for r in rows}

    # ── Removed jobs ──────────────────────────────────────────────────────────

    def mark_removed(self, job_id: str, by: str = "user") -> None:
        """Permanently hide a job from the Matches tab.

        ``by`` is either ``"user"`` (manual Remove click) or ``"agent"``
        (disqualified by the optional Claude verification pass).
        """
        with self._session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO removed_jobs (job_id, removed_at, removed_by) VALUES (?, ?, ?)",
                (job_id, datetime.utcnow().isoformat(), by),
            )

    def unmark_removed(self, job_id: str) -> None:
        """Restore a previously removed job to the Matches tab."""
        with self._session() as conn:
            conn.execute("DELETE FROM removed_jobs WHERE job_id = ?", (job_id,))

    def clear_removed(self) -> None:
        """Delete all removed jobs (they will reappear in Matches on the next scan)."""
        with self._session() as conn:
            conn.execute("DELETE FROM removed_jobs")

    # ── Pipeline stage ───────────────────────────────────────

    def stage_of(self, job_id: str) -> str:
        """Return the single pipeline stage ``job_id`` is in.

        One of ``"new"``, ``"shortlist"``, ``"applied"``, ``"replied"``,
        ``"out"``. A job with no row in any of the four stage tables reads
        as ``"new"``.

        Precedence (the product spec — "a job is in exactly one stage",
        made true here rather than by convention): ``replied > applied >
        shortlist > out > new``. Nothing before this method stopped a job
        from landing in two stage tables at once (e.g. ``mark_applied``
        then ``mark_removed`` on the same ``job_id``), so a pre-existing
        database can already have overlaps; this order decides which table
        wins for such a row without needing a backfill migration.
        Later-in-the-pipeline wins because it is the state the user most
        recently and most deliberately asserted — applying is a stronger,
        later action than shortlisting, replying is stronger still, and a
        removal is the one state the *user* didn't necessarily choose last
        for that job (it may predate a later shortlist/apply click, or be
        the "agent" auto-remove from the verification pass) so it ranks
        below the three deliberate forward moves but still above a blank
        "new".
        """
        with self._session() as conn:
            for table in ("replied_jobs", "applied_jobs", "shortlisted_jobs", "removed_jobs"):
                if conn.execute(f"SELECT 1 FROM {table} WHERE job_id = ?", (job_id,)).fetchone():
                    return _STAGE_BY_TABLE[table]
        return "new"

    def set_stage(self, job_id: str, stage: str, *, note: str | None = None, removed_by: str = "user") -> None:
        """Move ``job_id`` to ``stage``, atomically.

        Deletes the job from every *other* stage table in the same
        transaction as the insert, which is what makes "a job is in exactly
        one stage" a fact the code enforces rather
        than a convention callers have to remember. ``stage="new"`` means
        "in none of them" — nothing is inserted.

        Idempotent: setting the stage a job already occupies does not touch
        its stored timestamp (``INSERT OR IGNORE`` — see ``mark_applied``'s
        docstring for why ``OR REPLACE`` is the wrong tool here).
        """
        if stage not in _STAGE_TABLES and stage != "new":
            raise ValueError(f"unknown pipeline stage: {stage!r}")

        with self._session() as conn:
            for other_stage, (table, _col) in _STAGE_TABLES.items():
                if other_stage != stage:
                    conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))

            if stage == "new":
                return

            table, col = _STAGE_TABLES[stage]
            now = datetime.utcnow().isoformat()
            if stage == "replied":
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} (job_id, {col}, note) VALUES (?, ?, ?)",
                    (job_id, now, note),
                )
            elif stage == "out":
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} (job_id, {col}, removed_by) VALUES (?, ?, ?)",
                    (job_id, now, removed_by),
                )
            else:
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} (job_id, {col}) VALUES (?, ?)",
                    (job_id, now),
                )

    # ── Starred jobs (T101) ───────────────────────────────────
    #
    # "A star is not a stage" — the owner wants starred jobs floated to the
    # top of whatever column they are already in, not moved to a new one.
    # ``starred_jobs`` is therefore a separate table, following the same
    # idiom as ``applied_jobs``/``shortlisted_jobs`` above, and
    # deliberately never touched by ``set_stage``/``stage_of`` or
    # ``_STAGE_TABLES`` -- doing so would make starring a sixth pipeline
    # stage and break the product spec ("a job is in exactly one
    # stage"), which has nothing to do with whether it is starred.

    def set_starred(self, job_id: str, starred: bool) -> None:
        """Star or unstar a job. Does not read or write any stage table.

        ``INSERT OR IGNORE`` (not ``OR REPLACE``) on star, same reasoning
        as ``mark_applied``'s docstring: re-starring an already-starred
        job must not reset ``starred_at``.
        """
        with self._session() as conn:
            if starred:
                conn.execute(
                    "INSERT OR IGNORE INTO starred_jobs (job_id, starred_at) VALUES (?, ?)",
                    (job_id, datetime.utcnow().isoformat()),
                )
            else:
                conn.execute("DELETE FROM starred_jobs WHERE job_id = ?", (job_id,))

    def get_starred_job_ids(self) -> set[str]:
        """Return the set of every starred job_id, regardless of stage."""
        with self._session() as conn:
            rows = conn.execute("SELECT job_id FROM starred_jobs").fetchall()
        return {r["job_id"] for r in rows}

    # ── Skipped companies ─────────────────────────────────────────────────────

    def add_skipped_company(self, name: str) -> bool:
        """Add a company to the skip list. Returns False if the name is unusable
        or already present."""
        display = (name or "").strip()
        key = normalize_company(display)
        if not key:
            return False
        with self._session() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO skipped_companies (name_key, display, added_at) VALUES (?, ?, ?)",
                (key, display, datetime.utcnow().isoformat()),
            )
            return cur.rowcount > 0

    def remove_skipped_company(self, name_key: str) -> None:
        with self._session() as conn:
            conn.execute("DELETE FROM skipped_companies WHERE name_key = ?", (name_key,))

    def clear_skipped_companies(self) -> None:
        with self._session() as conn:
            conn.execute("DELETE FROM skipped_companies")

    def get_skipped_companies(self) -> list[dict]:
        """Return every skip-list entry, alphabetical by display name."""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT name_key, display, added_at FROM skipped_companies ORDER BY display COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_skipped_company_keys(self) -> set[str]:
        with self._session() as conn:
            rows = conn.execute("SELECT name_key FROM skipped_companies").fetchall()
        return {r["name_key"] for r in rows}

    def is_company_skipped(self, company: str) -> bool:
        return company_matches_skip(company, self.get_skipped_company_keys())

    # ── Full reset ────────────────────────────────────────────────────────────

    def clear_match_history(self) -> None:
        """Delete all match history records (jobs remain seen — they won't resurface)."""
        with self._session() as conn:
            conn.execute("DELETE FROM match_history")

    def clear_all(self) -> None:
        """Wipe every job table — all jobs become unseen so the next scan starts
        fresh. The company skip list is a preference, not job data, so it survives.

        Derived from ``_STAGE_TABLES`` rather than listed by hand. T79 added
        ``shortlisted_jobs`` and ``replied_jobs`` and this method kept its
        hand-written list, so a factory reset left those two tables standing
        while claiming in the UI to erase "every job record JobFinder has
        seen". The user-visible cost was not only the stale rows: ``seen_jobs``
        *was* cleared, so a job that resurfaced on a later scan rejoined its
        orphaned stage row and reappeared already shortlisted or replied, with
        no action by the user. Found by Phase 9's independent review.

        Listing the stage tables by hand is what made that possible, so this
        no longer does: a stage added to ``_STAGE_TABLES`` is cleared here
        without anyone remembering to come back.

        T101 added ``starred_jobs`` -- a star is not a stage (it must
        never join ``_STAGE_TABLES``, see that table's module comment),
        so it cannot ride along with the loop above for free. It is
        cleared explicitly here via ``_NON_STAGE_JOB_TABLES`` instead, the
        same "derived from a table list, not hand-copied" discipline, so
        a future non-stage per-job table only has to be added to one
        tuple rather than remembered at this specific call site too.
        """
        with self._session() as conn:
            for table, _col in _STAGE_TABLES.values():
                conn.execute(f"DELETE FROM {table}")
            for table in _NON_STAGE_JOB_TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM match_history")
            conn.execute("DELETE FROM seen_jobs")

    def get_removed_job_ids(self) -> set[str]:
        """Return the set of all job_ids the user has removed."""
        with self._session() as conn:
            rows = conn.execute("SELECT job_id FROM removed_jobs").fetchall()
        return {r["job_id"] for r in rows}

    def get_removed_jobs(self, limit: int | None = None) -> list[dict]:
        """Return removed jobs enriched with match data, newest first.

        Each row carries a ``removed_by`` field (``"user"`` or ``"agent"``) so
        callers can partition the list into sub-tabs. ``limit=None`` returns
        every row.
        """
        sql = """
            SELECT rj.removed_at, rj.removed_by,
                   sj.job_id, sj.title, sj.company, sj.url, sj.source,
                   sj.location, sj.country, sj.region, sj.city,
                   sj.work_arrangement, sj.employment_type,
                   sj.job_level, sj.job_function, sj.experience_range,
                   sj.listing_type, sj.job_url_direct, sj.linkedin_skills,
                   sj.company_url, sj.company_logo,
                   sj.company_industry, sj.company_num_employees,
                   sj.compensation_min, sj.compensation_max,
                   sj.compensation_currency, sj.compensation_interval,
                   COALESCE(mh.score, 0) AS score,
                   mh.skills_matched, mh.reasoning, mh.matched_at
            FROM removed_jobs rj
            JOIN seen_jobs sj ON sj.job_id = rj.job_id
            LEFT JOIN match_history mh ON mh.job_id = rj.job_id
            ORDER BY rj.removed_at DESC
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_applied_jobs(self, limit: int | None = None) -> list[dict]:
        """Return applied jobs enriched with match data, newest first.

        ``limit=None`` returns every row.
        """
        sql = """
            SELECT aj.applied_at, sj.job_id, sj.title, sj.company, sj.url, sj.source,
                   sj.location, sj.country, sj.region, sj.city,
                   sj.work_arrangement, sj.employment_type,
                   sj.job_level, sj.job_function, sj.experience_range,
                   sj.listing_type, sj.job_url_direct, sj.linkedin_skills,
                   sj.company_url, sj.company_logo,
                   sj.company_industry, sj.company_num_employees,
                   sj.compensation_min, sj.compensation_max,
                   sj.compensation_currency, sj.compensation_interval,
                   COALESCE(mh.score, 0) AS score,
                   mh.skills_matched, mh.reasoning, mh.matched_at
            FROM applied_jobs aj
            JOIN seen_jobs sj ON sj.job_id = aj.job_id
            LEFT JOIN match_history mh ON mh.job_id = aj.job_id
            ORDER BY aj.applied_at DESC
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_shortlisted_jobs(self, limit: int | None = None) -> list[dict]:
        """Return shortlisted jobs enriched with match data, newest first.

        Mirrors ``get_applied_jobs``/``get_removed_jobs`` (T80 board
        column). ``limit=None`` returns every row.
        """
        sql = """
            SELECT shj.shortlisted_at, sj.job_id, sj.title, sj.company, sj.url, sj.source,
                   sj.location, sj.country, sj.region, sj.city,
                   sj.work_arrangement, sj.employment_type,
                   sj.job_level, sj.job_function, sj.experience_range,
                   sj.listing_type, sj.job_url_direct, sj.linkedin_skills,
                   sj.company_url, sj.company_logo,
                   sj.company_industry, sj.company_num_employees,
                   sj.compensation_min, sj.compensation_max,
                   sj.compensation_currency, sj.compensation_interval,
                   COALESCE(mh.score, 0) AS score,
                   mh.skills_matched, mh.reasoning, mh.matched_at, mh.scan_id, mh.agent_verified
            FROM shortlisted_jobs shj
            JOIN seen_jobs sj ON sj.job_id = shj.job_id
            LEFT JOIN match_history mh ON mh.job_id = shj.job_id
            ORDER BY shj.shortlisted_at DESC
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_replied_jobs(self, limit: int | None = None) -> list[dict]:
        """Return replied jobs enriched with match data, newest first.

        Each row carries ``note`` (the optional reply note, T79's
        ``replied_jobs.note`` column) alongside the enrichment columns
        ``get_applied_jobs``/``get_removed_jobs`` already select.
        ``limit=None`` returns every row.
        """
        sql = """
            SELECT rpj.replied_at, rpj.note, sj.job_id, sj.title, sj.company, sj.url, sj.source,
                   sj.location, sj.country, sj.region, sj.city,
                   sj.work_arrangement, sj.employment_type,
                   sj.job_level, sj.job_function, sj.experience_range,
                   sj.listing_type, sj.job_url_direct, sj.linkedin_skills,
                   sj.company_url, sj.company_logo,
                   sj.company_industry, sj.company_num_employees,
                   sj.compensation_min, sj.compensation_max,
                   sj.compensation_currency, sj.compensation_interval,
                   COALESCE(mh.score, 0) AS score,
                   mh.skills_matched, mh.reasoning, mh.matched_at, mh.scan_id, mh.agent_verified
            FROM replied_jobs rpj
            JOIN seen_jobs sj ON sj.job_id = rpj.job_id
            LEFT JOIN match_history mh ON mh.job_id = rpj.job_id
            ORDER BY rpj.replied_at DESC
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def group_matches_by_scan(rows: list[dict]) -> list[list[dict]]:
    """Group ``get_recent_matches()`` rows into batches by ``scan_id`` (T57).

    Order-preserving: each batch first appears at the position of its
    first row, and rows keep their relative order within a batch. A row
    with ``scan_id is None`` (written before this migration, or any other
    caller that never set one) is never merged with another row — each is
    its own singleton batch, matching what "no batch information" meant
    before ``scan_id`` existed.
    """
    batches: list[list[dict]] = []
    index_by_scan: dict[str, int] = {}
    for row in rows:
        scan_id = row.get("scan_id")
        if scan_id is None:
            batches.append([row])
            continue
        if scan_id in index_by_scan:
            batches[index_by_scan[scan_id]].append(row)
        else:
            index_by_scan[scan_id] = len(batches)
            batches.append([row])
    return batches
