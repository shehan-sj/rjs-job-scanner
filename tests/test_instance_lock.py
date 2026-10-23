"""Tests for jobfinder.instance_lock (T34) — single-instance lock.

the product spec: "Starting a second instance must not fail on a port collision
— it opens the existing instance instead. A single-instance lock prevents
two schedulers."

The lock file is the *only* channel through which a second launch learns
where the first one is listening and how to authenticate to it, so it
carries both the port and the per-launch token (jobfinder/web/security.py's
``LaunchToken``). That makes it a secret-bearing file: these tests pin down
that it lives under the data dir (never the repo), that it is created with
owner-only permissions where the platform can express that, and — most
importantly — that a lock left behind by a crashed process does not brick
every future launch.

Liveness is deliberately two-signal (PID *and* a health probe): PIDs are
reused by the OS, so a live PID alone would make an unrelated process
masquerade as a running JobFinder forever.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from jobfinder import instance_lock as il


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "instance.lock"


def _force_liveness(monkeypatch, *, pid_alive: bool, health_ok: bool) -> None:
    monkeypatch.setattr(il, "process_alive", lambda pid: pid_alive)
    monkeypatch.setattr(il, "health_responds", lambda port, timeout=None: health_ok)


def test_lock_path_is_inside_the_data_dir_not_the_repo(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "data-root"))
    import importlib

    from jobfinder import paths

    importlib.reload(paths)
    try:
        path = paths.instance_lock_path()
        assert path.parent == paths.get_data_dir()
        assert str(path).startswith(str(tmp_path))
    finally:
        importlib.reload(paths)


def test_acquire_records_pid_port_and_token(lock_path) -> None:
    lock = il.InstanceLock(lock_path)
    lock.acquire(port=54321, token="launch-token-value")

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["port"] == 54321
    assert payload["token"] == "launch-token-value"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits are not how Windows expresses an ACL")
def test_acquire_creates_an_owner_only_file(lock_path) -> None:
    il.InstanceLock(lock_path).acquire(port=1, token="secret")
    assert (lock_path.stat().st_mode & 0o777) == 0o600


def test_running_instance_returns_none_when_no_lock_file_exists(lock_path) -> None:
    assert il.InstanceLock(lock_path).running_instance() is None


def test_running_instance_returns_none_for_a_corrupt_lock_file(lock_path, monkeypatch) -> None:
    lock_path.write_text("this is not json", encoding="utf-8")
    _force_liveness(monkeypatch, pid_alive=True, health_ok=True)
    assert il.InstanceLock(lock_path).running_instance() is None


def test_running_instance_reports_a_live_instance(lock_path, monkeypatch) -> None:
    lock_path.write_text(json.dumps({"pid": 4242, "port": 9999, "token": "tok"}), encoding="utf-8")
    _force_liveness(monkeypatch, pid_alive=True, health_ok=True)

    info = il.InstanceLock(lock_path).running_instance()
    assert info is not None
    assert (info.pid, info.port, info.token) == (4242, 9999, "tok")


def test_a_dead_pid_is_a_stale_lock_not_a_running_instance(lock_path, monkeypatch) -> None:
    lock_path.write_text(json.dumps({"pid": 4242, "port": 9999, "token": "tok"}), encoding="utf-8")
    _force_liveness(monkeypatch, pid_alive=False, health_ok=False)
    assert il.InstanceLock(lock_path).running_instance() is None


def test_a_live_pid_whose_port_is_silent_is_stale_too(lock_path, monkeypatch) -> None:
    """PID reuse: the recorded PID exists again as something unrelated."""
    lock_path.write_text(json.dumps({"pid": 4242, "port": 9999, "token": "tok"}), encoding="utf-8")
    _force_liveness(monkeypatch, pid_alive=True, health_ok=False)
    assert il.InstanceLock(lock_path).running_instance() is None


def test_acquire_takes_over_a_stale_lock(lock_path, monkeypatch) -> None:
    lock_path.write_text(json.dumps({"pid": 4242, "port": 9999, "token": "old"}), encoding="utf-8")
    _force_liveness(monkeypatch, pid_alive=False, health_ok=False)

    lock = il.InstanceLock(lock_path)
    lock.acquire(port=1234, token="new")

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["token"] == "new"


def test_release_removes_only_our_own_lock(lock_path, monkeypatch) -> None:
    lock = il.InstanceLock(lock_path)
    lock.acquire(port=1, token="ours")
    lock.release()
    assert not lock_path.exists()

    lock_path.write_text(json.dumps({"pid": os.getpid() + 1, "port": 2, "token": "theirs"}), encoding="utf-8")
    il.InstanceLock(lock_path).release()
    assert lock_path.exists(), "a lock owned by another PID must survive our release()"


def test_release_is_quiet_when_there_is_nothing_to_release(lock_path) -> None:
    il.InstanceLock(lock_path).release()  # must not raise


def test_instance_url_carries_the_token_the_spa_actually_reads(lock_path) -> None:
    info = il.InstanceInfo(pid=1, port=8756, token="abc")
    # frontend/src/api/client.ts reads the `token` query param — not `t`.
    assert il.instance_url(info) == "http://127.0.0.1:8756/?token=abc"


def test_process_alive_is_true_for_this_process_and_false_for_an_impossible_pid() -> None:
    assert il.process_alive(os.getpid()) is True
    assert il.process_alive(0) is False


def test_health_responds_is_false_for_a_closed_port() -> None:
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert il.health_responds(port, timeout=0.5) is False
