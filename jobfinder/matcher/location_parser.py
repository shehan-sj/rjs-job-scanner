"""Offline location and employment-type parsing.

Used by every job source to normalise the noisy free-text ``location`` string
into structured ``(country, region, city)`` fields, and to detect
``employment_type`` and ``work_arrangement`` from title/description text.
All heuristics are pure-Python, zero-dependency, and safe to run offline.
"""

from __future__ import annotations

import re

# ── Country aliases (lowercased key → canonical form) ──────────────────────────

# Country aliases — intentionally avoids 2-letter codes that collide with US
# state / Canadian province abbreviations (e.g. "CA" = California, not Canada).
_COUNTRY_ALIASES: dict[str, str] = {
    "united states": "United States",
    "united states of america": "United States",
    "usa": "United States",
    "u.s.a.": "United States",
    "u.s.a": "United States",
    "us": "United States",
    "u.s.": "United States",
    "america": "United States",
    "canada": "Canada",
    "united kingdom": "United Kingdom",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "germany": "Germany",
    "deutschland": "Germany",
    "france": "France",
    "ireland": "Ireland",
    "netherlands": "Netherlands",
    "holland": "Netherlands",
    "spain": "Spain",
    "italy": "Italy",
    "portugal": "Portugal",
    "poland": "Poland",
    "sweden": "Sweden",
    "norway": "Norway",
    "finland": "Finland",
    "denmark": "Denmark",
    "switzerland": "Switzerland",
    "austria": "Austria",
    "belgium": "Belgium",
    "india": "India",
    "australia": "Australia",
    "new zealand": "New Zealand",
    "singapore": "Singapore",
    "hong kong": "Hong Kong",
    "japan": "Japan",
    "south korea": "South Korea",
    "china": "China",
    "mexico": "Mexico",
    "brazil": "Brazil",
    "argentina": "Argentina",
    "united arab emirates": "United Arab Emirates",
    "uae": "United Arab Emirates",
    "israel": "Israel",
    "south africa": "South Africa",
}

# ── US states (name or abbreviation → canonical name) ──────────────────────────

_US_STATES: dict[str, str] = {
    "alabama": "Alabama",
    "al": "Alabama",
    "alaska": "Alaska",
    "ak": "Alaska",
    "arizona": "Arizona",
    "az": "Arizona",
    "arkansas": "Arkansas",
    "ar": "Arkansas",
    "california": "California",
    "ca": "California",
    "colorado": "Colorado",
    "co": "Colorado",
    "connecticut": "Connecticut",
    "ct": "Connecticut",
    "delaware": "Delaware",
    "de": "Delaware",
    "florida": "Florida",
    "fl": "Florida",
    "georgia": "Georgia",
    "ga": "Georgia",
    "hawaii": "Hawaii",
    "hi": "Hawaii",
    "idaho": "Idaho",
    "id": "Idaho",
    "illinois": "Illinois",
    "il": "Illinois",
    "indiana": "Indiana",
    "in": "Indiana",
    "iowa": "Iowa",
    "ia": "Iowa",
    "kansas": "Kansas",
    "ks": "Kansas",
    "kentucky": "Kentucky",
    "ky": "Kentucky",
    "louisiana": "Louisiana",
    "la": "Louisiana",
    "maine": "Maine",
    "me": "Maine",
    "maryland": "Maryland",
    "md": "Maryland",
    "massachusetts": "Massachusetts",
    "ma": "Massachusetts",
    "michigan": "Michigan",
    "mi": "Michigan",
    "minnesota": "Minnesota",
    "mn": "Minnesota",
    "mississippi": "Mississippi",
    "ms": "Mississippi",
    "missouri": "Missouri",
    "mo": "Missouri",
    "montana": "Montana",
    "mt": "Montana",
    "nebraska": "Nebraska",
    "ne": "Nebraska",
    "nevada": "Nevada",
    "nv": "Nevada",
    "new hampshire": "New Hampshire",
    "nh": "New Hampshire",
    "new jersey": "New Jersey",
    "nj": "New Jersey",
    "new mexico": "New Mexico",
    "nm": "New Mexico",
    "new york": "New York",
    "ny": "New York",
    "north carolina": "North Carolina",
    "nc": "North Carolina",
    "north dakota": "North Dakota",
    "nd": "North Dakota",
    "ohio": "Ohio",
    "oh": "Ohio",
    "oklahoma": "Oklahoma",
    "ok": "Oklahoma",
    "oregon": "Oregon",
    "or": "Oregon",
    "pennsylvania": "Pennsylvania",
    "pa": "Pennsylvania",
    "rhode island": "Rhode Island",
    "ri": "Rhode Island",
    "south carolina": "South Carolina",
    "sc": "South Carolina",
    "south dakota": "South Dakota",
    "sd": "South Dakota",
    "tennessee": "Tennessee",
    "tn": "Tennessee",
    "texas": "Texas",
    "tx": "Texas",
    "utah": "Utah",
    "ut": "Utah",
    "vermont": "Vermont",
    "vt": "Vermont",
    "virginia": "Virginia",
    "va": "Virginia",
    "washington": "Washington",
    "wa": "Washington",
    "west virginia": "West Virginia",
    "wv": "West Virginia",
    "wisconsin": "Wisconsin",
    "wi": "Wisconsin",
    "wyoming": "Wyoming",
    "wy": "Wyoming",
    "district of columbia": "District of Columbia",
    "dc": "District of Columbia",
}

