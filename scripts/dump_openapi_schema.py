#!/usr/bin/env python
"""Dump the FastAPI app's OpenAPI 3.1.0 schema to ``frontend/openapi.json`` (T21).

``frontend/src/api/schema.ts`` is generated from that JSON file by
``openapi-typescript`` (see ``frontend/package.json``'s ``gen:api`` script),
so a backend response-model field rename becomes a frontend compile error
instead of a silent runtime mismatch.

This never boots uvicorn or binds a socket — ``FastAPI.openapi()`` just
walks the already-registered routes and Pydantic models in-process, so this
is as fast and offline as ``scripts/smoke.py``.

Usage:
    .venv/Scripts/python.exe scripts/dump_openapi_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from jobfinder.web.app import create_app  # noqa: E402


def main() -> int:
    app = create_app()
    schema = app.openapi()

    out_path = _REPO_ROOT / "frontend" / "openapi.json"
    out_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} ({len(schema.get('paths', {}))} paths).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
