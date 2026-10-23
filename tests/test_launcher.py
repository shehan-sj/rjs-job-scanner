"""Tests for jobfinder.launcher (T34) — the entrypoint T33 deleted.

T33 removed ``main.py``, which left the app with no way to start at all.
This module is its replacement, and the *order* of its steps is the whole
point:

  1. Pre-bind a loopback socket on port 0 and read the assigned port off
     it. This is forced by T15: ``create_app(port=...)`` builds the Host
     and Origin allowlists from the port at app-creation time, but Uvicorn
     binds the socket *after* the app exists — so ``port=0`` can never be
     resolved into the allowlist afterwards. The only orders that work are
     "bind first" or "hardcode a port / weaken the allowlist", and the
     latter two are not acceptable.
  2. ``create_app(port=<that port>)``.
  3. Hand the already-bound socket to Uvicorn via ``run(sockets=[sock])``.
  4. **Then** open the browser. Opening it earlier gives the user a
     connection-refused page.

``test_serve_binds_creates_serves_then_opens_the_browser`` asserts step 4
happens after step 3 by probing ``/api/health`` from inside the fake
browser opener: the assertion is "the server was already answering when
the browser was told to go there", which is the actual user-visible
property, rather than a mock call-order check that would pass even if the
socket were not accepting yet.
"""

from __future__ import annotations

import importlib
import json
import logging
import socket
import sys
import threading
import urllib.request

import pytest

from jobfinder import instance_lock as il


@pytest.fixture(autouse=True)
def _contain_launcher_logging():
    """Undo the logging the launcher installs, for this test process only.

    ``launcher.main`` calls ``configure_logging()`` and deliberately never
    tears it down: in a real process the launcher owns logging for the
    whole run, so a failure *after* the app's lifespan has exited is still
    recorded. That is correct for production and wrong for a shared test
    process — the refcount stays above zero and the ``jobfinder`` logger
    keeps a handler pointing at this test's ``tmp_path``, which then makes
    ``tests/test_web_logging.py``'s handler assertions fail depending on
    file order. Isolate it here rather than weakening the launcher.

    Reaches into ``logging_setup._installed`` on purpose: the refcount
    registry is the state that leaks, and there is no public way to reset
    it without a key the launcher discards.
    """
    from jobfinder.web import logging_setup as ls

    package_logger = logging.getLogger(ls.PACKAGE_LOGGER)
    before_handlers = list(package_logger.handlers)
    before_level = package_logger.level
    before_installed = dict(ls._installed)  # noqa: SLF001 — see docstring

    yield

    for handler in list(package_logger.handlers):
        if handler not in before_handlers:
            package_logger.removeHandler(handler)
            handler.close()
    package_logger.setLevel(before_level)
    ls._installed.clear()  # noqa: SLF001
    ls._installed.update(before_installed)  # noqa: SLF001


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """The launcher module, pointed at a throwaway data dir."""
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "jobfinder-data"))

    from jobfinder import paths

    importlib.reload(paths)
    import jobfinder.web.app as web_app

    importlib.reload(web_app)
    from jobfinder import launcher as mod

    importlib.reload(mod)

    yield mod

    importlib.reload(paths)


def test_bind_loopback_socket_is_bound_and_listening_on_an_ephemeral_port(launcher) -> None:
    sock, port = launcher.bind_loopback_socket()
    try:
        assert port > 0
        assert sock.getsockname() == ("127.0.0.1", port)
        # Listening already: a client can connect before Uvicorn exists,
        # which is exactly why handing the socket over works.
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    finally:
        sock.close()


def test_bind_loopback_socket_never_binds_a_routable_address(launcher) -> None:
    """The product spec: loopback only, never 0.0.0.0 and never ``::``."""
    sock, _ = launcher.bind_loopback_socket()
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()


def test_serve_binds_creates_serves_then_opens_the_browser(launcher) -> None:
    opened: list[tuple[str, bool]] = []

    def fake_open(url: str) -> None:
        # Parse the port back out of the URL we were handed and check the
        # server is *already* answering. This is the connection-refused
        # regression, stated as an assertion.
        port = int(url.split("127.0.0.1:")[1].split("/")[0])
        opened.append((url, il.health_responds(port, timeout=5.0)))

    launcher.open_url = fake_open

    from jobfinder import paths

    lock = il.InstanceLock(paths.instance_lock_path())

    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(launcher.serve(lock=lock)), daemon=True)
    thread.start()

    # Wait for the lock file to appear, which the launcher writes only
    # after the socket is bound and the app is built.
    info = None
    for _ in range(100):
        info = lock.read()
        if info is not None and il.health_responds(info.port, timeout=0.5):
            break
        threading.Event().wait(0.1)
    assert info is not None, "launcher never wrote an instance lock"

    try:
        assert info.port > 0
        assert info.token, "the lock must carry the launch token, or a second launch cannot authenticate"

        # The browser was opened, and the server was already serving when
        # it was.
        for _ in range(50):
            if opened:
                break
            threading.Event().wait(0.1)
        assert opened, "the browser was never opened"
        url, was_serving = opened[0]
        assert url == f"http://127.0.0.1:{info.port}/?token={info.token}"
        assert was_serving is True, "the browser was opened before the server was answering"

        # The SPA is served on that port.
        with urllib.request.urlopen(f"http://127.0.0.1:{info.port}/", timeout=5) as resp:
            assert resp.status == 200

        # And the stop path actually ends the process's server loop.
        _stop_via_api(info)
    finally:
        thread.join(timeout=20)

    assert not thread.is_alive(), "serve() did not return after POST /api/stop"
    assert result == [0]
    assert lock.read() is None, "the lock file must be released on shutdown"


