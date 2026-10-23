"""T49 — the heuristic resume extractor, the path that works without a key.

These tests are written against four committed fixture resumes of four
different professions (nurse, civil engineer, accountant, teacher) in three
different countries plus one that states no country at all. The professional
variety is not decoration: the product spec records that JobFinder is hardcoded to
QA-in-Canada, and an extractor that only works on a software resume would
rebuild that bias one layer down. `test_fixture_corpus_is_not_qa_in_canada`
asserts the corpus itself stays varied.

The two load-bearing guarantees:

* **No key, no network, no new dependency.** `test_extracts_with_anthropic
  _blocked_at_import` blocks `anthropic` with a `sys.meta_path` hook the way
  `scripts/smoke.py` blocks PySide6, with `ANTHROPIC_API_KEY` unset.
* **A blank is honest; a guess is not.** A field the resume does not state
  comes back empty with provenance `absent` and confidence 0.0 — never a
  plausible-looking guess. A guessed country would silently skew every
  subsequent job search.
"""

from __future__ import annotations

import ast
import importlib
import re
import socket
import sys
from pathlib import Path

import pytest

from jobfinder.resume.heuristic import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    KNOWN_COUNTRIES,
    PROVENANCE_ABSENT,
    PROVENANCE_HEURISTIC,
    REQUIRED_FIELDS,
    SENIORITY_LEVELS,
    DerivedField,
    HeuristicProfile,
    extract_profile,
    extract_profile_from_resume,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "resumes"
_MODULE_PATH = Path(__file__).parent.parent / "jobfinder" / "resume" / "heuristic.py"


