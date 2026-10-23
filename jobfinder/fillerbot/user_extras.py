"""The user-edit layer: values the user states themselves.

Stored as a flat YAML file at ``data/user_extras.yaml``. It began (T31) as
nine string contact fields that fill gaps in the FillerProfile — address,
phone, country, etc. that many resumes omit — and is now also where a user's
corrections to the *matching* fields live (T50). Same file, more
keys: the product spec forbids a fourth store.

That means the values are no longer all strings. ``job_titles``, ``skills``
and ``target_industries`` are lists, and ``years_experience`` is an integer,
so this module can no longer coerce everything with ``str(v)`` on the way in
(a list round-tripped as the literal string ``"['a', 'b']"``) or ``v.strip()``
on the way out (which raises ``AttributeError`` on a list).

``load_extras`` and ``save_extras`` therefore take an optional ``shapes``
mapping of ``field name -> kind``. The kinds, and the table that defines them,
live in :mod:`jobfinder.derived_profile`; passing them in rather than
declaring a second copy here keeps one owner table and avoids an import cycle
(the read model imports this module). With no ``shapes`` argument the original
string-only behaviour is kept, so the nine contact fields round-trip
byte-identically either way and any caller that predates T50 is unaffected.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import yaml

from .models import FillerProfile

logger = logging.getLogger(__name__)

# Mirrors ``derived_profile``'s kind constants. Declared as plain strings
# rather than imported to keep this module free of that dependency; the one
# test that matters asserts the two agree.
_KIND_INT = "int"
_KIND_STR_LIST = "str_list"

# Fields that are commonly missing from resumes and worth prompting for.
PROMPTABLE_FIELDS: list[tuple[str, str]] = [
    ("address", "Street address"),
    ("city", "City"),
    ("state_province", "State / Province"),
    ("zip_postal", "ZIP / Postal code"),
    ("country", "Country"),
    ("phone", "Phone number"),
    ("email", "Email address"),
    ("linkedin_url", "LinkedIn URL"),
    ("website", "Personal website"),
]


def _coerce_in(key: str, value: object, shapes: Mapping[str, str]) -> object | None:
    """One stored YAML value, on the way in. ``None`` means "drop it".

    The file is plain YAML a user can edit by hand, so a scalar where a list
    belongs (``skills: Python``) is widened rather than rejected, and a
    structurally wrong value (a mapping under ``skills``, ``years_experience:
    lots``) is dropped rather than allowed to crash a read.
    """
    kind = shapes.get(key)
    if kind == _KIND_STR_LIST:
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, (list, tuple)) or not all(isinstance(item, str) for item in items):
            logger.warning("Ignoring %s in user extras: expected a list of strings.", key)
            return None
        cleaned = [item.strip() for item in items if item.strip()]
        return cleaned or None
    if kind == _KIND_INT:
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("Ignoring %s in user extras: expected an integer.", key)
            return None
    # Strings, and anything this mapping does not describe — the pre-T50
    # behaviour, kept so the nine contact fields and any unknown legacy key
    # are read exactly as before.
    return str(value)


def load_extras(shapes: Mapping[str, str] | None = None) -> dict[str, object]:
    """The stored user layer. ``shapes`` maps a key to ``str``/``int``/
    ``str_list``; keys it does not mention are read as strings, as before.
    """
    from jobfinder.paths import user_extras_path

    shapes = shapes or {}
    extras_path = user_extras_path()
    if not extras_path.exists():
        return {}
    try:
        with open(extras_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, Mapping):
            logger.warning("Could not load user extras: expected a mapping at the top level.")
            return {}
        loaded: dict[str, object] = {}
        for key, value in data.items():
            if not value:
                continue
            coerced = _coerce_in(str(key), value, shapes)
            if coerced:
                loaded[str(key)] = coerced
        return loaded
    except Exception as exc:
        logger.warning("Could not load user extras: %s", exc)
        return {}


def _coerce_out(key: str, value: object, shapes: Mapping[str, str]) -> object | None:
    """One value on the way out. ``None`` means "do not store it" — which is
    also how a field gets cleared: save it as ``""`` or ``[]``.
    """
    kind = shapes.get(key)
    if kind == _KIND_STR_LIST:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return None
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return cleaned or None
    if kind == _KIND_INT:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value or None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def save_extras(data: Mapping[str, object], shapes: Mapping[str, str] | None = None) -> None:
    shapes = shapes or {}
    from jobfinder.paths import user_extras_path

    extras_path = user_extras_path()
    extras_path.parent.mkdir(parents=True, exist_ok=True)
    clean: dict[str, object] = {}
    for key, value in data.items():
        if not value:
            continue
        coerced = _coerce_out(key, value, shapes)
        if coerced:
            clean[key] = coerced
    with open(extras_path, "w", encoding="utf-8") as fh:
        yaml.dump(clean, fh, default_flow_style=False, allow_unicode=True)
    logger.info("User extras saved (%d fields).", len(clean))


def missing_fields(profile: FillerProfile) -> list[tuple[str, str]]:
    """Return promptable fields that are empty in the profile."""
    return [(key, label) for key, label in PROMPTABLE_FIELDS if not getattr(profile, key, "")]