def _stop_via_api(info: il.InstanceInfo) -> None:
    base = f"http://127.0.0.1:{info.port}"
    exchange = urllib.request.Request(
        f"{base}/api/auth/exchange",
        data=json.dumps({"token": info.token}).encode(),
        headers={"Content-Type": "application/json", "Origin": base},
        method="POST",
    )
    with urllib.request.urlopen(exchange, timeout=5) as resp:
        assert resp.status == 200
        cookie = resp.headers["set-cookie"].split(";")[0]

    stop = urllib.request.Request(
        f"{base}/api/stop",
        data=b"",
        headers={"Origin": base, "Cookie": cookie, "Content-Length": "0"},
        method="POST",
    )
    with urllib.request.urlopen(stop, timeout=5) as resp:
        assert resp.status == 202


def test_main_opens_the_running_instance_instead_of_starting_a_second_one(launcher, monkeypatch) -> None:
    """The product spec: a second launch must not bind, scan, or error."""
    opened: list[str] = []
    monkeypatch.setattr(launcher, "open_url", lambda url: opened.append(url))

    live = il.InstanceInfo(pid=1234, port=51515, token="existing-token")
    monkeypatch.setattr(il.InstanceLock, "running_instance", lambda self: live)

    def explode(**kwargs):
        raise AssertionError("a second launch must not start a server")

    monkeypatch.setattr(launcher, "serve", explode)

    assert launcher.main([]) == 0
    assert opened == ["http://127.0.0.1:51515/?token=existing-token"]


def test_main_starts_serving_when_no_live_instance_exists(launcher, monkeypatch) -> None:
    monkeypatch.setattr(il.InstanceLock, "running_instance", lambda self: None)
    calls: list[bool] = []
    monkeypatch.setattr(launcher, "serve", lambda **kwargs: calls.append(kwargs["open_browser"]) or 0)

    assert launcher.main([]) == 0
    assert calls == [True]


def test_no_browser_flag_suppresses_opening_a_tab(launcher, monkeypatch) -> None:
    """Needed by the clean-environment proxy run: process counts can only
    be measured if launching does not spray browser tabs."""
    monkeypatch.setattr(il.InstanceLock, "running_instance", lambda self: None)
    calls: list[bool] = []
    monkeypatch.setattr(launcher, "serve", lambda **kwargs: calls.append(kwargs["open_browser"]) or 0)

    assert launcher.main(["--no-browser"]) == 0
    assert calls == [False]


def test_second_launch_with_no_browser_still_exits_zero(launcher, monkeypatch) -> None:
    live = il.InstanceInfo(pid=1234, port=51515, token="tok")
    monkeypatch.setattr(il.InstanceLock, "running_instance", lambda self: live)
    opened: list[str] = []
    monkeypatch.setattr(launcher, "open_url", lambda url: opened.append(url))
    monkeypatch.setattr(launcher, "serve", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not serve")))

    assert launcher.main(["--no-browser"]) == 0
    assert opened == []


def test_module_entrypoint_is_importable_without_package_context() -> None:
    """``python -m jobfinder`` and the PyInstaller entry script both load
    ``jobfinder/__main__.py``; PyInstaller runs it as ``__main__`` with no
    package, so it must not use relative imports.
    """
    from pathlib import Path

    import jobfinder

    source = (Path(jobfinder.__file__).parent / "__main__.py").read_text(encoding="utf-8")
    import_lines = [line.strip() for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert import_lines, "no imports found — did the file move?"
    assert not [line for line in import_lines if line.startswith("from .")], import_lines
    assert "from jobfinder.launcher import main" in import_lines


def test_command_line_flags_are_not_discarded(launcher, monkeypatch) -> None:
    """``python -m jobfinder --no-browser`` must honour the flag.

    ``_parse_args`` used to pass ``[]`` whenever ``argv`` was None, so
    argparse never looked at ``sys.argv`` and every flag typed on the real
    command line was silently dropped: the documented headless path opened
    a browser tab and printed nothing. Tests still pass explicit lists, so
    they never saw it.
    """
    monkeypatch.setattr(sys, "argv", ["jobfinder", "--no-browser"])
    assert launcher._parse_args(None).no_browser is True  # noqa: SLF001 — CLI contract

    monkeypatch.setattr(sys, "argv", ["jobfinder"])
    assert launcher._parse_args(None).no_browser is False  # noqa: SLF001

    # An explicit list still wins, which is what every other test relies on.
    assert launcher._parse_args([]).no_browser is False  # noqa: SLF001
