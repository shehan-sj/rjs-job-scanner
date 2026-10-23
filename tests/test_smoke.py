"""Trivial smoke test so `pytest -q` has something to collect.

Confirms the Qt-free core (yaml + anthropic import surface only) is
importable without jobspy/numpy/pandas/playwright/PySide6 installed.
Real behaviour tests land in T3.
"""

from __future__ import annotations


def test_core_modules_import():
    import jobfinder.config  # noqa: F401
    import jobfinder.matcher.location_parser  # noqa: F401
    import jobfinder.storage.database  # noqa: F401

    assert True
