#!/usr/bin/env python
"""Build the distributable JobFinder app (T34).

    .venv/Scripts/python.exe -m pip install -r requirements-build.txt
    .venv/Scripts/python.exe scripts/build_app.py

Produces a one-dir bundle at ``dist/JobFinder/`` (``JobFinder.exe`` on
Windows). This replaces the Qt-era ``build.py`` + ``build-mac.sh`` +
``build-windows.bat`` + Inno Setup chain T33 deleted; there is deliberately
no installer step. An installer for an unsigned binary adds a second
SmartScreen/Gatekeeper prompt and a second thing to get wrong, and
signing/notarization is the owner's call (it needs certificates this repo
does not and should not hold).

Two ordering rules the script enforces rather than documents:

1. ``npm run build`` runs **first**. ``frontend/dist`` is git-ignored, so
   a fresh clone has no SPA; bundling without it yields an app that serves
   the API and a blank page, which looks like a backend bug.
2. PyInstaller runs from the repo root with ``jobfinder.spec``, whose
   relative ``datas`` paths are resolved against the spec's own directory.

Nothing here is a test: the verification the task's DoD asks for (running
the artifact from outside the repo with no venv on PATH) is a separate,
manual step, because it is precisely about *not* being run by the build
environment.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"
SPEC = REPO_ROOT / "jobfinder.spec"
OUT_DIR = REPO_ROOT / "dist" / "JobFinder"


def _run(command: list[str], *, cwd: Path) -> None:
    printable = " ".join(command)
    print(f"\n$ {printable}  (in {cwd})", flush=True)
    result = subprocess.run(command, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(f"failed ({result.returncode}): {printable}")


def build_frontend() -> None:
    """``npm run build`` -> ``frontend/dist``.

    ``shutil.which`` rather than a bare ``"npm"``: on Windows npm is
    ``npm.cmd`` and ``subprocess.run`` without a shell will not find it.
    """
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit(
            "npm was not found on PATH, so the SPA cannot be built.\n"
            "Install Node.js, or run `npm run build` in frontend/ elsewhere and re-run with --skip-frontend."
        )
    _run([npm, "run", "build"], cwd=FRONTEND)


def build_bundle(*, clean: bool) -> None:
    command = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"]
    if clean:
        command.append("--clean")
    _run(command, cwd=REPO_ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Reuse the existing frontend/dist instead of running `npm run build`.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Keep PyInstaller's build/ cache (faster, but stale modules can survive a rename).",
    )
    args = parser.parse_args(argv)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PyInstaller is not installed. It is a build-only dependency, kept out of requirements.txt:\n"
            f"    {sys.executable} -m pip install -r requirements-build.txt"
        ) from None

    if args.skip_frontend:
        if not (FRONTEND / "dist" / "index.html").is_file():
            raise SystemExit("--skip-frontend was passed but frontend/dist/index.html does not exist.")
        print("Skipping the frontend build; using the existing frontend/dist.")
    else:
        build_frontend()

    build_bundle(clean=not args.no_clean)

    exe = OUT_DIR / ("JobFinder.exe" if sys.platform == "win32" else "JobFinder")
    if not exe.exists():
        raise SystemExit(f"build reported success but {exe} is missing")
    print(f"\nBuilt {OUT_DIR}")
    print(f"Run it with: {exe}")
    print(
        "\nBefore distributing: FillerBot needs a Chromium. The packaged app looks for it under the data\n"
        "directory, because jobfinder/paths.py sets PLAYWRIGHT_BROWSERS_PATH to <data dir>/browsers at\n"
        "startup. `playwright install` does not read JOBFINDER_DATA_DIR, so point it at that directory\n"
        "explicitly:\n"
        '    PLAYWRIGHT_BROWSERS_PATH="$(python -c "import jobfinder.paths as p; print(p.get_data_dir() / \'browsers\')")" \\\n'
        "        python -m playwright install chromium\n"
        "docs/RUNNING.md has the Windows form and the copy-it-to-another-machine case."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
