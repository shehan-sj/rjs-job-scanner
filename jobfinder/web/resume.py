"""Resume upload endpoint (T18) — ``POST /api/resume``, upload only.

This closes a real vulnerability. Today
``resume/parser.py``'s ``load_resume(path)`` accepts **any** path, and for
anything that isn't a PDF it falls through to reading the file as UTF-8
text — text which is then sent straight to the Anthropic API
(``resume/profiler.py``). With ``resume.path`` settable over HTTP, a
CSRF'd or local request could point it at ``config.yaml``, an SSH key, or
anything else readable by the process, and exfiltrate it via the next
scan's Claude call. So:

- The client never supplies a path or filename. ``UploadFile.filename`` is
  read only for logging and is never used to build the stored path.
- The server generates the stored filename itself
  (:func:`uuid.uuid4` + the sniffed extension) under
  :func:`jobfinder.paths.resume_uploads_dir`.
- The resolved destination is asserted to be inside the data dir —
  defence in depth against a traversal bug elsewhere in this module, not
  the primary control (the primary control is "we never use client input
  to build the path at all").
- The upload is type-*sniffed* (magic bytes / structure), not trusted by
  extension or declared ``Content-Type`` — both are attacker-controlled.
- The upload is size-capped, read in a bounded loop so an attacker can't
  force the server to buffer an unbounded body in memory before the cap
  is even checked.
- ``resume.path`` is not a field of ``jobfinder/web/settings.py``'s PUT
  model at all — see that module's docstring.

Profile extraction (``resume/profiler.py``, a real Anthropic API call) is
best-effort here: a missing/invalid key or a network failure must not
fail the upload itself, which is a question of fact (stored file,
extractable text) now answered, independent of whether a profile could
also be pre-warmed for the next scan.

**T45 addendum.** The client's declared ``UploadFile.filename`` is now
sanitised (:func:`sanitize_resume_display_name`) and persisted as
``config.resume.original_filename`` so ``GET /api/settings`` can report
a human-recognizable name instead of omitting the ``resume`` section
outright. This does **not** reopen the vector T18 closed: the value is
stored and returned purely as a display string, is never used to build
a filesystem path, and is re-sanitised on the way out
(``jobfinder/web/settings.py``) in case ``config.yaml`` was hand-edited.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator

from .. import derived_profile, paths
from ..config import Config, save_config
from ..resume.parser import load_resume
from ..resume.profiler import load_profile
from .redact import redact_text

logger = logging.getLogger("jobfinder.web.resume")

router = APIRouter(prefix="/api")

# 10 MiB — generous for a resume (even an image-heavy PDF), small enough
# that a repeated upload can't be used to exhaust disk/memory.
MAX_RESUME_BYTES = 10 * 1024 * 1024

_CHUNK_SIZE = 1024 * 1024

# How long a stored display filename is allowed to be (T45). Generous for
# any real filename, small enough that a client can't use it to bloat
# config.yaml or the GET /api/settings payload.
_MAX_DISPLAY_FILENAME_LEN = 150

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_resume_display_name(filename: str | None) -> str:
    """Turn a client-supplied filename into an opaque **display string**
    (T45) — never a path.

    This is the client's declared ``UploadFile.filename``, which is
    attacker-controlled in exactly the way the rest of this module's
    docstring already treats file *content*: it is read only to be shown
    back to the same user later, never to build a filesystem path (the
    server already generates its own uuid storage name in
    :func:`_store_resume`, independent of this value).

    - Both ``/`` and ``\\`` are treated as separators (a client on any OS
      can send either), so ``../../etc/passwd`` and
      ``C:\\Windows\\win.ini`` both collapse to their last path segment
      (``passwd``, ``win.ini``) rather than surviving as a path shape.
    - Control characters are stripped.
    - The result is capped at :data:`_MAX_DISPLAY_FILENAME_LEN` characters.

    Applied both when a filename is first stored (here) and again when
    ``GET /api/settings`` reads it back
    (:func:`jobfinder.web.settings._settings_payload`) — defense in
    depth against a hand-edited ``config.yaml`` that puts something
    path-shaped directly into ``resume.original_filename``.
    """
    if not filename:
        return ""
    name = filename.replace("\\", "/").split("/")[-1]
    name = _CONTROL_CHARS_RE.sub("", name).strip()
    return name[:_MAX_DISPLAY_FILENAME_LEN]


async def _read_capped(file: UploadFile, limit: int) -> bytes:
    """Read ``file`` in bounded chunks, aborting with 413 as soon as the
    cap is exceeded rather than buffering the whole body first — a
    ``Content-Length`` header can't be trusted to abort early on its own
    (it's attacker-controlled, and some clients omit it for chunked
    uploads).
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="resume file too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _is_docx(content: bytes) -> bool:
    """Whether ``content`` is structurally a ``.docx`` (a zip containing
    ``word/document.xml``) — used only to produce a specific rejection
    message. ``.docx`` is deliberately **not** an accepted resume format
    (see :func:`_sniff_resume_type`'s docstring); this exists so that
    rejection is clear rather than the generic "unsupported file type".
    """
    if content[:4] != b"PK\x03\x04":
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return "word/document.xml" in zf.namelist()
    except zipfile.BadZipFile:
        return False


