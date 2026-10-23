"""The one derived-profile read model.

Three resume-derived stores already existed, overlapping on name, country,
titles and skills, and nothing said which one won:

================================  =================  ======================
store                             built by           purpose
================================  =================  ======================
``resume/profiler.py``            Claude             matching and scoring
  ``ResumeProfile``
``fillerbot/models.py``           Claude             form filling
  ``FillerProfile``
``fillerbot/user_extras.py``      the user           corrections and gaps
  ``data/user_extras.yaml``
================================  =================  ======================

T49 added a fourth *source* — ``resume/heuristic.py``, the no-key path — but
deliberately not a fourth store. This module is the single surface over all of
them. It owns **no storage of its own**: every value it reports is read, at
read time, out of one of the three stores above. That is not an implementation
detail, it is the mechanism by which the product spec's first rule holds.

Precedence
----------
Per field, highest first: **a user edit > agentic extraction > heuristic
extraction > absent**.

Resolution happens on *read*, so there is no sequence of re-parses that can
reach a user's value: a re-parse rewrites the agentic stores, and the agentic
stores are consulted only after the user layer comes back empty. There is no
``apply``/``force`` lever here on purpose. Writing the user layer is a
separate, explicit call (:func:`save_user_edits`), which is what makes "a
re-parse proposes, it does not apply" structural rather than a convention a
later caller can forget. :func:`propose` computes what a re-parse *would*
change and flags what it is not allowed to touch; it writes nothing.

The per-field owner table
-------------------------
:data:`FIELD_SPECS` below is the deliverable people will reason about. It
states, for all 30 derived fields, which store owns the agentic value, which
``HeuristicProfile`` field backs it, and whether the user may edit it.

Two fields — ``skills`` and ``years_experience`` — exist in **both** agentic
stores. Both are "agentic", so the product spec's three-layer rule is silent on
which wins, and an implicit dict-merge order would decide it invisibly. Their
``agentic`` tuple is therefore an explicit, ordered fallback chain with the
reason recorded in ``note``. Every other field names exactly one store.

What is deliberately *not* exposed
----------------------------------
Both agentic dataclasses carry four cache-metadata fields, one of which —
``source_path`` — is the resume's absolute path on disk. The product spec
(the rule T45 established for ``GET /api/settings``) says that must never
leave the server.

The protection is an **allow-list, not a deny-list**: :meth:`DerivedProfile.
to_payload` iterates :data:`FIELD_SPECS` and can only ever emit registered
fields, so a path-bearing field added to either dataclass tomorrow is
unreachable by construction rather than by someone remembering to add it to an
exclusion list. ``tests/test_derived_profile.py`` pins both halves: a
recursive sentinel scan, and a key-name guard that fires if a path-bearing
field is ever *registered*.

Known residuals, pinned by tests rather than guessed at
-------------------------------------------------------
* ``years_experience`` is a plain ``int`` defaulting to 0 in both agentic
  dataclasses, so a genuine "0 years" cannot be told from "the extraction did
  not populate this" and resolves as ``absent``. Giving the agentic stores
  optionality is the real fix and is not this task's scope.
* ``work_experience`` and ``education`` are lists of nested entries. They are
  reported with provenance like everything else but are not user-editable
  yet — an edit surface for nested entries is T54's.
* The heuristic layer reports a single ``seniority`` rank word while the
  unified field ``seniority_levels`` is a list, and reports
  ``years_experience`` as a *string*. Both are widened/coerced here, at the
  boundary, so the heuristic module keeps its own honest shapes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from .fillerbot import profiler as filler_profiler
from .fillerbot.models import FillerProfile
from .fillerbot.user_extras import load_extras, save_extras
from .resume import heuristic as heuristic_layer
from .resume import profiler as resume_profiler
from .resume.profiler import ResumeProfile

logger = logging.getLogger(__name__)

__all__ = [
    "FIELDS",
    "FIELD_NAMES",
    "FIELD_SHAPES",
    "FIELD_SPECS",
    "KINDS",
    "KIND_ENTRY_LIST",
    "KIND_INT",
    "KIND_STR",
    "KIND_STR_LIST",
    "MAX_EDIT_LIST_ITEMS",
    "MAX_EDIT_VALUE_LEN",
    "PROVENANCE_ABSENT",
    "PROVENANCE_AGENTIC",
    "PROVENANCE_HEURISTIC",
    "PROVENANCE_USER",
    "PROVENANCE_VALUES",
    "STORE_FILLER",
    "STORE_HEURISTIC",
    "STORE_RESUME",
    "STORE_USER",
    "USER_EDITABLE_FIELDS",
    "DerivedProfile",
    "FieldSpec",
    "Proposal",
    "ResolvedField",
    "empty_value",
    "load_layers",
    "normalise",
    "overlay_user_values",
    "propose",
    "propose_from_agentic",
    "propose_from_heuristic",
    "resolve",
    "resolve_for_resume",
    "save_user_edits",
    "user_edits",
]

# the product spec's provenance vocabulary, in full. ``heuristic.py`` produces only
# the bottom two; the other two belong to the layers above it.
PROVENANCE_USER = "user"
PROVENANCE_AGENTIC = "agentic"
PROVENANCE_HEURISTIC = "heuristic"
PROVENANCE_ABSENT = "absent"
PROVENANCE_VALUES = (PROVENANCE_USER, PROVENANCE_AGENTIC, PROVENANCE_HEURISTIC, PROVENANCE_ABSENT)

# Which store a resolved value actually came out of. ``provenance`` answers
# "can I trust this"; ``store`` answers "where do I go to change it", and is
# the only thing that distinguishes the two agentic stores from each other.
STORE_USER = "user_extras"
STORE_RESUME = "resume_profile"
STORE_FILLER = "filler_profile"
STORE_HEURISTIC = "heuristic"

KIND_STR = "str"
KIND_INT = "int"
KIND_STR_LIST = "str_list"
KIND_ENTRY_LIST = "entry_list"
KINDS = (KIND_STR, KIND_INT, KIND_STR_LIST, KIND_ENTRY_LIST)

# The bounds T31 put on the 9 contact fields, applied to all of them: these
# values reach Anthropic prompts and a YAML file on disk.
MAX_EDIT_VALUE_LEN = 500
MAX_EDIT_LIST_ITEMS = 50


@dataclass(frozen=True)
class FieldSpec:
    """One row of the owner table."""

    name: str
    kind: str
    label: str
    #: Agentic stores to consult, in order. More than one entry means the
    #: field exists in both and the order is a deliberate, stated choice.
    agentic: tuple[str, ...]
    #: The backing ``HeuristicProfile`` attribute, or ``None`` if the no-key
    #: path cannot derive this field at all.
    heuristic: str | None = None
    user_editable: bool = True
    #: Why this owner, for the fields where that is a real question.
    note: str = ""


_SKILLS_NOTE = (
    "ResumeProfile first. Its prompt asks for matchable terms including "
    "variants and so has broader recall, and matching is the consumer that "
    "degrades silently when the list is thin, whereas a short skills list on "
    "a form is visible to the user filling it in."
)
_YEARS_NOTE = (
    "ResumeProfile first, because its years_experience is extracted in the "
    "same pass as target_seniority_min/max and is reasoned about against "
    "them. Taking it from FillerProfile would produce an inconsistent triple "
    "whenever the two extractions disagree."
)

# ── The per-field owner table ────────────────────────────────────────────────
#
# 30 derived fields = FillerProfile's 20 content fields + ResumeProfile's 12,
# less the 2 they share. The 4 cache-metadata fields on each dataclass
# (source_path, source_mtime, generated_at, profile_version) are deliberately
# absent: they are not derived data, and one of them is the resume's path.

FIELD_SPECS: tuple[FieldSpec, ...] = (
    # Contact and identity — FillerProfile's territory.
    FieldSpec("full_name", KIND_STR, "Full name", (STORE_FILLER,), "full_name"),
    FieldSpec("first_name", KIND_STR, "First name", (STORE_FILLER,)),
    FieldSpec("last_name", KIND_STR, "Last name", (STORE_FILLER,)),
    FieldSpec("email", KIND_STR, "Email address", (STORE_FILLER,), "email"),
    FieldSpec("phone", KIND_STR, "Phone number", (STORE_FILLER,), "phone"),
    FieldSpec("address", KIND_STR, "Street address", (STORE_FILLER,)),
    FieldSpec("city", KIND_STR, "City", (STORE_FILLER,)),
    FieldSpec("state_province", KIND_STR, "State / Province", (STORE_FILLER,)),
    FieldSpec("zip_postal", KIND_STR, "ZIP / Postal code", (STORE_FILLER,)),
    FieldSpec("country", KIND_STR, "Country", (STORE_FILLER,), "country"),
    FieldSpec("linkedin_url", KIND_STR, "LinkedIn URL", (STORE_FILLER,)),
    FieldSpec("website", KIND_STR, "Personal website", (STORE_FILLER,)),
    # Narrative and history — FillerProfile's territory too.
    FieldSpec("summary", KIND_STR, "Professional summary", (STORE_FILLER,)),
    FieldSpec("current_title", KIND_STR, "Current job title", (STORE_FILLER,)),
    FieldSpec("certifications", KIND_STR_LIST, "Certifications", (STORE_FILLER,)),
    FieldSpec("languages", KIND_STR_LIST, "Languages", (STORE_FILLER,)),
    FieldSpec(
        "work_experience",
        KIND_ENTRY_LIST,
        "Work history",
        (STORE_FILLER,),
        user_editable=False,
        note="Nested entries; an edit surface for them is T54's, so read-only here rather than half-editable.",
    ),
    FieldSpec(
        "education",
        KIND_ENTRY_LIST,
        "Education",
        (STORE_FILLER,),
        user_editable=False,
        note="Nested entries; see work_experience.",
    ),
    # The two fields both agentic stores hold. Order stated, not implied.
    FieldSpec("skills", KIND_STR_LIST, "Skills", (STORE_RESUME, STORE_FILLER), "skills", note=_SKILLS_NOTE),
    FieldSpec(
        "years_experience",
        KIND_INT,
        "Years of experience",
        (STORE_RESUME, STORE_FILLER),
        "years_experience",
        note=_YEARS_NOTE,
    ),
    # Matching and targeting — ResumeProfile's territory.
    FieldSpec("tools", KIND_STR_LIST, "Tools", (STORE_RESUME,)),
    FieldSpec("job_titles", KIND_STR_LIST, "Job titles", (STORE_RESUME,), "job_titles"),
    FieldSpec("domains", KIND_STR_LIST, "Domains", (STORE_RESUME,)),
    FieldSpec(
        "seniority_levels",
        KIND_STR_LIST,
        "Seniority levels",
        (STORE_RESUME,),
        "seniority",
        note="The heuristic layer derives one rank word; it widens into this list field at the boundary.",
    ),
    FieldSpec("keywords", KIND_STR_LIST, "Matching keywords", (STORE_RESUME,)),
    FieldSpec("target_job_functions", KIND_STR_LIST, "Target job functions", (STORE_RESUME,)),
    FieldSpec("target_seniority_min", KIND_INT, "Target seniority (min years)", (STORE_RESUME,)),
    FieldSpec("target_seniority_max", KIND_INT, "Target seniority (max years)", (STORE_RESUME,)),
    FieldSpec("target_industries", KIND_STR_LIST, "Target industries", (STORE_RESUME,)),
    FieldSpec("avoid_industries", KIND_STR_LIST, "Industries to avoid", (STORE_RESUME,)),
)

FIELDS: Mapping[str, FieldSpec] = {spec.name: spec for spec in FIELD_SPECS}
FIELD_NAMES: tuple[str, ...] = tuple(spec.name for spec in FIELD_SPECS)
USER_EDITABLE_FIELDS: tuple[str, ...] = tuple(spec.name for spec in FIELD_SPECS if spec.user_editable)

#: The value shape of each key the user layer may store, handed to
#: ``user_extras.load_extras``/``save_extras`` so that module does not need a
#: second copy of the owner table to know a list from a string.
FIELD_SHAPES: Mapping[str, str] = {spec.name: spec.kind for spec in FIELD_SPECS if spec.user_editable}


# ── Value shapes ─────────────────────────────────────────────────────────────


def empty_value(spec: FieldSpec):
    """The canonical "nothing here" value for a field's kind."""
    if spec.kind == KIND_INT:
        return 0
    if spec.kind in (KIND_STR_LIST, KIND_ENTRY_LIST):
        return []
    return ""


