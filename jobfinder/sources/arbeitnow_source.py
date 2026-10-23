"""Arbeitnow — free public API, no key required. Remote & European jobs.
API docs: https://www.arbeitnow.com/api
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta

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

_BASE_URL = "https://www.arbeitnow.com/api/job-board-api"
_MAX_DESCRIPTION_CHARS = 8000


@register
class ArbeitnowSource(JobSource):
    """Free, no-auth remote/EU jobs API."""

    name = "arbeitnow"

    def fetch(self, source_config: SourceConfig) -> list[JobPosting]:
        all_jobs: list[JobPosting] = []

        for term in source_config.search_terms:
            try:
                jobs = self._fetch_term(term, source_config)
                all_jobs.extend(jobs)
                logger.info("[arbeitnow] '%s' → %d jobs", term, len(jobs))
            except Exception as exc:
                logger.error("[arbeitnow] Failed for '%s': %s", term, exc)

        seen: set[str] = set()
        return [j for j in all_jobs if not (j.job_id in seen or seen.add(j.job_id))]

    def _fetch_term(self, term: str, cfg: SourceConfig) -> list[JobPosting]:
        cutoff = datetime.utcnow() - timedelta(hours=cfg.hours_old) if cfg.hours_old > 0 else None

        collected: list[JobPosting] = []
        page = 1

        while len(collected) < cfg.results_wanted:
            resp = requests.get(
                _BASE_URL,
                params={"search": term, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break

            for item in data:
                job = self._normalize(item)
                if not job:
                    continue
                if cutoff and not self._posted_after(item, cutoff):
                    # Arbeitnow returns newest-first; once we pass cutoff we can stop
                    return collected
                collected.append(job)
                if len(collected) >= cfg.results_wanted:
                    break
            page += 1

        return collected

    def _normalize(self, item: dict) -> JobPosting | None:
        url = item.get("url", "")
        if not url:
            return None
        job_id = hashlib.md5(url.encode()).hexdigest()
        description = item.get("description", "")[:_MAX_DESCRIPTION_CHARS]

        date_posted: datetime | None = None
        raw = item.get("created_at")
        if raw:
            try:
                date_posted = datetime.utcfromtimestamp(int(raw))
            except Exception:
                pass

        location = item.get("location", "")
        is_remote = bool(item.get("remote"))
        if is_remote:
            location = f"Remote — {location}".strip(" — ")

        title = item.get("title", "")
        job_type_raw = " ".join(item.get("job_types", []) or [])
        work_arrangement = parse_work_arrangement(is_remote or None, location, title, description)
        employment_type = parse_employment_type(job_type_raw, title, description)
        country, region, city = parse_location(location)

        return JobPosting(
            job_id=job_id,
            title=title,
            company=item.get("company_name", ""),
            location=location,
            description=description,
            url=url,
            source="arbeitnow",
            date_posted=date_posted,
            work_arrangement=work_arrangement,
            employment_type=employment_type,
            country=country,
            region=region,
            city=city,
            is_contract=employment_type in ("Contract", "Temporary", "Part-time"),
        )

    @staticmethod
    def _posted_after(item: dict, cutoff: datetime) -> bool:
        raw = item.get("created_at")
        if not raw:
            return True
        try:
            dt = datetime.utcfromtimestamp(int(raw))
            return dt >= cutoff
        except Exception:
            return True
