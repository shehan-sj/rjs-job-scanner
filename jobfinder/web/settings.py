"""Settings endpoints (T18) — ``GET``/``PUT /api/settings``.

the product spec: API keys (the Anthropic key, and per-source
credentials living in ``SourceConfig.extra``) are write-only over HTTP.
``GET`` never returns a secret value, only whether one is set and (when
long enough) a masked last-4. ``PUT`` treats an empty string on a
credential field as "leave it unchanged", never "clear it" — the client
cannot tell the server to blank a key it can't read back to confirm.

``resume.path`` is deliberately **not settable** anywhere in this module.
It used to be an ordinary setting; as of T18 the resume arrives as an
upload (``POST /api/resume``, ``jobfinder/web/resume.py``) and the server
owns the stored path entirely — a client that
could set it over this endpoint could point ``load_resume`` at an
arbitrary file (see ``resume.py``'s module docstring for the exploit).
``SettingsUpdate.resume`` (``ResumeIn`` below) only declares ``uploaded``
and ``filename`` — the same two read-only fields ``GET`` reports — and is
itself configured ``extra="forbid"``, so a ``PUT`` body containing
``{"resume": {"path": "..."}}`` is rejected with 422 by Pydantic before
any handler code runs, while a body that just echoes ``GET``'s own
``resume`` section back (the common "fetch, tweak one field, PUT the
whole thing back" client pattern) is accepted and silently ignored:
``_apply_update`` below always carries ``existing.resume`` forward
untouched, regardless of what ``update.resume`` contains.

**T45**: ``GET`` *does* now include a ``resume`` section —
``{"uploaded": bool, "filename": str | None}`` — so the Settings screen
and onboarding wizard can show the real uploaded filename instead of
caching it in ``localStorage`` (which showed "No resume on file yet" on
every other browser/profile even though the server still had the file).
``filename`` is ``config.resume.original_filename`` — the display name
``resume.py`` sanitised and stored at upload time — re-sanitised here
defensively (:func:`jobfinder.web.resume.sanitize_resume_display_name`)
in case ``config.yaml`` was hand-edited with something path-shaped.
Nothing derived from ``config.resume.path`` (the real, server-generated
storage path) is ever included in this payload — see ``resume.py``'s
module docstring for why that specifically must never happen.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import paths
from ..config import (
    Config,
    LocationFilterConfig,
    LOG_LEVEL_NAMES,
    SourceConfig,
    UIConfig,
    VALID_UI_THEMES,
    _DEFAULT_CHECK_INTERVAL_MINUTES,
    _DEFAULT_FILTER_CONTRACTS,
    _DEFAULT_MATCH_THRESHOLD,
    save_config,
    sync_config,
)
from .redact import is_sensitive_key, mask_last4, redact_extra
from .resume import sanitize_resume_display_name

logger = logging.getLogger("jobfinder.web.settings")

router = APIRouter(prefix="/api")


# ── GET representation ──────────────────────────────────────────────────


def _redact_source(src: SourceConfig) -> dict[str, Any]:
    return {
        "name": src.name,
        "enabled": src.enabled,
        # ``load_config`` (jobfinder/config.py) reads these with
        # ``raw.get(key, default)``, which only applies when the key is
        # *absent* — config.example.yaml (and so a freshly seeded
        # config.yaml) writes several of them present but with a blank
        # YAML scalar, which PyYAML loads as ``None``. Normalize at the
        # boundary so GET never emits an inconsistent ``null`` a client
        # would have to special-case, and so a GET→PUT→GET round trip
        # (SourceIn's matching before-validators coerce the same way on
        # the way in) is actually byte-identical rather than drifting
        # from null to [] / "" the first time it's saved back.
        "site_names": src.site_names or [],
        "search_terms": src.search_terms or [],
        "location": src.location or "",
        "results_wanted": src.results_wanted,
        "hours_old": src.hours_old,
        "linkedin_fetch_description": src.linkedin_fetch_description,
        "extra": redact_extra(src.extra),
    }


def _settings_payload(config: Config) -> dict[str, Any]:
    """The full settings payload, every secret redacted.

    ``anthropic_api_key`` (``config.py``'s ``Config.anthropic_api_key``
    property, not a field) is used for ``api_key_set``/``api_key_last4``
    rather than ``config.anthropic.api_key`` directly, so a key supplied
    only via the ``ANTHROPIC_API_KEY`` environment variable correctly
    reports as set even though nothing is in ``config.yaml`` — this is
    the same key resolution every other caller (``Orchestrator``,
    ``resume/profiler.py``) uses.
    """
    effective_key = config.anthropic_api_key
    # T45: display-only, re-sanitised defensively — see module docstring.
    # ``uploaded`` tracks whether ``POST /api/resume`` has ever actually
    # run (``original_filename`` is only ever set there), not whether
    # ``config.resume.path`` merely has *some* value — a fresh config
    # ships a non-upload placeholder path (``resume.pdf``) that was never
    # a real upload and has no filename to show.
    resume_filename = sanitize_resume_display_name(config.resume.original_filename)
    return {
        "resume": {
            "uploaded": bool(resume_filename),
            "filename": resume_filename or None,
        },
        "check_interval_minutes": config.check_interval_minutes,
        "match_threshold": config.match_threshold,
        "filter_contracts": config.filter_contracts,
        "anthropic": {
            "api_key": "",  # write-only — never the value, see module docstring
            "api_key_set": bool(effective_key),
            "api_key_last4": mask_last4(effective_key),
            "model": config.anthropic.model,
        },
        "sources": [_redact_source(src) for src in config.sources],
        "notifications": {"auto_dismiss_seconds": config.notifications.auto_dismiss_seconds},
        "location_filter": {
            # Same None-from-blank-YAML-scalar normalization as
            # ``_redact_source`` above — config.example.yaml ships
            # ``preferred_regions:``/``search_country:`` blank.
            "preferred_regions": config.location_filter.preferred_regions or [],
            "search_country": config.location_filter.search_country or "",
            "remote_ok_worldwide": config.location_filter.remote_ok_worldwide,
        },
        "tag_visibility": {
            "hide_full_time": config.tag_visibility.hide_full_time,
            "hide_part_time": config.tag_visibility.hide_part_time,
            "hide_contract": config.tag_visibility.hide_contract,
            "hide_temporary": config.tag_visibility.hide_temporary,
            "hide_internship": config.tag_visibility.hide_internship,
            "hide_remote": config.tag_visibility.hide_remote,
            "hide_hybrid": config.tag_visibility.hide_hybrid,
            "hide_onsite": config.tag_visibility.hide_onsite,
        },
        "agentic_filter": {
            "enabled": config.agentic_filter.enabled,
            "min_local_score": config.agentic_filter.min_local_score,
        },
        "role_filter": {
            "job_functions": config.role_filter.job_functions,
            "seniority_levels": config.role_filter.seniority_levels,
        },
        "quality_filter": {
            "hide_staffing_agencies": config.quality_filter.hide_staffing_agencies,
            "hide_promoted": config.quality_filter.hide_promoted,
            "min_salary": config.quality_filter.min_salary,
            "min_company_size": config.quality_filter.min_company_size,
            "max_post_age_days": config.quality_filter.max_post_age_days,
        },
        "fillerbot": {
            "enabled": config.fillerbot.enabled,
            "page_timeout_seconds": config.fillerbot.page_timeout_seconds,
            "auto_fill_low_confidence": config.fillerbot.auto_fill_low_confidence,
        },
        "ui": {
            "show_log_console": config.ui.show_log_console,
            "theme": config.ui.theme,
            "console_show_timestamp": config.ui.console_show_timestamp,
            "console_show_level": config.ui.console_show_level,
            "console_hidden_levels": config.ui.console_hidden_levels,
        },
    }


# ── PUT request model ───────────────────────────────────────────────────


class ResumeIn(BaseModel):
    """Mirrors ``GET``'s read-only ``resume`` section so a client can
    echo the whole settings payload back through ``PUT`` unmodified
    (the round-trip pattern several callers use). Declares no ``path``
    field and is ``extra="forbid"``, so ``{"resume": {"path": "..."}}``
    is rejected with 422 — see module docstring. Whatever is sent here
    is ignored by ``_apply_update``; there is no way to change
    ``config.resume`` through this endpoint at all.
    """

    model_config = ConfigDict(extra="forbid")

    uploaded: bool = False
    filename: str | None = None


class AnthropicIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tolerates GET's api_key_set/api_key_last4 echoed back

    api_key: str = ""  # write-only: "" means "leave the stored key unchanged"
    model: str = "claude-opus-4-6"


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    enabled: bool = True
    site_names: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    location: str = ""
    results_wanted: int = Field(20, ge=1, le=500)
    hours_old: int = Field(24, ge=1)
    linkedin_fetch_description: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    # ``load_config`` (jobfinder/config.py) does ``src.get("search_terms",
    # [])`` etc. — a dict ``.get`` with a default only applies when the
    # key is *absent*. config.example.yaml (and therefore a freshly
    # seeded config.yaml) writes several of these keys present but with
    # a blank YAML scalar, which PyYAML loads as ``None``, not the
    # dataclass field's default — so a round-tripped GET→PUT body can
    # legitimately contain these as ``null``. Coerce rather than reject.
    @field_validator("site_names", "search_terms", mode="before")
    @classmethod
    def _none_list_to_empty(cls, v: Any) -> Any:
        return [] if v is None else v

    @field_validator("location", mode="before")
    @classmethod
    def _none_str_to_empty(cls, v: Any) -> Any:
        return "" if v is None else v


class NotificationsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auto_dismiss_seconds: int = Field(0, ge=0)


class LocationFilterIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Derived from the dataclass rather than restated: these defaults are
    # what a PUT that omits the section resolves to, and the product spec's
    # whole point is that a second literal copy of a default drifts.
    preferred_regions: list[str] = Field(default_factory=lambda: list(LocationFilterConfig().preferred_regions))
    search_country: str = LocationFilterConfig().search_country
    remote_ok_worldwide: bool = LocationFilterConfig().remote_ok_worldwide

    # See SourceIn's equivalent validators — same blank-YAML-scalar-is-
    # None quirk, this time from config.example.yaml's own
    # ``location_filter: preferred_regions:`` / ``search_country:``.
    @field_validator("preferred_regions", mode="before")
    @classmethod
    def _none_list_to_empty(cls, v: Any) -> Any:
        return [] if v is None else v

    @field_validator("search_country", mode="before")
    @classmethod
    def _none_str_to_empty(cls, v: Any) -> Any:
        return "" if v is None else v


class TagVisibilityIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hide_full_time: bool = False
    hide_part_time: bool = False
    hide_contract: bool = False
    hide_temporary: bool = False
    hide_internship: bool = True
    hide_remote: bool = False
    hide_hybrid: bool = False
    hide_onsite: bool = False


class AgenticFilterIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    min_local_score: int = Field(60, ge=0, le=100)


class RoleFilterIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_functions: list[str] = Field(default_factory=list)
    seniority_levels: list[str] = Field(default_factory=list)


class QualityFilterIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hide_staffing_agencies: bool = True
    hide_promoted: bool = False
    min_salary: int = Field(0, ge=0)
    min_company_size: int = Field(0, ge=0)
    max_post_age_days: int = Field(0, ge=0)


class FillerBotIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    page_timeout_seconds: int = Field(30, ge=1)
    auto_fill_low_confidence: bool = False


class UIIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    show_log_console: bool = True
    theme: str = UIConfig().theme
    console_show_timestamp: bool = True
    console_show_level: bool = True
    # T97 — levels the log drawer hides. Empty by default: see
    # UIConfig.console_hidden_levels' docstring for why that default is
    # load-bearing, not arbitrary.
    console_hidden_levels: list[str] = Field(default_factory=list)

    @field_validator("console_hidden_levels")
    @classmethod
    def _hidden_levels_must_be_known(cls, v: list[str]) -> list[str]:
        """Reject an unrecognised level name at the API boundary (422) —
        same reasoning as `_theme_must_be_known` below: this is new
        input from a client, not a config.yaml that predates a value
        change and must still load, so there's no backward-compat
        reason to coerce instead of rejecting."""
        unknown = [level for level in v if level not in LOG_LEVEL_NAMES]
        if unknown:
            raise ValueError(f"unknown log level(s): {', '.join(unknown)}; must be one of {', '.join(LOG_LEVEL_NAMES)}")
        return v

    @field_validator("theme")
    @classmethod
    def _theme_must_be_known(cls, v: str) -> str:
        """Reject an unknown theme at the API boundary (422).

        ``load_config`` deliberately *coerces* instead of raising, because a
        config.yaml seeded before the product spec was fixed carries a
        colorhunt.co URL and must still load. A PUT body is different: it is
        new input from a client, nothing has to stay compatible with it, and
        accepting a value the UI cannot render is how that defect stayed invisible.
        """
        if v not in VALID_UI_THEMES:
            raise ValueError(f"theme must be one of {', '.join(VALID_UI_THEMES)}")
        return v


class SettingsUpdate(BaseModel):
    """Note: ``resume`` is read-only (``ResumeIn``) — nothing it carries is
    ever applied. See module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    check_interval_minutes: int = Field(_DEFAULT_CHECK_INTERVAL_MINUTES, ge=1)
    match_threshold: int = Field(_DEFAULT_MATCH_THRESHOLD, ge=0, le=100)
    filter_contracts: bool = _DEFAULT_FILTER_CONTRACTS
    resume: ResumeIn = Field(default_factory=ResumeIn)
    anthropic: AnthropicIn = Field(default_factory=AnthropicIn)
    sources: list[SourceIn] = Field(default_factory=list)
    notifications: NotificationsIn = Field(default_factory=NotificationsIn)
    location_filter: LocationFilterIn = Field(default_factory=LocationFilterIn)
    tag_visibility: TagVisibilityIn = Field(default_factory=TagVisibilityIn)
    agentic_filter: AgenticFilterIn = Field(default_factory=AgenticFilterIn)
    role_filter: RoleFilterIn = Field(default_factory=RoleFilterIn)
    quality_filter: QualityFilterIn = Field(default_factory=QualityFilterIn)
    fillerbot: FillerBotIn = Field(default_factory=FillerBotIn)
    ui: UIIn = Field(default_factory=UIIn)


