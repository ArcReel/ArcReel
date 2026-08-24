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

from lib.artifact_activation import ARTIFACT_MANIFEST_SCHEMA_VERSION
from lib.episode_ledger import parse_positive_episode_num
from lib.episode_paths import REFERENCE_VIDEO_STEP1_FILENAME, episode_drafts_dir
from lib.path_safety import try_safe_join
from lib.project_migration_failure import (
    MigrationFailureRecord,
    ProjectMigrationError,
    clear_migration_failure,
    record_migration_failure,
)
from lib.project_migrations.staged_swap import (
    cleanup_completed_swap_dirs,
    ensure_disk_headroom,
    reclaim_interrupted_swaps,
)
from lib.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1
from lib.project_migrations.v1_to_v2_normalize_providers import migrate_v1_to_v2
from lib.project_migrations.v2_to_v3_episode_ledger import migrate_v2_to_v3
from lib.project_migrations.v3_to_v4_text_tiers import migrate_v3_to_v4
from lib.project_migrations.v4_to_v5_generation_route import migrate_v4_to_v5
from lib.project_migrations.v5_to_v6_asset_namespace import migrate_v5_to_v6
from lib.project_migrations.v6_to_v7_ad_reference_video_units import migrate_v6_to_v7
from lib.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8
from lib.project_migrations.v8_to_v9_reference_unit_text import migrate_v8_to_v9
from lib.project_migrations.v9_to_v10_video_style_prompt import migrate_v9_to_v10
from lib.project_migrations.v10_to_v11_reference_storyboard_sheet import migrate_v10_to_v11
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION, parse_project_schema_version

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = CURRENT_PROJECT_SCHEMA_VERSION

#: 清单激活的输入备份命名所用的版本号（``*.bak.v7-*``）：它是那一步的**起点**版本。
_ACTIVATION_BACKUP_VERSION = ARTIFACT_MANIFEST_SCHEMA_VERSION - 1

MIGRATORS: dict[int, Callable[[Path], None]] = {}
_MIGRATORS_WITH_OWNED_BACKUP = frozenset({7, 8, 10})

# 只读预检：在 runner 写下任何备份之前跑，拒绝时项目目录一个字节都没被动过。
_MIGRATOR_PREFLIGHTS: dict[int, Callable[[Path], None]] = {5: ensure_disk_headroom}


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
    """Resolve every script-shaped source a migration was allowed to back up.

    账本里绑定的剧集脚本，加上同集的 step1 草稿——草稿是同一份正文的上一形态，改写脚本的
    迁移同批改写它，备份因此成对出现，回收也必须成对，否则草稿备份没有任何清理路径。
    """

    try:
        project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ()
    episodes = project.get("episodes") if isinstance(project, dict) else None
    if not isinstance(episodes, list):
        return ()
    sources: list[Path] = []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        script_file = episode.get("script_file")
        if isinstance(script_file, str) and script_file:
            source = try_safe_join(project_dir, script_file)
            if source is not None:
                sources.append(source)
        episode_num = parse_positive_episode_num(episode.get("episode"))
        if episode_num is not None:
            sources.append(episode_drafts_dir(project_dir, episode_num) / REFERENCE_VIDEO_STEP1_FILENAME)
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
        if not isinstance(data, dict):
            raise ValueError("project.json 必须包含对象")
        return parse_project_schema_version(data)
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
        preflight = _MIGRATOR_PREFLIGHTS.get(version)
        if preflight:
            preflight(project_dir)
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


def _append_error_log(project_dir: Path, tb: str) -> None:
    """Keep the full traceback out of the user-facing verdict but on disk for support."""

    error_log = project_dir.parent / "_migration_errors.log"
    try:
        error_log.parent.mkdir(parents=True, exist_ok=True)
        with error_log.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {project_dir.name}\n{tb}\n")
    except OSError as exc:
        logger.warning("无法写入迁移错误日志：%s（%s）", error_log, exc)


