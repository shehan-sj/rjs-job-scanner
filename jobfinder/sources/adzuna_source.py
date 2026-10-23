"""Adzuna — official jobs API. Free tier: 250 req/day.
Sign up at: https://developer.adzuna.com/
Aggregates listings from across the web including LinkedIn-posted roles.
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

_BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
_MAX_DESCRIPTION_CHARS = 8000


@register
class AdzunaSource(JobSource):
    """Official Adzuna jobs API (free tier, requires app_id + app_key)."""

    name = "adzuna"

    def fetch(self, source_config: SourceConfig) -> list[JobPosting]:
        app_id = source_config.extra.get("app_id", "")
        app_key = source_config.extra.get("app_key", "")
        if not app_id or not app_key:
            logger.error(
                "[adzuna] Missing app_id/app_key. Sign up at https://developer.adzuna.com/ and add them to config.yaml."
            )
            return []

        country = source_config.extra.get("country", "us")
        all_jobs: list[JobPosting] = []

        for term in source_config.search_terms:
            try:
                jobs = self._fetch_term(term, source_config, app_id, app_key, country)
                all_jobs.extend(jobs)
                logger.info("[adzuna] '%s' → %d jobs", term, len(jobs))
            except Exception as exc:
                logger.error("[adzuna] Failed for '%s': %s", term, exc)

        seen: set[str] = set()
        return [j for j in all_jobs if not (j.job_id in seen or seen.add(j.job_id))]

    def _fetch_term(self, term: str, cfg: SourceConfig, app_id: str, app_key: str, country: str) -> list[JobPosting]:
        max_days = max(1, cfg.hours_old // 24) if cfg.hours_old > 0 else 30

        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what": term,
            "where": cfg.location,
            "results_per_page": min(cfg.results_wanted, 50),
            "max_days_old": max_days,
            "content-type": "application/json",
        }

        resp = requests.get(
            _BASE_URL.format(country=country, page=1),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])

        jobs: list[JobPosting] = []
        for item in results:
            job = self._normalize(item)
            if job:
                jobs.append(job)
        return jobs

    def _normalize(self, item: dict) -> JobPosting | None:
        url = item.get("redirect_url", "")
        if not url:
            return None
        job_id = hashlib.md5(url.encode()).hexdigest()
        description = item.get("description", "")[:_MAX_DESCRIPTION_CHARS]

        date_posted: datetime | None = None
        raw = item.get("created")
        if raw:
            try:
                date_posted = datetime.fromisoformat(raw.rstrip("Z"))
            except Exception:
                pass

        company = item.get("company", {}).get("display_name", "")
        location = item.get("location", {}).get("display_name", "")
        title = item.get("title", "")
        contract_type = item.get("contract_type", "") or item.get("contract_time", "")

        work_arrangement = parse_work_arrangement(None, location, title, description)
        employment_type = parse_employment_type(contract_type, title, description)
        country, region, city = parse_location(location)

        # Adzuna also exposes an explicit country code per listing
        if not country:
            country_code = (item.get("category", {}) or {}).get("tag", "")
            if country_code:
                country, _, _ = parse_location(country_code)

        return JobPosting(
            job_id=job_id,
            title=title,
            company=company,
            location=location,
            description=description,
            url=url,
            source="adzuna",
            date_posted=date_posted,
            work_arrangement=work_arrangement,
            employment_type=employment_type,
            country=country,
            region=region,
            city=city,
            is_contract=employment_type in ("Contract", "Temporary", "Part-time"),
        )
