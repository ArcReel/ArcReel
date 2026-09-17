"""脚本规划条目到正式脚本条目的投影。

内容确认时脚本规划整份转为正式脚本（``ScriptGenerator.materialize_script_plan``），存量项目迁移
补建正式脚本与补录字段时读同一份投影；两处按本模块取变体、归一条目、投影内容层，不各写一份。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from lib.script_models import NarrationScriptPlanDraft, ReferenceScriptPlanDraft
from lib.script_skeleton import SKELETONS

#: script_plan 变体：drama / narration（按 content_mode）+ reference_video（按项目生成模式，
#: 跨 content_mode）。决定 script_plan 文件名与结构校验模型；三者共用同一内容确认。
#: ``lib.script_review`` 从本模块再导出，不另立一份。
ScriptPlanKind = Literal["drama", "narration", "reference_video"]


@dataclass(frozen=True, slots=True)
class ScriptPlanVariant:
    """一个脚本规划变体的全部按变体分叉的取值。"""

    #: 剧本骨架种类（``lib.script_skeleton.SKELETONS`` 的键）。
    skeleton_kind: str
    #: 该变体中间文件里的条目数组键。与 ``skeleton_kind``（剧本侧的键）不同名：参考生视频的
    #: 规划文件用 ``units``，剧本里则是 ``video_units``。
    plan_items_key: str
    #: 该变体中间文件的草稿模型，读规划时用它归一条目。drama 为 ``None``：它没有草稿模型，
    #: 消费原始 dict。
    draft_model: type[BaseModel] | None
    #: 规划条目投影到剧本条目内容层时**只保留**的字段；``None`` 表示整条透传。
    script_fields: tuple[str, ...] | None = None


#: 脚本规划变体表，是变体分叉的唯一真相源。
PLAN_VARIANTS: dict[str, ScriptPlanVariant] = {
    "drama": ScriptPlanVariant(skeleton_kind="scenes", plan_items_key="scenes", draft_model=None),
    "narration": ScriptPlanVariant(
        skeleton_kind="segments",
        plan_items_key="segments",
        draft_model=NarrationScriptPlanDraft,
    ),
    "reference_video": ScriptPlanVariant(
        skeleton_kind="video_units",
        plan_items_key="units",
        draft_model=ReferenceScriptPlanDraft,
        script_fields=("unit_id", "text", "duration_seconds", "source_text"),
    ),
}


def plan_variant(kind: ScriptPlanKind) -> ScriptPlanVariant:
    """变体记录；未知变体 fail-loud。"""

    variant = PLAN_VARIANTS.get(kind)
    if variant is None:
        raise ValueError(f"未知的脚本规划变体: {kind!r}")
    return variant


def entry_id_field(kind: ScriptPlanKind) -> str:
    """该变体在剧本条目上的 id 字段名。"""

    return SKELETONS[plan_variant(kind).skeleton_kind].id_field


def plan_entry_content(kind: ScriptPlanKind, entry: Mapping[str, object]) -> dict[str, object]:
    """脚本规划条目 → 剧本条目的内容层（不含视觉层）。

    drama / narration 整条透传（drama 的 ``scene_description`` 随之进入正式脚本，作为提示词编写的
    视觉基底），参考生视频只取 ``unit_id`` / ``text`` / ``duration_seconds`` / ``source_text``。返回新
    dict，不就地修改入参。
    """

    variant = plan_variant(kind)
    if variant.script_fields is not None:
        return {field: entry[field] for field in variant.script_fields if field in entry}
    return dict(entry)


def plan_entries_from_document(kind: ScriptPlanKind, document: object) -> list[dict[str, object]]:
    """脚本规划中间文件的内容 → 条目列表；形状不符或归一失败时返回空列表。

    narration / reference_video 的条目先经各自的草稿模型归一（``model_validate`` + ``model_dump``），
    省略了默认字段的存量中间文件（不带 ``source_text`` 的参考单元、不带 ``segment_break`` 的分镜）
    由此补齐。drama 无草稿模型，原样返回对象条目。
    """

    variant = plan_variant(kind)
    items_key = variant.plan_items_key
    if not isinstance(document, Mapping):
        return []
    entries = document.get(items_key)
    if not isinstance(entries, list):
        return []
    raw_entries = [entry for entry in entries if isinstance(entry, dict)]
    draft_model = variant.draft_model
    if draft_model is None or not raw_entries:
        return raw_entries
    try:
        draft = draft_model.model_validate({items_key: raw_entries})
    except ValidationError:
        return []
    return [item.model_dump() for item in getattr(draft, items_key)]


__all__ = [
    "PLAN_VARIANTS",
    "ScriptPlanKind",
    "ScriptPlanVariant",
    "entry_id_field",
    "plan_entries_from_document",
    "plan_entry_content",
    "plan_variant",
]
