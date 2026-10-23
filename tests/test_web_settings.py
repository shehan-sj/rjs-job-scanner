"""Tests for jobfinder.web.settings (T18) — GET/PUT /api/settings.

the product spec: API keys are write-only over HTTP — reads report
only whether a key is set, never the value, for both the Anthropic key
and per-source credentials (``SourceConfig.extra``).
"""

from __future__ import annotations

import importlib

import pytest
import yaml
from fastapi.testclient import TestClient

PORT = 8760
BASE_URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    # The settings DoD explicitly requires that "no key in config" is
    # distinguished from "no key available at all" — a real
    # ANTHROPIC_API_KEY in the test runner's environment would make
    # api_key_set=True even with an empty config.anthropic.api_key,
    # silently changing behaviour on a machine (or future CI) that
    # happens to have it set.
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
        yield app, c


@pytest.fixture
def authed_client(client):
    app, c = client
    resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
    assert resp.status_code == 200, resp.text
    return app, c


def _config_path(app):
    from jobfinder import paths

    return paths.config_path()


def _seed_secrets(app) -> None:
    """Seed the Anthropic key plus jsearch/adzuna per-source credentials
    (jsearch_source.py:48, adzuna_source.py:37-38) directly on the live
    config object the app holds, mirroring what a real PUT/config.yaml
    would contain.
    """
    from jobfinder.config import SourceConfig

    config = app.state.config
    config.anthropic.api_key = "sk-ant-super-secret-key-0001"  # gitleaks:allow — synthetic test fixture
    config.sources = [
        SourceConfig(
            name="jsearch",
            enabled=True,
            extra={"api_key": "rapidapi-secret-abcd"},  # gitleaks:allow — fixture
        ),
        SourceConfig(
            name="adzuna",
            enabled=True,
            extra={
                "app_id": "adzuna-id-1234",  # gitleaks:allow — fixture
                "app_key": "adzuna-key-5678",  # gitleaks:allow — fixture
            },
        ),
    ]


# ── Redaction ────────────────────────────────────────────────────────────


def test_get_settings_never_leaks_anthropic_key_substring(authed_client) -> None:
    app, c = authed_client
    _seed_secrets(app)

    resp = c.get("/api/settings")
    assert resp.status_code == 200, resp.text
    assert "sk-ant-" not in resp.text
    body = resp.json()
    assert body["anthropic"]["api_key"] == ""
    assert body["anthropic"]["api_key_set"] is True
    assert body["anthropic"]["api_key_last4"] == "0001"


def test_get_settings_redacts_per_source_extra_credentials(authed_client) -> None:
    app, c = authed_client
    _seed_secrets(app)

    resp = c.get("/api/settings")
    assert resp.status_code == 200, resp.text
    assert "rapidapi-secret-abcd" not in resp.text
    assert "adzuna-id-1234" not in resp.text
    assert "adzuna-key-5678" not in resp.text

    sources = {s["name"]: s for s in resp.json()["sources"]}
    jsearch_extra = sources["jsearch"]["extra"]
    assert "api_key" not in jsearch_extra
    assert jsearch_extra["api_key_set"] is True
    assert jsearch_extra["api_key_last4"] == "abcd"

    adzuna_extra = sources["adzuna"]["extra"]
    assert "app_id" not in adzuna_extra
    assert "app_key" not in adzuna_extra
    assert adzuna_extra["app_id_set"] is True
    assert adzuna_extra["app_key_set"] is True
    assert adzuna_extra["app_id_last4"] == "1234"
    assert adzuna_extra["app_key_last4"] == "5678"


def test_get_settings_redacts_unforeseen_sensitive_key_by_pattern(authed_client) -> None:
    """A future source adds a credential-shaped key this code has never
    heard of by name (e.g. "client_secret") — redaction must still catch
    it, because it's matched by pattern, not an allowlist.
    """
    app, c = authed_client
    from jobfinder.config import SourceConfig

    app.state.config.sources = [
        SourceConfig(name="future_source", enabled=True, extra={"client_secret": "brand-new-secret-value"}),
    ]

    resp = c.get("/api/settings")
    assert resp.status_code == 200, resp.text
    assert "brand-new-secret-value" not in resp.text
    extra = next(s for s in resp.json()["sources"] if s["name"] == "future_source")["extra"]
    assert "client_secret" not in extra
    assert extra["client_secret_set"] is True


