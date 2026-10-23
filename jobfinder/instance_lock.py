"""Single-instance lock for the launcher (T34).

the product spec requires two things that are really one mechanism:

  - "Starting a second instance must not fail on a port collision — it
    opens the existing instance instead."
  - "A single-instance lock prevents two schedulers."

The second launch has to *open* the first instance, and every route but
``/api/health`` and ``/api/auth/exchange`` needs a session cookie
(``jobfinder/web/security.py``). The cookie comes from exchanging the
per-launch token, which exists only in the first process's memory. So the
lock file cannot just be a PID marker: it has to carry the **port and the
launch token** of the running instance, or the second launch can only open
a URL that immediately 401s.

That makes this a secret-bearing file. Two consequences:

  1. It lives in the data dir (``paths.instance_lock_path()``), never in
     the repo or a world-readable temp directory.
  2. It is created with owner-only permissions. On POSIX that is a literal
     ``0o600`` passed to ``os.open``. On Windows, POSIX mode bits are not
     how access control is expressed — ``os.chmod`` there only toggles the
     read-only attribute and cannot restrict *other users* at all. The
     protection on Windows is inherited from the location instead: the
     default data root is ``platformdirs.user_data_dir("JobFinder")``,
     i.e. under ``%LOCALAPPDATA%``, whose ACL already grants access to the
     owning user, SYSTEM and Administrators only. We do not hand-roll an
     ACL with ``pywin32``/``ctypes`` here: that would add a dependency and
     a large new failure surface to re-derive a restriction the directory
     already provides. A user who points ``JOBFINDER_DATA_DIR`` at a
     shared location is choosing a weaker location explicitly.

**Staleness is the failure mode that matters.** If the app crashed, was
killed, or lost power, a leftover lock must not brick every future launch.
Liveness is therefore checked with two independent signals:

  - ``process_alive(pid)`` — necessary, but *not* sufficient: operating
    systems reuse PIDs, so some unrelated process can inherit the recorded
    PID and keep a dead lock looking alive forever.
  - ``health_responds(port)`` — the recorded port must actually answer
    ``GET /api/health`` with JobFinder's own payload. ``/api/health`` is
    deliberately in ``security.PUBLIC_PATHS``, so this probe needs no
    cookie.

Only when both agree is the lock treated as a running instance. Anything
else — missing file, truncated/corrupt JSON, dead PID, silent port — is a
stale lock that the next launch takes over.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("jobfinder.instance_lock")

# Keep this well under a human's patience for a double-click: the probe is
# a loopback round-trip against a process that is either answering
# immediately or not there at all.
HEALTH_TIMEOUT_SECONDS = 1.5


@dataclass(frozen=True)
class InstanceInfo:
    """The running instance's coordinates, as recorded in the lock file."""

    pid: int
    port: int
    token: str


def instance_url(info: InstanceInfo) -> str:
    """The URL that opens the running instance *authenticated*.

    The query parameter is ``token``, which is what the SPA actually reads
    (``bootstrapAuth()`` in ``frontend/src/api/client.ts`` calls
    ``params.get("token")`` and then strips it with ``history.replaceState``).
    the task ledger row writes ``?t=``; that shorthand would be silently
    ignored by the shipped bundle and every request after load would 401.
    """
    return f"http://127.0.0.1:{info.port}/?token={info.token}"


