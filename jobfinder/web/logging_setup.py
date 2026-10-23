"""Logging configuration for the web app's lifespan (T43).

Ported out of ``main.py``'s ``_setup_logging``, which was the only place a
``RotatingFileHandler`` was ever installed. Running the server without Qt
produced no log file at all — a scan's progress lines existed only in
whatever SSE clients happened to be attached (``jobfinder/web/stream.py``)
and were otherwise lost — and T33 deleted ``main.py``, which would have
taken file logging out of the product entirely. Same "business logic
trapped in the Qt entrypoint" shape as T6/T9/T10.

Two deliberate differences from ``main.py``:

1. **The ``jobfinder`` logger, not the root logger.** ``main.py`` is an
   application entrypoint and owns the process, so ``basicConfig`` on root
   is legitimate there. This module is imported by a server library that
   also runs under pytest, under a future launcher, and potentially
   embedded; installing handlers on root and raising root's level would
   stamp on a level the operator may have set deliberately, and would
   capture every third-party library's records into our file. Configuring
   ``logging.getLogger("jobfinder")`` gets every record this product
   emits, because level filtering happens on the *originating* logger:
   ``jobfinder.orchestrator`` resolves its effective level from
   ``jobfinder`` (INFO), passes, and the record is then handed to every
   ancestor handler *regardless of the ancestors' levels*. So T20's
   ``BroadcastLogHandler`` on the root logger keeps receiving scan lines,
   and the root logger's own level stays exactly where the operator put
   it. That is also what let ``jobfinder/web/scan.py`` drop its per-scan
   ``root_logger.setLevel`` mutation.

   The level is only raised when the ``jobfinder`` logger is still
   ``NOTSET`` or coarser than INFO, so ``-o log_level=DEBUG``, a test, or
   an operator asking for DEBUG is never quietly downgraded.

2. **Install is reference-counted and reversible.** ``create_app()`` runs
   hundreds of times in one test session. Adding a handler per app would
   stack handlers, duplicate every line N times, and — worse on Windows —
   keep a file handle open inside a ``tmp_path`` that pytest then tries to
   delete, failing an unrelated test. Installs for the same resolved log
   path share one handler set; the last teardown removes *and closes*
   them, and restores the level it changed.

``ui.console_show_timestamp`` / ``ui.console_show_level`` are deliberately
*not* consulted here. They are display preferences for the log console UI
(``jobfinder/ui/log_console.py``, deleted in T33, and now ``frontend/src/
components/LogDrawer.tsx``, applying them client-side to the SSE events).
``main.py``'s ``_setup_logging`` never honoured them either — its file and
console handlers both use the fixed formatter below. Letting them strip the
timestamp or level out of the *file* would make the on-disk log
unparseable for diagnostics, which is the opposite of what the setting is
for.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from dataclasses import dataclass

from .. import paths

#: Name, rotation limits and formatter are ``main.py``'s values verbatim,
#: so an existing install's ``jobfinder.log`` keeps the same shape.
LOG_FILENAME = "jobfinder.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"

#: Every logger this product creates is a descendant of this one.
PACKAGE_LOGGER = "jobfinder"


@dataclass
class _Installation:
    """One handler set, shared by every app instance pointing at the same
    log file.
    """

    handlers: list[logging.Handler]
    refcount: int = 1
    previous_level: int = logging.NOTSET
    level_raised: bool = False


_lock = threading.Lock()
_installed: dict[str, _Installation] = {}


def _make_console_handler(formatter: logging.Formatter) -> logging.Handler | None:
    """A stdout handler, but only when stdout is a real terminal — the same
    guard ``main.py`` used. In a packaged (windowed) build stdout may be
    ``None`` or a non-tty pipe, and under pytest it is captured, so this
    returns ``None`` in both cases rather than adding a second destination
    nobody reads.
    """
    stream = sys.stdout
    if stream is None or not hasattr(stream, "isatty"):
        return None
    try:
        if not stream.isatty():
            return None
    except (ValueError, OSError):
        # Closed or detached stream — not a terminal for our purposes.
        return None
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        # Non-reconfigurable stream (e.g. a plain io wrapper). Harmless:
        # the handler still works, it may just mangle non-ASCII.
        pass
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    return handler


def configure_logging() -> str:
    """Install file (and, on a terminal, console) logging for the
    ``jobfinder`` logger.

    Safe to call repeatedly: calls resolving to the same log file share one
    handler set. Returns an opaque key that must be handed back to
    :func:`teardown_logging` exactly once, by the same owner.
    """
    log_dir = paths.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILENAME
    key = str(log_path.resolve())

    logger = logging.getLogger(PACKAGE_LOGGER)

    with _lock:
        existing = _installed.get(key)
        if existing is not None:
            existing.refcount += 1
            return key

        formatter = logging.Formatter(LOG_FORMAT)
        # ``delay=True``: no file handle is opened until something is
        # actually logged, so merely building an app never locks a file.
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_path),
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(formatter)
        handlers: list[logging.Handler] = [file_handler]

        console_handler = _make_console_handler(formatter)
        if console_handler is not None:
            handlers.append(console_handler)

        installation = _Installation(handlers=handlers, previous_level=logger.level)

        # Only coarsen-to-INFO, never downgrade a deliberate DEBUG.
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            # Don't fight a level another live installation already set.
            if not _installed:
                installation.level_raised = True
            logger.setLevel(logging.INFO)

        for handler in handlers:
            logger.addHandler(handler)

        _installed[key] = installation
        return key


def teardown_logging(key: str) -> None:
    """Release one :func:`configure_logging` reference.

    When the last reference for a log file goes away its handlers are
    removed *and closed* — closing is the part that matters on Windows,
    where a live handle on a file inside a ``tmp_path`` makes pytest's
    cleanup of that directory fail. Unknown or already-released keys are
    ignored so a failed startup can't raise out of a ``finally``.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)

    with _lock:
        installation = _installed.get(key)
        if installation is None:
            return
        installation.refcount -= 1
        if installation.refcount > 0:
            return
        del _installed[key]

        for handler in installation.handlers:
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        if installation.level_raised and not _installed:
            logger.setLevel(installation.previous_level)
