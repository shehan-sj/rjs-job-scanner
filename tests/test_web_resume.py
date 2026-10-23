"""Tests for jobfinder.web.resume (T18) — ``POST /api/resume``, upload only.

the product spec: no client-supplied filesystem path is ever
accepted — the server generates the stored filename itself, type-sniffs
the upload, and caps its size. Profile building (``resume/profiler.py``)
is always asserted with the Anthropic client mocked — a real call needs a
key, network, and spend on every test run.
"""

from __future__ import annotations

import importlib
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

PORT = 8761
BASE_URL = f"http://127.0.0.1:{PORT}"


def _minimal_pdf_bytes(text: str = "Hello Resume") -> bytes:
    """A hand-built, minimal-but-valid single-page PDF — real enough for
    pdfplumber to extract ``text`` back out of it (verified directly
    against pdfplumber while writing this test), without pulling in a PDF
    *generation* dependency this project doesn't otherwise need.
    """
    stream = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("ascii")
    parts = [
        b"%PDF-1.4\n",
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 200 200] /Contents 5 0 R >>endobj\n",
        b"4 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
        b"5 0 obj<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream\nendobj\n",
        b"trailer\n<< /Size 6 /Root 1 0 R >>\n%%EOF\n",
    ]
    return b"".join(parts)


def _corrupt_pdf_bytes() -> bytes:
    """Starts with the ``%PDF-`` magic bytes (passes the sniff) but has no
    ``/Root`` object or valid structure at all — verified directly against
    pdfplumber while writing this test to raise ``PdfminerException``
    ("No /Root object! - Is this really a PDF?"), standing in for a
    truncated download or a corrupted file.
    """
    return b"%PDF-1.4\nthis is not a real pdf body at all, no xref, no root\n%%EOF\n"


def _text_free_pdf_bytes() -> bytes:
    """Syntactically valid — pdfplumber parses it with no exception — but
    its one page has an empty content stream, so ``extract_text()``
    returns ``""``. Stands in for an image-only/scanned PDF with no text
    layer (verified directly against pdfplumber while writing this test).
    """
    parts = [
        b"%PDF-1.4\n",
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /Resources << >> /MediaBox [0 0 200 200] /Contents 5 0 R >>endobj\n",
        b"5 0 obj<< /Length 0 >>\nstream\n\nendstream\nendobj\n",
        b"trailer\n<< /Size 6 /Root 1 0 R >>\n%%EOF\n",
    ]
    return b"".join(parts)