def normalise(spec: FieldSpec, raw):
    """Coerce a layer's raw value into the field's canonical shape.

    Returns :func:`empty_value` for anything unusable, which is what makes a
    layer "fall through" to the next one rather than resolving to junk — a
    non-numeric ``years_experience`` from the heuristic layer, a dict where a
    list belongs in a hand-edited YAML file, and so on.
    """
    if raw is None:
        return empty_value(spec)

    if spec.kind == KIND_STR:
        return raw.strip() if isinstance(raw, str) else ""

    if spec.kind == KIND_INT:
        if isinstance(raw, bool):
            return 0
        if isinstance(raw, int):
            return raw
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return 0

    if spec.kind == KIND_STR_LIST:
        # A scalar widens into a one-element list: the heuristic layer's
        # `seniority` is one rank word, and `skills: Python` is the obvious
        # thing to type into the YAML file by hand.
        items: Iterable
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, (list, tuple)):
            items = raw
        else:
            return []
        out: list[str] = []
        for item in items:
            if not isinstance(item, str):
                return []
            trimmed = item.strip()
            if trimmed and trimmed not in out:
                out.append(trimmed)
        return out

    if spec.kind == KIND_ENTRY_LIST:
        if not isinstance(raw, (list, tuple)):
            return []
        return [entry for entry in raw if is_dataclass(entry)]

    return empty_value(spec)


