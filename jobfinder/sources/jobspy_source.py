"""Job source backed by python-jobspy (supports LinkedIn, Indeed, Glassdoor, etc.)."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import replace
from datetime import datetime

from .base import JobSource
from .registry import register
from ..config import SourceConfig
from ..matcher.location_parser import (
    parse_employment_type,
    parse_location,
    parse_work_arrangement,
)
from ..matcher.models import JobPosting

logger = logging.getLogger(__name__)

_MAX_DESCRIPTION_CHARS = 8000

# Boards that take jobspy's ``country_indeed`` and default it to the United
# States when it is not given. The product spec names Indeed and Glassdoor;
# ``zip_recruiter`` is included because the call site below has always set the
# parameter for it too, and if it does read the value then leaving it unset
# reinstates exactly the silent US default this removes. Skipping a source
# that did not need the country costs results; searching the wrong country
# costs the user their trust in every result.
_COUNTRY_REQUIRING_SITES = frozenset({"indeed", "glassdoor", "zip_recruiter"})


def _parse_dt(value) -> datetime:
    try:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))
    except Exception:
        return datetime.utcnow()


def _is_contract_employment(employment_type: str) -> bool:
    """Contract-flavoured employment types that should respect the filter_contracts toggle."""
    return employment_type in ("Contract", "Temporary", "Part-time")


# ── Native-field decoders ─────────────────────────────────────────────────────

# LinkedIn exposes `work_from_home_type` as a clean string. Map its variants to
# our canonical labels.
_WFH_TYPE_MAP = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "on-site": "On-site",
    "on site": "On-site",
    "onsite": "On-site",
}


def _arrangement_from_wfh_type(value) -> str | None:
    """Map jobspy's ``work_from_home_type`` field to Remote / Hybrid / On-site.

    Returns ``None`` when the field is empty or unrecognised so the caller
    can fall back to text heuristics.
    """
    if not value:
        return None
    key = str(value).strip().lower()
    return _WFH_TYPE_MAP.get(key)


# LinkedIn's ``job_type`` is a list of JobType enums (or their string values
# when serialised). Map any element we recognise to our canonical label, in
# priority order so that a role listed as ['fulltime', 'contract'] is
# classified the more specific way.
_JOBTYPE_PRIORITY = (
    ("internship", "Internship"),
    ("intern", "Internship"),
    ("contract", "Contract"),
    ("contractor", "Contract"),
    ("temporary", "Temporary"),
    ("perdiem", "Temporary"),
    ("parttime", "Part-time"),
    ("part_time", "Part-time"),
    ("part-time", "Part-time"),
    ("fulltime", "Full-time"),
    ("full_time", "Full-time"),
    ("full-time", "Full-time"),
)


def _employment_from_jobtype(value) -> str | None:
    """Map the raw ``job_type`` cell into our canonical employment label."""
    if value is None:
        return None
    # jobspy hands us either a list of JobType enums or a stringified list.
    if isinstance(value, (list, tuple, set)):
        tokens = {str(getattr(v, "value", v)).strip().lower() for v in value}
        # JobType.value is actually a tuple of localised strings; take the first
        flat: set[str] = set()
        for t in tokens:
            flat.update(t.replace("(", "").replace(")", "").replace(",", " ").split())
        tokens = {t.strip("' \"") for t in flat if t.strip("' \"")}
    else:
        raw = str(value).strip().lower()
        if not raw or raw in ("none", "nan"):
            return None
        tokens = set(raw.replace(",", " ").replace("/", " ").replace("-", " ").split())

    for needle, label in _JOBTYPE_PRIORITY:
        if needle in tokens:
            return label
        # Substring check for enum-repr cases like "jobtype.full_time"
        if any(needle in t for t in tokens):
            return label
    return None


# ── Rich field extractors ─────────────────────────────────────────────────────


def _str(value, default: str = "") -> str:
    """Safely coerce a jobspy cell (often pandas NaN / None / enum) into a string."""
    if value is None:
        return default
    try:
        # pandas NaN check without importing pandas
        if value != value:
            return default
    except Exception:
        pass
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return default
    return s


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except Exception:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_skills(row) -> list[str]:
    """Return LinkedIn's structured skills list if present, else []."""
    raw = row.get("skills")
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(s).strip() for s in raw if str(s).strip()]
    s = _str(raw)
    if not s:
        return []
    # jobspy occasionally stringifies the list with commas
    parts = [p.strip(" '\"") for p in s.strip("[]").split(",")]
    return [p for p in parts if p]


