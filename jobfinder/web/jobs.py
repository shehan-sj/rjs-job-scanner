"""Job endpoints (T17): matches / applied / removed / skip-company.

Every handler below is ``async def`` but never calls a ``Database`` method
directly — a synchronous ``sqlite3`` call inside an ``async def`` endpoint
blocks the whole Uvicorn event loop, including the SSE stream T20 adds
later. Each handler instead delegates its actual
work to a plain, synchronous module-level ``_*_sync`` helper and awaits it
via ``fastapi.concurrency.run_in_threadpool`` — one threadpool hop per
request, not one per ``Database`` call.

Filtering (tag-visibility hide toggles, role filter, quality filter) is
applied here exactly as ``jobfinder/ui/widget.py`` already did it, by
importing the *same* helpers from ``jobfinder/filters.py`` — this module
must never reimplement a filter rule itself (the product spec behaviour-parity
requirement, same constraint ``jobfinder/filters.py``'s own docstring
states for ``widget.py``).

Response shapes come from ``jobfinder/dto.py``'s ``dict_to_card`` /
``result_to_card`` — the implicit API contract already pulled out of
``widget.py`` for exactly this reason. Applied/removed responses layer a
couple of extra keys (``applied_at``, ``removed_at``, ``removed_by`` — see
the old ``_dict_to_applied_card`` / ``_dict_to_removed_card`` helpers in
``widget.py`` for the historical precedent) onto the card *dict* returned
by ``dict_to_card`` rather than defining new DTO classes, since
``MatchCardDTO``/``HistoryCardDTO`` are ``extra="forbid"`` pydantic models
and these endpoints return plain dicts, not those model instances.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..dto import dict_to_card
from ..filters import (
    _card_hidden_by_tags,
    _classify_tier,
    country_tier_is_configured,
    tier_location_is_unresolved,
)
from ..storage.database import Database, company_matches_skip, normalize_company

router = APIRouter(prefix="/api")

# The five pipeline stages. Order matches the board's column
# order and ``_STAGE_TIMESTAMP_COLUMN``/``_STAGE_EXTRA_FIELD`` below.
STAGES: tuple[str, ...] = ("new", "shortlist", "applied", "replied", "out")

# Per-stage (table-row key -> board-card key) for the one field each
# non-"new" stage's enrichment query names differently from the rest
# (``get_shortlisted_jobs``/``get_applied_jobs``/``get_replied_jobs``/
# ``get_removed_jobs`` each carry their own "entered this stage at" column).
_STAGE_TIMESTAMP_FIELD: dict[str, str] = {
    "shortlist": "shortlisted_at",
    "applied": "applied_at",
    "replied": "replied_at",
    "out": "removed_at",
}


class SkipCompanyRequest(BaseModel):
    name: str


class SetStageRequest(BaseModel):
    stage: str
    note: str | None = None
    removed_by: str = "user"


class SetStarredRequest(BaseModel):
    starred: bool


# ── Synchronous helpers — all actual Database access happens here, off the
#    event loop, via run_in_threadpool in the route handlers below. ────────


def _list_matches_sync(db: Database, config) -> list[dict]:
    """Matches tab: history rows minus applied/removed/skipped/tag-hidden.

       ``get_recent_matches`` already excludes ``removed_jobs`` at the SQL
       level; the ``hidden`` union below also drops applied jobs, matching
       ``widget.py``'s historical ``load_history``/``add_matches`` behaviour
       (an applied job moves to the Applied tab and leaves Matches).

       Each card also carries a ``tier`` key (``preferred``/``country``/
       ``global``), computed here via ``_classify_tier`` — the single
       server-side implementation of the three-tier location classification
    — so the frontend's Matches sub-tabs (T23) never need
       to reimplement that rule in TypeScript.

       ``config.match_threshold`` is enforced here too (T59). It used to
       only gate whether a *new* scan result got saved as a match at all
       (``orchestrator.py``'s ``run_scan``/``_verify_with_agent``) — raising
       or lowering it after the fact had no effect on matches already in
       history, which contradicts docs/DESIGN.md principle 3's "changing
       [a filter] re-filters the visible list" for the one control
       (Minimum score) that control is actually about. This re-check is
       live and reversible — lowering the threshold again brings a card
       back, it does not prune history the way ``clear_matches`` does.
    """
    rows = db.get_recent_matches()
    hidden = db.get_applied_job_ids() | db.get_removed_job_ids()
    skip_keys = db.get_skipped_company_keys()
    preferred_regions = config.location_filter.preferred_regions if config else []
    search_country = config.location_filter.search_country if config else ""
    match_threshold = config.match_threshold if config else 0

    cards: list[dict] = []
    for row in rows:
        if row["job_id"] in hidden:
            continue
        if company_matches_skip(row.get("company", ""), skip_keys):
            continue
        card = dict_to_card(row)
        if card.get("score", 0) < match_threshold:
            continue
        if _card_hidden_by_tags(card, config):
            continue
        card["tier"] = _classify_tier(card, preferred_regions, search_country)
        cards.append(card)
    return cards


def _list_applied_sync(db: Database) -> list[dict]:
    cards: list[dict] = []
    for row in db.get_applied_jobs():
        card = dict_to_card(row)
        card["applied_at"] = row.get("applied_at", "")
        cards.append(card)
    return cards


def _list_removed_sync(db: Database) -> list[dict]:
    cards: list[dict] = []
    for row in db.get_removed_jobs():
        card = dict_to_card(row)
        card["removed_at"] = row.get("removed_at", "")
        card["removed_by"] = row.get("removed_by", "user")
        cards.append(card)
    return cards


def _board_card(row: dict, *, stage: str, starred_ids: set[str]) -> dict:
    """Build one board card: the 34 ``MATCH_CARD_FIELDS`` plus T80's five
    board-specific additions (``stage``, ``stage_entered_at``,
    ``removed_by``, ``reply_note``, ``tier`` — the last is filled in by the
    caller, which has the location-filter config this function does not).

    T81 adds ``tier_location_is_unresolved`` (``jobfinder/filters.py``,
    T84): whether this card's location had content but no country could be
    recognized from it, so the board's "location could not be parsed"
    card flag has real data behind it rather than guessing from the
    ``global`` tier alone (which also covers genuine worldwide-remote
    matches — see that helper's own docstring).

    T101 adds ``starred``: whether this job is in ``starred_jobs``,
    independent of ``stage`` -- a star is not a stage (the product spec rule
    1), so ``starred_ids`` is looked up once per board build
    (``Database.get_starred_job_ids``), not derived from anything in
    ``row``.
    """
    card = dict_to_card(row)
    card["stage"] = stage
    ts_field = _STAGE_TIMESTAMP_FIELD.get(stage)
    card["stage_entered_at"] = row.get(ts_field) if ts_field else None
    card["removed_by"] = row.get("removed_by") if stage == "out" else None
    card["reply_note"] = row.get("note") if stage == "replied" else None
    card["tier_location_is_unresolved"] = tier_location_is_unresolved(row)
    card["starred"] = row.get("job_id") in starred_ids
    return card


def _list_board_sync(db: Database, config) -> dict[str, list[dict]]:
    """``GET /api/board``: all five pipeline stages in one
    response.

    Performance (T80): resolves every job's stage with **one query per
    stage table** (``get_shortlisted_jobs``/``get_applied_jobs``/
    ``get_replied_jobs``/``get_removed_jobs``, four calls total) plus one
    query for the candidate pool (``get_recent_matches(exclude_removed=
    False)``) -- never ``stage_of()`` per job, which would be one query
    per job on a board that can hold hundreds.

    Precedence (same as ``Database.stage_of``, the product spec --
    "a job is in exactly one stage"): replied > applied > shortlist > out
    > new. ``set_stage`` has kept the four stage tables mutually exclusive
    since T79, but a legacy/pre-T79 row can still overlap two tables, so
    this resolves precedence in Python rather than assuming exclusivity.

    Filtering (T89, found while wiring the board filter control): before
    this task, ``match_threshold``/``tag_visibility``/``quality_filter``
    were enforced for the Matches tab (``_list_matches_sync`` above) but
    never for the board, so changing a filter had no visible effect —
    exactly the product spec regression this task exists to
    fix. The fix is scoped to the **New** column only, the board's
    equivalent of "unstaged candidate pool" that ``_list_matches_sync``
    filters: Shortlist/Applied/Replied/Not-for-me hold jobs the user has
    already acted on, and (like ``_list_applied_sync``/
    ``_list_removed_sync``, which never filtered either) must not vanish
    retroactively because a later score/tag filter tightened.
    """
    replied_rows = db.get_replied_jobs()
    applied_rows = db.get_applied_jobs()
    shortlisted_rows = db.get_shortlisted_jobs()
    removed_rows = db.get_removed_jobs()

    replied_ids = {r["job_id"] for r in replied_rows}
    applied_rows = [r for r in applied_rows if r["job_id"] not in replied_ids]
    applied_ids = {r["job_id"] for r in applied_rows}
    shortlisted_rows = [r for r in shortlisted_rows if r["job_id"] not in replied_ids | applied_ids]
    shortlisted_ids = {r["job_id"] for r in shortlisted_rows}
    removed_rows = [r for r in removed_rows if r["job_id"] not in replied_ids | applied_ids | shortlisted_ids]
    removed_ids = {r["job_id"] for r in removed_rows}

    staged_ids = replied_ids | applied_ids | shortlisted_ids | removed_ids
    new_rows = [r for r in db.get_recent_matches(exclude_removed=False) if r["job_id"] not in staged_ids]

    # T101: one lookup for the whole board, independent of stage -- a
    # star is not a stage, so it is not resolved per-column the way
    # ``_board_card``'s other fields are.
    starred_ids = db.get_starred_job_ids()

    preferred_regions = config.location_filter.preferred_regions if config else []
    search_country = config.location_filter.search_country if config else ""
    # T81/the product spec: "same country" is a filter over the board now, and
    # when it isn't configured at all (T84's `country_tier_is_configured`)
    # the filter must say so rather than silently rendering as zero
    # matches. Carried on every card (not a new top-level board key) so
    # `test_board_carries_every_enumerated_field_in_every_column`'s
    # `set(board.keys()) == set(STAGES)` assertion stays true.
    same_country_configured = country_tier_is_configured(search_country, preferred_regions)
    match_threshold = config.match_threshold if config else 0

    board: dict[str, list[dict]] = {stage: [] for stage in STAGES}
    for stage, rows in (
        ("new", new_rows),
        ("shortlist", shortlisted_rows),
        ("applied", applied_rows),
        ("replied", replied_rows),
        ("out", removed_rows),
    ):
        for row in rows:
            card = _board_card(row, stage=stage, starred_ids=starred_ids)
            if stage == "new":
                if card.get("score", 0) < match_threshold:
                    continue
                if _card_hidden_by_tags(card, config):
                    continue
            card["tier"] = _classify_tier(card, preferred_regions, search_country)
            card["same_country_tier_configured"] = same_country_configured
            board[stage].append(card)
    return board


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/matches")
async def list_matches(request: Request) -> list[dict]:
    db: Database = request.app.state.db
    config = request.app.state.config
    return await run_in_threadpool(_list_matches_sync, db, config)


@router.get("/applied")
async def list_applied(request: Request) -> list[dict]:
    db: Database = request.app.state.db
    return await run_in_threadpool(_list_applied_sync, db)


@router.get("/removed")
async def list_removed(request: Request) -> list[dict]:
    db: Database = request.app.state.db
    return await run_in_threadpool(_list_removed_sync, db)


@router.get("/board")
async def get_board(request: Request) -> dict[str, list[dict]]:
    """The product spec: all five pipeline stages in one response."""
    db: Database = request.app.state.db
    config = request.app.state.config
    return await run_in_threadpool(_list_board_sync, db, config)


@router.post("/jobs/{job_id}/stage")
async def set_job_stage(job_id: str, payload: SetStageRequest, request: Request) -> dict:
    """The single pipeline write path. Delegates
    straight to ``Database.set_stage`` -- idempotent, and the five
    existing endpoints below are now thin wrappers over this same call."""
    if payload.stage not in STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown pipeline stage {payload.stage!r}; valid stages are: {', '.join(STAGES)}",
        )
    db: Database = request.app.state.db
    await run_in_threadpool(db.set_stage, job_id, payload.stage, note=payload.note, removed_by=payload.removed_by)
    return {"status": "ok"}


@router.post("/jobs/{job_id}/star")
async def set_job_starred(job_id: str, payload: SetStarredRequest, request: Request) -> dict:
    """The single star write path (T101) — mirrors ``set_job_stage`` above,
    but deliberately never calls ``set_stage``/``stage_of``. A star is not
    a stage: a job can be starred in any column, and
    starring must not move it.
    """
    db: Database = request.app.state.db
    await run_in_threadpool(db.set_starred, job_id, payload.starred)
    return {"status": "ok"}


@router.get("/jobs/{job_id}/description")
async def get_job_description(job_id: str, request: Request) -> dict:
    """The posting's own text, fetched when the detail panel opens.

    Its own endpoint rather than a field on the card: ``GET /api/board``
    returns every card across all five stages, and a description runs to
    several KB, so carrying it there would multiply a response the board
    already streams for hundreds of cards — to show text that is only ever
    read one job at a time.

    ``stored`` distinguishes the two reasons the text can be absent, which
    the panel words differently: ``false`` means this job was found by a
    scan that ran before descriptions were kept (nothing is wrong, and a
    rescan will fill it in), rather than the posting genuinely having no
    body. 200 either way — "this job has no description to show" is an
    answer, not a failure, and a 404 here would make the panel's fetch
    look like a broken route.
    """
    db: Database = request.app.state.db
    description = await run_in_threadpool(db.get_job_description, job_id)
    return {"description": description, "stored": description is not None}


@router.post("/matches/{job_id}/apply")
async def apply_job(job_id: str, request: Request) -> dict:
    """Thin wrapper over ``Database.set_stage`` (the product spec — see
    ``set_job_stage`` above, the one write path).

    **Behaviour change (T80, deliberate):** before T80 this called
    ``mark_applied``, which never touched ``removed_jobs`` — a job could be
    "applied" and "removed" at once. ``set_stage`` makes that impossible
    ("a job is in exactly one stage"), so applying a
    job that was previously removed now also un-removes it. See
    ``tests/test_api_board.py::test_applying_a_previously_removed_job_un_removes_it``.

    ``set_stage``'s ``INSERT OR IGNORE`` (not ``mark_applied``'s old ``OR
    REPLACE``) keeps a repeat apply idempotent on the *row count* exactly
    as before, and additionally now leaves the original ``applied_at`` in
    place rather than refreshing it — a strictly stronger idempotency
    guarantee, not a weaker one.
    """
    db: Database = request.app.state.db
    await run_in_threadpool(db.set_stage, job_id, "applied")
    return {"status": "ok"}


@router.post("/matches/{job_id}/remove")
async def remove_job(job_id: str, request: Request) -> dict:
    """Thin wrapper over ``Database.set_stage``."""
    db: Database = request.app.state.db
    await run_in_threadpool(db.set_stage, job_id, "out", removed_by="user")
    return {"status": "ok"}


@router.post("/matches/{job_id}/restore")
async def restore_job(job_id: str, request: Request) -> dict:
    """Thin wrapper over ``Database.set_stage``. Moves
    the job back to ``"new"`` rather than only deleting its
    ``removed_jobs`` row, so a job somehow staged elsewhere as well as
    removed (legacy overlap, see ``stage_of``'s docstring) is fully reset
    rather than left half-removed.
    """
    db: Database = request.app.state.db
    await run_in_threadpool(db.set_stage, job_id, "new")
    return {"status": "ok"}


@router.post("/applied/{job_id}/unapply")
async def unapply_job(job_id: str, request: Request) -> dict:
    """Thin wrapper over ``Database.set_stage``. Undo
    action on an Applied card (``widget.py``'s ``_unapply_job``): the job
    drops off Applied and reappears in Matches on the next load, since
    ``_list_matches_sync`` only excludes jobs still in ``applied_jobs``.
    """
    db: Database = request.app.state.db
    await run_in_threadpool(db.set_stage, job_id, "new")
    return {"status": "ok"}


@router.post("/matches/clear")
async def clear_matches(request: Request) -> dict:
    """Clear-all for the Matches tab (``widget.py``'s ``_clear_matches``).
    Jobs stay in the ``seen`` list so they won't reappear on the next scan.
    """
    db: Database = request.app.state.db
    await run_in_threadpool(db.clear_match_history)
    return {"status": "ok"}


@router.post("/applied/clear")
async def clear_applied_jobs(request: Request) -> dict:
    """Clear-all for the Applied tab (``widget.py``'s ``_clear_applied``)."""
    db: Database = request.app.state.db
    await run_in_threadpool(db.clear_applied)
    return {"status": "ok"}


@router.post("/removed/clear")
async def clear_removed_jobs(request: Request) -> dict:
    """Clear-all for the Removed tab (``widget.py``'s ``_clear_removed``)."""
    db: Database = request.app.state.db
    await run_in_threadpool(db.clear_removed)
    return {"status": "ok"}


@router.post("/skip-company", status_code=201)
async def skip_company(payload: SkipCompanyRequest, request: Request) -> dict:
    # Pure string normalisation, not a Database call — safe to run inline.
    if not normalize_company(payload.name):
        raise HTTPException(status_code=400, detail="invalid company name")
    db: Database = request.app.state.db
    added = await run_in_threadpool(db.add_skipped_company, payload.name)
    return {"status": "ok", "added": added}


@router.get("/skipped")
async def list_skipped(request: Request) -> list[dict]:
    db: Database = request.app.state.db
    return await run_in_threadpool(db.get_skipped_companies)
