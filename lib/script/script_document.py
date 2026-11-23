"""整集剧本落盘前的收尾，与按脚本规划投影出的整集剧本。

提示词编写、内容确认转换与存量项目迁移写出的剧本都经这里收尾：集号与条目 id 前缀、待编写与
重新规划标记、项目级字段、小说信息与 metadata。参考生视频按已确认时长覆盖、按生效档位校验依赖
运行时视频能力值，留在 ``ScriptGenerator._add_metadata``，夹在两段收尾之间。

本模块不触达文本生成与供应商配置，迁移步可以直接导入。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from lib.episode.episode_ids import episode_title
from lib.script.script_models import PENDING_AUTHORING_FIELD
from lib.script.script_plan_entries import ScriptPlanKind, plan_entry_content, plan_variant
from lib.script.script_skeleton import resolve_declared_kind, resolve_kind_items, rewrite_episode_prefix
from lib.speech.speech_composition import admit_script_unit

SCRIPT_PLAN_CONVERSION_GENERATOR = "script_plan_conversion"

BLANK_SCRIPT_GENERATOR = "blank"


def episode_ledger_entry(project: Mapping[str, Any], episode: int) -> dict[str, Any]:
    """按集号取 project.json episodes 条目；缺失返回空 dict。"""

    episodes = project.get("episodes")
    return next(
        (entry for entry in episodes or [] if isinstance(entry, dict) and entry.get("episode") == episode),
        {},
    )


def ledger_outline(entry: Mapping[str, Any]) -> dict[str, Any]:
    """账本条目的 outline 字段归一化为 dict（缺失/形状异常返回空 dict）。"""

    raw_outline = entry.get("outline")
    return raw_outline if isinstance(raw_outline, dict) else {}


def prepare_script_entries(script_data: dict[str, Any], *, project: Mapping[str, Any], episode: int) -> list[str]:
    """写入集号、改写条目 id 前缀、重置待编写与重新规划标记；返回改写后的条目 id（按条目顺序）。"""

    script_data["episode"] = int(episode)
    ep = int(episode)
    kind = resolve_declared_kind(project.get("content_mode", "narration"), project.get("generation_mode"))
    raw_items, id_field, _kind = resolve_kind_items(script_data, kind=kind)
    items = raw_items if isinstance(raw_items, list) else []
    rewritten_ids: list[str] = []
    for item in items:
        if isinstance(item, dict) and id_field in item:
            item[id_field] = rewrite_episode_prefix(item.get(id_field), ep)
            rewritten_ids.append(str(item[id_field]))

    for item in items:
        if not isinstance(item, dict):
            continue
        item.pop(PENDING_AUTHORING_FIELD, None)
        admission = admit_script_unit(kind, item, ignore_marker=True)
        if admission.allowed:
            item.pop("needs_replan", None)
        else:
            item["needs_replan"] = True
    return rewritten_ids


def finish_script_document(
    script_data: dict[str, Any], *, project: Mapping[str, Any], episode: int, generator: str
) -> dict[str, Any]:
    """补项目级字段、账本钩子、小说信息与 metadata，剥离废弃字段。"""

    content_mode = project.get("content_mode", "narration")
    if content_mode != "ad" and project.get("generation_mode") == "reference_video":
        script_data["content_mode"] = content_mode
    else:
        script_data.setdefault("content_mode", content_mode)

    if content_mode != "ad":
        entry = episode_ledger_entry(project, int(episode))
        script_data["hook"] = entry.get("hook")
        script_data["next_episode_teaser"] = ledger_outline(entry).get("next_episode_teaser")

    novel = script_data.get("novel")
    if not isinstance(novel, dict) or not novel.get("title") or not novel.get("chapter"):
        script_data["novel"] = {
            "title": project.get("title", ""),
            "chapter": episode_title(project, int(episode)),
        }
    novel = script_data.get("novel")
    if isinstance(novel, dict):
        novel.pop("source_file", None)

    script_data.pop("generation_mode", None)

    now = datetime.now(UTC).isoformat()
    script_data.setdefault("metadata", {})
    script_data["metadata"]["created_at"] = now
    script_data["metadata"]["updated_at"] = now
    script_data["metadata"]["generator"] = generator

    script_data["metadata"].pop("total_scenes", None)
    script_data["metadata"].pop("estimated_duration_seconds", None)
    script_data.pop("duration_seconds", None)
    script_data.pop("characters_in_episode", None)
    script_data.pop("clues_in_episode", None)

    return script_data


def build_materialized_script(
    project: Mapping[str, Any],
    episode: int,
    *,
    plan_kind: ScriptPlanKind,
    plan_entries: Sequence[Mapping[str, Any]],
    title: str | None,
) -> dict[str, Any]:
    """把脚本规划条目投影成一份整集正式剧本，不写盘、不读脚本规划文件。

    全部条目待编写、视觉层为空；参考生视频的单元正文、时长与对应原文取自脚本规划。标题取 ``title``
    （只有存量项目迁移沿用 drama 规划标题），否则取分集账本标题；账本标题为空时留空，集名由呈现层按播出
    位置派生。内容确认一律取账本标题。内容确认与存量项目迁移共用这一份投影。
    """

    items: list[dict[str, Any]] = []
    for entry in plan_entries:
        content = plan_entry_content(plan_kind, entry)
        if plan_kind != "reference_video":
            content = {**content, "image_prompt": None, "video_prompt": None}
        items.append(content)
    items_key = plan_variant(plan_kind).skeleton_kind
    script_data: dict[str, Any] = {"title": title or episode_title(project, episode), items_key: items}
    prepare_script_entries(script_data, project=project, episode=episode)
    finish_script_document(script_data, project=project, episode=episode, generator=SCRIPT_PLAN_CONVERSION_GENERATOR)
    for item in script_data[items_key]:
        item[PENDING_AUTHORING_FIELD] = True
    return script_data


def build_blank_script(project: Mapping[str, Any], episode: int) -> dict[str, Any]:
    """「从空白开始」的整集正式剧本：按项目两轴定骨架，条目数组为空，不写盘。

    标题取分集账本标题，账本标题为空时留空。
    """
    items_key = resolve_declared_kind(project.get("content_mode"), project.get("generation_mode"))
    script_data: dict[str, Any] = {"title": episode_title(project, episode), items_key: []}
    prepare_script_entries(script_data, project=project, episode=episode)
    return finish_script_document(script_data, project=project, episode=episode, generator=BLANK_SCRIPT_GENERATOR)


__all__ = [
    "BLANK_SCRIPT_GENERATOR",
    "SCRIPT_PLAN_CONVERSION_GENERATOR",
    "build_blank_script",
    "build_materialized_script",
    "episode_ledger_entry",
    "finish_script_document",
    "ledger_outline",
    "prepare_script_entries",
]