def test_api_key_set_reflects_environment_fallback(authed_client, monkeypatch) -> None:
    """``Config.anthropic_api_key`` falls back to $ANTHROPIC_API_KEY when
    config.anthropic.api_key is blank (config.py:137). ``api_key_set``
    must reflect that effective value, not just the raw config field.
    """
    app, c = authed_client
    # config.example.yaml ships "anthropic: api_key:" blank, which
    # load_config (jobfinder/config.py) parses as None, not "" — either
    # way it's falsy, which is all the fallback logic (config.py:137)
    # relies on.
    assert not app.state.config.anthropic.api_key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-environment-only")

    resp = c.get("/api/settings")
    assert resp.status_code == 200, resp.text
    assert resp.json()["anthropic"]["api_key_set"] is True
    assert "sk-ant-" not in resp.text


# ── Round trip ───────────────────────────────────────────────────────────


def test_get_put_get_round_trip_is_byte_identical_and_preserves_unknown_keys(authed_client) -> None:
    app, c = authed_client
    _seed_secrets(app)
    config_path = _config_path(app)

    # Seed config.yaml with an unknown top-level key no dataclass models,
    # the same way save_config()'s own docstring describes an unmodeled
    # setting surviving a round trip.
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw["some_future_key"] = 1
    config_path.write_text(yaml.dump(raw, sort_keys=False), encoding="utf-8")

    first = c.get("/api/settings")
    assert first.status_code == 200, first.text

    put_resp = c.put("/api/settings", json=first.json())
    assert put_resp.status_code == 200, put_resp.text

    second = c.get("/api/settings")
    assert second.status_code == 200, second.text
    assert second.content == first.content

    raw_after = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw_after["some_future_key"] == 1


def test_round_trip_preserves_secrets_put_never_saw(authed_client) -> None:
    """The PUT body (echoing GET's redacted shape) never contains the raw
    secret values — they must still be intact after the PUT, not wiped.
    """
    app, c = authed_client
    _seed_secrets(app)

    body = c.get("/api/settings").json()
    put_resp = c.put("/api/settings", json=body)
    assert put_resp.status_code == 200, put_resp.text

    assert app.state.config.anthropic.api_key == "sk-ant-super-secret-key-0001"
    sources = {s.name: s for s in app.state.config.sources}
    assert sources["jsearch"].extra["api_key"] == "rapidapi-secret-abcd"
    assert sources["adzuna"].extra["app_id"] == "adzuna-id-1234"
    assert sources["adzuna"].extra["app_key"] == "adzuna-key-5678"


def test_put_can_actually_change_a_credential_via_its_real_key_name(authed_client) -> None:
    app, c = authed_client
    _seed_secrets(app)

    body = c.get("/api/settings").json()
    body["sources"][0]["extra"]["api_key"] = "brand-new-jsearch-key"
    resp = c.put("/api/settings", json=body)
    assert resp.status_code == 200, resp.text

    jsearch = next(s for s in app.state.config.sources if s.name == "jsearch")
    assert jsearch.extra["api_key"] == "brand-new-jsearch-key"


# ── T59: the filter panel's freshness-only partial `sources` write ───────


