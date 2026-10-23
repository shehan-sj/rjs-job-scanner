"""The matcher's view of the user — profession-neutral by construction.

the product spec: scoring used to carry a hardcoded QA title regex and a QA
keyword list, so a marketing résumé was scored against QA vocabulary. The fix
is not a longer list. It is to make the *only* vocabulary scoring knows be the
user's own, read off their profile.

``MatchProfile`` is that vocabulary. It holds the eight fields scoring reads
and nothing else, and derives two term sets from them:

``title_vocabulary``
    The distinctive words of the user's own target titles, plus single-word
    domain terms. "Does this job title signal a role you target?" is answered
    against this. Generic rank and role words ("senior", "engineer",
    "manager") are stripped, because on their own they signal nothing — every
    profession has them. What is left is what makes a title *that* profession's:
    "qa", "sdet", "nurse", "paralegal", "chef".

    Crucially this keeps **short acronyms**. That is the whole reason the
    hardcoded regex existed: ``score_job``'s word-overlap pass only looks at
    words longer than three characters, so "QA" in "QA Engineer" was invisible
    to it and the regex compensated. Deriving the vocabulary instead carries
    "RN", "CPA" and "QA" alike, from whoever's résumé they came off.

``domain_vocabulary``
    The user's ``domains`` and ``keywords`` — the phrases their field's
    postings use. This replaces the QA keyword list in the function-fit
    fallback.

    ``skills`` and ``tools`` are deliberately *not* folded in here. They
    already carry two scoring axes of their own; counting them a third time on
    the function axis would quietly reweight the scorer while claiming only to
    de-bias it.

Why a separate type, rather than reading ``ResumeProfile`` directly
-------------------------------------------------------------------
T50 made a user's edits authoritative (``jobfinder/derived_profile.py``,
the product spec) but nothing routed scoring through that read model, so a user who
corrected their job titles changed what FillerBot typed into forms and nothing
about which jobs they were shown. :meth:`MatchProfile.from_derived` closes
that: the orchestrator resolves the derived profile **once per scan** and
hands the result down, so the per-job hot loop pays nothing for it.

:meth:`MatchProfile.coerce` keeps a plain ``ResumeProfile`` (and any
profile-shaped object) accepted, so the golden-file regeneration script and
existing callers need no changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..derived_profile import DerivedProfile

#: Rank and role words that appear in every profession's titles. A title
#: sharing only these with the user's targets tells us nothing — which is the
#: distinction the old regex blurred by hardcoding one field's words as if
#: they were the general case.
GENERIC_TITLE_WORDS = frozenset(
    {
        "senior",
        "junior",
        "lead",
        "principal",
        "manager",
        "director",
        "head",
        "staff",
        "associate",
        "engineer",
        "analyst",
        "specialist",
        "coordinator",
        "supervisor",
        "consultant",
        "architect",
        "developer",
        "assistant",
        "officer",
        "executive",
        "intern",
        "trainee",
        "representative",
        "technician",
        "administrator",
        "chief",
        "vice",
        "president",
    }
)

#: Function words that survive tokenisation but carry no signal.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "de",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)

#: The fields scoring reads. Named once so :meth:`MatchProfile.coerce` and
#: :meth:`MatchProfile.from_derived` cannot drift apart.
_LIST_FIELDS = (
    "job_titles",
    "skills",
    "tools",
    "domains",
    "keywords",
    "seniority_levels",
    "target_job_functions",
)

#: The word-length floor used by both title word-overlap passes
#: (``score_job``'s ``len(w) > 3`` and ``_title_is_relevant``'s). Terms at or
#: below it are invisible to those passes, which is what
#: :attr:`MatchProfile.short_title_terms` exists to carry.
_OVERLAP_MIN_WORD_LEN = 3

_WORD_RE = re.compile(r"[a-z0-9+#]+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _clean_terms(values: Iterable[Any]) -> tuple[str, ...]:
    """Lowercase, strip, drop blanks, preserve order, de-duplicate."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        if not isinstance(value, str):
            continue
        clean = value.strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)
    return tuple(out)


