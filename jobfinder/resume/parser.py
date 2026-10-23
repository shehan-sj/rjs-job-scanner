"""Parse PDF or plain-text resumes into ResumeData."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .models import ResumeData

logger = logging.getLogger(__name__)

_cache: ResumeData | None = None


def load_resume(path: str) -> ResumeData:
    """Load resume from file, using cached version if file hasn't changed."""
    global _cache

    resolved = str(Path(path).resolve())
    if not os.path.exists(resolved):
        raise FileNotFoundError(
            f"Resume not found: {resolved}\nPlease set 'resume.path' in config.yaml to point to your resume file."
        )

    mtime = os.path.getmtime(resolved)

    if _cache and _cache.file_path == resolved and _cache.mtime == mtime:
        return _cache

    ext = Path(resolved).suffix.lower()
    if ext == ".pdf":
        text = _parse_pdf(resolved)
    else:
        text = _parse_text(resolved)

    _cache = ResumeData(text=text, file_path=resolved, mtime=mtime)
    logger.info("Resume loaded: %d characters from %s", len(text), resolved)
    return _cache


def _parse_pdf(path: str) -> str:
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber is required for PDF resumes: pip install pdfplumber")

    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n\n".join(pages)


def _parse_text(path: str) -> str:
    # errors="replace" never raises — it turns "this isn't valid UTF-8
    # text" into "here's some text with U+FFFD in it" instead of an
    # exception, which is exactly what makes a non-text file read as
    # plausible-looking mojibake rather than a clear error (this is why
    # jobfinder/web/resume.py's upload handler sniffs the type itself
    # rather than relying on this function to reject anything). Kept as
    # is: the desktop app still relies on this for ordinary .txt resumes
    # with the occasional stray encoding quirk.
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()
