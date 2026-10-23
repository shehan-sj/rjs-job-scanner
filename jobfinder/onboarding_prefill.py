"""The resume-derived pre-fill onboarding offers (T52).

the product spec: "The resume is the source of truth for *who the user is and
what they want*, and the rest of configuration should follow from it. Today it
does not: onboarding asks for regions, countries and job titles that the
resume already states, and nothing derived from the resume reaches those
fields." T50 built the read model that knows the answers
(:mod:`jobfinder.derived_profile`); this module is the one place that maps
those answers onto the configuration fields onboarding writes.

===========================  ==================================================
derived field                configuration it pre-fills
===========================  ==================================================
``country``                  ``location_filter.search_country`` **and**
                             ``sources[].location``
``job_titles``               ``sources[].search_terms``
``target_job_functions``     ``role_filter.job_functions``
``seniority_levels``         ``role_filter.seniority_levels``
===========================  ==================================================

Three properties this holds to, each of them a decision rather than an
accident:

**It writes nothing.** This is a read model over a read model. The caller
shows the values, the user edits whichever they disagree with, and the finish
step persists them through the existing ``PUT /api/settings``. Nothing here
can overwrite a setting the user already has.

**It carries provenance, never a bare value.** The product spec: "a value the user
cannot tell the origin of is a value they cannot judge." Each field reports
``user`` / ``agentic`` / ``heuristic`` / ``absent`` straight from the read
model, plus a sentence a non-engineer can read, plus the configuration field
it lands in.

**It never guesses to fill a blank.** A field the resume does not state comes
back empty with provenance ``absent`` and a note saying so. The product's own
history is the argument: the product spec is a record of what one baked-in
assumption about somebody's job and country cost, and 7.8 is the last corner
the same assumption was hiding in.

Two user-edit layers, and why only one of them is written
---------------------------------------------------------
A "user edit" can mean two different things here, and conflating them loses
data quietly:

1. **T50's profile user-edit layer** (``derived_profile.save_user_edits``, in
   ``data/user_extras.yaml``) holds corrections to *facts about the person* —
   their titles, skills, country.
2. **Config** (``sources[].search_terms``,
   ``location_filter.search_country``, ``role_filter``) holds *search
   settings*, which is what onboarding populates.

Editing a pre-filled value writes **config only**. A search term is not a
claim about the person — plenty of people search for a title they have never
held, and somebody relocating searches a country they have never worked in.
The stronger reason is the one the product spec makes load-bearing: the profile
layer is the single thing a re-parse can never overwrite, so writing it during
onboarding would silently disable T54's accept-per-field re-parse for every
field onboarding happened to touch. Pinned by a test asserting
``user_extras.yaml`` is untouched by a finish that edits a pre-filled value.

The function axis has no keyless source, and says so
----------------------------------------------------
``target_job_functions`` is LinkedIn's own closed vocabulary and lives only in
the agentic ``ResumeProfile`` (the product spec's owner table). Deriving it from a
job title needs a profession keyword table — precisely what T49's heuristic
layer refuses to contain, because "a keyword list would rebuild that bias one
layer down, working best on the resumes it already over-fits and returning
nothing for a nurse or an accountant", which is how the product spec was resolved.
So with no API key this field is honestly empty and says a key would fill it.
Country, titles and seniority all pre-fill from the keyless path.
"""

from __future__ import annotations

from pathlib import Path

from . import derived_profile

# ── The four configuration fields onboarding pre-fills ───────────────────────

PREFILL_SEARCH_COUNTRY = "search_country"
PREFILL_SEARCH_TERMS = "search_terms"
PREFILL_JOB_FUNCTIONS = "job_functions"
PREFILL_SENIORITY_LEVELS = "seniority_levels"

#: Which extraction path the values on offer actually came from. ``user``
#: provenance is not an extraction, so a profile made entirely of user
#: corrections reports ``none`` here: the question this answers is "what read
#: your resume", and the honest answer is "nothing did".
EXTRACTION_AGENTIC = "agentic"
EXTRACTION_HEURISTIC = "heuristic"
EXTRACTION_NONE = "none"

#: LinkedIn's "Seniority level" facet, verbatim. ``jobfinder/filters.py``
#: compares ``role_filter.seniority_levels`` against a card's ``job_level``,
#: which is this vocabulary, so a pre-fill outside it does not merely fail to
#: help — it hides every job that reports a level at all.
LINKEDIN_SENIORITY_LEVELS = (
    "Internship",
    "Entry level",
    "Associate",
    "Mid-Senior level",
    "Director",
    "Executive",
)

