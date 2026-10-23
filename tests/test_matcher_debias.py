"""T51 — matching must not presuppose a profession.

the product spec: the product behaved as though every user is a QA engineer. This
module covers the two mechanisms on the *matching* side:

1. ``local_matcher`` scored every résumé against a hardcoded QA title regex and
   an 18-term QA keyword list. The load-bearing assertion is behavioural and
   runs in *both* directions: a non-QA résumé must rank a matching posting
   above an unrelated QA posting, **and** a QA résumé must still rank the QA
   posting first. De-biasing in a way that broke QA users would just move the
   bias somewhere else.
2. ``orchestrator._title_is_relevant`` waved through any title carrying a QA
   keyword, and this gate runs *before* scoring — so for a non-LinkedIn source
   it decided what a nurse was ever shown, whatever the scorer then did.

The third section pins the hole T50 left behind: a user's correction to their
job titles must reach matching, not only form filling.

The extraction prompt — the bias's third mechanism, and the subtlest — is covered
separately in tests/test_profiler_prompt.py, because it is asserted on text
rather than behaviour and the reasoning for that differs.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from jobfinder import derived_profile
from jobfinder.matcher.local_matcher import score_job
from jobfinder.matcher.models import JobPosting
from jobfinder.matcher.profile import MatchProfile
from jobfinder.orchestrator import _title_is_relevant
from jobfinder.resume.profiler import ResumeProfile

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Fixtures: four professions the product was never built for ────────────────
#
# Each entry is a (profile, matching posting) pair written from the profession's
# own vocabulary. Descriptions are kept deliberately similar in length and
# density to the QA posting below, so a score comparison reflects vocabulary
# alignment rather than one posting simply having more text to match against.


def _posting(job_id: str, title: str, job_function: str, description: str) -> JobPosting:
    return JobPosting(
        job_id=job_id,
        title=title,
        company="Example Employer",
        location="",
        description=description,
        url=f"https://example.com/{job_id}",
        source="indeed",
        job_function=job_function,
        experience_range="5-9 years",
    )


_QA_POSTING = _posting(
    "qa-1",
    "Senior QA Engineer",
    "Quality Assurance",
    "We are hiring a Senior QA Engineer to own test automation across our "
    "platform. You will build Selenium and Cypress suites, grow our regression "
    "testing coverage, write test plans and test cases, run exploratory "
    "testing, and drive quality processes with the engineering team. Experience "
    "with API testing, BDD and Cucumber, Postman, JIRA and CI/CD pipelines is "
    "expected. You will report defects, maintain our automation framework, and "
    "mentor other testers on test strategy and shift-left testing.",
)


_PROFESSIONS: dict[str, tuple[ResumeProfile, JobPosting]] = {
    "nurse": (
        ResumeProfile(
            skills=[
                "patient care",
                "triage",
                "medication administration",
                "wound care",
                "vital signs",
                "care planning",
                "patient assessment",
                "phlebotomy",
            ],
            tools=["Epic", "Cerner", "Meditech", "infusion pump"],
            job_titles=[
                "Registered Nurse",
                "Charge Nurse",
                "Nurse Practitioner",
                "Clinical Nurse",
            ],
            domains=["acute care", "emergency medicine", "critical care"],
            seniority_levels=["Senior", "Charge"],
            keywords=[
                "patient outcomes",
                "bedside care",
                "clinical documentation",
                "infection control",
                "nursing care",
            ],
            years_experience=8,
            target_job_functions=["Health Care Provider"],
        ),
        _posting(
            "nurse-1",
            "Registered Nurse — Emergency Department",
            "Health Care Provider",
            "Our emergency medicine team is hiring a Registered Nurse to "
            "deliver bedside care in a busy acute care setting. You will "
            "perform triage and patient assessment, take vital signs, handle "
            "medication administration and wound care, and own clinical "
            "documentation in Epic. Critical care experience, phlebotomy, and "
            "a strong record on infection control and patient outcomes are "
            "expected. You will contribute to care planning and nursing care "
            "standards alongside our charge nurse team.",
        ),
    ),
    "chef": (
        ResumeProfile(
            skills=[
                "menu development",
                "food costing",
                "line cooking",
                "butchery",
                "pastry",
                "plating",
                "inventory management",
                "kitchen management",
            ],
            tools=["combi oven", "sous vide circulator", "Toast POS", "MarketMan"],
            job_titles=["Sous Chef", "Executive Chef", "Chef de Partie", "Head Chef"],
            domains=["fine dining", "hospitality", "banquet service"],
            seniority_levels=["Senior", "Head", "Executive"],
            keywords=[
                "food safety",
                "kitchen brigade",
                "seasonal menu",
                "portion control",
                "back of house",
            ],
            years_experience=9,
            target_job_functions=["Food and Beverage"],
        ),
        _posting(
            "chef-1",
            "Sous Chef — Fine Dining",
            "Food and Beverage",
            "A fine dining restaurant is hiring a Sous Chef to run our kitchen "
            "brigade with the head chef. You will own menu development and "
            "seasonal menu changes, line cooking on service, butchery and "
            "pastry sections, and plating standards. Responsibilities include "
            "food costing, portion control, inventory management and food "
            "safety compliance across back of house. Hospitality experience "
            "and strong kitchen management are expected.",
        ),
    ),
    "paralegal": (
        ResumeProfile(
            skills=[
                "legal research",
                "document review",
                "discovery",
                "deposition summaries",
                "court filings",
                "case management",
                "contract review",
                "trial preparation",
            ],
            tools=["Relativity", "Westlaw", "LexisNexis", "Clio"],
            job_titles=[
                "Paralegal",
                "Senior Paralegal",
                "Litigation Paralegal",
                "Legal Assistant",
            ],
            domains=["litigation", "corporate law", "intellectual property"],
            seniority_levels=["Senior", "Lead"],
            keywords=[
                "e-discovery",
                "privilege review",
                "legal documentation",
                "docketing",
                "client intake",
            ],
            years_experience=7,
            target_job_functions=["Legal"],
        ),
        _posting(
            "paralegal-1",
            "Litigation Paralegal",
            "Legal",
            "Our litigation group is hiring a Litigation Paralegal to support "
            "trial preparation and case management. You will run legal "
            "research, document review and e-discovery in Relativity, draft "
            "deposition summaries, manage court filings and docketing, and "
            "assist with privilege review. Westlaw and LexisNexis experience, "
            "contract review exposure, and strong legal documentation habits "
            "are expected. Corporate law or intellectual property background "
            "is a plus, as is client intake experience.",
        ),
    ),
    "marketing": (
        ResumeProfile(
            skills=[
                "campaign management",
                "content marketing",
                "SEO",
                "email marketing",
                "brand positioning",
                "copywriting",
                "market research",
                "demand generation",
            ],
            tools=["HubSpot", "Marketo", "Google Analytics", "Figma"],
            job_titles=[
                "Marketing Manager",
                "Brand Manager",
                "Content Marketing Manager",
                "Demand Generation Manager",
            ],
            domains=["B2B marketing", "consumer goods", "digital advertising"],
            seniority_levels=["Senior", "Manager"],
            keywords=[
                "go-to-market",
                "lead nurturing",
                "marketing funnel",
                "customer acquisition",
                "campaign performance",
            ],
            years_experience=8,
            target_job_functions=["Marketing"],
        ),
        _posting(
            "marketing-1",
            "Senior Marketing Manager — Demand Generation",
            "Marketing",
            "We are hiring a Senior Marketing Manager to own demand generation "
            "and campaign management. You will run content marketing, SEO and "
            "email marketing programmes in HubSpot and Marketo, own brand "
            "positioning and copywriting, and report campaign performance from "
            "Google Analytics. B2B marketing experience, market research "
            "skills, and a track record on customer acquisition, lead "
            "nurturing and the marketing funnel are expected. You will shape "
            "our go-to-market motion with sales.",
        ),
    ),
}


def _golden_qa_profile() -> ResumeProfile:
    """The committed QA profile — a legitimate regression corpus for a QA user.

    Reused here rather than re-written so the "QA users keep working" direction
    is asserted against the same profile T4's golden file pins.
    """
    path = _REPO_ROOT / "tests" / "fixtures" / "golden" / "profile.json"
    with open(path, encoding="utf-8") as fh:
        return ResumeProfile(**json.load(fh))


# ── 1. Scoring: both directions ──────────────────────────────────────────────


@pytest.mark.parametrize("profession", sorted(_PROFESSIONS))
@pytest.mark.parametrize("has_target_functions", [True, False], ids=["stage5", "no-target-functions"])
def test_matching_posting_outranks_unrelated_qa_posting(profession, has_target_functions):
    """The inverse of the biased behaviour: the user's own field wins.

    Run with and without ``target_job_functions``, because that field decides
    which branch of the function axis runs and the two told very different
    stories before this task. Measured on the pre-fix scorer, a Stage-5
    profile's 10-point function bonus was the *only* thing keeping these
    postings in the right order — strip it and the unrelated QA posting came
    within 2 points of outranking a nurse's own posting (49 vs 45), on the
    strength of a title bonus it got from a hardcoded regex.
    """
    profile, own_posting = _PROFESSIONS[profession]
    if not has_target_functions:
        profile = replace(profile, target_job_functions=[])
    own = score_job(profile, own_posting)
    qa = score_job(profile, _QA_POSTING)
    assert own.score > qa.score, (
        f"{profession}: own posting scored {own.score} ({own.reasoning}) but the "
        f"unrelated QA posting scored {qa.score} ({qa.reasoning})"
    )


@pytest.mark.parametrize("profession", sorted(_PROFESSIONS))
def test_qa_profile_still_prefers_the_qa_posting(profession):
    """The other direction: de-biasing must not break the QA user it was built for."""
    _, other_posting = _PROFESSIONS[profession]
    qa_profile = _golden_qa_profile()
    qa = score_job(qa_profile, _QA_POSTING)
    other = score_job(qa_profile, other_posting)
    assert qa.score > other.score, (
        f"QA profile scored the {profession} posting {other.score} ({other.reasoning}) "
        f"at or above the QA posting {qa.score} ({qa.reasoning})"
    )


@pytest.mark.parametrize("profession", sorted(_PROFESSIONS))
def test_title_axis_credits_the_profiles_own_distinctive_title_words(profession):
    """The 'does this title signal a role you target?' axis is profile-derived.

    Pinned on the ``title`` signal specifically, because an aggregate score
    could hide the title axis being driven by a hardcoded vocabulary: the QA
    posting's title must earn this profile nothing.
    """
    profile, own_posting = _PROFESSIONS[profession]
    own = score_job(profile, own_posting)
    qa = score_job(profile, _QA_POSTING)
    assert own.signals["title"] > qa.signals["title"]


def test_short_distinctive_title_acronyms_are_not_lost():
    """A two-letter acronym in the user's own titles must still be a signal.

    This is why the hardcoded regex existed: ``best_title_overlap`` only
    considers words longer than three characters, so "QA" in "QA Engineer"
    was invisible to it and the regex was compensating. The replacement has to
    carry short acronyms from *any* profession — "RN", "CPA", "QA" alike —
    or de-biasing would silently downgrade everyone who has one.
    """
    profile = ResumeProfile(job_titles=["RN", "Registered Nurse"])
    with_acronym = score_job(profile, _posting("a", "RN — Night Shift", "", ""))
    without = score_job(profile, _posting("b", "Night Shift Associate", "", ""))
    assert with_acronym.signals["title"] > without.signals["title"]


def test_function_axis_fallback_uses_the_profiles_own_domain_vocabulary():
    """With no ``job_function`` on the posting, the fallback must not be QA words.

    Both postings here lack a ``job_function``, which is the branch that used
    to run an 18-term QA keyword list over the description.
    """
    profile, own_posting = _PROFESSIONS["chef"]
    own = score_job(profile, _posting("c", own_posting.title, "", own_posting.description))
    qa = score_job(profile, _posting("d", _QA_POSTING.title, "", _QA_POSTING.description))
    assert own.signals["function"] > qa.signals["function"]


def test_empty_profile_scores_everything_equally_rather_than_favouring_qa():
    """A profile with nothing in it has no opinion — not a QA opinion."""
    empty = ResumeProfile()
    qa = score_job(empty, _QA_POSTING)
    nurse = score_job(empty, _PROFESSIONS["nurse"][1])
    assert qa.score == nurse.score


# ── 2. The pre-scoring relevance filter ──────────────────────────────────────


def test_title_filter_does_not_give_qa_titles_a_free_pass():
    """With an empty profile, a QA title is no more relevant than any other.

    Two cases in tests/test_orchestrator_filters.py asserted the opposite
    (``("Senior Quality Assurance Lead", "indeed", [], True)``); they pinned
    the bias and are inverted there.
    """
    empty = MatchProfile.from_resume_profile(ResumeProfile())
    qa_job = _posting("e", "Senior Quality Assurance Lead", "", "")
    assert _title_is_relevant(qa_job, empty) is False


@pytest.mark.parametrize("profession", sorted(_PROFESSIONS))
def test_title_filter_keeps_the_users_own_postings_and_drops_the_qa_one(profession):
    """The filter runs *before* scoring, so a biased filter hides the fix."""
    profile, own_posting = _PROFESSIONS[profession]
    assert _title_is_relevant(own_posting, profile) is True
    assert _title_is_relevant(_QA_POSTING, profile) is False


# ── 3. A user's correction must reach matching (the T50 hole) ────────────────


def test_a_user_edit_to_job_titles_changes_matching():
    """T50 made user edits authoritative, but scoring never read that surface.

    Before this task a user who corrected their job titles changed what
    FillerBot typed into forms and nothing about which jobs they were shown.
    """
    agentic = ResumeProfile(job_titles=["QA Engineer"], skills=["test automation"])
    corrected = derived_profile.resolve(
        user_edits={"job_titles": ["Registered Nurse", "Charge Nurse"]},
        resume_profile=agentic,
    )
    nurse_profile = MatchProfile.from_derived(corrected)
    nurse_posting = _PROFESSIONS["nurse"][1]

    assert nurse_profile.job_titles == ("Registered Nurse", "Charge Nurse")
    corrected_score = score_job(nurse_profile, nurse_posting)
    uncorrected_score = score_job(agentic, nurse_posting)
    assert corrected_score.signals["title"] > uncorrected_score.signals["title"]


def test_the_user_layer_wins_over_the_agentic_layer_in_the_match_profile():
    """Precedence is T50's; this pins that the matcher's view honours it."""
    resolved = derived_profile.resolve(
        user_edits={"skills": ["patient care", "triage"]},
        resume_profile=ResumeProfile(skills=["test automation", "selenium"]),
    )
    assert MatchProfile.from_derived(resolved).skills == ("patient care", "triage")


# ── 4. No profession literals left in the scoring or extraction modules ──────


@pytest.mark.parametrize(
    "module_path",
    [
        "jobfinder/matcher/local_matcher.py",
        "jobfinder/matcher/models.py",
        "jobfinder/matcher/profile.py",
        "jobfinder/resume/profiler.py",
        "jobfinder/orchestrator.py",
    ],
)
def test_scoring_and_extraction_modules_name_no_profession(module_path):
    """The DoD's grep, as an assertion — over executable code.

    Comments and docstrings are excluded, which is the DoD's own "historical
    comments" carve-out and is also the stronger property to assert. A
    profession name in a comment biases nothing; a comment that cites the product spec
    in order to explain why the code carries no profession vocabulary is
    the *opposite* of the defect, and a test that forbade it would be
    satisfiable by deleting the documentation. What must not exist is a
    profession's vocabulary in code or in a prompt the model sees — so string
    literals are checked (``_SYSTEM_PROMPT`` is one), docstrings are not.

    ``jobfinder/resume/heuristic.py`` needs no entry here: its only two matches
    are exactly such citations. ``matcher/location_parser.py`` needs none
    either — contrary to the task brief it has **zero** matches for this
    pattern, because "canada" does not contain the substring "qa".
    """
    offenders = _profession_literals_in_code((_REPO_ROOT / module_path).read_text(encoding="utf-8"))
    assert not offenders, f"profession literals in {module_path} code:\n" + "\n".join(offenders)


def _profession_literals_in_code(source: str) -> list[str]:
    """Lines of ``source`` naming a profession, ignoring comments and docstrings."""
    import ast
    import io
    import re
    import tokenize

    docstring_lines: set[int] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            docstring_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))

    comment_lines = {
        tok.start[0] for tok in tokenize.generate_tokens(io.StringIO(source).readline) if tok.type == tokenize.COMMENT
    }

    pattern = re.compile(r"\bqa\b|quality assurance|sdet|selenium", re.IGNORECASE)
    return [
        f"  {n}: {line.strip()}"
        for n, line in enumerate(source.splitlines(), 1)
        if n not in docstring_lines and n not in comment_lines and pattern.search(line)
    ]


def test_the_literal_check_does_not_pass_by_ignoring_code():
    """Guard the guard: a profession literal in code must still be caught.

    Without this, the comment/docstring exclusions above could be widened until
    the assertion was vacuous.
    """
    assert _profession_literals_in_code('TITLES = ["QA Engineer"]\n')
    assert _profession_literals_in_code('PROMPT = """Extract titles like SDET."""\nX = PROMPT\n')
    assert not _profession_literals_in_code("# historical: the QA bias of the product spec\nX = 1\n")
    assert not _profession_literals_in_code('"""Module docstring mentioning Selenium for history."""\nX = 1\n')