def _serialise(spec: FieldSpec, value):
    if spec.kind == KIND_ENTRY_LIST:
        return [asdict(entry) for entry in value]
    return value


# ── Resolution ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedField:
    """One field of the read model, with where it came from.

    ``confidence`` is ``None`` for the user and agentic layers: neither
    reports one, and a constant stamped on every field would be decoration
    rather than a signal (T49's rule). Only the heuristic layer, which
    genuinely approximates, carries a number; ``absent`` carries 0.0.
    """

    name: str
    value: object
    provenance: str
    store: str
    confidence: float | None
    method: str

    @property
    def found(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True)
class DerivedProfile:
    """The unified read model. Built per read; stores nothing."""

    fields: Mapping[str, ResolvedField]

    def __getitem__(self, name: str) -> ResolvedField:
        return self.fields[name]

    def __contains__(self, name: object) -> bool:
        return name in self.fields

    def value(self, name: str):
        return self.fields[name].value

    def to_payload(self) -> dict:
        """What a ``GET`` of this surface returns.

        Built by walking :data:`FIELD_SPECS`, so this is an allow-list of
        exposed fields: an unregistered field — including the ``source_path``
        both agentic dataclasses carry — has no route into the result
        (the rule T45 established).
        """
        return {
            "fields": {
                spec.name: {
                    "value": _serialise(spec, self.fields[spec.name].value),
                    "provenance": self.fields[spec.name].provenance,
                    "store": self.fields[spec.name].store,
                    "confidence": self.fields[spec.name].confidence,
                    "method": self.fields[spec.name].method,
                    "label": spec.label,
                    "kind": spec.kind,
                    "user_editable": spec.user_editable,
                }
                for spec in FIELD_SPECS
            }
        }


