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

from lib.path_safety import try_safe_join
from lib.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1
from lib.project_migrations.v1_to_v2_normalize_providers import migrate_v1_to_v2
from lib.project_migrations.v2_to_v3_episode_ledger import migrate_v2_to_v3
from lib.project_migrations.v3_to_v4_text_tiers import migrate_v3_to_v4
from lib.project_migrations.v4_to_v5_generation_route import migrate_v4_to_v5
from lib.project_migrations.v5_to_v6_asset_namespace import migrate_v5_to_v6
from lib.project_migrations.v6_to_v7_ad_reference_video_units import migrate_v6_to_v7
from lib.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 8

MIGRATORS: dict[int, Callable[[Path], None]] = {}
_MIGRATORS_WITH_OWNED_BACKUP = frozenset({7})


def _versioned_backup_name(base_name: str, from_version: int, ts: int) -> str:
    """生成单个版本化备份名，例如 project.json → project.json.bak.v0-1712345678。"""
    return f"{base_name}.bak.v{from_version}-{ts}"


def _numeric_backup_candidates(source: Path, versions: tuple[int, ...]) -> list[Path]:
    """Enumerate only backup names emitted for one migration-owned source."""

    candidates: list[Path] = []
    for version in versions:
        prefix = f"{source.name}.bak.v{version}-"
        for candidate in source.parent.glob(f"{prefix}*"):
            if candidate.name.removeprefix(prefix).isdigit():
                candidates.append(candidate)
    return candidates


def _bound_script_sources(project_dir: Path) -> tuple[Path, ...]:
    """Resolve the exact script paths that v6/v7 migrations were allowed to back up."""

    try:
        project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ()
    episodes = project.get("episodes") if isinstance(project, dict) else None
    if not isinstance(episodes, list):
        return ()
    sources: list[Path] = []
    for episode in episodes:
        script_file = episode.get("script_file") if isinstance(episode, dict) else None
        if not isinstance(script_file, str) or not script_file:
            continue
        source = try_safe_join(project_dir, script_file)
        if source is not None:
            sources.append(source)
    return tuple(sources)


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
        raw_version = data.get("schema_version")
        if raw_version is None:
            return 0  # 仅字段缺失或显式 null 视为旧项目（v0）
        if isinstance(raw_version, bool):
            # bool 是 int 子类：int(True) == 1 会把损坏值静默当 v1
            raise ValueError(f"schema_version 不可解析: {raw_version!r}")
        # int 解析留在 try 内：其余不可解析值（空串/非数字串）与 JSON 损坏
        # 同口径跳过——不当 v0 重跑迁移，也不让单个损坏项目中断整个迁移循环
        return int(raw_version)
    except Exception as exc:
        logger.warning("project.json 损坏或 schema_version 不可解析，跳过：%s（%s）", project_dir, exc)
        return -1


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


def migrate_project_dir(project_dir: Path) -> bool:
    """将单个项目目录逐级升级到 CURRENT_SCHEMA_VERSION，返回是否实际迁移。

    供启动期 ``run_project_migrations`` 与项目导入路径共用：启动期 runner 只覆盖启动时已存在的
    项目，启动后导入的旧归档需在导入入口补跑此函数走完整迁移链，否则解析链（不再读 legacy
    字段）会让该项目静默回退到全局默认。非项目目录 / 已是最新版本返回 False。"""
    version = _load_schema_version(project_dir)
    if version < 0 or version >= CURRENT_SCHEMA_VERSION:
        return False
    while version < CURRENT_SCHEMA_VERSION:
        # Activation migrations must finish their complete read-only preflight
        # before creating any backup.  Their commit boundary owns the backup so
        # the runner cannot leave writes behind when preflight rejects a project.
        if version not in _MIGRATORS_WITH_OWNED_BACKUP:
            _backup_project_json(project_dir, version)
        if version == 0:
            _hardlink_backup_clues(project_dir, version)
        migrator = MIGRATORS.get(version)
        if not migrator:
            raise RuntimeError(f"no migrator from v{version}")
        migrator(project_dir)
        version += 1
    return True


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
            migrate_project_dir(child)
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
    """删除超过 max_age_days、且可归属到迁移输入的版本化备份。"""
    if not projects_root.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        sources = (
            (project_dir / "project.json", tuple(range(CURRENT_SCHEMA_VERSION))),
            (project_dir / ".arcreel_artifacts.json", (7,)),
            *((source, (6, 7)) for source in _bound_script_sources(project_dir)),
        )
        for source, versions in sources:
            for bak in _numeric_backup_candidates(source, versions):
                if bak.is_symlink() or not bak.is_file():
                    continue
                try:
                    if bak.stat().st_mtime < cutoff:
                        bak.unlink()
                except OSError:
                    logger.warning("无法删除备份：%s", bak)
        for bak_dir in _numeric_backup_candidates(project_dir / "clues", (0,)):
            if bak_dir.is_symlink() or not bak_dir.is_dir():
                continue
            try:
                if bak_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(bak_dir, ignore_errors=True)
            except OSError:
                logger.warning("无法删除 clues 备份：%s", bak_dir)


# 注册迁移器（顶部 import，此处仅赋值）
MIGRATORS[0] = migrate_v0_to_v1
MIGRATORS[1] = migrate_v1_to_v2
MIGRATORS[2] = migrate_v2_to_v3
MIGRATORS[3] = migrate_v3_to_v4
MIGRATORS[4] = migrate_v4_to_v5
MIGRATORS[5] = migrate_v5_to_v6
MIGRATORS[6] = migrate_v6_to_v7
MIGRATORS[7] = migrate_v7_to_v8
