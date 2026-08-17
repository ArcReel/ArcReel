"""v5→v6 目录交换的崩溃窗口认领、磁盘预检与中间目录清理。"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest

from lib.project_migrations.runner import (
    CURRENT_SCHEMA_VERSION,
    cleanup_stale_backups,
    run_project_migrations,
)
from lib.project_migrations.staged_swap import (
    MigrationDiskSpaceError,
    reclaim_interrupted_swaps,
    rollback_project_name,
    staging_project_name,
)
from lib.project_migrations.v5_to_v6_asset_namespace import migrate_v5_to_v6

pytestmark = pytest.mark.unit

_EIGHT_DAYS = 8 * 86400


@pytest.fixture
def tmp_projects(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def _write_project(root: Path, name: str, data: dict) -> Path:
    project_dir = root / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return project_dir


def _v5_project(root: Path, name: str) -> Path:
    return _write_project(
        root,
        name,
        {
            "schema_version": 5,
            "characters": {"Hero": {"description": "c", "character_sheet": ""}},
            "scenes": {},
            "props": {},
            "products": {},
        },
    )


def _swap_dir(root: Path, project_name: str, *, rollback: bool) -> Path:
    infix = "v6-rollback-" if rollback else "v6-"
    return root / f".{project_name}.{infix}{uuid.uuid4().hex}"


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_startup_reclaims_rollback_dir_when_project_dir_missing(tmp_projects: Path) -> None:
    """交换窗口内被硬杀：项目目录缺失、rollback 目录残留，启动期扫描把它改回。"""
    project_dir = _write_project(tmp_projects, "demo", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "demo"})
    (project_dir / "keep.txt").write_text("payload", encoding="utf-8")
    rollback = _swap_dir(tmp_projects, "demo", rollback=True)
    os.replace(project_dir, rollback)
    assert not project_dir.exists()

    summary = run_project_migrations(tmp_projects)

    assert project_dir.is_dir()
    assert (project_dir / "keep.txt").read_text(encoding="utf-8") == "payload"
    assert not rollback.exists()
    assert summary.skipped == ["demo"]


def test_reclaimed_project_continues_migrating_in_the_same_run(tmp_projects: Path) -> None:
    project_dir = _v5_project(tmp_projects, "demo")
    rollback = _swap_dir(tmp_projects, "demo", rollback=True)
    os.replace(project_dir, rollback)

    summary = run_project_migrations(tmp_projects)

    assert summary.migrated == ["demo"]
    payload = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == CURRENT_SCHEMA_VERSION


def test_reclaim_keeps_rollback_dir_when_project_dir_present(tmp_projects: Path) -> None:
    """项目目录在位说明交换已完成，rollback 目录是待清理备份而非恢复源。"""
    _write_project(tmp_projects, "demo", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "demo"})
    rollback = _swap_dir(tmp_projects, "demo", rollback=True)
    rollback.mkdir()

    assert reclaim_interrupted_swaps(tmp_projects) == []
    assert rollback.is_dir()


def test_reclaim_prefers_the_newest_rollback_dir(tmp_projects: Path) -> None:
    older = _swap_dir(tmp_projects, "demo", rollback=True)
    older.mkdir()
    (older / "marker.txt").write_text("older", encoding="utf-8")
    _age(older, _EIGHT_DAYS)
    newer = _swap_dir(tmp_projects, "demo", rollback=True)
    newer.mkdir()
    (newer / "marker.txt").write_text("newer", encoding="utf-8")

    assert reclaim_interrupted_swaps(tmp_projects) == ["demo"]
    assert (tmp_projects / "demo" / "marker.txt").read_text(encoding="utf-8") == "newer"
    assert older.is_dir()


def test_reclaim_ignores_dirs_outside_the_naming_convention(tmp_projects: Path) -> None:
    for name in (".demo.v6-rollback-not-hex", ".v6-rollback-abc123", "demo.v6-rollback-abc123", ".demo.v6-deadbeef"):
        (tmp_projects / name).mkdir()

    assert reclaim_interrupted_swaps(tmp_projects) == []
    assert not (tmp_projects / "demo").exists()


def test_swap_dir_names_round_trip_project_names_containing_dots() -> None:
    assert rollback_project_name(".my.show.v6-rollback-" + "a" * 32) == "my.show"
    assert staging_project_name(".my.show.v6-" + "b" * 32) == "my.show"
    assert staging_project_name(".my.show.v6-rollback-" + "a" * 32) is None
    assert rollback_project_name(".my.show.v6-" + "b" * 32) is None


def test_migration_fails_readably_when_disk_headroom_is_insufficient(tmp_projects: Path, monkeypatch) -> None:
    project_dir = _v5_project(tmp_projects, "demo")
    original = (project_dir / "project.json").read_bytes()

    class _Usage:
        total = 1 << 40
        used = 1 << 40
        free = 1024

    monkeypatch.setattr("lib.project_migrations.staged_swap.shutil.disk_usage", lambda _path: _Usage())

    with pytest.raises(MigrationDiskSpaceError) as excinfo:
        migrate_v5_to_v6(project_dir)

    message = str(excinfo.value)
    assert "demo" in message
    assert "磁盘空间不足" in message
    assert (project_dir / "project.json").read_bytes() == original
    assert not list(tmp_projects.glob(".demo.v6-*"))


def test_startup_isolates_disk_headroom_failure_to_one_project(tmp_projects: Path, monkeypatch) -> None:
    _v5_project(tmp_projects, "demo")
    _write_project(tmp_projects, "other", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "other"})

    class _Usage:
        total = 1 << 40
        used = 1 << 40
        free = 1024

    monkeypatch.setattr("lib.project_migrations.staged_swap.shutil.disk_usage", lambda _path: _Usage())

    summary = run_project_migrations(tmp_projects)

    assert summary.failed == ["demo"]
    assert summary.skipped == ["other"]
    assert json.loads((tmp_projects / "demo" / "project.json").read_text(encoding="utf-8"))["schema_version"] == 5


def test_cleanup_removes_stale_swap_dirs_of_completed_migrations(tmp_projects: Path) -> None:
    _write_project(tmp_projects, "demo", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "demo"})
    stale_rollback = _swap_dir(tmp_projects, "demo", rollback=True)
    stale_rollback.mkdir()
    (stale_rollback / "payload.bin").write_bytes(b"x")
    stale_staging = _swap_dir(tmp_projects, "demo", rollback=False)
    stale_staging.mkdir()
    fresh_rollback = _swap_dir(tmp_projects, "demo", rollback=True)
    fresh_rollback.mkdir()
    _age(stale_rollback, _EIGHT_DAYS)
    _age(stale_staging, _EIGHT_DAYS)

    cleanup_stale_backups(tmp_projects, max_age_days=7)

    assert not stale_rollback.exists()
    assert not stale_staging.exists()
    assert fresh_rollback.is_dir()
    assert (tmp_projects / "demo" / "project.json").is_file()


def test_cleanup_keeps_rollback_dir_while_project_dir_is_missing(tmp_projects: Path) -> None:
    """认领尚未发生时 rollback 目录是唯一的项目数据，年龄再老也不能删。"""
    rollback = _swap_dir(tmp_projects, "demo", rollback=True)
    rollback.mkdir()
    _age(rollback, _EIGHT_DAYS)

    cleanup_stale_backups(tmp_projects, max_age_days=7)

    assert rollback.is_dir()
