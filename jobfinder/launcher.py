"""The JobFinder entrypoint (T34) — ``python -m jobfinder``.

T33 deleted ``main.py`` (the Qt entrypoint), which left the app with no
way to start other than hand-rolling a Uvicorn invocation. This is its
replacement, for both a source checkout and the PyInstaller one-dir bundle
(``jobfinder/__main__.py`` is the bundle's entry script; everything real
lives here so that file can stay a two-line shim).

The order of operations is not a style choice — it is forced twice over.

**Why the socket is bound before the app exists.** T15's security
middleware builds the Host and Origin allowlists from the port, at
*app-creation* time (``host_allowlist(port)`` in
``jobfinder/web/security.py``), because both are exact-match checks
against ``127.0.0.1:<port>`` and friends. Uvicorn, left to itself, binds
the socket *after* the app object exists. With ``port=0`` (which is what
we want — never collide with whatever else is on a fixed port) there is
therefore no moment at which Uvicorn's chosen port could be fed back into
an allowlist that has already been built. The two ways out of that are to
hardcode a port or to loosen the allowlist to a prefix/wildcard match, and
both are worse than inverting the order: bind the socket here, read the
port off it, build the app with the real port, and hand the *already
bound* socket to Uvicorn via ``Server.run(sockets=[sock])``.

**Why the browser is opened last.** The tab is opened only once the server
reports it is up. Opening it earlier — the obvious "open the browser, then
block in run()" shape — races the user against the server's startup and
shows a connection-refused page often enough to look broken.

**Why a second launch does not start a server.** The product spec: "Starting a
second instance must not fail on a port collision — it opens the existing
instance instead. A single-instance lock prevents two schedulers." The
lock (``jobfinder/instance_lock.py``) records the running instance's port
*and* launch token, so the second process can open a URL that actually
authenticates, then exit 0 without binding anything, without starting a
scheduler and without logging an error.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import webbrowser

import uvicorn

from jobfinder import paths
from jobfinder.instance_lock import InstanceInfo, InstanceLock, instance_url
from jobfinder.web.app import create_app
from jobfinder.web.logging_setup import configure_logging

logger = logging.getLogger("jobfinder.launcher")

# How long the browser-opening thread waits for Uvicorn to report that it
# is serving before giving up and opening the tab anyway. Generous: a cold
# start on a slow disk pays for the config load, the SQLite migrations and
# APScheduler's startup. If it does expire, opening the tab is still the
# better failure mode than never opening one — the user can reload.
STARTUP_TIMEOUT_SECONDS = 30.0
_STARTUP_POLL_SECONDS = 0.05


def bind_loopback_socket() -> tuple[socket.socket, int]:
    """Bind and listen on an OS-assigned loopback port; return socket and port.

    ``127.0.0.1`` explicitly, never ``0.0.0.0`` and never ``::`` (the product spec):
    this server is unauthenticated apart from a session cookie
    and must not be reachable from the network.

    ``SO_REUSEADDR`` is deliberately *not* set. On Windows it does not mean
    what it means on BSD/Linux — it allows another process to bind the same
    address outright, which for a loopback server holding a session cookie
    would be a hijacking primitive rather than a convenience. We are
    binding an ephemeral port anyway, so there is no ``TIME_WAIT`` problem
    to work around.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
    except BaseException:
        sock.close()
        raise
    port = sock.getsockname()[1]
    logger.info("Bound loopback socket on port %d.", port)
    return sock, port


def open_url(url: str) -> None:
    """Open ``url`` in the user's default browser.

    A thin, patchable seam: tests replace this to assert *when* it is
    called relative to the server being up, and ``--no-browser`` skips it
    so a headless check can count processes without spraying tabs.
    """
    try:
        webbrowser.open(url)
    except Exception as exc:  # pragma: no cover - platform browser plumbing
        # Never let a missing/odd default browser take down the server the
        # user just started; the URL is logged so they can paste it.
        logger.warning("Could not open a browser automatically (%s). Open this URL manually: %s", exc, url)


def _open_when_serving(server: uvicorn.Server, url: str) -> threading.Thread:
    """Open ``url`` once ``server`` reports it is serving.

    ``Server.started`` is set by Uvicorn after startup completes (lifespan
    startup included, so the scheduler and DB are already up when the page
    loads — a tab that opens onto a half-initialized app shows spurious
    "scheduler not responding" in the heartbeat strip).
    """

    def _wait_and_open() -> None:
        waited = 0.0
        while not server.started and waited < STARTUP_TIMEOUT_SECONDS:
            if server.should_exit:
                return  # stopped before it ever came up: no tab to open
            threading.Event().wait(_STARTUP_POLL_SECONDS)
            waited += _STARTUP_POLL_SECONDS
        if not server.started:
            logger.warning("Server did not report startup within %.0fs; opening the browser anyway.", waited)
        open_url(url)

    thread = threading.Thread(target=_wait_and_open, name="jobfinder-open-browser", daemon=True)
    thread.start()
    return thread