_AGENTIC_METHOD = {
    STORE_RESUME: "extracted from your resume by Claude (matching profile)",
    STORE_FILLER: "extracted from your resume by Claude (form-filling profile)",
}


def _absent(spec: FieldSpec) -> ResolvedField:
    return ResolvedField(
        name=spec.name,
        value=empty_value(spec),
        provenance=PROVENANCE_ABSENT,
        store="",
        confidence=0.0,
        method="",
    )


def _resolve_field(
    spec: FieldSpec,
    user_values: Mapping[str, object],
    stores: Mapping[str, object],
    heuristic: object | None,
    *,
    include_user: bool = True,
) -> ResolvedField:
    if include_user and spec.user_editable:
        value = normalise(spec, user_values.get(spec.name))
        if value:
            return ResolvedField(
                name=spec.name,
                value=value,
                provenance=PROVENANCE_USER,
                store=STORE_USER,
                confidence=None,
                method="you edited this field",
            )

    for store_name in spec.agentic:
        store = stores.get(store_name)
        if store is None:
            continue
        value = normalise(spec, getattr(store, spec.name, None))
        if value:
            return ResolvedField(
                name=spec.name,
                value=value,
                provenance=PROVENANCE_AGENTIC,
                store=store_name,
                confidence=None,
                method=_AGENTIC_METHOD[store_name],
            )

    if heuristic is not None and spec.heuristic:
        field = getattr(heuristic, spec.heuristic, None)
        if field is not None and getattr(field, "provenance", "") == heuristic_layer.PROVENANCE_HEURISTIC:
            value = normalise(spec, field.value)
            if value:
                return ResolvedField(
                    name=spec.name,
                    value=value,
                    provenance=PROVENANCE_HEURISTIC,
                    store=STORE_HEURISTIC,
                    confidence=field.confidence,
                    method=field.method,
                )

    return _absent(spec)


