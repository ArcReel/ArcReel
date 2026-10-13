"""迁移链在第一次规划产物目标态之前，把过去正常流程写出的旧形态规整成当前代码认得的形态（ADR 0098）。

v6→v7 的剧本改写、产物目标态规划（v7→v8 起的各步与归档导入）与 v8→v9、v9→v10、v14→v15 曾把
这些形态当作数据错误拒绝整个项目；它们其实是旧版本代码自己写出来的。这里按确定规则规整，有取舍时另一份留作永久保留副本
（:func:`lib.project.project_migrations.backups.set_aside_as_kept_copy`），每处改动都作为
:class:`MigrationAdjustment` 进迁移报告：

- **账本里同一集号出现多条**：留一条（绑定的剧本在盘上、且优先规范路径的那条，都不在时留第一条），
  其余从账本去掉；它们绑定的剧本文件原样留在盘上，改前的 ``project.json`` 留作保留副本。
- **不同集号绑到同一份剧本**（旧版按剧本自报的集号同步账本）：剧本留给它自己声明的那一集，
  都不是时留给账本里靠前的那一集；其余各集改绑到自己的规范路径。
- **集绑定不是 ``scripts/episode_N.json``**（schema < 15；旧版 SSE 索引同步登记的任意剧本）：
  剧本挪到规范路径并改绑。规范路径上已有另一份内容时，那一份先挪成保留副本。产物清单里该集剧本
  的登记、版本记录里的 ``execution_script_file`` 与宫格记录里的 ``script_file`` 随之改写，已登记
  的产物不因改绑丢失归属。
- **剧本顶层的集号缺失或与绑定不符**（旧版同步与保存都接受缺集号的剧本）：以账本绑定为准写回。
- **同一资产表里名字归一后相同**（去两端空白、Unicode NFC），或名字归一后为空：留第一条，其余从
  资产表去掉；资产图文件不动，改前的 ``project.json`` 留作保留副本。
- **剧本条目缺 id 或 id 重复**：缺的补新 id，重复的从第二条起换新 id（取号规则同新增条目）；换号
  条目上指向原 id 规范媒体路径的产物引用清空——那些文件属于保留原 id 的条目。
- **两集里有同号条目**：媒体与登记归 id 前缀所指的那一集，对不上时归账本里靠前的那一集；另一集的
  条目在本集换新 id，不带走媒体。
- **新旧草稿名并存**（schema < 10）：以新名为准，旧名文件挪成保留副本。

读不出的 ``project.json`` 或剧本不在这里处理：之后的迁移步照旧拒绝整个项目。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import MANIFEST_FILENAME, ArtifactKey, ProjectArtifactManifestAdapter
from lib.episode.episode_paths import episode_drafts_dir, episode_script_relpath
from lib.infra.json_io import atomic_write_json
from lib.infra.path_safety import try_safe_join
from lib.project.asset_types import ASSET_SPECS, asset_name_comparison_key
from lib.project.project_migration_report import MigrationAdjustment
from lib.project.project_migrations.backups import ensure_versioned_backup, set_aside_as_kept_copy
from lib.project.project_migrations.v9_to_v10_script_plan_naming import DRAFT_FILE_RENAMES
from lib.project.resource_paths import resource_relative_path
from lib.script.script_editor import ScriptEditError, new_item_id, resolve_items

logger = logging.getLogger(__name__)

#: 从这个版本起账本与资产名称空间都已就位；规整先于 v6→v7（它按账本逐集改写剧本）与之后的
#: 每一次产物目标态规划。
NORMALIZE_FROM_SCHEMA_VERSION = 6
_CANONICAL_BINDING_SCHEMA_VERSION = 15
_DRAFT_RENAME_SCHEMA_VERSION = 10

#: 条目产物字段 → 资源类型；参考生视频单元的视频在 ``reference_videos`` 下。
_MEDIA_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("storyboard_image", "storyboards", "storyboards"),
    ("video_clip", "videos", "reference_videos"),
    ("narration_audio", "audio", "audio"),
)


@dataclass
class _Work:
    project_dir: Path
    schema_version: int
    project: dict[str, Any]
    project_changed: bool = False
    adjusted: list[MigrationAdjustment] = field(default_factory=list)
    #: 改动 ``project.json`` 的规整项；保留副本路径要等写盘时才知道。
    project_adjustments: list[MigrationAdjustment] = field(default_factory=list)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.project_dir).as_posix()


def _episode_entries(project: dict[str, Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    episodes = project.get("episodes")
    if not isinstance(episodes, list):
        return
    for entry in episodes:
        if not isinstance(entry, dict):
            continue
        episode = entry.get("episode")
        if type(episode) is int and episode >= 1:
            yield episode, entry


def _bound_path(project_dir: Path, binding: object) -> Path | None:
    if not isinstance(binding, str) or not binding:
        return None
    return try_safe_join(project_dir, binding.replace("\\", "/"))


def _load_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _drop_duplicate_episodes(work: _Work) -> None:
    episodes = work.project.get("episodes")
    if not isinstance(episodes, list):
        return
    groups: dict[int, list[dict[str, Any]]] = {}
    for episode, entry in _episode_entries(work.project):
        groups.setdefault(episode, []).append(entry)
    dropped: list[tuple[int, dict[str, Any]]] = []
    for episode, entries in groups.items():
        if len(entries) < 2:
            continue
        canonical = work.project_dir / episode_script_relpath(episode)

        def rank(entry: dict[str, Any], canonical: Path = canonical) -> int:
            path = _bound_path(work.project_dir, entry.get("script_file"))
            if path is None or not path.is_file():
                return 2
            return 0 if path == canonical else 1

        keeper = min(entries, key=rank)
        dropped.extend((episode, entry) for entry in entries if entry is not keeper)
    if not dropped:
        return
    dropped_ids = {id(entry) for _episode, entry in dropped}
    work.project["episodes"] = [entry for entry in episodes if id(entry) not in dropped_ids]
    work.project_changed = True
    for episode, entry in dropped:
        binding = entry.get("script_file")
        work.project_adjustments.append(
            MigrationAdjustment(
                code="duplicate_episode_entry_removed",
                episode=episode,
                file=binding if isinstance(binding, str) and binding else None,
            )
        )


def _split_shared_bindings(work: _Work) -> None:
    """不同集号绑到同一份剧本时，剧本留给它自己声明的那一集（都不是时留给账本里靠前的那一集），
    其余各集改绑到自己的规范路径；那里没有文件时该集就是还没有剧本。"""

    groups: dict[Path, list[tuple[int, dict[str, Any]]]] = {}
    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is not None:
            groups.setdefault(path, []).append((episode, entry))
    for path, sharers in groups.items():
        if len({episode for episode, _entry in sharers}) < 2:
            continue
        script = _load_object(path) if path.is_file() and not path.is_symlink() else None
        claimed = script.get("episode") if script is not None else None
        owner = next((sharer for sharer in sharers if sharer[0] == claimed), sharers[0])
        for episode, entry in sharers:
            if episode == owner[0]:
                continue
            raw = entry["script_file"]
            canonical_rel = episode_script_relpath(episode)
            entry["script_file"] = canonical_rel
            work.project_changed = True
            work.project_adjustments.append(
                MigrationAdjustment(
                    code="shared_script_binding_split",
                    episode=episode,
                    file=canonical_rel,
                    params={"from": raw, "to": canonical_rel, "owner": str(owner[0])},
                )
            )


def _correct_script_episodes(work: _Work) -> None:
    """剧本顶层的集号以账本绑定为准：缺了就补上，与绑定不符就改成绑定的集号。"""

    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is None or not path.is_file() or path.is_symlink():
            continue
        script = _load_object(path)
        if script is None:
            continue
        current = script.get("episode")
        if type(current) is int and current == episode:
            continue
        script["episode"] = episode
        ensure_versioned_backup(path, work.schema_version)
        atomic_write_json(path, script)
        work.adjusted.append(
            MigrationAdjustment(
                code="script_episode_corrected",
                episode=episode,
                file=work.rel(path),
                params={"from": "" if current is None else str(current), "to": str(episode)},
            )
        )


def _rewrite_script_references(work: _Work, episode: int, old_paths: set[str], canonical: str) -> None:
    """改绑后让版本记录与宫格记录里指向旧剧本的引用跟着改到规范路径。"""

    def points_at_old(value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        normalized = value.replace("\\", "/").removeprefix("./")
        return normalized in old_paths or f"scripts/{normalized}" in old_paths

    versions_path = work.project_dir / "versions" / "versions.json"
    versions = _load_object(versions_path) if versions_path.is_file() else None
    if versions is not None:
        changed = False
        for bucket in versions.values():
            if not isinstance(bucket, dict):
                continue
            for resource in bucket.values():
                records = resource.get("versions") if isinstance(resource, dict) else None
                for record in records if isinstance(records, list) else []:
                    if isinstance(record, dict) and points_at_old(record.get("execution_script_file")):
                        record["execution_script_file"] = canonical
                        changed = True
        if changed:
            ensure_versioned_backup(versions_path, work.schema_version)
            atomic_write_json(versions_path, versions)

    grids_dir = work.project_dir / "grids"
    if grids_dir.is_dir() and not grids_dir.is_symlink():
        for path in sorted(grids_dir.glob("grid_*.json")):
            record = _load_object(path)
            if record is None or record.get("episode") != episode or not points_at_old(record.get("script_file")):
                continue
            record["script_file"] = canonical
            ensure_versioned_backup(path, work.schema_version)
            atomic_write_json(path, record)


def _rebind_manifest_entry(work: _Work, episode: int, canonical: str) -> None:
    if not (work.project_dir / MANIFEST_FILENAME).is_file():
        return
    adapter = ProjectArtifactManifestAdapter(work.project_dir)
    key = ArtifactKey.episode_script(episode)
    stored = adapter.get_entry(key)
    if stored is None or stored.artifact_path == canonical:
        return
    ensure_versioned_backup(work.project_dir / MANIFEST_FILENAME, work.schema_version)
    replacement = replace(stored, artifact_path=canonical)
    if not adapter.replace_entries_if_matches_atomically(expected={key: stored}, replacements={key: replacement}):
        raise RuntimeError("artifact manifest changed while rebinding an episode script")


def _canonicalize_bindings(work: _Work) -> None:
    if work.schema_version >= _CANONICAL_BINDING_SCHEMA_VERSION:
        return
    for episode, entry in _episode_entries(work.project):
        raw = entry.get("script_file")
        canonical_rel = episode_script_relpath(episode)
        if not isinstance(raw, str) or not raw or raw == canonical_rel:
            continue
        canonical = work.project_dir / canonical_rel
        bound = _bound_path(work.project_dir, raw)
        backup: str | None = None
        if bound is not None and bound != canonical and bound.is_file():
            if canonical.is_file() and canonical.read_bytes() != bound.read_bytes():
                backup = work.rel(set_aside_as_kept_copy(canonical, work.schema_version))
            canonical.parent.mkdir(parents=True, exist_ok=True)
            bound.replace(canonical)
        old_paths = {raw.replace("\\", "/").removeprefix("./")}
        if bound is not None:
            old_paths.add(work.rel(bound))
        _rebind_manifest_entry(work, episode, canonical_rel)
        _rewrite_script_references(work, episode, old_paths, canonical_rel)
        entry["script_file"] = canonical_rel
        work.project_changed = True
        work.adjusted.append(
            MigrationAdjustment(
                code="script_binding_canonicalized",
                episode=episode,
                file=canonical_rel,
                backup=backup,
                params={"from": raw, "to": canonical_rel},
            )
        )


def _drop_duplicate_assets(work: _Work) -> None:
    for asset_type, spec in ASSET_SPECS.items():
        bucket = work.project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        seen: dict[str, str] = {}
        removed: list[tuple[str, str]] = []
        for name in list(bucket):
            if not isinstance(name, str):
                continue
            key = asset_name_comparison_key(name)
            if key and key not in seen:
                seen[key] = name
                continue
            removed.append((name, seen.get(key, "")))
        for name, kept in removed:
            del bucket[name]
            work.project_changed = True
            work.project_adjustments.append(
                MigrationAdjustment(
                    code="duplicate_asset_removed",
                    params={"asset_type": asset_type, "name": name, "kept": kept},
                )
            )


#: 条目 id 的集号前缀（与 ``script_editor`` 的条目 id 形态一致，含 ``_N`` 拆分后缀）。
_ITEM_ID_EPISODE = re.compile(r"\AE(\d+)[SU]\d+(?:_\d+)?\Z")
#: ``new_item_id`` 取出的号：集号、字母、序号。
_ITEM_ID_PARTS = re.compile(r"\AE(\d+)([SU])(\d+)\Z")


def _id_owner_episode(item_id: str) -> int | None:
    match = _ITEM_ID_EPISODE.match(item_id)
    return int(match.group(1)) if match else None


def _fresh_item_id(script: dict[str, Any], taken: set[str]) -> str:
    """按新增条目的取号规则取号，并避开其他集已经在用的 id。"""

    candidate = new_item_id(script)
    match = _ITEM_ID_PARTS.match(candidate)
    if match is None:
        return candidate
    prefix, number = f"E{match.group(1)}{match.group(2)}", int(match.group(3))
    width = len(match.group(3)) if match.group(2) == "S" else 0
    while candidate in taken:
        number += 1
        candidate = f"{prefix}{number:0{width}d}"
    return candidate


def _repair_item_ids(work: _Work) -> None:
    """条目 id 在剧本内缺失或重复、或与另一集的条目同号时换号。

    产物路径只由条目 id 决定，两集同号的条目会认领同一个媒体文件。媒体与登记归 id 前缀 ``E<n>``
    所指的那一集，前缀对不上任何一个同号集时归账本里靠前的那一集；另一集的条目在本集换新 id、
    不带走媒体。
    """

    scripts: list[tuple[int, Path, dict[str, Any], list[Any], str, str]] = []
    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is None or not path.is_file() or path.is_symlink():
            continue
        script = _load_object(path)
        if script is None:
            continue
        try:
            items, id_field, kind = resolve_items(script)
        except ScriptEditError:
            continue
        scripts.append((episode, path, script, items, id_field, kind))

    holders: dict[str, list[int]] = {}
    for episode, _path, _script, items, id_field, _kind in scripts:
        for item in items:
            current = item.get(id_field) if isinstance(item, dict) else None
            if isinstance(current, str) and current and episode not in holders.setdefault(current, []):
                holders[current].append(episode)
    owners: dict[str, int] = {}
    for item_id, episodes in holders.items():
        if len(episodes) > 1:
            prefixed = _id_owner_episode(item_id)
            owners[item_id] = prefixed if prefixed in episodes else episodes[0]
    taken = set(holders)

    for episode, path, script, items, id_field, kind in scripts:
        seen: set[str] = set()
        changes: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            current = item.get(id_field)
            foreign = isinstance(current, str) and owners.get(current, episode) != episode
            if isinstance(current, str) and current and current not in seen and not foreign:
                seen.add(current)
                continue
            old_id = current if isinstance(current, str) else ""
            # 先清掉坏 id 再取号：取号只看现存条目，坏 id 不该参与顺延。
            item.pop(id_field, None)
            new_id = _fresh_item_id(script, taken)
            item[id_field] = new_id
            seen.add(new_id)
            taken.add(new_id)
            if old_id:
                _release_media_of(item, old_id, kind)
            changes.append((old_id, new_id))
        if not changes:
            continue
        ensure_versioned_backup(path, work.schema_version)
        atomic_write_json(path, script)
        for old_id, new_id in changes:
            work.adjusted.append(
                MigrationAdjustment(
                    code="item_id_reassigned",
                    episode=episode,
                    file=work.rel(path),
                    params={"from": old_id, "to": new_id},
                )
            )


def _release_media_of(item: dict[str, Any], old_id: str, kind: str) -> None:
    assets = item.get("generated_assets")
    if not isinstance(assets, dict):
        return
    for field_name, storyboard_type, unit_type in _MEDIA_FIELDS:
        resource_type = unit_type if kind == "video_units" else storyboard_type
        if assets.get(field_name) == resource_relative_path(resource_type, old_id):
            assets[field_name] = None


def _resolve_draft_name_conflicts(work: _Work) -> None:
    if work.schema_version >= _DRAFT_RENAME_SCHEMA_VERSION:
        return
    for episode, _entry in _episode_entries(work.project):
        drafts_dir = episode_drafts_dir(work.project_dir, episode)
        if not drafts_dir.is_dir() or drafts_dir.is_symlink():
            continue
        for old_name, new_name in DRAFT_FILE_RENAMES.items():
            old, new = drafts_dir / old_name, drafts_dir / new_name
            if old.is_symlink() or new.is_symlink() or not (old.is_file() and new.is_file()):
                continue
            kept = set_aside_as_kept_copy(old, work.schema_version)
            work.adjusted.append(
                MigrationAdjustment(
                    code="draft_name_conflict_resolved",
                    episode=episode,
                    file=work.rel(new),
                    backup=work.rel(kept),
                )
            )


def normalize_legacy_shapes(project_dir: Path, *, schema_version: int) -> tuple[MigrationAdjustment, ...]:
    """规整 ``project_dir`` 里的旧形态并返回做过的改动；无事可做时返回空元组、不写盘。"""

    project_file = project_dir / "project.json"
    project = _load_object(project_file)
    if project is None:
        return ()
    work = _Work(project_dir=project_dir, schema_version=schema_version, project=project)
    _drop_duplicate_episodes(work)
    _split_shared_bindings(work)
    _canonicalize_bindings(work)
    _correct_script_episodes(work)
    _drop_duplicate_assets(work)
    _repair_item_ids(work)
    _resolve_draft_name_conflicts(work)
    if work.project_changed:
        backup = None
        if work.project_adjustments:
            backup = work.rel(set_aside_as_kept_copy(project_file, schema_version, copy=True))
        else:
            ensure_versioned_backup(project_file, schema_version)
        atomic_write_json(project_file, work.project)
        work.adjusted[:0] = [item.model_copy(update={"backup": backup}) for item in work.project_adjustments]
    if work.adjusted:
        logger.info("规整旧形态 %s：%d 处", project_dir.name, len(work.adjusted))
    return tuple(work.adjusted)


__all__ = ["NORMALIZE_FROM_SCHEMA_VERSION", "normalize_legacy_shapes"]
