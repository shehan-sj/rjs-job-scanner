"""Tests for jobfinder.derived_profile (T50) — the one derived-profile read model.

the product spec is the contract under test:

* three existing stores (``ResumeProfile``, ``FillerProfile``, ``user_extras``)
  unified behind **one** read surface, not joined by a fourth store;
* precedence **user edit > agentic > heuristic**, per field;
* provenance (``user`` / ``agentic`` / ``heuristic`` / ``absent``) on *every*
  field, because "a value the user cannot tell the origin of is a value they
  cannot judge";
* a re-parse **proposes**, it does not apply.

Plus the rule T45 established: a ``GET`` of the surface never
carries the resume's filesystem path. That is asserted structurally here —
the payload is built from the owner table's allow-list, so an unregistered
field cannot reach it — and pinned by a recursive sentinel scan and a
key-name guard that fires if a path-bearing field is ever registered.

The precedence tests are parametrised over the owner table rather than over a
representative field or two: the defect this task exists to prevent is a
*single* field quietly resolving the wrong way.
"""

from __future__ import annotations

import importlib
import pytest
import yaml
from fastapi.testclient import TestClient

from jobfinder import derived_profile as dp
from jobfinder.fillerbot.models import EducationEntry, FillerProfile, WorkEntry
from jobfinder.resume import heuristic as heur
from jobfinder.resume.profiler import ResumeProfile

PORT = 8771
BASE_URL = f"http://127.0.0.1:{PORT}"

# The four cache-metadata fields both agentic dataclasses carry. They are not
# derived data, and one of them is the resume's absolute path.
CACHE_FIELDS = frozenset({"source_path", "source_mtime", "generated_at", "profile_version"})

PATH_SENTINEL = "C:/secret/uploads/9f3a-resume.pdf"


# ── Layer builders ───────────────────────────────────────────────────────────


def _agentic_value(spec: dp.FieldSpec, tag: str):
    """A distinct, kind-correct value for one store, tagged so a resolution
    coming from the wrong store is visible in the assertion message."""
    if spec.kind == dp.KIND_INT:
        return 31 if tag == "resume" else 32
    if spec.kind == dp.KIND_STR_LIST:
        return [f"{spec.name}-{tag}-a", f"{spec.name}-{tag}-b"]
    if spec.kind == dp.KIND_ENTRY_LIST:
        if spec.name == "education":
            return [EducationEntry(institution=f"{tag}-university", degree="BSc")]
        return [WorkEntry(company=f"{tag}-corp", title=f"{tag}-title")]
    return f"{spec.name}-{tag}"


def _user_value(spec: dp.FieldSpec):
    if spec.kind == dp.KIND_INT:
        return 9
    if spec.kind == dp.KIND_STR_LIST:
        return [f"{spec.name}-user"]
    return f"{spec.name}-user"


def _full_resume_profile(tag: str = "resume") -> ResumeProfile:
    profile = ResumeProfile()
    for spec in dp.FIELD_SPECS:
        if dp.STORE_RESUME in spec.agentic:
            setattr(profile, spec.name, _agentic_value(spec, tag))
    profile.source_path = PATH_SENTINEL
    profile.source_mtime = 1.0
    profile.generated_at = "2026-10-21T00:00:00"
    profile.profile_version = "v9"
    return profile


def _full_filler_profile(tag: str = "filler") -> FillerProfile:
    profile = FillerProfile()
    for spec in dp.FIELD_SPECS:
        if dp.STORE_FILLER in spec.agentic:
            setattr(profile, spec.name, _agentic_value(spec, tag))
    profile.source_path = PATH_SENTINEL
    profile.source_mtime = 1.0
    profile.generated_at = "2026-10-21T00:00:00"
    profile.profile_version = "v9"
    return profile


def _hfound(value, method: str = "test") -> heur.DerivedField:
    return heur.DerivedField(
        value=value,
        provenance=heur.PROVENANCE_HEURISTIC,
        confidence=heur.CONFIDENCE_HIGH,
        method=method,
    )


def _heuristic(**overrides) -> heur.HeuristicProfile:
    base = {name: heur.DerivedField("", heur.PROVENANCE_ABSENT, 0.0, "") for name in heur.REQUIRED_FIELDS}
    for list_field in ("job_titles", "skills"):
        base[list_field] = heur.DerivedField((), heur.PROVENANCE_ABSENT, 0.0, "")
    base.update(overrides)
    return heur.HeuristicProfile(**base)


def _heuristic_value(spec: dp.FieldSpec):
    """A heuristic-layer value for a unified field, in the shape the heuristic
    extractor actually produces: strings and tuples only — ``years_experience``
    comes back as the *string* ``"11"`` (``heuristic._extract_seniority`` does
    ``_found(str(years), ...)``), and ``seniority`` is one scalar rank word
    feeding a list-valued unified field.
    """
    if spec.kind == dp.KIND_INT:
        return "11"
    if spec.kind == dp.KIND_STR_LIST and spec.heuristic == "seniority":
        return "senior"
    if spec.kind == dp.KIND_STR_LIST:
        return (f"{spec.name}-heuristic",)
    return f"{spec.name}-heuristic"


def _expected_heuristic_resolution(spec: dp.FieldSpec):
    if spec.kind == dp.KIND_INT:
        return 11
    if spec.kind == dp.KIND_STR_LIST and spec.heuristic == "seniority":
        return ["senior"]
    if spec.kind == dp.KIND_STR_LIST:
        return [f"{spec.name}-heuristic"]
    return f"{spec.name}-heuristic"


def _full_heuristic() -> heur.HeuristicProfile:
    overrides = {}
    for spec in dp.FIELD_SPECS:
        if spec.heuristic:
            overrides[spec.heuristic] = _hfound(_heuristic_value(spec))
    return _heuristic(**overrides)


