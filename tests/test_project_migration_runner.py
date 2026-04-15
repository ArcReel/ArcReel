"""迁移 runner：版本检测、幂等、错误隔离、备份清理。"""

import json
import time
from pathlib import Path

import pytest

from lib.project_migrations.runner import (
    CURRENT_SCHEMA_VERSION,
    cleanup_stale_backups,
    run_project_migrations,
)


@pytest.fixture
def tmp_projects(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def _write_project(root: Path, name: str, data: dict) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False))
    return d


def test_skip_already_current(tmp_projects: Path):
    _write_project(tmp_projects, "p1", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "p1"})
    summary = run_project_migrations(tmp_projects)
    assert summary.migrated == []
    assert summary.skipped == ["p1"]


def test_migrate_v0_bumps_version(tmp_projects: Path, monkeypatch):
    _write_project(tmp_projects, "p1", {"name": "p1"})  # 无 schema_version

    called = {}

    def fake_migrate_v0_to_v1(project_dir: Path) -> None:
        called["p1"] = True
        data = json.loads((project_dir / "project.json").read_text())
        data["schema_version"] = 1
        (project_dir / "project.json").write_text(json.dumps(data))

    monkeypatch.setattr(
        "lib.project_migrations.runner.MIGRATORS",
        {0: fake_migrate_v0_to_v1},
    )

    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    assert called == {"p1": True}
    data = json.loads((tmp_projects / "p1" / "project.json").read_text())
    assert data["schema_version"] == 1


def test_skip_underscore_dirs(tmp_projects: Path):
    (tmp_projects / "_global_assets").mkdir()
    (tmp_projects / "_global_assets" / "keep.txt").write_text("x")
    _write_project(tmp_projects, "p1", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "p1"})
    summary = run_project_migrations(tmp_projects)
    assert "_global_assets" not in summary.skipped
    assert "_global_assets" not in summary.migrated


def test_error_isolated_not_abort(tmp_projects: Path, monkeypatch):
    _write_project(tmp_projects, "broken", {"name": "broken"})
    _write_project(tmp_projects, "ok", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "ok"})

    def bad(_d):
        raise RuntimeError("boom")

    monkeypatch.setattr("lib.project_migrations.runner.MIGRATORS", {0: bad})
    summary = run_project_migrations(tmp_projects)
    assert "broken" in summary.failed
    assert "ok" in summary.skipped


def test_cleanup_old_backups(tmp_projects: Path):
    p = _write_project(tmp_projects, "p1", {"schema_version": 1})
    old = p / "project.json.bak.v0-100000000"
    new = p / "project.json.bak.v0-9999999999"
    old.write_text("old")
    new.write_text("new")

    # mtime 控制：old 文件 mtime 设为 8 天前
    eight_days_ago = time.time() - 8 * 86400
    import os

    os.utime(old, (eight_days_ago, eight_days_ago))

    cleanup_stale_backups(tmp_projects, max_age_days=7)
    assert not old.exists()
    assert new.exists()