def _sniff_resume_type(content: bytes) -> str | None:
    """Sniff the actual file type from content, never from the client's
    declared filename/extension or ``Content-Type`` header (both are
    attacker-controlled). Returns the extension to store it under, or
    ``None`` if it isn't one of the two formats this application has ever
    actually supported: PDF and plain text.

    ``.docx`` is deliberately **not** accepted, even though it's easy to
    detect structurally (a zip containing ``word/document.xml`` — see
    :func:`_is_docx`): ``resume/parser.py`` has no ``.docx`` parser at
    all, only ``_parse_pdf`` and a plain-text ``_parse_text`` fallback, and
    the desktop app's own file dialogs never offered ``.docx`` either
    (``ui/wizard.py:118``, ``ui/settings.py:654`` both filter
    ``Resume files (*.pdf *.txt)``). Accepting it here would silently feed
    ``_parse_text``'s ``errors="replace"`` read raw zip bytes — mojibake,
    not a resume — straight to Claude. Adding real ``.docx`` support is a
    new capability, out of scope during the migration; the
    caller checks :func:`_is_docx` separately to give a clear, specific
    400 instead of a generic "unsupported file type" one.
    """
    if content.startswith(b"%PDF-"):
        return "pdf"

    if content[:4] == b"PK\x03\x04":
        # Zip-shaped content (a .docx, or anything else zip-shaped,
        # including a renamed .exe-in-a-zip trick) is never accepted —
        # see the docstring above for why .docx specifically is excluded
        # rather than supported.
        return None

    # Plain text: binary content (a NUL byte, or bytes that don't decode
    # as UTF-8) is rejected rather than accepted as "text" — this is what
    # keeps a renamed .exe from sailing through as a .txt resume.
    if b"\x00" in content:
        return None
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "txt"


def _store_resume(content: bytes, extension: str) -> Path:
    data_dir = paths.get_data_dir().resolve()
    uploads_dir = paths.resume_uploads_dir()
    uploads_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.{extension}"
    dest = (uploads_dir / filename).resolve()

    # Defence in depth: the filename above has no path separators and
    # can't escape uploads_dir, but assert it anyway rather than trust
    # that invariant silently.
    if data_dir != dest and data_dir not in dest.parents:
        raise HTTPException(status_code=500, detail="resolved upload path escaped the data directory")

    dest.write_bytes(content)
    return dest


def _cleanup_previous_upload(previous_path: str, new_dest: Path) -> None:
    """Delete the resume file this endpoint replaces, once the new upload
    is confirmed parseable and persisted — otherwise every successful
    upload leaves the one before it behind forever, and nothing but this
    code path ever points at it again. Scoped to files *this endpoint*
    created (inside ``resume_uploads_dir()``): the pre-upload default
    (``config.example.yaml``'s ``resume.path: resume.pdf``, or a path a
    user set some other way before T18) is never a candidate for
    deletion, only a prior ``POST /api/resume`` upload is.
    """
    try:
        previous = Path(previous_path).resolve()
    except OSError:
        return
    if previous == new_dest:
        return

    uploads_dir = paths.resume_uploads_dir().resolve()
    if uploads_dir not in previous.parents:
        return

    try:
        previous.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove previous resume upload %s: %s", previous, exc)