def test_sources_only_partial_put_does_not_wipe_a_credential(authed_client) -> None:
    """T59's filter panel (frontend/src/filters/filterPanelLogic.ts,
    `sourcesWithUniformHours`) sends a *partial* body containing only
    `{"sources": [...]}` — not the whole echoed `SettingsUpdate` the
    other tests above exercise — built from the fields `GET` itself
    returned, with `hours_old` overridden. `GET`'s redacted `extra`
    never contains the raw credential at all (only `<key>_set`/
    `<key>_last4` shadow keys, jobfinder/web/redact.py's
    `redact_extra`), so echoing it back must leave the stored credential
    exactly as it was — not blanked, not replaced by the shadow keys
    themselves landing in `extra` as if they were real fields.
    """
    app, c = authed_client
    _seed_secrets(app)

    before = c.get("/api/settings").json()
    before_sources = before["sources"]

    # Exactly `sourcesWithUniformHours`'s shape: every field copied from
    # the GET response, only `hours_old` changed.
    patched_sources = [
        {
            "name": s["name"],
            "enabled": s["enabled"],
            "site_names": s["site_names"],
            "search_terms": s["search_terms"],
            "location": s["location"],
            "results_wanted": s["results_wanted"],
            "hours_old": 96,
            "linkedin_fetch_description": s["linkedin_fetch_description"],
            "extra": s["extra"],
        }
        for s in before_sources
    ]

    resp = c.put("/api/settings", json={"sources": patched_sources})
    assert resp.status_code == 200, resp.text

    # The raw credentials must be exactly what `_seed_secrets` set —
    # never the mask, never empty, never the `_set`/`_last4` shadow keys
    # bleeding into the real field.
    live = {s.name: s for s in app.state.config.sources}
    assert live["jsearch"].extra["api_key"] == "rapidapi-secret-abcd"
    assert live["adzuna"].extra["app_id"] == "adzuna-id-1234"
    assert live["adzuna"].extra["app_key"] == "adzuna-key-5678"
    assert "api_key_set" not in live["jsearch"].extra or live["jsearch"].extra["api_key"] != "True"

    after = c.get("/api/settings").json()
    assert "sk-ant-" not in resp.text  # belt-and-suspenders: still redacted on the way out
    for src in after["sources"]:
        assert src["hours_old"] == 96

    # The rest of the payload — top-level fields this partial body never
    # named — is untouched too (T46's exact shape, at the array level).
    expected = dict(before)
    expected["sources"] = [dict(s, hours_old=96) for s in before_sources]
    assert after == expected


def test_sources_only_partial_put_leaves_match_threshold_and_tag_visibility_alone(authed_client) -> None:
    """The other half of T46's regression, for the panel's own write
    shape: a `{"sources": [...]}`-only body must not reset
    `match_threshold`/`tag_visibility`/`quality_filter` to their
    Pydantic defaults the way the pre-T46 endpoint did for *every*
    omitted section.
    """
    app, c = authed_client
    _seed_non_default_full_config(app)

    before = c.get("/api/settings").json()
    patched_sources = [dict(s, hours_old=48) for s in before["sources"]] or [
        {
            "name": "linkedin",
            "enabled": True,
            "site_names": ["linkedin"],
            "search_terms": [],
            "location": "",
            "results_wanted": 20,
            "hours_old": 48,
            "linkedin_fetch_description": True,
            "extra": {},
        }
    ]

    resp = c.put("/api/settings", json={"sources": patched_sources})
    assert resp.status_code == 200, resp.text
    after = c.get("/api/settings").json()

    assert after["match_threshold"] == before["match_threshold"] == 71
    assert after["tag_visibility"] == before["tag_visibility"]
    assert after["quality_filter"] == before["quality_filter"]
    assert after["check_interval_minutes"] == before["check_interval_minutes"] == 37


# ── Validation ───────────────────────────────────────────────────────────


def _seed_non_default_full_config(app) -> None:
    """Load every section with values that differ from every one of
    config.py's dataclass defaults (the product spec's measured repro), so a
    partial-PUT test that fails to preserve a section is guaranteed to be
    caught — asserting against an empty/default config would hide the
    defect, since "reset to default" and "left alone" would look the same.
    """
    from jobfinder.config import (
        AgenticFilterConfig,
        LocationFilterConfig,
        NotificationsConfig,
        QualityFilterConfig,
        RoleFilterConfig,
        TagVisibilityConfig,
        UIConfig,
    )

    config = app.state.config
    config.check_interval_minutes = 37
    config.match_threshold = 71
    config.filter_contracts = False
    config.notifications = NotificationsConfig(auto_dismiss_seconds=12)
    config.location_filter = LocationFilterConfig(
        preferred_regions=["Canada"], search_country="Canada", remote_ok_worldwide=False
    )
    config.tag_visibility = TagVisibilityConfig(
        hide_full_time=True,
        hide_part_time=True,
        hide_contract=True,
        hide_temporary=True,
        hide_internship=False,
        hide_remote=True,
        hide_hybrid=True,
        hide_onsite=True,
    )
    config.agentic_filter = AgenticFilterConfig(enabled=True, min_local_score=42)
    config.role_filter = RoleFilterConfig(job_functions=["quality assurance"], seniority_levels=["senior"])
    config.quality_filter = QualityFilterConfig(
        hide_staffing_agencies=False,
        hide_promoted=True,
        min_salary=123456,
        min_company_size=500,
        max_post_age_days=14,
    )
    config.ui = UIConfig(
        show_log_console=False,
        theme="light",
        console_show_timestamp=False,
        console_show_level=False,
    )