def _real_docx_bytes() -> bytes:
    """A structurally real, DEFLATE-compressed ``.docx`` — a zip
    containing ``word/document.xml``, built with ``zipfile``'s default
    compression (not stored uncompressed), so this is what an actual
    Word export produces: binary-compressed content, not the
    partially-readable mojibake an uncompressed test fixture would leak.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:r><w:t>Experienced QA Engineer</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))

    import jobfinder.paths as paths

    importlib.reload(paths)

    import jobfinder.web.app as web_app

    importlib.reload(web_app)

    yield web_app

    importlib.reload(paths)


@pytest.fixture
def client(isolated_app):
    app = isolated_app.create_app(port=PORT)
    with TestClient(app, base_url=BASE_URL) as c:
        yield app, c


@pytest.fixture
def authed_client(client):
    app, c = client
    resp = c.post("/api/auth/exchange", json={"token": app.state.launch_token.value})
    assert resp.status_code == 200, resp.text
    return app, c


# ── Happy path: PDF upload, stored under the data dir, server-generated name ──


def test_pdf_upload_is_stored_inside_data_dir_under_server_generated_name(authed_client) -> None:
    from jobfinder import paths

    app, c = authed_client
    pdf_bytes = _minimal_pdf_bytes("Experienced QA Engineer")

    resp = c.post("/api/resume", files={"file": ("my_resume.pdf", pdf_bytes, "application/pdf")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text_length"] > 0

    stored_path = app.state.config.resume.path
    data_dir = paths.get_data_dir().resolve()
    resolved = __import__("pathlib").Path(stored_path).resolve()

    # Stored inside the data dir...
    assert data_dir in resolved.parents
    # ...under a server-generated name, never the client's filename.
    assert resolved.name != "my_resume.pdf"


def test_pdf_upload_text_is_extractable_via_load_resume(authed_client) -> None:
    from jobfinder.resume.parser import load_resume

    app, c = authed_client
    pdf_bytes = _minimal_pdf_bytes("Experienced QA Engineer")

    resp = c.post("/api/resume", files={"file": ("resume.pdf", pdf_bytes, "application/pdf")})
    assert resp.status_code == 200, resp.text

    resume_data = load_resume(app.state.config.resume.path)
    assert "Experienced QA Engineer" in resume_data.text


def test_pdf_upload_persists_resume_path_to_config_yaml(authed_client) -> None:
    from jobfinder import paths

    app, c = authed_client
    pdf_bytes = _minimal_pdf_bytes()

    resp = c.post("/api/resume", files={"file": ("resume.pdf", pdf_bytes, "application/pdf")})
    assert resp.status_code == 200, resp.text

    import yaml

    raw = yaml.safe_load(paths.config_path().read_text(encoding="utf-8"))
    assert raw["resume"]["path"] == app.state.config.resume.path


# ── Rejections: wrong type, oversized ────────────────────────────────────


def test_exe_upload_is_rejected_400(authed_client) -> None:
    app, c = authed_client
    # PE header ("MZ") plus padding bytes that are neither valid UTF-8
    # nor a PDF/zip signature — the sniff must reject this regardless of
    # the ".exe" extension or declared content-type below.
    exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + bytes(range(256)) * 4

    resp = c.post("/api/resume", files={"file": ("totally_a_resume.exe", exe_bytes, "application/pdf")})
    assert resp.status_code == 400, resp.text


def test_real_docx_upload_is_rejected_400_naming_word_documents(authed_client) -> None:
    """resume/parser.py has no .docx parser — only .pdf and plain text —
    and the desktop app's file dialogs never offered .docx either
    (ui/wizard.py:118, ui/settings.py:654 both filter "*.pdf *.txt").
    Accepting .docx here would silently feed its raw zip bytes through
    _parse_text's errors="replace" read, producing mojibake that looks
    like a resume but isn't. Must be rejected, with a message that names
    the actual problem rather than a generic "unsupported file type".
    """
    app, c = authed_client

    resp = c.post(
        "/api/resume",
        files={
            "file": (
                "resume.docx",
                _real_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 400, resp.text
    assert "word" in resp.text.lower()


def test_docx_upload_leaves_no_orphan_file(authed_client) -> None:
    from jobfinder import paths

    app, c = authed_client
    before = set(paths.resume_uploads_dir().glob("*")) if paths.resume_uploads_dir().exists() else set()

    resp = c.post("/api/resume", files={"file": ("resume.docx", _real_docx_bytes(), "application/octet-stream")})
    assert resp.status_code == 400, resp.text

    after = set(paths.resume_uploads_dir().glob("*")) if paths.resume_uploads_dir().exists() else set()
    assert after == before


def test_oversized_upload_is_rejected_413(authed_client) -> None:
    from jobfinder.web.resume import MAX_RESUME_BYTES

    app, c = authed_client
    oversized = b"%PDF-1.4\n" + (b"A" * (MAX_RESUME_BYTES + 1024))

    resp = c.post("/api/resume", files={"file": ("big.pdf", oversized, "application/pdf")})
    assert resp.status_code == 413, resp.text

    # Must not have been written to disk as the new resume.
    assert app.state.config.resume.path in ("", None, "resume.pdf")


def test_empty_upload_is_rejected_400(authed_client) -> None:
    app, c = authed_client
    resp = c.post("/api/resume", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert resp.status_code == 400, resp.text


def test_corrupt_pdf_that_passes_the_sniff_is_rejected_400_not_500(authed_client) -> None:
    """A file starting with ``%PDF-`` (passes the magic-byte sniff) but
    with no usable structure — a truncated download, a corrupted file —
    must surface as 400, not an unguarded ``load_resume`` exception
    bubbling up as 500.
    """
    app, c = authed_client

    resp = c.post("/api/resume", files={"file": ("resume.pdf", _corrupt_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 400, resp.text


def test_corrupt_pdf_upload_leaves_no_orphan_file(authed_client) -> None:
    from jobfinder import paths

    app, c = authed_client
    before = set(paths.resume_uploads_dir().glob("*")) if paths.resume_uploads_dir().exists() else set()

    resp = c.post("/api/resume", files={"file": ("resume.pdf", _corrupt_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 400, resp.text

    after = set(paths.resume_uploads_dir().glob("*")) if paths.resume_uploads_dir().exists() else set()
    assert after == before


def test_text_free_pdf_is_rejected_400_and_profile_is_never_built(authed_client, monkeypatch) -> None:
    """A syntactically valid PDF (parses cleanly) with no text layer — an
    image-only/scanned page — must be rejected rather than silently
    accepted with ``text_length: 0`` and handed to Claude for profiling.
    """
    import jobfinder.web.resume as resume_module

    app, c = authed_client
    app.state.config.anthropic.api_key = "sk-ant-test-only-not-real"

    calls: list[object] = []
    monkeypatch.setattr(resume_module, "load_profile", lambda *a, **kw: calls.append(a) or None)

    resp = c.post("/api/resume", files={"file": ("resume.pdf", _text_free_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 400, resp.text
    assert calls == []


def test_text_free_pdf_upload_leaves_no_orphan_file(authed_client) -> None:
    from jobfinder import paths

    app, c = authed_client
    before = set(paths.resume_uploads_dir().glob("*")) if paths.resume_uploads_dir().exists() else set()

    resp = c.post("/api/resume", files={"file": ("resume.pdf", _text_free_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 400, resp.text

    after = set(paths.resume_uploads_dir().glob("*")) if paths.resume_uploads_dir().exists() else set()
    assert after == before


def test_rejected_upload_does_not_change_active_resume_path(authed_client) -> None:
    app, c = authed_client
    original_path = app.state.config.resume.path

    resp = c.post("/api/resume", files={"file": ("resume.pdf", _corrupt_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 400, resp.text
    assert app.state.config.resume.path == original_path


def test_second_successful_upload_replaces_rather_than_accumulates(authed_client) -> None:
    from jobfinder import paths

    app, c = authed_client

    resp1 = c.post(
        "/api/resume", files={"file": ("resume1.pdf", _minimal_pdf_bytes("First Resume"), "application/pdf")}
    )
    assert resp1.status_code == 200, resp1.text
    first_path = app.state.config.resume.path
    assert __import__("pathlib").Path(first_path).exists()

    resp2 = c.post(
        "/api/resume", files={"file": ("resume2.pdf", _minimal_pdf_bytes("Second Resume"), "application/pdf")}
    )
    assert resp2.status_code == 200, resp2.text
    second_path = app.state.config.resume.path

    assert second_path != first_path
    assert not __import__("pathlib").Path(first_path).exists()
    assert __import__("pathlib").Path(second_path).exists()
    # Exactly one upload should remain in the uploads dir.
    assert len(list(paths.resume_uploads_dir().glob("*"))) == 1


def test_declared_content_type_is_not_trusted_over_sniffed_bytes(authed_client) -> None:
    """A client can lie about Content-Type; the sniff must still reject
    binary garbage even when it claims to be a PDF.
    """
    app, c = authed_client
    not_actually_a_pdf = bytes(range(256)) * 8

    resp = c.post("/api/resume", files={"file": ("resume.pdf", not_actually_a_pdf, "application/pdf")})
    assert resp.status_code == 400, resp.text


# ── Path traversal defence in depth ──────────────────────────────────────


def test_client_filename_with_path_traversal_is_never_used_as_a_path(authed_client) -> None:
    from jobfinder import paths

    app, c = authed_client
    pdf_bytes = _minimal_pdf_bytes()

    resp = c.post(
        "/api/resume",
        files={"file": ("../../../../etc/passwd.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text

    resolved = __import__("pathlib").Path(app.state.config.resume.path).resolve()
    data_dir = paths.get_data_dir().resolve()
    assert data_dir in resolved.parents
    assert "etc" not in resolved.parts


# ── Profile building — Anthropic client always mocked, never live ───────


def test_profile_building_is_invoked_with_mocked_anthropic_client(authed_client, monkeypatch) -> None:
    import jobfinder.web.resume as resume_module

    app, c = authed_client
    app.state.config.anthropic.api_key = "sk-ant-test-only-not-real"

    calls: list[tuple[str, str, str]] = []

    def _fake_load_profile(resume_path: str, api_key: str, model: str):
        calls.append((resume_path, api_key, model))
        from jobfinder.resume.profiler import ResumeProfile

        return ResumeProfile(skills=["pytest"], tools=[], job_titles=[])

    monkeypatch.setattr(resume_module, "load_profile", _fake_load_profile)

    resp = c.post("/api/resume", files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile_status"] == "ok"
    assert len(calls) == 1
    assert calls[0][1] == "sk-ant-test-only-not-real"


def test_profile_building_degrades_gracefully_when_no_api_key_configured(authed_client) -> None:
    app, c = authed_client
    assert not app.state.config.anthropic_api_key

    resp = c.post("/api/resume", files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile_status"] == "skipped-no-api-key"


def test_profile_building_degrades_gracefully_when_anthropic_call_fails(authed_client, monkeypatch) -> None:
    import jobfinder.web.resume as resume_module

    app, c = authed_client
    app.state.config.anthropic.api_key = "sk-ant-test-only-not-real"

    def _raising_load_profile(resume_path: str, api_key: str, model: str):
        raise RuntimeError("simulated Anthropic SDK failure — never a real call")

    monkeypatch.setattr(resume_module, "load_profile", _raising_load_profile)

    resp = c.post("/api/resume", files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile_status"] == "failed"
    # The upload itself must still have succeeded despite the profiling failure.
    assert app.state.config.resume.path


# ── Auth enforcement ─────────────────────────────────────────────────────


def test_resume_upload_rejects_request_with_no_session_cookie(client) -> None:
    app, c = client
    resp = c.post("/api/resume", files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 401


# ── T45: display filename reporting, never a path ───────────────────────


def test_sanitize_resume_display_name_strips_unix_traversal() -> None:
    from jobfinder.web.resume import sanitize_resume_display_name

    assert sanitize_resume_display_name("../../etc/passwd") == "passwd"


def test_sanitize_resume_display_name_strips_windows_path() -> None:
    from jobfinder.web.resume import sanitize_resume_display_name

    assert sanitize_resume_display_name("C:\\Windows\\win.ini") == "win.ini"


def test_sanitize_resume_display_name_caps_length() -> None:
    from jobfinder.web.resume import _MAX_DISPLAY_FILENAME_LEN, sanitize_resume_display_name

    long_name = ("a" * 500) + ".pdf"
    result = sanitize_resume_display_name(long_name)
    assert len(result) == _MAX_DISPLAY_FILENAME_LEN


def test_sanitize_resume_display_name_handles_empty_and_none() -> None:
    from jobfinder.web.resume import sanitize_resume_display_name

    assert sanitize_resume_display_name("") == ""
    assert sanitize_resume_display_name(None) == ""


def test_upload_response_reports_sanitised_filename_not_the_stored_path(authed_client) -> None:
    app, c = authed_client
    resp = c.post("/api/resume", files={"file": ("candidate-qa-resume.pdf", _minimal_pdf_bytes(), "application/pdf")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "candidate-qa-resume.pdf"

    stored_path = app.state.config.resume.path
    assert stored_path not in str(body)


def test_upload_with_traversal_filename_is_stored_as_harmless_display_string(authed_client) -> None:
    """The client's declared filename is attacker-controlled; it must be
    reduced to a harmless display string, never treated as (or left
    looking like) a path.
    """
    app, c = authed_client
    resp = c.post(
        "/api/resume",
        files={"file": ("../../etc/passwd", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    reported = resp.json()["filename"]
    assert reported == "passwd"
    assert "/" not in reported and "\\" not in reported
    assert app.state.config.resume.original_filename == "passwd"


def test_upload_with_windows_traversal_filename_is_stored_as_harmless_display_string(authed_client) -> None:
    app, c = authed_client
    resp = c.post(
        "/api/resume",
        files={"file": ("C:\\Windows\\win.ini", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    reported = resp.json()["filename"]
    assert reported == "win.ini"
    assert "/" not in reported and "\\" not in reported


def test_second_upload_replaces_reported_filename(authed_client) -> None:
    app, c = authed_client

    resp1 = c.post("/api/resume", files={"file": ("first.pdf", _minimal_pdf_bytes("First"), "application/pdf")})
    assert resp1.status_code == 200, resp1.text
    assert resp1.json()["filename"] == "first.pdf"

    resp2 = c.post("/api/resume", files={"file": ("second.pdf", _minimal_pdf_bytes("Second"), "application/pdf")})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["filename"] == "second.pdf"

    get_resp = c.get("/api/settings")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["resume"]["filename"] == "second.pdf"


def test_rejected_upload_does_not_change_the_reported_filename(authed_client) -> None:
    app, c = authed_client

    resp1 = c.post("/api/resume", files={"file": ("good.pdf", _minimal_pdf_bytes(), "application/pdf")})
    assert resp1.status_code == 200, resp1.text

    resp2 = c.post("/api/resume", files={"file": ("bad.pdf", _corrupt_pdf_bytes(), "application/pdf")})
    assert resp2.status_code == 400, resp2.text

    assert app.state.config.resume.original_filename == "good.pdf"
