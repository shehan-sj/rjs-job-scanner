"""One-time Claude extraction of structured personal data from the resume.

Produces a FillerProfile (contact info, work history, education) suitable for
form-filling.  Cached to data/filler_profile.json — only regenerated when
the resume file changes.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import EducationEntry, FillerProfile, WorkEntry

logger = logging.getLogger(__name__)

_PROFILE_VERSION = "v1"

_FILLER_SCHEMA = {
    "type": "object",
    "properties": {
        "full_name": {"type": "string"},
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "address": {"type": "string"},
        "city": {"type": "string"},
        "state_province": {"type": "string"},
        "zip_postal": {"type": "string"},
        "country": {"type": "string"},
        "linkedin_url": {"type": "string"},
        "website": {"type": "string"},
        "summary": {"type": "string"},
        "current_title": {"type": "string"},
        "years_experience": {"type": "integer"},
        "work_experience": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "title": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "location": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["company", "title", "start_date", "end_date", "location", "description"],
                "additionalProperties": False,
            },
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "institution": {"type": "string"},
                    "degree": {"type": "string"},
                    "field_of_study": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "gpa": {"type": "string"},
                },
                "required": ["institution", "degree", "field_of_study", "start_date", "end_date", "gpa"],
                "additionalProperties": False,
            },
        },
        "skills": {"type": "array", "items": {"type": "string"}},
        "certifications": {"type": "array", "items": {"type": "string"}},
        "languages": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "full_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "address",
        "city",
        "state_province",
        "zip_postal",
        "country",
        "linkedin_url",
        "website",
        "summary",
        "current_title",
        "years_experience",
        "work_experience",
        "education",
        "skills",
        "certifications",
        "languages",
    ],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """\
You are a resume parser extracting structured personal data for job application \
form filling. Extract every field as accurately as possible from the resume text.

Guidelines:
- full_name: The candidate's complete name as written on the resume.
- first_name / last_name: Split from full_name.
- email, phone: Exactly as listed. Empty string if not found.
- address, city, state_province, zip_postal, country: From the resume header. \
  Use empty strings for any part not explicitly stated.
- linkedin_url, website: Exact URLs if present.
- summary: A 2-3 sentence professional summary suitable for an application form. \
  Rewrite from the resume's summary/objective — do not copy verbatim.
- current_title: The candidate's most recent job title.
- years_experience: Total professional years (best estimate).
- work_experience: Every position listed, ordered most recent first. \
  For dates use the format shown on the resume (e.g. "Jan 2020", "2019"). \
  end_date should be empty string for current roles. \
  description: 2-3 bullet points summarizing key responsibilities/achievements.
- education: All entries, most recent first. Use empty string for missing fields.
- skills: Technical and professional skills as short phrases.
- certifications: Any listed certifications or licenses.
- languages: Spoken/written languages with proficiency if stated \
  (e.g. "English (Native)", "French (Intermediate)")."""


def load_filler_profile(
    resume_path: str,
    api_key: str,
    model: str,
) -> FillerProfile:
    """Load from cache if resume hasn't changed, otherwise extract via Claude."""
    resolved = str(Path(resume_path).resolve())
    mtime = os.path.getmtime(resolved)

    cached = _load_cached(resolved, mtime)
    if cached:
        logger.info("Filler profile loaded from cache.")
        return cached

    logger.info("Extracting filler profile via Claude (one-time cost)...")
    profile = _extract(resolved, mtime, api_key, model)
    _save_cached(profile)
    logger.info("Filler profile extracted and cached: %s", profile.full_name)
    return profile


def extract_profile_without_caching(resume_path: str, api_key: str, model: str) -> FillerProfile:
    """A fresh Claude extraction that never touches ``filler_profile.json``
    (T54's re-parse). See ``resume/profiler.py``'s twin function — same
    reasoning: persisting here would make a re-parse's "proposal" already
    live, which the product spec forbids.
    """
    resolved = str(Path(resume_path).resolve())
    mtime = os.path.getmtime(resolved)
    return _extract(resolved, mtime, api_key, model)


def cached_filler_profile(resume_path: str) -> FillerProfile | None:
    """The cached agentic profile for this resume, or ``None``. Never extracts.

    See ``resume/profiler.py``'s ``cached_resume_profile``: the unified read
    model (T50) reads the agentic layer on every ``GET``, which must not be
    able to trigger a billed Anthropic call.
    """
    try:
        resolved = str(Path(resume_path).resolve())
        return _load_cached(resolved, os.path.getmtime(resolved))
    except OSError as exc:
        logger.warning("Could not read the resume for a cached filler-profile lookup: %s", exc)
        return None


def _load_cached(resolved_path: str, mtime: float) -> FillerProfile | None:
    from jobfinder.paths import filler_profile_path

    profile_path = filler_profile_path()
    if not profile_path.exists():
        return None
    try:
        with open(profile_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("profile_version") != _PROFILE_VERSION:
            return None
        if data.get("source_path") != resolved_path:
            return None
        if abs(data.get("source_mtime", 0) - mtime) > 1:
            logger.info("Resume changed — regenerating filler profile.")
            return None
        return _dict_to_profile(data)
    except Exception as exc:
        logger.warning("Could not load cached filler profile: %s", exc)
        return None


def _extract(
    resolved_path: str,
    mtime: float,
    api_key: str,
    model: str,
) -> FillerProfile:
    # Imported here, not at module scope, so importing this module does not
    # require the Anthropic SDK. The heuristic no-key path (T49) and the
    # unified read model that reads both layers (T50) have to be importable
    # with `anthropic` unavailable; only actually calling Claude needs it.
    import anthropic

    from ..resume.parser import load_resume

    resume = load_resume(resolved_path)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": resume.text}],
        output_config={"format": {"type": "json_schema", "schema": _FILLER_SCHEMA}},
    )

    text = next((b.text for b in response.content if b.type == "text"), "{}")
    data = json.loads(text)

    profile = _dict_to_profile(data)
    profile.source_path = resolved_path
    profile.source_mtime = mtime
    profile.generated_at = datetime.utcnow().isoformat()
    profile.profile_version = _PROFILE_VERSION
    return profile


def _dict_to_profile(data: dict) -> FillerProfile:
    work = [WorkEntry(**w) for w in data.get("work_experience", [])]
    edu = [EducationEntry(**e) for e in data.get("education", [])]
    simple_fields = {
        k: data[k]
        for k in (
            "full_name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "address",
            "city",
            "state_province",
            "zip_postal",
            "country",
            "linkedin_url",
            "website",
            "summary",
            "current_title",
        )
        if k in data
    }
    return FillerProfile(
        **simple_fields,
        years_experience=int(data.get("years_experience", 0)),
        work_experience=work,
        education=edu,
        skills=list(data.get("skills", [])),
        certifications=list(data.get("certifications", [])),
        languages=list(data.get("languages", [])),
        source_path=data.get("source_path", ""),
        source_mtime=data.get("source_mtime", 0.0),
        generated_at=data.get("generated_at", ""),
        profile_version=data.get("profile_version", ""),
    )


def _save_cached(profile: FillerProfile) -> None:
    from jobfinder.paths import filler_profile_path

    profile_path = filler_profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(profile)
    data["profile_version"] = _PROFILE_VERSION
    with open(profile_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
