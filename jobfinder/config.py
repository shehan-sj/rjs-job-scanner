"""Load and expose config.yaml as a typed Config object."""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ResumeConfig:
    path: str = "resume.pdf"
    # The original filename the user picked at upload time (T18's
    # ``POST /api/resume``), kept only so ``GET /api/settings`` (T45) can
    # report *something* a human recognizes instead of ``path``'s
    # server-generated uuid name. This is a display string, never a
    # filesystem path: treat a hand-edited or hostile value here (one
    # containing path separators or `..`) the same way T18 treats
    # upload content — opaque and untrusted. Nothing in this codebase
    # may use it to open a file; sanitizing it for display is
    # ``jobfinder.web.resume.sanitize_resume_display_name``'s job, and
    # both the writer (resume.py) and the reader (settings.py) apply it
    # rather than trusting whatever's on disk.
    original_filename: str = ""


@dataclass
class AnthropicConfig:
    api_key: str = ""
    model: str = "claude-opus-4-6"


@dataclass
class SourceConfig:
    name: str = ""
    enabled: bool = True
    site_names: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    location: str = ""
    results_wanted: int = 20
    hours_old: int = 24
    linkedin_fetch_description: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationsConfig:
    auto_dismiss_seconds: int = 0


# The only two values ``ui.theme`` may hold, and the one a fresh install
# gets. Single-sourced here because config.example.yaml, web/settings.py's
# ``UIIn`` and the normalization in :func:`load_config` all have to agree —
# the product spec is what happens when they don't (the shipped template carried
# a colorhunt.co URL, and every real install was seeded from it).
VALID_UI_THEMES = ("dark", "light")
_DEFAULT_UI_THEME = "dark"

# T97 — the five levels the log drawer already classifies every line
# into (`LogDrawer.tsx`'s `LEVEL_CLASS`), single-sourced here so
# `web/settings.py`'s `UIIn` validator and this module's own
# `UIConfig.console_hidden_levels` default agree on the closed
# vocabulary, same reasoning as `VALID_UI_THEMES` above.
LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class UIConfig:
    show_log_console: bool = True
    theme: str = _DEFAULT_UI_THEME  # one of VALID_UI_THEMES
    # Console log line density
    console_show_timestamp: bool = True
    console_show_level: bool = True
    # T97 — levels the log drawer should hide (DEBUG/INFO/WARNING/ERROR/
    # CRITICAL). Empty by default: a fresh install must show every level,
    # so a user never misses a failure they didn't choose to hide.
    console_hidden_levels: list[str] = field(default_factory=list)


@dataclass
class LocationFilterConfig:
    """Geographic filters — deliberately empty by default.

    There is **no** baked-in country: the dataclass defaults used to say
    ``United States`` while config.example.yaml left both of these blank and
    pointed ``sources[0].location`` at ``Canada``, so an install searched one
    country while the in-memory default claimed another. Any single country
    is wrong for most users, and the product spec makes the user's résumé the actual
    source of these values (filled in by the task ledger). Empty here means "no
    geographic preference" — ``_classify_tier`` then returns ``global`` for
    everything and ``_check_geo`` raises no warning.
    """

    # Regions/cities where hybrid or on-site work is preferred (tier 1)
    preferred_regions: list[str] = field(default_factory=list)
    # Country-level filter (tier 2) — jobs in this country but outside preferred regions
    search_country: str = ""
    # If True, remote jobs from anywhere in the world are acceptable (tier 3)
    remote_ok_worldwide: bool = True


@dataclass
class TagVisibilityConfig:
    """Which employment / arrangement tags to hide from the Matches tab.

    A ``True`` value hides jobs carrying that tag — the default is to show
    everything. These toggles only affect display, not scraping or persistence.
    """

    hide_full_time: bool = False
    hide_part_time: bool = False
    hide_contract: bool = False
    hide_temporary: bool = False
    hide_internship: bool = True
    hide_remote: bool = False
    hide_hybrid: bool = False
    hide_onsite: bool = False


@dataclass
class AgenticFilterConfig:
    """Optional second-pass Claude verification of locally-matched jobs."""

    enabled: bool = False
    # Only verify jobs whose local score is at least this threshold (saves API spend)
    min_local_score: int = 60