HEURISTIC_BACKED = tuple(spec.name for spec in dp.FIELD_SPECS if spec.heuristic)


def _expected_user_resolution(spec: dp.FieldSpec):
    return _user_value(spec)


# ── The owner table itself ───────────────────────────────────────────────────


def test_the_owner_table_declares_every_field_once():
    names = [spec.name for spec in dp.FIELD_SPECS]
    assert len(names) == len(set(names)), "duplicate field in the owner table"
    assert dp.FIELD_NAMES == tuple(names)
    assert set(dp.FIELDS) == set(names)


def test_the_owner_table_covers_every_content_field_of_both_agentic_stores():
    """A field added to either agentic dataclass and *not* registered would
    silently vanish from the unified surface. Asserted in both directions so
    the table cannot drift from the stores it claims to unify.
    """
    for store, cls in ((dp.STORE_RESUME, ResumeProfile), (dp.STORE_FILLER, FillerProfile)):
        content = {name for name in cls.__dataclass_fields__ if name not in CACHE_FIELDS}
        registered = {spec.name for spec in dp.FIELD_SPECS if store in spec.agentic}
        assert registered == content, f"{store}: unregistered {content - registered}, stale {registered - content}"


def test_no_cache_metadata_field_is_registered():
    assert CACHE_FIELDS.isdisjoint(set(dp.FIELD_NAMES))


def test_the_overlapping_fields_declare_an_explicit_owner_order():
    """``skills`` and ``years_experience`` exist in *both* agentic stores and
    both are "agentic", so the product spec's three-layer rule is silent on which
    wins. The table must state it, and state it only for those two.
    """
    overlapping = {spec.name for spec in dp.FIELD_SPECS if len(spec.agentic) > 1}
    assert overlapping == {"skills", "years_experience"}
    for name in sorted(overlapping):
        spec = dp.FIELDS[name]
        assert spec.agentic == (dp.STORE_RESUME, dp.STORE_FILLER)
        assert spec.note, f"{name}: an overlap resolved without a stated reason is folklore"


def test_every_heuristic_field_is_mapped_into_the_table():
    mapped = {spec.heuristic for spec in dp.FIELD_SPECS if spec.heuristic}
    assert mapped == set(heur.REQUIRED_FIELDS)


def test_every_registered_field_declares_a_known_kind():
    for spec in dp.FIELD_SPECS:
        assert spec.kind in dp.KINDS, spec.name
        assert spec.label, spec.name


# ── Precedence: user edit wins ───────────────────────────────────────────────


@pytest.mark.parametrize("name", dp.USER_EDITABLE_FIELDS)
def test_a_user_edit_wins_over_a_disagreeing_agentic_extraction(name):
    spec = dp.FIELDS[name]
    resolved = dp.resolve(
        user_edits={name: _user_value(spec)},
        resume_profile=_full_resume_profile(),
        filler_profile=_full_filler_profile(),
        heuristic=_full_heuristic(),
    )[name]

    assert resolved.value == _expected_user_resolution(spec)
    assert resolved.provenance == dp.PROVENANCE_USER
    assert resolved.store == dp.STORE_USER


@pytest.mark.parametrize("name", dp.USER_EDITABLE_FIELDS)
def test_a_user_edit_survives_a_reparse_that_disagrees_with_it(name):
    """A re-parse replaces the agentic stores wholesale. The user's value is
    resolved from the user layer at read time, so there is no sequence of
    re-parses that can reach it — the trapdoor the product spec rejects.
    """
    spec = dp.FIELDS[name]
    edits = {name: _user_value(spec)}

    before = dp.resolve(
        user_edits=edits,
        resume_profile=_full_resume_profile("first-parse"),
        filler_profile=_full_filler_profile("first-parse"),
        heuristic=_full_heuristic(),
    )[name]
    after = dp.resolve(
        user_edits=edits,
        resume_profile=_full_resume_profile("re-parse"),
        filler_profile=_full_filler_profile("re-parse"),
        heuristic=_full_heuristic(),
    )[name]

    assert before.value == after.value == _expected_user_resolution(spec)
    assert after.provenance == dp.PROVENANCE_USER


def test_every_field_resolves_to_the_user_layer_when_all_three_disagree_at_once():
    """The per-field cases above vary one field at a time; this one loads all
    three layers in full conflict, so a resolution that depends on the *other*
    fields' states cannot hide.
    """
    edits = {name: _user_value(dp.FIELDS[name]) for name in dp.USER_EDITABLE_FIELDS}
    profile = dp.resolve(
        user_edits=edits,
        resume_profile=_full_resume_profile(),
        filler_profile=_full_filler_profile(),
        heuristic=_full_heuristic(),
    )
    for name in dp.USER_EDITABLE_FIELDS:
        assert profile[name].provenance == dp.PROVENANCE_USER, name
        assert profile[name].value == _expected_user_resolution(dp.FIELDS[name]), name


# ── Precedence: agentic beats heuristic ──────────────────────────────────────


@pytest.mark.parametrize("name", HEURISTIC_BACKED)
def test_agentic_extraction_wins_over_the_heuristic_layer(name):
    spec = dp.FIELDS[name]
    resolved = dp.resolve(
        resume_profile=_full_resume_profile(),
        filler_profile=_full_filler_profile(),
        heuristic=_full_heuristic(),
    )[name]

    expected_store = spec.agentic[0]
    tag = "resume" if expected_store == dp.STORE_RESUME else "filler"
    assert resolved.value == dp.normalise(spec, _agentic_value(spec, tag))
    assert resolved.provenance == dp.PROVENANCE_AGENTIC
    assert resolved.store == expected_store