# ── T46: partial PUT must not destroy omitted settings ──────────────────


def test_single_field_put_leaves_every_other_section_byte_identical(authed_client) -> None:
    """The product spec's measured repro: a body of {"ui": {"theme": "dark"}}
    used to reset check_interval_minutes, match_threshold, quality_filter,
    role_filter, tag_visibility and agentic_filter because every section
    was rebuilt from the request body's Pydantic defaults. Asserted
    against the non-default config seeded above, not an empty one.
    """
    app, c = authed_client
    _seed_non_default_full_config(app)

    before = c.get("/api/settings").json()
    resp = c.put("/api/settings", json={"ui": {"theme": "dark"}})
    assert resp.status_code == 200, resp.text
    after = c.get("/api/settings").json()

    expected = dict(before)
    expected["ui"] = dict(before["ui"], theme="dark")
    assert after == expected


def test_nested_partial_put_changes_only_the_named_field(authed_client) -> None:
    """A partial write to a nested section ({"quality_filter": {"min_salary": 500}})
    must leave the other quality_filter fields untouched too, not just
    the other top-level sections.
    """
    app, c = authed_client
    _seed_non_default_full_config(app)

    before = c.get("/api/settings").json()
    resp = c.put("/api/settings", json={"quality_filter": {"min_salary": 500}})
    assert resp.status_code == 200, resp.text
    after = c.get("/api/settings").json()

    expected = dict(before)
    expected["quality_filter"] = dict(before["quality_filter"], min_salary=500)
    assert after == expected
    assert app.state.config.quality_filter.min_company_size == 500
    assert app.state.config.quality_filter.hide_staffing_agencies is False
    assert app.state.config.quality_filter.hide_promoted is True
    assert app.state.config.quality_filter.max_post_age_days == 14


def test_settings_update_defaults_derive_from_config_constants(tmp_path, monkeypatch) -> None:
    """Every source of a shipped default must agree.

    T46 added this as the divergence guard for the three top-level
    settings, comparing ``SettingsUpdate``'s defaults to config.py's
    constants. T48 widened it to the fourth source — the
    ``config.example.yaml`` a real install is actually seeded from — and to
    the two fields that were still disagreeing three ways when T46 landed:
    ``location_filter`` (dataclass said ``United States``, the template
    left it blank and pointed ``sources[0].location`` at ``Canada``) and
    ``ui.theme`` (the template shipped a colorhunt.co URL).

    Deliberately one test rather than a parallel second one: a separate
    test asserting the same class of thing a different way is how these
    two sources drifted apart again after T46.
    """
    import dataclasses
    import re

    from jobfinder.config import (
        VALID_UI_THEMES,
        Config,
        _DEFAULT_CHECK_INTERVAL_MINUTES,
        _DEFAULT_FILTER_CONTRACTS,
        _DEFAULT_MATCH_THRESHOLD,
        load_config,
    )
    from jobfinder.matcher.location_parser import _COUNTRY_ALIASES
    from jobfinder.web.settings import SettingsUpdate

    defaults = SettingsUpdate()
    assert defaults.check_interval_minutes == _DEFAULT_CHECK_INTERVAL_MINUTES
    assert defaults.match_threshold == _DEFAULT_MATCH_THRESHOLD
    assert defaults.filter_contracts == _DEFAULT_FILTER_CONTRACTS
    assert defaults.location_filter.preferred_regions == Config().location_filter.preferred_regions
    assert defaults.location_filter.search_country == Config().location_filter.search_country
    assert defaults.ui.theme == Config().ui.theme

    # The fresh-install path for real: seed a clean data dir the way
    # paths.setup_data_environment() does on first run, then load what
    # landed there — not config.example.yaml read in place.
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "fresh-install"))
    import jobfinder.paths as paths

    importlib.reload(paths)
    try:
        paths.setup_data_environment()
        paths_config_text = paths.config_path().read_text(encoding="utf-8")
        seeded = load_config(paths.config_path())
    finally:
        importlib.reload(paths)

    assert seeded.location_filter == Config().location_filter
    assert seeded.ui.theme == Config().ui.theme

    # DoD: a fresh install's theme is a theme, not a URL — asserted on the
    # seeded file's *raw* value, because ``load_config`` coerces an unknown
    # theme to the default (see ``_coerce_theme``), so asserting only on the
    # loaded Config would pass with that URL back in the template.
    seeded_raw = yaml.safe_load(paths_config_text)
    assert seeded_raw["ui"]["theme"] in VALID_UI_THEMES, (
        f"config.example.yaml ships ui.theme={seeded_raw['ui']['theme']!r} — the product spec"
    )

    # DoD: no geographic string in *either* default. Asserted against the
    # repo's own country vocabulary rather than eyeballed, and over the
    # parsed values (not the file text) so the explanatory comments in the
    # template are not what's being checked.
    def _strings(value) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for v in value.values() for s in _strings(v)]
        if isinstance(value, (list, tuple)):
            return [s for v in value for s in _strings(v)]
        return []

    candidates = _strings(dataclasses.asdict(Config())) + _strings(dataclasses.asdict(seeded))
    for text in candidates:
        for alias, canonical in _COUNTRY_ALIASES.items():
            assert not re.search(rf"\b{re.escape(alias)}\b", text.lower()), (
                f"shipped default {text!r} names a country ({canonical}) — the product spec says there is no "
                f"geographic default; the résumé supplies it"
            )


