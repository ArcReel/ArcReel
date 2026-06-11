"""迁移 runner：版本检测、幂等、错误隔离、备份清理。"""

import json
import time
from pathlib import Path

import pytest

from lib.project_migrations.runner import (
    CURRENT_SCHEMA_VERSION,
    cleanup_stale_backups,
    migrate_project_dir,
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
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def test_skip_already_current(tmp_projects: Path):
    _write_project(tmp_projects, "p1", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "p1"})
    summary = run_project_migrations(tmp_projects)
    assert summary.migrated == []
    assert summary.skipped == ["p1"]


def test_migrate_bumps_through_all_versions(tmp_projects: Path, monkeypatch):
    """runner 逐级跑到 CURRENT_SCHEMA_VERSION（此处 v0→v1→v2→v3）。"""
    _write_project(tmp_projects, "p1", {"name": "p1"})  # 无 schema_version

    called: list[int] = []

    def _fake(from_version: int):
        def migrator(project_dir: Path) -> None:
            called.append(from_version)
            data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            data["schema_version"] = from_version + 1
            (project_dir / "project.json").write_text(json.dumps(data), encoding="utf-8")

        return migrator

    monkeypatch.setattr(
        "lib.project_migrations.runner.MIGRATORS",
        {v: _fake(v) for v in range(CURRENT_SCHEMA_VERSION)},
    )

    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    assert called == list(range(CURRENT_SCHEMA_VERSION))
    data = json.loads((tmp_projects / "p1" / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION


def test_real_v1_to_v2_normalizes_via_runner(tmp_projects: Path):
    """用真实 MIGRATORS：v1 项目经 runner 归一化 legacy provider 名并升到最新版本。"""
    _write_project(
        tmp_projects,
        "p1",
        {"schema_version": 1, "video_backend": "seedance/x", "image_backend": "vertex/y"},
    )
    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    data = json.loads((tmp_projects / "p1" / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION
    assert data["video_backend"] == "ark/x"
    assert data["image_provider_t2i"] == "gemini-vertex/y"
    assert "image_backend" not in data


def test_real_v2_to_v3_backfills_ledger_via_runner(tmp_projects: Path):
    """用真实 MIGRATORS：v2 项目经 runner 回填分集账本并产生版本化备份。"""
    novel = "第一集的正文内容。第二集还没拆出来的余文。"
    p = _write_project(
        tmp_projects,
        "p1",
        {
            "schema_version": 2,
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    source = p / "source"
    source.mkdir()
    (source / "novel.txt").write_text(novel, encoding="utf-8")
    (source / "episode_1.txt").write_text("第一集的正文内容。", encoding="utf-8")
    (source / "_remaining.txt").write_text("第二集还没拆出来的余文。", encoding="utf-8")

    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    data = json.loads((p / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION
    # 回填语义细节由 test_project_migration_v2_v3 / test_episode_ledger 专测，
    # 此处只验证迁移器经 MIGRATORS 注册生效与 runner 外围行为
    assert data["episodes"][0]["ledger_status"] == "planned"
    assert data["planning_cursor"] is not None
    assert (source / "_remaining.txt").exists()  # 余文保留，旧拆分流程不受影响
    assert list(p.glob("project.json.bak.v2-*"))  # runner 自动版本化备份


def test_migrate_project_dir_single_project(tmp_projects: Path):
    """单项目入口（供导入路径复用）：v1 项目走完整链升到 v2 并归一化 legacy 名。"""
    d = _write_project(tmp_projects, "imported", {"schema_version": 1, "image_backend": "vertex/y"})
    assert migrate_project_dir(d) is True
    data = json.loads((d / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION
    assert data["image_provider_t2i"] == "gemini-vertex/y"
    assert "image_backend" not in data
    # 幂等：已是最新版本再调返回 False、不改动
    assert migrate_project_dir(d) is False


def test_skip_underscore_dirs(tmp_projects: Path):
    (tmp_projects / "_global_assets").mkdir()
    (tmp_projects / "_global_assets" / "keep.txt").write_text("x", encoding="utf-8")
    _write_project(tmp_projects, "p1", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "p1"})
    summary = run_project_migrations(tmp_projects)
    assert "_global_assets" not in summary.skipped
    assert "_global_assets" not in summary.migrated


def test_corrupted_schema_version_skipped_not_abort(tmp_projects: Path):
    """schema_version 不可解析的项目按损坏跳过：不盖戳、不中断其他项目迁移。"""
    _write_project(tmp_projects, "broken", {"schema_version": "corrupted"})
    _write_project(tmp_projects, "ok", {"schema_version": 1, "video_backend": "seedance/x"})

    summary = run_project_migrations(tmp_projects)

    assert "broken" not in summary.migrated + summary.failed + summary.skipped
    assert "ok" in summary.migrated
    data = json.loads((tmp_projects / "broken" / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == "corrupted"  # 原样保留，待人工修复


def test_falsy_or_bool_schema_version_skipped_not_v0(tmp_projects: Path):
    """空串 / bool 等不可解析版本号按损坏跳过，不误当 v0 重跑迁移。"""
    for name, bad in [("empty", ""), ("bool-true", True), ("bool-false", False)]:
        _write_project(tmp_projects, name, {"schema_version": bad})

    summary = run_project_migrations(tmp_projects)

    for name in ("empty", "bool-true", "bool-false"):
        assert name not in summary.migrated + summary.failed + summary.skipped


def test_explicit_null_schema_version_treated_as_v0(tmp_projects: Path):
    """显式 null 与字段缺失同义（v0），正常走完整迁移链。"""
    _write_project(tmp_projects, "p1", {"schema_version": None, "episodes": []})
    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    data = json.loads((tmp_projects / "p1" / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION


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
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")

    old_clues_dir = p / "clues.bak.v0-100000000"
    new_clues_dir = p / "clues.bak.v0-9999999999"
    old_clues_dir.mkdir()
    (old_clues_dir / "a.png").write_bytes(b"x")
    new_clues_dir.mkdir()

    # mtime 控制：old 文件/目录 mtime 设为 8 天前
    eight_days_ago = time.time() - 8 * 86400
    import os

    os.utime(old, (eight_days_ago, eight_days_ago))
    os.utime(old_clues_dir, (eight_days_ago, eight_days_ago))

    cleanup_stale_backups(tmp_projects, max_age_days=7)
    assert not old.exists()
    assert new.exists()
    assert not old_clues_dir.exists()
    assert new_clues_dir.exists()


def test_hardlink_backup_clues_creates_mirror(tmp_projects: Path, monkeypatch):
    """v0→v1 迁移前应硬链接备份 clues/ 到 clues.bak.v0-<ts>/。"""
    p = _write_project(tmp_projects, "p1", {"name": "p1"})  # v0
    (p / "clues").mkdir()
    (p / "clues" / "玉佩.png").write_bytes(b"prop-image")
    (p / "clues" / "nested").mkdir()
    (p / "clues" / "nested" / "deep.png").write_bytes(b"deep")

    def noop_migrator(project_dir: Path) -> None:
        data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        data["schema_version"] = 1
        (project_dir / "project.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr("lib.project_migrations.runner.MIGRATORS", {0: noop_migrator})
    run_project_migrations(tmp_projects)

    backups = list(p.glob("clues.bak.v0-*"))
    assert len(backups) == 1
    bak = backups[0]
    assert (bak / "玉佩.png").read_bytes() == b"prop-image"
    assert (bak / "nested" / "deep.png").read_bytes() == b"deep"