def resolve(
    *,
    user_edits: Mapping[str, object] | None = None,
    resume_profile: ResumeProfile | None = None,
    filler_profile: FillerProfile | None = None,
    heuristic: heuristic_layer.HeuristicProfile | None = None,
) -> DerivedProfile:
    """Apply the product spec's precedence across whichever layers are present.

    Every layer is optional: no API key means no agentic stores and the
    heuristic layer decides; a brand-new install has none of them and every
    field comes back ``absent`` rather than missing.

    Resolution is per field, not per profile — an agentic store that
    populated half its fields does not suppress the heuristic layer for the
    other half.

    There is deliberately no parameter here that lets a caller apply a
    re-parse over a user edit. See :func:`propose`.
    """
    user_values = dict(user_edits or {})
    stores = {STORE_RESUME: resume_profile, STORE_FILLER: filler_profile}
    return DerivedProfile(
        fields={spec.name: _resolve_field(spec, user_values, stores, heuristic) for spec in FIELD_SPECS}
    )


# ── A re-parse proposes, it does not apply ───────────────────────────────────


@dataclass(frozen=True)
class Proposal:
    """One field a re-parse would change, and whether it is allowed to.

    ``blocked_by_user_edit`` is the whole point: the caller shows both values
    and applies neither until the user accepts (T54's DoD).
    """

    name: str
    label: str
    current_value: object
    current_provenance: str
    proposed_value: object
    proposed_provenance: str
    proposed_store: str
    blocked_by_user_edit: bool


def propose(
    current: DerivedProfile,
    *,
    resume_profile: ResumeProfile | None = None,
    filler_profile: FillerProfile | None = None,
    heuristic: heuristic_layer.HeuristicProfile | None = None,
) -> tuple[Proposal, ...]:
    """What a fresh extraction offers, as proposals. Writes nothing.

    ``current`` is the profile the user is looking at now; the keyword
    arguments are the layers a **re-parse just produced**. Both are needed:
    comparing a new extraction against a resolution of itself would only ever
    surface the fields a user edit blocks, which would make an "accept per
    field" UI impossible to build on this — the T54 case.

    The candidate is resolved with the user layer *excluded*, so each proposal
    is what the parse alone says. Fields a user edit already decides come back
    flagged rather than omitted, because "we changed something and will not
    say what" and "we changed nothing and will not say why" are the same
    trapdoor from two sides.
    """
    stores = {STORE_RESUME: resume_profile, STORE_FILLER: filler_profile}

    proposals: list[Proposal] = []
    for spec in FIELD_SPECS:
        current_field = current[spec.name]
        candidate = _resolve_field(spec, {}, stores, heuristic, include_user=False)
        if candidate.value == current_field.value:
            continue
        proposals.append(
            Proposal(
                name=spec.name,
                label=spec.label,
                current_value=current_field.value,
                current_provenance=current_field.provenance,
                proposed_value=candidate.value,
                proposed_provenance=candidate.provenance,
                proposed_store=candidate.store,
                blocked_by_user_edit=current_field.provenance == PROVENANCE_USER,
            )
        )
    return tuple(proposals)