@dataclass
class RoleFilterConfig:
    """Role-level filters — what kind of work you actually want."""

    # Multi-select. Empty list = accept all.
    job_functions: list[str] = field(default_factory=list)  # e.g. ["Engineering", "Information Technology"]
    seniority_levels: list[str] = field(default_factory=list)  # e.g. ["Mid-Senior level", "Director"]


@dataclass
class QualityFilterConfig:
    """Filters that cut noise — staffing agencies, promoted listings, etc."""

    hide_staffing_agencies: bool = True  # industry match
    hide_promoted: bool = False  # listing_type == "promoted"
    min_salary: int = 0  # annual, in the dominant currency of the scan; 0 disables
    min_company_size: int = 0  # minimum headcount; 0 disables
    max_post_age_days: int = 0  # 0 disables — otherwise filter by date_posted


@dataclass
class FillerBotConfig:
    """Settings for the FillerBot application form filler."""

    enabled: bool = True
    page_timeout_seconds: int = 30
    auto_fill_low_confidence: bool = False  # fill fields with confidence < 0.5?


# Single source of truth for the three top-level defaults.
# These match config.example.yaml, which is what real installs are seeded with,
# so a fresh install and a missing key resolve to the same values. Previously
# three sources disagreed: this dataclass said 30/70/False, main.py's
# _write_default_config said 10/50/True, and the shipped template said
# 20/65/true. A fifth was later found in web/settings.py's Pydantic model
# (30/70) and aligned in T46. Change these and config.example.yaml together,
# never one alone, and derive rather than restate anywhere else.
_DEFAULT_CHECK_INTERVAL_MINUTES = 20
_DEFAULT_MATCH_THRESHOLD = 65
_DEFAULT_FILTER_CONTRACTS = True
# Defaults to True: an unattended periodic scan is what the product *is*.
# Before this existed the scheduler booted paused every single time and the
# only way to arm the periodic cycle was to find the "Resume automatic
# scanning" button and press it again after every launch — so a configured
# `check_interval_minutes` described a schedule that was never running.
_DEFAULT_AUTO_SCAN_ENABLED = True