def test_put_with_match_threshold_over_bound_is_rejected_422(authed_client) -> None:
    app, c = authed_client
    body = c.get("/api/settings").json()
    body["match_threshold"] = 150
    resp = c.put("/api/settings", json=body)
    assert resp.status_code == 422, resp.text


def test_put_attempting_to_set_resume_path_is_rejected_422(authed_client) -> None:
    app, c = authed_client
    body = c.get("/api/settings").json()
    body["resume"] = {"path": "C:/Users/someone/.ssh/id_rsa"}
    resp = c.put("/api/settings", json=body)
    assert resp.status_code == 422, resp.text

    # And the attempt must not have touched the live config either.
    assert app.state.config.resume.path != "C:/Users/someone/.ssh/id_rsa"


# ── Scheduler reschedule wiring ───────────────────────────────────────────


def test_put_changing_check_interval_reschedules_the_live_scheduler(authed_client) -> None:
    app, c = authed_client
    body = c.get("/api/settings").json()
    original_interval = body["check_interval_minutes"]
    body["check_interval_minutes"] = original_interval + 5

    resp = c.put("/api/settings", json=body)
    assert resp.status_code == 200, resp.text
    assert app.state.scheduler._interval == original_interval + 5


# ── Auth enforcement ───────────────────────────────────────────────────


def test_get_settings_rejects_request_with_no_session_cookie(client) -> None:
    app, c = client
    resp = c.get("/api/settings")
    assert resp.status_code == 401


def test_put_settings_rejects_request_with_no_session_cookie(client) -> None:
    app, c = client
    resp = c.put("/api/settings", json={})
    assert resp.status_code == 401


# ── T45: report the uploaded resume's name, never a path ────────────────


def test_fresh_config_reports_no_resume_uploaded(authed_client) -> None:
    """A fresh install (no upload has ever happened) must report
    ``uploaded: false`` and no filename — not the default placeholder
    path (``resume.pdf``) dressed up as if it were a real upload.
    """
    app, c = authed_client
    body = c.get("/api/settings").json()
    assert body["resume"] == {"uploaded": False, "filename": None}


def test_get_settings_reports_real_uploaded_filename_with_no_client_state(authed_client) -> None:
    """Simulates "a fresh browser profile with no localStorage": the
    filename comes back from a plain GET, with nothing client-side
    seeded.
    """
    app, c = authed_client
    from jobfinder.config import ResumeConfig

    app.state.config.resume = ResumeConfig(
        path=str(_write_fake_resume_file(app)), original_filename="candidate-qa-resume.pdf"
    )

    resp = c.get("/api/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resume"]["uploaded"] is True
    assert body["resume"]["filename"] == "candidate-qa-resume.pdf"


def test_get_settings_response_contains_no_filesystem_path(authed_client) -> None:
    """The data directory's own path string must never appear anywhere
    in the ``GET`` body — not as ``resume.path``, not nested, not as a
    substring of anything else.
    """
    from jobfinder import paths
    from jobfinder.config import ResumeConfig

    app, c = authed_client
    data_dir = str(paths.get_data_dir().resolve())
    fake_resume = _write_fake_resume_file(app)
    app.state.config.resume = ResumeConfig(path=str(fake_resume), original_filename="my-resume.pdf")

    resp = c.get("/api/settings")
    assert resp.status_code == 200, resp.text
    assert data_dir not in resp.text
    assert str(fake_resume) not in resp.text
    assert fake_resume.name not in resp.text  # the uuid storage name itself