@pytest.mark.parametrize("name", HEURISTIC_BACKED)
def test_the_heuristic_layer_applies_when_no_agentic_layer_exists(name):
    """The no-key path: both agentic stores absent (no API key, nothing
    cached), so the lowest-precedence layer is what the user sees — and it is
    labelled ``heuristic``, never presented as equivalent.
    """
    spec = dp.FIELDS[name]
    resolved = dp.resolve(heuristic=_full_heuristic())[name]

    assert resolved.value == _expected_heuristic_resolution(spec)
    assert resolved.provenance == dp.PROVENANCE_HEURISTIC
    assert resolved.store == dp.STORE_HEURISTIC
    assert resolved.confidence > 0
    assert resolved.method


@pytest.mark.parametrize("name", HEURISTIC_BACKED)
def test_the_heuristic_layer_applies_per_field_not_per_profile(name):
    """An agentic store that extracted *some* fields must not suppress the
    heuristic layer for the ones it left empty. Precedence is per field.
    """
    spec = dp.FIELDS[name]
    resolved = dp.resolve(
        resume_profile=ResumeProfile(),
        filler_profile=FillerProfile(),
        heuristic=_full_heuristic(),
    )[name]
    assert resolved.provenance == dp.PROVENANCE_HEURISTIC, spec.name


# ── Provenance on every field ────────────────────────────────────────────────


@pytest.mark.parametrize("name", dp.FIELD_NAMES)
def test_provenance_is_reported_for_every_field_in_the_read_model(name):
    for profile in (
        dp.resolve(),
        dp.resolve(heuristic=_full_heuristic()),
        dp.resolve(resume_profile=_full_resume_profile(), filler_profile=_full_filler_profile()),
        dp.resolve(user_edits={n: _user_value(dp.FIELDS[n]) for n in dp.USER_EDITABLE_FIELDS}),
    ):
        resolved = profile[name]
        assert resolved.name == name
        assert resolved.provenance in dp.PROVENANCE_VALUES


@pytest.mark.parametrize("name", dp.FIELD_NAMES)
def test_a_field_no_layer_supplies_is_absent_and_empty(name):
    """``absent`` is not a kind of heuristic (T49's rule, carried up a layer):
    an empty value with provenance ``absent``, never a plausible-looking guess.
    """
    resolved = dp.resolve()[name]
    assert resolved.provenance == dp.PROVENANCE_ABSENT
    assert resolved.store == ""
    assert resolved.confidence == 0.0
    assert not resolved.value
    assert resolved.value == dp.empty_value(dp.FIELDS[name])


# ── Overlapping agentic stores ───────────────────────────────────────────────


@pytest.mark.parametrize("name", ["skills", "years_experience"])
def test_the_declared_owner_wins_when_both_agentic_stores_disagree(name):
    spec = dp.FIELDS[name]
    resolved = dp.resolve(
        resume_profile=_full_resume_profile(),
        filler_profile=_full_filler_profile(),
    )[name]
    assert resolved.store == dp.STORE_RESUME
    assert resolved.value == dp.normalise(spec, _agentic_value(spec, "resume"))


@pytest.mark.parametrize("name", ["skills", "years_experience"])
def test_the_second_agentic_store_is_used_when_the_owner_is_empty(name):
    """The order is a fallback chain, not a single owner: a cached
    ``FillerProfile`` with no ``ResumeProfile`` beside it still resolves.
    """
    spec = dp.FIELDS[name]
    resolved = dp.resolve(
        resume_profile=ResumeProfile(),
        filler_profile=_full_filler_profile(),
    )[name]
    assert resolved.store == dp.STORE_FILLER
    assert resolved.value == dp.normalise(spec, _agentic_value(spec, "filler"))


# ── Residuals, pinned rather than guessed at ─────────────────────────────────


def test_a_user_edit_equal_to_the_agentic_value_still_reports_user():
    """Deliberate: provenance answers "where does the value I am looking at
    come from", and a stored edit is where it comes from even when the
    extraction happens to agree. Reporting ``agentic`` would mean a re-parse
    could appear free to change it.
    """
    resolved = dp.resolve(
        user_edits={"city": "Lisbon"},
        filler_profile=FillerProfile(city="Lisbon"),
    )["city"]
    assert resolved.provenance == dp.PROVENANCE_USER


def test_a_cleared_user_edit_falls_back_rather_than_resolving_to_an_empty_user_value():
    """Clearing is how a field gets reset (PATCH it to ""), so an empty user
    value must not win — it would pin the field blank forever.
    """
    resolved = dp.resolve(
        user_edits={"city": "", "job_titles": []},
        filler_profile=FillerProfile(city="Porto"),
        resume_profile=ResumeProfile(job_titles=["Nurse"]),
    )
    assert resolved["city"].value == "Porto"
    assert resolved["city"].provenance == dp.PROVENANCE_AGENTIC
    assert resolved["job_titles"].value == ["Nurse"]


def test_zero_years_experience_is_indistinguishable_from_unstated_in_the_agentic_stores():
    """A known residual. ``years_experience`` is an ``int`` defaulting to 0 in
    both agentic dataclasses, so a genuine zero cannot be told from "the
    extraction did not populate it" and resolves as ``absent``. A user edit
    can state it, which is the available escape hatch; fixing it properly
    needs the agentic stores to carry optionality (not this task's scope).
    """
    resolved = dp.resolve(resume_profile=ResumeProfile(years_experience=0))["years_experience"]
    assert resolved.provenance == dp.PROVENANCE_ABSENT

    edited = dp.resolve(user_edits={"years_experience": 0})["years_experience"]
    assert edited.provenance == dp.PROVENANCE_ABSENT, (
        "same residual on the user layer — pinned, not silently special-cased"
    )


def test_the_structured_history_fields_are_not_user_editable_yet():
    """``work_experience`` and ``education`` are lists of nested entries; an
    edit surface for them is T54's problem. They are still *reported* with
    provenance, so nothing is invisible — they are just read-only for now.
    """
    assert set(dp.FIELD_NAMES) - set(dp.USER_EDITABLE_FIELDS) == {"work_experience", "education"}
    for name in ("work_experience", "education"):
        assert dp.FIELDS[name].kind == dp.KIND_ENTRY_LIST
        assert name not in dp.FIELD_SHAPES