# ── Canadian provinces ─────────────────────────────────────────────────────────

_CA_PROVINCES: dict[str, str] = {
    "alberta": "Alberta",
    "ab": "Alberta",
    "british columbia": "British Columbia",
    "bc": "British Columbia",
    "manitoba": "Manitoba",
    "mb": "Manitoba",
    "new brunswick": "New Brunswick",
    "nb": "New Brunswick",
    "newfoundland and labrador": "Newfoundland and Labrador",
    "nl": "Newfoundland and Labrador",
    "nova scotia": "Nova Scotia",
    "ns": "Nova Scotia",
    "ontario": "Ontario",
    "on": "Ontario",
    "prince edward island": "Prince Edward Island",
    "pe": "Prince Edward Island",
    "quebec": "Quebec",
    "qc": "Quebec",
    "québec": "Quebec",
    "saskatchewan": "Saskatchewan",
    "sk": "Saskatchewan",
    "northwest territories": "Northwest Territories",
    "nt": "Northwest Territories",
    "nunavut": "Nunavut",
    "nu": "Nunavut",
    "yukon": "Yukon",
    "yt": "Yukon",
}

# ── Fast-path: well-known city → country (only set when country is ambiguous) ──

_CITY_COUNTRY: dict[str, str] = {
    "new york": "United States",
    "san francisco": "United States",
    "los angeles": "United States",
    "chicago": "United States",
    "boston": "United States",
    "seattle": "United States",
    "austin": "United States",
    "denver": "United States",
    "toronto": "Canada",
    "vancouver": "Canada",
    "montreal": "Canada",
    "ottawa": "Canada",
    "calgary": "Canada",
    "edmonton": "Canada",
    "mississauga": "Canada",
    "kitchener": "Canada",
    "waterloo": "Canada",
    "guelph": "Canada",
    "hamilton": "Canada",
    "quebec city": "Canada",
    "london": "United Kingdom",
    "manchester": "United Kingdom",
    "edinburgh": "United Kingdom",
    "glasgow": "United Kingdom",
    "dublin": "Ireland",
    "berlin": "Germany",
    "munich": "Germany",
    "hamburg": "Germany",
    "paris": "France",
    "amsterdam": "Netherlands",
    "barcelona": "Spain",
    "madrid": "Spain",
    "lisbon": "Portugal",
    "milan": "Italy",
    "rome": "Italy",
    "warsaw": "Poland",
    "stockholm": "Sweden",
    "copenhagen": "Denmark",
    "oslo": "Norway",
    "helsinki": "Finland",
    "zurich": "Switzerland",
    "vienna": "Austria",
    "brussels": "Belgium",
    "tokyo": "Japan",
    "singapore": "Singapore",
    "sydney": "Australia",
    "melbourne": "Australia",
    "bangalore": "India",
    "mumbai": "India",
    "delhi": "India",
    "hyderabad": "India",
    "pune": "India",
    "chennai": "India",
    "dubai": "United Arab Emirates",
    "tel aviv": "Israel",
}

# Tokens treated as "not a real place" during parsing
_NON_PLACE_TOKENS: set[str] = {
    "remote",
    "anywhere",
    "worldwide",
    "global",
    "wfh",
    "work from home",
    "hybrid",
    "on-site",
    "onsite",
    "on site",
    "in office",
    "in-office",
    "any location",
    "any",
    "n/a",
}