def serve(*, lock: InstanceLock, open_browser: bool = True) -> int:
    """Run the server to completion. Returns a process exit code.

    Returns only when the server stops: SIGINT/SIGTERM (Uvicorn's own
    handlers) or ``POST /api/stop`` (``jobfinder/web/control.py``). Both
    paths go through the ASGI lifespan shutdown, which is what shuts down
    the APScheduler thread and closes a live FillerBot Chrome — that is
    why the stop path leaves no orphan process.
    """
    sock, port = bind_loopback_socket()
    try:
        app = create_app(port=port)
        token: str = app.state.launch_token.value
        url = instance_url(InstanceInfo(pid=os.getpid(), port=port, token=token))

        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        )
        # Registered before serving starts: a request could otherwise
        # arrive against a controller with no handler and get a 501.
        app.state.stop_controller.register(lambda: setattr(server, "should_exit", True))

        # Acquired before the first request can be served, so a second
        # launch racing us finds the lock rather than a free port.
        lock.acquire(port=port, token=token)

        if open_browser:
            _open_when_serving(server, url)
        else:
            # The URL is the one thing a --no-browser run has to surface on
            # stdout. logger.info only reaches a console when stdout is a
            # tty (configure_logging's isatty guard), so a piped or
            # SSH-captured run — exactly what this flag exists for — saw
            # nothing at all while the server came up fine.
            _print_quietly(f"JobFinder is serving at: {url}")
            logger.info("Started with --no-browser. Open this URL to use the app: %s", url)

        server.run(sockets=[sock])
    finally:
        lock.release()
        # Uvicorn closes the sockets it was handed, but closing an already
        # closed socket is harmless and this guarantees the descriptor is
        # gone even if run() raised before it got that far.
        sock.close()
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jobfinder",
        description="Start JobFinder and open it in your browser.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help=(
            "Do not open a browser tab; print the URL instead. Used by the clean-environment "
            "check, which has to count processes, and handy over SSH."
        ),
    )
    # argparse reads sys.argv[1:] when handed None. Passing [] instead
    # silently discarded every flag from the real command line, so
    # `python -m jobfinder --no-browser` opened a browser anyway and never
    # printed the URL — the documented headless/SSH path did not work at
    # all. Callers that must not see the ambient argv (tests) pass [].
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Before anything touches a path: this creates the data root, points
    # PLAYWRIGHT_BROWSERS_PATH into it, and migrates a legacy install.
    # The lock file lives in that data root, so it has to happen first.
    paths.setup_data_environment()
    # Not ``logging.basicConfig``: the packaged build is windowed, so
    # ``sys.stderr`` is ``None`` there and basicConfig's StreamHandler
    # would raise on its first emit — the exact situation where a log is
    # the only way to find out what went wrong. ``configure_logging()``
    # writes to the data dir's rotating log file and only adds a console
    # handler when stdout is a real terminal. It is refcounted, so the
    # app's own lifespan calling it again is expected; we deliberately
    # never tear this one down, so a failure *after* the lifespan has
    # exited is still recorded.
    configure_logging()

    lock = InstanceLock(paths.instance_lock_path())
    running = lock.running_instance()
    if running is not None:
        # the product spec: open the existing instance, quietly. No port bind,
        # no scheduler, no error — and exit 0, because from the user's
        # point of view double-clicking the app worked.
        logger.info("JobFinder is already running (pid %d, port %d) — opening it.", running.pid, running.port)
        if not args.no_browser:
            open_url(instance_url(running))
        else:
            _print_quietly(f"JobFinder is already running at http://127.0.0.1:{running.port}/")
        return 0

    return serve(lock=lock, open_browser=not args.no_browser)


def _print_quietly(message: str) -> None:
    """``print`` that tolerates a windowed build with no stdout attached.

    Flushes deliberately: this process then serves for hours, and a
    non-tty stdout is block-buffered, so without a flush the line sits in
    the buffer until exit — indistinguishable from the launcher never
    printing it.
    """
    try:
        if sys.stdout is not None:
            print(message, flush=True)
    except (OSError, ValueError):  # pragma: no cover - windowed-build plumbing
        pass
