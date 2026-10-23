# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for JobFinder (T34).

Build with:
    .venv/Scripts/python.exe scripts/build_app.py
or directly (after `npm run build` in frontend/):
    pyinstaller jobfinder.spec --clean --noconfirm

This replaces the Qt-era spec T33 deleted. Three things are different in
kind, not just in detail:

1. **One-dir, not one-file.** ``COLLECT`` below produces
   ``dist/JobFinder/`` with the interpreter and data beside the exe. A
   one-file build unpacks itself into a fresh temp directory on every
   launch, which would (a) re-extract ~100 MB of Playwright-adjacent
   dependencies each time and (b) make ``sys._MEIPASS`` — and therefore
   ``paths.get_resource_dir()`` — a path that changes between runs, which
   is exactly the kind of cwd/locality assumption ``jobfinder/paths.py``
   was rewritten to stop depending on.

2. **The entry point is the web launcher**, ``jobfinder/__main__.py``, and
   it is deliberately the same file ``python -m jobfinder`` runs. That
   file is a shim with absolute imports because PyInstaller executes an
   entry script as ``__main__`` with no package context.

3. **The built SPA is bundled** at ``frontend/dist`` *inside* the bundle,
   because ``paths.frontend_dist_dir()`` resolves
   ``get_resource_dir() / "frontend" / "dist"`` — which is the repo root
   from source and ``sys._MEIPASS`` when frozen. Keeping the same relative
   layout in both places is what makes that one expression correct in
   both. ``frontend/dist`` is git-ignored, so the build script runs the
   Vite build first and fails loudly if it is missing.