_REMOTE_PREFIX_RE = re.compile(
    r"^(?:remote|wfh|work from home|anywhere|worldwide|global)\s*[-–—·|/]\s*",
    re.IGNORECASE,
)
_REMOTE_SUFFIX_RE = re.compile(
    r"\s*[-–—·|/]\s*(?:remote|wfh|work from home|anywhere|worldwide|global)$",
    re.IGNORECASE,
)


def _tokens(raw: str) -> list[str]:
    """Split a raw location string on commas and strip non-place tokens."""
    if not raw:
        return []
    # Peel off "Remote — ..." / "... — Remote" wrappers so the real place shows through
    cleaned = _REMOTE_PREFIX_RE.sub("", raw.strip())
    cleaned = _REMOTE_SUFFIX_RE.sub("", cleaned).strip()
    parts = [p.strip() for p in cleaned.split(",")]
    return [p for p in parts if p and p.lower() not in _NON_PLACE_TOKENS]


def parse_location(raw: str) -> tuple[str, str, str]:
    """Parse a free-text location into ``(country, region, city)``.

    Returns empty strings for fields that could not be determined. Handles
    the common formats:

    * ``"Toronto, Ontario, Canada"``
    * ``"San Francisco, CA"``
    * ``"Remote — Canada"``
    * ``"London, UK"``
    * ``"Bangalore"``
    """
    parts = _tokens(raw or "")
    if not parts:
        return "", "", ""

    country = ""
    region = ""

    # Rightmost token — check state/province BEFORE country aliases so that
    # "CA" (California) and "IN" (Indiana) win over their country-code collisions.
    tail = parts[-1].lower()
    if tail in _US_STATES:
        country = "United States"
        region = _US_STATES[tail]
        parts = parts[:-1]
    elif tail in _CA_PROVINCES:
        country = "Canada"
        region = _CA_PROVINCES[tail]
        parts = parts[:-1]
    elif tail in _COUNTRY_ALIASES:
        country = _COUNTRY_ALIASES[tail]
        parts = parts[:-1]

    # Next rightmost — state/province if we still need a region
    if parts and not region:
        tail2 = parts[-1].lower()
        if tail2 in _US_STATES:
            country = country or "United States"
            region = _US_STATES[tail2]
            parts = parts[:-1]
        elif tail2 in _CA_PROVINCES:
            country = country or "Canada"
            region = _CA_PROVINCES[tail2]
            parts = parts[:-1]
        elif country and len(parts) >= 2:
            # Unknown region name but country is known AND there is still a
            # city token in front — accept the middle token as a regional label.
            region = parts[-1]
            parts = parts[:-1]

    # Anything remaining is the city (take the leftmost meaningful token)
    city = parts[0] if parts else ""

    # Backfill country from well-known city names
    if not country and city:
        country = _CITY_COUNTRY.get(city.lower(), "")

    return country, region, city


# ── Employment type detection ──────────────────────────────────────────────────
#
# Title-level checks are word-boundary regexes so "intern" doesn't accidentally
# match "Internal" / "International", "contract" doesn't match "contracts" in
# normal prose, "temp" doesn't match "template" / "temperature".
#
# The INTERN pattern specifically excludes substrings like "Internal" by using
# a negative look-ahead for trailing letters that would form a longer word.

_INTERN_TITLE_RE = re.compile(r"\b(intern(?!s?hip|al|ation)|internship|co[\s-]?op)\b", re.I)
_CONTRACT_TITLE_RE = re.compile(r"\b(contract[\s-]?to[\s-]?hire|c2h|c2c|1099|contingent|contractor|contract)\b", re.I)
_TEMP_TITLE_RE = re.compile(r"\b(temporary|temp)\b(?!late|erature|lat)", re.I)
_PARTTIME_RE = re.compile(r"\bpart[\s_-]?time\b", re.I)
_FULLTIME_RE = re.compile(r"\b(full[\s_-]?time|fte|permanent)\b", re.I)

# Body-level checks are more forgiving but still word-boundary
_EMPLOYMENT_BODY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_INTERN_TITLE_RE, "Internship"),
    (_CONTRACT_TITLE_RE, "Contract"),
    (_TEMP_TITLE_RE, "Temporary"),
    (_PARTTIME_RE, "Part-time"),
    (_FULLTIME_RE, "Full-time"),
]