def test_the_heuristic_seniority_scalar_widens_into_the_list_valued_unified_field():
    profile = dp.resolve(heuristic=_heuristic(seniority=_hfound("senior")))
    assert profile["seniority_levels"].value == ["senior"]
    assert profile["seniority_levels"].provenance == dp.PROVENANCE_HEURISTIC


def test_the_heuristic_years_string_is_coerced_to_the_unified_int():
    profile = dp.resolve(heuristic=_heuristic(years_experience=_hfound("7")))
    assert profile["years_experience"].value == 7
    assert isinstance(profile["years_experience"].value, int)


def test_a_non_numeric_heuristic_years_value_is_absent_rather_than_crashing():
    profile = dp.resolve(heuristic=_heuristic(years_experience=_hfound("about ten")))
    assert profile["years_experience"].provenance == dp.PROVENANCE_ABSENT


# ── A re-parse proposes, it does not apply ───────────────────────────────────


def test_a_reparse_proposes_changes_and_applies_none_of_them():
    """``propose`` needs the profile the user is looking at *and* the layers a
    re-parse just produced. Comparing a new extraction against a resolution
    of itself can only ever surface the fields a user edit blocks, which
    would make T54's accept-per-field UI impossible to build on this.
    """
    edits = {"country": "Portugal", "job_titles": ["Staff Nurse"]}
    current = dp.resolve(
        user_edits=edits,
        resume_profile=ResumeProfile(job_titles=["Nurse"], domains=["Nursing"], tools=["Pyxis"]),
        filler_profile=FillerProfile(country="Ireland", city="Cork"),
    )
    proposals = {
        p.name: p
        for p in dp.propose(
            current,
            resume_profile=ResumeProfile(job_titles=["Nurse Practitioner"], domains=["Healthcare"], tools=["Pyxis"]),
            filler_profile=FillerProfile(country="Spain", city="Cork"),
        )
    }

    # Conflicts with a user edit are surfaced, both values visible, neither applied.
    assert proposals["country"].blocked_by_user_edit is True
    assert proposals["country"].current_value == "Portugal"
    assert proposals["country"].proposed_value == "Spain"
    assert proposals["job_titles"].blocked_by_user_edit is True
    assert proposals["job_titles"].current_value == ["Staff Nurse"]
    assert proposals["job_titles"].proposed_value == ["Nurse Practitioner"]

    # A field the user never touched is an ordinary, unblocked proposal.
    assert proposals["domains"].blocked_by_user_edit is False
    assert proposals["domains"].current_value == ["Nursing"]
    assert proposals["domains"].proposed_value == ["Healthcare"]

    # Only fields the re-parse would actually change are proposed at all.
    assert "city" not in proposals
    assert "tools" not in proposals
    assert "summary" not in proposals

    # And nothing was applied: the same layers still resolve the old way.
    assert current["country"].value == "Portugal"
    assert current["domains"].value == ["Nursing"]


def test_propose_never_writes_to_the_user_layer(extras_isolated):
    dp.save_user_edits({"country": "Portugal"})
    before = _extras_path().read_bytes()

    current = dp.resolve(user_edits=dp.user_edits(), filler_profile=FillerProfile(country="Ireland"))
    dp.propose(
        current,
        resume_profile=ResumeProfile(job_titles=["Nurse"]),
        filler_profile=FillerProfile(country="Ireland"),
        heuristic=_full_heuristic(),
    )

    assert _extras_path().read_bytes() == before
    assert dp.user_edits() == {"country": "Portugal"}
    assert (
        dp.resolve(user_edits=dp.user_edits(), filler_profile=FillerProfile(country="Ireland"))["country"].value
        == "Portugal"
    )


def test_resolve_cannot_be_handed_a_way_to_apply_a_reparse_over_a_user_edit():
    """There is deliberately no ``apply``/``force`` lever on the read model:
    the only way a derived value reaches a user-edited field is for the user
    layer to be written, which is a separate, explicit call.
    """
    assert not hasattr(dp, "apply_proposals")
    assert "force" not in dp.resolve.__code__.co_varnames


# ── The GET surface never carries the resume's path ──────────────────────────


def _walk(node, keys=None, values=None):
    keys = [] if keys is None else keys
    values = [] if values is None else values
    if isinstance(node, dict):
        for key, value in node.items():
            keys.append(key)
            _walk(value, keys, values)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _walk(item, keys, values)
    else:
        values.append(node)
    return keys, values


def _loaded_payload() -> dict:
    return dp.resolve(
        user_edits={"city": "Lisbon"},
        resume_profile=_full_resume_profile(),
        filler_profile=_full_filler_profile(),
        heuristic=_full_heuristic(),
    ).to_payload()


def test_the_payload_never_contains_the_resume_filesystem_path():
    _keys, values = _walk(_loaded_payload())
    for value in values:
        assert PATH_SENTINEL not in str(value)


def test_the_payload_exposes_exactly_the_owner_tables_allow_list():
    """Exposure is an allow-list built from the table, not a deny-list of
    fields to hide: an unregistered field on either agentic dataclass cannot
    reach the payload at all, so the rule does not rot when one is added.
    """
    payload = _loaded_payload()
    assert set(payload["fields"]) == set(dp.FIELD_NAMES)


def test_no_key_anywhere_in_the_payload_is_path_bearing():
    """The pin that fires if a path-bearing field is ever *registered* — the
    failure mode the allow-list alone does not catch.
    """
    keys, _values = _walk(_loaded_payload())
    for key in keys:
        lowered = key.lower()
        for banned in ("path", "filename", "filepath", "dir", "mtime", "source_"):
            assert banned not in lowered, f"{key} looks like a filesystem reference"


