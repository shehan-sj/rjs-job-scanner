"""One-time Claude call to extract a structured profile from the resume.

The result is cached to data/resume_profile.json and only regenerated when
the resume file changes (detected via mtime). This means Claude is called
at most once per resume update — never per job scan.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROFILE_VERSION = "v3"  # bump this to force cache invalidation

_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "skills": {"type": "array", "items": {"type": "string"}},
        "tools": {"type": "array", "items": {"type": "string"}},
        "job_titles": {"type": "array", "items": {"type": "string"}},
        "domains": {"type": "array", "items": {"type": "string"}},
        "seniority_levels": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "years_experience": {"type": "integer"},
        # Stage-5 additions — used for seniority/function matching
        "target_job_functions": {"type": "array", "items": {"type": "string"}},
        "target_seniority_min": {"type": "integer"},  # years (use 0 for entry)
        "target_seniority_max": {"type": "integer"},  # years (use 0 for n/a)
        "target_industries": {"type": "array", "items": {"type": "string"}},
        "avoid_industries": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "skills",
        "tools",
        "job_titles",
        "domains",
        "seniority_levels",
        "keywords",
        "years_experience",
        "target_job_functions",
        "target_seniority_min",
        "target_seniority_max",
        "target_industries",
        "avoid_industries",
    ],
    "additionalProperties": False,
}

#: The extraction prompt.
#:
#: It names no profession, and that is a correctness property, not a style
# choice. This prompt used to be seeded with one field's
#: few-shot examples — "QA Engineer", "SDET", "Selenium", "Cypress" — which
#: biased the *agentic* path at its source: the model was shown one
#: profession's output and asked to produce more of it, whatever the résumé in
#: front of it said. A profile cannot be correct if it comes from a prompt that
#: presupposes the answer, and no amount of de-biasing downstream in the
#: matcher can recover a profile that was wrong when it was written.
#:
#: The few-shot examples were therefore replaced with (a) instructions about
#: the *form* each field takes, illustrated with abstract placeholders that
#: belong to no field of work, and (b) an explicit instruction to draw every
#: term from the résumé. Output *structure* is not at risk from this: it is
#: enforced separately by ``_PROFILE_SCHEMA`` as a json_schema output format,
#: not by example.
#:
#: Tests assert the absence of several professions' vocabulary, not only QA's,
#: so that this cannot be "fixed" by swapping one bias for another.
_SYSTEM_PROMPT = """You are a resume parser extracting a keyword profile for automated \
job-description matching. Your output must use the exact words and phrases that \
EMPLOYERS write in job postings — not resume jargon.

Draw every entry from THIS résumé only. Infer the candidate's field from what
they have actually done; do not assume a field, and do not add terms that are
common in job postings generally but absent from this résumé. If the résumé
does not support a field, leave it empty rather than guessing — an empty list
is a correct answer, a plausible-sounding wrong one is not.

Guidelines:
- skills:
    Competencies and methodologies as they appear in job postings.
    Use SHORT, common forms: the two- or three-word phrase an employer would
    put in a requirements list, not a longer phrase describing this person's
    particular achievement with it.
    Do NOT use slash-separated compound terms — where the résumé writes
    "A/B", output "A" and "B" as two separate entries.
    Target 15–25 entries.

- tools:
    Specific software, equipment, systems and platforms by their common names,
    whatever kind of work they are for. Use the official product name, spelled
    and capitalised as its maker writes it; do not reproduce typos from the
    résumé.
    Target 10–20 entries.

- job_titles:
    ALL roles this person is qualified for based on their experience — be broad.
    Include the exact titles they have held, the common variants employers use
    for the same job, and the adjacent titles their experience would qualify
    them for. Include any standard abbreviation of a title in their field
    alongside its full form, since postings use both.
    Target 10–15 entries.

- domains:
    The industry verticals and specialisms this person works in — the answer to
    "what kind of X are they?", not just "what are they?".
    Target 5–10 entries.

- seniority_levels:
    All applicable seniority words (e.g. "Senior", "Lead", "Principal",
    "Manager", "Staff"). These are rank words only — no job content.

- keywords:
    Additional terms likely to appear in matching job postings — focus on
    phrases employers in this person's field actually write, NOT unique
    accomplishments from this résumé.
    Target 10–15 entries.

- years_experience: Best estimate of total professional years.

- target_job_functions:
    LinkedIn's own "Job function" values that this candidate fits, written
    exactly as LinkedIn writes them. These are a fixed vocabulary of LinkedIn's,
    so use their wording rather than inventing a label. 1–4 entries.

- target_seniority_min, target_seniority_max:
    A sensible hiring range in total years of experience. For a candidate with
    6 years' experience you might output min=4, max=10 — broad enough to
    include stretch roles but not entry-level.

- target_industries:
    Industries the candidate would thrive in, as LinkedIn writes them.
    0–5 entries. Leave empty if the candidate is industry-agnostic.

