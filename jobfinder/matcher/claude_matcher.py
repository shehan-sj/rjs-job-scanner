"""Optional agentic verification pass over locally-matched jobs.

This is NOT a first-pass scorer — the local matcher does that cheaply. When the
user enables ``agentic_filter`` in settings, each candidate job above the
minimum local score is handed to Claude with the already-extracted resume
profile. Claude re-reads the job text and returns a structured verdict
confirming (or correcting) the detected location, work arrangement, and
employment type, plus an adjusted score.

The resume profile is placed in the cached portion of the system prompt so it
is hashed once and re-used across every job in the batch, keeping latency and
token spend low.
"""

from __future__ import annotations

import json
import logging

import anthropic

from ..resume.profiler import ResumeProfile
from .models import JobPosting, MatchResult

logger = logging.getLogger(__name__)

_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "Match percentage 0-100 after reviewing the full job text.",
        },
        "reasoning": {
            "type": "string",
            "description": "1-2 sentence explanation of the match quality and any red flags.",
        },
        "skills_matched": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key skills/tools from the job that the candidate demonstrably has.",
        },
        "skills_missing": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Required skills/experience the candidate lacks.",
        },
        "country": {
            "type": "string",
            "description": "Country where the role is based, best guess from the job text. Empty string if unclear.",
        },
        "region": {
            "type": "string",
            "description": "State / province / region. Empty string if unclear.",
        },
        "city": {
            "type": "string",
            "description": "City. Empty string if unclear.",
        },
        "work_arrangement": {
            "type": "string",
            "enum": ["Remote", "Hybrid", "On-site", "Unknown"],
        },
        "employment_type": {
            "type": "string",
            "enum": ["Full-time", "Part-time", "Contract", "Temporary", "Internship", "Unknown"],
        },
        "disqualifying": {
            "type": "boolean",
            "description": "True if the role is fundamentally mismatched and should be hidden.",
        },
    },
    "required": [
        "score",
        "reasoning",
        "skills_matched",
        "skills_missing",
        "country",
        "region",
        "city",
        "work_arrangement",
        "employment_type",
        "disqualifying",
    ],
    "additionalProperties": False,
}

_SYSTEM_INSTRUCTIONS = """You are a rigorous recruiter verifying whether a candidate \
genuinely fits a job posting. You are given a pre-extracted candidate profile \
and one job posting. Read the full job text. Return a structured verdict.

Scoring rubric:
  90-100  Exceptional — meets or exceeds every key requirement
  75-89   Strong — meets most requirements with minor gaps
  60-74   Good — meets core requirements; some notable gaps
  40-59   Moderate — transferable skills but significant gaps
  0-39    Poor — fundamental mismatch

Weight:
  Technical skills alignment  40%
  Years / depth of experience 30%
  Domain / industry fit       20%
  Education / certifications  10%

Be strict: a score of 75+ must mean genuine readiness, not keyword overlap. \
Always include the detected country/region/city, work arrangement, and \
employment type fields — leave them empty strings if the posting truly does \
not say.

Set ``disqualifying`` to true if, after reading the job text, the role would \
almost certainly reject this candidate (wrong discipline, unachievable \
seniority, incompatible location demands, etc.)."""


class ClaudeMatcher:
    """Agentic verifier that re-scores a pre-screened job against the resume profile."""

    def __init__(self, api_key: str, model: str, profile: ResumeProfile):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._profile = profile
        self._profile_block = self._render_profile_block(profile)

    @staticmethod
    def _render_profile_block(profile: ResumeProfile) -> str:
        """Compact, deterministic markdown rendering of the profile.

        Kept stable across calls so prompt caching returns a hit on every job
        in a scan batch.
        """
        lines = [
            "## Candidate Profile",
            f"- Years of experience: {profile.years_experience}",
            f"- Target titles: {', '.join(profile.job_titles)}",
            f"- Seniority: {', '.join(profile.seniority_levels)}",
            f"- Skills: {', '.join(profile.skills)}",
            f"- Tools: {', '.join(profile.tools)}",
            f"- Domains: {', '.join(profile.domains)}",
            f"- Keywords: {', '.join(profile.keywords)}",
        ]
        return "\n".join(lines)

    def verify(self, result: MatchResult) -> tuple[MatchResult, bool]:
        """Re-score *result* using Claude.

        Returns ``(result, disqualifying)``. If the API call fails the original
        result is returned untouched with ``disqualifying=False``. The
        candidate profile is delivered as a cached system block so Anthropic
        reuses the hash across every job in a scan.
        """
        job = result.job
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=[
                    {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
                    {
                        "type": "text",
                        "text": self._profile_block,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": self._build_user_msg(job, result)}],
                output_config={
                    "format": {"type": "json_schema", "schema": _VERIFY_SCHEMA},
                },
            )
        except Exception as exc:  # network, API, auth, etc.
            logger.warning("Agentic verification failed for '%s': %s", job.title, exc)
            return result, False

        text = next((b.text for b in response.content if b.type == "text"), "{}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Agentic verifier returned unparseable JSON for '%s'", job.title)
            return result, False

        # Overlay agent-verified fields back onto the JobPosting so the widget
        # can render the corrected tags and the orchestrator can re-tier.
        job.country = data.get("country") or job.country
        job.region = data.get("region") or job.region
        job.city = data.get("city") or job.city
        arr = data.get("work_arrangement") or ""
        if arr in ("Remote", "Hybrid", "On-site", "Unknown"):
            job.work_arrangement = arr
        emp = data.get("employment_type") or ""
        if emp in ("Full-time", "Part-time", "Contract", "Temporary", "Internship", "Unknown"):
            job.employment_type = emp

        return MatchResult(
            job=job,
            score=int(data.get("score", result.score)),
            reasoning=str(data.get("reasoning", result.reasoning)),
            skills_matched=list(data.get("skills_matched", result.skills_matched)) or result.skills_matched,
            skills_missing=list(data.get("skills_missing", result.skills_missing)),
            matched_at=result.matched_at,
            geo_warning=result.geo_warning,
            tier=result.tier,
            agent_verified=True,
            scan_id=result.scan_id,
        ), bool(data.get("disqualifying", False))

    @staticmethod
    def _build_user_msg(job: JobPosting, local: MatchResult) -> str:
        return (
            f"## Job posting\n\n"
            f"**Title:** {job.title}\n"
            f"**Company:** {job.company}\n"
            f"**Raw location:** {job.location}\n"
            f"**Source:** {job.source}\n"
            f"**Local matcher score:** {local.score}\n\n"
            f"{job.description}"
        )