def propose_from_heuristic(resume_path: str) -> tuple[Proposal, ...]:
    """What a heuristic re-parse offers (4.3.1's keyless path).

    Sends nothing anywhere — ``heuristic_layer.extract_profile_from_resume``
    is local, keyless text processing. ``current`` is resolved the normal
    way (all three layers), so this also surfaces the case where the
    heuristic layer alone would improve on what is showing now, e.g. an
    agentic field that was never populated.
    """
    from .resume.parser import load_resume

    current = resolve_for_resume(resume_path)
    try:
        heuristic = heuristic_layer.extract_profile_from_resume(load_resume(resume_path))
    except Exception as exc:
        logger.warning("Heuristic re-parse skipped: %s", exc)
        heuristic = None
    return propose(current, heuristic=heuristic)


def propose_from_agentic(resume_path: str, api_key: str, model: str) -> tuple[Proposal, ...]:
    """What an agentic re-parse offers (4.3.1's Anthropic path).

    Calls Claude twice — once per agentic store, since the owner table's 30
    fields split across ``ResumeProfile`` and ``FillerProfile`` — using the
    **in-memory-only** extraction functions, not ``load_profile``/
    ``load_filler_profile``. Those two save their result to
    ``resume_profile.json``/``filler_profile.json`` as their very next line;
    calling them here would make the "proposal" already live by the time
    this function returns, which is the silent overwrite the product spec
    forbids. Persisting is :func:`save_user_edits`'s job alone, triggered by
    the caller accepting a proposal — this function writes nothing.

    The caller (``jobfinder/web/resume.py``) is responsible for the product spec's
    acknowledgement bound and for not calling this at all without an
    API key; this function assumes both are already satisfied.

    Calls through the ``resume_profiler``/``filler_profiler`` **module
    objects** imported at the top of this file, not
    ``from .resume.profiler import extract_profile_without_caching`` inside
    this function. A call-time ``from X.Y import name`` re-resolves ``Y`` as
    an attribute of package ``X`` every time it runs; T49's
    ``test_the_agentic_profilers_import_without_anthropic``
    (``tests/test_resume_heuristic.py``) does a ``sys.modules`` swap
    (``monkeypatch.delitem`` + ``importlib.import_module``) that rebinds
    ``jobfinder.resume.profiler`` as a package attribute but leaves
    ``sys.modules`` restored to the original object afterwards — so a
    call-time ``from``-import and a test's ``import jobfinder.resume.profiler
    as m; monkeypatch.setattr(m, ...)`` can end up looking at two different
    module objects, and the patched one is not the one this function calls.
    A module-object import binds once, here, at this file's own import time,
    so it is immune to that later ``sys.modules`` surgery — and
    ``monkeypatch.setattr(resume_profiler, "extract_profile_without_caching",
    ...)`` still works as the test seam, since it mutates the very module
    object this function reads from.
    """
    current = resolve_for_resume(resume_path)
    resume_profile = resume_profiler.extract_profile_without_caching(resume_path, api_key, model)
    filler_profile = filler_profiler.extract_profile_without_caching(resume_path, api_key, model)
    return propose(current, resume_profile=resume_profile, filler_profile=filler_profile)


# ── The user-edit layer ──────────────────────────────────────────────────────


def user_edits() -> dict[str, object]:
    """The user layer, read from ``data/user_extras.yaml``.

    Restricted to registered fields: a stray key in a hand-edited file is left
    on disk (``save_user_edits`` preserves it) but takes no part in
    resolution.
    """
    stored = load_extras(shapes=FIELD_SHAPES)
    return {key: value for key, value in stored.items() if key in FIELD_SHAPES}


def _validate_edit(spec: FieldSpec, value: object) -> None:
    def _check_text(text: str, where: str) -> None:
        if len(text) > MAX_EDIT_VALUE_LEN:
            raise ValueError(f"{where} exceeds {MAX_EDIT_VALUE_LEN} characters")
        # Control characters would survive into a prompt and into the YAML
        # file; none of these fields has any use for them.
        if any(ch < " " and ch != "\t" for ch in text):
            raise ValueError(f"{where} contains control characters")

    if spec.kind == KIND_STR:
        if not isinstance(value, str):
            raise ValueError(f"{spec.name} must be a string")
        _check_text(value, spec.name)
    elif spec.kind == KIND_INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{spec.name} must be an integer")
    elif spec.kind == KIND_STR_LIST:
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{spec.name} must be a list of strings")
        if len(value) > MAX_EDIT_LIST_ITEMS:
            raise ValueError(f"{spec.name} exceeds {MAX_EDIT_LIST_ITEMS} items")
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise ValueError(f"{spec.name} must be a list of strings")
            _check_text(item, f"{spec.name}[{index}]")
    else:  # pragma: no cover - entry-list fields are rejected as unknown above
        raise ValueError(f"{spec.name} is not user-editable")


