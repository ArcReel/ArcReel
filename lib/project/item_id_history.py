"""项目里出现过的条目编号（``docs/adr/0090``：编号只分配、不复用）。

五个来源：剧本、版本历史（``versions/versions.json``）、产物清单、项目回收站里的删除记录（含快照里的剧本
与剪辑时间线）、剪辑时间线的片段引用。迁移与整份剧本落盘按它确定发号下界；脚本规划已占的号
由账本高水位保留，内容确认另发新号，不依赖会被清理的历史记录来判断编号能否沿用。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import decode_artifact_key_parts
from lib.project.project_trash import trash_dir
from lib.script.item_ids import ITEM_ID_RE, ItemIdIssuer, episode_item_id_high_water, reissue_item_ids
from lib.script.script_skeleton import resolve_kind_items

#: 条目编号所在的字段：剧本条目的 id 字段与剪辑片段的 ``unit_id``。
_ID_FIELDS = frozenset({"segment_id", "scene_id", "shot_id", "unit_id"})
#: 版本历史里以条目编号为键的资源类型（与 ``script_entry_cleanup.ENTRY_MEDIA_RESOURCE_TYPES`` 一致）。
_ENTRY_RESOURCE_TYPES = ("storyboards", "end_frames", "videos", "reference_videos", "audio")
_MANIFEST_FILE = ".arcreel_artifacts.json"
_SCRIPT_ENTRY_KIND = "script_entry"


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


def used_item_numbers(project_dir: Path) -> dict[int, set[int]]:
    """``{集 ID: 用过的主序号}``；读不出的文件跳过。"""

    project_dir = Path(project_dir)
    found: dict[int, set[int]] = {}

    for path in (*_json_files(project_dir / "scripts"), *_json_files(project_dir / "edit_timelines")):
        for item_id in _id_fields(_load(path)):
            _record(found, item_id)

    versions = _load(project_dir / "versions" / "versions.json")
    if isinstance(versions, dict):
        for resource_type in _ENTRY_RESOURCE_TYPES:
            resources = versions.get(resource_type)
            for resource_id in resources if isinstance(resources, dict) else ():
                _record(found, resource_id)

    manifest = _load(project_dir / _MANIFEST_FILE)
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    for key in entries if isinstance(entries, dict) else ():
        parts = decode_artifact_key_parts(key) if isinstance(key, str) else None
        for component in parts.components if parts is not None else ():
            _record(found, component)

    root = trash_dir(project_dir)
    for record_dir in root.iterdir() if root.is_dir() else ():
        record = _load(record_dir / "record.json")
        obj = record.get("object") if isinstance(record, dict) else None
        if isinstance(obj, dict) and obj.get("kind") == _SCRIPT_ENTRY_KIND:
            _record(found, obj.get("id"))
        # 快照里改写前的剧本、删掉的集与剪辑时间线
        for path in _json_files(record_dir):
            for item_id in _id_fields(_load(path)):
                _record(found, item_id)

    return found


def max_used_item_numbers(project_dir: Path) -> dict[int, int]:
    """``{集 ID: 用过的最大主序号}``。"""

    return {episode: max(numbers) for episode, numbers in used_item_numbers(project_dir).items() if numbers}


def issue_landing_item_ids(project_dir: Path, project: Mapping[str, Any], script: dict[str, Any]) -> dict[str, str]:
    """生成产出落盘前给整份剧本的条目发号，就地改写并返回 ``{原 ID: 新 ID}``。

    每次整份转出或生成都发新号，取在本集账本记录与五个来源里用过的最大号之后。
    """

    episode = script.get("episode")
    if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1:
        raise ValueError("script episode must be a positive integer before issuing item ids")
    items, id_field, kind = resolve_kind_items(script)
    entries = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    used = used_item_numbers(project_dir).get(episode, set())
    floor = max(episode_item_id_high_water(project, episode), *used, 0)
    issuer = ItemIdIssuer(episode, kind, floor)
    return reissue_item_ids(entries, id_field, issuer)


__all__ = ["issue_landing_item_ids", "max_used_item_numbers", "used_item_numbers"]
