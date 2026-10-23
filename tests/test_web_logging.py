"""Tests for jobfinder.web.logging_setup (T43) — file logging owned by the
web app's lifespan rather than by ``main.py``'s Qt entrypoint.

Why these tests look the way they do:

* ``main.py``'s ``_setup_logging`` was the *only* place a
  ``RotatingFileHandler`` was installed, so running the server standalone
  produced no log file at all and scan activity survived only in connected
  SSE clients. T33 deleted ``main.py``, so this had to move first.
* The suite builds ``create_app()`` hundreds of times. A per-app handler
  install that isn't idempotent would stack handlers, duplicate every line
  N times, and keep file handles open — on Windows an open handle blocks
  ``tmp_path`` cleanup and fails *other* tests
  (``tests/test_database_connections.py`` exists for that class of bug, and
  the strict-``rmtree`` test below follows its convention).
* T20's ``make_scan_fn`` used to raise the *root* logger's level for the
  duration of each scan. These tests pin the replacement behaviourally — a
  deliberately configured root level must come out of a scan unchanged —
  rather than grepping the source for ``setLevel``.
"""

from __future__ import annotations

import importlib
import json
import logging
import logging.handlers
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PORT = 8771
BASE_URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    """Same isolation convention as tests/test_web_stream.py: point the data
    root at a tmp dir and reload the modules that resolved paths at import
    time.
    """
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


def _log_file() -> Path:
    from jobfinder import paths

    return paths.logs_dir() / "jobfinder.log"