def migrate_project_with_verdict(project_dir: Path) -> MigrationFailureRecord | None:
    """Run the chain for one project and persist the verdict beside its data.

    Returns ``None`` once the project sits at the current schema — the previous
    verdict, if any, is cleared — or the recorded failure. Idempotent: a project
    that is already current is a no-op success, so retrying costs nothing.
    """

    try:
        migrate_project_dir(project_dir)
    except Exception as exc:  # noqa: BLE001 - one project's failure is isolated, not fatal
        logger.error("迁移失败 %s: %s", project_dir.name, exc)
        # The persisted verdict is what the production status, the production plan
        # and every generation entry read to refuse work on this project.
        _append_error_log(project_dir, traceback.format_exc())
        return record_migration_failure(project_dir, exc, schema_version=_load_schema_version(project_dir))
    version = _load_schema_version(project_dir)
    if version != CURRENT_SCHEMA_VERSION:
        # ``migrate_project_dir`` returns without raising for a directory it cannot
        # place on the chain at all — no readable ``project.json``, or a version the
        # chain does not cover. Reaching the current schema is the only evidence the
        # project is repaired, so anything else keeps it blocked.
        return record_migration_failure(
            project_dir,
            ProjectMigrationError(
                f"schema version {version} did not reach v{CURRENT_SCHEMA_VERSION}",
                file="project.json",
            ),
            schema_version=version,
        )
    # A retry that finally lands clears the verdict, so the project stops being
    # reported as blocked without any further user action.
    clear_migration_failure(project_dir)
    return None


def run_project_migrations(projects_root: Path) -> MigrationSummary:
    """扫 projects_root 下每个项目目录，升级到 CURRENT_SCHEMA_VERSION。"""
    summary = MigrationSummary()
    if not projects_root.exists():
        return summary

    # 认领在遍历之前：被改回的项目在本轮就继续迁移，不必等下次启动。
    reclaim_interrupted_swaps(projects_root)

    for child in sorted(projects_root.iterdir()):
        if not child.is_dir():
            continue
        # 跳过下划线前缀与隐藏目录
        if child.name.startswith("_") or child.name.startswith("."):
            continue

        version = _load_schema_version(child)
        if version < 0:
            continue  # 非项目目录
        # Persisting the verdict is itself disk work: one project whose directory
        # cannot be written must not abort the pass for every project after it.
        try:
            if version >= CURRENT_SCHEMA_VERSION:
                # A project that reached the current schema by any route is not blocked;
                # a verdict left over from an earlier attempt would strand it forever.
                clear_migration_failure(child)
                summary.skipped.append(child.name)
                continue

            if migrate_project_with_verdict(child) is None:
                summary.migrated.append(child.name)
            else:
                summary.failed.append(child.name)
        except OSError as exc:
            logger.error("迁移裁决无法落盘 %s：%s", child.name, exc)
            summary.failed.append(child.name)

    return summary


def cleanup_stale_backups(projects_root: Path, max_age_days: int = 7) -> None:
    """删除超过 max_age_days、且可归属到迁移输入的版本化备份与目录交换中间目录。"""
    if not projects_root.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    cleanup_completed_swap_dirs(projects_root, cutoff)
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        # 清单激活的恢复备份留到那一步的版本提升坐实为止，之后才可回收；它锚在
        # 「激活落点」而非「当前版本」上，否则每次 schema 升版都会把回收目标错位到别的迁移。
        retain_activation_recovery = _load_schema_version(project_dir) < ARTIFACT_MANIFEST_SCHEMA_VERSION
        project_backup_versions = tuple(
            version
            for version in range(CURRENT_SCHEMA_VERSION)
            if not (retain_activation_recovery and version == _ACTIVATION_BACKUP_VERSION)
        )
        activation_backup_versions = () if retain_activation_recovery else (_ACTIVATION_BACKUP_VERSION,)
        # 脚本类源文与 project.json 用同一份版本集合：备份名按来源文件枚举，列进从未产生过
        # 备份的版本没有代价，而写死一张「哪几版改过脚本」的清单会在下一次迁移时漏掉新版本。
        script_backup_versions = project_backup_versions
        sources = (
            (project_dir / "project.json", project_backup_versions),
            (project_dir / ".arcreel_artifacts.json", activation_backup_versions),
            *((source, script_backup_versions) for source in _bound_script_sources(project_dir)),
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
MIGRATORS[8] = migrate_v8_to_v9
MIGRATORS[9] = migrate_v9_to_v10
MIGRATORS[10] = migrate_v10_to_v11
