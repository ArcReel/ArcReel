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

from lib.script.script_models import PENDING_AUTHORING_FIELD
from lib.script.script_plan_entries import ScriptPlanKind, plan_entry_content, plan_variant
from lib.script.script_skeleton import resolve_declared_kind, resolve_kind_items, rewrite_episode_prefix
from lib.speech.speech_composition import admit_script_unit

#: 内容确认转出的剧本写进 ``metadata.generator`` 的标记：条目是按脚本规划投影出来的，不经文本模型。
SCRIPT_PLAN_CONVERSION_GENERATOR = "script_plan_conversion"


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

    # CLI 参数 --episode 是集号唯一真相源。AI 输出的 schema 不含 episode 字段，
    # 这里负责落盘前补上。
    script_data["episode"] = int(episode)
    # 兜底改写 segment/scene/unit ID 中的 E\d+ 前缀，避免 LLM 写错集号导致文件
    # 名跨集冲突（如 storyboards/scene_E1S01.png 被 E2 重新覆盖）。
    ep = int(episode)
    # segment/scene/shot/unit ID 前缀统一经规范解析定骨架 + resolve_kind_items 查条目数组
    # 与 id 字段改写（参考生视频三种 content_mode 均映射到 video_units，无需按生成模式分支）。
    kind = resolve_declared_kind(project.get("content_mode", "narration"), project.get("generation_mode"))
    raw_items, id_field, _kind = resolve_kind_items(script_data, kind=kind)
    # 校验失败降级保存的原始 dict 里该数组可能为非列表脏值（LLM 误写标量），
    # `... or []` 只挡 falsy、挡不住真值标量，isinstance 守卫避免 `for` 迭代崩溃。
    items = raw_items if isinstance(raw_items, list) else []
    rewritten_ids: list[str] = []
    for item in items:
        if isinstance(item, dict) and id_field in item:
            item[id_field] = rewrite_episode_prefix(item.get(id_field), ep)
            rewritten_ids.append(str(item[id_field]))

    for item in items:
        if not isinstance(item, dict):
            continue
        # 本轮产出的条目视觉层已由提示词编写写回；内容确认转换在此之后为全部条目重新置位。
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
    # content_mode 严格只是"内容类型"（narration/drama/ad）；"视频来源"维度是项目级事实，
    # 剧本不落盘任何生成模式标记——生成分派一律读项目生成模式。
    # 参考生视频剧本必须强制覆盖：ReferenceVideoScript.content_mode 有 Pydantic 默认值
    # "narration"，setdefault 拿不到项目级真值；非参考集 LLM 已在 schema 中产出
    # narration/drama，setdefault 仅作 fallback。
    if content_mode != "ad" and project.get("generation_mode") == "reference_video":
        script_data["content_mode"] = content_mode
    else:
        script_data.setdefault("content_mode", content_mode)

    # 集级钩子/下集预告：分集账本是钩子设计的单一真相源，强制以账本值覆盖
    # （LLM 不参与填写，model_dump 只会留下 None 默认值）。账本无规划数据时为 None。
    # ad 恒单集、无分集账本概念，剧本模型也不持有这两个字段，跳过注入。
    if content_mode != "ad":
        entry = episode_ledger_entry(project, int(episode))
        script_data["hook"] = entry.get("hook")
        script_data["next_episode_teaser"] = ledger_outline(entry).get("next_episode_teaser")

    # 添加小说信息
    # 注意守卫语义：novel 字段已 SkipJsonSchema 隐藏，但 default_factory=NovelInfo
    # 让 model_dump 输出必带 {"title":"","chapter":""} 占位。所以判 "key 是否存在"
    # 无法捕获真实"未注入"状态，必须按内容判：title/chapter 任一为空就重注入。
    novel = script_data.get("novel")
    if not isinstance(novel, dict) or not novel.get("title") or not novel.get("chapter"):
        script_data["novel"] = {
            "title": project.get("title", ""),
            "chapter": f"第{episode}集",
        }
    # 剥离已废弃的 source_file（AI 可能虚构）
    novel = script_data.get("novel")
    if isinstance(novel, dict):
        novel.pop("source_file", None)

    # 剥离剧本级 generation_mode：生成模式的真相源是 project.json，剧本不留标记。
    # 校验失败时 script_data 是后端原样返回的 dict（未经模型过滤），存量剧本重生成也会
    # 把旧值带进来——不在此处删就会随写盘回到磁盘上。
    script_data.pop("generation_mode", None)

    # 添加时间戳
    now = datetime.now(UTC).isoformat()
    script_data.setdefault("metadata", {})
    script_data["metadata"]["created_at"] = now
    script_data["metadata"]["updated_at"] = now
    script_data["metadata"]["generator"] = generator

    # 剥离废弃的 episode 级聚合字段：条目数、总时长与角色/场景/道具聚合都是从剧本正文
    # 逐读即得的派生值，由项目摘要读时计算，落盘一份只会与正文漂移。
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

    全部条目待编写、视觉层为空；参考生视频的单元正文、时长与对应原文取自脚本规划。标题取规划标题，
    否则取分集账本标题，再否则按集号兜底。内容确认与存量项目迁移共用这一份投影。
    """

    items: list[dict[str, Any]] = []
    for entry in plan_entries:
        content = plan_entry_content(plan_kind, entry)
        if plan_kind != "reference_video":
            content = {**content, "image_prompt": None, "video_prompt": None}
        items.append(content)
    items_key = plan_variant(plan_kind).skeleton_kind
    episode_title = episode_ledger_entry(project, episode).get("title")
    fallback_title = episode_title if isinstance(episode_title, str) and episode_title.strip() else f"第{episode}集"
    script_data: dict[str, Any] = {"title": title or fallback_title, items_key: items}
    prepare_script_entries(script_data, project=project, episode=episode)
    finish_script_document(script_data, project=project, episode=episode, generator=SCRIPT_PLAN_CONVERSION_GENERATOR)
    for item in script_data[items_key]:
        item[PENDING_AUTHORING_FIELD] = True
    return script_data


__all__ = [
    "SCRIPT_PLAN_CONVERSION_GENERATOR",
    "build_materialized_script",
    "episode_ledger_entry",
    "finish_script_document",
    "ledger_outline",
    "prepare_script_entries",
]