def _jobfinder_file_handlers() -> list[logging.handlers.RotatingFileHandler]:
    return [h for h in logging.getLogger("jobfinder").handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


class _StubOrchestrator:
    """No network, no real scrape (same seam T19/T20 tests use). Logs one
    INFO line so both destinations — the file handler and T20's
    ``BroadcastLogHandler`` — have something to observe.
    """

    MESSAGE = "stub scan progress line (no network)"

    def __init__(self, events, *, delay: float = 0.0) -> None:
        self._events = events
        self._delay = delay

    def run_scan(self) -> None:
        self._events.put({"type": "scan_start"})
        logging.getLogger("jobfinder.test_stub").info(self.MESSAGE)
        if self._delay:
            time.sleep(self._delay)
        self._events.put({"type": "scan_complete", "new_jobs": 0, "matches": 0})


def _patch_stub_builder(monkeypatch, *, delay: float = 0.0) -> None:
    import jobfinder.web.scan as scan_module

    monkeypatch.setattr(
        scan_module,
        "_build_orchestrator",
        lambda db, config, events: _StubOrchestrator(events, delay=delay),
    )


def _read_log() -> str:
    log_file = _log_file()
    if not log_file.exists():
        return ""
    return log_file.read_text(encoding="utf-8", errors="replace")


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ── A log file exists, under logs_dir(), with no Qt involvement ──────────


def test_lifespan_creates_log_file_under_logs_dir(client) -> None:
    _app, _c = client
    log_file = _log_file()
    assert log_file.exists(), f"no log file at {log_file}"
    assert log_file.parent == Path(log_file.parent)
    assert _read_log().strip() != "", "log file created but nothing written to it"


def test_file_handler_is_a_rotating_handler_with_main_pys_limits(client) -> None:
    """Ported values, not invented ones: 5 MiB x 3 backups and
    ``main.py``'s formatter, as in its ``_setup_logging``.
    """
    _app, _c = client
    handlers = _jobfinder_file_handlers()
    assert len(handlers) == 1, handlers
    handler = handlers[0]
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 3
    assert handler.level in (logging.NOTSET, logging.INFO)
    assert Path(handler.baseFilename) == _log_file()
    assert handler.formatter is not None
    assert handler.formatter._fmt == "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"


def test_scan_info_lines_land_in_the_log_file(authed_client, monkeypatch) -> None:
    _patch_stub_builder(monkeypatch)
    _app, c = authed_client

    assert c.post("/api/scan").status_code == 202

    assert _wait_for(lambda: _StubOrchestrator.MESSAGE in _read_log()), _read_log()


def test_scan_log_lines_reach_both_the_file_and_sse_clients(authed_client, monkeypatch) -> None:
    """T20's SSE log stream must keep working after the root-logger
    mutation is gone — the file is an *additional* destination, not a
    replacement.
    """
    _patch_stub_builder(monkeypatch, delay=0.05)
    _app, c = authed_client

    seen: list[dict] = []
    with c.stream("GET", "/api/stream/scan") as resp:
        assert resp.status_code == 200, resp.text
        assert c.post("/api/scan").status_code == 202

        deadline = time.monotonic() + 10
        for line in resp.iter_lines():
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if payload:
                    seen.append(json.loads(payload))
            if any(e.get("type") == "scan_complete" for e in seen):
                break
            if time.monotonic() > deadline:
                break

    log_events = [e for e in seen if e.get("type") == "log"]
    assert any(_StubOrchestrator.MESSAGE in e.get("message", "") for e in log_events), seen
    assert _StubOrchestrator.MESSAGE in _read_log()


# ── A deliberately configured root level survives a scan ─────────────────


@pytest.mark.parametrize("deliberate_level", [logging.DEBUG, logging.WARNING])
def test_deliberately_configured_root_level_is_not_overridden_by_a_scan(
    authed_client, monkeypatch, deliberate_level
) -> None:
    _patch_stub_builder(monkeypatch)
    _app, c = authed_client

    root = logging.getLogger()
    original = root.level
    root.setLevel(deliberate_level)
    try:
        assert c.post("/api/scan").status_code == 202
        assert _wait_for(lambda: _StubOrchestrator.MESSAGE in _read_log()), _read_log()
        # The scan must not have touched the operator's root level, either
        # during the cycle or by "restoring" a value it had no business
        # setting in the first place.
        assert root.level == deliberate_level
    finally:
        root.setLevel(original)


class _RootLevelObservingOrchestrator(_StubOrchestrator):
    """Records the root logger's level *from inside* the scan cycle.

    This is the assertion that actually catches T20's removed mutation.
    The before/after checks above cannot: the old code restored the level
    in a ``finally``, so from outside the scan it looked untouched while
    an operator's deliberate WARNING was silently INFO for the whole
    duration of every scan — including for any third-party library logging
    on root at the time.
    """

    observed_root_level: int | None = None

    def run_scan(self) -> None:
        type(self).observed_root_level = logging.getLogger().level
        super().run_scan()


def test_root_level_is_not_mutated_during_the_scan_itself(authed_client, monkeypatch) -> None:
    import jobfinder.web.scan as scan_module

    _RootLevelObservingOrchestrator.observed_root_level = None
    monkeypatch.setattr(
        scan_module,
        "_build_orchestrator",
        lambda db, config, events: _RootLevelObservingOrchestrator(events),
    )
    _app, c = authed_client

    root = logging.getLogger()
    original = root.level
    root.setLevel(logging.WARNING)
    try:
        assert c.post("/api/scan").status_code == 202
        assert _wait_for(lambda: _RootLevelObservingOrchestrator.observed_root_level is not None)
        assert _RootLevelObservingOrchestrator.observed_root_level == logging.WARNING, (
            "the scan raised the root logger's level mid-cycle"
        )
        # ...and the scan's INFO lines still reached the file, which is why
        # the mutation is not needed.
        assert _wait_for(lambda: _StubOrchestrator.MESSAGE in _read_log()), _read_log()
    finally:
        root.setLevel(original)
        _RootLevelObservingOrchestrator.observed_root_level = None


def test_configuring_logging_does_not_touch_the_root_logger(client) -> None:
    """Library code configures its own ``jobfinder`` logger; hijacking root
    is an application entrypoint's prerogative, not a server module's.
    """
    _app, _c = client
    root = logging.getLogger()
    assert not any(
        isinstance(h, logging.handlers.RotatingFileHandler) and Path(h.baseFilename) == _log_file()
        for h in root.handlers
    ), root.handlers
    assert _jobfinder_file_handlers(), "the jobfinder logger is where the handler belongs"


def test_scan_emits_info_lines_with_root_at_notset(authed_client, monkeypatch) -> None:
    """With root at NOTSET, a scan's INFO lines still have to be emitted —
    proof the fix was "configure the jobfinder logger", not "raise root for
    the duration of the scan".
    """
    _patch_stub_builder(monkeypatch)
    _app, c = authed_client

    root = logging.getLogger()
    original = root.level
    root.setLevel(logging.NOTSET)
    try:
        assert c.post("/api/scan").status_code == 202
        assert _wait_for(lambda: _StubOrchestrator.MESSAGE in _read_log()), _read_log()
        assert root.level == logging.NOTSET
    finally:
        root.setLevel(original)


# ── Idempotency under repeated create_app() ──────────────────────────────


def test_repeated_app_creation_does_not_stack_handlers(isolated_app) -> None:
    jf_logger = logging.getLogger("jobfinder")
    before = list(jf_logger.handlers)

    for _ in range(3):
        app = isolated_app.create_app(port=PORT)
        with TestClient(app, base_url=BASE_URL):
            inside = [h for h in jf_logger.handlers if h not in before]
            assert len(inside) <= 2, inside
            assert len([h for h in inside if isinstance(h, logging.handlers.RotatingFileHandler)]) == 1, inside

    assert list(jf_logger.handlers) == before, "handler leaked after lifespan shutdown"


def test_nested_app_creation_does_not_duplicate_log_lines(isolated_app) -> None:
    app_a = isolated_app.create_app(port=PORT)
    app_b = isolated_app.create_app(port=PORT)
    marker = "duplicate-detection-marker-9f1c"

    with TestClient(app_a, base_url=BASE_URL):
        with TestClient(app_b, base_url=BASE_URL):
            logging.getLogger("jobfinder.dup_test").info(marker)

    text = _read_log()
    assert text.count(marker) == 1, text


def test_log_file_handle_is_released_so_a_strict_rmtree_succeeds(monkeypatch) -> None:
    """The real proof the handler is closed, in the same spirit as
    ``tests/test_database_connections.py``: a non-``ignore_errors``
    ``shutil.rmtree`` of the data root must succeed. A live
    ``RotatingFileHandler`` would raise ``WinError 32`` here, and in the
    real suite it would instead break whatever unrelated test owned the
    ``tmp_path``.
    """
    tmp = tempfile.mkdtemp(prefix="jobfinder-logging-close-test-")
    monkeypatch.setenv("JOBFINDER_DATA_DIR", tmp)

    import jobfinder.paths as paths

    importlib.reload(paths)
    import jobfinder.web.app as web_app

    importlib.reload(web_app)

    try:
        app = web_app.create_app(port=PORT)
        with TestClient(app, base_url=BASE_URL):
            logging.getLogger("jobfinder.close_test").info("a line to force the file open")
        assert (Path(tmp) / "logs" / "jobfinder.log").exists()
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        importlib.reload(paths)
        raise

    # No try/finally on purpose — a strict rmtree failing *is* the failure.
    shutil.rmtree(tmp)
    assert not Path(tmp).exists()
    importlib.reload(paths)


def test_concurrent_configure_and_teardown_is_safe(isolated_app) -> None:
    """``create_app()`` is not serialised by anything in the product, and
    the install path mutates process-global logger state.
    """
    from jobfinder.web import logging_setup

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(10):
                key = logging_setup.configure_logging()
                logging.getLogger("jobfinder.concurrency_test").info("line")
                logging_setup.teardown_logging(key)
        except BaseException as exc:  # pragma: no cover - diagnostic
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert not _jobfinder_file_handlers(), logging.getLogger("jobfinder").handlers