- avoid_industries:
    Industries that are almost never a fit for this candidate. "Staffing and
    Recruiting" is a common one for candidates of every kind, because those
    postings come from agencies rather than the employer; include it only if
    the résumé gives no sign the person works in that industry themselves.
    0–3 entries."""


@dataclass
class ResumeProfile:
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    seniority_levels: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    years_experience: int = 0
    # Stage-5 additions
    target_job_functions: list[str] = field(default_factory=list)
    target_seniority_min: int = 0
    target_seniority_max: int = 0
    target_industries: list[str] = field(default_factory=list)
    avoid_industries: list[str] = field(default_factory=list)
    # Cache metadata (not used for matching)
    source_path: str = ""
    source_mtime: float = 0.0
    generated_at: str = ""
    profile_version: str = ""

    @property
    def all_terms(self) -> list[str]:
        """Flat list of every matchable term, lowercased."""
        terms = self.skills + self.tools + self.job_titles + self.domains + self.seniority_levels + self.keywords
        return [t.lower().strip() for t in terms if t.strip()]


def load_profile(resume_path: str, api_key: str, model: str) -> ResumeProfile:
    """Load from cache if resume hasn't changed, otherwise re-extract via Claude."""
    resolved = str(Path(resume_path).resolve())
    mtime = os.path.getmtime(resolved)

    cached = _load_cached(resolved, mtime)
    if cached:
        logger.info("Resume profile loaded from cache (%d terms).", len(cached.all_terms))
        return cached

    logger.info("Extracting resume profile via Claude (one-time cost)...")
    profile = _extract(resolved, mtime, api_key, model)
    _save_cached(profile)
    logger.info(
        "Profile extracted and cached: %d skills, %d tools, %d keywords.",
        len(profile.skills),
        len(profile.tools),
        len(profile.keywords),
    )
    return profile


def extract_profile_without_caching(resume_path: str, api_key: str, model: str) -> ResumeProfile:
    """A fresh Claude extraction that never touches ``resume_profile.json``
    (T54's re-parse).

    ``load_profile`` always calls :func:`_save_cached` after
    :func:`_extract`, which is right for every existing caller (``POST
    /api/resume``, a scan) — they *are* the moment this profile becomes
    authoritative. A re-parse is not: the product spec says a re-parse proposes
    changes for per-field acceptance and "a trapdoor that discards
    deliberate configuration" is exactly what persisting here would build,
    since every non-user field's "proposal" would already be live by the
    time the user saw it. ``_extract``/``_save_cached`` already being two
    separate functions is what makes this the one-line fix rather than a
    new code path through the Anthropic call.
    """
    resolved = str(Path(resume_path).resolve())
    mtime = os.path.getmtime(resolved)
    return _extract(resolved, mtime, api_key, model)


def cached_resume_profile(resume_path: str) -> ResumeProfile | None:
    """The cached agentic profile for this resume, or ``None``. Never extracts.

    The unified read model (T50) reads the agentic layer on every ``GET`` of
    the derived-profile surface, which must not be able to trigger a billed
    Anthropic call — so it needs the cache lookup without the extraction that
    :func:`load_profile` falls through to.
    """
    try:
        resolved = str(Path(resume_path).resolve())
        return _load_cached(resolved, os.path.getmtime(resolved))
    except OSError as exc:
        logger.warning("Could not read the resume for a cached profile lookup: %s", exc)
        return None


def _load_cached(resolved_path: str, mtime: float) -> ResumeProfile | None:
    from jobfinder.paths import resume_profile_path

    profile_path = resume_profile_path()
    if not profile_path.exists():
        return None
    try:
        with open(profile_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("profile_version") != _PROFILE_VERSION:
            logger.info("Profile schema version changed — regenerating profile.")
            return None
        if data.get("source_path") != resolved_path:
            return None
        if abs(data.get("source_mtime", 0) - mtime) > 1:
            logger.info("Resume file changed — regenerating profile.")
            return None
        return ResumeProfile(**{k: v for k, v in data.items() if k in ResumeProfile.__dataclass_fields__})
    except Exception as exc:
        logger.warning("Could not load cached profile: %s", exc)
        return None


def _extract(resolved_path: str, mtime: float, api_key: str, model: str) -> ResumeProfile:
    # Imported here, not at module scope, so importing this module does not
    # require the Anthropic SDK. The heuristic no-key path (T49) and the
    # unified read model that reads both layers (T50) have to be importable
    # with `anthropic` unavailable; only actually calling Claude needs it.
    import anthropic

    from .parser import load_resume

    resume = load_resume(resolved_path)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": resume.text}],
        output_config={"format": {"type": "json_schema", "schema": _PROFILE_SCHEMA}},
    )

    text = next((b.text for b in response.content if b.type == "text"), "{}")
    data = json.loads(text)

    return ResumeProfile(
        skills=data.get("skills", []),
        tools=data.get("tools", []),
        job_titles=data.get("job_titles", []),
        domains=data.get("domains", []),
        seniority_levels=data.get("seniority_levels", []),
        keywords=data.get("keywords", []),
        years_experience=int(data.get("years_experience", 0)),
        target_job_functions=list(data.get("target_job_functions", []) or []),
        target_seniority_min=int(data.get("target_seniority_min", 0) or 0),
        target_seniority_max=int(data.get("target_seniority_max", 0) or 0),
        target_industries=list(data.get("target_industries", []) or []),
        avoid_industries=list(data.get("avoid_industries", []) or []),
        source_path=resolved_path,
        source_mtime=mtime,
        generated_at=datetime.utcnow().isoformat(),
        profile_version=_PROFILE_VERSION,
    )


def _save_cached(profile: ResumeProfile) -> None:
    from jobfinder.paths import resume_profile_path

    profile_path = resume_profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(profile)
    data["profile_version"] = _PROFILE_VERSION
    with open(profile_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
