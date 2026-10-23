"""Tests for jobfinder.paths (T12).

The bug this module fixes: every path in the app used to be a relative-path
literal resolved against the process's current working directory, and the
frozen build's startup hook used to change that working directory to make
things line up. With that hook no longer firing (T33 deleted the Qt
PyInstaller build, and a web server has no reason to chdir at all, so T34's
build will not reintroduce one), the app would silently
read and write a different database depending on which directory the
process happened to be launched from.

These tests assert working-directory independence: resolving `db_path()`
(and friends) from two different cwds, with `JOBFINDER_DATA_DIR` pinned,
must yield the identical absolute path.
"""

from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "jobfinder-data"
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(data_dir))
    import jobfinder.paths as paths

    importlib.reload(paths)
    yield paths, data_dir
    importlib.reload(paths)


def test_db_path_is_cwd_independent(isolated_data_dir, monkeypatch, tmp_path):
    paths, data_dir = isolated_data_dir

    cwd_a = tmp_path / "cwd-a"
    cwd_b = tmp_path / "cwd-b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.chdir(cwd_a)
    path_from_a = paths.db_path()

    monkeypatch.chdir(cwd_b)
    path_from_b = paths.db_path()

    assert path_from_a == path_from_b
    assert path_from_a.is_absolute()
    assert path_from_a == data_dir / "data" / "jobfinder.db"


def test_config_path_and_logs_dir_are_cwd_independent(isolated_data_dir, monkeypatch, tmp_path):
    paths, data_dir = isolated_data_dir

    monkeypatch.chdir(tmp_path)
    cfg_before = paths.config_path()
    logs_before = paths.logs_dir()

    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    cfg_after = paths.config_path()
    logs_after = paths.logs_dir()

    assert cfg_before == cfg_after == data_dir / "config.yaml"
    assert logs_before == logs_after == data_dir / "logs"


def test_env_override_takes_precedence_over_platformdirs(monkeypatch, tmp_path):
    custom = tmp_path / "custom-data-dir"
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(custom))
    import jobfinder.paths as paths

    importlib.reload(paths)
    try:
        assert paths.get_data_dir() == custom
    finally:
        importlib.reload(paths)


def test_no_chdir_calls_in_module_source():
    """Guards the root-cause fix: this module must never call os.chdir."""
    import jobfinder.paths as paths

    source_path = paths.__file__
    with open(source_path, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert "os.chdir(" not in source


def test_setup_data_environment_creates_dirs_without_chdir(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBFINDER_DATA_DIR", str(tmp_path / "fresh"))
    import jobfinder.paths as paths

    importlib.reload(paths)
    try:
        cwd_before = os.getcwd()
        paths.setup_data_environment()
        assert os.getcwd() == cwd_before

        data_dir = paths.get_data_dir()
        assert data_dir.exists()
        assert (data_dir / "data").exists()
        assert (data_dir / "logs").exists()
        assert (data_dir / "browsers").exists()
        assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(data_dir / "browsers")
    finally:
        importlib.reload(paths)


def test_migration_copies_legacy_config_without_env_override(tmp_path, monkeypatch):
    """Simulates an existing dev install: a config.yaml sitting at the
    legacy location (Path.cwd() when not frozen) gets copied into the new
    explicit data root on first run, and the old file is left untouched.
    """
    monkeypatch.delenv("JOBFINDER_DATA_DIR", raising=False)

    legacy_cwd = tmp_path / "legacy-install"
    legacy_cwd.mkdir()
    (legacy_cwd / "config.yaml").write_text("match_threshold: 70\n", encoding="utf-8")
    monkeypatch.chdir(legacy_cwd)

    new_data_dir = tmp_path / "new-platformdirs-location"
    monkeypatch.setattr(
        "jobfinder.paths.platformdirs.user_data_dir",
        lambda *a, **k: str(new_data_dir),
    )

    import jobfinder.paths as paths

    paths.setup_data_environment()

    migrated_config = new_data_dir / "config.yaml"
    assert migrated_config.exists()
    assert "match_threshold: 70" in migrated_config.read_text(encoding="utf-8")
    # Original, legacy file is left in place — not deleted.
    assert (legacy_cwd / "config.yaml").exists()
