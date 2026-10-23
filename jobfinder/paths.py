"""Resolve the single explicit data root JobFinder reads and writes under.

Historically this module keyed everything off ``is_frozen()``: a dev run
used ``Path.cwd()`` and a frozen (.exe) run changed the process's working
directory into ``%APPDATA%\\JobFinder`` so the rest of the codebase's
relative-path literals (the database file, ``config.yaml``, the logs
folder) kept working unmodified.

That breaks the moment the process is started by something other than a
double-clicked .exe with a predictable cwd (a web server, a service, a test
runner invoked from an arbitrary directory): changing the process-wide
working directory is a *global* side effect, and nothing here ever
accounted for the possibility that ``is_frozen()`` could become
permanently False (T33 deleted the Qt PyInstaller build) while relative
paths were still in use — the app would then silently read and write a
different database depending on the working directory it was launched
from.

The fix: every caller asks this module for an absolute path
(``config_path()``, ``db_path()``, ``logs_dir()``, ...) instead of using a
relative-path literal, and the data root those paths are built from is
resolved once, explicitly, independent of cwd:

    1. ``JOBFINDER_DATA_DIR`` environment variable, if set.
    2. Otherwise ``platformdirs.user_data_dir("JobFinder")``.

No code path in this module changes the process's working directory.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

import platformdirs

logger = logging.getLogger(__name__)


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle.

    Kept for the bundled-resource lookup in :func:`get_resource_dir`, and
    because T34 reintroduces a PyInstaller build (of the web entrypoint, not
    the Qt app T33 deleted) that will need it. Nothing about
    *where user data lives* depends on this anymore — see :func:`get_data_dir`.
    """
    return getattr(sys, "frozen", False)