def _read(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _entry_headers(text: str) -> list[list[str]]:
    """Every dated entry's header lines, for tests that inspect the title
    selection's inputs rather than its output."""
    from jobfinder.resume.heuristic import _DATE_RANGE_RE, _entry_header, _is_bullet, _split_sections

    headers = []
    for section in _split_sections(text.splitlines()):
        if section.kind != "experience":
            continue
        for index, line in enumerate(section.body):
            if line.strip() and not _is_bullet(line) and _DATE_RANGE_RE.search(line):
                headers.append(_entry_header(section.body, index))
    return headers


def _HAS_DATE_RANGE(line: str) -> bool:
    """Deliberately a separate, dumber pattern than the extractor's own: a
    test that reuses the implementation's regex cannot catch that regex being
    wrong."""
    return bool(re.search(r"(19|20)\d{2}\s*[-–—]\s*((19|20)\d{2}|Present)", line))


# --------------------------------------------------------------------------
# The no-key / no-network / no-new-dependency contract
# --------------------------------------------------------------------------


class _BlockImports:
    """sys.meta_path hook that raises on any attempt to import `blocked`.

    Same shape as scripts/smoke.py's _BlockQtImports: find_spec raises for the
    blocked root and its submodules, and returns None for everything else so
    the normal import machinery still runs.
    """

    def __init__(self, blocked: str) -> None:
        self.blocked = blocked

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.blocked or fullname.startswith(self.blocked + "."):
            raise ImportError(f"Blocked import of {fullname!r} — the no-key path must not need it.")
        return None


@pytest.fixture
def anthropic_blocked(monkeypatch):
    """Block `import anthropic` and force a fresh import of the extractor.

    Teardown matters as much as setup here: the hook is removed and the
    modules that were evicted from sys.modules are put back. Leaving either
    behind would poison every test that runs afterwards in the same process.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    evicted = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "anthropic" or name.startswith("anthropic.") or name == "jobfinder.resume.heuristic"
    }
    for name in evicted:
        del sys.modules[name]

    hook = _BlockImports("anthropic")
    sys.meta_path.insert(0, hook)
    try:
        yield hook
    finally:
        sys.meta_path.remove(hook)
        for name in list(sys.modules):
            if name == "anthropic" or name.startswith("anthropic."):
                del sys.modules[name]
        sys.modules.update(evicted)


def test_the_import_block_itself_actually_blocks(anthropic_blocked):
    """Guard against a vacuous pass: if the hook silently did nothing, every
    other assertion in this section would hold for the wrong reason."""
    with pytest.raises(ImportError):
        importlib.import_module("anthropic")


def test_extracts_with_anthropic_blocked_at_import(anthropic_blocked):
    module = importlib.import_module("jobfinder.resume.heuristic")

    assert "anthropic" not in sys.modules, "the extractor pulled in anthropic transitively"

    profile = module.extract_profile(_read("nurse_ireland.txt"))
    assert profile.email.value == "aoife.nibhriain@example.com"
    assert profile.country.value == "Ireland"
    assert profile.job_titles.value, "no job titles extracted on the no-key path"


@pytest.mark.parametrize("module_name", ["jobfinder.resume.profiler", "jobfinder.fillerbot.profiler"])
def test_the_agentic_profilers_import_without_anthropic(anthropic_blocked, monkeypatch, module_name):
    """Both agentic profilers moved `import anthropic` into `_extract()`.

    Not this task's DoD, but it is what lets T50's unified read model import
    the heuristic and agentic layers together under this same import block —
    a module-level `import anthropic` made that impossible.

    **T54 addendum.** `importlib.import_module` on a name already absent
    from `sys.modules` creates a *new* module object and rebinds it as an
    attribute of the parent package (`jobfinder.resume.profiler` /
    `jobfinder.fillerbot.profiler`) — that is just how `import` works, not
    something this test does on purpose. `monkeypatch.delitem` restores
    the `sys.modules` entry at teardown, but has no idea about that parent
    attribute, so without the `finally` block below this test would leave
    the parent package pointing at a throwaway module object while
    `sys.modules` points at the original one. A later call-time
    `from jobfinder.resume.profiler import name` resolves against the
    parent attribute (the orphan); a test's `import jobfinder.resume.profiler
    as m; monkeypatch.setattr(m, "name", fake)` resolves `m` against
    `sys.modules` (the original) — two different objects, one patched, one
    actually called. That divergence is exactly what sent a real request
    to api.anthropic.com during T54's `propose_from_agentic` tests: this
    test ran first in suite order, the leak went uncaught, and a later
    test's "mocked" Anthropic call was never mocked at all. Restoring the
    parent attribute here closes that hole for any future call-time import
    in this codebase, not just this task's.
    """
    parent_name, _, attr_name = module_name.rpartition(".")
    parent = sys.modules[parent_name]
    original_submodule = getattr(parent, attr_name, None)

    monkeypatch.delitem(sys.modules, module_name, raising=False)
    try:
        module = importlib.import_module(module_name)
        assert "anthropic" not in sys.modules
        assert callable(module._extract)
    finally:
        if original_submodule is not None:
            setattr(parent, attr_name, original_submodule)


def test_extraction_opens_no_socket(monkeypatch):
    """No network. Not 'no network call we noticed' — no socket at all."""

    def _no_sockets(*args, **kwargs):
        raise AssertionError("the heuristic extractor must not open a socket")

    monkeypatch.setattr(socket, "socket", _no_sockets)
    monkeypatch.setattr(socket, "create_connection", _no_sockets)

    profile = extract_profile(_read("civil_engineer_india.txt"))
    assert profile.full_name.value == "Rohan Desai"


def test_module_imports_only_stdlib_and_jobfinder():
    """No new dependency. Asserted over the real import statements, so adding
    one later fails here rather than at install time on a user's machine."""
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    third_party = {r for r in roots if r not in sys.stdlib_module_names and r != "jobfinder"}
    assert third_party == set(), f"heuristic.py grew a third-party dependency: {sorted(third_party)}"
    assert "anthropic" not in roots


# --------------------------------------------------------------------------
# Extraction across professions
# --------------------------------------------------------------------------

# fixture -> (expected name, email, phone substring, country, a title, a skill)
_CORPUS = {
    "nurse_ireland.txt": (
        "AOIFE NÍ BHRIAIN",
        "aoife.nibhriain@example.com",
        "+353",
        "Ireland",
        "Senior Staff Nurse",
        "IV cannulation",
    ),
    "civil_engineer_india.txt": (
        "Rohan Desai",
        "rohan.desai@example.com",
        "+91",
        "India",
        "Senior Structural Engineer",
        "Seismic analysis",
    ),
    "accountant_canada.txt": (
        "Marguerite Tremblay",
        "marguerite.tremblay@example.com",
        "019-4471",
        "Canada",
        "Management Accountant",
        "Variance analysis",
    ),
    "teacher_sparse.txt": (
        "Daniel Okonkwo-Weber",
        "daniel.okonkwo-weber@example.com",
        None,  # states no phone
        None,  # states no country
        "Head of Mathematics Department",
        None,  # has no skills section
    ),
    # Layout B: employer above title.
    "veterinarian_cv_heading.txt": (
        "Lotte Hagedorn",
        "lotte.hagedorn@example.com",
        "+49",
        "Germany",
        "Senior Veterinary Surgeon",
        "Diagnostic imaging",
    ),
    # Layout A: title above employer, no separator to split on.
    "electrician_australia.txt": (
        "Thomas Bragg-Whitfield",
        "thomas.braggwhitfield@example.com",
        "+61",
        "Australia",
        "Senior Electrician",
        "Cable sizing",
    ),
    # Layout E: title, employer, then a city line, then the dates.
    "chef_spain.txt": (
        "Marta Oliveira Ferré",
        "marta.oliveiraferre@example.com",
        "+34",
        "Spain",
        "Head Chef",
        "Brigade supervision",
    ),
}

# Every title each fixture should yield, in order. The single-title spot check
# in `_CORPUS` cannot see an employer or a city sneaking into the list
# alongside the right answer, which is exactly the defect this corpus missed
# the first time round: four professions, one layout.
_EXPECTED_TITLES = {
    "nurse_ireland.txt": ["Senior Staff Nurse", "Staff Nurse"],
    "civil_engineer_india.txt": ["Senior Structural Engineer", "Structural Engineer"],
    "accountant_canada.txt": ["Management Accountant", "Staff Accountant"],
    "teacher_sparse.txt": ["Head of Mathematics Department", "Mathematics Teacher"],
    "veterinarian_cv_heading.txt": ["Senior Veterinary Surgeon", "Veterinary Surgeon"],
    "electrician_australia.txt": ["Senior Electrician", "Electrician", "Apprentice Electrician"],
    "chef_spain.txt": ["Head Chef", "Sous Chef", "Chef de Partie"],
}


@pytest.mark.parametrize("fixture_name", sorted(_CORPUS))
def test_extracts_the_named_fields(fixture_name):
    name, email, phone_part, country, title, skill = _CORPUS[fixture_name]
    profile = extract_profile(_read(fixture_name))

    assert profile.full_name.value == name
    assert profile.email.value == email
    assert title in profile.job_titles.value

    if phone_part is None:
        assert profile.phone.value == ""
    else:
        assert phone_part in profile.phone.value

    if country is None:
        assert profile.country.value == ""
    else:
        assert profile.country.value == country

    if skill is None:
        assert profile.skills.value == ()
    else:
        assert skill in profile.skills.value


@pytest.mark.parametrize("fixture_name", sorted(_CORPUS))
def test_every_found_field_is_heuristic_with_a_confidence(fixture_name):
    profile = extract_profile(_read(fixture_name))

    assert set(profile.fields()) == set(REQUIRED_FIELDS)
    for field_name, field in profile.fields().items():
        assert isinstance(field, DerivedField), field_name
        if field.found:
            assert field.provenance == PROVENANCE_HEURISTIC, field_name
            assert 0.0 < field.confidence <= 1.0, field_name
            assert field.method, f"{field_name} does not say how it was found"
        else:
            assert field.provenance == PROVENANCE_ABSENT, field_name
            assert field.confidence == CONFIDENCE_NONE, field_name


@pytest.mark.parametrize("fixture_name", sorted(_CORPUS))
def test_seniority_is_derived_for_every_fixture(fixture_name):
    profile = extract_profile(_read(fixture_name))
    assert isinstance(profile, HeuristicProfile)
    assert profile.seniority.found, "seniority not derived"
    assert profile.seniority.value in SENIORITY_LEVELS


# --------------------------------------------------------------------------
# Entry layout — the axis the first corpus did not vary
# --------------------------------------------------------------------------

# The same one-entry resume written six ways. Every mainstream resume template
# is one of these, and `job_titles` drives T52's job search, so an employer or
# a city landing here is not a cosmetic error: it searches for "Northwind
# Analytics" or "Barcelona" as though it were a job title.
_ONE_ENTRY_HEAD = "Marta Oliveira\nmarta.oliveira@example.com\n\nEXPERIENCE\n\n"
_ONE_ENTRY_TAIL = "- Ran a brigade of nine across two services.\n"

_ENTRY_LAYOUTS = {
    "title / employer / dates": "Head Chef\nRestaurant Les Glicines\n2019 - Present\n",
    "employer / title / dates": "Restaurant Les Glicines\nHead Chef\n2019 - Present\n",
    "title - employer / dates": "Head Chef - Restaurant Les Glicines\n2019 - Present\n",
    "title / dates / employer": "Head Chef\n2019 - Present\nRestaurant Les Glicines\n",
    "title / employer / city / dates": ("Head Chef\nRestaurant Les Glicines\nBarcelona, Spain\n2019 - Present\n"),
    "title, employer <tab> dates": "Head Chef, Restaurant Les Glicines        2019 - Present\n",
}


@pytest.mark.parametrize("layout", sorted(_ENTRY_LAYOUTS))
def test_titles_survive_every_entry_layout(layout):
    text = _ONE_ENTRY_HEAD + _ENTRY_LAYOUTS[layout] + _ONE_ENTRY_TAIL
    profile = extract_profile(text)
    assert list(profile.job_titles.value) == ["Head Chef"], f"layout {layout!r} misread the title"


def test_employers_and_cities_never_appear_as_titles():
    """The regression in full: three entries, every one of them title / employer
    / city / dates, which yielded three employer names."""
    titles = list(extract_profile(_read("chef_spain.txt")).job_titles.value)
    assert titles == ["Head Chef", "Sous Chef", "Chef de Partie"]
    for employer in ("Restaurant Les Glicines", "Hotel Miramar", "Can Rovira"):
        assert employer not in titles
    for city in ("Barcelona", "Malaga", "Girona", "Barcelona, Spain"):
        assert city not in titles


@pytest.mark.parametrize("fixture_name", sorted(_EXPECTED_TITLES))
def test_every_title_in_every_fixture(fixture_name):
    profile = extract_profile(_read(fixture_name))
    assert list(profile.job_titles.value) == _EXPECTED_TITLES[fixture_name]


def test_a_rank_word_in_a_title_is_not_lost_to_a_layout():
    """The knock-on the first version hid. Seniority falls back to counting
    dates when the title holding the rank word was never extracted, so a
    layout bug downgrades the confidence of a *different* field."""
    for fixture_name in ("electrician_australia.txt", "veterinarian_cv_heading.txt", "chef_spain.txt"):
        profile = extract_profile(_read(fixture_name))
        assert profile.seniority.confidence == CONFIDENCE_HIGH, fixture_name
        assert "rank word" in profile.seniority.method, fixture_name


def test_the_headline_under_the_name_seeds_title_recognition():
    """A two-word headline like "Pastry Chef" is shape-identical to a person's
    name, so an earlier version discarded it and lost the only evidence that
    could resolve an employer-above-title entry."""
    with_headline = extract_profile(
        "Owen Castellanos\nPastry Chef\n\nowen.castellanos@example.com\n\n"
        "EXPERIENCE\n\nLes Glicines\nPastry Chef\n2019 - 2024\n- Ran the dessert section.\n"
    )
    assert list(with_headline.job_titles.value) == ["Pastry Chef"]


def test_an_undecidable_entry_prefers_the_topmost_line():
    """The documented residual, pinned so it stays a decision.

    One entry, employer above title, no rank word and no headline: nothing in
    the document says which line is which. Title-above-employer is the
    dominant convention, so the topmost line is the better guess — and here
    that guess is wrong. Pinned rather than hidden: if a future change makes
    this resolvable, this test should be updated to expect the title.
    """
    undecidable = extract_profile(
        "Owen Castellanos\nowen.castellanos@example.com\n\n"
        "EXPERIENCE\n\nLes Glicines\nPastry Chef\n2019 - 2024\n- Ran the dessert section.\n"
    )
    assert list(undecidable.job_titles.value) == ["Les Glicines"]

    # Any one of the three evidence sources is enough to get it right: a rank
    # word in the title, a legal suffix on the employer, or a headline.
    for rescued in (
        "Owen Castellanos\nowen.castellanos@example.com\n\n"
        "EXPERIENCE\n\nLes Glicines\nHead Pastry Chef\n2019 - 2024\n- Ran it.\n",
        "Owen Castellanos\nowen.castellanos@example.com\n\n"
        "EXPERIENCE\n\nLes Glicines Ltd\nPastry Chef\n2019 - 2024\n- Ran it.\n",
        "Owen Castellanos\nPastry Chef\n\nowen.castellanos@example.com\n\n"
        "EXPERIENCE\n\nLes Glicines\nPastry Chef\n2019 - 2024\n- Ran it.\n",
    ):
        titles = list(extract_profile(rescued).job_titles.value)
        assert titles and "Chef" in titles[0], titles


def _one_entry(header: str, headline: str = "") -> str:
    """A single-entry resume, so a test can isolate one header layout."""
    top = f"Dana Whitlock\n{headline}\n\n" if headline else "Dana Whitlock\n"
    return f"{top}dana.whitlock@example.com\n\nEXPERIENCE\n\n{header}- Did the work.\n"


@pytest.mark.parametrize(
    "title",
    ["Quality Engineer 2", "Technician 1", "Developer 3", "Analyst 2", "Quality Engineer II", "Radiographer Band 6"],
)
def test_a_levelled_title_keeps_its_level(title):
    """Numbered ladders are not a software quirk — NHS bands, civil-service
    grades and trade certifications all number the role. Rejecting any segment
    containing a digit discarded the title and left the employer."""
    profile = extract_profile(_one_entry(f"{title}\nKalevala Instruments\n2020 - Present\n"))
    assert list(profile.job_titles.value) == [title]


@pytest.mark.parametrize(
    "not_a_title",
    [
        "2019 - 2024",
        "Jan 2019",
        "Flat 402, Anand Residency, Pune 411006, India",
        "28 Kurrajong Street, Newcastle, Australia",
        "Tel 555 019 4471",
    ],
)
def test_date_and_address_lines_are_still_not_titles(not_a_title):
    """The intent behind the digit rejection, kept while narrowing its reach:
    a date or an address must never be read as a job title."""
    profile = extract_profile(_one_entry(f"Head Chef\n{not_a_title}\n2020 - Present\n"))
    titles = list(profile.job_titles.value)
    assert titles == ["Head Chef"], titles


def test_a_rank_word_in_an_employer_name_loses_to_the_resumes_own_vocabulary():
    """ "Senior Living Communities of Ohio" is an eldercare employer, not a
    senior role. Evidence from the document outranks the generic rank-word
    prior, so a headline or a second entry resolves it correctly."""
    with_headline = extract_profile(
        _one_entry(
            "Dining Services Coordinator\nSenior Living Communities of Ohio\n2019 - Present\n",
            headline="Dining Services Coordinator",
        )
    )
    assert list(with_headline.job_titles.value) == ["Dining Services Coordinator"]

    two_entries = extract_profile(
        "Dana Whitlock\ndana.whitlock@example.com\n\nEXPERIENCE\n\n"
        "Dining Services Coordinator\nSenior Living Communities of Ohio\n2019 - Present\n- Ran meals.\n\n"
        "Lead Dining Coordinator\nMaple Grove Residences\n2015 - 2019\n- Ran meals.\n"
    )
    assert list(two_entries.job_titles.value) == ["Dining Services Coordinator", "Lead Dining Coordinator"]


def test_a_word_recurring_across_entries_identifies_the_title():
    """The asymmetry the title selection turns on: employers differ from entry
    to entry, job titles repeat.

    Employer above title, no rank word anywhere and no headline — none of the
    other signals apply. The repetition alone is enough, and it needs no
    built-in vocabulary: nothing here knows what a pastry chef is.
    """
    profile = extract_profile(
        "Dana Whitlock\ndana.whitlock@example.com\n\nEXPERIENCE\n\n"
        "Pemberton Hall\nPastry Chef\n2019 - 2024\n- Ran desserts.\n\n"
        "Crumb & Rye\nPastry Chef\n2015 - 2019\n- Ran desserts.\n"
    )
    assert list(profile.job_titles.value) == ["Pastry Chef"]


def test_the_recurring_word_vocabulary_is_derived_per_resume():
    """Asserted across the whole corpus: the word that identifies each
    profession is recovered from the resume itself, for seven professions,
    with no profession named anywhere in the module."""
    from jobfinder.resume.heuristic import _recurring_words

    expected_word = {
        "nurse_ireland.txt": "nurse",
        "civil_engineer_india.txt": "engineer",
        "accountant_canada.txt": "accountant",
        "teacher_sparse.txt": "mathematics",
        "veterinarian_cv_heading.txt": "surgeon",
        "electrician_australia.txt": "electrician",
        "chef_spain.txt": "chef",
    }
    for fixture_name, word in expected_word.items():
        headers = _entry_headers(_read(fixture_name))
        assert word in _recurring_words(headers), f"{fixture_name} lost {word!r}"


def test_a_rank_word_in_an_employer_name_is_a_known_residual():
    """The documented limit, pinned so it stays a decision.

    One entry, no headline: "Senior Living Communities of Ohio" holds a rank
    word and "Dining Services Coordinator" holds nothing the document can
    corroborate, so the employer wins. Resolving it needs to know Ohio is a
    place, and a state gazetteer was rejected deliberately — it would demote
    real titles like "Washington Bureau Chief". If a future change makes this
    resolvable, update this test to expect the title.
    """
    profile = extract_profile(
        _one_entry("Dining Services Coordinator\nSenior Living Communities of Ohio\n2019 - Present\n")
    )
    assert list(profile.job_titles.value) == ["Senior Living Communities of Ohio"]


def test_a_pay_band_after_a_comma_is_dropped():
    """ "Radiographer, Grade 7" yields "Radiographer": the band narrows a job
    search for no gain, and the searchable title is the part before it."""
    profile = extract_profile(_one_entry("Radiographer, Grade 7\nSt Aidans Hospital\n2019 - Present\n"))
    assert list(profile.job_titles.value) == ["Radiographer"]


def test_the_headline_is_found_when_the_name_is_in_capitals():
    """A name in capitals reads as a section heading, which emptied the
    preamble and silently killed the headline seed — for a majority of real
    resumes, since capitalised names are commonplace. Here the headline is the
    only thing that can resolve an employer-above-title entry."""
    profile = extract_profile(
        "AOIFE NÍ BHRIAIN\nRegistered Nurse\n\naoife.nibhriain@example.com\n\n"
        "EXPERIENCE\n\nMercy Regional Hospital\nRegistered Nurse\n2017 - 2021\n- Cared for patients.\n"
    )
    assert list(profile.job_titles.value) == ["Registered Nurse"]


def test_a_document_heading_above_the_name_does_not_block_the_headline():
    profile = extract_profile(
        "CURRICULUM VITAE\n\nLotte Hagedorn\nVeterinary Surgeon\n\nlotte.hagedorn@example.com\n\n"
        "EXPERIENCE\n\nPraxis am Stadtpark\nVeterinary Surgeon\n2015 - 2020\n- Ran consultations.\n"
    )
    assert list(profile.job_titles.value) == ["Veterinary Surgeon"]


def test_a_labelled_name_line_is_not_a_headline_title():
    """ "Name: Rohan Desai" was being used as the headline, seeding the
    corroboration vocabulary with the person's own surname — which would
    promote an employer named after them."""
    profile = extract_profile(
        "Name: Rohan Desai\nStructural design engineer\n\nrohan.desai@example.com\n\n"
        "EXPERIENCE\n\nDeccan Bridge Consortium\nStructural Engineer\n2014 - 2019\n- Detailed bridges.\n"
    )
    assert list(profile.job_titles.value) == ["Structural Engineer"]


def test_a_title_ending_in_an_organisation_word_is_kept():
    """The employer filter only ever demotes a candidate. "Technical Services
    Manager" ends in an org word but is the real title here."""
    profile = extract_profile(
        "Dana Whitlock\ndana.whitlock@example.com\n\n"
        "EXPERIENCE\n\nTechnical Services Manager\nPinewood Holdings Ltd\n2019 - 2024\n- Ran the desk.\n"
    )
    assert list(profile.job_titles.value) == ["Technical Services Manager"]


def test_entry_headers_do_not_bleed_between_entries():
    """Entries with no blank line or bullet between them: the run above a
    dates line must not reach back into the entry before it."""
    profile = extract_profile(
        "Dana Whitlock\ndana.whitlock@example.com\n\nEXPERIENCE\n"
        "Senior Baker\nFennel & Rye\n2019 - 2024\nBaker\nCrumb Street\n2015 - 2019\n"
    )
    assert list(profile.job_titles.value) == ["Senior Baker", "Baker"]


def test_fixture_corpus_covers_more_than_one_entry_layout():
    """The sibling of `test_fixture_corpus_is_not_qa_in_canada`.

    The first corpus varied profession and held layout constant, so a defect
    affecting every layout but one was invisible to all four fixtures. This
    asserts the second axis stays varied too.
    """
    layouts: dict[str, set[str]] = {}

    for fixture_name, expected in _EXPECTED_TITLES.items():
        lines = [line.strip() for line in _read(fixture_name).splitlines()]
        title = expected[0]
        index = next(i for i, line in enumerate(lines) if line.startswith(title))
        after = lines[index + 1] if index + 1 < len(lines) else ""

        if lines[index] != title:
            layout = "title and employer on one line"
        elif _HAS_DATE_RANGE(after):
            layout = "title then dates"
        elif lines[index - 1] in ("", "EXPERIENCE", "WORK HISTORY", "PROFESSIONAL EXPERIENCE"):
            layout = "title above employer"
        else:
            layout = "employer above title"
        layouts.setdefault(layout, set()).add(fixture_name)

    assert "title and employer on one line" in layouts
    assert "title above employer" in layouts
    assert "employer above title" in layouts
    assert len(layouts) >= 3, f"the corpus only covers {sorted(layouts)}"
    assert len(_EXPECTED_TITLES) >= 6


def test_fixture_corpus_is_not_qa_in_canada():
    """The anti-bias requirement, asserted on the corpus rather than trusted.

    the product spec: the product is hardcoded to QA jobs in Canada. If this
    extractor is only ever exercised against software resumes in Canada, the
    next person will tune it into the same shape.
    """
    texts = {name: _read(name) for name in _CORPUS}
    assert len(texts) >= 3

    countries = {expected[3] for expected in _CORPUS.values()} - {None}
    assert len(countries) >= 2, "the corpus needs more than one country"
    assert countries - {"Canada"}, "every fixture is Canadian — a country bug could hide"

    # Every profession in the corpus is non-software, so a software-shaped
    # assumption anywhere in the extractor shows up as a failure rather than
    # as an untested path. Asserted by the titles each fixture must yield,
    # which is the part a future edit would have to change.
    software_words = ("engineer, software", "developer", "programmer", "qa ", "test analyst")
    for fixture_name, titles in _EXPECTED_TITLES.items():
        for title in titles:
            lowered_title = title.lower()
            for word in software_words:
                assert word not in lowered_title, f"{fixture_name} expects a software title: {title}"
    professions = {titles[-1].split()[-1].lower() for titles in _EXPECTED_TITLES.values()}
    assert len(professions) >= 5, f"the corpus covers too few professions: {sorted(professions)}"

    for name, text in texts.items():
        lowered = text.lower()
        for banned in ("quality assurance", "sdet", "selenium"):
            assert banned not in lowered, f"{name} mentions {banned}"
        # Synthetic only: invented people, example.com, 555-style numbers.
        assert "@example.com" in lowered, f"{name} must use an example.com address"


def test_module_carries_no_profession_specific_vocabulary():
    """Structure over keywords. A seed vocabulary of QA/software terms would
    make the extractor work best on exactly the resumes it already over-fits.
    """
    source = _MODULE_PATH.read_text(encoding="utf-8").lower()
    for banned in ("quality assurance", "sdet", "selenium", "javascript", "pytest", "cypress", "jira"):
        assert banned not in source, f"heuristic.py seeds the vocabulary with {banned!r}"


def test_no_country_is_privileged():
    """No baked-in default country, and Canada is not special — the two ways
    the product spec's bias would reappear here."""
    assert len(KNOWN_COUNTRIES) >= 50
    assert "Canada" in KNOWN_COUNTRIES
    assert "India" in KNOWN_COUNTRIES
    # A resume with no country gets no country, not the most common one.
    assert extract_profile("Jordan Vance\njordan.vance@example.com\n").country.value == ""


# --------------------------------------------------------------------------
# Decision 1: absent, never guessed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    ["CURRICULUM VITAE", "Curriculum Vitae", "Personal Details", "PERSONAL INFORMATION", "Contact Details"],
)
def test_a_document_heading_is_not_a_name(heading):
    """ "CURRICULUM VITAE" is two alphabetic words, so the shape test alone
    accepts it. Returning it as the user's name breaks the rule this module is
    built on: a blank is honest, a wrong-but-plausible value is not."""
    profile = extract_profile(
        f"{heading}\n\nsomebody@example.com\n\nEXPERIENCE\n\nHead Chef - Les Glicines\n2019 - 2024\n"
    )
    assert profile.full_name.value == ""
    assert profile.full_name.provenance == PROVENANCE_ABSENT
    assert profile.full_name.confidence == CONFIDENCE_NONE


@pytest.mark.parametrize(
    "sentence",
    ["Prepared specimens. Cleaned equipment.", "Cleaned equipment.", "Logged results.", "Duties included filing."],
)
def test_a_sentence_is_not_a_name(sentence):
    """A short sentence of body text is two to four alphabetic words, so the
    shape test alone accepts it. This field is what FillerBot types into
    application forms, which makes a plausible-looking guess here worse than
    a blank."""
    from jobfinder.resume.heuristic import _looks_like_person_name

    assert not _looks_like_person_name(sentence)


@pytest.mark.parametrize(
    "name",
    [
        "Jane A.",
        "Jane Smith Jr.",
        "St. John Smith",
        "Dr. Jane Smith",
        "Mr. John Smith",
        "Mrs. Jane Smith",
        "Prof. Jane Smith",
        "Rev. John Smith",
        "Marguerite Tremblay",
        "AOIFE NÍ BHRIAIN",
        "Daniel Okonkwo-Weber",
        "Marta Oliveira Ferré",
        "Thomas Bragg-Whitfield",
    ],
)
def test_real_names_still_pass_the_sentence_guard(name):
    """The guard keys on a word of three or more letters followed by a full
    stop, so a trailing initial ("Jane A."), a generational suffix ("Jr.") and
    an abbreviated name particle ("St. John") all survive. A leading honorific
    is stripped first, because "Prof." and "Mrs." are over the threshold and a
    false `absent` on a real name is a worse trade than the bug being fixed."""
    from jobfinder.resume.heuristic import _looks_like_person_name

    assert _looks_like_person_name(name)


@pytest.mark.parametrize("typo", ["Maria Fernanda Souza.", "Jane Smith Esq."])
def test_a_name_ending_in_a_full_stop_is_a_known_residual(typo):
    """The documented cost of the sentence guard, pinned so it stays a
    decision.

    "Maria Fernanda Souza." and "Cleaned equipment." are the same shape — two
    or three capitalised words closed by a full stop — so no rule separates
    them, and the guard has to choose which way to be wrong. It chooses
    `absent`, because this field is typed into application forms and a
    sentence landing there is worse than a blank the user can fill in. A
    trailing "Esq." pays the same cost for the same reason.
    """
    from jobfinder.resume.heuristic import _looks_like_person_name

    assert not _looks_like_person_name(typo)


def test_a_resume_with_no_name_and_only_body_text_yields_absent():
    """The degenerate shape the document-heading fix made reachable: the
    heading is skipped correctly, then the scan walks into body text."""
    profile = extract_profile("CURRICULUM VITAE\n\nDUTIES\n\nPrepared specimens. Cleaned equipment.\nLogged results.\n")
    assert profile.full_name.value == ""
    assert profile.full_name.provenance == PROVENANCE_ABSENT
    assert profile.job_titles.value == ()
    assert profile.job_titles.provenance == PROVENANCE_ABSENT


def test_an_unrecognised_single_word_heading_is_not_a_job_title():
    """ "DUTIES" is not in the known-section vocabulary, so it reached
    job_titles through the headline fallback. A single all-caps word with
    nothing corroborating it is a heading, not a title."""
    for heading in ("DUTIES", "BACKGROUND2", "RESPONSIBILITIES"):
        profile = extract_profile(f"CURRICULUM VITAE\n\n{heading}\n\nSomething was done here.\n")
        assert profile.job_titles.value == (), heading


def test_a_two_word_headline_in_capitals_is_still_a_title():
    """The guard is scoped to a *single* all-caps word, so a resume that sets
    its headline in capitals keeps it."""
    profile = extract_profile("Dana Whitlock\nREGISTERED NURSE\n\ndana.whitlock@example.com\n")
    assert profile.job_titles.value == ("REGISTERED NURSE",)


@pytest.mark.parametrize(
    "text",
    [
        # A resume headed "RESUME", no name anywhere.
        "RESUME\n\nlab.tech@example.com\n+44 20 5550 0133\nManchester, United Kingdom\n\n"
        "EMPLOYMENT\n\nSenior Laboratory Technician\nNorthfield Pathology\n2018 - Present\n"
        "- Prepared specimens and logged results.\n",
        # A resume opening on a professional summary, no name anywhere.
        "PROFESSIONAL SUMMARY\n\nLaboratory technician with eleven years in histopathology.\n\n"
        "EMPLOYMENT\n\nSenior Laboratory Technician\nNorthfield Pathology\n2018 - Present\n"
        "- Prepared specimens and logged results.\n",
    ],
)
def test_a_realistic_nameless_resume_still_reads_its_title(text):
    """The guards protect a degenerate shape and must not cost anything on a
    realistic one: no name is still `absent`, and the title still resolves."""
    profile = extract_profile(text)
    assert profile.full_name.value == ""
    assert profile.full_name.provenance == PROVENANCE_ABSENT
    assert list(profile.job_titles.value) == ["Senior Laboratory Technician"]


def test_a_document_heading_is_skipped_to_reach_the_real_name():
    """The commoner case: the heading is there *and* so is the name."""
    profile = extract_profile(_read("veterinarian_cv_heading.txt"))
    assert profile.full_name.value == "Lotte Hagedorn"
    assert profile.full_name.confidence == CONFIDENCE_LOW


def test_a_field_the_resume_does_not_state_is_absent_not_heuristic():
    profile = extract_profile(_read("teacher_sparse.txt"))

    for field_name in ("phone", "country", "skills"):
        field = profile.fields()[field_name]
        assert not field.found, f"{field_name} was invented"
        assert field.provenance == PROVENANCE_ABSENT
        assert field.confidence == CONFIDENCE_NONE
        assert field.value in ("", ())

    # ... while the fields it does state are still extracted.
    assert profile.email.found
    assert profile.job_titles.found


def test_country_is_not_guessed_from_an_ambiguous_calling_code():
    """+1 covers the US, Canada and nineteen other territories. Picking one
    would be a coin flip presented as data."""
    profile = extract_profile("Sam Ridley\nsam.ridley@example.com\n+1 555 019 2288\n")
    assert profile.phone.found
    assert profile.country.value == ""
    assert profile.country.provenance == PROVENANCE_ABSENT


def test_empty_resume_yields_all_absent_and_no_exception():
    for text in ("", "   \n\n\t  \n"):
        profile = extract_profile(text)
        assert set(profile.fields()) == set(REQUIRED_FIELDS)
        for field_name, field in profile.fields().items():
            assert not field.found, field_name
            assert field.provenance == PROVENANCE_ABSENT, field_name
            assert field.confidence == CONFIDENCE_NONE, field_name


def test_unreadable_resume_yields_all_absent_and_no_exception():
    """parser.py's _parse_text uses errors='replace', so a binary file reads
    back as plausible-looking mojibake rather than raising. The extractor has
    to recognise that for what it is instead of mining it for a name."""
    garbage = "%PDF-1.4\n\x00\x01��� stream \x04��\x0c��x�\n���"
    profile = extract_profile(garbage)
    for field_name, field in profile.fields().items():
        assert not field.found, f"{field_name} extracted from binary noise"
        assert field.provenance == PROVENANCE_ABSENT


@pytest.mark.parametrize("bad", [None, 12, b"\x00\x01", object()])
def test_non_text_input_does_not_raise(bad):
    profile = extract_profile(bad)
    assert all(not field.found for field in profile.fields().values())


# --------------------------------------------------------------------------
# Decision 2: confidence is a real signal
# --------------------------------------------------------------------------


def test_confidence_reflects_how_the_value_was_found():
    labelled = extract_profile(_read("civil_engineer_india.txt"))
    positional = extract_profile(_read("accountant_canada.txt"))

    # "Name: Rohan Desai" is stated; "Marguerite Tremblay" is merely the first
    # line that looks like a name. Those are not equally good evidence.
    assert labelled.full_name.confidence == CONFIDENCE_HIGH
    assert positional.full_name.confidence == CONFIDENCE_LOW
    assert labelled.full_name.confidence > positional.full_name.confidence


def test_country_stated_literally_outranks_country_inferred_from_a_dial_code():
    stated = extract_profile(_read("nurse_ireland.txt"))
    inferred = extract_profile("Priya Raman\npriya.raman@example.com\n+353 21 555 0199\n")

    assert stated.country.value == "Ireland"
    assert stated.country.confidence == CONFIDENCE_HIGH
    assert inferred.country.value == "Ireland"
    assert inferred.country.confidence == CONFIDENCE_LOW
    assert stated.country.confidence > inferred.country.confidence


def test_phone_with_a_country_code_outranks_a_bare_local_number():
    with_code = extract_profile(_read("nurse_ireland.txt"))
    bare = extract_profile(_read("accountant_canada.txt"))

    assert with_code.phone.confidence == CONFIDENCE_HIGH
    assert bare.phone.confidence == CONFIDENCE_MEDIUM


def test_seniority_stated_in_a_title_outranks_seniority_counted_from_dates():
    stated = extract_profile(_read("nurse_ireland.txt"))
    counted = extract_profile(_read("accountant_canada.txt"))

    assert stated.seniority.value == "senior"
    assert stated.seniority.confidence == CONFIDENCE_HIGH

    # No rank word anywhere in "Management Accountant" / "Staff Accountant" —
    # this one is inferred from an eight-year span, which is weaker evidence.
    assert counted.seniority.value == "senior"
    assert counted.seniority.confidence == CONFIDENCE_MEDIUM


def test_confidence_is_not_one_constant_across_fields():
    """If every field came back with the same number, the number would be
    decoration rather than a signal."""
    observed = set()
    for name in _CORPUS:
        for field in extract_profile(_read(name)).fields().values():
            if field.found:
                observed.add(field.confidence)
    assert len(observed) >= 3, f"confidence barely varies: {sorted(observed)}"
    assert observed <= {CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW}


def test_confidence_bands_are_documented_in_the_module_docstring():
    module = importlib.import_module("jobfinder.resume.heuristic")
    doc = (module.__doc__ or "").lower()
    assert "confidence" in doc
    for band in ("high", "medium", "low"):
        assert band in doc, f"the {band} band is undocumented"


# --------------------------------------------------------------------------
# The ResumeData wrapper (parser.py already yields the text)
# --------------------------------------------------------------------------


def test_extract_from_resume_data(tmp_path):
    from jobfinder.resume.models import ResumeData
    from jobfinder.resume.parser import load_resume

    target = tmp_path / "resume.txt"
    target.write_text(_read("teacher_sparse.txt"), encoding="utf-8")

    resume = load_resume(str(target))
    assert isinstance(resume, ResumeData)

    profile = extract_profile_from_resume(resume)
    assert profile.full_name.value == "Daniel Okonkwo-Weber"
    assert profile.country.provenance == PROVENANCE_ABSENT


def test_job_titles_come_from_the_experience_section_in_order():
    profile = extract_profile(_read("accountant_canada.txt"))
    titles = list(profile.job_titles.value)
    assert titles[:2] == ["Management Accountant", "Staff Accountant"]
    # Company names and bullet text are not titles.
    assert "Laurentide Food Group" not in titles
    assert not any(t.startswith("Owned the monthly") for t in titles)


def test_skills_come_only_from_a_skills_section():
    """No document-wide keyword sweep: that is how a seed vocabulary starts
    deciding what counts as a skill."""
    no_section = extract_profile(
        "Lena Hartmann\nlena.hartmann@example.com\n\n"
        "EXPERIENCE\n\nPastry Chef\n2018 - 2024\n- Ran the dessert section.\n"
    )
    assert no_section.skills.value == ()
    assert no_section.skills.provenance == PROVENANCE_ABSENT
    assert no_section.job_titles.found
