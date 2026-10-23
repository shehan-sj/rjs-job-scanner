"""Heuristic resume extraction — the path that works with no API key.

`jobfinder/resume/profiler.py` and `jobfinder/fillerbot/profiler.py` both ask
Claude to read the resume, so a user with no API key gets nothing derived at
all, which is exactly when sensible pre-filling matters most.
This module is the other path: structural pattern matching over the plain text
`parser.load_resume()` already produces. Standard library only, no network, no
key, nothing sent anywhere.

It approximates, and says so. Per the product spec the heuristic layer is the
*lowest* precedence of three (user edit > agentic > heuristic) and must never
be presented as equivalent to the agentic one.

Absent is not a kind of heuristic
---------------------------------
the product spec's provenance vocabulary is ``user`` / ``agentic`` / ``heuristic``
/ ``absent``. A field this module extracted carries provenance ``heuristic``
and a confidence. A field the resume does not clearly state comes back with an
empty value, provenance ``absent`` and confidence 0.0 — **never** a
plausible-looking guess. A blank the user can fill in is honest; a guessed
country is a silent, invisible skew on every subsequent job search, which is
precisely the failure the product spec exists to undo.

Confidence bands
----------------
Confidence is deliberately coarse: three levels mapped to numbers, not a
fabricated float. A field's number says *how* the value was found, and every
field also carries a human-readable ``method`` saying it in words. A single
constant repeated across every field would be decoration, not a signal.

================  =====  ====================================================
band              value  what earns it
================  =====  ====================================================
``high``          0.9    an unambiguous, explicitly labelled or strictly
                         patterned match: an RFC-shaped e-mail address, a
                         ``Name:`` label, a phone number carrying an
                         international dialling prefix, a country written out
                         in the contact block, job titles anchored to a date
                         range inside a recognised experience section, skills
                         itemised under a skills heading, a seniority rank
                         word stated in one of those titles.
``medium``        0.6    a reliable pattern read without its corroborating
                         structure: a phone number with no country code, a
                         country named in the body rather than the contact
                         block, a headline title with no experience section
                         behind it, a seniority counted from how long the
                         dated history runs rather than stated.
``low``           0.3    positional or indirect inference with nothing
                         corroborating it: a name taken from the first line
                         that merely looks like one, a country deduced from a
                         phone's dialling code alone.
``absent``        0.0    not found. Empty value, provenance ``absent``.
================  =====  ====================================================

Deliberately *not* done here
----------------------------
No seed vocabulary of job titles or skills. The product spec records that the
product is hardcoded to QA jobs in Canada; a keyword list would rebuild that
bias one layer down, working best on the resumes it already over-fits and
returning nothing for a nurse or an accountant. Titles and skills are read
from document structure instead — section headings, date-range anchors, list
punctuation — which is profession-neutral by construction. The only word lists
are cross-profession rank words (``senior``, ``head``, ``intern`` …),
organisation suffixes (``Ltd``, ``GmbH`` …), country names, document-title
headings, and section-heading synonyms. None of them names a profession.

Telling a title from an employer
--------------------------------
An entry's header is however many lines the template puts above the dates, in
whatever order, so "the line above the dates" is the title in some layouts and
the employer in others. ``_choose_title`` reads the whole header run and
*weighs* three signals rather than consulting them in turn:

* the resume's own title vocabulary (worth 2), built before anything is
  decided — see ``_titles_from_experience``;
* a rank word in the line (worth 1), a generic prior;
* an organisation suffix or a place name (worth -2).

Weighing rather than short-circuiting is what stops a rank word *inside an
employer's name* winning outright: "Senior Living Communities of Ohio" is an
eldercare provider, not a senior role, and it beat "Dining Services
Coordinator" while the rank check ran first and returned immediately.
Document-specific evidence outranks a rule of thumb.

The vocabulary itself is derived per resume, never built in — most of it from
the observation that **a person's employers differ from entry to entry while
their job titles repeat**. Across the seven fixtures that alone recovers
"nurse", "engineer", "accountant", "surgeon", "electrician", "chef" and
"mathematics" without this file naming a single profession.

Where nothing distinguishes the lines, the topmost wins, because
title-above-employer is the dominant convention. That tie-break is a guess and
is sometimes wrong; ``test_an_undecidable_entry_prefers_the_topmost_line`` and
``test_a_rank_word_in_an_employer_name_is_a_known_residual`` pin the two cases
so they stay decisions rather than becoming folklore.

Known limits, each of them a deliberate blank rather than a guess:

* A mononym is not recognised as a name (two to four words are required).
* ``+1`` is shared by the US, Canada and twenty other territories, so it
  yields no country at all.
* A country word used as a given name would be a false positive, so the line
  identified as the name is excluded from the country scan.
* Rank words whose meaning inverts between professions are omitted on
  purpose: a *staff* nurse is junior while a *staff* engineer is senior, and a
  school *principal* runs the school while a principal engineer does not.
* A city with no country beside it is not recognised as a place, so in a
  header of the form title / employer / ``Barcelona`` the city is a title
  candidate. It loses to any line with a rank word, which is the common case.
* A one-entry resume that lists the employer above the title, states no rank
  and carries no headline cannot be resolved from the document alone: there
  is one entry, so nothing recurs, and no headline to fall back on.
* An employer whose name contains a rank word ("Senior Living Communities",
  "Principal Financial Group", "Head Start") wins against an uncorroborated
  title in a single-entry resume. Resolving it needs to know that "Ohio" is a
  place; a state gazetteer was considered and rejected, because it would
  demote real titles such as "Washington Bureau Chief". A second entry or a
  headline resolves it.
* Digits are read as a job level only at the end of a title ("Quality
  Engineer 2", "Radiographer Band 6"), so a leading one ("Level 3 Teaching
  Assistant") is not recognised.
* A name written with a trailing full stop ("Maria Fernanda Souza.") is
  indistinguishable from a one-line sentence of body text ("Cleaned
  equipment."), so it comes back ``absent``. The guard chooses which way to be
  wrong deliberately: a sentence in the name field gets typed into
  application forms, while a blank is one the user can fill in.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from .models import ResumeData

__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_NONE",
    "KNOWN_COUNTRIES",
    "PROVENANCE_ABSENT",
    "PROVENANCE_HEURISTIC",
    "REQUIRED_FIELDS",
    "SENIORITY_LEVELS",
    "DerivedField",
    "HeuristicProfile",
    "extract_profile",
    "extract_profile_from_resume",
]

# the product spec's provenance vocabulary. The other two values (`user`,
# `agentic`) are never produced here; they belong to layers above this one.
PROVENANCE_HEURISTIC = "heuristic"
PROVENANCE_ABSENT = "absent"

CONFIDENCE_HIGH = 0.9
CONFIDENCE_MEDIUM = 0.6
CONFIDENCE_LOW = 0.3
CONFIDENCE_NONE = 0.0

REQUIRED_FIELDS = (
    "full_name",
    "email",
    "phone",
    "country",
    "job_titles",
    "skills",
    "seniority",
    "years_experience",
)

SENIORITY_LEVELS = ("intern", "junior", "mid", "senior", "lead", "manager", "executive")


@dataclass(frozen=True)
class DerivedField:
    """One derived value together with where it came from and how sure we are.

    `value` is a string for scalar fields and a tuple of strings for the list
    fields (`job_titles`, `skills`). Tuples rather than lists so a profile
    cannot be mutated in place by whatever reads it.
    """

    value: str | tuple[str, ...]
    provenance: str
    confidence: float
    method: str

    @property
    def found(self) -> bool:
        return bool(self.value)


def _found(value: str | tuple[str, ...], confidence: float, method: str) -> DerivedField:
    if not value:
        return _absent()
    return DerivedField(value=value, provenance=PROVENANCE_HEURISTIC, confidence=confidence, method=method)


def _absent(empty: str | tuple[str, ...] = "") -> DerivedField:
    return DerivedField(value=empty, provenance=PROVENANCE_ABSENT, confidence=CONFIDENCE_NONE, method="")


@dataclass(frozen=True)
class HeuristicProfile:
    """What the no-key path could derive from a resume's text.

    Every attribute is a `DerivedField`, including the ones that were not
    found — a caller must never have to distinguish "missing key" from "no
    value", only read the provenance.
    """

    full_name: DerivedField
    email: DerivedField
    phone: DerivedField
    country: DerivedField
    job_titles: DerivedField
    skills: DerivedField
    seniority: DerivedField
    years_experience: DerivedField

    def fields(self) -> dict[str, DerivedField]:
        return {name: getattr(self, name) for name in REQUIRED_FIELDS}


def _empty_profile() -> HeuristicProfile:
    return HeuristicProfile(
        full_name=_absent(),
        email=_absent(),
        phone=_absent(),
        country=_absent(),
        job_titles=_absent(()),
        skills=_absent(()),
        seniority=_absent(),
        years_experience=_absent(),
    )


# ---------------------------------------------------------------------------
# Readability
# ---------------------------------------------------------------------------

# parser.py's _parse_text reads with errors="replace", so a PDF handed over as
# a .txt comes back as mojibake rather than raising. Recognising that is the
# difference between "empty fields, no exception" and mining binary noise for
# a name.
_JUNK_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_WORD_RE = re.compile(r"[^\W\d_]{2,}")


def _as_text(value: object) -> str:
    """Coerce anything to text. Non-strings are treated as unreadable, not
    decoded: guessing an encoding here would be the same mistake as guessing a
    country."""
    return value if isinstance(value, str) else ""


def _looks_unreadable(text: str) -> bool:
    if not text.strip():
        return True
    junk = sum(1 for ch in text if ch == "�" or (ch not in "\t\n\r" and unicodedata.category(ch) in _JUNK_CATEGORIES))
    if junk / len(text) > 0.03:
        return True
    return len(_WORD_RE.findall(text)) < 5


# ---------------------------------------------------------------------------
# Section structure
# ---------------------------------------------------------------------------

# Heading synonyms, chosen to span professions: a nurse's REGISTRATION and an
# engineer's CERTIFICATIONS are both just section boundaries.
_KNOWN_SECTION_RE = re.compile(
    r"\b(summary|objective|profile|about|experience|employment|work\s+history|career|education|"
    r"skills|competenc\w*|expertise|proficienc\w*|capabilities|certificat\w*|licen[cs]\w*|"
    r"registration|qualificat\w*|awards|publications|references|projects|languages|volunteer|"
    r"training|achievements|interests|affiliations|memberships|background|highlights)\b",
    re.IGNORECASE,
)
_EXPERIENCE_SECTION_RE = re.compile(
    r"\b(experience|employment|work\s+history|career\s+history|positions?\s+held|appointments)\b",
    re.IGNORECASE,
)
_SKILLS_SECTION_RE = re.compile(
    r"\b(skills|competenc\w*|areas\s+of\s+expertise|expertise|proficienc\w*|capabilities)\b",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*[-•·*–—•>]\s+")


def _is_bullet(line: str) -> bool:
    return bool(_BULLET_RE.match(line))


def _is_heading(line: str) -> bool:
    """A heading is a short, unpunctuated, all-capitals or known-name line.

    Detecting headings by capitalisation rather than by a fixed list is what
    keeps the extractor working on a resume whose author invented their own
    section names.
    """
    stripped = line.strip().rstrip(":")
    if not stripped or len(stripped) > 60 or stripped.endswith("."):
        return False
    if not stripped[0].isalpha():
        return False
    letters = [ch for ch in stripped if ch.isalpha()]
    if len(letters) < 3:
        return False
    if all(ch.isupper() for ch in letters):
        return True
    return bool(_KNOWN_SECTION_RE.fullmatch(stripped))


@dataclass(frozen=True)
class _Section:
    kind: str  # "experience" | "skills" | "other" | "preamble"
    heading: str
    body: tuple[str, ...]


def _classify(heading: str) -> str:
    if _EXPERIENCE_SECTION_RE.search(heading):
        return "experience"
    if _SKILLS_SECTION_RE.search(heading):
        return "skills"
    return "other"


def _split_sections(lines: list[str]) -> list[_Section]:
    sections: list[_Section] = []
    kind, heading, body = "preamble", "", []
    for line in lines:
        if _is_heading(line):
            sections.append(_Section(kind=kind, heading=heading, body=tuple(body)))
            heading = line.strip().rstrip(":")
            kind = _classify(heading)
            body = []
        else:
            body.append(line)
    sections.append(_Section(kind=kind, heading=heading, body=tuple(body)))
    return sections


def _contact_block(lines: list[str]) -> list[str]:
    """The lines before the first recognised section — where contact details
    live. Capped so a resume with no headings does not make the whole document
    count as its contact block."""
    for index, line in enumerate(lines[:20]):
        if _is_heading(line) and _KNOWN_SECTION_RE.search(line.strip().rstrip(":")):
            return lines[:index]
    return lines[:12]


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------

_LABELLED_NAME_RE = re.compile(r"^\s*(?:full\s+)?name\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE)
# A name word is letters, optionally joined by an apostrophe, hyphen or dot:
# O'Brien, Okonkwo-Weber, St.John. Written as an alternation rather than one
# negated class because `[^\W\d_]` cannot have characters added back into it —
# `-` is itself `\W`, so listing it inside the negation excludes it.
_NAME_WORD_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|['’.\-])*")


# Titles of the document, not names of the person. "CURRICULUM VITAE" is two
# alphabetic words in title case, so the shape test alone accepts it and the
# user ends up named after their own file. Returning it would break the rule
# this module is built on: a blank is honest, a plausible wrong value is not.
# Profession-neutral, so it does not touch the anti-bias constraint.
_DOCUMENT_HEADING_RE = re.compile(
    r"(?:curriculum\s+vitae|resum[eé]|r[eé]sum[eé]|c\.?\s?v\.?|personal\s+(?:details|information|particulars)|"
    r"contact\s+(?:details|information)|bio\s?data|job\s+application|application\s+form|"
    r"candidate\s+profile)",
    re.IGNORECASE,
)


# A full stop closing a word of three or more letters: the end of a sentence.
# A short resume bullet stripped of its bullet ("Cleaned equipment.") is two
# to four alphabetic words, so the shape test alone accepts it as a name —
# and this is the field FillerBot types into application forms, which makes a
# plausible-looking guess here worse than a blank.
#
# Three letters is the threshold that keeps real names: a trailing initial
# ("Jane A."), a generational suffix ("Jane Smith Jr.") and an abbreviated
# particle ("St. John Smith") are all one or two letters before the stop.
#
# A leading honorific is stripped before the check rather than left to that
# threshold, because "Prof.", "Mrs." and "Rev." are over it and dropping the
# name of anyone who writes their title would be a worse trade than the bug
# this guard fixes.
_HONORIFIC_PREFIX_RE = re.compile(
    r"^(?:mr|mrs|ms|mx|miss|dr|prof|professor|rev|hon|sir|dame|st|fr|br|capt|col|sgt)\.?\s+",
    re.IGNORECASE,
)
_SENTENCE_STOP_RE = re.compile(r"[^\W\d_]{3,}\.(?:\s|$)")


def _looks_like_person_name(candidate: str) -> bool:
    text = candidate.strip().strip(",")
    if not 2 <= len(text) <= 60:
        return False
    if _DOCUMENT_HEADING_RE.fullmatch(text):
        return False
    if _SENTENCE_STOP_RE.search(_HONORIFIC_PREFIX_RE.sub("", text)):
        return False
    if any(ch.isdigit() for ch in text) or any(ch in text for ch in "@:|/\\()"):
        return False
    words = text.split()
    if not 2 <= len(words) <= 4:
        return False
    return all(_NAME_WORD_RE.fullmatch(word) for word in words)


def _extract_name(lines: list[str]) -> tuple[DerivedField, int | None]:
    """Return the name field and the index of the line it came from.

    The index matters: that line is excluded from the country scan, because
    Chad, Jordan and Georgia are given names as well as countries.
    """
    for index, line in enumerate(lines[:15]):
        match = _LABELLED_NAME_RE.match(line)
        if match and _looks_like_person_name(match.group(1)):
            return _found(match.group(1).strip(), CONFIDENCE_HIGH, "stated on a labelled 'Name:' line"), index

    seen = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        seen += 1
        if seen > 8:
            break
        if _is_heading(line) and _KNOWN_SECTION_RE.search(line.strip().rstrip(":")):
            break
        if _looks_like_person_name(line):
            return _found(line.strip().strip(","), CONFIDENCE_LOW, "first line that reads like a person's name"), index
    return _absent(), None


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~\-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~\-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*"
    r"\.[A-Za-z]{2,}"
)


def _extract_email(text: str) -> DerivedField:
    match = _EMAIL_RE.search(text)
    if not match:
        return _absent()
    return _found(match.group(0), CONFIDENCE_HIGH, "matched a strict RFC-shaped address pattern")


# ---------------------------------------------------------------------------
# Phone (and the country it may or may not imply)
# ---------------------------------------------------------------------------

_PHONE_INTERNATIONAL_RE = re.compile(r"\+\s?\d[\d\s.\-()]{6,18}\d")
# A bare local number only counts in a recognisable grouping. Loosening this
# is how year ranges and postcodes start being read as phone numbers.
_PHONE_LOCAL_RE = re.compile(r"(?<![\d+])(?:\(\d{3}\)\s*\d{3}[\s.\-]\d{4}|\d{3}[\s.\-]\d{3}[\s.\-]\d{4})(?!\d)")

# Calling code -> country. Codes shared by several countries are deliberately
# absent: +1 alone covers the US, Canada and twenty more territories, and
# picking one would be a coin flip presented as data.
_CALLING_CODES = {
    "20": "Egypt",
    "27": "South Africa",
    "30": "Greece",
    "31": "Netherlands",
    "32": "Belgium",
    "33": "France",
    "34": "Spain",
    "36": "Hungary",
    "39": "Italy",
    "40": "Romania",
    "41": "Switzerland",
    "43": "Austria",
    "44": "United Kingdom",
    "45": "Denmark",
    "46": "Sweden",
    "47": "Norway",
    "48": "Poland",
    "49": "Germany",
    "51": "Peru",
    "52": "Mexico",
    "54": "Argentina",
    "55": "Brazil",
    "56": "Chile",
    "57": "Colombia",
    "60": "Malaysia",
    "61": "Australia",
    "62": "Indonesia",
    "63": "Philippines",
    "64": "New Zealand",
    "65": "Singapore",
    "66": "Thailand",
    "81": "Japan",
    "82": "South Korea",
    "84": "Vietnam",
    "86": "China",
    "90": "Turkey",
    "91": "India",
    "92": "Pakistan",
    "94": "Sri Lanka",
    "98": "Iran",
    "212": "Morocco",
    "213": "Algeria",
    "216": "Tunisia",
    "233": "Ghana",
    "234": "Nigeria",
    "251": "Ethiopia",
    "254": "Kenya",
    "255": "Tanzania",
    "256": "Uganda",
    "260": "Zambia",
    "263": "Zimbabwe",
    "351": "Portugal",
    "352": "Luxembourg",
    "353": "Ireland",
    "354": "Iceland",
    "358": "Finland",
    "359": "Bulgaria",
    "370": "Lithuania",
    "371": "Latvia",
    "372": "Estonia",
    "380": "Ukraine",
    "385": "Croatia",
    "386": "Slovenia",
    "420": "Czechia",
    "421": "Slovakia",
    "880": "Bangladesh",
    "886": "Taiwan",
    "961": "Lebanon",
    "962": "Jordan",
    "964": "Iraq",
    "965": "Kuwait",
    "966": "Saudi Arabia",
    "968": "Oman",
    "971": "United Arab Emirates",
    "972": "Israel",
    "974": "Qatar",
    "977": "Nepal",
}


def _extract_phone(text: str) -> tuple[DerivedField, str]:
    """Return the phone field and the country its dialling code implies ("" if
    the code is ambiguous, unknown or absent)."""
    for match in _PHONE_INTERNATIONAL_RE.finditer(text):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if not 8 <= len(digits) <= 15:
            continue
        country = ""
        for length in (3, 2, 1):
            country = _CALLING_CODES.get(digits[:length], "")
            if country:
                break
        return _found(raw, CONFIDENCE_HIGH, "carries an international dialling prefix"), country

    match = _PHONE_LOCAL_RE.search(text)
    if match:
        return _found(match.group(0).strip(), CONFIDENCE_MEDIUM, "local number grouping, no country code"), ""
    return _absent(), ""


# ---------------------------------------------------------------------------
# Country
# ---------------------------------------------------------------------------

# Alias -> canonical name. No country is the default and none is privileged:
# a resume that states none gets none (see the product spec — the product's QA-in-
# Canada bias is exactly what a baked-in default looks like from the inside).
#
# `jobfinder/matcher/location_parser.py` has a table of the same name whose 32
# canonical names are a strict subset of these 84. The two are deliberately
# not shared yet, for one behavioural reason and one scope reason:
#
#  * It accepts bare "us", "u.s." and "america" as the United States, which is
#    right for a job posting's location field ("Remote — US") and wrong here:
#    a resume is prose, and "us" is a pronoun in prose far more often than it
#    is a country.
#  * Merging them means editing the matcher, whose behaviour is pinned by
#    T4's golden scoring fixtures and due to be rewritten by T51.
#
# If you are unifying them, make the shared table the superset and keep the
# "bare abbreviation" question a per-caller argument rather than flattening
# the two answers into one.
_COUNTRY_ALIASES: dict[str, str] = {
    "united states of america": "United States",
    "united states": "United States",
    "canada": "Canada",
    "mexico": "Mexico",
    "brazil": "Brazil",
    "argentina": "Argentina",
    "chile": "Chile",
    "colombia": "Colombia",
    "peru": "Peru",
    "uruguay": "Uruguay",
    "costa rica": "Costa Rica",
    "panama": "Panama",
    "united kingdom": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
    "ireland": "Ireland",
    "france": "France",
    "germany": "Germany",
    "spain": "Spain",
    "portugal": "Portugal",
    "italy": "Italy",
    "netherlands": "Netherlands",
    "holland": "Netherlands",
    "belgium": "Belgium",
    "luxembourg": "Luxembourg",
    "switzerland": "Switzerland",
    "austria": "Austria",
    "denmark": "Denmark",
    "sweden": "Sweden",
    "norway": "Norway",
    "finland": "Finland",
    "iceland": "Iceland",
    "poland": "Poland",
    "czechia": "Czechia",
    "czech republic": "Czechia",
    "slovakia": "Slovakia",
    "hungary": "Hungary",
    "romania": "Romania",
    "bulgaria": "Bulgaria",
    "greece": "Greece",
    "croatia": "Croatia",
    "slovenia": "Slovenia",
    "serbia": "Serbia",
    "ukraine": "Ukraine",
    "lithuania": "Lithuania",
    "latvia": "Latvia",
    "estonia": "Estonia",
    "turkey": "Turkey",
    "israel": "Israel",
    "lebanon": "Lebanon",
    "jordan": "Jordan",
    "iraq": "Iraq",
    "iran": "Iran",
    "kuwait": "Kuwait",
    "qatar": "Qatar",
    "oman": "Oman",
    "saudi arabia": "Saudi Arabia",
    "united arab emirates": "United Arab Emirates",
    "egypt": "Egypt",
    "morocco": "Morocco",
    "algeria": "Algeria",
    "tunisia": "Tunisia",
    "ghana": "Ghana",
    "nigeria": "Nigeria",
    "kenya": "Kenya",
    "tanzania": "Tanzania",
    "uganda": "Uganda",
    "ethiopia": "Ethiopia",
    "zambia": "Zambia",
    "zimbabwe": "Zimbabwe",
    "south africa": "South Africa",
    "india": "India",
    "pakistan": "Pakistan",
    "bangladesh": "Bangladesh",
    "sri lanka": "Sri Lanka",
    "nepal": "Nepal",
    "china": "China",
    "taiwan": "Taiwan",
    "hong kong": "Hong Kong",
    "japan": "Japan",
    "south korea": "South Korea",
    "republic of korea": "South Korea",
    "vietnam": "Vietnam",
    "thailand": "Thailand",
    "malaysia": "Malaysia",
    "singapore": "Singapore",
    "indonesia": "Indonesia",
    "philippines": "Philippines",
    "australia": "Australia",
    # Longest alias wins, so this stops an Australian licence issued in "New
    # South Wales" from being read as Wales, i.e. the United Kingdom.
    "new south wales": "Australia",
    "new zealand": "New Zealand",
}

KNOWN_COUNTRIES = frozenset(_COUNTRY_ALIASES.values())

_COUNTRY_RE = re.compile(
    r"\b(" + "|".join(re.escape(alias) for alias in sorted(_COUNTRY_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
# Abbreviations are matched case-sensitively — "us" is a pronoun far more often
# than it is a country, so bare "US" is not accepted at all.
_COUNTRY_ABBREVIATIONS = (
    (re.compile(r"\bU\.?S\.?A\.?(?![A-Za-z])"), "United States"),
    (re.compile(r"\bU\.?K\.?(?![A-Za-z])"), "United Kingdom"),
    (re.compile(r"\bUAE\b"), "United Arab Emirates"),
)


_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)


def _strip_addresses(line: str) -> str:
    """Remove e-mail addresses and URLs before scanning for a country.

    A dot is a word boundary, so `jordan.vance@example.com` otherwise reads as
    the country Jordan, and `chadwick.io` as Chad. The same trap in reverse to
    the one that makes `_extract_name`'s line exclusion necessary: country
    names and personal names share a lot of words.
    """
    return _URL_RE.sub(" ", _EMAIL_RE.sub(" ", line))


def _country_in(raw_lines: list[str]) -> str:
    """Longest alias wins, so "Northern Ireland" is not read as "Ireland"."""
    best: tuple[int, str] = (0, "")
    for line in (_strip_addresses(raw) for raw in raw_lines):
        for match in _COUNTRY_RE.finditer(line):
            alias = match.group(1)
            if len(alias) > best[0]:
                best = (len(alias), _COUNTRY_ALIASES[alias.lower()])
        for pattern, canonical in _COUNTRY_ABBREVIATIONS:
            if pattern.search(line) and not best[1]:
                best = (len(canonical), canonical)
    return best[1]


def _extract_country(lines: list[str], name_line: int | None, phone_country: str) -> DerivedField:
    scannable = [line for index, line in enumerate(lines) if index != name_line]
    contact = [line for index, line in enumerate(_contact_block(lines)) if index != name_line]

    stated = _country_in(contact)
    if stated:
        return _found(stated, CONFIDENCE_HIGH, "written out in the contact block")

    stated = _country_in(scannable)
    if stated:
        return _found(stated, CONFIDENCE_MEDIUM, "named in the body, not the contact block")

    if phone_country:
        return _found(phone_country, CONFIDENCE_LOW, "inferred from the phone number's dialling code alone")
    return _absent()


# ---------------------------------------------------------------------------
# Rank words
# ---------------------------------------------------------------------------

# Rank words that mean roughly the same thing whatever the profession. Words
# whose meaning inverts between professions are omitted on purpose: a staff
# nurse is junior and a staff engineer is senior; a school principal runs the
# school and a principal engineer does not. Guessing on those would be worse
# than the honest fallback of counting years.
#
# Used twice, which is why this sits above both callers: to grade seniority,
# and to tell a job title apart from an employer when an entry's layout gives
# no other clue. "Head Chef" holds a rank word; "Restaurant Les Glicines"
# cannot. That is a profession-neutral discriminator — it knows nothing about
# cooking, nursing or accountancy, only about rank.
_RANK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("executive", re.compile(r"\b(chief|ceo|cto|cfo|coo|vice[\s\-]?president|vp|president|executive)\b", re.I)),
    ("manager", re.compile(r"\b(manager|managing|head|director|superintendent)\b", re.I)),
    ("lead", re.compile(r"\b(lead|leader|supervisor|foreman|forewoman|in\s+charge)\b", re.I)),
    ("senior", re.compile(r"\b(senior|snr|sr)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jnr|jr|assistant|trainee|graduate)\b", re.I)),
    ("intern", re.compile(r"\b(intern|interne?ship|apprentice)\b", re.I)),
)


def _has_rank_word(text: str) -> bool:
    return any(pattern.search(text) for _level, pattern in _RANK_PATTERNS)


# ---------------------------------------------------------------------------
# Job titles
# ---------------------------------------------------------------------------

_MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_DATE_POINT = r"(?:(?:" + _MONTHS + r")[a-z]*\.?\s+)?(?:19|20)\d{2}"
_DATE_NOW = r"present|current|now|to\s+date|ongoing|date"
_DATE_RANGE_RE = re.compile(
    r"(" + _DATE_POINT + r")\s*(?:-{1,2}|–|—|to|until|through)\s*(" + _DATE_POINT + r"|" + _DATE_NOW + r")",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")

# Where a title ends and an employer begins. Ordered: the dash forms are tried
# before the comma so "Staff Nurse — Mercy Regional Hospital, Cork" splits at
# the dash rather than inside the employer's own address.
_TITLE_SEPARATORS = (
    " — ",
    " – ",
    " - ",
    " | ",
    " @ ",
    " at ",
    " / ",
    "—",
    "–",
    ",",
)


def _title_segment(line: str) -> str:
    text = line.strip()
    for separator in _TITLE_SEPARATORS:
        if separator in text:
            return text.split(separator, 1)[0].strip()
    return text


# A level on the end of a title: "Quality Engineer 2", "Technician 1",
# "Radiographer Band 6", "Engineer II". Numbered ladders are not a software
# habit — NHS bands, civil-service grades and trade certifications all number
# the role — so a blanket ban on digits discarded the title and left the
# employer in its place. One or two digits only, which is what keeps a
# postcode ("Pune 411006") and a street number out.
_TRAILING_LEVEL_RE = re.compile(r"\s+(?:[0-9]{1,2}|[IVXL]{1,4})$")


def _plausible_title(line: str) -> str | None:
    segment = _title_segment(line).strip(" \t|,;:-–—•·")
    if not segment or len(segment) > 70 or segment.endswith("."):
        return None
    if not segment[0].isalpha():
        return None
    if "@" in segment or "http" in segment.lower() or ":" in segment:
        return None
    # Digits are allowed only as a trailing level. A four-digit year is never
    # one, which is the original point of this rule: keep date lines out.
    if _YEAR_RE.search(segment) or any(ch.isdigit() for ch in _TRAILING_LEVEL_RE.sub("", segment)):
        return None
    words = segment.split()
    if not 1 <= len(words) <= 8:
        return None
    if not any(word[0].isupper() for word in words):
        return None
    if _KNOWN_SECTION_RE.fullmatch(segment) or _DOCUMENT_HEADING_RE.fullmatch(segment):
        return None
    return segment


def _neighbour_title(body: tuple[str, ...], index: int, step: int) -> str | None:
    cursor = index + step
    while 0 <= cursor < len(body):
        line = body[cursor]
        if line.strip() and not _is_bullet(line) and not _is_heading(line):
            return _plausible_title(line)
        if line.strip():
            return None
        cursor += step
    return None


# An entry's header is however many lines the template puts above the dates:
# one ("Head Chef - Les Glicines"), two (title and employer on their own
# lines, in either order) or three (title, employer, city). Taking only the
# line adjacent to the dates reads the employer as the title on every layout
# that puts the employer last, which is most of them.
_MAX_ENTRY_HEADER_LINES = 4

# Organisation words, anchored to the *end* of the line, which is where legal
# and structural suffixes live. Anchoring matters: "Restaurant Manager" is a
# job title that opens with a venue word, while "Smith & Co" is an employer
# that closes with a legal one. Not a vocabulary of professions — it says
# nothing about what the person does, only that the line names a body
# corporate.
_ORG_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|ltd|limited|llc|llp|lp|plc|gmbh|ag|bv|nv|sa|sas|srl|spa|ab|oy|aps|pty|pte|"
    r"corp|corporation|co|company|group|holdings|partners|associates|consultants|consulting|"
    r"services|solutions|systems|technologies|industries|enterprises|ventures|works)\b\.?$",
    re.IGNORECASE,
)
# "Barcelona, ES" / "Austin, TX" / "Toronto, ON". Restricted to a two-letter
# uppercase tail so it cannot swallow "Management Accountant, Laurentide Food
# Group", whose tail is also comma-separated title case.
_STATE_CODE_TAIL_RE = re.compile(r",\s*[A-Z]{2}\.?\s*$")

# Words too generic to prove two lines describe the same kind of work.
_TITLE_STOPWORDS = frozenset(
    {"of", "the", "and", "for", "in", "at", "to", "de", "del", "la", "le", "les", "du", "des", "van", "von", "der"}
)


def _looks_like_employer_or_place(raw_line: str) -> bool:
    """True when a line names an organisation or a location rather than a job.

    Only ever used to *demote* a candidate, never to promote one: a false
    negative here costs nothing, while a false positive could discard a real
    title, so every rule is deliberately narrow.
    """
    text = raw_line.strip().rstrip(".")
    if _ORG_SUFFIX_RE.search(text):
        return True
    if _STATE_CODE_TAIL_RE.search(text):
        return True
    return bool(_country_in([text]))


def _significant_words(text: str) -> set[str]:
    """The words of a title that say what the work *is*.

    Rank words are stripped out because they are shared across professions:
    keeping "senior" would make "Senior Electrician" corroborate "Senior
    Veterinary Surgeon".
    """
    words = set()
    for word in re.findall(r"[^\W\d_]+", text.lower()):
        if len(word) < 3 or word in _TITLE_STOPWORDS or _has_rank_word(word):
            continue
        words.add(word)
    return words


def _entry_header(body: tuple[str, ...], index: int) -> list[str]:
    """The contiguous run of header lines directly above a dates line.

    Stops at a blank line, a bullet, a heading, or another entry's date range
    — the four things that mark the end of the previous entry — so the run
    never reaches back into one entry from another. The date range matters
    when entries are separated by nothing at all: without it the run walks
    over the previous entry's dates and collects its employer too.
    """
    run: list[str] = []
    cursor = index - 1
    while cursor >= 0 and len(run) < _MAX_ENTRY_HEADER_LINES:
        line = body[cursor]
        if not line.strip() or _is_bullet(line) or _is_heading(line):
            break
        if _DATE_RANGE_RE.search(line):
            break
        run.append(line)
        cursor -= 1
    run.reverse()  # document order, topmost first
    return run


def _recurring_words(headers: list[list[str]]) -> set[str]:
    """Words appearing in the headers of two or more different entries.

    Employers differ from entry to entry; job titles repeat. So a word that
    recurs across entries is a title word — "chef" across three restaurants,
    "coordinator" across two care homes. Counted once per entry, so a word
    repeated inside a single header does not vouch for itself.
    """
    entry_counts: dict[str, int] = {}
    for header in headers:
        words: set[str] = set()
        for raw in header:
            title = _plausible_title(raw)
            if title:
                words |= _significant_words(title)
        for word in words:
            entry_counts[word] = entry_counts.get(word, 0) + 1
    return {word for word, count in entry_counts.items() if count >= 2}


def _title_evidence(raw: str, title: str, lexicon: set[str]) -> int:
    """How much this line looks like the entry's job title.

    The three signals are weighed rather than consulted in turn. Consulting
    them in turn let a rank word *inside an employer's name* win outright:
    "Senior Living Communities of Ohio" is an eldercare provider, and it beat
    "Dining Services Coordinator" because the rank check ran first and
    returned immediately. Weighing them puts the resume's own vocabulary above
    a generic prior, which is the right order — evidence drawn from this
    document beats a rule of thumb about job titles in general.
    """
    score = 0
    if _significant_words(title) & lexicon:
        score += 2  # this resume already used one of these words in a title
    if _has_rank_word(title):
        score += 1  # a generic prior: real titles state rank, employers rarely
    if _looks_like_employer_or_place(raw):
        score -= 2  # names a body corporate or a location
    return score


def _choose_title(header: list[str], lexicon: set[str]) -> tuple[str | None, bool]:
    """Pick the job title out of an entry's header lines.

    Returns `(title, from_evidence)`. `from_evidence` is False when the pick
    was positional guesswork, which lets the caller come back to it once the
    rest of the resume has been read.

    Ties go to the topmost line — title-above-employer is the dominant
    convention — which the `-position` term in the sort key expresses.
    """
    candidates = [(raw, _plausible_title(raw)) for raw in header]
    candidates = [(raw, title) for raw, title in candidates if title]
    if not candidates:
        return None, False
    if len(candidates) == 1:
        return candidates[0][1], True

    scored = [
        (_title_evidence(raw, title, lexicon), -position, title) for position, (raw, title) in enumerate(candidates)
    ]
    best_score, _position, best_title = max(scored)

    # A positive score means something in the document pointed here. A zero or
    # negative one means the pick was the topmost line and nothing more, which
    # the caller may want to revisit once the whole resume has been read.
    return best_title, best_score > 0


def _titles_from_experience(sections: list[_Section], headline: str | None) -> list[str]:
    """Titles anchored to a date range, which is what makes this structural
    rather than a keyword list: an entry's dates mark where an entry starts,
    whatever profession it belongs to.

    The vocabulary used to recognise a title line is built up *before* any
    ambiguous entry is decided, from three sources that are all safe to trust:
    the headline under the name, the entries whose header has only one possible
    reading, and words that recur across entries.

    That last source carries most of the weight, and it is the asymmetry this
    whole function turns on: **a person's employers differ from entry to
    entry, while their job titles repeat.** So a word appearing in the headers
    of two different entries is a title word. It resolves "Dining Services
    Coordinator" against "Senior Living Communities of Ohio" — an eldercare
    employer whose name contains a rank word — because "dining" and
    "coordinator" also turn up in the next entry while "living" and "ohio" do
    not. Deriving it from the document rather than from a built-in list is
    what keeps this profession-neutral.

    Building the lexicon first also matters for correctness, not just order:
    seeding it from a pick that was itself a guess would let an employer's own
    words vouch for that employer on a second look.
    """
    headers: list[list[str]] = []
    resolved: list[str] = []

    for section in sections:
        if section.kind != "experience":
            continue
        for index, raw in enumerate(section.body):
            line = raw.strip()
            if not line or _is_bullet(raw):
                continue
            match = _DATE_RANGE_RE.search(line)
            if not match:
                continue
            remainder = (line[: match.start()] + " " + line[match.end() :]).strip()
            same_line = _plausible_title(remainder) if remainder else None
            if same_line:
                headers.append([])
                resolved.append(same_line)
                continue
            header = _entry_header(section.body, index)
            if header:
                headers.append(header)
                resolved.append("")
            else:
                # Dates first, header below: "2019 - Present" / "Head Chef".
                headers.append([])
                resolved.append(_neighbour_title(section.body, index, 1) or "")

    # Source 1: the headline. Source 2: entries that read only one way.
    lexicon = _significant_words(headline) if headline else set()
    for title in resolved:
        if title:
            lexicon |= _significant_words(title)

    # Source 3: words recurring across entry headers.
    lexicon |= _recurring_words(headers)

    for position, title in enumerate(resolved):
        if title:
            continue
        chosen, from_evidence = _choose_title(headers[position], lexicon)
        if chosen and from_evidence:
            resolved[position] = chosen
            lexicon |= _significant_words(chosen)

    for position, title in enumerate(resolved):
        if title:
            continue
        chosen, _ = _choose_title(headers[position], lexicon)
        if chosen:
            resolved[position] = chosen

    return [title for title in resolved if title]


def _is_bare_heading_word(line: str) -> bool:
    """A single word set in capitals, standing alone: a section heading.

    `_KNOWN_SECTION_RE` only knows the headings people commonly use, so an
    unusual one ("DUTIES", "RESPONSIBILITIES") is not recognised as a section
    and was reaching `job_titles` through the headline fallback. One word in
    capitals with nothing corroborating it is a heading, and `absent` is the
    honest answer.

    Scoped to a *single* word so a resume that sets its headline in capitals
    ("REGISTERED NURSE") keeps it.
    """
    stripped = line.strip().rstrip(":")
    if not stripped or " " in stripped:
        return False
    letters = [ch for ch in stripped if ch.isalpha()]
    return len(letters) >= 3 and all(ch.isupper() for ch in letters)


def _headline_title(lines: list[str], name: DerivedField) -> str | None:
    """The standalone line under the name, e.g. "Registered Nurse".

    Serves twice: as the fallback when a resume has no recognisable experience
    section, and as the seed for `_titles_from_experience`'s lexicon, since it
    is usually the current job title.

    Scans the contact block rather than the "preamble" section, because a name
    set in capitals — or a "CURRICULUM VITAE" above it — parses as a section
    heading and leaves the preamble empty. That emptied the headline for a
    large share of real resumes, and silently: the lexicon simply never got
    seeded, so entries that needed it fell back to guessing by position.

    The name's own line is excluded by value. It deliberately does *not* also
    reject name-*shaped* lines: "Pastry Chef" and "Veterinary Surgeon" are two
    capitalised words each, indistinguishable in shape from a person's name,
    so such a test silently discarded most real headlines.
    """
    seen = 0
    for raw in _contact_block(lines):
        line = raw.strip()
        if not line or line == name.value or _is_bullet(raw):
            continue
        seen += 1
        if seen > 6:
            break
        if _looks_like_employer_or_place(line) or _is_bare_heading_word(line):
            continue
        title = _plausible_title(line)
        if title:
            return title
    return None


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            ordered.append(value)
    return tuple(ordered)


def _extract_titles(lines: list[str], sections: list[_Section], name: DerivedField) -> DerivedField:
    # The headline is read first even when an experience section exists: it is
    # usually the current job title, which makes it the best available seed
    # for recognising the title lines inside the entries.
    headline = _headline_title(lines, name)
    titles = _dedupe(_titles_from_experience(sections, headline))
    if titles:
        return _found(titles, CONFIDENCE_HIGH, "entries in a recognised experience section, anchored to date ranges")
    if headline:
        return _found((headline,), CONFIDENCE_MEDIUM, "headline line under the name, with no experience section")
    return _absent(())


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

_SKILL_SPLIT_RE = re.compile(r"[,;|•·\n]|\s+/\s+|\s{3,}")
_MAX_SKILLS = 40


def _plausible_skill(candidate: str) -> str | None:
    text = candidate.strip().strip("-–—.:•· ")
    if not 2 <= len(text) <= 45:
        return None
    if not any(ch.isalpha() for ch in text):
        return None
    if "@" in text or "http" in text.lower():
        return None
    if len(text.split()) > 6:
        return None
    return text


def _extract_skills(sections: list[_Section]) -> DerivedField:
    """Only ever read from an explicit skills section.

    A document-wide keyword sweep is the other obvious implementation and it is
    the one that reintroduces the product spec's bias: it needs a vocabulary to
    sweep for, and whoever maintains that vocabulary decides which professions
    have skills.
    """
    collected: list[str] = []
    for section in sections:
        if section.kind != "skills":
            continue
        for piece in _SKILL_SPLIT_RE.split("\n".join(section.body)):
            skill = _plausible_skill(piece)
            if skill:
                collected.append(skill)
    if not collected:
        return _absent(())
    return _found(_dedupe(collected)[:_MAX_SKILLS], CONFIDENCE_HIGH, "itemised under a skills heading")


# ---------------------------------------------------------------------------
# Seniority
# ---------------------------------------------------------------------------


def _seniority_from_titles(titles: tuple[str, ...]) -> str:
    joined = " | ".join(titles)
    for level, pattern in _RANK_PATTERNS:
        if pattern.search(joined):
            return level
    return ""


def _years_from_experience(sections: list[_Section]) -> int:
    this_year = date.today().year
    starts: list[int] = []
    ends: list[int] = []
    for section in sections:
        if section.kind != "experience":
            continue
        for line in section.body:
            for match in _DATE_RANGE_RE.finditer(line):
                start_year = _YEAR_RE.search(match.group(1))
                if not start_year:
                    continue
                starts.append(int(start_year.group(0)))
                end_year = _YEAR_RE.search(match.group(2))
                ends.append(int(end_year.group(0)) if end_year else this_year)
    if not starts:
        return 0
    span = max(ends) - min(starts)
    return span if 0 < span <= 60 else 0


def _extract_seniority(titles: DerivedField, years: int) -> tuple[DerivedField, DerivedField]:
    years_field = (
        _found(str(years), CONFIDENCE_MEDIUM, "span between the earliest and latest dates in the experience section")
        if years
        else _absent()
    )

    stated = _seniority_from_titles(titles.value if isinstance(titles.value, tuple) else ())
    if stated:
        return _found(stated, CONFIDENCE_HIGH, "rank word stated in an extracted job title"), years_field

    if years:
        # Bands stop at "senior": nothing about a long tenure says "manager",
        # and claiming it would be a guess dressed as a derivation.
        level = "junior" if years < 2 else "mid" if years < 6 else "senior"
        return _found(level, CONFIDENCE_MEDIUM, "counted from the length of the dated work history"), years_field

    return _absent(), years_field


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def extract_profile(text: object) -> HeuristicProfile:
    """Derive what can be derived from a resume's plain text.

    Never raises and never returns a partial object: unreadable, empty and
    non-text input all produce a profile whose every field is `absent` with
    confidence 0.0. Pair with `jobfinder.resume.parser.load_resume()`, which
    already turns PDF and .txt into text, or use
    `extract_profile_from_resume()`.
    """
    body = _as_text(text)
    if _looks_unreadable(body):
        return _empty_profile()

    lines = body.splitlines()
    sections = _split_sections(lines)

    name, name_line = _extract_name(lines)
    email = _extract_email(body)
    phone, phone_country = _extract_phone(body)
    country = _extract_country(lines, name_line, phone_country)
    titles = _extract_titles(lines, sections, name)
    skills = _extract_skills(sections)
    seniority, years = _extract_seniority(titles, _years_from_experience(sections))

    return HeuristicProfile(
        full_name=name,
        email=email,
        phone=phone,
        country=country,
        job_titles=titles,
        skills=skills,
        seniority=seniority,
        years_experience=years,
    )


def extract_profile_from_resume(resume: ResumeData) -> HeuristicProfile:
    """`extract_profile()` over an already-parsed resume."""
    return extract_profile(getattr(resume, "text", None))
