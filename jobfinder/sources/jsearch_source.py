"""JSearch — official RapidAPI aggregator. Free tier: 500 req/month.
Aggregates LinkedIn, Indeed, Glassdoor, ZipRecruiter, and more via official channels.
Sign up at: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime

import requests

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

_BASE_URL = "https://jsearch.p.rapidapi.com/search"
_MAX_DESCRIPTION_CHARS = 8000


# Maps hours_old ranges to JSearch's date_posted filter values
def _date_posted_filter(hours_old: int) -> str:
    if hours_old <= 24:
        return "today"
    if hours_old <= 72:
        return "3days"
    if hours_old <= 168:
        return "week"
    return "month"


@register
class JSearchSource(JobSource):
    """Official RapidAPI JSearch — aggregates LinkedIn, Indeed, Glassdoor, and more."""

    name = "jsearch"

    def fetch(self, source_config: SourceConfig) -> list[JobPosting]:
        api_key = source_config.extra.get("api_key", "")
        if not api_key:
            logger.error(
                "[jsearch] Missing api_key. "
                "Sign up at https://rapidapi.com and subscribe to JSearch (free tier). "
                "Add 'api_key' under the jsearch source in config.yaml."
            )
            return []

        all_jobs: list[JobPosting] = []

        for term in source_config.search_terms:
            try:
                query = f"{term} {source_config.location}".strip()
                jobs = self._fetch_query(query, source_config, api_key)
                all_jobs.extend(jobs)
                logger.info("[jsearch] '%s' → %d jobs", term, len(jobs))
            except Exception as exc:
                logger.error("[jsearch] Failed for '%s': %s", term, exc)

        seen: set[str] = set()
        return [j for j in all_jobs if not (j.job_id in seen or seen.add(j.job_id))]

    def _fetch_query(self, query: str, cfg: SourceConfig, api_key: str) -> list[JobPosting]:
        num_pages = max(1, cfg.results_wanted // 10)

        resp = requests.get(
            _BASE_URL,
            headers={
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
            },
            params={
                "query": query,
                "page": "1",
                "num_pages": str(num_pages),
                "date_posted": _date_posted_filter(cfg.hours_old),
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])

        jobs: list[JobPosting] = []
        for item in data[: cfg.results_wanted]:
            job = self._normalize(item)
            if job:
                jobs.append(job)
        return jobs

    def _normalize(self, item: dict) -> JobPosting | None:
        url = item.get("job_apply_link", "") or item.get("job_google_link", "")
        if not url:
            return None

        # Use JSearch's own job_id if available, else hash the URL
        raw_id = item.get("job_id", "")
        job_id = raw_id if raw_id else hashlib.md5(url.encode()).hexdigest()

        description = item.get("job_description", "")[:_MAX_DESCRIPTION_CHARS]

        raw_city = item.get("job_city", "") or ""
        raw_state = item.get("job_state", "") or ""
        raw_country = item.get("job_country", "") or ""
        location_parts = [p for p in [raw_city, raw_state, raw_country] if p]
        location = ", ".join(location_parts) if location_parts else "Unknown"
        is_remote = bool(item.get("job_is_remote"))
        if is_remote:
            location = f"Remote — {location}".strip(" — ")

        date_posted: datetime | None = None
        raw = item.get("job_posted_at_datetime_utc")
        if raw:
            try:
                date_posted = datetime.fromisoformat(raw.rstrip("Z"))
            except Exception:
                pass

        title = item.get("job_title", "")
        job_type_raw = item.get("job_employment_type", "") or ""
        work_arrangement = parse_work_arrangement(
            is_remote or None,
            location,
            title,
            description,
        )
        employment_type = parse_employment_type(job_type_raw, title, description)
        country, region, city = parse_location(location)
        # Prefer JSearch's explicit fields when the parser couldn't resolve them
        country = country or raw_country
        region = region or raw_state
        city = city or raw_city

        return JobPosting(
            job_id=job_id,
            title=title,
            company=item.get("employer_name", ""),
            location=location,
            description=description,
            url=url,
            source="jsearch",
            date_posted=date_posted,
            work_arrangement=work_arrangement,
            employment_type=employment_type,
            country=country,
            region=region,
            city=city,
            is_contract=employment_type in ("Contract", "Temporary", "Part-time"),
        )