def test_an_unregistered_path_bearing_agentic_field_cannot_reach_the_payload():
    """Simulates the drift the allow-list exists to survive: a field added to
    an agentic store and not registered.
    """
    profile = _full_filler_profile()
    profile.resume_storage_path = PATH_SENTINEL  # type: ignore[attr-defined]
    _keys, values = _walk(dp.resolve(filler_profile=profile).to_payload())
    for value in values:
        assert PATH_SENTINEL not in str(value)


# ── user_extras: non-string values, and the 9 string fields unchanged ────────


def _extras_path():
    from jobfinder.paths import user_extras_path

    return user_extras_path()


def _isolate_extras(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))
    import jobfinder.paths as paths

    importlib.reload(paths)
    return paths


@pytest.fixture
def extras_isolated(tmp_path, monkeypatch):
    _isolate_extras(tmp_path, monkeypatch)
    yield
    import jobfinder.paths as paths

    importlib.reload(paths)


LEGACY_NINE = {
    "address": "12 Rua Augusta",
    "city": "Lisbon",
    "state_province": "Lisboa",
    "zip_postal": "1100-048",
    "country": "Portugal",
    "phone": "+351 000 000 000",
    "email": "jane@example.com",
    "linkedin_url": "https://linkedin.com/in/jane",
    "website": "https://jane.example.com",
}


def test_the_nine_existing_string_fields_round_trip_byte_identically(extras_isolated):
    """The storage file, the field names and the stored shape of the 9
    original contact fields are unchanged — same file, more keys, not a
    fourth store.

    Asserted three ways, because "keeps working unchanged" is the clause most
    easily broken by accident: the exact bytes on disk, identical bytes with
    and without the new ``shapes`` argument, and a read back through both the
    new and the legacy code paths.
    """
    from jobfinder.fillerbot import user_extras

    user_extras.save_extras(dict(LEGACY_NINE), shapes=dp.FIELD_SHAPES)
    with_shapes = _extras_path().read_bytes()

    # yaml.dump through a text-mode handle, which is what the module has
    # always used — on Windows that means CRLF, and changing it would rewrite
    # every existing user's file.
    expected = yaml.dump(dict(LEGACY_NINE), default_flow_style=False, allow_unicode=True)
    assert _extras_path().read_text(encoding="utf-8") == expected

    user_extras.save_extras(dict(LEGACY_NINE))
    assert _extras_path().read_bytes() == with_shapes, "the shapes argument changed the stored bytes"

    assert user_extras.load_extras(shapes=dp.FIELD_SHAPES) == LEGACY_NINE
    # And identical through the pre-change, string-only read path.
    assert user_extras.load_extras() == LEGACY_NINE


def test_a_list_valued_matching_field_round_trips_as_a_list(extras_isolated):
    """Before this task ``load_extras`` did ``str(v)`` on every value, so a
    list came back as the literal string ``"['a', 'b']"``.
    """
    from jobfinder.fillerbot import user_extras

    user_extras.save_extras({"job_titles": ["Staff Nurse", "Charge Nurse"]}, shapes=dp.FIELD_SHAPES)
    loaded = user_extras.load_extras(shapes=dp.FIELD_SHAPES)
    assert loaded["job_titles"] == ["Staff Nurse", "Charge Nurse"]


def test_saving_a_list_does_not_raise(extras_isolated):
    """Before this task ``save_extras`` did ``v.strip()`` on every value,
    which raises ``AttributeError`` on a list.
    """
    from jobfinder.fillerbot import user_extras

    user_extras.save_extras({"skills": ["Python"]}, shapes=dp.FIELD_SHAPES)
    assert user_extras.load_extras(shapes=dp.FIELD_SHAPES) == {"skills": ["Python"]}


def test_an_int_valued_field_round_trips_as_an_int(extras_isolated):
    from jobfinder.fillerbot import user_extras

    user_extras.save_extras({"years_experience": 11}, shapes=dp.FIELD_SHAPES)
    assert user_extras.load_extras(shapes=dp.FIELD_SHAPES) == {"years_experience": 11}


def test_empty_list_entries_and_empty_lists_are_dropped_on_save(extras_isolated):
    from jobfinder.fillerbot import user_extras

    user_extras.save_extras(
        {"skills": ["  Python  ", "", "   "], "tools": [], "domains": ["", ""]},
        shapes=dp.FIELD_SHAPES,
    )
    assert user_extras.load_extras(shapes=dp.FIELD_SHAPES) == {"skills": ["Python"]}


def test_a_hand_edited_scalar_in_a_list_field_is_coerced_not_stringified(extras_isolated):
    """``data/user_extras.yaml`` is a plain file a user can edit. ``skills:
    Python`` is the obvious thing to write there.
    """
    from jobfinder.fillerbot import user_extras

    _extras_path().parent.mkdir(parents=True, exist_ok=True)
    _extras_path().write_text("skills: Python\ncity: Lisbon\n", encoding="utf-8")
    assert user_extras.load_extras(shapes=dp.FIELD_SHAPES) == {"skills": ["Python"], "city": "Lisbon"}


def test_a_structurally_wrong_stored_value_is_dropped_rather_than_crashing(extras_isolated):
    from jobfinder.fillerbot import user_extras

    _extras_path().parent.mkdir(parents=True, exist_ok=True)
    _extras_path().write_text("skills:\n  a: 1\nyears_experience: lots\ncity: Lisbon\n", encoding="utf-8")
    assert user_extras.load_extras(shapes=dp.FIELD_SHAPES) == {"city": "Lisbon"}


def test_an_unknown_stored_key_keeps_its_legacy_string_coercion(extras_isolated):
    from jobfinder.fillerbot import user_extras

    _extras_path().parent.mkdir(parents=True, exist_ok=True)
    _extras_path().write_text("something_old: 12\n", encoding="utf-8")
    assert user_extras.load_extras(shapes=dp.FIELD_SHAPES) == {"something_old": "12"}


