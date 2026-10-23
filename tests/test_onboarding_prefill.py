"""Tests for resume-driven onboarding (T52).

the product spec is the contract: the resume is the source of truth for who the
user is, and the rest of the configuration should *follow* from it. T50 built
the read model (``jobfinder/derived_profile.py``); this task maps that read
model onto the four configuration fields onboarding writes, carrying each
field's provenance with it:

===========================  ==================================================
derived field                configuration it pre-fills
===========================  ==================================================
``country``                  ``location_filter.search_country`` **and**
                             ``sources[].location``
``job_titles``               ``sources[].search_terms``
``target_job_functions``     ``role_filter.job_functions``
``seniority_levels``         ``role_filter.seniority_levels``
===========================  ==================================================

Everything here drives the **real API** — ``POST /api/resume`` then ``GET
/api/onboarding/prefill`` then ``PUT /api/settings`` — rather than calling the
mapping helper directly, because the thing that can break is the wiring.

No billed Anthropic request is made by any test in this file, and none is
possible: ``derived_profile.load_layers`` reads the agentic stores from cache
only, and the "with a key" cases fake the key *and* monkeypatch the two cached
loaders. A real key is configured on this machine, so a live call would be
billed for real.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from jobfinder import onboarding_prefill as op

PORT = 8761
BASE_URL = f"http://127.0.0.1:{PORT}"

FIXTURES = Path(__file__).parent / "fixtures" / "resumes"

# Deliberately not the QA/Canada resume the product used to over-fit
# an Irish nurse exercises a country, titles and a rank word
# that no part of the pipeline has ever been tuned for.
NURSE = FIXTURES / "nurse_ireland.txt"
CHEF = FIXTURES / "chef_spain.txt"
TEACHER = FIXTURES / "teacher_sparse.txt"

FAKE_KEY = "sk-ant-api03-FAKE" + "b3Qd" * 20


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
def client(isolated_app):
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
        assert resp.status_code == 200, resp.text
        yield app, c


def _upload(c, fixture: Path):
    """Upload as ``.txt``: ``.docx`` is rejected on purpose and ``parser.py``
    has only ``_parse_pdf`` plus a text fallback (T50's note)."""
    return c.post(
        "/api/resume",
        files={"file": (fixture.name, fixture.read_bytes(), "text/plain")},
    )


def _prefill(c) -> dict:
    resp = c.get("/api/onboarding/prefill")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _fake_agentic(monkeypatch, *, resume_profile=None, filler_profile=None):
    """Stand in for the two cached agentic stores.

    ``derived_profile.load_layers`` imports these inside the function body, so
    patching the module attribute is what takes effect. Nothing here reaches
    the network: that is the whole point.
    """
    import jobfinder.fillerbot.profiler as fb_profiler
    import jobfinder.resume.profiler as rs_profiler

    monkeypatch.setattr(rs_profiler, "cached_resume_profile", lambda _path: resume_profile)
    monkeypatch.setattr(fb_profiler, "cached_filler_profile", lambda _path: filler_profile)


# ── With no API key: the heuristic path ──────────────────────────────────────


def test_with_no_api_key_a_resume_prefills_country_and_titles_as_heuristic(client):
    """The DoD's first clause, through the real API.

    the product spec: "a user without a key gets nothing derived, which is exactly
    when sensible pre-filling matters most".
    """
    _app, c = client
    assert _upload(c, NURSE).status_code == 200

    payload = _prefill(c)
    fields = payload["fields"]

    assert fields[op.PREFILL_SEARCH_COUNTRY]["value"] == "Ireland"
    assert fields[op.PREFILL_SEARCH_COUNTRY]["provenance"] == "heuristic"

    terms = fields[op.PREFILL_SEARCH_TERMS]["value"]
    assert "Staff Nurse" in terms
    assert fields[op.PREFILL_SEARCH_TERMS]["provenance"] == "heuristic"

    # "must say which it used"
    assert payload["extraction"] == op.EXTRACTION_HEURISTIC
    assert payload["api_key_configured"] is False
    assert payload["extraction_note"]


def test_the_no_key_prefill_says_which_config_field_each_value_lands_in(client):
    _app, c = client
    assert _upload(c, NURSE).status_code == 200
    fields = _prefill(c)["fields"]

    assert fields[op.PREFILL_SEARCH_COUNTRY]["targets"] == [
        "location_filter.search_country",
        "sources[].location",
    ]
    assert fields[op.PREFILL_SEARCH_TERMS]["targets"] == ["sources[].search_terms"]
    assert fields[op.PREFILL_JOB_FUNCTIONS]["targets"] == ["role_filter.job_functions"]
    assert fields[op.PREFILL_SENIORITY_LEVELS]["targets"] == ["role_filter.seniority_levels"]


def test_every_prefilled_value_carries_a_provenance_and_a_human_origin(client):
    """The product spec: "a value the user cannot tell the origin of is a value they
    cannot judge" — so provenance is not optional on any field, including the
    ones that came back empty."""
    _app, c = client
    assert _upload(c, NURSE).status_code == 200
    fields = _prefill(c)["fields"]

    assert set(fields) == set(op.PREFILL_FIELD_NAMES)
    for name, field in fields.items():
        assert field["provenance"] in ("user", "agentic", "heuristic", "absent"), name
        assert field["origin"], name
        assert field["label"], name


def test_a_rank_word_is_translated_into_linkedins_level_vocabulary(client):
    """``role_filter.seniority_levels`` is compared against a card's LinkedIn
    ``job_level`` (``jobfinder/filters.py``), so the rank word the heuristic
    layer reports has to be translated or it would hide every job that
    reports a level at all."""
    _app, c = client
    assert _upload(c, NURSE).status_code == 200
    field = _prefill(c)["fields"][op.PREFILL_SENIORITY_LEVELS]

    assert field["value"] == ["Mid-Senior level"]
    assert field["provenance"] == "heuristic"
    for level in field["value"]:
        assert level in op.LINKEDIN_SENIORITY_LEVELS


def test_a_rank_word_outside_linkedins_vocabulary_prefills_nothing(client):
    """The chef's resume reads as ``manager``, which is not one of LinkedIn's
    six levels. Pre-filling it verbatim would hide every job carrying a level;
    guessing the nearest level is a guess. So: nothing, and a note saying why."""
    _app, c = client
    assert _upload(c, CHEF).status_code == 200
    field = _prefill(c)["fields"][op.PREFILL_SENIORITY_LEVELS]

    assert field["value"] == []
    assert field["provenance"] == "absent"
    assert field["note"]


def test_job_functions_are_absent_without_a_key_and_say_so(client):
    """A documented residual, not an oversight.

    The DoD asks for functions with provenance ``heuristic``, but there is no
    heuristic function axis to give: ``target_job_functions`` is LinkedIn's own
    closed vocabulary, and T49's heuristic layer deliberately contains **no**
    profession vocabulary ("a keyword list would rebuild that bias one layer
    down" — the product spec's resolution). Deriving a function from a title needs
    exactly such a table, so with no key this field is honestly empty and says
    an API key would fill it.
    """
    _app, c = client
    assert _upload(c, NURSE).status_code == 200
    field = _prefill(c)["fields"][op.PREFILL_JOB_FUNCTIONS]

    assert field["value"] == []
    assert field["provenance"] == "absent"
    assert "key" in field["note"].lower()


def test_the_prefill_get_makes_no_anthropic_request(client):
    """The suite makes zero billed Anthropic requests and this endpoint must
    not be the one that breaks that (``load_layers`` is cache-only)."""
    _app, c = client
    assert _upload(c, NURSE).status_code == 200
    with patch("anthropic.Anthropic") as anthropic_cls:
        _prefill(c)
    anthropic_cls.assert_not_called()


# ── With a (faked) key: the agentic path ─────────────────────────────────────


def test_with_a_faked_key_the_same_resume_prefills_as_agentic(client, monkeypatch):
    """The DoD's second clause. The key is faked and the cached agentic stores
    are monkeypatched — no request leaves the machine."""
    app, c = client
    assert _upload(c, NURSE).status_code == 200

    from jobfinder.fillerbot.models import FillerProfile
    from jobfinder.resume.profiler import ResumeProfile

    _fake_agentic(
        monkeypatch,
        resume_profile=ResumeProfile(
            job_titles=["Registered Nurse", "Clinical Nurse Manager"],
            seniority_levels=["Senior"],
            target_job_functions=["Health Care Provider"],
        ),
        filler_profile=FillerProfile(country="Ireland"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    payload = _prefill(c)
    fields = payload["fields"]

    assert payload["extraction"] == op.EXTRACTION_AGENTIC
    assert payload["api_key_configured"] is True

    assert fields[op.PREFILL_SEARCH_COUNTRY]["provenance"] == "agentic"
    assert fields[op.PREFILL_SEARCH_TERMS]["provenance"] == "agentic"
    assert fields[op.PREFILL_SEARCH_TERMS]["value"] == [
        "Registered Nurse",
        "Clinical Nurse Manager",
    ]
    assert fields[op.PREFILL_JOB_FUNCTIONS]["provenance"] == "agentic"
    assert fields[op.PREFILL_JOB_FUNCTIONS]["value"] == ["Health Care Provider"]


def test_an_agentic_store_that_populated_half_its_fields_leaves_the_rest_heuristic(client, monkeypatch):
    """Resolution is per field, not per profile (T50's rule) — and the summary
    still reports the *better* path it used."""
    app, c = client
    assert _upload(c, NURSE).status_code == 200

    from jobfinder.resume.profiler import ResumeProfile

    _fake_agentic(monkeypatch, resume_profile=ResumeProfile(job_titles=["Registered Nurse"]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    fields = _prefill(c)["fields"]
    assert fields[op.PREFILL_SEARCH_TERMS]["provenance"] == "agentic"
    # No FillerProfile, so the country still comes from the local read.
    assert fields[op.PREFILL_SEARCH_COUNTRY]["provenance"] == "heuristic"
    assert fields[op.PREFILL_SEARCH_COUNTRY]["value"] == "Ireland"


# ── A resume that yields nothing ─────────────────────────────────────────────


def test_a_resume_that_yields_nothing_leaves_onboarding_usable_with_empty_fields(client):
    """The DoD's fourth clause: empty, not broken, and not guessed."""
    _app, c = client
    nothing = c.post("/api/resume", files={"file": ("cv.txt", b"hello there\n", "text/plain")})
    assert nothing.status_code == 200, nothing.text

    payload = _prefill(c)
    assert payload["extraction"] == op.EXTRACTION_NONE
    for name, field in payload["fields"].items():
        assert field["provenance"] == "absent", name
        assert field["value"] in ("", []), name

    # Still completable: the wizard's finish step is a plain settings write.
    done = c.put("/api/settings", json={"location_filter": {"search_country": "Spain"}})
    assert done.status_code == 200, done.text
    assert c.get("/api/settings").json()["location_filter"]["search_country"] == "Spain"


def test_the_prefill_get_works_before_any_resume_is_uploaded(client):
    _app, c = client
    payload = _prefill(c)
    assert payload["resume_on_file"] is False
    assert payload["extraction"] == op.EXTRACTION_NONE
    assert set(payload["fields"]) == set(op.PREFILL_FIELD_NAMES)


def test_the_prefill_get_never_returns_the_resume_filesystem_path(client):
    app, c = client
    assert _upload(c, NURSE).status_code == 200
    stored = app.state.config.resume.path
    assert stored

    body = c.get("/api/onboarding/prefill")
    for fragment in (stored, stored.replace("\\", "/"), stored.replace("\\", "\\\\")):
        assert fragment not in body.text


# ── Editing a pre-filled value ───────────────────────────────────────────────


def test_editing_a_prefilled_value_persists_the_edit_not_the_derived_value(client):
    """The DoD's third clause. The edit is a *search setting*, so it lands in
    config, and completing onboarding must not re-apply the derived value over
    it."""
    _app, c = client
    assert _upload(c, NURSE).status_code == 200
    derived = _prefill(c)["fields"]
    assert derived[op.PREFILL_SEARCH_TERMS]["value"] != ["Theatre Nurse"]
    assert derived[op.PREFILL_SEARCH_COUNTRY]["value"] == "Ireland"

    finish = c.put(
        "/api/settings",
        json={
            "location_filter": {"search_country": "Spain"},
            "sources": [
                {
                    "name": "linkedin",
                    "enabled": True,
                    "site_names": ["linkedin"],
                    "search_terms": ["Theatre Nurse"],
                    "location": "Spain",
                }
            ],
            "role_filter": {"job_functions": ["Health Care Provider"], "seniority_levels": []},
        },
    )
    assert finish.status_code == 200, finish.text

    settings = c.get("/api/settings").json()
    assert settings["location_filter"]["search_country"] == "Spain"
    primary = next(s for s in settings["sources"] if s["name"] == "linkedin")
    assert primary["search_terms"] == ["Theatre Nurse"]
    assert primary["location"] == "Spain"
    assert settings["role_filter"]["job_functions"] == ["Health Care Provider"]

    # And re-reading the prefill does not overwrite what was saved: it is a
    # read model, so it still reports the resume's own answer.
    assert _prefill(c)["fields"][op.PREFILL_SEARCH_COUNTRY]["value"] == "Ireland"
    assert c.get("/api/settings").json()["location_filter"]["search_country"] == "Spain"


def test_editing_a_prefilled_search_setting_does_not_write_the_profile_user_edit_layer(client, tmp_path):
    """The decision this task had to make, stated as a test.

    There are two places a "user edit" can live: T50's profile user-edit layer
    (``data/user_extras.yaml``, corrections to facts *about the person*) and
    config (search *settings*). Onboarding writes **config only**.

    Why: a search term is not a claim about the person — someone may well
    search for a title they have never held — and the profile layer is the one
    thing a re-parse can never overwrite, so silently pinning
    profile fields during onboarding would quietly disable T54's accept-per-
    field re-parse for every field onboarding happened to touch.
    """
    _app, c = client
    assert _upload(c, NURSE).status_code == 200
    _prefill(c)

    from jobfinder import paths

    extras = paths.user_extras_path()
    before = extras.read_text(encoding="utf-8") if extras.exists() else None

    assert (
        c.put(
            "/api/settings",
            json={
                "location_filter": {"search_country": "Spain"},
                "sources": [
                    {
                        "name": "linkedin",
                        "enabled": True,
                        "site_names": ["linkedin"],
                        "search_terms": ["Theatre Nurse"],
                        "location": "Spain",
                    }
                ],
            },
        ).status_code
        == 200
    )

    after = extras.read_text(encoding="utf-8") if extras.exists() else None
    assert after == before, "completing onboarding must not touch the profile user-edit layer"

    # The derived profile still reports the resume's answer with heuristic
    # provenance — not a ``user`` provenance invented by a settings write.
    body = c.get("/api/resume/derived-profile").json()
    assert body["fields"]["country"]["provenance"] == "heuristic"
    assert body["fields"]["country"]["value"] == "Ireland"


# ── The sparse fixture: partial is not all-or-nothing ────────────────────────


def test_a_resume_missing_a_country_still_prefills_the_titles_it_does_state(client):
    """``teacher_sparse.txt`` states no country. A blank the user can fill in is
    honest; a guessed country is a silent skew on every search."""
    _app, c = client
    assert _upload(c, TEACHER).status_code == 200
    fields = _prefill(c)["fields"]

    assert fields[op.PREFILL_SEARCH_COUNTRY]["value"] == ""
    assert fields[op.PREFILL_SEARCH_COUNTRY]["provenance"] == "absent"
    assert "Mathematics Teacher" in fields[op.PREFILL_SEARCH_TERMS]["value"]
    assert fields[op.PREFILL_SEARCH_TERMS]["provenance"] == "heuristic"


# ── The config written by the generated YAML stays loadable ──────────────────


def test_a_prefilled_country_round_trips_into_the_saved_config_file(client, isolated_app):
    """What onboarding writes has to survive a reload — the pre-fill is only
    useful if it is a real ``PUT /api/settings`` body."""
    app, c = client
    assert _upload(c, NURSE).status_code == 200
    country = _prefill(c)["fields"][op.PREFILL_SEARCH_COUNTRY]["value"]

    assert c.put("/api/settings", json={"location_filter": {"search_country": country}}).status_code == 200

    from jobfinder import paths

    raw = yaml.safe_load(paths.config_path().read_text(encoding="utf-8"))
    assert raw["location_filter"]["search_country"] == "Ireland"