def test_get_settings_sanitises_hand_edited_hostile_original_filename(authed_client) -> None:
    """Defense in depth: even if ``config.yaml`` was hand-edited (or
    corrupted) to put a path-shaped value directly into
    ``resume.original_filename``, ``GET`` must still never echo it as a
    path — it is re-sanitised on the way out, not just at upload time.
    """
    from jobfinder.config import ResumeConfig

    app, c = authed_client
    app.state.config.resume = ResumeConfig(path="resume.pdf", original_filename="../../etc/passwd")

    resp = c.get("/api/settings")
    assert resp.status_code == 200, resp.text
    assert resp.json()["resume"]["filename"] == "passwd"


def test_put_still_rejects_resume_path_with_422(authed_client) -> None:
    """Belt-and-suspenders alongside the existing
    ``test_put_attempting_to_set_resume_path_is_rejected_422`` above:
    the ``resume`` section ``GET`` now returns is read-only
    (``uploaded``/``filename`` only) and a ``path`` key under it is
    still rejected outright.
    """
    app, c = authed_client
    body = c.get("/api/settings").json()
    assert body["resume"] == {"uploaded": False, "filename": None}

    body["resume"]["path"] = "/etc/passwd"
    resp = c.put("/api/settings", json=body)
    assert resp.status_code == 422, resp.text


def test_put_echoing_gets_own_resume_section_is_accepted_and_ignored(authed_client) -> None:
    """The common client pattern — fetch, tweak one unrelated field, PUT
    the whole payload back — must not break just because ``GET`` now
    includes a ``resume`` section.
    """
    app, c = authed_client
    from jobfinder.config import ResumeConfig

    app.state.config.resume = ResumeConfig(path="resume.pdf", original_filename="my-resume.pdf")

    body = c.get("/api/settings").json()
    assert body["resume"] == {"uploaded": True, "filename": "my-resume.pdf"}
    body["match_threshold"] = 55

    resp = c.put("/api/settings", json=body)
    assert resp.status_code == 200, resp.text
    assert app.state.config.match_threshold == 55
    # Still untouched — PUT can never change it, regardless of what was echoed.
    assert app.state.config.resume.original_filename == "my-resume.pdf"


def _write_fake_resume_file(app):
    """A uuid-named file inside the real uploads dir, standing in for a
    file ``POST /api/resume`` actually stored — used so path-shaped
    assertions below have a real resolved path to check against.
    """
    from jobfinder import paths

    uploads_dir = paths.resume_uploads_dir()
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / "11111111111111111111111111111111.pdf"
    dest.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return dest


def test_an_invalid_stored_theme_means_no_override_not_dark(caplog) -> None:
    """An unreadable ``ui.theme`` must leave the OS preference in charge.

    the product spec's broken template is the *only* way a config can hold an
    invalid theme, so every affected install is a pre-existing one. There is
    no ``auto`` member of ``VALID_UI_THEMES``, which makes "follow the OS"
    representable only as the absence of a value — and that is exactly what
    ``frontend/src/theme/theme.ts``'s ``applyServerTheme`` already does with
    an unrecognized theme.

    Coercing to ``dark`` instead would flip every one of those installs that
    sits on a light desktop from light to dark on its next load, over a
    cosmetic field they never set. We do not know what they wanted, so we
    return "" and they keep what they had. A fresh install is unaffected: the
    template ships an explicit ``dark``.
    """
    import logging

    from jobfinder.config import VALID_UI_THEMES, _coerce_theme

    assert _coerce_theme("dark") == "dark"
    assert _coerce_theme("light") == "light"
    assert _coerce_theme("  LIGHT ") == "light"

    # The exact value the template shipped, plus a plausible hand-edit.
    with caplog.at_level(logging.WARNING):
        assert _coerce_theme("https://colorhunt.co/palette/41431baeb784e3dbbbf8f3e1") == ""
        assert _coerce_theme("purple") == ""
    assert any("ui.theme" in r.message for r in caplog.records), caplog.records

    # Absent is not an error and must not warn.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert _coerce_theme(None) == ""
        assert _coerce_theme("") == ""
    assert not caplog.records, [r.message for r in caplog.records]

    # "" is deliberately NOT a valid theme — it is the absence of one.
    assert "" not in VALID_UI_THEMES