# ── The user-edit layer, through the read model ──────────────────────────────


@pytest.mark.parametrize("name", dp.USER_EDITABLE_FIELDS)
def test_every_user_editable_field_persists_and_then_wins(name, extras_isolated):
    spec = dp.FIELDS[name]
    value = _user_value(spec)
    dp.save_user_edits({name: value})

    resolved = dp.resolve(
        user_edits=dp.user_edits(),
        resume_profile=_full_resume_profile(),
        filler_profile=_full_filler_profile(),
        heuristic=_full_heuristic(),
    )[name]
    assert resolved.value == _expected_user_resolution(spec)
    assert resolved.provenance == dp.PROVENANCE_USER


def test_save_user_edits_merges_rather_than_replacing(extras_isolated):
    dp.save_user_edits({"city": "Lisbon"})
    dp.save_user_edits({"skills": ["Python"]})
    assert dp.user_edits() == {"city": "Lisbon", "skills": ["Python"]}


def test_save_user_edits_rejects_a_field_outside_the_owner_table(extras_isolated):
    with pytest.raises(ValueError, match="unknown"):
        dp.save_user_edits({"source_path": PATH_SENTINEL})
    with pytest.raises(ValueError, match="unknown"):
        dp.save_user_edits({"work_experience": []})


def test_save_user_edits_rejects_a_wrongly_typed_value(extras_isolated):
    with pytest.raises(ValueError):
        dp.save_user_edits({"city": ["Lisbon"]})
    with pytest.raises(ValueError):
        dp.save_user_edits({"skills": "Python"})
    with pytest.raises(ValueError):
        dp.save_user_edits({"years_experience": "eleven"})


def test_save_user_edits_bounds_values_the_way_the_extras_endpoint_does(extras_isolated):
    """These values reach Anthropic prompts and a YAML file, so
    the bounds T31 put on the 9 contact fields apply to the new ones too.
    """
    with pytest.raises(ValueError, match="characters"):
        dp.save_user_edits({"summary": "x" * (dp.MAX_EDIT_VALUE_LEN + 1)})
    with pytest.raises(ValueError, match="control characters"):
        dp.save_user_edits({"city": "Lis\x00bon"})
    with pytest.raises(ValueError, match="characters"):
        dp.save_user_edits({"skills": ["x" * (dp.MAX_EDIT_VALUE_LEN + 1)]})
    with pytest.raises(ValueError, match="items"):
        dp.save_user_edits({"skills": [f"s{i}" for i in range(dp.MAX_EDIT_LIST_ITEMS + 1)]})


def test_a_user_edit_is_trimmed_and_deduplicated(extras_isolated):
    dp.save_user_edits({"city": "  Lisbon  ", "skills": ["Python", " Python ", "SQL", ""]})
    assert dp.user_edits() == {"city": "Lisbon", "skills": ["Python", "SQL"]}


# ── The behaviour change: FillerBot gets the corrected value ─────────────────


def test_the_user_layer_overwrites_a_non_empty_profile_value_for_the_engine():
    """Deliberate behaviour change. ``merge_into_profile`` filled only *empty*
    fields, which is gap-filling and the opposite of the product spec: if the user
    corrected their phone number, the form must get the corrected one.
    """
    profile = FillerProfile(phone="+1 555 0100", email="old@example.com", city="")
    dp.overlay_user_values(profile, {"phone": "+351 000 000", "city": "Lisbon"})

    assert profile.phone == "+351 000 000"
    assert profile.city == "Lisbon"
    assert profile.email == "old@example.com"


def test_the_overlay_ignores_fields_the_filler_profile_does_not_have():
    profile = FillerProfile()
    dp.overlay_user_values(profile, {"job_titles": ["Nurse"], "city": "Lisbon"})
    assert profile.city == "Lisbon"
    assert not hasattr(profile, "job_titles")


def test_the_overlay_never_writes_cache_metadata():
    profile = FillerProfile(source_path="/real/path.pdf")
    dp.overlay_user_values(profile, {"source_path": PATH_SENTINEL})
    assert profile.source_path == "/real/path.pdf"


# ── GET /api/resume/derived-profile ──────────────────────────────────────────


# A plain-text resume, not a .docx: ``_sniff_resume_type`` rejects zip-shaped
# uploads on purpose (``resume/parser.py`` has no .docx parser, only
# ``_parse_pdf`` and a text fallback), so a .docx fixture here would 400 and
# the heuristic layer would see nothing.
def _upload_resume(client, text: str):
    return client.post("/api/resume", files={"file": ("cv.txt", text.encode("utf-8"), "text/plain")})


RESUME_TEXT = "\n".join(
    [
        "Amara Okonkwo-Weber",
        "amara@example.com | +351 912 000 000",
        "Lisbon, Portugal",
        "",
        "EXPERIENCE",
        "Senior Staff Nurse",
        "Hospital de Santa Maria",
        "2019 - 2026",
        "",
        "Charge Nurse",
        "Clinica do Norte",
        "2015 - 2019",
        "",
        "SKILLS",
        "Triage, Paediatrics, Phlebotomy",
    ]
)


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))

    import jobfinder.paths as paths

    importlib.reload(paths)

    import jobfinder.web.app as web_app

    importlib.reload(web_app)

    yield web_app

    importlib.reload(paths)


@pytest.fixture
def authed_client(isolated_app):
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
        assert resp.status_code == 200, resp.text
        yield app, c


def test_the_derived_profile_get_reports_provenance_for_every_field(authed_client):
    _app, c = authed_client
    resp = c.get("/api/resume/derived-profile")
    assert resp.status_code == 200, resp.text
    fields = resp.json()["fields"]
    assert set(fields) == set(dp.FIELD_NAMES)
    for name, field in fields.items():
        assert field["provenance"] in dp.PROVENANCE_VALUES, name