def get_data_dir() -> Path:
    """The single explicit data root for config, the DB, logs, and caches.

    Resolution order:
      1. ``JOBFINDER_DATA_DIR`` env var, if set — always honoured verbatim,
         and never auto-migrated into (tests and power users rely on this
         being a clean, explicit location).
      2. ``platformdirs.user_data_dir("JobFinder")`` otherwise.

    This is independent of the process's current working directory and of
    :func:`is_frozen`, which is what makes it safe to call from a web
    server started from anywhere.
    """
    override = os.environ.get("JOBFINDER_DATA_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_data_dir("JobFinder"))


def get_resource_dir() -> Path:
    """Directory containing bundled read-only assets (default config, icon)."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent.parent


def config_path() -> Path:
    return get_data_dir() / "config.yaml"


def db_path() -> Path:
    return get_data_dir() / "data" / "jobfinder.db"


def logs_dir() -> Path:
    return get_data_dir() / "logs"


def resume_profile_path() -> Path:
    """Cache for jobfinder/resume/profiler.py's extracted resume profile."""
    return get_data_dir() / "data" / "resume_profile.json"


def resume_uploads_dir() -> Path:
    """Where ``POST /api/resume`` (T18) stores uploaded resume files.

    The server generates the filename itself (a random token plus the
    sniffed extension) — this directory, and this directory alone, is
    where client-uploaded resume bytes are allowed to land. The product spec
    item 5: no client-supplied filesystem path is ever accepted; the
    upload handler additionally asserts the resolved destination path is
    inside :func:`get_data_dir` as defence in depth.
    """
    return get_data_dir() / "data" / "resume_uploads"


def filler_profile_path() -> Path:
    """Cache for jobfinder/fillerbot/profiler.py's extracted filler profile."""
    return get_data_dir() / "data" / "filler_profile.json"


def user_extras_path() -> Path:
    """FillerBot supplemental fields (jobfinder/fillerbot/user_extras.py)."""
    return get_data_dir() / "data" / "user_extras.yaml"


def onboarding_disclosure_path() -> Path:
    """Record of the one-time the product spec data-disclosure acknowledgement
    shown by the web onboarding wizard (T26, ``jobfinder/web/onboarding.py``).
    Deliberately separate from ``config.yaml``/``Config`` — it isn't a user
    preference and never participates in ``needs_onboarding``.
    """
    return get_data_dir() / "data" / "onboarding_disclosure.json"


def instance_lock_path() -> Path:
    """Single-instance lock written by the launcher (T34).

    Lives in the data dir rather than the repo or a temp directory because
    it is **secret-bearing**: it records the running instance's port *and*
    its per-launch token, which is the only way a second launch can open a
    URL that actually authenticates (see
    ``jobfinder/instance_lock.py``'s module docstring).
    """
    return get_data_dir() / "instance.lock"


def frontend_dist_dir() -> Path:
    """The built SPA (``frontend/dist``), served by ``create_app`` (T34).

    This is the one place :func:`is_frozen` still matters, via
    :func:`get_resource_dir`:

      - from source, ``get_resource_dir()`` is the repo root, so this is
        ``<repo>/frontend/dist`` — whatever ``npm run build`` just wrote.
      - inside a one-dir PyInstaller bundle, ``get_resource_dir()`` is
        ``sys._MEIPASS`` (the ``_internal`` directory), and the build spec
        copies the Vite output to ``frontend/dist`` *within* it, so the
        same expression resolves to the bundled copy.

    Deliberately not derived from ``Path.cwd()`` or from ``__file__``
    directly: the packaged app is launched from arbitrary working
    directories, and in a one-file bundle ``__file__`` points inside a
    temporary extraction dir that is not where the datas land.
    """
    return get_resource_dir() / "frontend" / "dist"


def _legacy_data_dir() -> Path | None:
    """Where this user's data lived before this module had a single,
    cwd-independent data root (used only for one-time migration detection).

    - Frozen builds used to switch into ``%APPDATA%\\JobFinder`` at startup.
    - Dev runs used to resolve everything relative to ``Path.cwd()``.
    """
    if is_frozen():
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "JobFinder" if appdata else None
    return Path.cwd()


def _migrate_legacy_data(data_dir: Path) -> None:
    """One-time copy of an existing install's data into the new data root.

    Never runs when ``JOBFINDER_DATA_DIR`` is set explicitly (that's an
    intentional, explicit location — e.g. tests — and must never be
    silently populated from an unrelated legacy directory). Never
    overwrites an already-initialized new location. Copies rather than
    moves, so a user's pre-migration data is never deleted even if the
    copy is interrupted or something downstream goes wrong.
    """
    if os.environ.get("JOBFINDER_DATA_DIR"):
        return
    legacy = _legacy_data_dir()
    if legacy is None or legacy == data_dir:
        return
    legacy_config = legacy / "config.yaml"
    if not legacy_config.exists():
        return  # no pre-existing install detected at the legacy location
    if (data_dir / "config.yaml").exists():
        return  # new location already initialized — do not clobber it

    logger.info("Existing JobFinder data found at legacy location %s — migrating to %s.", legacy, data_dir)
    try:
        for name in ("config.yaml", "data", "logs", "browsers"):
            src = legacy / name
            if not src.exists():
                continue
            dest = data_dir / name
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy(src, dest)
        logger.info(
            "Migration complete. Your existing config/database/logs are now also at %s. "
            "The original files at %s were left in place, not deleted.",
            data_dir,
            legacy,
        )
    except OSError as exc:
        logger.warning(
            "Could not migrate legacy data from %s to %s (%s). "
            "Set JOBFINDER_DATA_DIR=%s to keep using the old location.",
            legacy,
            data_dir,
            exc,
            legacy,
        )


def setup_data_environment() -> None:
    """Called once at startup, unconditionally (not just when frozen).

    - Resolves and creates the data root, its ``data/`` and ``logs/``
      subdirectories, and a Playwright browsers cache dir.
    - Sets ``PLAYWRIGHT_BROWSERS_PATH`` so Playwright finds its Chromium
      inside the data root rather than the install/source dir.
    - Migrates an existing install's data from its legacy location, if
      any is found (see :func:`_migrate_legacy_data`).
    - Seeds a default ``config.yaml`` on first run (after migration, so a
      migrated config is never clobbered by the placeholder).

    Does **not** change the process's working directory — every caller must
    use the explicit path helpers above (``config_path()``, ``db_path()``,
    ``logs_dir()``, etc.) instead of relying on relative-path literals
    resolving against the process's cwd.
    """
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "data").mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)

    browsers_dir = data_dir / "browsers"
    browsers_dir.mkdir(exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)

    _migrate_legacy_data(data_dir)

    config_dest = data_dir / "config.yaml"
    if not config_dest.exists():
        default = get_resource_dir() / "config.example.yaml"
        if default.exists():
            shutil.copy(default, config_dest)
        else:
            logger.warning("config.example.yaml not found in resource dir; no config seeded")
