"""Onboarding wizard support endpoints (T26).

Web equivalent of ``main.py``'s ``_needs_onboarding`` (the Qt app's own
first-run gate, kept there until T33 deletes the Qt layer): a fresh
install has no Anthropic API key and no resume on file.
``GET /api/onboarding/status`` recomputes this from the live in-memory
``app.state.config`` (kept current by T18's settings/resume write paths)
rather than re-reading ``config.yaml`` from disk, so a ``PUT
/api/settings`` or ``POST /api/resume`` made earlier in the same wizard
session is reflected immediately with no extra round trip. Unlike
``main.py``'s version, this never has a "no config file at all" branch
to handle — ``paths.setup_data_environment`` (``jobfinder/web/app.py``'s
lifespan) always seeds a ``config.yaml`` before any ``Config`` exists on
this path.

``POST /api/onboarding/test-key`` is the wizard's "Test connection"
button (ported from ``jobfinder/ui/wizard.py``'s ``_ApiKeyTester``, a
``QThread`` there — this handler is already off the request-handling
event loop via ``run_in_threadpool``, so no thread of its own is
needed). It makes one real, minimal round trip to the Anthropic API and
reports pass/fail. **It never persists the key it's given** — the key
only reaches disk when the wizard finishes, via the existing ``PUT
/api/settings``.

``POST /api/onboarding/acknowledge-disclosure`` records that the
one-time the product spec data-disclosure step was shown and accepted
(a timestamp, last-write-wins). Deliberately **not** stored in
``config.yaml``/``Config``: it isn't a user preference, and recording
it has no bearing on ``needs_onboarding`` — a user who quits the wizard
before this step still has an incomplete config and sees the wizard
again next launch regardless of whether they'd acknowledged the
disclosure on the attempt before.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import onboarding_prefill, paths
from ..config import Config, save_config
from .redact import redact_text

logger = logging.getLogger("jobfinder.web.onboarding")

router = APIRouter(prefix="/api/onboarding")

# Cheapest available model — this call exists only to prove the key
# authenticates and can reach the API, not to exercise whatever model
# the user intends to use day-to-day (same reasoning as
# jobfinder/ui/wizard.py's _ApiKeyTester, which hardcodes this model
# regardless of config.anthropic.model).
_TEST_MODEL = "claude-haiku-4-5"


def needs_onboarding(config: Config) -> bool:
    """Port of ``main.py``'s ``_needs_onboarding`` body, minus the
    file-not-found branch (see module docstring for why that can't
    happen here), **plus** T61's completion flag.

    Before T61 this was just "no Anthropic key, or no resume file that
    actually exists on disk" — and that was the whole bug: a user who
    finished the wizard with no key still had no key, so the very next
    load recomputed ``True`` and showed the wizard again, forever. There
    was no onboarding-completed flag anywhere to remember that Finish had
    already been pressed.

    Now: ``True`` only when the wizard has **not** been explicitly
    completed (``config.onboarding_completed`` — set once by ``POST
    /api/onboarding/complete``, the wizard's single Start/Finish button,
    with or without a key) **and** the old key-or-resume check still
    says the install looks unconfigured. The ``and`` is deliberate and is
    what keeps an already-working install from ever being newly trapped:
    ``onboarding_completed`` defaults to ``False`` both for a fresh
    install and for every config.yaml written before T61 (the key never
    existed to be read), so for those two cases this reduces to exactly
    the pre-T61 check — a real key and a real resume on disk still clear
    the gate on their own, with no need to ever call ``/complete``. The
    flag only *adds* a way to clear the gate; it never adds a way to
    re-arm it.
    """
    if config.onboarding_completed:
        return False
    if not config.anthropic_api_key:
        return True
    if not config.resume.path or not Path(config.resume.path).exists():
        return True
    return False


@router.get("/status")
async def onboarding_status(request: Request) -> dict:
    config: Config = request.app.state.config
    return {"needs_onboarding": needs_onboarding(config)}


@router.get("/prefill")
async def onboarding_prefill_read_model(request: Request) -> dict:
    """What the wizard should pre-fill from the resume, and where each value
    came from (T52).

    A read model over T50's read model: it writes nothing, so it cannot
    overwrite a setting the user already has, and the finish step persists
    whatever the user left or changed through the existing ``PUT
    /api/settings`` — there is no onboarding write endpoint for config, by
    design. See :mod:`jobfinder.onboarding_prefill` for the field table and
    for why editing a pre-filled value does *not* write the profile's
    user-edit layer.

    Deliberately not gated on an API key: the agentic stores are read from
    cache only, and the keyless heuristic path is exactly the case the product spec
    says pre-filling matters most for. The response says which path it used.
    """
    config: Config = request.app.state.config
    return await run_in_threadpool(onboarding_prefill.build_prefill, config)


class TestKeyRequest(BaseModel):
    api_key: str = ""
    model: str = _TEST_MODEL


def _test_key_sync(api_key: str, model: str) -> dict[str, Any]:
    if not api_key.strip():
        return {"ok": False, "error": "paste your key first"}
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        # Smallest possible round trip: forces real auth plus a real
        # completion against the live API — same shape as wizard.py's
        # _ApiKeyTester.run().
        client.messages.create(
            model=model,
            max_tokens=4,
            messages=[{"role": "user", "content": "hi"}],
        )
        return {"ok": True, "error": None}
    except Exception as exc:
        # SDK exceptions can echo request headers; redact before this
        # ever reaches a response body or a log line (same convention
        # jobfinder/web/resume.py uses for parse-failure messages).
        message = redact_text(str(exc))
        short = message.splitlines()[0][:200] if message else "connection failed"
        return {"ok": False, "error": short}


@router.post("/test-key")
async def test_key(body: TestKeyRequest) -> dict:
    return await run_in_threadpool(_test_key_sync, body.api_key, body.model or _TEST_MODEL)


def _acknowledge_disclosure_sync() -> dict[str, Any]:
    record = {
        "acknowledged": True,
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
    }
    dest = paths.onboarding_disclosure_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(record), encoding="utf-8")
    return record


@router.post("/acknowledge-disclosure")
async def acknowledge_disclosure() -> dict:
    return await run_in_threadpool(_acknowledge_disclosure_sync)


def _complete_sync(request_app_state: Any) -> dict[str, Any]:
    config: Config = request_app_state.config
    config.onboarding_completed = True
    save_config(config, paths.config_path())
    return {"onboarding_completed": True}


@router.post("/complete")
async def complete_onboarding(request: Request) -> dict:
    """The wizard's single Start/Finish button, separate from ``PUT
    /api/settings`` (which this still calls first, same as before T61):
    marks the wizard itself as done so ``needs_onboarding`` stops
    recomputing its answer from the key/resume check alone. See
    :func:`needs_onboarding` for why this is additive rather than a
    replacement for that check. Mutates the live ``app.state.config`` the
    same way ``PUT /api/settings`` does, so a status check made later in
    the same session reflects it with no extra round trip.
    """
    return await run_in_threadpool(_complete_sync, request.app.state)
