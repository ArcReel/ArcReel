"""Runner: 扫描 projects/ 并按版本顺序跑迁移器。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path

from lib.json_io import atomic_write_bytes
from lib.project_migrations.script_binding import resolve_bound_script_path
from lib.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1
from lib.project_migrations.v1_to_v2_normalize_providers import migrate_v1_to_v2
from lib.project_migrations.v2_to_v3_episode_ledger import migrate_v2_to_v3
from lib.project_migrations.v3_to_v4_text_tiers import migrate_v3_to_v4
from lib.project_migrations.v4_to_v5_generation_route import migrate_v4_to_v5
from lib.project_migrations.v5_to_v6_asset_namespace import migrate_v5_to_v6
from lib.project_migrations.v6_to_v7_ad_reference_video_units import migrate_v6_to_v7
from lib.project_migrations.v7_to_v8_domain_contract import migrate_v7_to_v8

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 8

MIGRATORS: dict[int, Callable[[Path], None]] = {}


def _versioned_backup_name(base_name: str, from_version: int, ts: int) -> str:
    """生成单个版本化备份名，例如 project.json → project.json.bak.v0-1712345678。"""
    return f"{base_name}.bak.v{from_version}-{ts}"


#: 迁移备份名形如 ``<原名>.bak.v<版本>-<时间戳>``，与被备份的文件同目录。
_BACKUP_NAME_GLOB = "*.bak.v*-*"
_BACKUP_NAME_RE = re.compile(r"\.bak\.v\d+-\d+$")


def is_migration_backup_name(name: str) -> bool:
    """判断单个文件 / 目录名是否是迁移备份。

    导出、校验等遍历项目树的调用方用它把备份挡在外面：备份是迁移的回滚材料，不属于项目内容，
    跟着归档走会让导入方多出一份旧字段形态的剧本副本。
    """
    return bool(_BACKUP_NAME_RE.search(name))


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
        # 只认真正的整数：bool 是 int 子类（int(True) == 1 会把损坏值当 v1），小数与
        # 数字串则会被 int() 截断（7.5 当成 v7 迁一遍，把未知形态改写成 v8 的样子）。
        # 判定与 require_current_schema / 导入侧版本闸门同口径。
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            raise ValueError(f"schema_version 不可解析: {raw_version!r}")
        # 抛出留在 try 内：不可解析值与 JSON 损坏同口径跳过——不当 v0 重跑迁移，
        # 也不让单个损坏项目中断整个迁移循环
        return raw_version
    except Exception as exc:
        logger.warning("project.json 损坏或 schema_version 不可解析，跳过：%s（%s）", project_dir, exc)
        return -1


def _backup_project_json(project_dir: Path, from_version: int) -> Path | None:
    pj = project_dir / "project.json"
    if not pj.exists():
        return None
    ts = int(time.time())
    bak = project_dir / _versioned_backup_name("project.json", from_version, ts)
    # 同目录 tmp + rename：备份要么完整要么不存在。直接 write_bytes 中途失败（磁盘满 / IO 错误）
    # 会留下截断的 .bak，而迁移器自己的备份逻辑按同一命名规则复用既有备份当原版，回滚时反倒用
    # 半截内容盖掉现场。
    atomic_write_bytes(bak, pj.read_bytes())
    return bak


def _discard_unchanged_backup(project_dir: Path, backup: Path | None) -> None:
    """迁移这一级失败后，若 project.json 与刚落的备份逐字节相同就回收该备份。

    迁移器可能在动任何文件之前就因预检失败（如 v7→v8 的全量文件预检）。此时备份没有承载
    任何现场，留着只会让每次启动重跑都多堆一个 ``.bak``：故障不自愈的项目会无限攒备份。
    内容一旦不同就保留——那是真被改过的原版，回滚要靠它。
    """
    if backup is None or not backup.exists():
        return
    pj = project_dir / "project.json"
    try:
        if pj.exists() and pj.read_bytes() == backup.read_bytes():
            backup.unlink()
    except OSError as exc:
        logger.warning("回收未生效的迁移备份失败（非阻塞）：%s: %s", backup, exc)


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
        backup = _backup_project_json(project_dir, version)
        if version == 0:
            _hardlink_backup_clues(project_dir, version)
        migrator = MIGRATORS.get(version)
        if not migrator:
            raise RuntimeError(f"no migrator from v{version}")
        try:
            migrator(project_dir)
        except Exception:
            _discard_unchanged_backup(project_dir, backup)
            raise
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


def _backup_search_dirs(project_dir: Path) -> list[Path]:
    """备份可能落在哪些目录：项目根、一级子目录，外加绑定剧本的实际所在目录。

    备份总是留在被备份文件旁边——project.json 与 profile manifest 在项目根，剧本备份在
    ``episodes[].script_file`` 指向处。该绑定可以指到更深的层级（``scripts/season_1/episode_1.json``
    是合法路径），固定扫两层会漏掉这类备份、让它们永久堆积；顺着绑定解析则不必递归整棵项目树——
    项目树里绝大多数是媒体文件，每次启动全量遍历不划算。

    只收在项目内的真实目录：清理是删除操作，顺着符号链接扫下去会删到项目树以外的文件。
    """
    dirs: dict[Path, None] = {project_dir: None}
    try:
        for child in project_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                dirs[child] = None
    except OSError:
        logger.warning("无法列出项目子目录，仅按绑定剧本与项目根清理备份：%s", project_dir)

    project_file = project_dir / "project.json"
    try:
        project = json.loads(project_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return list(dirs)
    if not isinstance(project, dict):
        return list(dirs)
    episodes = project.get("episodes")
    if not isinstance(episodes, list):
        return list(dirs)

    try:
        project_root = project_dir.resolve()
    except OSError:
        return list(dirs)
    for entry in episodes:
        if not isinstance(entry, dict):
            continue
        script_file = entry.get("script_file")
        if not isinstance(script_file, str) or not script_file:
            continue
        try:
            parent = resolve_bound_script_path(project_dir, script_file).parent
            # 逐段跟随符号链接后仍在项目内才收：链接指到项目外时 resolve() 会暴露真实位置
            if not parent.resolve().is_relative_to(project_root):
                continue
        except (OSError, ValueError):
            continue
        if parent.is_dir():
            dirs[parent] = None
    return list(dirs)


def cleanup_stale_backups(projects_root: Path, max_age_days: int = 7) -> None:
    """删除超过 max_age_days 的迁移备份文件与目录。"""
    if not projects_root.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for project_dir in projects_root.iterdir():
        # 目录本身是符号链接就整个跳过：清理是删除操作，顺着链接扫下去会删到项目树以外的文件。
        # 只判备份条目自身是不是链接不够——父目录是链接时，链接目标里的备份看起来就在项目内。
        if project_dir.is_symlink() or not project_dir.is_dir():
            continue
        search_dirs = _backup_search_dirs(project_dir)
        for bak in chain.from_iterable(directory.glob(_BACKUP_NAME_GLOB) for directory in search_dirs):
            if not is_migration_backup_name(bak.name):
                continue
            try:
                if bak.stat().st_mtime >= cutoff:
                    continue
                if bak.is_dir() and not bak.is_symlink():
                    shutil.rmtree(bak, ignore_errors=True)
                else:
                    bak.unlink()
            except OSError:
                logger.warning("无法删除迁移备份：%s", bak)


# 注册迁移器（顶部 import，此处仅赋值）
MIGRATORS[0] = migrate_v0_to_v1
MIGRATORS[1] = migrate_v1_to_v2
MIGRATORS[2] = migrate_v2_to_v3
MIGRATORS[3] = migrate_v3_to_v4
MIGRATORS[4] = migrate_v4_to_v5
MIGRATORS[5] = migrate_v5_to_v6
MIGRATORS[6] = migrate_v6_to_v7
MIGRATORS[7] = migrate_v7_to_v8