def test_the_derived_profile_get_never_returns_the_resume_filesystem_path(authed_client):
    app, c = authed_client
    upload = _upload_resume(c, RESUME_TEXT)
    assert upload.status_code == 200, upload.text
    stored = app.state.config.resume.path
    assert stored, "the upload should have set a stored path"

    body = c.get("/api/resume/derived-profile")
    assert body.status_code == 200, body.text
    text = body.text
    for fragment in (stored, stored.replace("\\", "/"), stored.replace("\\", "\\\\")):
        assert fragment not in text
    keys, _values = _walk(body.json())
    for key in keys:
        assert "path" not in key.lower()


def test_the_derived_profile_get_works_with_no_api_key_and_says_it_is_heuristic(authed_client):
    """The product spec: the no-key path is exactly when pre-filling matters most,
    and it must never be presented as equivalent to the agentic one.
    """
    _app, c = authed_client
    assert _upload_resume(c, RESUME_TEXT).status_code == 200

    payload = c.get("/api/resume/derived-profile").json()
    assert payload["fields"]["email"]["provenance"] == dp.PROVENANCE_HEURISTIC
    assert payload["fields"]["email"]["value"] == "amara@example.com"
    assert payload["fields"]["country"]["provenance"] == dp.PROVENANCE_HEURISTIC
    assert payload["fields"]["email"]["method"]


def test_the_derived_profile_get_requires_the_launch_token(isolated_app):
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        assert c.get("/api/resume/derived-profile").status_code == 401


def test_the_derived_profile_get_is_usable_with_no_resume_on_file(authed_client):
    _app, c = authed_client
    payload = c.get("/api/resume/derived-profile").json()
    assert payload["resume_on_file"] is False
    assert all(f["provenance"] == dp.PROVENANCE_ABSENT for f in payload["fields"].values())


def test_a_user_edit_shows_through_the_get_over_a_disagreeing_heuristic_value(authed_client):
    _app, c = authed_client
    assert _upload_resume(c, RESUME_TEXT).status_code == 200
    dp.save_user_edits({"email": "corrected@example.com"})

    field = c.get("/api/resume/derived-profile").json()["fields"]["email"]
    assert field["value"] == "corrected@example.com"
    assert field["provenance"] == dp.PROVENANCE_USER


