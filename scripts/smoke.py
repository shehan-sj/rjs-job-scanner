#!/usr/bin/env python
"""Phase gate smoke test (T5) — one fast, offline command every later
migration phase must pass before it is considered done.

Runs in-process (no subprocess, no network, no Chrome, no Qt) in well under
10 seconds. Steps:

  1. Import every module under jobfinder/, with a sys.meta_path hook that
     raises if anything tries to import PySide6. This fails loudly the
     moment a Qt dependency creeps back into the "Qt-free core". Nothing is
     excluded any more: jobfinder/fillerbot/engine.py came off
     QThread/Signal in T29, and T33 deleted jobfinder/ui/ outright, so the
     whole package is now in scope (see EXCLUDED_MODULES below).
  2. Config save/load round-trip: save_config(Config()), then load_config()
     the result back and assert equality (T9).
  3. Database lifecycle: open a Database at a temp path, let
     _init_schema/_migrate run, insert one job, assert filter_unseen
     returns empty on the second call.
  4. Scoring: score one fixture job against one fixture profile; assert
     0 <= score <= 100 and all five signals keys are present.
  5. FastAPI app boots and serves a health check (T14): boot the app
     through TestClient's lifespan (not a raw ``create_app()`` call — the
     scheduler and DB only exist once lifespan startup has run) against an
     isolated JOBFINDER_DATA_DIR, then GET /api/health and assert 200.
  6. Frontend build output (T21): SKIP if `frontend/dist/` doesn't exist
     at all (no npm build has been run — a backend-only change should
     not need one just to pass this gate), FAIL if it exists but
     `index.html` is missing or empty, PASS if a real build is present.

Usage:
    .venv/Scripts/python.exe scripts/smoke.py
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Modules that are expected to still import PySide6 / Qt and are out of scope
# for this phase gate. Empty since T33: jobfinder/ui/* (the whole PySide6
# desktop UI) was the only entry and has been deleted, and
# jobfinder/fillerbot/engine.py came off QThread/Signal in T29. Step 1 now
# imports every module under jobfinder/ with no exceptions, which is the
# point — there is no longer anywhere in the package Qt is allowed to live.
# Adding an entry back here means admitting a Qt dependency, so it should
# need a task number and a reason.
EXCLUDED_MODULES: set[str] = set()


class _BlockQtImports:
    """sys.meta_path hook that raises on any attempt to import PySide6."""

    def find_module(self, fullname, path=None):  # pragma: no cover - legacy hook API
        return self.find_spec(fullname, path)

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            raise ImportError(f"Blocked import of {fullname!r} — the Qt-free core must not depend on PySide6.")
        return None  # defer to the normal import machinery


class SmokeSkip(Exception):
    """Raised by a step to report SKIP (not FAIL) with a reason — used
    when a step's precondition legitimately doesn't hold yet (e.g. no
    frontend build has been produced) rather than when something is
    actually broken.
    """


def _step(name: str):
    def decorator(fn):
        fn._smoke_step_name = name
        return fn

    return decorator


@_step("1. Import Qt-free core with PySide6 blocked")
def step_import_qt_free_core() -> None:
    sys.meta_path.insert(0, _BlockQtImports())

    import jobfinder

    discovered = {name for _finder, name, _ispkg in pkgutil.walk_packages(jobfinder.__path__, jobfinder.__name__ + ".")}
    discovered.add("jobfinder")

    to_import = sorted(discovered - EXCLUDED_MODULES)
    if not to_import:
        raise AssertionError("No modules discovered under jobfinder/ — package layout changed?")

    for module_name in to_import:
        importlib.import_module(module_name)


@_step("2. Config save/load round-trip")
def step_config_roundtrip() -> None:
    from jobfinder.config import Config, load_config, save_config

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.yaml"
        save_config(Config(), path)
        reloaded = load_config(path)
        if reloaded != Config():
            raise AssertionError(f"round-trip mismatch: {reloaded!r} != {Config()!r}")


@_step("3. Database lifecycle (schema, migrate, filter_unseen)")
def step_database_lifecycle() -> None:
    from jobfinder.matcher.models import JobPosting
    from jobfinder.storage.database import Database

    # T16 fixed the root cause: every `with self._connect()`
    # site now goes through `Database._session()`, which closes the
    # connection in a `finally` instead of leaving it to refcounting. That
    # is what let this revert from `mkdtemp()` + `rmtree(ignore_errors=True)`
    # back to the strict-cleanup `tempfile.TemporaryDirectory()` — on
    # Windows, a still-open handle is exactly what used to make its
    # `__exit__` raise `WinError 32` (file in use).
    with tempfile.TemporaryDirectory(prefix="jobfinder-smoke-") as tmp:
        db_path = str(Path(tmp) / "smoke.db")
        db = Database(db_path)  # runs _init_schema / _migrate

        job = JobPosting(
            job_id="smoke-job-1",
            title="QA Engineer",
            company="Smoke Co",
            location="Remote",
            description="Smoke test job posting.",
            url="https://example.com/smoke-job-1",
            source="smoke",
        )

        unseen_first = db.filter_unseen([job])
        if unseen_first != [job]:
            raise AssertionError(f"expected [job] on first filter_unseen call, got {unseen_first!r}")

        db.mark_seen([job])

        unseen_second = db.filter_unseen([job])
        if unseen_second != []:
            raise AssertionError(f"expected [] on second filter_unseen call, got {unseen_second!r}")


@_step("4. Scoring produces an in-bounds result with all signals")
def step_scoring() -> None:
    from jobfinder.matcher.local_matcher import score_job
    from jobfinder.matcher.models import JobPosting
    from jobfinder.resume.profiler import ResumeProfile

    profile = ResumeProfile(
        skills=["test automation", "selenium"],
        tools=["selenium", "jira"],
        job_titles=["QA Engineer"],
        seniority_levels=["Senior"],
        years_experience=5,
    )
    job = JobPosting(
        job_id="smoke-job-2",
        title="QA Engineer",
        company="Smoke Co",
        location="Remote",
        description="Selenium test automation with JIRA tracking.",
        url="https://example.com/smoke-job-2",
        source="smoke",
    )

    result = score_job(profile, job)

    if not (0 <= result.score <= 100):
        raise AssertionError(f"score out of bounds: {result.score}")

    expected_keys = {"title", "skills", "tools", "seniority", "function"}
    if set(result.signals.keys()) != expected_keys:
        raise AssertionError(f"signals keys mismatch: {sorted(result.signals.keys())}")


@_step("5. FastAPI app boots and serves a health check")
def step_fastapi_boot() -> None:
    import os
    import shutil

    from fastapi.testclient import TestClient

    # Isolated data dir (NG-4: throwaway, not cleaned up via a trailing rm —
    # mirrors step 3's mkdtemp + rmtree(ignore_errors=True) pattern, needed
    # here too since lifespan startup opens a sqlite3 connection that
    # Database never explicitly closes, see the product spec).
    tmp = tempfile.mkdtemp(prefix="jobfinder-smoke-web-")
    previous = os.environ.get("JOBFINDER_DATA_DIR")
    os.environ["JOBFINDER_DATA_DIR"] = tmp
    try:
        import jobfinder.paths as jf_paths

        importlib.reload(jf_paths)
        import jobfinder.web.app as web_app

        importlib.reload(web_app)

        # Fixed port: jobfinder.web.security (T15) allowlists Host/Origin
        # headers against the exact port create_app() is given, so
        # TestClient's base_url must match it.
        port = 8758
        app = web_app.create_app(port=port)
        with TestClient(app, base_url=f"http://127.0.0.1:{port}") as client:
            resp = client.get("/api/health")
        if resp.status_code != 200:
            raise AssertionError(f"expected 200 from /api/health, got {resp.status_code}")
        if resp.json() != {"status": "ok"}:
            raise AssertionError(f"unexpected health payload: {resp.json()!r}")
    finally:
        if previous is None:
            os.environ.pop("JOBFINDER_DATA_DIR", None)
        else:
            os.environ["JOBFINDER_DATA_DIR"] = previous
        shutil.rmtree(tmp, ignore_errors=True)


@_step("6. Frontend build output (frontend/dist/index.html)")
def step_frontend_build_exists() -> None:
    dist_dir = _REPO_ROOT / "frontend" / "dist"
    if not dist_dir.exists():
        raise SmokeSkip(
            "frontend/dist/ does not exist - no npm build has been run. "
            "A backend-only change should not need `npm run build` just to pass this gate."
        )

    index_html = dist_dir / "index.html"
    if not index_html.exists():
        raise AssertionError(f"{dist_dir} exists but index.html is missing - build output looks incomplete")
    if index_html.stat().st_size == 0:
        raise AssertionError(f"{index_html} exists but is empty - build output looks broken")


STEPS = [
    step_import_qt_free_core,
    step_config_roundtrip,
    step_database_lifecycle,
    step_scoring,
    step_fastapi_boot,
    step_frontend_build_exists,
]


def main() -> int:
    start = time.monotonic()
    failures: list[str] = []
    skipped: list[str] = []

    for step_fn in STEPS:
        name = step_fn._smoke_step_name
        try:
            step_fn()
        except SmokeSkip as exc:
            print(f"SKIP  {name}: {exc}")
            skipped.append(name)
        except Exception as exc:  # noqa: BLE001 - smoke gate reports, doesn't re-raise
            print(f"FAIL  {name}: {exc}")
            failures.append(name)
        else:
            print(f"PASS  {name}")

    elapsed = time.monotonic() - start
    passed = len(STEPS) - len(failures) - len(skipped)
    print(f"\n{passed}/{len(STEPS)} steps passed, {len(skipped)} skipped, in {elapsed:.2f}s")

    if failures:
        print(f"FAILED steps: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