def parse_employment_type(job_type: str, title: str, description: str) -> str:
    """Detect employment type with the following precedence:

    1. **Title** — an explicit ``Intern`` / ``Contract`` / ``Part-time`` in the
       job title trumps everything else. LinkedIn titles with "Intern" in them
       are always internships regardless of what ``job_type`` the posting
       structurally carries (many employers tag interns as "Full-time" on the
       structured field which is wrong).
    2. **Structured job_type** from the source.
    3. **Description** scan on the first 1500 chars.
    """
    title_str = title or ""

    # Step 1 — title is authoritative
    if _INTERN_TITLE_RE.search(title_str):
        return "Internship"
    if _CONTRACT_TITLE_RE.search(title_str):
        return "Contract"
    if _PARTTIME_RE.search(title_str):
        return "Part-time"
    if _TEMP_TITLE_RE.search(title_str):
        return "Temporary"

    # Step 2 — structured field
    if job_type:
        for pat, kind in _EMPLOYMENT_BODY_PATTERNS:
            if pat.search(str(job_type)):
                return kind

    # Step 3 — description scan
    desc = (description or "")[:1500]
    for pat, kind in _EMPLOYMENT_BODY_PATTERNS:
        if pat.search(desc):
            return kind

    return "Unknown"


# ── Work arrangement detection (generic, text-based) ──────────────────────────
#
# Precedence:
#   1. LinkedIn's parenthetical marker in the location string — "(Hybrid)",
#      "(Remote)", "(On-site)" — which is literally the badge LinkedIn shows
#      on the job card. Most reliable single signal when it's there.
#   2. Explicit arrangement word in the title.
#   3. ``is_remote=True`` flag from the source (True means fully remote).
#      NOTE: ``is_remote=False`` on LinkedIn can mean either hybrid *or*
#      on-site — it is NOT treated as evidence of on-site any more.
#   4. Strong body phrases ("fully remote", "100% remote", "hybrid schedule").
#   5. Fallback: Unknown. Better to surface "Unknown" than mis-tag.
#
# All regexes use ``\b`` word boundaries to avoid "remote" matching "Remote
# Desktop", "hybrid" matching "hybrid cloud", etc.

_WFH_PAREN_RE = re.compile(r"\(\s*(remote|hybrid|on[-\s]?site|onsite)\s*\)", re.I)

_TITLE_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_TITLE_REMOTE_RE = re.compile(r"\bremote\b", re.I)
_TITLE_ONSITE_RE = re.compile(r"\bon[-\s]?site\b|\bonsite\b", re.I)

# Body signals — require fairly strong wording so we don't trip on tangential
# mentions in the benefits / perks sections.
_BODY_REMOTE_STRONG_RE = re.compile(
    r"\b(fully\s+remote|100\s*%\s*remote|remote[-\s]first|work[-\s]from[-\s]home)\b",
    re.I,
)
_BODY_HYBRID_STRONG_RE = re.compile(
    r"\bhybrid\b(?!\s+(cloud|infrastructure|model\s+of|approach))",
    re.I,
)
_BODY_ONSITE_STRONG_RE = re.compile(
    r"\b(on[-\s]?site|onsite|in[-\s]?office|in[-\s]?person)\b",
    re.I,
)
# Negation guard: skip matches preceded by "no", "not", "never" within 3 words
_NEGATION_NEAR_RE = re.compile(
    r"\b(no|not|never|without|neither)\s+(?:\w+\s+){0,2}(remote|hybrid|on[-\s]?site)",
    re.I,
)


def _has_unnegated(pattern: re.Pattern[str], text: str, keyword: str | tuple[str, ...]) -> bool:
    if not pattern.search(text):
        return False
    # Accept either a single spelling or multiple spellings of the same
    # keyword (e.g. "on-site" / "onsite") — any of them counts as a match
    # against the negation phrase.
    keywords = (keyword,) if isinstance(keyword, str) else keyword
    # If a negation of the keyword sits anywhere in the text, consider it ambiguous
    for m in _NEGATION_NEAR_RE.finditer(text):
        match_text = m.group(0).lower()
        if any(kw.lower() in match_text for kw in keywords):
            return False
    return True


