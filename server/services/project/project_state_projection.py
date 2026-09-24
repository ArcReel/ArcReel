"""项目状态投影：把项目当前状态归一成可比对快照，并把两份快照的差异描述成项目事件。

纯函数，不读盘：读取项目状态的 IO 归事件服务。投影规则按注册表派生、整条目比对
（见 ``docs/adr/0085``）：

- 资产按 ``ASSET_SPECS`` 的每一行取整条；开启衍生能力的类型，衍生单独成条，身份写作
  ``本体/衍生``，本体条目比对时排除衍生表。
- 集条目、概述、剧本条目、剧本层字段取整条；项目设置取 ``project.json`` 去掉资产表、集、
  概述与 ``metadata`` 后的全部内容。
- 排除两类：每次写盘都会变的 ``metadata``；剧本条目的 ``generated_assets``，它只用于判定
  分镜图 / 视频就绪，其余变化归显式事件。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lib.infra.content_digest import canonical_json_digest
from lib.project.asset_derivatives import DERIVATIVE_ID_SEPARATOR, derivative_artifact_id, derivative_table
from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, AssetSpec
from lib.project.project_change_hints import build_change_label
from lib.script.script_models import get_generated_assets
from lib.script.script_skeleton import (
    SKELETON_ANCHOR_TYPES,
    SKELETON_ENTITY_TYPES,
    SKELETON_ITEM_LABEL_KEYS,
    SKELETONS,
    resolve_kind_items,
)

logger = logging.getLogger(__name__)

_METADATA_KEY = "metadata"
_GENERATED_ASSETS_KEY = "generated_assets"

# 剧本里不属于「剧本层字段」的顶层键：当前骨架的条目数组单独按条目比对；其他骨架的残留数组
# （如参考生视频剧本里遗留的 shots）不参与比对，否则其中的生成产物回写会被报成集更新。
_NON_DOCUMENT_KEYS = frozenset(SKELETONS) | {_METADATA_KEY}

# project.json 里不属于「项目设置」的顶层键：它们各自单独投影，或不参与比对。
_NON_SETTINGS_KEYS = frozenset(
    {spec.bucket_key for spec in ASSET_SPECS.values()} | {"episodes", "overview", _METADATA_KEY}
)


@dataclass(frozen=True)
class ProjectState:
    """项目当前状态：``project.json`` 与 ``scripts/`` 下每份可解析剧本（文件名 → 剧本）。"""

    project: Mapping[str, Any]
    scripts: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class ProjectSnapshot:
    """归一后的项目状态；``fingerprint`` 相同即两份快照不会差分出任何项目事件。"""

    data: dict[str, Any]
    fingerprint: str


def build_snapshot(state: ProjectState) -> ProjectSnapshot:
    project = state.project
    data = {
        "assets": _asset_entries(project),
        "derivatives": _derivative_entries(project),
        "settings": {key: value for key, value in project.items() if key not in _NON_SETTINGS_KEYS},
        "overview": project.get("overview"),
        "episodes": _episode_entries(project),
        "scripts": {name: _script_projection(script) for name, script in sorted(state.scripts.items())},
    }
    return ProjectSnapshot(data=data, fingerprint=canonical_json_digest(data))


def diff_snapshots(previous: ProjectSnapshot, current: ProjectSnapshot) -> list[dict[str, Any]]:
    before, after = previous.data, current.data
    changes: list[dict[str, Any]] = []
    for spec in ASSET_SPECS.values():
        changes.extend(
            _diff_assets(
                before["assets"].get(spec.asset_type, {}),
                after["assets"].get(spec.asset_type, {}),
                asset_type=spec.asset_type,
                pane=spec.bucket_key,
                label_key=f"named_entity_{spec.asset_type}",
            )
        )
        if spec.supports_derivatives:
            changes.extend(
                _diff_assets(
                    before["derivatives"].get(spec.asset_type, {}),
                    after["derivatives"].get(spec.asset_type, {}),
                    asset_type=spec.asset_type,
                    pane=spec.bucket_key,
                    label_key=f"named_entity_{spec.asset_type}_derivative",
                )
            )
    if before["settings"] != after["settings"]:
        changes.append(_change("project", "updated", "project", "project_settings", focus=None, important=False))
    if before["overview"] != after["overview"]:
        changes.append(_change("overview", "updated", "overview", "overview", focus=None, important=False))
    changes.extend(
        _dedupe_episode_updates(
            _diff_episodes(before["episodes"], after["episodes"]) + _diff_scripts(before["scripts"], after["scripts"])
        )
    )
    return changes


def _bucket_entries(project: Mapping[str, Any], spec: AssetSpec) -> list[tuple[str, dict[str, Any]]]:
    """资产表里结构合法的条目，按名排序；畸形的表或条目按缺失处理（结构错误由校验层报告）。"""
    bucket = project.get(spec.bucket_key)
    if not isinstance(bucket, dict):
        return []
    return [(name, entry) for name, entry in sorted(bucket.items()) if isinstance(entry, dict)]


def _asset_entries(project: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        spec.asset_type: {
            name: {key: value for key, value in entry.items() if key != DERIVATIVES_FIELD}
            for name, entry in _bucket_entries(project, spec)
        }
        for spec in ASSET_SPECS.values()
    }


def _derivative_entries(project: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        spec.asset_type: {
            derivative_artifact_id(owner, name): derivative
            for owner, entry in _bucket_entries(project, spec)
            for name, derivative in sorted(derivative_table(entry).items())
        }
        for spec in ASSET_SPECS.values()
        if spec.supports_derivatives
    }


def _episode_entries(project: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    episodes = [
        entry
        for entry in project.get("episodes") or []
        if isinstance(entry, dict) and isinstance(entry.get("episode"), int)
    ]
    return {str(entry["episode"]): entry for entry in sorted(episodes, key=lambda entry: entry["episode"])}


def _script_projection(script: Mapping[str, Any]) -> dict[str, Any]:
    """剧本拆成条目表与剧本层字段；骨架种类按数据形状取证（``resolve_kind_items``）。"""
    raw_items, id_field, kind = resolve_kind_items(dict(script))
    if kind not in script:
        raw_items = []
    elif not isinstance(raw_items, list):
        logger.warning("剧本条目字段非列表，按空快照处理 kind=%s type=%s", kind, type(raw_items).__name__)
        raw_items = []

    items: dict[str, Any] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get(id_field) or "")
        if item_id:
            items[item_id] = item

    return {
        "episode": script.get("episode"),
        "kind": kind,
        "document": {key: value for key, value in script.items() if key not in _NON_DOCUMENT_KEYS},
        "items": items,
    }


def _diff_assets(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    asset_type: str,
    pane: str,
    label_key: str,
) -> list[dict[str, Any]]:
    def focus(entity_id: str) -> dict[str, Any]:
        # 衍生没有自己的卡片，定位到本体卡；资产名不含分隔符，本体的 id 原样保留。
        owner = entity_id.partition(DERIVATIVE_ID_SEPARATOR)[0]
        return {"pane": pane, "anchor_type": asset_type, "anchor_id": owner}

    changes = [
        _change(asset_type, "created", name, label_key, {"id": name}, focus=focus(name), important=True)
        for name in sorted(current.keys() - previous.keys())
    ]
    changes.extend(
        _change(asset_type, "deleted", name, label_key, {"id": name}, focus=None, important=False)
        for name in sorted(previous.keys() - current.keys())
    )
    changes.extend(
        _change(asset_type, "updated", name, label_key, {"id": name}, focus=focus(name), important=True)
        for name in sorted(previous.keys() & current.keys())
        if previous[name] != current[name]
    )
    return changes


def _episode_change(action: str, episode: int, *, script_file: object, important: bool) -> dict[str, Any]:
    return _change(
        "episode",
        action,
        str(episode),
        "episode",
        {"episode": episode},
        focus=None,
        important=important,
        script_file=script_file if isinstance(script_file, str) else None,
        episode=episode,
    )


def _diff_episodes(previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[dict[str, Any]]:
    def entry_change(action: str, entry: Mapping[str, Any], *, important: bool) -> dict[str, Any]:
        return _episode_change(action, entry["episode"], script_file=entry.get("script_file"), important=important)

    changes = [
        entry_change("created", current[key], important=True)
        for key in sorted(current.keys() - previous.keys(), key=int)
    ]
    changes.extend(
        entry_change("deleted", previous[key], important=False)
        for key in sorted(previous.keys() - current.keys(), key=int)
    )
    changes.extend(
        entry_change("updated", current[key], important=True)
        for key in sorted(previous.keys() & current.keys(), key=int)
        if previous[key] != current[key]
    )
    return changes


def _diff_scripts(previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[dict[str, Any]]:
    """剧本的条目差异与剧本层差异；剧本层差异（含剧本文件新出现或消失）报所属集更新。"""
    changes: list[dict[str, Any]] = []
    for script_file in sorted(previous.keys() | current.keys()):
        if script_file not in previous or script_file not in current:
            # 剧本文件整份出现或消失，不逐条报条目增删。
            present = current[script_file] if script_file in current else previous[script_file]
            changes.extend(_script_level_change(present, script_file))
            continue
        before, after = previous[script_file], current[script_file]
        before_items, after_items = before["items"], after["items"]
        changes.extend(
            _item_change(after, script_file, item_id, "created", important=True)
            for item_id in sorted(after_items.keys() - before_items.keys())
        )
        changes.extend(
            _item_change(before, script_file, item_id, "deleted", important=False)
            for item_id in sorted(before_items.keys() - after_items.keys())
        )
        for item_id in sorted(before_items.keys() & after_items.keys()):
            before_item, after_item = before_items[item_id], after_items[item_id]
            before_assets, after_assets = get_generated_assets(before_item), get_generated_assets(after_item)
            for field, action in (("storyboard_image", "storyboard_ready"), ("video_clip", "video_ready")):
                if after_assets.get(field) and not before_assets.get(field):
                    changes.append(_item_change(after, script_file, item_id, action, important=True))
            if _without(before_item, _GENERATED_ASSETS_KEY) != _without(after_item, _GENERATED_ASSETS_KEY):
                changes.append(_item_change(after, script_file, item_id, "updated", important=True))

        if before["document"] != after["document"]:
            changes.extend(_script_level_change(after, script_file))
    return changes


def _script_level_change(script: Mapping[str, Any], script_file: str) -> list[dict[str, Any]]:
    episode = script["episode"]
    if not isinstance(episode, int):
        return []
    # script_file 与集条目同口径（相对项目根），同一集两路报出的集更新载荷一致。
    return [_episode_change("updated", episode, script_file=f"scripts/{script_file}", important=True)]


def _item_change(
    script: Mapping[str, Any], script_file: str, item_id: str, action: str, *, important: bool
) -> dict[str, Any]:
    kind = script["kind"]
    episode = script["episode"]
    focus = (
        None
        if action == "deleted"
        else {"pane": "episode", "episode": episode, "anchor_type": SKELETON_ANCHOR_TYPES[kind], "anchor_id": item_id}
    )
    return _change(
        SKELETON_ENTITY_TYPES[kind],
        action,
        item_id,
        SKELETON_ITEM_LABEL_KEYS[kind],
        {"id": item_id},
        focus=focus,
        important=important,
        script_file=script_file,
        episode=episode,
    )


def _change(
    entity_type: str,
    action: str,
    entity_id: str,
    label_key: str,
    label_params: dict[str, Any] | None = None,
    *,
    focus: dict[str, Any] | None,
    important: bool,
    script_file: str | None = None,
    episode: object = None,
) -> dict[str, Any]:
    change = {
        "entity_type": entity_type,
        "action": action,
        "entity_id": entity_id,
        **build_change_label(label_key, **(label_params or {})),
        "focus": focus,
        "important": important,
    }
    if script_file:
        change["script_file"] = script_file
    if isinstance(episode, int):
        change["episode"] = episode
    return change


def _dedupe_episode_updates(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一集已由更早的事件报过（新增 / 删除 / 更新）时，丢掉后续的「集更新」。

    一次写入可能同时改到集条目与该集剧本：集条目的差异排在前面，剧本层的集更新让给它。
    """
    reported: set[str] = set()
    unique: list[dict[str, Any]] = []
    for change in changes:
        if change["entity_type"] == "episode":
            if change["action"] == "updated" and change["entity_id"] in reported:
                continue
            reported.add(change["entity_id"])
        unique.append(change)
    return unique


def _without(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in mapping.items() if name != key}