#: Rank word to LinkedIn level. The keys cover both producers of
#: ``seniority_levels``: T49's heuristic bands (``intern``, ``junior``,
#: ``mid``, ``senior``, ``lead``, ``manager``, ``executive``) and the rank
#: words ``resume/profiler.py`` asks Claude for ("Senior", "Lead",
#: "Principal", "Manager", "Staff").
#:
#: ``manager`` and ``head`` are **deliberately absent**. T49's ``manager`` band
#: bundles manager, head, director and superintendent, which straddles two of
#: LinkedIn's levels — a manager is usually "Mid-Senior level" and a director
#: is "Director" — and picking either is a guess that silently narrows every
#: search. An unmapped word pre-fills nothing and says why instead.
_RANK_WORD_TO_LEVEL: dict[str, str] = {
    "intern": "Internship",
    "internship": "Internship",
    "apprentice": "Internship",
    "trainee": "Entry level",
    "graduate": "Entry level",
    "junior": "Entry level",
    "jnr": "Entry level",
    "jr": "Entry level",
    "entry": "Entry level",
    "entry level": "Entry level",
    "associate": "Associate",
    "mid": "Mid-Senior level",
    "mid-level": "Mid-Senior level",
    "intermediate": "Mid-Senior level",
    "senior": "Mid-Senior level",
    "snr": "Mid-Senior level",
    "sr": "Mid-Senior level",
    "mid-senior level": "Mid-Senior level",
    "lead": "Mid-Senior level",
    "leader": "Mid-Senior level",
    "principal": "Mid-Senior level",
    "staff": "Mid-Senior level",
    "supervisor": "Mid-Senior level",
    "director": "Director",
    "executive": "Executive",
    "chief": "Executive",
    "vp": "Executive",
    "vice president": "Executive",
    "president": "Executive",
    "c-level": "Executive",
}


def translate_seniority(words) -> tuple[list[str], list[str]]:
    """Rank words to LinkedIn levels. Returns ``(levels, untranslated)``.

    Order is LinkedIn's own, not the input's, and duplicates collapse: three
    titles reading "Senior", "Sr" and "Lead" are one level, not three.
    """
    levels: list[str] = []
    untranslated: list[str] = []
    for word in words or ():
        text = str(word).strip()
        if not text:
            continue
        level = _RANK_WORD_TO_LEVEL.get(text.lower())
        if level is None:
            untranslated.append(text)
        elif level not in levels:
            levels.append(level)
    levels.sort(key=LINKEDIN_SENIORITY_LEVELS.index)
    return levels, untranslated


# ── Field table ──────────────────────────────────────────────────────────────


class _Spec:
    """One pre-fillable configuration field and where its value comes from."""

    def __init__(self, name, label, source_field, targets, hint=""):
        self.name = name
        self.label = label
        self.source_field = source_field
        self.targets = targets
        self.hint = hint


PREFILL_SPECS: tuple[_Spec, ...] = (
    _Spec(
        PREFILL_SEARCH_COUNTRY,
        "Country to search",
        "country",
        ("location_filter.search_country", "sources[].location"),
        hint="Your resume does not clearly state a country. Left blank, sources that "
        "need one are skipped rather than searching somewhere you did not ask for.",
    ),
    _Spec(
        PREFILL_SEARCH_TERMS,
        "Job titles to search for",
        "job_titles",
        ("sources[].search_terms",),
        hint="Your resume does not state any job titles clearly enough to search for. "
        "Add the ones you want, one per line.",
    ),
    _Spec(
        PREFILL_JOB_FUNCTIONS,
        "Job functions",
        "target_job_functions",
        ("role_filter.job_functions",),
        hint="This one is LinkedIn's own list of job functions, and only Claude reads "
        "it from your resume. Add an Anthropic API key to have it filled in, or "
        "type it yourself — leaving it empty searches every function.",
    ),
    _Spec(
        PREFILL_SENIORITY_LEVELS,
        "Seniority levels",
        "seniority_levels",
        ("role_filter.seniority_levels",),
        hint="Your resume does not state a rank that matches one of LinkedIn's six "
        "levels. Leaving it empty searches every level.",
    ),
)

