"""Remotive — free public API, no key required. Remote jobs only.
API docs: https://remotive.com/api/remote-jobs
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

_BASE_URL = "https://remotive.com/api/remote-jobs"
_MAX_DESCRIPTION_CHARS = 8000


@register
class RemotiveSource(JobSource):
    """Free, no-auth remote jobs API."""

    name = "remotive"

    def fetch(self, source_config: SourceConfig) -> list[JobPosting]:
        all_jobs: list[JobPosting] = []

        for term in source_config.search_terms:
            try:
                jobs = self._fetch_term(term, source_config)
                all_jobs.extend(jobs)
                logger.info("[remotive] '%s' → %d jobs", term, len(jobs))
            except Exception as exc:
                logger.error("[remotive] Failed for '%s': %s", term, exc)

        # Deduplicate
        seen: set[str] = set()
        return [j for j in all_jobs if not (j.job_id in seen or seen.add(j.job_id))]

    def _fetch_term(self, term: str, cfg: SourceConfig) -> list[JobPosting]:
        resp = requests.get(
            _BASE_URL,
            params={"search": term, "limit": cfg.results_wanted},
            timeout=30,
        )
        resp.raise_for_status()
        raw_jobs = resp.json().get("jobs", [])

        cutoff = self._cutoff_dt(cfg.hours_old)
        jobs: list[JobPosting] = []
        for item in raw_jobs:
            job = self._normalize(item)
            if job and (cutoff is None or self._posted_after(item, cutoff)):
                jobs.append(job)
        return jobs

    def _normalize(self, item: dict) -> JobPosting | None:
        url = item.get("url", "")
        if not url:
            return None
        job_id = hashlib.md5(url.encode()).hexdigest()
        description = item.get("description", "")[:_MAX_DESCRIPTION_CHARS]

        date_posted: datetime | None = None
        raw = item.get("publication_date")
        if raw:
            try:
                date_posted = datetime.fromisoformat(raw.rstrip("Z"))
            except Exception:
                pass

        title = item.get("title", "")
        raw_location = item.get("candidate_required_location", "Remote")
        job_type = item.get("job_type", "")

        # Remotive posts remote-only roles; treat unspecified as Remote
        work_arrangement = parse_work_arrangement(True, raw_location, title, description)
        employment_type = parse_employment_type(job_type, title, description)
        country, region, city = parse_location(raw_location)

        return JobPosting(
            job_id=job_id,
            title=title,
            company=item.get("company_name", ""),
            location=raw_location,
            description=description,
            url=url,
            source="remotive",
            date_posted=date_posted,
            work_arrangement=work_arrangement,
            employment_type=employment_type,
            country=country,
            region=region,
            city=city,
            is_contract=employment_type in ("Contract", "Temporary", "Part-time"),
        )

    @staticmethod
    def _cutoff_dt(hours_old: int) -> datetime | None:
        if hours_old <= 0:
            return None
        from datetime import timedelta

        return datetime.utcnow() - timedelta(hours=hours_old)

    @staticmethod
    def _posted_after(item: dict, cutoff: datetime) -> bool:
        raw = item.get("publication_date")
        if not raw:
            return True  # include if no date
        try:
            dt = datetime.fromisoformat(raw.rstrip("Z"))
            return dt >= cutoff
        except Exception:
            return True
