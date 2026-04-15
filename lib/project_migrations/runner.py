"""Runner: 扫描 projects/ 并按版本顺序跑迁移器。"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lib.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1

MIGRATORS: dict[int, Callable[[Path], None]] = {}


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
    bak = project_dir / f"project.json.bak.v{from_version}-{ts}"
    bak.write_bytes(pj.read_bytes())


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
    """删除超过 max_age_days 的 .bak.v*- 备份文件。"""
    if not projects_root.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        for bak in project_dir.glob("project.json.bak.v*-*"):
            try:
                if bak.stat().st_mtime < cutoff:
                    bak.unlink()
            except OSError:
                logger.warning("无法删除备份：%s", bak)


# 注册 v0→v1 迁移器（顶部 import，此处仅赋值）
MIGRATORS[0] = migrate_v0_to_v1