def save_user_edits(values: Mapping[str, object]) -> dict[str, object]:
    """Merge user edits into the user layer and persist them.

    PATCH semantics, like ``PATCH /api/fillerbot/profile/extras``: the body is
    merged over what is stored, so writing one field cannot wipe the others.
    Clearing a field is PATCHing it to ``""``/``[]`` — ``save_extras`` drops
    empty values, after which the field falls back to the agentic or heuristic
    layer rather than resolving to an empty user value.

    Keys are allow-listed to the owner table and values are bounded; these
    reach Anthropic prompts and a YAML file on disk.
    """
    unknown = sorted(set(values) - set(FIELD_SHAPES))
    if unknown:
        raise ValueError(f"unknown profile field(s): {', '.join(unknown)}")

    cleaned: dict[str, object] = {}
    for key, value in values.items():
        spec = FIELDS[key]
        _validate_edit(spec, value)
        cleaned[key] = normalise(spec, value)

    # Merged over the raw stored mapping, not over ``user_edits()``, so an
    # unregistered key someone added to the file by hand survives the write.
    merged = {**load_extras(shapes=FIELD_SHAPES), **cleaned}
    save_extras(merged, shapes=FIELD_SHAPES)
    return user_edits()


def overlay_user_values(profile: FillerProfile, edits: Mapping[str, object]) -> None:
    """Overlay the user layer onto a ``FillerProfile``, user winning.

    **This replaces gap-filling.** ``user_extras.merge_into_profile`` used to
    write only onto *empty* fields, so a user who corrected their phone number
    kept watching FillerBot type the resume's old one into forms. Under
    the product spec a user edit is never silently overwritten, so it wins here
    too — the engine fills forms with the corrected value.

    Only registered, user-editable fields the ``FillerProfile`` actually has
    are touched, so no key in the stored YAML can reach its cache metadata.
    """
    for spec in FIELD_SPECS:
        if not spec.user_editable or STORE_FILLER not in spec.agentic:
            continue
        if spec.name not in edits or not hasattr(profile, spec.name):
            continue
        value = normalise(spec, edits[spec.name])
        if value:
            setattr(profile, spec.name, value)


# ── Loading the layers ───────────────────────────────────────────────────────


def load_layers(resume_path: str = "") -> dict[str, object]:
    """The three layers, as keyword arguments for :func:`resolve`.

    The agentic stores are read **from cache only**. A ``GET`` of this surface
    must not be able to trigger a billed Anthropic call, and the heuristic
    layer is local, keyless and free — which is also exactly the no-key
    behaviour the product spec asks for.
    """
    layers: dict[str, object] = {
        "user_edits": user_edits(),
        "resume_profile": None,
        "filler_profile": None,
        "heuristic": None,
    }
    if not resume_path or not Path(resume_path).exists():
        return layers

    from .fillerbot.profiler import cached_filler_profile
    from .resume.profiler import cached_resume_profile

    layers["resume_profile"] = cached_resume_profile(resume_path)
    layers["filler_profile"] = cached_filler_profile(resume_path)

    try:
        from .resume.parser import load_resume

        layers["heuristic"] = heuristic_layer.extract_profile_from_resume(load_resume(resume_path))
    except Exception as exc:
        # An unreadable or corrupt resume must leave the surface usable with
        # empty fields, not fail the request.
        logger.warning("Heuristic extraction skipped: %s", exc)

    return layers


def resolve_for_resume(resume_path: str = "") -> DerivedProfile:
    """:func:`load_layers` then :func:`resolve` — the whole read, in one call.

    Every consumer of this surface wants exactly this pair (``GET
    /api/resume/derived-profile`` and T52's onboarding pre-fill both did it
    inline), and the pair is where the cache-only guarantee lives: keeping it
    in one place is what stops a later caller from loading the agentic layers
    some other way and making a billed call out of a ``GET``.
    """
    return resolve(**load_layers(resume_path))
