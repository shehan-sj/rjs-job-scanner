#!/usr/bin/env python
"""Does the packaged app actually contain the frontend that is checked in?

Why this exists: the owner rebuilt, restarted, and reported that finished
work was missing. It was not — ``dist/JobFinder/JobFinder.exe`` was six
hours older than the commits, and PyInstaller copies ``frontend/dist``
*into* the bundle at build time, so restarting the exe reruns whatever UI
it was built with. That is the second time a stale build has made shipped
work look absent; the first (Phase 8) was a whole CSS file that stopped
being bundled while every test stayed green.

So this compares three things that are easy to let drift apart:

  1. ``frontend/dist`` against ``frontend/src`` — is the checked-in build
     older than the sources it was built from?
  2. the bundle's copy of ``frontend/dist`` against the repo's — is the
     packaged app serving a different UI than the one in the tree?
  3. optionally, that a given string is present in the built assets
     (``--expect "Pause automatic scanning"``), which is the only way to
     prove a specific change reached the thing the user actually runs.

Exit 0 when everything lines up, 1 otherwise, naming what is stale. A
missing ``dist/JobFinder`` is **not** a failure — not every checkout has
built the exe — but it is reported rather than passed over.

Usage::

    .venv/Scripts/python.exe scripts/check_bundle_freshness.py
    .venv/Scripts/python.exe scripts/check_bundle_freshness.py --expect "Scanning now"
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
BUNDLE_DIST = REPO_ROOT / "dist" / "JobFinder" / "_internal" / "frontend" / "dist"


def newest_mtime(root: Path, patterns: tuple[str, ...]) -> tuple[float, Path | None]:
    """The most recently modified matching file under ``root``."""
    best = 0.0
    which: Path | None = None
    for pattern in patterns:
        for path in root.rglob(pattern):
            mtime = path.stat().st_mtime
            if mtime > best:
                best = mtime
                which = path
    return best, which


def assets_text(dist: Path) -> str:
    """Every built JS and CSS asset, concatenated."""
    parts: list[str] = []
    for pattern in ("*.js", "*.css"):
        for path in sorted((dist / "assets").glob(pattern)):
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        help="a string that must appear in the built assets (repeatable)",
    )
    args = parser.parse_args()

    problems: list[str] = []

    if not (FRONTEND_DIST / "index.html").is_file():
        print("frontend/dist/index.html missing — run `npm run build` in frontend/")
        return 1

    src_mtime, src_file = newest_mtime(FRONTEND_SRC, ("*.ts", "*.tsx", "*.css"))
    dist_mtime, _ = newest_mtime(FRONTEND_DIST, ("*.js", "*.css", "index.html"))
    if src_mtime > dist_mtime and src_file is not None:
        problems.append(
            f"frontend/dist is older than frontend/src (newest source: {src_file.relative_to(REPO_ROOT)}) "
            "— run `npm run build` in frontend/"
        )
    else:
        print("frontend/dist is at least as new as frontend/src: ok")

    repo_assets = assets_text(FRONTEND_DIST)
    for needle in args.expect:
        if needle not in repo_assets:
            problems.append(f"frontend/dist does not contain {needle!r} — the build predates that change")
        else:
            print(f"frontend/dist contains {needle!r}: ok")

    if not BUNDLE_DIST.is_dir():
        print("dist/JobFinder not built in this checkout — skipping the packaged-app comparison")
    else:
        bundle_assets = assets_text(BUNDLE_DIST)
        if bundle_assets.strip() != repo_assets.strip():
            problems.append(
                "the packaged app's frontend differs from frontend/dist — rebuild with "
                "`scripts/build_app.py`, or the exe will go on serving the old UI"
            )
        else:
            print("dist/JobFinder serves the same frontend as frontend/dist: ok")
        for needle in args.expect:
            if needle not in bundle_assets:
                problems.append(f"the packaged app does not contain {needle!r} — rebuild `scripts/build_app.py`")
            else:
                print(f"dist/JobFinder contains {needle!r}: ok")

    if problems:
        print("\nFAIL:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("\nPASS: the built frontend and the packaged app are both current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
