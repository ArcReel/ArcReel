"""v7→v8：领域数据契约字段更名，去掉可读时计算的持久化统计。

一次性改写：
- ``content_mode`` → ``creation_type``
- ``source_kind`` → ``source_file_type``
- 删除 ``episodes[].scenes_count`` 与剧本 ``metadata.total_scenes``
- 绑定剧本与 profile manifest 同步更名

不触碰参考生视频的 ``shots[]`` / ``references[]``。
预检全部通过后才备份并原子写入；任一文件失败则不产生部分写入。
"""

from __future__ import annotations

import copy
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.json_io import atomic_write_json, load_json
from lib.path_safety import safe_join
from lib.profile_manifest import MANIFEST_FILENAME

_VALID_CREATION_TYPES = frozenset({"narration", "drama", "ad"})
_VALID_SOURCE_FILE_TYPES = frozenset({"novel", "screenplay"})
_DEFAULT_CREATION_TYPE = "narration"
_DEFAULT_SOURCE_FILE_TYPE = "novel"


def _require_creation_type(value: object, *, source: str) -> str:
    if value is None:
        return _DEFAULT_CREATION_TYPE
    if not isinstance(value, str) or value not in _VALID_CREATION_TYPES:
        raise ValueError(f"{source} creation_type/content_mode 无效: {value!r}")
    return value


def _require_source_file_type(value: object, *, source: str) -> str:
    if value is None:
        return _DEFAULT_SOURCE_FILE_TYPE
    if not isinstance(value, str) or value not in _VALID_SOURCE_FILE_TYPES:
        raise ValueError(f"{source} source_file_type/source_kind 无效: {value!r}")
    return value


def migrate_project_payload(project: Mapping[str, Any]) -> dict[str, Any]:
    """纯转换 project.json；已是 v8 字段形态时保持幂等。"""
    migrated = copy.deepcopy(dict(project))
    if "creation_type" not in migrated:
        migrated["creation_type"] = _require_creation_type(migrated.get("content_mode"), source="project")
    else:
        migrated["creation_type"] = _require_creation_type(migrated.get("creation_type"), source="project")
    migrated.pop("content_mode", None)

    if "source_file_type" not in migrated:
        migrated["source_file_type"] = _require_source_file_type(migrated.get("source_kind"), source="project")
    else:
        migrated["source_file_type"] = _require_source_file_type(migrated.get("source_file_type"), source="project")
    migrated.pop("source_kind", None)

    episodes = migrated.get("episodes")
    if episodes is None:
        return migrated
    if not isinstance(episodes, list):
        raise ValueError("project.episodes 必须是数组")
    cleaned: list[Any] = []
    for index, entry in enumerate(episodes):
        if not isinstance(entry, dict):
            raise ValueError(f"project.episodes[{index}] 必须是对象")
        item = dict(entry)
        item.pop("scenes_count", None)
        cleaned.append(item)
    migrated["episodes"] = cleaned
    return migrated


def migrate_script_payload(payload: Mapping[str, Any], *, fallback_creation_type: str) -> dict[str, Any]:
    """纯转换绑定剧本。保留具体领域集合与参考生视频 shots/references。"""
    migrated = copy.deepcopy(dict(payload))
    if "creation_type" not in migrated:
        raw = migrated.get("content_mode", fallback_creation_type)
        migrated["creation_type"] = _require_creation_type(raw, source="script")
    else:
        migrated["creation_type"] = _require_creation_type(migrated.get("creation_type"), source="script")
    migrated.pop("content_mode", None)

    metadata = migrated.get("metadata")
    if isinstance(metadata, dict):
        cleaned_meta = dict(metadata)
        cleaned_meta.pop("total_scenes", None)
        migrated["metadata"] = cleaned_meta
    return migrated


def migrate_manifest_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(dict(payload))
    if "creation_type" not in migrated and "content_mode" in migrated:
        migrated["creation_type"] = _require_creation_type(migrated.get("content_mode"), source="manifest")
    elif "creation_type" in migrated:
        migrated["creation_type"] = _require_creation_type(migrated.get("creation_type"), source="manifest")
    migrated.pop("content_mode", None)
    return migrated


def _script_paths(project_dir: Path, project: Mapping[str, Any]) -> list[Path]:
    episodes = project.get("episodes")
    if episodes is None:
        return []
    if not isinstance(episodes, list):
        raise ValueError("project.episodes 必须是数组")
    result: list[Path] = []
    seen: set[Path] = set()
    for index, entry in enumerate(episodes):
        if not isinstance(entry, dict):
            raise ValueError(f"project.episodes[{index}] 必须是对象")
        script_file = entry.get("script_file")
        if not isinstance(script_file, str) or not script_file:
            raise ValueError(f"project.episodes[{index}].script_file 必须是非空字符串")
        path = safe_join(project_dir, script_file)
        if path in seen:
            raise ValueError(f"多个 episode 指向同一剧本文件: {script_file}")
        seen.add(path)
        if path.is_symlink():
            raise ValueError(f"剧本文件不是普通文件: {script_file}")
        if not path.exists():
            continue
        if not path.is_file():
            raise ValueError(f"剧本文件不是普通文件: {script_file}")
        result.append(path)
    return result


def _ensure_backup(path: Path) -> None:
    if any(path.parent.glob(f"{path.name}.bak.v7-*")):
        return
    backup = path.with_name(f"{path.name}.bak.v7-{time.time_ns()}")
    shutil.copy2(path, backup)


def migrate_v7_to_v8(project_dir: Path) -> None:
    """先预检项目、剧本与 manifest，再备份并原子替换，最后提交 schema_version。"""
    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if int(project.get("schema_version") or 0) >= 8:
        return

    migrated_project = migrate_project_payload(project)
    fallback_creation_type = str(migrated_project["creation_type"])

    script_plans: list[tuple[Path, dict[str, Any]]] = []
    for path in _script_paths(project_dir, project):
        payload = load_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"剧本必须是对象: {path.relative_to(project_dir)}")
        script_plans.append((path, migrate_script_payload(payload, fallback_creation_type=fallback_creation_type)))

    manifest_plan: tuple[Path, dict[str, Any]] | None = None
    manifest_path = project_dir / MANIFEST_FILENAME
    if manifest_path.is_file() and not manifest_path.is_symlink():
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("profile manifest 必须是对象")
        manifest_plan = (manifest_path, migrate_manifest_payload(manifest))

    _ensure_backup(project_file)
    for path, _payload in script_plans:
        _ensure_backup(path)
    if manifest_plan is not None:
        _ensure_backup(manifest_plan[0])

    # 写入顺序是刻意的：单文件由 atomic_write_json 保证原子，跨文件则以 project.json 的
    # schema_version 作为唯一提交点——附属文件全部落盘后才最后提交它。中途崩溃留下的是
    # 「附属文件已是 v8 形态、project.json 仍标 v7」，下次启动重跑本迁移即可收敛：三个
    # payload 转换器对已迁移字段均幂等，且 _ensure_backup 见到既有 .bak.v7-* 会跳过，
    # 不会用半迁移态覆盖首轮留下的原版备份。反过来先提交 schema_version 才是不可恢复的
    # ——项目会被当作已升级，而附属文件仍是旧字段。
    for path, payload in script_plans:
        atomic_write_json(path, payload)
    if manifest_plan is not None:
        atomic_write_json(manifest_plan[0], manifest_plan[1])
    migrated_project["schema_version"] = 8
    atomic_write_json(project_file, migrated_project)


__all__ = [
    "migrate_manifest_payload",
    "migrate_project_payload",
    "migrate_script_payload",
    "migrate_v7_to_v8",
]
