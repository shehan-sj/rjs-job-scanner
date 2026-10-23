"""Pure-Python local job matcher — zero API cost per job.

Scoring (max 100):
  Title alignment   30 pts  — does the job title signal a role you target?
  Skills match      30 pts  — resume skills that appear in the job's structured
                              LinkedIn skills list (preferred) or its description
  Tools match       15 pts  — resume tools that appear anywhere in the posting
  Seniority fit     15 pts  — does job_level / experience_range sit in the
                              candidate's range (±1 level tolerance)
  Function fit      10 pts  — does LinkedIn's job_function align with the
                              candidate's target areas (falls back to the
                              profile's own domain vocabulary)

Every axis is exposed on ``MatchResult.signals`` so the card can render a
"Why matched" breakdown.

Profession neutrality (T51)
-----------------------------------------
This module used to hold a hardcoded QA title regex and an 18-term QA keyword
list, so every résumé was scored against one profession's vocabulary. Measured
on four non-QA fixtures, an unrelated QA posting scored 73–86/100 on the title
axis against a nurse's, chef's, paralegal's and marketer's own profile, while
each user's genuinely matching posting scored 26–53.

Every term scoring now uses comes off the user's own profile, via
:class:`~jobfinder.matcher.profile.MatchProfile`. There is no profession
vocabulary in this file, and adding one back would reintroduce that defect.
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import JobPosting, MatchResult
from .profile import MatchProfile

_SENIORITY_ORDER = [
    "intern",
    "entry",
    "associate",
    "junior",
    "mid",
    "mid-senior",
    "senior",
    "staff",
    "lead",
    "principal",
    "manager",
    "director",
    "head",
    "vp",
    "executive",
]

# LinkedIn's job_level values map roughly to these buckets
_LEVEL_TO_RANK = {
    "internship": 0,
    "entry level": 1,
    "associate": 2,
    "mid-senior level": 5,
    "director": 11,
    "executive": 13,
}


# ── Scoring weights (must sum to 100) ─────────────────────────────────────────

_W_TITLE = 30
_W_SKILLS = 30
_W_TOOLS = 15
_W_SENIORITY = 15
_W_FUNCTION = 10

# Caps determine how many hits saturate an axis
_SKILLS_CAP = 10
_TOOLS_CAP = 8
#: Domain-vocabulary hits that saturate the function-fit fallback.
_FUNCTION_TERMS_CAP = 6


# ── Helpers ──────────────────────────────────────────────────────────────────


def _normalize_terms(terms: list[str]) -> list[str]:
    """Split slash-separated compound terms and return lowercased, de-duplicated list."""
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        for part in t.split("/"):
            clean = part.strip().lower()
            if clean and clean not in seen:
                seen.add(clean)
                out.append(clean)
    return out


def _term_matches(term: str, haystack: str) -> bool:
    if not term:
        return False
    if term in haystack:
        return True
    words = [w for w in term.split() if len(w) > 3]
    if len(words) >= 2:
        return all(w in haystack for w in words)
    return False


def _count_matches(terms: list[str], haystack: str) -> list[str]:
    return [t for t in terms if _term_matches(t, haystack)]


def _rank_for_level(text: str) -> int | None:
    """Best-effort map of a seniority string to an integer rank."""
    if not text:
        return None
    t = text.strip().lower()
    if t in _LEVEL_TO_RANK:
        return _LEVEL_TO_RANK[t]
    for i, keyword in enumerate(_SENIORITY_ORDER):
        if keyword in t:
            return i
    return None


def _years_from_range(experience_range: str) -> int | None:
    """Extract the MAXIMUM years from an experience_range like "5-7 years"."""
    if not experience_range:
        return None
    nums = re.findall(r"\d+", experience_range)
    if not nums:
        return None
    return max(int(n) for n in nums)


# ── Main scorer ──────────────────────────────────────────────────────────────


def score_job(profile: MatchProfile | object, job: JobPosting) -> MatchResult:
    """Score a job posting against the user's profile.

    Accepts a :class:`~jobfinder.matcher.profile.MatchProfile` — which the
    orchestrator builds **once per scan** from T50's derived profile, so a
    user's own corrections reach matching — or any profile-shaped object
    (a ``ResumeProfile``), which is adapted here. Adapting per job is cheap but
    not free: hot callers should pass a ``MatchProfile``.
    """
    profile = MatchProfile.coerce(profile)
    title_lower = job.title.lower()
    # Prefer LinkedIn's structured skills list as the skills haystack;
    # fall back to the description for sources that don't expose it.
    ls_text = " ".join(job.linkedin_skills).lower()
    haystack = f"{title_lower}\n{ls_text}\n{job.description.lower()}"

    signals: dict[str, int] = {}

    # ── 1. Title alignment (0–30) ─────────────────────────────────────────
    # Does the title carry a distinctive word from the user's OWN target
    # titles (or domains)? This replaces the QA regex; the weight is unchanged
    # so the axis keeps its shape, only its vocabulary changes.
    targets_role = profile.title_signals_target_role(title_lower)
    seniority_hit = any(s.lower() in title_lower for s in profile.seniority_levels)

    best_title_overlap = 0.0
    for target in profile.job_titles:
        words = [w for w in target.lower().split() if len(w) > 3]
        if words:
            frac = sum(1 for w in words if w in title_lower) / len(words)
            best_title_overlap = max(best_title_overlap, frac)

    title_raw = (14 if targets_role else 0) + (8 if seniority_hit else 0) + round(best_title_overlap * 8)
    title_pts = min(title_raw, _W_TITLE)
    signals["title"] = int(100 * title_pts / _W_TITLE) if _W_TITLE else 0

    # ── 2. Skills (0–30) ──────────────────────────────────────────────────
    # LinkedIn's structured list is the preferred haystack; each overlap is
    # worth more than a description mention because it's explicit.
    # For non-LinkedIn sources, description hits get full weight so scoring
    # is comparable across sources.
    skills_norm = _normalize_terms(profile.skills)
    if job.linkedin_skills:
        structured_lower = [s.lower() for s in job.linkedin_skills]
        explicit_hits = [t for t in skills_norm if any(t in ls or ls in t for ls in structured_lower)]
        desc_hits = [t for t in skills_norm if t not in explicit_hits and _term_matches(t, haystack)]
        skills_score = (len(explicit_hits) / _SKILLS_CAP) * 1.0 + (len(desc_hits) / _SKILLS_CAP) * 0.4
    else:
        desc_hits = _count_matches(skills_norm, haystack)
        explicit_hits = []
        skills_score = len(desc_hits) / _SKILLS_CAP
    skills_pts = min(skills_score, 1.0) * _W_SKILLS
    signals["skills"] = int(100 * skills_pts / _W_SKILLS) if _W_SKILLS else 0
    matched_skills = list(dict.fromkeys(explicit_hits + desc_hits))

    # ── 3. Tools (0–15) ───────────────────────────────────────────────────
    tools_norm = _normalize_terms(profile.tools)
    matched_tools = _count_matches(tools_norm, haystack)
    tools_pts = min(len(matched_tools) / _TOOLS_CAP, 1.0) * _W_TOOLS
    signals["tools"] = int(100 * tools_pts / _W_TOOLS) if _W_TOOLS else 0

    # ── 4. Seniority fit (0–15) ───────────────────────────────────────────
    seniority_pts, seniority_signal = _score_seniority(profile, job)
    signals["seniority"] = seniority_signal

    # ── 5. Function fit (0–10) ────────────────────────────────────────────
    function_pts, function_signal = _score_function(profile, job, haystack)
    signals["function"] = function_signal

    score = round(title_pts + skills_pts + tools_pts + seniority_pts + function_pts)
    score = min(score, 100)

    reasoning = (
        f"title:{int(title_pts)}  skills:{int(skills_pts)} "
        f"({len(explicit_hits)} from LinkedIn + {len(desc_hits)} from desc)  "
        f"tools:{int(tools_pts)} ({len(matched_tools)})  "
        f"seniority:{int(seniority_pts)}  function:{int(function_pts)}"
    )

    return MatchResult(
        job=job,
        score=int(score),
        reasoning=reasoning,
        skills_matched=(matched_skills + matched_tools)[:20],
        skills_missing=[],
        matched_at=datetime.utcnow(),
        signals=signals,
    )


# ── Sub-scorers ──────────────────────────────────────────────────────────────


def _score_seniority(profile: MatchProfile, job: JobPosting) -> tuple[float, int]:
    """Return (points_0_to_W_SENIORITY, signal_0_to_100)."""
    # Normalised to an int by MatchProfile, so no defensive getattr here.
    candidate_years = profile.years_experience or 0

    # 1. Compare the job's experience_range against the candidate's years
    job_max_years = _years_from_range(job.experience_range)
    # 2. Map job_level string into a rank bucket
    job_rank = _rank_for_level(job.job_level)
    # 3. Map candidate seniority_levels into a rank bucket (pick the highest)
    candidate_rank = None
    for level in profile.seniority_levels:
        r = _rank_for_level(level)
        if r is not None and (candidate_rank is None or r > candidate_rank):
            candidate_rank = r

    # If we have no signals at all, give a neutral half-score
    if job_max_years is None and job_rank is None:
        return _W_SENIORITY * 0.5, 50

    score = 0.0
    denom = 0

    if job_max_years is not None and candidate_years > 0:
        # Great fit: candidate_years within [0.7*max, 1.5*max]
        ratio = candidate_years / max(job_max_years, 1)
        if 0.7 <= ratio <= 1.5:
            score += 1.0
        elif 0.5 <= ratio < 0.7 or 1.5 < ratio <= 2.0:
            score += 0.6
        else:
            score += 0.2
        denom += 1

    if job_rank is not None and candidate_rank is not None:
        gap = abs(job_rank - candidate_rank)
        if gap <= 1:
            score += 1.0
        elif gap == 2:
            score += 0.6
        elif gap == 3:
            score += 0.3
        else:
            score += 0.0
        denom += 1

    if denom == 0:
        # Had range OR rank but no candidate counterpart — neutral
        return _W_SENIORITY * 0.5, 50

    normalized = score / denom
    return _W_SENIORITY * normalized, int(100 * normalized)


def _score_function(profile: MatchProfile, job: JobPosting, haystack: str) -> tuple[float, int]:
    """Return (points_0_to_W_FUNCTION, signal_0_to_100)."""
    # Prefer an explicit target list from the profile (Stage 5)
    targets = [t.lower() for t in profile.target_job_functions]
    job_function = (job.job_function or "").lower()

    if targets and job_function:
        if any(t in job_function or job_function in t for t in targets):
            return _W_FUNCTION, 100
        return 0.0, 0

    # Fallback for a posting with no structured job_function, or a profile with
    # no target list (a v2-era or heuristic-only profile). This branch used to
    # run an 18-term QA keyword list over the posting — the profession-vocabulary defect in its
    # purest form, since it handed a bonus to QA postings no matter whose
    # résumé was loaded. It now asks the same question of the user's own field:
    # does this posting talk about the domains and phrases *their* profile
    # names? ``skills``/``tools`` are deliberately excluded — they already own
    # two axes, and counting them here would reweight the scorer under cover of
    # de-biasing it.
    terms = profile.domain_vocabulary
    if not terms:
        # No domain vocabulary means no evidence either way. Zero rather than a
        # neutral half-score, matching what the old list returned for a profile
        # it had no words for, and constant across postings so it cannot
        # reorder results.
        return 0.0, 0
    hits = sum(1 for t in terms if t in haystack)
    # Saturate at six hits as before, but never above the number of terms the
    # profile actually has: a profile with three domain phrases could otherwise
    # score at most half this axis however well it matched.
    cap = min(_FUNCTION_TERMS_CAP, len(terms))
    normalized = min(hits / cap, 1.0)
    return _W_FUNCTION * normalized, int(100 * normalized)