def process_alive(pid: int) -> bool:
    """Whether a process with this PID currently exists.

    Not a JobFinder-identity check — see the module docstring on PID reuse.

    ``os.kill(pid, 0)`` is the POSIX idiom but is **not** portable here: on
    Windows, ``os.kill`` with any signal other than ``CTRL_C_EVENT`` /
    ``CTRL_BREAK_EVENT`` calls ``TerminateProcess``, so the usual
    "signal 0 probe" would *kill* the process it was supposed to merely
    observe. Windows goes through ``OpenProcess`` +
    ``GetExitCodeProcess`` instead.
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Either no such process, or one we may not query. A process we
            # cannot query is not one we can treat as "our running
            # instance" either, so False is the right (fail-open-to-
            # takeover) answer for both.
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else. Same reasoning as the Windows
        # unqueryable case: not something we can adopt as our instance.
        return False
    return True


def health_responds(port: int, timeout: float | None = None) -> bool:
    """Whether ``127.0.0.1:<port>`` is a JobFinder answering ``/api/health``.

    This is the half of the liveness test that survives PID reuse. It is
    also why the identity assertion is on the *response body* rather than
    on "something accepted a TCP connection": any process can hold a port.
    """
    if port <= 0:
        return False
    deadline = HEALTH_TIMEOUT_SECONDS if timeout is None else timeout
    url = f"http://127.0.0.1:{port}/api/health"
    request = urllib.request.Request(url, method="GET", headers={"Host": f"127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(request, timeout=deadline) as response:  # noqa: S310 - fixed loopback URL
            if response.status != 200:
                return False
            payload = json.loads(response.read(4096).decode("utf-8"))
    except (urllib.error.URLError, OSError, socket.timeout, ValueError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


class InstanceLock:
    """Owner of the lock file at ``path``.

    Deliberately *not* an OS-level advisory file lock (``fcntl.flock`` /
    ``msvcrt.locking``): the second launch needs to **read the contents**
    of a lock the first process still holds, and the point of the file is
    to transfer the port and token, not to block. A held exclusive lock
    would make the handoff harder, not safer, and would still need the
    stale-PID recovery below after a hard kill (an advisory lock is
    released by the OS on exit, but a lock *file* is not).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # ── Reading ──────────────────────────────────────────────────────────

    def read(self) -> InstanceInfo | None:
        """Parse the lock file, or ``None`` if it is absent or unusable.

        A corrupt lock is indistinguishable from no lock for our purposes
        (both mean "we cannot locate a running instance"), and must never
        raise: a truncated write from a power loss would otherwise make the
        app permanently unlaunchable.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("Could not read instance lock %s (%s); treating as absent.", self.path, exc)
            return None

        try:
            payload = json.loads(raw)
            return InstanceInfo(pid=int(payload["pid"]), port=int(payload["port"]), token=str(payload["token"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Instance lock %s is corrupt (%s); treating as stale.", self.path, exc)
            return None

    def running_instance(self) -> InstanceInfo | None:
        """The live instance described by the lock file, or ``None``.

        ``None`` covers every stale case, so the caller's logic is simply
        "if this returns something, open it; otherwise start serving".
        """
        info = self.read()
        if info is None:
            return None
        if info.pid == os.getpid():
            # Our own PID in a lock file we did not write this run: a
            # recycled PID from a previous boot. Not a running instance.
            return None
        if not process_alive(info.pid):
            logger.info("Instance lock names PID %d, which is not running — taking over.", info.pid)
            return None
        if not health_responds(info.port):
            logger.info(
                "PID %d is alive but port %d does not answer /api/health — stale lock (PID reuse), taking over.",
                info.pid,
                info.port,
            )
            return None
        return info

    # ── Writing ──────────────────────────────────────────────────────────

    def acquire(self, *, port: int, token: str) -> None:
        """Record this process as the running instance.

        Unconditional overwrite by design: the caller only reaches this
        after ``running_instance()`` returned ``None``, i.e. any existing
        lock is already known to be stale. Written to a temp file in the
        same directory and ``os.replace``d into place so a reader never
        sees a half-written lock.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "port": port, "token": token})
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        # 0o600 at creation time rather than a chmod afterwards: a chmod
        # leaves a window in which the token is on disk world-readable.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self.path)
        logger.info("Instance lock acquired: %s (pid %d, port %d).", self.path, os.getpid(), port)

    def release(self) -> None:
        """Remove the lock, but only if it is still ours.

        The PID check stops a slow-exiting process from deleting the lock a
        *newer* instance has already taken over — otherwise a third launch
        would see no lock and start a second scheduler.
        """
        info = self.read()
        if info is None:
            return
        if info.pid != os.getpid():
            logger.info("Instance lock now belongs to pid %d, not us — leaving it alone.", info.pid)
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove instance lock %s (%s).", self.path, exc)