def _extract_compensation(row) -> tuple[float | None, float | None, str, str]:
    """Return (min, max, currency, interval). Reads from flattened columns if the
    DataFrame was flattened, else from the Compensation object."""
    # jobspy usually flattens these to top-level columns on the DataFrame
    mn = _float_or_none(row.get("min_amount"))
    mx = _float_or_none(row.get("max_amount"))
    currency = _str(row.get("currency"))
    interval = _str(row.get("interval"))

    if mn is None and mx is None:
        comp = row.get("compensation")
        if comp is not None and hasattr(comp, "min_amount"):
            mn = _float_or_none(getattr(comp, "min_amount", None))
            mx = _float_or_none(getattr(comp, "max_amount", None))
            currency = currency or _str(getattr(comp, "currency", ""))
            interval = interval or _str(getattr(comp, "interval", ""))

    return mn, mx, currency.upper() if currency else "", interval.lower() if interval else ""


@register
class JobSpySource(JobSource):
    name = "linkedin"

    def fetch(self, source_config: SourceConfig) -> list[JobPosting]:
        try:
            from jobspy import scrape_jobs  # type: ignore  # noqa: F401 (fail-fast availability check)
        except ImportError:
            raise ImportError("python-jobspy is required: pip install python-jobspy")

        # the product spec: decided once per source, before any search is issued,
        # so three search terms do not produce three identical warnings.
        runnable = self._without_country_blind_sites(source_config)
        if runnable is None:
            return []
        source_config = runnable

        all_jobs: list[JobPosting] = []

        for term in source_config.search_terms:
            try:
                jobs = self._fetch_term(term, source_config)
                all_jobs.extend(jobs)
                logger.info("[%s] '%s' → %d jobs", source_config.name, term, len(jobs))
            except Exception as exc:
                logger.error("[%s] Failed to fetch '%s': %s", source_config.name, term, exc)

        # Deduplicate within this source by job_id
        seen: set[str] = set()
        deduped: list[JobPosting] = []
        for job in all_jobs:
            if job.job_id not in seen:
                seen.add(job.job_id)
                deduped.append(job)

        return deduped

    # Maps location text to jobspy's country_indeed values
    _COUNTRY_MAP = {
        "canada": "canada",
        "ca": "canada",
        "united states": "usa",
        "usa": "usa",
        "us": "usa",
        "united kingdom": "uk",
        "uk": "uk",
        "australia": "australia",
        "au": "australia",
        "germany": "germany",
        "de": "germany",
        "india": "india",
        "in": "india",
        "france": "france",
        "fr": "france",
    }

    def _infer_country(self, location: str) -> str | None:
        """The ``country_indeed`` value this location text states, or ``None``.

        It used to end ``return "usa"``. The product spec recorded that fallback as
        deliberate only until T52 derived a real country from the resume,
        after which it "should become an error rather than a guess": with no
        location configured, every Indeed and Glassdoor search was quietly
        pointed at the United States, which is the same class of silent
        assumption the product spec exists to undo.

        Returning ``None`` is the honest answer. What the caller does with it
        is :meth:`fetch`'s decision, not this method's.

        The two-letter codes are matched as whole words, the full names as
        substrings. Matching both by substring — which is what this did — read
        "Dublin, Ireland" as **India** (``"in"``) and "Vienna, Austria" as the
        **United States** (``"us"``), and the dict's insertion order was the
        only thing keeping "Austin, United States" out of India. A wrong
        country decided silently is worse than the US fallback the product spec
        removes, not better: it is confident. Full names are tried first and
        longest-first, so no key's ordering decides the answer.
        """
        loc = location.lower().strip()
        if not loc:
            return None
        tokens = {t for t in re.split(r"[^a-z]+", loc) if t}

        names = sorted((k for k in self._COUNTRY_MAP if len(k) > 2), key=len, reverse=True)
        for key in names:
            if key in loc:
                return self._COUNTRY_MAP[key]
        for key, country in self._COUNTRY_MAP.items():
            if len(key) <= 2 and key in tokens:
                return country
        return None

    def _country_for(self, cfg: SourceConfig) -> str | None:
        """``cfg``'s country: stated outright in ``extra``, or read from its
        location text. ``extra["country_indeed"]`` is a user naming the
        country explicitly, so it is not a guess and takes precedence."""
        stated = str(cfg.extra.get("country_indeed") or "").strip()
        return stated or self._infer_country(cfg.location)

    def _without_country_blind_sites(self, cfg: SourceConfig) -> SourceConfig | None:
        """``cfg`` minus the sites that cannot be searched without a country.

        Returns ``None`` when nothing searchable is left, so the caller skips
        the source outright.

        the product spec's constraint is absolute: a scan with no determinable
        country must not silently search the United States. Of the two ways to
        honour it, this is the gentler one — the affected sites are skipped
        with a warning the user can see, and the rest of the source still
        runs. The alternatives were worse trades: a mid-scan exception kills a
        scan the user is waiting on, and refusing the configuration up front
        would block the shipped template's LinkedIn-only install over a
        parameter LinkedIn never reads.
        """
        if self._country_for(cfg):
            return cfg

        sites = list(cfg.site_names or [cfg.name])
        blind = [s for s in sites if s.lower().strip() in _COUNTRY_REQUIRING_SITES]
        if not blind:
            return cfg

        logger.warning(
            "[%s] Skipping %s this scan: no country could be determined from the "
            "location %r, and these boards search the United States by default "
            "unless told otherwise. Set a country or city on this source (your "
            "resume fills it in during onboarding) and they will run again.",
            cfg.name,
            ", ".join(blind),
            cfg.location,
        )
        remaining = [s for s in sites if s not in blind]
        if not remaining:
            return None
        return replace(cfg, site_names=remaining)

    def _fetch_term(self, term: str, cfg: SourceConfig) -> list[JobPosting]:
        import inspect
        from jobspy import scrape_jobs  # type: ignore

        _accepted = inspect.signature(scrape_jobs).parameters

        kwargs: dict = dict(
            site_name=cfg.site_names or [cfg.name],
            search_term=term,
            location=cfg.location,
            results_wanted=cfg.results_wanted,
        )

        # Set country for Indeed/Glassdoor/ZipRecruiter — read from the
        # location text, or stated outright in extra. Omitted entirely when
        # neither says: fetch() has already dropped the sites that would
        # otherwise fall back to jobspy's own "usa" default.
        country = self._country_for(cfg)
        if "country_indeed" in _accepted and country:
            kwargs["country_indeed"] = country

        # Parameters added in newer jobspy releases — skip gracefully if absent.
        # We apply our own recency and description filters below regardless.
        if "hours_old" in _accepted:
            kwargs["hours_old"] = cfg.hours_old
        if "description_format" in _accepted:
            kwargs["description_format"] = "markdown"
        if cfg.linkedin_fetch_description and "linkedin_fetch_description" in _accepted:
            kwargs["linkedin_fetch_description"] = True

        # Pass any extra/unknown keys only if they are accepted
        for k, v in cfg.extra.items():
            if k in _accepted:
                kwargs[k] = v

        df = scrape_jobs(**kwargs)

        if df is None or df.empty:
            return []

        if "date_posted" in df.columns:
            from datetime import timedelta

            cutoff = datetime.utcnow() - timedelta(hours=24)
            before = len(df)
            df = df[
                df["date_posted"].isna()
                | df["date_posted"].apply(
                    lambda d: (d if isinstance(d, datetime) else _parse_dt(d)) >= cutoff if d is not None else True
                )
            ]
            dropped = before - len(df)
            if dropped:
                logger.info("[%s] Filtered %d job(s) older than 24 hours", cfg.name, dropped)

        jobs: list[JobPosting] = []
        for _, row in df.iterrows():
            job = self._normalize(row, cfg.name)
            if job:
                jobs.append(job)
        return jobs

    def _normalize(self, row, source_name: str) -> JobPosting | None:
        url = str(row.get("job_url") or "")
        if not url:
            return None

        job_id = hashlib.md5(url.encode()).hexdigest()

        description = str(row.get("description") or "")
        if len(description) > _MAX_DESCRIPTION_CHARS:
            description = description[:_MAX_DESCRIPTION_CHARS] + "\n\n[Description truncated]"

        date_posted: datetime | None = None
        raw_date = row.get("date_posted")
        if raw_date is not None:
            try:
                date_posted = raw_date if isinstance(raw_date, datetime) else datetime.fromisoformat(str(raw_date))
            except Exception:
                pass

        title_str = str(row.get("title") or "")
        location_str = str(row.get("location") or "")

        # ── Work arrangement: trust LinkedIn's native fields first ────────
        # ``work_from_home_type`` is the string LinkedIn actually shows under
        # "Workplace type" (Remote / Hybrid / On-site). When it's present we
        # use it verbatim. Otherwise fall back to the ``is_remote`` boolean
        # and only then to text heuristics.
        work_arrangement = _arrangement_from_wfh_type(row.get("work_from_home_type"))
        if work_arrangement is None:
            is_remote_flag = row.get("is_remote")
            if is_remote_flag not in (True, False):
                is_remote_flag = None
            work_arrangement = parse_work_arrangement(
                is_remote_flag,
                location_str,
                title_str,
                description,
            )

        # ── Employment type ──────────────────────────────────────────────
        # Run the title-first parser BEFORE the structured job_type mapper —
        # LinkedIn sometimes tags internships / contracts as "Full-time"
        # structurally, but the title ("… Intern", "… Contractor") is the
        # authoritative signal. Only fall back to the structured list when
        # the title + description scan returns Unknown.
        employment_type = parse_employment_type(
            str(row.get("job_type") or ""),
            title_str,
            description,
        )
        if employment_type == "Unknown":
            structured = _employment_from_jobtype(row.get("job_type"))
            if structured is not None:
                employment_type = structured

        country, region, city = parse_location(location_str)

        # Rich structured fields from LinkedIn
        comp_min, comp_max, comp_currency, comp_interval = _extract_compensation(row)

        return JobPosting(
            job_id=job_id,
            title=title_str,
            company=_str(row.get("company")),
            location=location_str,
            description=description,
            url=url,
            source=source_name,
            date_posted=date_posted,
            work_arrangement=work_arrangement,
            employment_type=employment_type,
            country=country,
            region=region,
            city=city,
            is_contract=_is_contract_employment(employment_type),
            # ── Rich fields ───────────────────────────────────────────────
            job_level=_str(row.get("job_level")),
            job_function=_str(row.get("job_function")),
            experience_range=_str(row.get("experience_range")),
            listing_type=_str(row.get("listing_type")),
            job_url_direct=_str(row.get("job_url_direct")),
            linkedin_skills=_extract_skills(row),
            company_url=_str(row.get("company_url")) or _str(row.get("company_url_direct")),
            company_logo=_str(row.get("company_logo")),
            company_industry=_str(row.get("company_industry")),
            company_num_employees=_str(row.get("company_num_employees")),
            compensation_min=comp_min,
            compensation_max=comp_max,
            compensation_currency=comp_currency,
            compensation_interval=comp_interval,
        )


class _IndeedSource(JobSpySource):
    name = "indeed"


class _GlassdoorSource(JobSpySource):
    name = "glassdoor"


class _ZipRecruiterSource(JobSpySource):
    name = "zip_recruiter"


class _GoogleSource(JobSpySource):
    name = "google"


register(_IndeedSource)
register(_GlassdoorSource)
register(_ZipRecruiterSource)
register(_GoogleSource)