PREFILL_FIELD_NAMES: tuple[str, ...] = tuple(spec.name for spec in PREFILL_SPECS)

#: How each provenance reads to someone who did not write the code.
_ORIGIN_TEXT = {
    derived_profile.PROVENANCE_USER: "you corrected this earlier",
    derived_profile.PROVENANCE_AGENTIC: "read from your resume by Claude",
    derived_profile.PROVENANCE_HEURISTIC: (
        "read from your resume on this machine, without Claude — approximate, so worth a glance"
    ),
    derived_profile.PROVENANCE_ABSENT: "not found in your resume",
}

_EXTRACTION_NOTE = {
    EXTRACTION_AGENTIC: (
        "Claude read your resume. Everything below is editable — change anything that does not look right."
    ),
    EXTRACTION_HEURISTIC: (
        "Your resume was read on this machine, without an API key. That read "
        "approximates, so check each value before you continue — and it is not the "
        "same as what Claude would produce with a key configured."
    ),
    EXTRACTION_NONE: (
        "Nothing could be read from your resume yet, so these start empty. Fill in whichever you want to search by."
    ),
}


def _seniority_value(field) -> tuple[list[str], str]:
    """``role_filter.seniority_levels``, plus a note about anything dropped."""
    raw = field.value if isinstance(field.value, (list, tuple)) else ()
    levels, untranslated = translate_seniority(raw)
    if levels:
        note = ""
        if untranslated:
            note = (
                "Your resume also reads as "
                + ", ".join(f"“{w}”" for w in untranslated)
                + ", which is not one of LinkedIn's six levels, so it was left out "
                "rather than rounded to the nearest one."
            )
        return levels, note
    if untranslated:
        note = (
            "Your resume reads as "
            + ", ".join(f"“{w}”" for w in untranslated)
            + ", which is not one of LinkedIn's six levels. Choosing the nearest one "
            "would narrow every search on a guess, so nothing is pre-filled — pick a "
            "level yourself if you want one."
        )
        return [], note
    return [], ""


def _field_payload(spec: _Spec, profile) -> dict:
    field = profile[spec.source_field]
    provenance = field.provenance
    note = ""

    if spec.name == PREFILL_SENIORITY_LEVELS:
        value, note = _seniority_value(field)
        if not value:
            # Nothing landed in configuration, so the honest provenance is
            # "absent" even when a rank word was read: there is no pre-filled
            # value for the one to describe.
            provenance = derived_profile.PROVENANCE_ABSENT
    elif isinstance(field.value, (list, tuple)):
        value = [str(v) for v in field.value]
    else:
        value = field.value

    if not value and not note:
        note = spec.hint

    return {
        "value": value,
        "provenance": provenance,
        "confidence": field.confidence,
        "method": field.method,
        "origin": _ORIGIN_TEXT.get(provenance, ""),
        "label": spec.label,
        "source_field": spec.source_field,
        "targets": list(spec.targets),
        "note": note,
    }


def _extraction_used(fields: dict) -> str:
    provenances = {payload["provenance"] for payload in fields.values()}
    if derived_profile.PROVENANCE_AGENTIC in provenances:
        return EXTRACTION_AGENTIC
    if derived_profile.PROVENANCE_HEURISTIC in provenances:
        return EXTRACTION_HEURISTIC
    return EXTRACTION_NONE


def build_prefill(config) -> dict:
    """Blocking. What onboarding should pre-fill, and where each value is from.

    Reads the agentic stores from cache only (via
    ``derived_profile.resolve_for_resume``), so this cannot trigger a billed
    Anthropic call and works with no API key at all — which the product spec says
    is exactly when pre-filling matters most.
    """
    resume_path = config.resume.path
    on_file = bool(resume_path) and Path(resume_path).exists()
    profile = derived_profile.resolve_for_resume(resume_path if on_file else "")

    fields = {spec.name: _field_payload(spec, profile) for spec in PREFILL_SPECS}
    extraction = _extraction_used(fields)
    return {
        # Whether a resume is stored, never *where* (the product spec, the
        # rule T45 established). The field payloads are built from the table
        # above, so no store's ``source_path`` has a route in here either.
        "resume_on_file": on_file,
        "api_key_configured": bool(config.anthropic_api_key),
        "extraction": extraction,
        "extraction_note": _EXTRACTION_NOTE[extraction],
        "fields": fields,
    }
