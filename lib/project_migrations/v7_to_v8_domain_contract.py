"""v7→v8：领域数据契约字段更名，去掉可读时计算的持久化统计。

一次性改写：
- ``content_mode`` → ``creation_type``
- ``source_kind`` → ``source_file_type``
- 删除 ``episodes[].scenes_count`` 与剧本 ``metadata`` 里按骨架落盘的四个统计键
- 绑定剧本与 profile manifest 同步更名

不触碰参考生视频的 ``shots[]`` / ``references[]``。
预检全部通过后才备份并原子写入；任一文件失败则不产生部分写入。
"""

from __future__ import annotations

import copy
import logging
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.json_io import atomic_write_bytes, atomic_write_json, load_json
from lib.path_safety import safe_join
from lib.profile_manifest import MANIFEST_FILENAME

logger = logging.getLogger(__name__)

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


#: v7 剧本按骨架种类落盘的四个统计键。骨架不同键名也不同（segments→total_segments，
#: shots→total_shots，video_units→total_units），一个项目里三种形态都可能存在，逐个删。
#: 这里刻意抄一份而不引用 ``lib.script_models.NON_PERSISTED_COUNT_KEYS``：迁移描述的是
#: v7 那一刻的形状，后续版本再新增计数键也不该改变这一步的行为。
_LEGACY_METADATA_COUNT_KEYS = ("total_scenes", "total_segments", "total_shots", "total_units")


def migrate_script_payload(payload: Mapping[str, Any], *, fallback_creation_type: str) -> dict[str, Any]:
    """纯转换绑定剧本。保留具体领域集合与参考生视频 shots/references。"""
    migrated = copy.deepcopy(dict(payload))
    # 显式 null 与未打戳同义（与 ``lib.script_models.resolve_creation_type`` 同口径），逐级回退到
    # 项目声明。按「键在场」判断会让 ``"content_mode": null`` 落进兜底值 narration，把一集 drama
    # 静默改标成说书——后续视频执行会跳过对白与音色注入。
    raw = migrated.get("creation_type")
    if raw is None:
        raw = migrated.get("content_mode")
    if raw is None:
        raw = fallback_creation_type
    migrated["creation_type"] = _require_creation_type(raw, source="script")
    migrated.pop("content_mode", None)

    metadata = migrated.get("metadata")
    if isinstance(metadata, dict):
        cleaned_meta = dict(metadata)
        for key in _LEGACY_METADATA_COUNT_KEYS:
            cleaned_meta.pop(key, None)
        migrated["metadata"] = cleaned_meta
    return migrated


def migrate_manifest_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """manifest 里 null 戳保持 null：``load_manifest`` 把它读成「未迁移」并整体重置同步，
    而编出一个兜底值会让一个 drama 项目的 manifest 看起来是说书的、只能靠不匹配才纠回来。"""
    migrated = copy.deepcopy(dict(payload))
    raw = migrated.get("creation_type")
    if raw is None:
        raw = migrated.get("content_mode")
    if raw is not None:
        migrated["creation_type"] = _require_creation_type(raw, source="manifest")
    elif "creation_type" in migrated or "content_mode" in migrated:
        migrated["creation_type"] = None
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


def _ensure_backup(path: Path) -> Path:
    """备份 ``path`` 并返回备份路径；已有备份时复用最早那份（首轮留下的才是原版）。"""
    existing = sorted(path.parent.glob(f"{path.name}.bak.v7-*"))
    if existing:
        return existing[0]
    backup = path.with_name(f"{path.name}.bak.v7-{time.time_ns()}")
    shutil.copy2(path, backup)
    # copy2 会把源文件的旧 mtime 一并复制过来。启动时迁移紧接着跑 7 天过期清理，长期没改过的
    # 项目刚做出来的备份会在同一次启动里被当成陈旧备份删掉——恰恰是最需要保留回滚材料的那批。
    os.utime(backup, None)
    return backup


def _restore_from_backups(written: list[Path], backups: Mapping[Path, Path]) -> None:
    """把已写入的附属文件还原回备份内容，让失败的迁移不留下部分写入。

    逐文件 best-effort：还原本身失败（磁盘满、权限收紧往往就是首次写失败的同因）只记日志、
    继续还原其余文件，最终由调用方抛出原始异常。半还原态不影响可恢复性——三个 payload 转换器
    对已迁移字段幂等，且 ``project.json`` 未提交 ``schema_version``，重跑本迁移即可收敛。

    还原走 ``atomic_write_bytes`` 而不是 ``shutil.copy2``：后者先截断目标再拷贝，磁盘耗尽时
    留下的是半截无效 JSON，下次重跑在 ``load_json`` 上直接失败，前面说的「重跑即可收敛」就不
    成立了。原子替换让还原失败的文件保持完整的 v8 形态。
    """
    for path in reversed(written):
        backup = backups.get(path)
        if backup is None:
            continue
        try:
            atomic_write_bytes(path, backup.read_bytes())
        except OSError:
            logger.exception("v7→v8 迁移回滚失败，%s 仍是 v8 形态；重跑迁移可收敛", path)


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
    # symlink 形态的 manifest 跳过而非拒绝：profile 同步侧同样拒绝信任并在下次同步时整体
    # 重置（见 lib.profile_manifest.load_manifest），其中的旧字段永远不会被读到；为一个
    # 注定被重置的文件让整个项目迁移失败是把可恢复状态升级成不可用状态。
    if manifest_path.is_file() and not manifest_path.is_symlink():
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("profile manifest 必须是对象")
        manifest_plan = (manifest_path, migrate_manifest_payload(manifest))

    backups: dict[Path, Path] = {project_file: _ensure_backup(project_file)}
    for path, _payload in script_plans:
        backups[path] = _ensure_backup(path)
    if manifest_plan is not None:
        backups[manifest_plan[0]] = _ensure_backup(manifest_plan[0])

    # 写入顺序是刻意的：单文件由 atomic_write_json 保证原子，跨文件则以 project.json 的
    # schema_version 作为唯一提交点——附属文件全部落盘后才最后提交它。反过来先提交
    # schema_version 是不可恢复的：项目会被当作已升级，而附属文件仍是旧字段。
    #
    # 写入期异常按已写入清单逆序回滚到备份，失败的迁移不留下部分写入。进程崩溃这类跑不到
    # 回滚的中断留下「附属文件已是 v8 形态、project.json 仍标 v7」，下次启动重跑本迁移即可
    # 收敛：三个 payload 转换器对已迁移字段均幂等，且 _ensure_backup 见到既有 .bak.v7-*
    # 会复用，不会用半迁移态覆盖首轮留下的原版备份。
    written: list[Path] = []
    try:
        for path, payload in script_plans:
            atomic_write_json(path, payload)
            written.append(path)
        if manifest_plan is not None:
            atomic_write_json(manifest_plan[0], manifest_plan[1])
            written.append(manifest_plan[0])
        migrated_project["schema_version"] = 8
        atomic_write_json(project_file, migrated_project)
    except Exception:
        _restore_from_backups(written, backups)
        raise


__all__ = [
    "migrate_manifest_payload",
    "migrate_project_payload",
    "migrate_script_payload",
    "migrate_v7_to_v8",
]