``jobfinder.ui`` / PySide6 / pystray / the PIL-tkinter bridge are gone
from ``hiddenimports`` along with the Qt layer itself; ``excludes`` now
also lists PySide6 so a stray transitive reference cannot drag ~150 MB of
Qt back into a web app that no longer uses it.
"""

import os
from pathlib import Path

# ── tls_client's native DLL ───────────────────────────────────────────────
# Loaded through ctypes at runtime, so the import graph never mentions it
# and PyInstaller cannot infer it. Carried over from the Qt-era spec: the
# dependency (via python-jobspy) is unchanged.
import importlib.util as _ilu

_tls_spec = _ilu.find_spec("tls_client")
_tls_dir = os.path.dirname(_tls_spec.origin) if _tls_spec else ""
_tls_dll = os.path.join(_tls_dir, "dependencies", "tls-client-64.dll")
_tls_binaries = [(_tls_dll, "tls_client/dependencies")] if os.path.exists(_tls_dll) else []

_REPO_ROOT = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
_DIST = _REPO_ROOT / "frontend" / "dist"
if not (_DIST / "index.html").is_file():
    raise SystemExit(
        f"frontend/dist/index.html is missing ({_DIST}).\n"
        "Run `npm run build` in frontend/ first, or use scripts/build_app.py which does it for you.\n"
        "Bundling without it would produce an app that serves the API and a blank page."
    )

a = Analysis(
    ["jobfinder/__main__.py"],
    pathex=["."],
    binaries=_tls_binaries,
    datas=[
        # Seeded into the data dir on first run by paths.setup_data_environment().
        ("config.example.yaml", "."),
        # Window/installer icon source files.
        ("assets", "assets"),
        # The SPA. Must keep this exact relative path — see docstring (3).
        ("frontend/dist", "frontend/dist"),
    ],
    hiddenimports=[
        # ── Core (loaded dynamically / via the registry pattern) ─────────
        "jobfinder.config",
        "jobfinder.paths",
        "jobfinder.instance_lock",
        "jobfinder.launcher",
        "jobfinder.orchestrator",
        "jobfinder.scheduler",
        "jobfinder.storage.database",
        "jobfinder.matcher.models",
        "jobfinder.matcher.local_matcher",
        "jobfinder.resume.profiler",
        # ── Web backend ──────────────────────────────────────────────────
        "jobfinder.web.app",
        "jobfinder.web.control",
        "jobfinder.web.fillerbot",
        "jobfinder.web.jobs",
        "jobfinder.web.logging_setup",
        "jobfinder.web.onboarding",
        "jobfinder.web.redact",
        "jobfinder.web.resume",
        "jobfinder.web.scan",
        "jobfinder.web.security",
        "jobfinder.web.settings",
        "jobfinder.web.stream",
        # ── FillerBot (Playwright-driven) ────────────────────────────────
        "jobfinder.fillerbot",
        "playwright",
        "playwright.sync_api",
        "playwright.async_api",
        # ── Source plug-ins (registry pattern — all must be explicit) ────
        "jobfinder.sources.base",
        "jobfinder.sources.registry",
        "jobfinder.sources.jobspy_source",
        "jobfinder.sources.remotive_source",
        "jobfinder.sources.arbeitnow_source",
        "jobfinder.sources.adzuna_source",
        "jobfinder.sources.jsearch_source",
        # ── ASGI server. Uvicorn resolves its loop/protocol implementations
        #    by *string* at runtime ("auto" -> "uvicorn.loops.asyncio" etc.),
        #    so none of these appear in the import graph. ────────────────
        "uvicorn",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.logging",
        "h11",
        "httptools",
        "websockets",
        "watchfiles",
        # ── FastAPI / Starlette / multipart ──────────────────────────────
        "fastapi",
        "starlette",
        "multipart",
        "python_multipart",
        # ── APScheduler ──────────────────────────────────────────────────
        "apscheduler.schedulers.background",
        "apscheduler.executors.pool",
        "apscheduler.jobstores.memory",
        "apscheduler.triggers.interval",
        "apscheduler.events",
        # ── PDF parsing ──────────────────────────────────────────────────
        "pdfplumber",
        "pdfminer",
        "pdfminer.high_level",
        "pdfminer.layout",
        # ── Anthropic / HTTP ─────────────────────────────────────────────
        "anthropic",
        "httpx",
        "httpcore",
        # ── python-jobspy and its back-ends (1.1.82+ module layout) ──────
        "jobspy",
        "jobspy.model",
        "jobspy.util",
        "jobspy.exception",
        "jobspy.linkedin",
        "jobspy.linkedin.constant",
        "jobspy.linkedin.util",
        "jobspy.indeed",
        "jobspy.indeed.constant",
        "jobspy.indeed.util",
        "jobspy.glassdoor",
        "jobspy.glassdoor.constant",
        "jobspy.glassdoor.util",
        "jobspy.ziprecruiter",
        "jobspy.ziprecruiter.constant",
        "jobspy.ziprecruiter.util",
        "jobspy.google",
        "jobspy.google.constant",
        "jobspy.google.util",
        # ── Data / config ────────────────────────────────────────────────
        "yaml",
        "pandas",
        "pandas.core.arrays.masked",
        "pandas.core.arrays.integer",
        "pandas.core.arrays.string_",
        # ── Misc ─────────────────────────────────────────────────────────
        "platformdirs",
        "packaging",
        "charset_normalizer",
        "tls_client",
        "tls_client.cffi",
        "tls_client.sessions",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The Qt layer is gone (T33) — make sure nothing drags it back in.
        "PySide6",
        "shiboken6",
        "pystray",
        "tkinter",
        # Large packages some dependencies reference but never need here.
        "matplotlib",
        "scipy",
        "IPython",
        "notebook",
        "jupyter",
        "jupyterlab",
        "sklearn",
        "tensorflow",
        "torch",
        "cv2",
        "unittest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821 - injected by PyInstaller

exe = EXE(  # noqa: F821 - injected by PyInstaller
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JobFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX off, unlike the Qt-era spec. Compressed executables are a
    # well-known false-positive trigger for Windows Defender/SmartScreen
    # heuristics, and this app is already unsigned — it does not need a
    # second reason to be quarantined. The cost is disk size only.
    upx=False,
    # Windowed: double-clicking must not leave a console window behind the
    # browser tab. jobfinder/launcher.py accounts for the consequence
    # (stdout/stderr are None) by logging to the data dir's log file
    # instead of logging.basicConfig.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version_file=None,
)

coll = COLLECT(  # noqa: F821 - injected by PyInstaller
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JobFinder",
)
