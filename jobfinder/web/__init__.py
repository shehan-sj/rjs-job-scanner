"""Phase 2 web backend (FastAPI) — jobfinder/web/.

Replaces the PySide6 desktop app's process model (main.py, deleted by
T33) with a single long-running server process the browser talks to over
HTTP/SSE. See the product spec's deployment and non-functional
contracts for the constraints every module in this package must honour:
loopback-only binding, no accounts/tenancy, and the security controls
implemented in ``security.py`` (T15).
"""

from __future__ import annotations