@dataclass
class MatchProfile:
    """Everything the local scorer is allowed to know about the user."""

    job_titles: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    seniority_levels: tuple[str, ...] = ()
    target_job_functions: tuple[str, ...] = ()
    years_experience: int = 0

    title_vocabulary: tuple[str, ...] = field(default=(), init=False, repr=False)
    short_title_terms: tuple[str, ...] = field(default=(), init=False, repr=False)
    domain_vocabulary: tuple[str, ...] = field(default=(), init=False, repr=False)
    _title_re: "re.Pattern[str] | None" = field(default=None, init=False, repr=False, compare=False)
    _short_title_re: "re.Pattern[str] | None" = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.title_vocabulary = self._derive_title_vocabulary()
        self.short_title_terms = tuple(t for t in self.title_vocabulary if len(t) <= _OVERLAP_MIN_WORD_LEN or " " in t)
        self.domain_vocabulary = self._derive_domain_vocabulary()
        self._title_re = _alternation(self.title_vocabulary)
        self._short_title_re = _alternation(self.short_title_terms)

    # ── Vocabulary derivation ────────────────────────────────────────────

    def _derive_title_vocabulary(self) -> tuple[str, ...]:
        """Distinctive words from the user's own titles, plus domain words.

        Multi-word domain entries ("acute care", "fine dining") are kept whole
        as well as split, because both shapes turn up in real titles.
        """
        terms: list[str] = []
        seen: set[str] = set()

        def add(term: str) -> None:
            if term and term not in seen:
                seen.add(term)
                terms.append(term)

        for title in self.job_titles:
            for token in _tokens(title):
                if token in GENERIC_TITLE_WORDS or token in _STOPWORDS:
                    continue
                add(token)

        for domain in self.domains:
            lowered = domain.strip().lower()
            if " " in lowered:
                add(lowered)
            for token in _tokens(domain):
                if token in GENERIC_TITLE_WORDS or token in _STOPWORDS:
                    continue
                add(token)

        return tuple(terms)

    def _derive_domain_vocabulary(self) -> tuple[str, ...]:
        """The phrases the user's field writes into postings."""
        terms: list[str] = []
        seen: set[str] = set()
        for value in list(self.domains) + list(self.keywords):
            lowered = value.strip().lower()
            if lowered and lowered not in seen:
                seen.add(lowered)
                terms.append(lowered)
        return tuple(terms)

    # ── Queries scoring asks ─────────────────────────────────────────────

    def title_signals_target_role(self, title: str) -> bool:
        """Does this job title carry a distinctive word from the user's targets?

        Used by the **scoring** title axis, where it replaces the QA regex's
        14-point bonus. The full vocabulary is right here: "nurse", "chef" and
        "qa" must each earn their owner the same bonus, or de-biasing would
        just favour whoever's job title happens to abbreviate.
        """
        if self._title_re is None:
            return False
        return self._title_re.search(title.lower()) is not None

    def title_carries_term_overlap_cannot_see(self, title: str) -> bool:
        """Does the title match a term the word-overlap pass is blind to?

        Used by the **relevance gate**, which is a narrower question than
        scoring's and deliberately gets a narrower answer.

        ``score_job`` and ``_title_is_relevant`` both compare titles word by
        word, but only for words longer than three characters. That is the
        precise gap the hardcoded QA regex was filling: "QA" in "QA Engineer"
        was invisible to it, so a literal list compensated. Multi-word domain
        phrases are invisible for a different reason — they are not in
        ``job_titles`` at all.

        Only those two shapes get a free pass through the gate. A longer single
        word like "automation" does not: the existing "two non-generic
        overlaps" rule already judges it, and widening the free pass to cover
        it would admit "Automation Guru" for someone targeting "Automation
        Specialist" — a loosening this task has no business making, and one
        that is already pinned against in tests/test_orchestrator_filters.py.
        """
        if self._short_title_re is None:
            return False
        return self._short_title_re.search(title.lower()) is not None

    # ── Constructors ─────────────────────────────────────────────────────

    @classmethod
    def from_resume_profile(cls, profile: Any) -> "MatchProfile":
        """Build from a ``ResumeProfile`` or any object with the same attributes."""
        kwargs: dict[str, Any] = {name: _clean_terms(getattr(profile, name, ()) or ()) for name in _LIST_FIELDS}
        kwargs["years_experience"] = _as_years(getattr(profile, "years_experience", 0))
        return cls(**kwargs)

    @classmethod
    def from_derived(cls, derived: "DerivedProfile") -> "MatchProfile":
        """Build from T50's read model, so a user's correction reaches matching.

        Reads through :class:`~jobfinder.derived_profile.DerivedProfile`, which
        resolves **user edit > agentic > heuristic** per field. Resolution has
        already happened by the time this runs; this only projects the fields
        scoring uses.
        """
        kwargs: dict[str, Any] = {}
        for name in _LIST_FIELDS:
            value = derived.value(name) if name in derived else ()
            kwargs[name] = _clean_terms(value if isinstance(value, (list, tuple)) else ())
        years = derived.value("years_experience") if "years_experience" in derived else 0
        kwargs["years_experience"] = _as_years(years)
        return cls(**kwargs)

    @classmethod
    def coerce(cls, profile: Any) -> "MatchProfile":
        """Accept a ``MatchProfile`` unchanged; adapt anything else."""
        if isinstance(profile, cls):
            return profile
        return cls.from_resume_profile(profile)


def _as_years(value: Any) -> int:
    """Years of experience as an int.

    T49's heuristic layer reports this as a *string* ("11"), and the read model
    coerces at its boundary — but a caller handing us a raw layer should not
    make the scorer explode either.
    """
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _alternation(terms: Iterable[str]) -> re.Pattern[str] | None:
    """One compiled word-boundary alternation over a profile's terms.

    Built once per profile rather than per job. ``(?<![a-z0-9])`` rather than
    ``\\b`` so that terms ending in ``+`` or ``#`` ("c++", "c#") still match.
    Longest-first so "quality assurance" wins over "quality".
    """
    ordered = sorted({t for t in terms if t}, key=len, reverse=True)
    if not ordered:
        return None
    joined = "|".join(re.escape(t) for t in ordered)
    return re.compile(rf"(?<![a-z0-9])(?:{joined})(?![a-z0-9])")