def _process_resume_upload_sync(content: bytes, original_filename: str | None, config: Config) -> dict:
    extension = _sniff_resume_type(content)
    if extension is None:
        if _is_docx(content):
            raise HTTPException(
                status_code=400,
                detail="Word documents aren't supported — please export your resume as PDF",
            )
        raise HTTPException(status_code=400, detail="unsupported file type (pdf or txt only)")

    dest = _store_resume(content, extension)

    # Validate *before* anything becomes the active resume: a file that
    # passes the magic-byte sniff can still fail to parse (truncated,
    # corrupt, or password-protected) or parse cleanly into zero
    # characters (an image-only/scanned PDF). Either way this upload must
    # not succeed, and must not leave an orphan file behind — nothing
    # will ever reference <uuid>.pdf once this function returns an error.
    try:
        resume_data = load_resume(str(dest))
    except Exception as exc:
        dest.unlink(missing_ok=True)
        logger.warning("Resume upload could not be parsed: %s", redact_text(str(exc)))
        raise HTTPException(
            status_code=400,
            detail="could not read this file as a resume (it may be corrupt, truncated, or password-protected)",
        ) from exc

    if not resume_data.text.strip():
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="no extractable text found in this file (an image-only/scanned PDF needs OCR, which isn't supported)",
        )

    previous_path = config.resume.path
    config.resume.path = str(dest)
    config.resume.original_filename = sanitize_resume_display_name(original_filename)
    save_config(config, paths.config_path())
    if previous_path:
        _cleanup_previous_upload(previous_path, dest)

    profile_status = "ok"
    api_key = config.anthropic_api_key
    if not api_key:
        profile_status = "skipped-no-api-key"
    else:
        try:
            load_profile(str(dest), api_key, config.anthropic.model)
        except Exception as exc:
            # A failed profile extraction must not fail the upload — the
            # file is stored and parseable regardless of whether Claude
            # could be reached. Redact before this reaches a log file.
            logger.error("Resume profile extraction failed: %s", redact_text(str(exc)))
            profile_status = "failed"

    return {
        "status": "ok",
        "text_length": len(resume_data.text),
        "profile_status": profile_status,
        # Sanitised display name only (T45) — never the path. Lets the
        # uploading browser update its own UI immediately without a round
        # trip to GET /api/settings, which every *other* browser/profile
        # relies on instead.
        "filename": config.resume.original_filename,
    }


def _derived_profile_payload(config: Config) -> dict:
    """Blocking. The unified read model for the resume on file.

    Agentic layers are read from cache only (``derived_profile.load_layers``),
    so this endpoint cannot trigger a billed Anthropic call, and it works with
    no API key at all — the heuristic layer is local and keyless, which is the
    case the product spec says matters most.
    """
    resume_path = config.resume.path
    on_file = bool(resume_path) and Path(resume_path).exists()
    payload = derived_profile.resolve_for_resume(resume_path if on_file else "").to_payload()
    # Whether a resume is stored — never *where* (the product spec, the
    # rule T45 established). The field payload itself is built from the owner
    # table's allow-list, so no store's ``source_path`` can reach it.
    payload["resume_on_file"] = on_file
    return payload


@router.get("/resume/derived-profile")
async def derived_profile_read_model(request: Request) -> dict:
    """Every resume-derived field with its provenance.

    Deliberately *not* gated on an API key, unlike
    ``GET /api/fillerbot/profile``: a user with no key still has a derived
    profile, it is just heuristic, and each field says so.
    """
    config: Config = request.app.state.config
    return await run_in_threadpool(_derived_profile_payload, config)


