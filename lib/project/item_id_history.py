"""项目里出现过的条目编号（``docs/adr/0090``：编号只分配、不复用）。

五个来源：剧本、版本历史（``versions/versions.json``）、产物清单、项目回收站里的删除记录（含快照里的剧本
与剪辑时间线）、剪辑时间线的片段引用。迁移与整份剧本落盘按它确定发号下界；脚本规划已占的号
由账本高水位保留，内容确认另发新号，不依赖会被清理的历史记录来判断编号能否沿用。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter, decode_artifact_key_parts
from lib.artifacts.sqlite_history import SQLiteHistory
from lib.edit_timeline.store import episode_timeline_dirs
from lib.infra.json_io import atomic_write_json
from lib.infra.path_safety import safe_join
from lib.project.project_trash import SCRIPT_ENTRY_KIND, SCRIPT_OVERWRITE_KIND, is_structure_file, trash_dir
from lib.script.item_ids import ITEM_ID_RE, ItemIdIssuer, episode_item_id_high_water, reissue_item_ids
from lib.script.script_review import formal_script_filename
from lib.script.script_skeleton import resolve_kind_items

_ID_FIELDS = frozenset({"segment_id", "scene_id", "shot_id", "unit_id"})
_ENTRY_RESOURCE_TYPES = ("storyboards", "end_frames", "videos", "reference_videos", "audio")
_MANIFEST_FILE = ".arcreel_artifacts.json"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record(found: dict[int, set[int]], value: object) -> None:
    if isinstance(value, str) and (match := ITEM_ID_RE.match(value)) is not None:
        found.setdefault(int(match.group(1)), set()).add(int(match.group(3)))


def _id_fields(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ID_FIELDS:
                yield item
            else:
                yield from _id_fields(item)
    elif isinstance(value, list):
        for item in value:
            yield from _id_fields(item)


def _json_files(directory: Path) -> Iterator[Path]:
    if directory.is_dir():
        yield from (path for path in directory.rglob("*.json") if path.is_file())


def _record_version_names(found: dict[int, set[int]], value: Mapping) -> None:
    for kind in _ENTRY_RESOURCE_TYPES:
        bucket = value.get(kind)
        for name in bucket if isinstance(bucket, dict) else ():
            _record(found, name)


def _record_manifest_names(found: dict[int, set[int]], value: Mapping) -> None:
    entries = value.get("entries")
    for key in entries if isinstance(entries, dict) else ():
        parts = decode_artifact_key_parts(key)
        for component in parts.components if parts is not None else ():
            _record(found, component)


def _collect_archived_numbers(found: dict[int, set[int]], records: Iterable[Path], *, strict: bool) -> None:
    seen: set[tuple[int, int, str]] = set()
    for record_dir in records:
        if strict and record_dir.is_symlink():
            raise ValueError("unexpected symlink in archived item ID history")
        for path in _json_files(record_dir):
            if strict and path.is_symlink():
                raise ValueError("unexpected symlink in archived item ID history")
            info = path.stat()
            identity = (info.st_dev, info.st_ino, path.name)
            if info.st_ino and identity in seen:
                continue
            seen.add(identity)
            try:
                value = json.loads(path.read_bytes()) if strict else _load(path)
            except ValueError:
                if path.is_relative_to(record_dir / "files"):
                    relative = path.relative_to(record_dir / "files").as_posix()
                    if not is_structure_file(relative):
                        continue
                    header = json.loads((record_dir / "record.json").read_bytes())
                    obj = header.get("object", {})
                    if obj.get("kind") == SCRIPT_OVERWRITE_KIND and obj.get("path") == relative:
                        continue
                raise
            if path == record_dir / "record.json" and isinstance(value, dict):
                obj = value.get("object")
                if isinstance(obj, dict) and obj.get("kind") == SCRIPT_ENTRY_KIND:
                    _record(found, obj.get("id"))
            for item_id in _id_fields(value):
                _record(found, item_id)
            if path.name == _MANIFEST_FILE and isinstance(value, dict):
                _record_manifest_names(found, value)
            if path.name == "versions.json" and isinstance(value, dict):
                _record_version_names(found, value)
            if path.name == "project.json" and isinstance(value, dict):
                for entry in value.get("episodes") or ():
                    if isinstance(entry, dict) and type(ep := entry.get("episode")) is int:
                        high = entry.get("item_id_high_water")
                        if type(high) is int and high > 0:
                            found.setdefault(ep, set()).add(high)


def used_item_numbers(project_dir: Path, *, initialize: bool = False) -> dict[int, set[int]]:
    """``{集 ID: 用过的主序号}``；读不出的文件跳过。"""

    project_dir = Path(project_dir)
    found: dict[int, set[int]] = {}

    for path in (*_json_files(project_dir / "scripts"), *_json_files(project_dir / "edit_timelines")):
        for item_id in _id_fields(_load(path)):
            _record(found, item_id)

    store = SQLiteHistory(project_dir)
    if store.path.is_file():
        for kind, name in store.resource_keys():
            if kind in _ENTRY_RESOURCE_TYPES:
                _record(found, name)
        versions = None
    else:
        versions = _load(project_dir / "versions" / "versions.json")
    if isinstance(versions, dict):
        _record_version_names(found, versions)

    manifest = _load(project_dir / _MANIFEST_FILE)
    if isinstance(manifest, dict):
        _record_manifest_names(found, manifest)

    root = trash_dir(project_dir)
    _collect_archived_numbers(found, root.iterdir() if root.is_dir() else (), strict=initialize)
    return found


def max_used_item_numbers(project_dir: Path) -> dict[int, int]:
    """``{集 ID: 用过的最大主序号}``。"""

    return {episode: max(numbers) for episode, numbers in used_item_numbers(project_dir).items() if numbers}


def _history_floors(project_dir: Path) -> Mapping[str, int]:
    baseline_path = safe_join(project_dir, ".arcreel/item-id-floors.json")
    if baseline_path != project_dir.resolve() / ".arcreel/item-id-floors.json":
        raise ValueError("unexpected symlink in item ID history index")
    root = trash_dir(project_dir)
    records = (
        {record.name for record in root.iterdir() if (record / "record.json").is_file()} if root.is_dir() else set()
    )
    try:
        index = json.loads(baseline_path.read_bytes())
    except (FileNotFoundError, ValueError):
        index = None
    if (
        isinstance(index, dict)
        and isinstance(floors := index.get("floors"), dict)
        and all(type(n) is int and n >= 0 for n in floors.values())
        and isinstance(known := index.get("records"), list)
        and all(isinstance(key, str) for key in known)
    ):
        added = records.difference(known)
        if added:
            archived: dict[int, set[int]] = {}
            _collect_archived_numbers(archived, (root / key for key in added), strict=True)
            for ep, numbers in archived.items():
                floors[str(ep)] = max(floors.get(str(ep), 0), max(numbers))
        update = records != set(known)
    else:
        floors = {str(key): max(numbers) for key, numbers in used_item_numbers(project_dir, initialize=True).items()}
        update = True
    if update:
        baseline_path.parent.mkdir(exist_ok=True)
        atomic_write_json(baseline_path, {"floors": floors, "records": sorted(records)})
    return floors


def _landing_floor(project_dir: Path, project: Mapping[str, Any], episode: int) -> int:
    floors = _history_floors(project_dir)

    found: dict[int, set[int]] = {}
    bound_name = formal_script_filename(project_dir, project, episode)
    script_paths = dict.fromkeys(
        (project_dir / "scripts" / bound_name, project_dir / "scripts" / f"episode_{episode}.json")
    )
    timeline_files = [
        path for _, directory in episode_timeline_dirs(project_dir, episode) for path in _json_files(directory)
    ]
    for path in (*script_paths, *timeline_files):
        for value in _id_fields(_load(path)):
            _record(found, value)
    store = SQLiteHistory(project_dir)
    if store.path.is_file():
        for name in store.episode_resource_names(episode, _ENTRY_RESOURCE_TYPES):
            _record(found, name)
    else:
        history = _load(project_dir / "versions" / "versions.json") or {}
        for kind in _ENTRY_RESOURCE_TYPES:
            for name in history.get(kind, {}):
                _record(found, name)
    for key in ProjectArtifactManifestAdapter(project_dir).read_snapshot().entries:
        if key.episode_number == episode:
            for value in key.components:
                _record(found, value)
    return max(episode_item_id_high_water(project, episode), floors.get(str(episode), 0), *found.get(episode, ()))


def issue_landing_item_ids(project_dir: Path, project: Mapping[str, Any], script: dict[str, Any]) -> dict[str, str]:
    """生成产出落盘前给整份剧本的条目发号，就地改写并返回 ``{原 ID: 新 ID}``。

    每次整份转出或生成都发新号，取在本集账本记录与五个来源里用过的最大号之后。
    """

    episode = script.get("episode")
    if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1:
        raise ValueError("script episode must be a positive integer before issuing item ids")
    items, id_field, kind = resolve_kind_items(script)
    entries = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    floor = _landing_floor(project_dir, project, episode)
    issuer = ItemIdIssuer(episode, kind, floor)
    return reissue_item_ids(entries, id_field, issuer)


__all__ = ["issue_landing_item_ids", "max_used_item_numbers", "used_item_numbers"]
