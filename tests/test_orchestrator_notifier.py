"""Behaviour-pinning tests for Orchestrator's Qt-free Notifier protocol (T6).

Orchestrator.__init__ used to take an untyped `tray` argument and
`_alert_batch` called `self._tray.notify(title=..., message=...)` directly,
making the "Qt-free core" depend on `jobfinder.ui.tray.TrayIcon`. These tests
pin: (1) Orchestrator can be constructed with no notifier at all and still
runs `_alert_batch` without raising, (2) a custom notifier satisfying the
`Notifier` protocol receives the expected title/message, (3) the protocol's
own signature, and (4) that orchestrator.py imports neither PySide6 nor the
`jobfinder.ui` package — directly or transitively.

T33 deleted `jobfinder/ui/`, so (4) replaced an earlier test that AST-parsed
`jobfinder/ui/tray.py` to confirm `TrayIcon.notify` still matched the
protocol. With the Qt side gone there is no TrayIcon to compare against, and
the assertion worth keeping is the one in the other direction: Qt must not
come back into the core.
"""

from __future__ import annotations

import inspect
import queue

from jobfinder.matcher.models import JobPosting, MatchResult
from jobfinder.orchestrator import NullNotifier, Orchestrator


class _FakeDb:
    def save_match(self, result) -> None:
        pass


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.calls.append((title, message))


def _match(job_id: str, title: str, score: int) -> MatchResult:
    job = JobPosting(
        job_id=job_id,
        title=title,
        company="Acme",
        location="Remote",
        description="",
        url=f"https://example.com/{job_id}",
        source="indeed",
    )
    return MatchResult(job=job, score=score, signals={}, reasoning="")


def test_orchestrator_requires_no_notifier() -> None:
    """Orchestrator works with no notifier supplied (defaults to a no-op)."""
    orch = Orchestrator(config=None, db=_FakeDb(), tk_queue=queue.Queue())
    assert isinstance(orch._tray, NullNotifier)

    # Must not raise even though nothing is listening.
    orch._alert_batch([_match("1", "QA Engineer", 80)])


def test_orchestrator_alert_batch_calls_custom_notifier() -> None:
    notifier = _RecordingNotifier()
    orch = Orchestrator(config=None, db=_FakeDb(), tk_queue=queue.Queue(), tray=notifier)

    orch._alert_batch([_match("1", "QA Engineer", 80)])

    assert len(notifier.calls) == 1
    title, message = notifier.calls[0]
    assert title == "1 New Match Found"
    assert message == "QA Engineer @ Acme"


def test_notifier_protocol_signature_is_title_message() -> None:
    """The protocol's shape is itself pinned, so a later rename of
    `title`/`message` has to break a test rather than silently breaking
    every caller that passes them by keyword (`_alert_batch` does)."""
    from jobfinder.orchestrator import Notifier

    protocol_sig = inspect.signature(Notifier.notify)
    assert list(protocol_sig.parameters) == ["self", "title", "message"]


def test_orchestrator_module_does_not_import_qt_or_the_deleted_ui_package() -> None:
    """The point of T6's Notifier protocol was that the Qt-free core must not
    reach for `jobfinder.ui.tray.TrayIcon`. Until T33 this was checked
    indirectly, by AST-parsing `jobfinder/ui/tray.py` to confirm TrayIcon
    still structurally satisfied the protocol. That package is now deleted, so
    the check becomes a direct one on orchestrator.py: no import of PySide6 or
    of `jobfinder.ui` in its AST. `jobfinder.ui` appears in a docstring there
    as history, which is why this inspects imports rather than raw text.
    """
    import ast
    from pathlib import Path

    src = Path("jobfinder/orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(src, filename="jobfinder/orchestrator.py")

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)

    offenders = [
        name
        for name in imported
        if name == "PySide6"
        or name.startswith("PySide6.")
        or name == "jobfinder.ui"
        or name.startswith("jobfinder.ui.")
    ]
    assert offenders == [], f"jobfinder/orchestrator.py must stay Qt-free, but imports: {offenders}"


def test_importing_orchestrator_pulls_in_no_qt() -> None:
    """Fresh-interpreter check, so a transitive import of Qt through one of
    orchestrator.py's own dependencies is caught too, not just a direct one.
    """
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import jobfinder.orchestrator; "
            "assert not any(n == 'PySide6' or n.startswith('PySide6.') for n in sys.modules), "
            "'PySide6 was imported transitively'",
        ],
        check=True,
        capture_output=True,
    )
