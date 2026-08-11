"""v6→v7：把广告参考路线迁移为自包含 ``video_units``。"""

from __future__ import annotations

import copy
import json
import shutil
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from lib.asset_types import asset_name_comparison_key
from lib.json_io import atomic_write_json, load_json
from lib.path_safety import safe_join
from lib.speech_composition import SpeechComposition, SpeechProblemCode, adapt_video_unit

_REFERENCE_TYPES = (
    ("product", "products_in_shot"),
    ("character", "characters_in_shot"),
    ("scene", "scenes"),
    ("prop", "props"),
)
_TRANSITIONS = frozenset({"cut", "fade", "dissolve"})


def _positive_seconds(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _strings(value: object) -> Iterable[str]:
    """按旧对象字段顺序产出非空文本，不推断或改写其语义。"""
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key != "dialogue":
                yield from _strings(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _shot_references(shots: list[dict[str, Any]]) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    seen_names: set[str] = set()
    # 产品绝对优先；其余类型依照统一解析优先级。每类内部保持镜头与字段原顺序。
    for asset_type, field in _REFERENCE_TYPES:
        for shot in shots:
            names = shot.get(field)
            if not isinstance(names, list):
                continue
            for raw_name in names:
                if not isinstance(raw_name, str) or not raw_name.strip():
                    continue
                name = asset_name_comparison_key(raw_name)
                if name in seen_names:
                    continue
                seen_names.add(name)
                references.append({"type": asset_type, "name": name})
    return references


def _shot_text(shot: dict[str, Any]) -> str:
    mentions: list[str] = []
    for _asset_type, field in _REFERENCE_TYPES:
        values = shot.get(field)
        if not isinstance(values, list):
            continue
        for raw_name in values:
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            mention = f"@[{asset_name_comparison_key(raw_name)}]"
            if mention not in mentions:
                mentions.append(mention)

    visual = [*_strings(shot.get("image_prompt")), *_strings(shot.get("video_prompt"))]
    first_line = "；".join([*mentions, *visual])
    lines = [first_line] if first_line else []

    video_prompt = shot.get("video_prompt")
    dialogue = video_prompt.get("dialogue") if isinstance(video_prompt, Mapping) else None
    if isinstance(dialogue, list):
        for entry in dialogue:
            if not isinstance(entry, Mapping):
                continue
            line = entry.get("line")
            speaker = entry.get("speaker")
            if not isinstance(line, str) or not line.strip():
                continue
            if isinstance(speaker, str) and speaker.strip():
                lines.append(f"@[{asset_name_comparison_key(speaker)}]：{{{line}}}")
            else:
                # 旧数据没有可用说话人时只保留原文，不伪造角色归属。
                lines.append(f"{{{line}}}")

    voiceover = shot.get("voiceover_text")
    if isinstance(voiceover, str) and voiceover.strip():
        lines.append(f"{{{voiceover}}}")
    return "\n".join(lines)


def _unit_transition(shots: list[dict[str, Any]]) -> str:
    """沿用旧导出语义：unit 间转场取最后一个有效成员镜头。"""
    if not shots:
        return "cut"
    value = shots[-1].get("transition_to_next")
    return value if isinstance(value, str) and value in _TRANSITIONS else "cut"


def _unit_from_shots(
    *,
    unit_id: str,
    shots: list[dict[str, Any]],
    generated_assets: object,
    requires_replan: bool,
) -> dict[str, Any]:
    unit: dict[str, Any] = {
        "unit_id": unit_id,
        "shots": [{"text": _shot_text(shot)} for shot in shots],
        "references": _shot_references(shots),
        "duration_seconds": sum(_positive_seconds(shot.get("duration_seconds")) for shot in shots),
        "transition_to_next": _unit_transition(shots),
        "note": None,
        # 付费产物、URI 与旧来源签名均是历史事实，迁移必须逐键原样保留。
        "generated_assets": copy.deepcopy(generated_assets) if isinstance(generated_assets, dict) else {},
    }
    preparation = SpeechComposition.prepare(adapt_video_unit(unit))
    needs_replan = requires_replan or any(
        problem.code
        in {
            SpeechProblemCode.MIXED_SPEECH,
            SpeechProblemCode.PARSE_FAILED,
            SpeechProblemCode.EMPTY_SPEAKER,
        }
        for problem in preparation.problems
    )
    if needs_replan:
        unit["needs_replan"] = True
    return unit


def migrate_ad_reference_script(payload: dict[str, Any], *, episode: int) -> dict[str, Any]:
    """纯转换旧广告剧本；已转换脚本原样返回，供中断后安全重跑。"""
    if "video_units" in payload and "shots" not in payload and "reference_units" not in payload:
        existing_units = payload.get("video_units")
        if not isinstance(existing_units, list):
            raise ValueError("video_units 必须是数组")
        return copy.deepcopy(payload)

    raw_shots = payload.get("shots")
    if not isinstance(raw_shots, list):
        raise ValueError("广告参考路线旧剧本缺少 shots 数组")
    shots: list[dict[str, Any]] = []
    shot_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_shot in enumerate(raw_shots):
        if not isinstance(raw_shot, dict):
            raise ValueError(f"shots[{index}] 必须是对象")
        shot = copy.deepcopy(raw_shot)
        shots.append(shot)
        shot_id = shot.get("shot_id")
        if isinstance(shot_id, str) and shot_id:
            shot_by_id[shot_id] = shot

    raw_units = payload.get("reference_units")
    units: list[dict[str, Any]] = []
    if raw_units is None or raw_units == []:
        for ordinal, shot in enumerate(shots, start=1):
            units.append(
                _unit_from_shots(
                    unit_id=f"E{episode}U{ordinal}",
                    shots=[shot],
                    generated_assets={},
                    requires_replan=False,
                )
            )
    else:
        if not isinstance(raw_units, list):
            raise ValueError("reference_units 必须是数组或 null")
        covered_shot_ids: set[str] = set()
        used_unit_ids: set[str] = set()
        for index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, dict):
                raise ValueError(f"reference_units[{index}] 必须是对象")
            unit_id = raw_unit.get("unit_id")
            if not isinstance(unit_id, str) or not unit_id:
                raise ValueError(f"reference_units[{index}].unit_id 必须是非空字符串")
            used_unit_ids.add(unit_id)
            shot_ids = raw_unit.get("shot_ids")
            if not isinstance(shot_ids, list):
                raise ValueError(f"reference_units[{index}].shot_ids 必须是数组")
            members: list[dict[str, Any]] = []
            dangling = not shot_ids
            for shot_id in shot_ids:
                if isinstance(shot_id, str) and shot_id in shot_by_id:
                    members.append(shot_by_id[shot_id])
                    covered_shot_ids.add(shot_id)
                else:
                    dangling = True
            units.append(
                _unit_from_shots(
                    unit_id=unit_id,
                    shots=members,
                    generated_assets=raw_unit.get("generated_assets"),
                    requires_replan=dangling,
                )
            )

        # 非空旧索引也可能已落后于权威 shots。保留索引内可证明的 unit 身份与付费产物，
        # 再把未覆盖镜头逐条收进新问题单元；用户确认编排前禁止误生成，也不因删除 shots 丢内容。
        next_ordinal = 1
        for shot in shots:
            shot_id = shot.get("shot_id")
            if isinstance(shot_id, str) and shot_id and shot_id in covered_shot_ids:
                continue
            while f"E{episode}U{next_ordinal}" in used_unit_ids:
                next_ordinal += 1
            unit_id = f"E{episode}U{next_ordinal}"
            used_unit_ids.add(unit_id)
            next_ordinal += 1
            units.append(
                _unit_from_shots(
                    unit_id=unit_id,
                    shots=[shot],
                    generated_assets={},
                    requires_replan=True,
                )
            )

    migrated = copy.deepcopy(payload)
    migrated.pop("shots", None)
    migrated.pop("reference_units", None)
    migrated["video_units"] = units
    migrated["duration_seconds"] = sum(_positive_seconds(unit.get("duration_seconds")) for unit in units)
    return migrated


def _script_paths(project_dir: Path, project: dict[str, Any]) -> list[tuple[Path, int]]:
    episodes = project.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("project.episodes 必须是数组")
    result: list[tuple[Path, int]] = []
    seen: set[Path] = set()
    for index, entry in enumerate(episodes):
        if not isinstance(entry, dict):
            raise ValueError(f"project.episodes[{index}] 必须是对象")
        episode = entry.get("episode")
        script_file = entry.get("script_file")
        if not isinstance(episode, int) or isinstance(episode, bool) or episode <= 0:
            raise ValueError(f"project.episodes[{index}].episode 必须是正整数")
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
        result.append((path, episode))
    return result


def _ensure_script_backup(path: Path) -> None:
    if any(path.parent.glob(f"{path.name}.bak.v6-*")):
        return
    backup = path.with_name(f"{path.name}.bak.v6-{time.time_ns()}")
    shutil.copy2(path, backup)


def migrate_v6_to_v7(project_dir: Path) -> None:
    """先预检所有剧本，再逐文件原子替换，最后提交 ``project.json`` 版本。"""
    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if int(project.get("schema_version") or 0) >= 7:
        return

    target_route = project.get("content_mode") == "ad" and project.get("generation_mode") == "reference_video"
    plans: list[tuple[Path, dict[str, Any]]] = []
    if target_route:
        # 此循环只读：任一文件损坏时还没有备份或业务文件写入。
        for path, episode in _script_paths(project_dir, project):
            try:
                payload = load_json(path)
            except json.JSONDecodeError:
                raise
            if not isinstance(payload, dict):
                raise ValueError(f"剧本必须是对象: {path.relative_to(project_dir)}")
            plans.append((path, migrate_ad_reference_script(payload, episode=episode)))

        # 预检全部成功后才创建备份；所有脚本先备份完，再开始替换。
        for path, _payload in plans:
            _ensure_script_backup(path)
        for path, payload in plans:
            atomic_write_json(path, payload)

    migrated_project = copy.deepcopy(project)
    migrated_project["schema_version"] = 7
    atomic_write_json(project_file, migrated_project)


__all__ = ["migrate_ad_reference_script", "migrate_v6_to_v7"]