class ProfilePatchIn(BaseModel):
    """A partial update of the derived-profile's **user-editable** fields
    (T54; the task ledger note: this is the HTTP write path T50 deliberately
    left unbuilt).

    This mirrors ``ExtrasIn`` (``jobfinder/web/fillerbot.py``)'s discipline —
    an explicit key allowlist, ``extra="forbid"`` — but the owner table
    backs three different value shapes (``KIND_STR``/``KIND_INT``/
    ``KIND_STR_LIST``), so a single ``dict[str, str]`` annotation can't do
    the rejection in one line the way ``ExtrasIn`` does. ``values`` is
    typed ``dict[str, object]`` (pydantic v2 performs no coercion on
    ``object`` either) and the validator below dispatches per field's
    registered kind, rejecting the wrong *shape* — a non-string in a
    string field, a bool where only a real int is wanted, a scalar or a
    non-string item in a list field — before it can reach
    ``derived_profile.save_user_edits`` and, for the matching fields,
    before it could ever reach an Anthropic prompt. Content bounds
    (length, control characters) are **not** duplicated here: they live in
    ``save_user_edits``/``_validate_edit`` alone, and a value that passes
    this shape gate but fails those still comes back as a 422 — just one
    raised from the save step instead of from this validator.
    """

    model_config = {"extra": "forbid"}

    values: dict[str, object]

    @field_validator("values")
    @classmethod
    def _only_known_shape_valid_values(cls, values: dict[str, object]) -> dict[str, object]:
        unknown = sorted(set(values) - set(derived_profile.FIELD_SHAPES))
        if unknown:
            raise ValueError(f"unknown profile field(s): {', '.join(unknown)}")
        for key, value in values.items():
            kind = derived_profile.FIELD_SHAPES[key]
            if kind == derived_profile.KIND_STR:
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")
            elif kind == derived_profile.KIND_INT:
                # bool is a subclass of int in Python — reject it
                # explicitly or `True`/`False` would silently pass as 1/0.
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"{key} must be an integer")
            elif kind == derived_profile.KIND_STR_LIST:
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise ValueError(f"{key} must be a list of strings")
            else:  # pragma: no cover - FIELD_SHAPES only ever holds the three kinds above
                raise ValueError(f"{key} is not user-editable")
        return values


@router.patch("/resume/derived-profile")
async def patch_derived_profile(body: ProfilePatchIn, request: Request) -> dict:
    """Write a user edit to one or more derived-profile fields, then
    return the freshly re-resolved read model (same shape ``GET`` returns)
    so the caller never has to guess what resolution did with it.

    This is also how a re-parse proposal gets **accepted** (T54's brief):
    there is no separate "accept" endpoint — accepting a proposed value is
    just PATCHing it through here, same allowlist, same bounds, and the
    resulting provenance is ``user`` because the user did choose it.
    """
    config: Config = request.app.state.config

    def _save_and_report() -> dict:
        try:
            derived_profile.save_user_edits(body.values)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _derived_profile_payload(config)

    return await run_in_threadpool(_save_and_report)


class ReparseIn(BaseModel):
    """Which re-parse method the user explicitly chose.

    The method is never inferred from whether a key happens to be
    configured — a client must say which it wants, and for ``agentic`` must
    also set ``acknowledged``. That field is the product spec's disclosure
    made server-enforced rather than merely cosmetic: a UI that never
    showed the "this sends your resume text to Anthropic" notice has no way
    to drive this endpoint into actually sending it.
    """

    model_config = {"extra": "forbid"}

    method: Literal["heuristic", "agentic"]
    acknowledged: bool = False


@router.post("/resume/reparse")
async def reparse_resume(body: ReparseIn, request: Request) -> dict:
    """Re-run extraction and propose what changed. Writes
    nothing — ``derived_profile.propose_from_heuristic``/
    ``propose_from_agentic`` resolve a fresh candidate and diff it against
    what is showing now; applying a proposal is a separate, explicit
    ``PATCH /resume/derived-profile`` call.
    """
    config: Config = request.app.state.config
    resume_path = config.resume.path
    if not resume_path or not Path(resume_path).exists():
        raise HTTPException(status_code=400, detail="no resume on file to re-parse")

    if body.method == "heuristic":
        proposals = await run_in_threadpool(derived_profile.propose_from_heuristic, resume_path)
    else:
        if not body.acknowledged:
            raise HTTPException(
                status_code=400,
                detail=("an agentic re-parse sends your resume text to Anthropic — acknowledge this before retrying"),
            )
        api_key = config.anthropic_api_key
        if not api_key:
            raise HTTPException(status_code=400, detail="Anthropic API key is not configured")
        proposals = await run_in_threadpool(
            derived_profile.propose_from_agentic, resume_path, api_key, config.anthropic.model
        )

    return {"proposals": [asdict(p) for p in proposals]}


@router.post("/resume")
async def upload_resume(request: Request, file: UploadFile = File(...)) -> dict:
    content = await _read_capped(file, MAX_RESUME_BYTES)
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    config: Config = request.app.state.config
    return await run_in_threadpool(_process_resume_upload_sync, content, file.filename, config)
