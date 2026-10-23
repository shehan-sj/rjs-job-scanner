"""Credential redaction helpers — the product spec (API keys are
write-only over HTTP; logs and error responses are redacted) and the T18
DoD's explicit "no ``sk-ant-`` substring" assertion.

Two different redaction shapes are needed:

- :func:`redact_extra` operates on ``SourceConfig.extra`` (``jobfinder/
  config.py``), a free-form dict. The known credential keys today are
  jsearch's ``api_key`` (``jsearch_source.py:48``) and adzuna's ``app_id``/
  ``app_key`` (``adzuna_source.py:37-38``), but ``extra`` is explicitly
  free-form — a future source can add ``client_secret``, ``access_token``,
  ``password`` or anything else at any time. Redacting by an allowlist of
  known key names would be correct today and silently wrong the moment
  that happens, with the leak invisible until someone reads the response
  body. So this redacts by *key-name pattern* instead — any key whose name
  contains ``key``/``secret``/``token``/``password``/``credential``
  (case-insensitive, substring match) is treated as sensitive, regardless
  of which source declared it. This fails closed, not open: a harmless key
  that happens to match (e.g. a literal ``"api_key_hint"``) gets
  over-redacted, which is a cosmetic bug, not a leaked secret.

- :func:`redact_text` scans a string — an Anthropic SDK exception message,
  a log line — for secret-*shaped* substrings and masks them. This is
  defense in depth for the case a credential leaks into free text rather
  than a structured field: ``fillerbot/vision.py`` puts ``str(exc)`` from
  the Anthropic SDK straight into ``PageAnalysis.human_note`` and
  ``logger.error`` today, and SDK errors can echo request headers.
"""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY_PATTERNS = ("key", "secret", "token", "password", "credential")

# adzuna_source.py:37-38 pairs "app_id" with "app_key" as its two halves
# of one API credential — knowing the id half without the key half isn't
# useful on its own, but it's still half a credential, named after the
# fact that it's an *application* identifier rather than a literal secret.
# None of the substrings above match "id" (deliberately — "id" alone is
# far too broad a substring and would over-redact ordinary identifiers
# like "region_id"), so this specific, known compound name is listed by
# exact match instead. This supplements the pattern match; it never
# replaces it as the primary, fail-closed heuristic for anything else.
_SENSITIVE_EXACT_KEYS = frozenset({"app_id"})


def is_sensitive_key(key: str) -> bool:
    """Whether ``key`` looks credential-shaped by name (see module
    docstring for why this is a pattern match, not an allowlist). Public
    because callers outside this module (``jobfinder/web/settings.py``'s
    write-only merge logic) need the same heuristic :func:`redact_extra`
    uses internally.
    """
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(pattern in lowered for pattern in _SENSITIVE_KEY_PATTERNS)


def mask_last4(value: str) -> str | None:
    """Last 4 characters of ``value``, or ``None`` if it's empty or too
    short to usefully mask (returning the whole thing defeats the point).
    """
    if not value or len(value) < 4:
        return None
    return value[-4:]


def redact_extra(extra: dict[str, Any]) -> dict[str, Any]:
    """Redact every key in ``extra`` whose *name* looks credential-shaped,
    whichever source declared it, replacing the raw value with
    ``"<key>_set": bool`` and (when long enough) ``"<key>_last4": str``.
    Non-matching keys (e.g. adzuna's ``country``) pass through unredacted —
    ``extra`` carries ordinary per-source settings too, not just secrets.
    """
    redacted: dict[str, Any] = {}
    for key, value in extra.items():
        if is_sensitive_key(key):
            str_value = "" if value is None else str(value)
            redacted[f"{key}_set"] = bool(str_value)
            last4 = mask_last4(str_value)
            if last4 is not None:
                redacted[f"{key}_last4"] = last4
        else:
            redacted[key] = value
    return redacted


# Patterns that indicate a literal secret leaked into free text rather than
# a structured field. Deliberately broad (over-redaction is the safe
# failure mode here, per the module docstring).
_SECRET_TEXT_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
    re.compile(r"(?i)authorization\s*:\s*(bearer\s+)?\S+"),
    re.compile(r"(?i)x-api-key\s*:\s*\S+"),
)


def redact_text(text: str) -> str:
    """Mask secret-shaped substrings in free text (an SDK exception
    message, a log line) before it reaches a log file or an API response.
    """
    if not text:
        return text
    redacted = text
    for pattern in _SECRET_TEXT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
