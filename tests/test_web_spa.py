"""Tests for serving the built SPA from the app process (T34).

``jobfinder/web/app.py`` deliberately left this unwired through Phase 3
because ``frontend/dist`` did not exist yet. It exists now, so
``create_app`` mounts it with FastAPI's ``app.frontend()``.

What these pin down is the mount's blast radius rather than FastAPI's
documented "path operations are checked first": the SPA must be reachable
at ``/`` with no session cookie (the HTML has to load before the token
exchange can run), and it must not answer for ``/api/*`` — a 200
``index.html`` where a 404 JSON body belongs is the sort of bug that turns
a typo'd endpoint into a mystery parse error months later.

``frontend/dist`` is a build artifact and is **git-ignored**
(``frontend/.gitignore``), so a fresh clone does not have one until
``npm run build`` runs. These tests therefore skip rather than fail when
it is absent: the backend suite must not require Node to be installed,
and a red test in that situation would report a missing build artifact as
a broken app. The skip reason names the command to run.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

PORT = 8759
BASE_URL = f"http://127.0.0.1:{PORT}"


def _dist_is_built() -> bool:
    from jobfinder import paths

    return (paths.frontend_dist_dir() / "index.html").is_file()


requires_dist = pytest.mark.skipif(
    not _dist_is_built(),
    reason="frontend/dist is a git-ignored build artifact — run `npm run build` in frontend/",
)


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
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


@requires_dist
def test_root_serves_the_spa_without_a_session_cookie(client) -> None:
    """The HTML has to load *before* the token exchange can happen."""
    app, c = client
    resp = c.get("/")
    assert resp.status_code == 200
    assert '<div id="root">' in resp.text or "<title>" in resp.text


@requires_dist
def test_hashed_asset_is_served(client) -> None:
    from jobfinder import paths

    app, c = client
    assets = sorted((paths.frontend_dist_dir() / "assets").glob("*.js"))
    assert assets, "no built JS bundle found in frontend/dist/assets"
    resp = c.get(f"/assets/{assets[0].name}")
    assert resp.status_code == 200


@requires_dist
def test_html_accepting_request_to_an_unknown_api_path_is_still_a_404(client) -> None:
    """No index.html fallback, so /api/* keeps its own error responses.

    This is the test that drove ``fallback=None`` in ``create_app``.
    FastAPI's index.html fallback fires for any request whose ``Accept``
    includes ``text/html`` — i.e. every address-bar navigation — so with
    it enabled, ``GET /api/typo`` returns a 200 HTML shell and a genuine
    API 404 is invisible. This SPA has no client-side router (nothing in
    ``frontend/src`` rewrites the path), so the fallback would buy
    nothing in exchange.
    """
    app, c = client
    # Authenticate first: without a session cookie this path 401s in
    # middleware before routing is even consulted, which would make the
    # assertion pass for the wrong reason.
    assert c.post("/api/auth/exchange", json={"token": app.state.launch_token.value}).status_code == 200
    resp = c.get("/api/definitely-not-a-route", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert "<title>" not in resp.text


@requires_dist
def test_unknown_non_api_path_is_a_404_not_the_app_shell(client) -> None:
    app, c = client
    resp = c.get("/some/path/that/does/not/exist", headers={"Accept": "text/html"})
    assert resp.status_code == 404


@requires_dist
def test_unknown_api_path_is_a_404_json_body_not_the_spa(client) -> None:
    app, c = client
    resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
    assert resp.status_code == 200
    resp = c.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "<title>" not in resp.text


@requires_dist
def test_api_routes_are_not_shadowed_by_the_frontend_mount(client) -> None:
    app, c = client
    assert c.get("/api/health").status_code == 200
    resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
    assert resp.status_code == 200
    # Still an honest 405, not an HTML page (see tests/test_web_control.py).
    assert c.get("/api/stop").status_code == 405


def test_create_app_survives_a_missing_dist_directory(isolated_app, monkeypatch, tmp_path) -> None:
    """A source checkout that has never run ``npm run build`` must still boot.

    The API-only app is useful (that is what most of the test suite
    exercises); crashing at import time because a build artifact is absent
    would make every backend test depend on Node.
    """
    monkeypatch.setattr(isolated_app.paths, "frontend_dist_dir", lambda: tmp_path / "nope" / "dist")
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/").status_code == 404


def test_frozen_bundle_resolves_dist_inside_the_bundle(monkeypatch, tmp_path) -> None:
    """One-dir PyInstaller: ``sys._MEIPASS`` is the ``_internal`` dir.

    The build spec copies the Vite output to ``frontend/dist`` *inside*
    it, so the one expression ``get_resource_dir() / "frontend" / "dist"``
    has to cover both source and frozen runs — this is the reason T33 kept
    ``paths.is_frozen()``.
    """
    from jobfinder import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)

    assert paths.frontend_dist_dir() == tmp_path / "_internal" / "frontend" / "dist"
