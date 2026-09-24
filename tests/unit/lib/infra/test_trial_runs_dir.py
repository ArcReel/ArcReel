"""测试连接产物目录的解析与一次性迁移。

判据是位置与副作用：目录必须落在项目根之外（否则会被 project 枚举当成伪项目），
迁移必须清掉旧位置、且不碰手工放进来的 project.json。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from lib.infra import app_data_dir as app_data_dir_mod
from lib.infra import trial_runs_dir as trial_runs_dir_mod


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把 app_data_dir() 与 PROJECT_ROOT 都钉到 tmp_path 下的独立子目录。

    使新旧路径分别落在 tmp_path/data/trial_runs（旧）与 tmp_path/root/trial_runs（新）。
    """
    data_root = tmp_path / "data"
    project_root = tmp_path / "root"
    data_root.mkdir()
    project_root.mkdir()
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
    monkeypatch.setattr(trial_runs_dir_mod, "PROJECT_ROOT", project_root)
    app_data_dir_mod.reset_for_tests()
    yield tmp_path
    app_data_dir_mod.reset_for_tests()


def test_resolve_default_is_project_root(isolated_data_dir: Path) -> None:
    assert trial_runs_dir_mod.resolve_trial_runs_dir() == isolated_data_dir / "root" / "trial_runs"


def test_resolve_is_outside_app_data_dir(isolated_data_dir: Path) -> None:
    """产物目录不得落在数据根内——数据根同时是 projects_root，无前缀的兄弟目录
    会被 project 枚举当作项目暴露给前端（本票要消除的伪项目来源）。"""
    resolved = trial_runs_dir_mod.resolve_trial_runs_dir()
    assert not resolved.is_relative_to(app_data_dir_mod.app_data_dir())


def test_legacy_points_to_app_data(isolated_data_dir: Path) -> None:
    assert trial_runs_dir_mod.legacy_trial_runs_dir() == (isolated_data_dir / "data" / "trial_runs").resolve()


def test_migrate_removes_legacy_dir(isolated_data_dir: Path) -> None:
    old_dir = isolated_data_dir / "data" / "trial_runs"
    (old_dir / "017c7b84").mkdir(parents=True)
    (old_dir / "017c7b84" / "result.json").write_text("{}", encoding="utf-8")

    trial_runs_dir_mod.migrate_legacy_trial_runs_dir()

    assert not old_dir.exists()


def test_migrate_keeps_legacy_dir_containing_project_json(
    isolated_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """含 project.json 只可能是手工放置的项目目录，不静默删除，只告警。"""
    old_dir = isolated_data_dir / "data" / "trial_runs"
    old_dir.mkdir(parents=True)
    (old_dir / "project.json").write_text("{}", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        trial_runs_dir_mod.migrate_legacy_trial_runs_dir()

    assert (old_dir / "project.json").exists()
    assert "contains project.json" in caplog.text


def test_migrate_noop_when_legacy_absent(isolated_data_dir: Path) -> None:
    trial_runs_dir_mod.migrate_legacy_trial_runs_dir()  # 不抛
    assert not (isolated_data_dir / "root" / "trial_runs").exists()


def test_migrate_noop_when_paths_equal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ARCREEL_DATA_DIR == PROJECT_ROOT 时新旧路径解析到同一处，不要删掉正在用的目录。"""
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(trial_runs_dir_mod, "PROJECT_ROOT", tmp_path)
    app_data_dir_mod.reset_for_tests()
    try:
        trial_runs = tmp_path / "trial_runs"
        trial_runs.mkdir()
        (trial_runs / "017c7b84").mkdir()

        trial_runs_dir_mod.migrate_legacy_trial_runs_dir()  # 不抛

        assert (trial_runs / "017c7b84").exists()
    finally:
        app_data_dir_mod.reset_for_tests()


def test_migrate_silent_when_removed_by_another_process(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """并发启动的另一个进程先删掉了：删除目的已达成，不算失败、不该告警。"""
    old_dir = isolated_data_dir / "data" / "trial_runs"
    old_dir.mkdir(parents=True)

    def racing_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", str(path))

    monkeypatch.setattr(trial_runs_dir_mod.shutil, "rmtree", racing_rmtree)

    with caplog.at_level(logging.WARNING):
        trial_runs_dir_mod.migrate_legacy_trial_runs_dir()  # 不抛

    assert "FAILED" not in caplog.text


def test_migrate_survives_rmtree_failure(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """删除失败只告警不阻断启动；目录仍在，运维需要看到这条提示。"""
    old_dir = isolated_data_dir / "data" / "trial_runs"
    old_dir.mkdir(parents=True)

    def failing_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(trial_runs_dir_mod.shutil, "rmtree", failing_rmtree)

    with caplog.at_level(logging.WARNING):
        trial_runs_dir_mod.migrate_legacy_trial_runs_dir()  # 不抛

    assert old_dir.exists()
    assert "cleanup FAILED" in caplog.text
