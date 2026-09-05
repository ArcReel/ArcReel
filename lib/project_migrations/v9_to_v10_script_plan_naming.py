"""v9→v10：脚本规划 / 提示词编写的落盘名称随术语改名。

改名覆盖三处落盘事实，语义一律不变：

- ``drafts/episode_N/`` 下的脚本规划草稿与待修复草稿文件名（含旧版 ``.md`` 别名）由
  ``step1_*`` / ``step2_*`` 前缀改为 ``script_plan_*`` / ``prompt_authoring_*``；
- ``project.json`` 分集条目的 ``step1_review`` / ``stale_step1_revision`` /
  ``stale_step1_rebuilt_revision`` 三个字段；
- 剧集脚本 ``metadata`` 的 ``step1_revision`` 字段。

产物清单 ``.arcreel_artifacts.json`` 同批改写：脚本规划产物的 artifact key 里编码着
``episode-step1`` 这个 kind，改名后旧 key 不再可解析，而条目的 ``artifact_path`` 指着刚被
改名的草稿文件。两者不改写，清单在下一次读取时整体判为损坏。产物指纹 ``basis_digest``
不动——它由 ``lib.artifact_provenance`` 的冻结 basis token 保证跨改名稳定。

写入顺序沿用本目录约定：先只读预检并算出全部目标载荷，全部通过后才创建备份、再逐文件
落盘。任一处损坏时项目目录一个字节都没被动过，runner 据此落「需要修复」裁决。
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lib.artifact_manifest import decode_artifact_key_parts, encode_artifact_key_parts
from lib.json_io import atomic_write_json, load_json
from lib.path_safety import safe_join
from lib.project_migration_failure import ProjectMigrationError
from lib.project_migrations.backups import ensure_versioned_backup

_TARGET_VERSION = 10

#: 草稿文件的旧名 → 新名。键是 v9 的落盘事实，值是 v10 的落盘事实；两侧都写死在本迁移里，
#: 之后任一侧再改名都不该回头改写这一步的历史对应关系。runner 的备份回收按这两侧枚举。
DRAFT_FILE_RENAMES: dict[str, str] = {
    "step1_normalized_script.json": "script_plan_normalized_script.json",
    "step1_normalized_script.invalid.json": "script_plan_normalized_script.invalid.json",
    "step1_normalized_script.md": "script_plan_normalized_script.md",
    "step1_segments.json": "script_plan_segments.json",
    "step1_segments.invalid.json": "script_plan_segments.invalid.json",
    "step1_segments.md": "script_plan_segments.md",
    "step1_reference_units.json": "script_plan_reference_units.json",
    "step1_reference_units.invalid.json": "script_plan_reference_units.invalid.json",
    "step1_reference_units.md": "script_plan_reference_units.md",
    "step2_reference_script.invalid.json": "prompt_authoring_reference_script.invalid.json",
}

#: ``project.json`` 分集条目上的字段改名。
_EPISODE_FIELD_RENAMES: dict[str, str] = {
    "step1_review": "script_plan_review",
    "stale_step1_revision": "stale_script_plan_revision",
    "stale_step1_rebuilt_revision": "stale_script_plan_rebuilt_revision",
}

#: 剧集脚本 ``metadata`` 上的字段改名。
_SCRIPT_METADATA_FIELD_RENAMES: dict[str, str] = {"step1_revision": "script_plan_revision"}

#: 产物 key 里编码的 kind 值改名（见 ``lib.artifact_manifest.ArtifactKind``）。
_ARTIFACT_KIND_RENAMES: dict[str, str] = {"episode-step1": "episode-script-plan"}

_MANIFEST_FILENAME = ".arcreel_artifacts.json"


def _rename_fields(payload: dict[str, Any], renames: dict[str, str], location: str) -> dict[str, Any]:
    """按对照表改字段名；新名已占用时拒绝，不做静默取舍。"""

    migrated = copy.deepcopy(payload)
    for old, new in renames.items():
        if old not in migrated:
            continue
        if new in migrated:
            raise ValueError(f"{location} 同时存在 {old} 与 {new}，无法判定保留哪一个")
        migrated[new] = migrated.pop(old)
    return migrated


def _rename_artifact_path(artifact_path: str) -> str:
    """把清单里指向草稿文件的相对路径换成新文件名；其余路径原样返回。"""

    head, _, name = artifact_path.rpartition("/")
    new_name = DRAFT_FILE_RENAMES.get(name)
    if new_name is None:
        return artifact_path
    return f"{head}/{new_name}" if head else new_name


def migrate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """纯转换一份产物清单：改写脚本规划产物的 key 与被改名的草稿路径。"""

    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("产物清单 entries 必须是对象")
    migrated_entries: dict[str, Any] = {}
    for encoded_key, entry in entries.items():
        if not isinstance(encoded_key, str):
            raise ValueError("产物清单 key 必须是字符串")
        parts = decode_artifact_key_parts(encoded_key)
        new_key = encoded_key
        if parts is not None and parts.kind in _ARTIFACT_KIND_RENAMES:
            new_key = encode_artifact_key_parts(_ARTIFACT_KIND_RENAMES[parts.kind], parts.components)
        if new_key in migrated_entries:
            raise ValueError(f"产物清单改名后 key 冲突: {new_key}")
        new_entry = entry
        if isinstance(entry, dict) and isinstance(entry.get("artifact_path"), str):
            new_entry = {**entry, "artifact_path": _rename_artifact_path(entry["artifact_path"])}
        migrated_entries[new_key] = new_entry
    return {**payload, "entries": migrated_entries}


def _episode_entries(project_dir: Path, project: dict[str, Any]) -> list[tuple[Path | None, int]]:
    episodes = project.get("episodes")
    if episodes is None:
        return []
    if not isinstance(episodes, list):
        raise ValueError("project.episodes 必须是数组")
    result: list[tuple[Path | None, int]] = []
    for index, entry in enumerate(episodes):
        if not isinstance(entry, dict):
            raise ValueError(f"project.episodes[{index}] 必须是对象")
        episode = entry.get("episode")
        if not isinstance(episode, int) or isinstance(episode, bool) or episode <= 0:
            raise ValueError(f"project.episodes[{index}].episode 必须是正整数")
        script_file = entry.get("script_file")
        script_path = safe_join(project_dir, script_file) if isinstance(script_file, str) and script_file else None
        result.append((script_path, episode))
    return result


def _readable_object(path: Path, label: str) -> dict[str, Any] | None:
    if path.is_symlink():
        raise ValueError(f"{label} 不是普通文件")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"{label} 不是普通文件")
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 必须是对象")
    return payload


def _plan_draft_renames(project_dir: Path, episodes: list[tuple[Path | None, int]]) -> list[tuple[Path, Path]]:
    """列出待改名的草稿文件；新名已被占用时拒绝，不覆盖任何既有内容。"""

    renames: list[tuple[Path, Path]] = []
    for _script_path, episode in episodes:
        drafts_dir = project_dir / "drafts" / f"episode_{episode}"
        if not drafts_dir.is_dir():
            continue
        for old_name, new_name in DRAFT_FILE_RENAMES.items():
            source = drafts_dir / old_name
            if source.is_symlink():
                raise ProjectMigrationError(f"{old_name} 不是普通文件", episode=episode, file=old_name)
            if not source.is_file():
                continue
            target = drafts_dir / new_name
            if target.exists():
                raise ProjectMigrationError(
                    f"{old_name} 与 {new_name} 同时存在，无法判定保留哪一个",
                    episode=episode,
                    file=old_name,
                )
            renames.append((source, target))
    return renames


def plan_script_plan_draft_renames(project_dir: Path) -> tuple[tuple[Path, Path], ...]:
    """只读地列出待改名的草稿文件，供改名之前就要按新名寻址的迁移步做前置预检。

    产物清单激活（v7→v8）用当前代码解析脚本规划草稿路径，解析到的是新名；它跑在本改名之前，
    因而要么先把文件改成新名，要么让规划按旧名读取，否则激活会认定草稿不存在、不登记该产物。
    探测与落盘分开，调用方得以把两步的只读预检合并到写盘之前。新名已就位的项目返回空元组。
    """

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return ()
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    return tuple(_plan_draft_renames(project_dir, _episode_entries(project_dir, project)))


def pending_draft_rename_map(renames: Sequence[tuple[Path, Path]], project_dir: Path) -> dict[str, str]:
    """把待改名清单转成规划器要的「规范相对路径 → 当前落盘相对路径」映射。

    改名对是「当前落盘位置 → 规范位置」，映射的方向与之相反：规划按规范路径寻址，读的却是
    改名尚未落盘时的旧名。方向只在这里定义一次。
    """

    return {
        target.relative_to(project_dir).as_posix(): source.relative_to(project_dir).as_posix()
        for source, target in renames
    }


def apply_script_plan_draft_renames(renames: Sequence[tuple[Path, Path]], *, from_version: int) -> None:
    """落盘执行 :func:`plan_script_plan_draft_renames` 的结果：先备份全部来源，再逐个改名。"""

    for source, _target in renames:
        ensure_versioned_backup(source, from_version)
    for source, target in renames:
        source.replace(target)


def migrate_v9_to_v10(project_dir: Path) -> None:
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

    # 以下全程只读：任一处损坏时还没有备份或业务文件写入。
    episodes = _episode_entries(project_dir, project)
    draft_renames = _plan_draft_renames(project_dir, episodes)

    migrated_project = copy.deepcopy(project)
    raw_episodes = migrated_project.get("episodes")
    if isinstance(raw_episodes, list):
        migrated_project["episodes"] = [
            _rename_fields(entry, _EPISODE_FIELD_RENAMES, f"project.episodes[{index}]")
            if isinstance(entry, dict)
            else entry
            for index, entry in enumerate(raw_episodes)
        ]

    plans: list[tuple[Path, dict[str, Any]]] = []
    for script_path, episode in episodes:
        if script_path is None:
            continue
        label = script_path.name
        try:
            script = _readable_object(script_path, f"剧本 {label}")
        except ValueError as exc:
            raise ProjectMigrationError(str(exc), episode=episode, file=label) from exc
        if script is None:
            continue
        metadata = script.get("metadata")
        if not isinstance(metadata, dict):
            continue
        try:
            renamed = _rename_fields(metadata, _SCRIPT_METADATA_FIELD_RENAMES, f"剧本 {label}.metadata")
        except ValueError as exc:
            raise ProjectMigrationError(str(exc), episode=episode, file=label) from exc
        if renamed != metadata:
            plans.append((script_path, {**script, "metadata": renamed}))

    manifest_file = project_dir / _MANIFEST_FILENAME
    try:
        manifest = _readable_object(manifest_file, "产物清单")
    except ValueError as exc:
        raise ProjectMigrationError(str(exc), file=_MANIFEST_FILENAME) from exc
    if manifest is not None:
        try:
            plans.append((manifest_file, migrate_manifest(manifest)))
        except ValueError as exc:
            raise ProjectMigrationError(str(exc), file=_MANIFEST_FILENAME) from exc

    # 预检全部通过后才动盘：所有被改写或改名的文件先备份完，再落盘。
    for path, _payload in [*plans, (project_file, project)]:
        ensure_versioned_backup(path, 9)
    apply_script_plan_draft_renames(draft_renames, from_version=9)
    for path, payload in plans:
        atomic_write_json(path, payload)

    migrated_project["schema_version"] = _TARGET_VERSION
    atomic_write_json(project_file, migrated_project)


__all__ = [
    "DRAFT_FILE_RENAMES",
    "apply_script_plan_draft_renames",
    "migrate_manifest",
    "migrate_v9_to_v10",
    "pending_draft_rename_map",
    "plan_script_plan_draft_renames",
]