@dataclass
class Config:
    check_interval_minutes: int = _DEFAULT_CHECK_INTERVAL_MINUTES
    # Whether the periodic scan cycle should be armed. Written by
    # ``POST /api/scan/play`` / ``POST /api/scan/pause``
    # (``jobfinder/web/scan.py``) and read once at boot by the web app's
    # lifespan, which is what makes pausing survive a restart *and* makes
    # the default — scanning — survive one. Deliberately absent from
    # ``SettingsUpdate``/``_settings_payload``: the board header's
    # play/pause control already owns this bit, and a second writer on the
    # Settings page could silently disagree with it. Same shape of
    # internal, route-owned flag as ``onboarding_completed`` below, and
    # exempted from the settings-parity check for the same reason.
    auto_scan_enabled: bool = _DEFAULT_AUTO_SCAN_ENABLED
    match_threshold: int = _DEFAULT_MATCH_THRESHOLD
    filter_contracts: bool = _DEFAULT_FILTER_CONTRACTS
    # T61: set once by `POST /api/onboarding/complete` when the wizard's
    # single Start/Finish button is pressed, with or without an Anthropic
    # key. Before T61, `needs_onboarding` (jobfinder/web/onboarding.py) had
    # no such flag and recomputed its answer from scratch on every load —
    # "no key" and "not yet onboarded" were the same bit, so a user who
    # finished without a key was shown the wizard again forever. This flag
    # is the other half of that AND: `load_config` always defaults it to
    # `False`, so a fresh install and every pre-T61 config.yaml (which
    # never wrote this key) still fall through to the old key-or-resume
    # check unchanged — an already-working install is never newly trapped.
    onboarding_completed: bool = False
    resume: ResumeConfig = field(default_factory=ResumeConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    sources: list[SourceConfig] = field(default_factory=list)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    location_filter: LocationFilterConfig = field(default_factory=LocationFilterConfig)
    tag_visibility: TagVisibilityConfig = field(default_factory=TagVisibilityConfig)
    agentic_filter: AgenticFilterConfig = field(default_factory=AgenticFilterConfig)
    role_filter: RoleFilterConfig = field(default_factory=RoleFilterConfig)
    quality_filter: QualityFilterConfig = field(default_factory=QualityFilterConfig)
    fillerbot: FillerBotConfig = field(default_factory=FillerBotConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    @property
    def anthropic_api_key(self) -> str:
        return self.anthropic.api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    @property
    def active_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def _coerce_theme(value: Any) -> str:
    """Return a value from :data:`VALID_UI_THEMES`, loudly.

    ``load_config`` used to pass ``ui.theme`` through untouched, and nothing
    downstream complained: ``frontend/src/theme/theme.ts``'s
    ``applyServerTheme`` silently ignores anything that isn't ``dark`` or
    ``light`` and leaves the OS preference in charge. That silence is why
    the product spec's defect — the shipped template setting ``theme`` to a
    colorhunt.co URL — went unnoticed through every install that was seeded
    from it.

    So an unrecognized value is logged at WARNING, naming the file's value.
    Not an exception, deliberately — every install seeded before this fix has
    the URL in its own config.yaml, and refusing to load a config over a
    cosmetic field would turn a harmless defect into a startup failure.

    It returns ``""`` — "no override" — rather than forcing the default, and
    that distinction is the whole point. There is no ``auto`` member of
    :data:`VALID_UI_THEMES`, so "follow the OS" is representable only as the
    absence of a value, which is exactly what ``applyServerTheme`` already
    treats an unrecognized theme as. Coercing to ``dark`` instead would flip
    every pre-existing install on a light desktop from light to dark on its
    next load — and an install with an invalid value is the *only* kind that
    can be affected, because the value came from the broken template. We do
    not know what those users wanted, so we do not pretend to: they keep the
    behaviour they already had. A fresh install still gets the template's
    explicit ``dark``.
    """
    if isinstance(value, str) and value.strip().lower() in VALID_UI_THEMES:
        return value.strip().lower()
    if value in (None, ""):
        return ""
    logger.warning(
        "config.yaml: ui.theme is %r, which is not one of %s - ignoring it and "
        "following the operating system preference. Set ui.theme to one of "
        "those values to choose explicitly.",
        value,
        ", ".join(VALID_UI_THEMES),
    )
    return ""


def load_config(path: str | Path = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    resume = ResumeConfig(**raw.get("resume", {}))
    anthropic_cfg = AnthropicConfig(**raw.get("anthropic", {}))

    sources = []
    for src in raw.get("sources", []):
        known_fields = {
            "name",
            "enabled",
            "site_names",
            "search_terms",
            "location",
            "results_wanted",
            "hours_old",
            "linkedin_fetch_description",
        }
        extra = {k: v for k, v in src.items() if k not in known_fields}
        sources.append(
            SourceConfig(
                name=src.get("name", ""),
                enabled=src.get("enabled", True),
                # ``site_names:``/``search_terms:``/``location:`` with nothing
                # after the colon parse to None, not to an empty list/string
                # — and since T48 config.example.yaml ships
                # ``location:`` blank, so this is the fresh-install path.
                # ``jobspy_source._infer_country`` calls ``.lower()`` on it.
                site_names=src.get("site_names") or [],
                search_terms=src.get("search_terms") or [],
                location=src.get("location") or "",
                results_wanted=src.get("results_wanted", 20),
                hours_old=src.get("hours_old", 24),
                linkedin_fetch_description=src.get("linkedin_fetch_description", False),
                extra=extra,
            )
        )

    notif = NotificationsConfig(**raw.get("notifications", {}))

    lf_raw = raw.get("location_filter", {}) or {}
    # ``preferred_regions:``/``search_country:`` with nothing after the colon
    # parse to None, not to an empty list/string — and config.example.yaml
    # ships exactly that. Coerce here so a seeded install's loaded Config is
    # field-for-field equal to ``Config()`` instead of carrying
    # Nones that every reader has to guard with ``or []`` (web/settings.py
    # had to).
    location_filter = LocationFilterConfig(
        preferred_regions=list(lf_raw.get("preferred_regions") or []),
        search_country=lf_raw.get("search_country") or "",
        remote_ok_worldwide=lf_raw.get("remote_ok_worldwide", True),
    )

    tv_raw = raw.get("tag_visibility", {})
    tag_visibility = TagVisibilityConfig(
        hide_full_time=bool(tv_raw.get("hide_full_time", False)),
        hide_part_time=bool(tv_raw.get("hide_part_time", False)),
        hide_contract=bool(tv_raw.get("hide_contract", False)),
        hide_temporary=bool(tv_raw.get("hide_temporary", False)),
        hide_internship=bool(tv_raw.get("hide_internship", True)),
        hide_remote=bool(tv_raw.get("hide_remote", False)),
        hide_hybrid=bool(tv_raw.get("hide_hybrid", False)),
        hide_onsite=bool(tv_raw.get("hide_onsite", False)),
    )

    af_raw = raw.get("agentic_filter", {})
    agentic_filter = AgenticFilterConfig(
        enabled=bool(af_raw.get("enabled", False)),
        min_local_score=int(af_raw.get("min_local_score", 60)),
    )

    rf_raw = raw.get("role_filter", {})
    role_filter = RoleFilterConfig(
        job_functions=list(rf_raw.get("job_functions", []) or []),
        seniority_levels=list(rf_raw.get("seniority_levels", []) or []),
    )

    qf_raw = raw.get("quality_filter", {})
    quality_filter = QualityFilterConfig(
        hide_staffing_agencies=bool(qf_raw.get("hide_staffing_agencies", True)),
        hide_promoted=bool(qf_raw.get("hide_promoted", False)),
        min_salary=int(qf_raw.get("min_salary", 0) or 0),
        min_company_size=int(qf_raw.get("min_company_size", 0) or 0),
        max_post_age_days=int(qf_raw.get("max_post_age_days", 0) or 0),
    )

    fb_raw = raw.get("fillerbot", {})
    fillerbot = FillerBotConfig(
        enabled=bool(fb_raw.get("enabled", True)),
        page_timeout_seconds=int(fb_raw.get("page_timeout_seconds", 30)),
        auto_fill_low_confidence=bool(fb_raw.get("auto_fill_low_confidence", False)),
    )

    ui_raw = raw.get("ui", {})
    ui = UIConfig(
        show_log_console=ui_raw.get("show_log_console", True),
        theme=_coerce_theme(ui_raw.get("theme", _DEFAULT_UI_THEME)),
        console_show_timestamp=bool(ui_raw.get("console_show_timestamp", True)),
        console_show_level=bool(ui_raw.get("console_show_level", True)),
        console_hidden_levels=list(ui_raw.get("console_hidden_levels", []) or []),
    )

    return Config(
        check_interval_minutes=raw.get("check_interval_minutes", _DEFAULT_CHECK_INTERVAL_MINUTES),
        # A pre-existing config.yaml never wrote this key, so it resolves to
        # the default — i.e. an install that predates the flag starts
        # scanning on its configured interval rather than staying paused.
        auto_scan_enabled=bool(raw.get("auto_scan_enabled", _DEFAULT_AUTO_SCAN_ENABLED)),
        match_threshold=raw.get("match_threshold", _DEFAULT_MATCH_THRESHOLD),
        filter_contracts=bool(raw.get("filter_contracts", _DEFAULT_FILTER_CONTRACTS)),
        onboarding_completed=bool(raw.get("onboarding_completed", False)),
        resume=resume,
        anthropic=anthropic_cfg,
        sources=sources,
        notifications=notif,
        location_filter=location_filter,
        tag_visibility=tag_visibility,
        agentic_filter=agentic_filter,
        role_filter=role_filter,
        quality_filter=quality_filter,
        fillerbot=fillerbot,
        ui=ui,
    )


def _source_to_raw(src: SourceConfig) -> dict[str, Any]:
    """Convert a SourceConfig back to a raw dict, merging ``extra`` keys
    back in alongside the modeled fields so unknown per-source keys
    (e.g. a scraper-specific option) survive a save/load round-trip."""
    raw: dict[str, Any] = {
        "name": src.name,
        "enabled": src.enabled,
        "site_names": src.site_names,
        "search_terms": src.search_terms,
        "location": src.location,
        "results_wanted": src.results_wanted,
        "hours_old": src.hours_old,
        "linkedin_fetch_description": src.linkedin_fetch_description,
    }
    raw.update(src.extra)
    return raw


def save_config(config: Config, path: str | Path = "config.yaml") -> None:
    """Write ``config`` to ``path`` as YAML.

    This is the single writer for config.yaml (the product spec's
    "No ``save_config()``" rule). It must preserve two things the old Qt
    writers (``ui/settings.py``, ``ui/wizard.py`` — both deleted in T33)
    happened to get right by mutating a raw dict in place:

    1. **Unknown top-level keys.** Anything in the existing file that no
       dataclass here models (e.g. a future setting added before this
       module learns about it) is read back in and re-written untouched.
    2. **Key order.** Keys already present in the file keep their
       position (``dict.__setitem__`` on an existing key does not move
       it); only genuinely new keys are appended, at the end.

    If ``path`` does not exist yet, this writes a fresh file containing
    exactly the fields ``Config`` models, in declaration order.
    """
    path = Path(path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}
    else:
        raw = {}

    raw["check_interval_minutes"] = config.check_interval_minutes
    raw["auto_scan_enabled"] = config.auto_scan_enabled
    raw["match_threshold"] = config.match_threshold
    raw["filter_contracts"] = config.filter_contracts
    raw["onboarding_completed"] = config.onboarding_completed
    raw["resume"] = {"path": config.resume.path, "original_filename": config.resume.original_filename}
    raw["anthropic"] = {
        "api_key": config.anthropic.api_key,
        "model": config.anthropic.model,
    }
    raw["sources"] = [_source_to_raw(src) for src in config.sources]
    raw["notifications"] = {"auto_dismiss_seconds": config.notifications.auto_dismiss_seconds}
    raw["location_filter"] = {
        "preferred_regions": config.location_filter.preferred_regions,
        "search_country": config.location_filter.search_country,
        "remote_ok_worldwide": config.location_filter.remote_ok_worldwide,
    }
    raw["tag_visibility"] = {
        "hide_full_time": config.tag_visibility.hide_full_time,
        "hide_part_time": config.tag_visibility.hide_part_time,
        "hide_contract": config.tag_visibility.hide_contract,
        "hide_temporary": config.tag_visibility.hide_temporary,
        "hide_internship": config.tag_visibility.hide_internship,
        "hide_remote": config.tag_visibility.hide_remote,
        "hide_hybrid": config.tag_visibility.hide_hybrid,
        "hide_onsite": config.tag_visibility.hide_onsite,
    }
    raw["agentic_filter"] = {
        "enabled": config.agentic_filter.enabled,
        "min_local_score": config.agentic_filter.min_local_score,
    }
    raw["role_filter"] = {
        "job_functions": config.role_filter.job_functions,
        "seniority_levels": config.role_filter.seniority_levels,
    }
    raw["quality_filter"] = {
        "hide_staffing_agencies": config.quality_filter.hide_staffing_agencies,
        "hide_promoted": config.quality_filter.hide_promoted,
        "min_salary": config.quality_filter.min_salary,
        "min_company_size": config.quality_filter.min_company_size,
        "max_post_age_days": config.quality_filter.max_post_age_days,
    }
    raw["fillerbot"] = {
        "enabled": config.fillerbot.enabled,
        "page_timeout_seconds": config.fillerbot.page_timeout_seconds,
        "auto_fill_low_confidence": config.fillerbot.auto_fill_low_confidence,
    }
    raw["ui"] = {
        "show_log_console": config.ui.show_log_console,
        "theme": config.ui.theme,
        "console_show_timestamp": config.ui.console_show_timestamp,
        "console_show_level": config.ui.console_show_level,
        "console_hidden_levels": config.ui.console_hidden_levels,
    }

    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(raw, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


def sync_config(dst: Any, src: Any) -> None:
    """Copy every field from ``src`` onto ``dst`` in place, recursing into
    nested dataclasses (``resume``, ``anthropic``, ``location_filter``, ...).

    This exists so settings changes can apply live without a restart
    (T10): callers elsewhere (``Orchestrator``, ``JobScheduler``, the Qt
    widget) hold a reference to ``dst`` itself, or to one of its nested
    sub-configs (e.g. ``config.tag_visibility``). Rebinding a local
    variable to a freshly ``load_config()``-ed ``Config`` would not reach
    any of them. Mutating ``dst`` (and its nested dataclass instances) in
    place does, without re-deriving `load_config`'s own field-by-field
    parsing and defaulting a second time.
    """
    for f in dataclasses.fields(dst):
        src_value = getattr(src, f.name)
        dst_value = getattr(dst, f.name)
        if (
            dataclasses.is_dataclass(dst_value)
            and dataclasses.is_dataclass(src_value)
            and type(dst_value) is type(src_value)
        ):
            sync_config(dst_value, src_value)
        else:
            setattr(dst, f.name, src_value)
