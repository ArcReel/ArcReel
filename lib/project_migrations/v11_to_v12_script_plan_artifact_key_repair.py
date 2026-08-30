"""v11→v12：把产物清单里残留的 `episode-step1` kind 改名为 `episode-script-plan`。

残留的旧 kind 不会被读侧当作「该产物缺失」：`lib.artifact_manifest` 解析清单时遇到不认识的
kind 判整份清单不可读，该项目每个产物随即变成 `manifest_unreadable` 阻塞，且除清空清单外没有
自愈路径。本步就地改名，其余条目一字不动。

改名复用 v9→v10 的同一份纯转换，对照表只认旧 kind：已在新 key 下的项目是空操作，重跑无副作用。
写入顺序沿用本目录约定：先只读预检，全部通过后才备份并落盘——清单形状损坏时本步拒绝迁移，
项目目录因此必须一个字节都不变，故 project.json 的备份也由本步自持而非交给 runner。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from lib.json_io import atomic_write_json, load_json
from lib.project_migration_failure import ProjectMigrationError
from lib.project_migrations.v9_to_v10_script_plan_naming import migrate_manifest

_TARGET_VERSION = 12
_MANIFEST_FILENAME = ".arcreel_artifacts.json"


def _ensure_backup(path: Path, from_version: int) -> None:
    if any(path.parent.glob(f"{path.name}.bak.v{from_version}-*")):
        return
    shutil.copy2(path, path.with_name(f"{path.name}.bak.v{from_version}-{time.time_ns()}"))


def _planned_manifest_repair(manifest_file: Path) -> dict[str, Any] | None:
    """只读预检：算出待写回的清单载荷；无清单或已无旧 key 时返回 None。"""

    if manifest_file.is_symlink():
        raise ProjectMigrationError("产物清单不是普通文件", file=_MANIFEST_FILENAME)
    if not manifest_file.exists():
        return None
    if not manifest_file.is_file():
        raise ProjectMigrationError("产物清单不是普通文件", file=_MANIFEST_FILENAME)
    manifest = load_json(manifest_file)
    if not isinstance(manifest, dict):
        raise ProjectMigrationError("产物清单必须是对象", file=_MANIFEST_FILENAME)
    try:
        repaired = migrate_manifest(manifest)
    except ValueError as exc:
        raise ProjectMigrationError(str(exc), file=_MANIFEST_FILENAME) from exc
    return repaired if repaired != manifest else None


def migrate_v11_to_v12(project_dir: Path) -> None:
    """启动扫描与归档导入共用的单一入口（经 ``migrate_project_dir`` 调用）。"""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if int(project.get("schema_version") or 0) >= _TARGET_VERSION:
        return

    manifest_file = project_dir / _MANIFEST_FILENAME
    repaired = _planned_manifest_repair(manifest_file)

    # 预检全部通过后才动盘：本步自持 project.json 的备份（runner 据
    # ``_MIGRATORS_WITH_OWNED_BACKUP`` 让位），预检拒绝时项目目录一个字节都没被动过。
    _ensure_backup(project_file, _TARGET_VERSION - 1)
    if repaired is not None:
        _ensure_backup(manifest_file, _TARGET_VERSION - 1)
        atomic_write_json(manifest_file, repaired)
    atomic_write_json(project_file, {**project, "schema_version": _TARGET_VERSION})


__all__ = ["migrate_v11_to_v12"]
