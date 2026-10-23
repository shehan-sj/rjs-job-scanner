"""T16 DoD: no ``async def`` endpoint in ``jobfinder/web`` calls a
``Database`` method directly. A synchronous ``sqlite3`` call inside an
``async def`` endpoint blocks the whole Uvicorn event loop, including the
SSE stream T20 adds later — this is asserted by a test in the suite, not
"in CI" (CI does not exist until T36).

Implemented as a static AST check rather than a runtime trace: it has to
hold for every route regardless of which ones a given test happens to
exercise, and it must also catch the *correct* pattern (``await
run_in_threadpool(db.method, ...)``, where ``db.method`` is a bare
attribute reference, never actually called in the ``async def`` body)
without false-flagging it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from jobfinder.storage.database import Database

_WEB_DIR = Path(__file__).resolve().parent.parent / "jobfinder" / "web"

# Public Database method names - deliberately excludes dunders/private
# helpers. Used as a heuristic: an async route handler literally calling
# `<something>.<one of these names>(...)` is calling a Database method
# directly, whatever `<something>` happens to be named.
_DB_METHOD_NAMES = {name for name in dir(Database) if not name.startswith("_")}

_ROUTE_DECORATOR_METHODS = {"get", "post", "put", "patch", "delete"}


def _is_route_decorator(node: ast.expr) -> bool:
    func = node.func if isinstance(node, ast.Call) else node
    return isinstance(func, ast.Attribute) and func.attr in _ROUTE_DECORATOR_METHODS


def _direct_db_calls_in_route_handlers(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not any(_is_route_decorator(dec) for dec in node.decorator_list):
            continue

        for sub in ast.walk(node):
            # Flag `X.method(...)` (a direct call) but not a bare
            # `X.method` reference passed to run_in_threadpool.
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in _DB_METHOD_NAMES:
                violations.append(
                    f"{py_file}:{sub.lineno}: direct call to .{sub.func.attr}(...) in async def {node.name}"
                )

    return violations


def test_no_async_endpoint_calls_database_method_directly() -> None:
    assert _WEB_DIR.is_dir(), f"expected jobfinder/web at {_WEB_DIR}"

    violations: list[str] = []
    for py_file in sorted(_WEB_DIR.rglob("*.py")):
        violations.extend(_direct_db_calls_in_route_handlers(py_file))

    assert violations == [], "async route handlers calling Database methods directly:\n" + "\n".join(violations)


def test_heuristic_itself_flags_a_known_bad_pattern() -> None:
    """Pins the checker against regressing into a no-op: a synthetic
    ``async def`` route handler that *does* call a Database method
    directly must be caught.
    """
    import tempfile

    bad_source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/bad')\n"
        "async def bad_handler(request):\n"
        "    db = request.app.state.db\n"
        "    return db.get_applied_job_ids()\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as f:
        f.write(bad_source)
        tmp_path = Path(f.name)
    try:
        violations = _direct_db_calls_in_route_handlers(tmp_path)
        assert violations, "checker failed to flag a direct Database call in an async route handler"
    finally:
        tmp_path.unlink(missing_ok=True)
