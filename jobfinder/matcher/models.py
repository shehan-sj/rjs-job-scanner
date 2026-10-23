from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class JobPosting:
    job_id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    source: str
    date_posted: datetime | None = None
    work_arrangement: str = "Unknown"  # "Remote" | "Hybrid" | "On-site" | "Unknown"
    employment_type: str = "Unknown"  # "Full-time" | "Part-time" | "Contract" | "Temporary" | "Internship" | "Unknown"
    country: str = ""
    region: str = ""
    city: str = ""
    is_contract: bool = False

    # ── Structured fields from LinkedIn (Stage 1) ────────────────────────
    job_level: str = ""  # e.g. "Mid-Senior level", "Director", "Entry level"
    job_function: str = ""  # LinkedIn's own value, e.g. "Marketing", "Legal"
    experience_range: str = ""  # e.g. "5-7 years"
    listing_type: str = ""  # "promoted" | "organic" | ""
    job_url_direct: str = ""  # employer's own ATS URL (when present)
    linkedin_skills: list[str] = field(default_factory=list)

    # Company metadata
    company_url: str = ""
    company_logo: str = ""
    company_industry: str = ""
    company_num_employees: str = ""  # LinkedIn keeps this as a string like "1,001-5,000"

    # Compensation (optional — only present on a subset of postings)
    compensation_min: float | None = None
    compensation_max: float | None = None
    compensation_currency: str = ""
    compensation_interval: str = ""  # "yearly" | "hourly" | "monthly"


@dataclass
class MatchResult:
    job: JobPosting
    score: int  # 0–100
    reasoning: str
    skills_matched: list[str] = field(default_factory=list)
    skills_missing: list[str] = field(default_factory=list)
    matched_at: datetime = field(default_factory=datetime.utcnow)
    geo_warning: str | None = None  # e.g. "On-site · outside GTA/KW"
    tier: str = "global"  # "preferred" | "country" | "global"
    agent_verified: bool = False  # True if the optional Claude verification pass ran
    # Which scan cycle produced this result (jobfinder/orchestrator.py sets
    # this per run_scan() call; T57). Not matched_at-derived — see
    # jobfinder/storage/database.py's module docstring for why.
    scan_id: str | None = None

    # Why-matched breakdown (Stage 2) — each axis is 0–100 "how well did this fire"
    signals: dict[str, int] = field(
        default_factory=dict
    )  # e.g. {"title": 85, "skills": 70, "seniority": 60, "function": 100}

    @property
    def score_label(self) -> str:
        if self.score >= 85:
            return "Excellent"
        if self.score >= 70:
            return "Strong"
        if self.score >= 55:
            return "Good"
        return "Moderate"

    @property
    def score_color(self) -> str:
        if self.score >= 85:
            return "#27ae60"
        if self.score >= 70:
            return "#2980b9"
        if self.score >= 55:
            return "#f39c12"
        return "#e74c3c"