def parse_work_arrangement(
    is_remote: bool | None,
    location: str,
    title: str,
    description: str,
) -> str:
    """Return one of ``Remote``, ``Hybrid``, ``On-site``, ``Unknown``."""
    loc = location or ""
    title_str = title or ""

    # 1. LinkedIn's parenthetical marker on the location string
    m = _WFH_PAREN_RE.search(loc)
    if m:
        v = m.group(1).lower().replace(" ", "").replace("-", "")
        if v == "remote":
            return "Remote"
        if v == "hybrid":
            return "Hybrid"
        return "On-site"

    # 2. Title-level explicit labels
    if _TITLE_HYBRID_RE.search(title_str):
        return "Hybrid"
    if _TITLE_REMOTE_RE.search(title_str):
        return "Remote"
    if _TITLE_ONSITE_RE.search(title_str):
        return "On-site"

    # 3. is_remote=True is strong; False is weak (could be hybrid)
    if is_remote is True:
        return "Remote"

    # 4. Body scan with unnegated matches (check hybrid first — it's
    #    specifically-phrased, whereas "remote" appears in many contexts)
    body = (description or "")[:3000]
    if _has_unnegated(_BODY_HYBRID_STRONG_RE, body, "hybrid"):
        return "Hybrid"
    if _has_unnegated(_BODY_REMOTE_STRONG_RE, body, "remote"):
        return "Remote"
    if _has_unnegated(_BODY_ONSITE_STRONG_RE, body, ("on-site", "onsite")):
        return "On-site"

    # 5. Last-ditch: lone "Remote" in the location string
    if re.search(r"\bremote\b", loc, re.I):
        return "Remote"

    return "Unknown"


# ── Matching helpers ───────────────────────────────────────────────────────────


_COUNTRY_CANONICAL_LOWER: dict[str, str] = {v.lower(): v for v in _COUNTRY_ALIASES.values()}


def recognize_country(raw: str) -> str:
    """Return the canonical country name for ``raw`` if it (case-folded,
    trimmed) denotes a recognised country — either a known alias ("US",
    "UK") or an already-canonical name ("Canada") — else ``""``.

    Used both to backfill ``country`` from free-text location strings
    (``parse_location``) and, as of T84, to recognise a bare country name
    sitting inside ``preferred_regions`` (e.g. "Canada") so it can be matched
    exactly rather than ignored.
    """
    if not raw:
        return ""
    key = raw.strip().lower()
    if not key:
        return ""
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    return _COUNTRY_CANONICAL_LOWER.get(key, "")


def country_is_unresolved(city: str, region: str, country: str) -> bool:
    """True when there was location content (``city`` or ``region``) but no
    ``country`` could be identified from it.

    This is the "unknown" case — e.g. "Greater Winnipeg Metropolitan Area"
    parses to a city with no recognised country (T84, found 2026-10-22). It
    is distinct from a genuine worldwide-remote posting, which has no
    location fields at all. Tier classification alone collapses both into
    "global"; this predicate lets a caller surface the difference instead of
    treating "we don't know" as indistinguishable from "anywhere is fine".
    """
    return not (country or "").strip() and bool((city or "").strip() or (region or "").strip())


def matches_region_list(city: str, region: str, preferred: list[str], *, country: str = "") -> bool:
    """Return True if ``city``, ``region``, or (as of T84) ``country``
    matches any entry in ``preferred``.

    City/region matching keeps its substring fallback (a preferred entry
    like "GTA" may appear inside a longer city string) — that behaviour is
    load-bearing and unchanged. Country matching is deliberately **exact**,
    never substring: "US" as a substring of "Australia" would otherwise
    produce a dangerous false positive (a preferred list containing "US"
    silently matching Australian jobs). Country comparison does go through
    ``recognize_country`` so an alias like "US" in ``preferred`` still
    exactly matches a job whose parsed country is the canonical "United
    States".
    """
    if not preferred:
        return False
    targets = {p.strip().lower() for p in preferred if p.strip()}
    if city and city.strip().lower() in targets:
        return True
    if region and region.strip().lower() in targets:
        return True
    # Substring fallback — a preferred entry like "GTA" may appear in city string
    for t in targets:
        if city and t in city.lower():
            return True
        if region and t in region.lower():
            return True
    country_key = (country or "").strip().lower()
    if country_key:
        for t in targets:
            if t == country_key:
                return True
            recognized = recognize_country(t)
            if recognized and recognized.lower() == country_key:
                return True
    return False