def _merge_extra(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge an incoming ``extra`` dict onto the existing one.

    - Shadow keys GET adds for redaction (``"<key>_set"``, ``"<key>_last4"``)
      are never persisted — they're read-only metadata, not real settings,
      and a client that echoes a GET response straight back into a PUT (the
      round-trip case) will include them.
    - A sensitive key (same key-name heuristic as ``redact_extra``) with an
      empty-string value means "unchanged" — the write-only contract.
    - Anything else overwrites/adds the key. Keys present in ``existing``
      but absent from ``incoming`` are preserved rather than dropped, so a
      client that only means to change one source field doesn't have to
      round-trip every credential it can't read back to resend verbatim.
    """
    merged = dict(existing)
    for key, value in incoming.items():
        if key.endswith("_set") or key.endswith("_last4"):
            continue
        if is_sensitive_key(key) and value == "":
            continue
        merged[key] = value
    return merged


def _apply_update(existing: Config, update: SettingsUpdate) -> Config:
    """Build a new ``Config`` reflecting ``update`` layered onto
    ``existing`` — preserving write-only credentials ``update`` left
    blank/unmentioned. ``existing.resume`` is carried over untouched
    (``SettingsUpdate`` has no way to express a change to it at all).

    ``update.model_dump(exclude_unset=True)`` is the key to not destroying
    data on a partial ``PUT``: Pydantic v2 recurses ``exclude_unset`` into
    nested ``BaseModel`` fields, tracking each sub-model's own
    ``__fields_set__`` independently of its parent. So a body of
    ``{"quality_filter": {"min_salary": 500}}`` dumps as
    ``{"quality_filter": {"min_salary": 500}}`` — *not* every field
    ``QualityFilterIn`` declares a default for — and a body that omits
    ``quality_filter`` entirely has no ``"quality_filter"`` key at all.
    (Verified interactively: a provided-but-empty nested dict, e.g.
    ``{"quality_filter": {}}``, dumps as ``{"quality_filter": {}}`` too — a
    section mentioned with no sub-fields behaves the same as a section left
    out, which is the merge semantics we want either way.)

    Each present section's dict is merged onto the *existing* dataclass with
    ``dataclasses.replace`` rather than rebuilt from the Pydantic model's own
    defaults — only ``replace``'s explicit keyword overrides change, every
    other field of the existing section is copied forward untouched. A
    section key absent from ``provided`` skips ``replace`` entirely and
    reuses the existing section instance as-is.

    ``sync_config`` (``config.py``, from T10) does something adjacent — it
    copies every field of one ``Config`` onto another in place, recursing
    into nested dataclasses — but it always copies everything, which is
    exactly the "rebuild from scratch" behaviour that causes this defect.
    It isn't a fit for a *partial* merge, so this function does the merging
    itself and leaves ``sync_config``'s in-place copy (used by the caller,
    ``_put_settings_sync``) to propagate the already-fully-merged result
    onto the live ``Config`` instance other callers hold a reference to.
    """
    provided = update.model_dump(exclude_unset=True)

    def merge_section(name: str, current: Any) -> Any:
        if name in provided:
            return replace(current, **provided[name])
        return current

    if "anthropic" in provided:
        anthropic_fields = provided["anthropic"]
        new_api_key = anthropic_fields.get("api_key", "") or existing.anthropic.api_key
        new_model = anthropic_fields.get("model", existing.anthropic.model)
        anthropic = type(existing.anthropic)(api_key=new_api_key, model=new_model)
    else:
        anthropic = existing.anthropic

    if "sources" in provided:
        existing_sources_by_name = {src.name: src for src in existing.sources}
        new_sources: list[SourceConfig] = []
        for src_in in update.sources:
            existing_src = existing_sources_by_name.get(src_in.name)
            existing_extra = existing_src.extra if existing_src is not None else {}
            new_sources.append(
                SourceConfig(
                    name=src_in.name,
                    enabled=src_in.enabled,
                    site_names=src_in.site_names,
                    search_terms=src_in.search_terms,
                    location=src_in.location,
                    results_wanted=src_in.results_wanted,
                    hours_old=src_in.hours_old,
                    linkedin_fetch_description=src_in.linkedin_fetch_description,
                    extra=_merge_extra(existing_extra, src_in.extra),
                )
            )
    else:
        new_sources = existing.sources

    new_config = Config(
        check_interval_minutes=provided.get("check_interval_minutes", existing.check_interval_minutes),
        match_threshold=provided.get("match_threshold", existing.match_threshold),
        filter_contracts=provided.get("filter_contracts", existing.filter_contracts),
        resume=existing.resume,  # not settable here — see module docstring
        anthropic=anthropic,
        sources=new_sources,
        notifications=merge_section("notifications", existing.notifications),
        location_filter=merge_section("location_filter", existing.location_filter),
        tag_visibility=merge_section("tag_visibility", existing.tag_visibility),
        agentic_filter=merge_section("agentic_filter", existing.agentic_filter),
        role_filter=merge_section("role_filter", existing.role_filter),
        quality_filter=merge_section("quality_filter", existing.quality_filter),
        fillerbot=merge_section("fillerbot", existing.fillerbot),
        ui=merge_section("ui", existing.ui),
    )
    return new_config


def _put_settings_sync(request_app_state: Any, update: SettingsUpdate) -> dict[str, Any]:
    """Runs off the event loop via ``run_in_threadpool`` — it hits disk
    (``save_config``) and, when ``check_interval_minutes`` changed, pokes
    the APScheduler engine (not a ``Database`` call, so the T16 AST check
    doesn't apply, but it's still not something to do inline in an
    ``async def``).
    """
    config: Config = request_app_state.config
    old_interval = config.check_interval_minutes

    new_config = _apply_update(config, update)
    sync_config(config, new_config)
    save_config(config, paths.config_path())

    if config.check_interval_minutes != old_interval:
        scheduler = getattr(request_app_state, "scheduler", None)
        if scheduler is not None:
            scheduler.reschedule(config.check_interval_minutes)

    return _settings_payload(config)


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    config: Config = request.app.state.config
    return _settings_payload(config)


@router.put("/settings")
async def put_settings(update: SettingsUpdate, request: Request) -> dict:
    return await run_in_threadpool(_put_settings_sync, request.app.state, update)