def test_a_matching_field_edit_is_not_corrupted_by_the_contact_extras_patch(authed_client, monkeypatch):
    """The trap in extending the same YAML file: ``PATCH /profile/extras``
    does ``{**load_extras(), **values}`` then ``save_extras(merged)``. With
    string-only coercion that read-modify-write turns a stored ``skills``
    list into the string ``"['Python']"`` — a user edit destroyed by editing
    an unrelated field.
    """
    app, c = authed_client
    assert _upload_resume(c, RESUME_TEXT).status_code == 200
    # ``anthropic_api_key`` is a read-only property over config + env, and
    # the extras endpoint is gated on it.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app.state.config.fillerbot.enabled = True
    # The preflight this endpoint returns loads the agentic FillerProfile,
    # which would otherwise be a real, billed Anthropic call.
    import jobfinder.web.fillerbot as web_fillerbot

    monkeypatch.setattr(
        web_fillerbot,
        "load_filler_profile",
        lambda *_args, **_kwargs: FillerProfile(full_name="Amara Okonkwo-Weber", city="Porto"),
    )
    dp.save_user_edits({"skills": ["Triage", "Paediatrics"], "job_titles": ["Staff Nurse"]})

    resp = c.patch("/api/fillerbot/profile/extras", json={"values": {"city": "Lisbon"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["extras"] == {"city": "Lisbon"}, "the dialog's echo stays scoped to the promptable 9"

    stored = dp.user_edits()
    assert stored["skills"] == ["Triage", "Paediatrics"]
    assert stored["job_titles"] == ["Staff Nurse"]
    assert stored["city"] == "Lisbon"


# ── Cases a resolution that merely looks right would get wrong ───────────────


def test_the_two_modules_kind_constants_agree():
    """``user_extras`` declares the kind strings itself rather than importing
    them, to keep the read model's dependency one-directional (the read model
    imports it, not the other way round). This is the assertion that stops the
    two copies drifting.
    """
    from jobfinder.fillerbot import user_extras

    assert user_extras._KIND_INT == dp.KIND_INT
    assert user_extras._KIND_STR_LIST == dp.KIND_STR_LIST


def test_an_agentic_value_that_is_only_blanks_falls_through_to_the_heuristic():
    """An extraction that produced ``[""]`` or ``"   "`` has not produced a
    value, and must not outrank a heuristic layer that actually found one.
    """
    resolved = dp.resolve(
        resume_profile=ResumeProfile(skills=["", "  "]),
        filler_profile=FillerProfile(country="   "),
        heuristic=_heuristic(skills=_hfound(("Triage",)), country=_hfound("Portugal")),
    )
    assert resolved["skills"].value == ["Triage"]
    assert resolved["skills"].provenance == dp.PROVENANCE_HEURISTIC
    assert resolved["country"].value == "Portugal"
    assert resolved["country"].provenance == dp.PROVENANCE_HEURISTIC


def test_a_whitespace_only_heuristic_value_is_absent_not_a_blank_heuristic_field():
    resolved = dp.resolve(heuristic=_heuristic(country=_hfound("   ")))["country"]
    assert resolved.provenance == dp.PROVENANCE_ABSENT
    assert resolved.value == ""


def test_a_blank_user_edit_does_not_outrank_a_real_agentic_value_per_kind():
    """The empty shapes differ by kind, and each of them must fall through."""
    resolved = dp.resolve(
        user_edits={"city": "   ", "skills": ["", "  "], "years_experience": 0},
        resume_profile=ResumeProfile(skills=["Triage"], years_experience=12),
        filler_profile=FillerProfile(city="Porto"),
    )
    assert (resolved["city"].value, resolved["city"].provenance) == ("Porto", dp.PROVENANCE_AGENTIC)
    assert (resolved["skills"].value, resolved["skills"].provenance) == (["Triage"], dp.PROVENANCE_AGENTIC)
    assert resolved["years_experience"].value == 12


def test_a_stored_value_for_a_non_user_editable_field_takes_no_part_in_resolution():
    """``work_experience`` is not user-editable, so a key of that name in the
    YAML file — hand-added, or left by some future version — must not be able
    to override the agentic history.
    """
    entries = [WorkEntry(company="Hospital de Santa Maria", title="Staff Nurse")]
    resolved = dp.resolve(
        user_edits={"work_experience": [WorkEntry(company="Injected Ltd")]},
        filler_profile=FillerProfile(work_experience=entries),
    )["work_experience"]
    assert resolved.value == entries
    assert resolved.provenance == dp.PROVENANCE_AGENTIC


def test_a_reparse_that_agrees_with_the_user_edit_proposes_nothing():
    """Nothing to accept, so nothing to show. A ``propose`` that compared
    *provenance* instead of values would raise a pointless conflict here.
    """
    current = dp.resolve(user_edits={"country": "Portugal"}, filler_profile=FillerProfile(country="Ireland"))
    proposals = dp.propose(current, filler_profile=FillerProfile(country="Portugal"))
    assert [p.name for p in proposals] == []


def test_a_reparse_that_loses_a_value_is_proposed_as_a_removal():
    """A field going blank is a change the user gets to decline, not a silent
    one — and not one that is applied either.
    """
    current = dp.resolve(resume_profile=ResumeProfile(domains=["Healthcare"]))
    proposals = {p.name: p for p in dp.propose(current, resume_profile=ResumeProfile())}
    assert proposals["domains"].current_value == ["Healthcare"]
    assert proposals["domains"].proposed_value == []
    assert proposals["domains"].proposed_provenance == dp.PROVENANCE_ABSENT
    assert current["domains"].value == ["Healthcare"]


def test_a_reparse_cannot_reach_a_user_edit_through_either_agentic_store():
    """The overlap fields have two agentic stores, so a re-parse has two ways
    to try to reach them. Neither does.
    """
    edits = {"skills": ["Triage"], "years_experience": 9}
    for resume, filler in (
        (ResumeProfile(skills=["Selenium"], years_experience=3), FillerProfile()),
        (ResumeProfile(), FillerProfile(skills=["Selenium"], years_experience=3)),
        (ResumeProfile(skills=["A"], years_experience=1), FillerProfile(skills=["B"], years_experience=2)),
    ):
        resolved = dp.resolve(user_edits=edits, resume_profile=resume, filler_profile=filler)
        assert resolved["skills"].value == ["Triage"]
        assert resolved["years_experience"].value == 9
        assert resolved["skills"].provenance == dp.PROVENANCE_USER


def test_a_matching_field_edit_reaches_the_form_filling_profile():
    """``skills`` and ``years_experience`` live on the FillerProfile too, so a
    correction made for matching also improves what FillerBot types.
    """
    profile = FillerProfile(skills=["Selenium"], years_experience=3, full_name="Old Name")
    dp.overlay_user_values(profile, {"skills": ["Triage"], "years_experience": 9, "full_name": "Amara"})
    assert profile.skills == ["Triage"]
    assert profile.years_experience == 9
    assert profile.full_name == "Amara"


def test_the_resolved_list_value_is_not_the_stores_own_list():
    """Mutating what the read model handed back must not reach into a store."""
    store = ResumeProfile(job_titles=["Nurse"])
    resolved = dp.resolve(resume_profile=store)["job_titles"].value
    resolved.append("Injected")
    assert store.job_titles == ["Nurse"]


def test_the_name_parts_have_no_heuristic_backing_on_purpose():
    """``full_name`` is heuristic-backed; ``first_name``/``last_name`` are not.
    Splitting a full name is a guess about word order and particles that is
    wrong for a large share of real names, and T49's rule is that a blank the
    user can fill in beats a guess they cannot see.
    """
    assert dp.FIELDS["full_name"].heuristic == "full_name"
    assert dp.FIELDS["first_name"].heuristic is None
    assert dp.FIELDS["last_name"].heuristic is None

    resolved = dp.resolve(heuristic=_heuristic(full_name=_hfound("Amara Okonkwo-Weber")))
    assert resolved["full_name"].value == "Amara Okonkwo-Weber"
    assert resolved["first_name"].provenance == dp.PROVENANCE_ABSENT


def test_the_read_model_imports_with_the_anthropic_sdk_unavailable(monkeypatch):
    """``scripts/smoke.py`` step 1 imports every module with PySide6 blocked,
    and ``matcher/claude_matcher.py``/``fillerbot/vision.py`` still import
    ``anthropic`` at module scope — so the read model must reach the profilers
    without touching either.
    """
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("blocked for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    importlib.reload(importlib.import_module("jobfinder.derived_profile"))


def test_the_heuristic_layer_is_never_reported_as_agentic():
    """The product spec: "the heuristic path must never be presented as
    equivalent". Every heuristic-sourced field says so and carries its
    approximation's confidence and method.
    """
    resolved = dp.resolve(heuristic=_full_heuristic())
    heuristic_fields = [f for f in resolved.fields.values() if f.store == dp.STORE_HEURISTIC]
    assert len(heuristic_fields) == len(HEURISTIC_BACKED)
    for field in heuristic_fields:
        assert field.provenance == dp.PROVENANCE_HEURISTIC
        assert 0 < (field.confidence or 0) <= 1
        assert field.method
