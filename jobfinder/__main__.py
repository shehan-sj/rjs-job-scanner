"""``python -m jobfinder`` — and the PyInstaller bundle's entry script.

Deliberately a shim with absolute imports and no logic. PyInstaller runs
its entry script as ``__main__`` with no package context, so a relative
``from .launcher import main`` here would fail inside the bundle while
working fine from source — a difference that only shows up after a build.
"""

from __future__ import annotations

from jobfinder.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
