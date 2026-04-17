"""Runner: 扫描 projects/ 并按版本顺序跑迁移器。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lib.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1

MIGRATORS: dict[int, Callable[[Path], None]] = {}


def _versioned_backup_name(base_name: str, from_version: int, ts: int) -> str:
    """生成单个版本化备份名，例如 project.json → project.json.bak.v0-1712345678。"""
    return f"{base_name}.bak.v{from_version}-{ts}"


def _backup_glob_pattern(base_name: str) -> str:
    """生成 cleanup 用 glob，例如 project.json → project.json.bak.v*-*。"""
    return f"{base_name}.bak.v*-*"


@dataclass
class MigrationSummary:
    migrated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _load_schema_version(project_dir: Path) -> int:
    pj = project_dir / "project.json"
    if not pj.exists():
        return -1  # 跳过非项目目录
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("project.json 损坏，跳过：%s", project_dir)
        return -1
    return int(data.get("schema_version", 0))


def _backup_project_json(project_dir: Path, from_version: int) -> None:
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    ts = int(time.time())
    bak = project_dir / _versioned_backup_name("project.json", from_version, ts)
    bak.write_bytes(pj.read_bytes())


def _hardlink_backup_clues(project_dir: Path, from_version: int) -> None:
    """v0→v1 专用：硬链接备份 clues/ 到 clues.bak.v0-<ts>/，失败则 copytree。0 磁盘开销且可完整回滚。"""
    src = project_dir / "clues"
    if not src.is_dir():
        return
    ts = int(time.time())
    bak = project_dir / _versioned_backup_name("clues", from_version, ts)
    if bak.exists():
        return
    try:
        bak.mkdir()
        for entry in src.rglob("*"):
            rel = entry.relative_to(src)
            target = bak / rel
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(entry, target)
            except OSError:
                # 跨文件系统（EXDEV）等情况 fallback 到复制
                shutil.copy2(entry, target)
    except OSError as exc:
        logger.warning("clues 备份失败（非阻塞）：%s: %s", project_dir, exc)


def run_project_migrations(projects_root: Path) -> MigrationSummary:
    """扫 projects_root 下每个项目目录，升级到 CURRENT_SCHEMA_VERSION。"""
    summary = MigrationSummary()
    if not projects_root.exists():
        return summary

    error_log = projects_root / "_migration_errors.log"

    for child in sorted(projects_root.iterdir()):
        if not child.is_dir():
            continue
        # 跳过下划线前缀与隐藏目录
        if child.name.startswith("_") or child.name.startswith("."):
            continue

        version = _load_schema_version(child)
        if version < 0:
            continue  # 非项目目录
        if version >= CURRENT_SCHEMA_VERSION:
            summary.skipped.append(child.name)
            continue

        try:
            # 逐级迁移
            while version < CURRENT_SCHEMA_VERSION:
                _backup_project_json(child, version)
                if version == 0:
                    _hardlink_backup_clues(child, version)
                migrator = MIGRATORS.get(version)
                if not migrator:
                    raise RuntimeError(f"no migrator from v{version}")
                migrator(child)
                version += 1
            summary.migrated.append(child.name)
        except Exception as e:
            summary.failed.append(child.name)
            tb = traceback.format_exc()
            logger.error("迁移失败 %s: %s", child.name, e)
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with error_log.open("a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {child.name}\n{tb}\n")

    return summary


def cleanup_stale_backups(projects_root: Path, max_age_days: int = 7) -> None:
    """删除超过 max_age_days 的 .bak.v*- 备份文件与 clues.bak.v*-/ 目录。"""
    if not projects_root.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        for bak in project_dir.glob(_backup_glob_pattern("project.json")):
            try:
                if bak.stat().st_mtime < cutoff:
                    bak.unlink()
            except OSError:
                logger.warning("无法删除备份：%s", bak)
        for bak_dir in project_dir.glob(_backup_glob_pattern("clues")):
            if not bak_dir.is_dir():
                continue
            try:
                if bak_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(bak_dir, ignore_errors=True)
            except OSError:
                logger.warning("无法删除 clues 备份：%s", bak_dir)


# 注册 v0→v1 迁移器（顶部 import，此处仅赋值）
MIGRATORS[0] = migrate_v0_to_v1
