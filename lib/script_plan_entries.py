"""脚本规划条目内容指纹：登记与比对共用的单一构造器。

剧本的时效判定原本只有一个整集口径——脚本规划内容整体取一次指纹（``script_review``
的 ``SCRIPT_PLAN_REVISION_FIELD``），改一个错别字与重拆整集同等对待。本模块把口径下沉到
**条目**：对每个脚本规划条目的内容字段取一次规范摘要，剧本的每个条目持久化它当时消费的那个
值（``SCRIPT_PLAN_ENTRY_REVISION_FIELD``），于是「哪些条目失效」是可回答的问题，提示词编写
便能只重写失配条目、保留其余条目的视觉层与用户字段。

指纹的构造口径与产物清单的 basis 摘要同一套（``ArtifactBasis``：规范 JSON + sha256 + 算法
前缀），且登记（生成落盘）与比对（工作流状态）共用本模块这一个构造器——ADR 0062 已经写明，
两侧各写一份投影会表现为整类条目无故失效。

条目 id 不进指纹：它是映射的键而非内容，条目集合与顺序的变化由 id 序列比对回答
（见 :func:`evaluate_entry_currency`），把 id 也摘进去只会让「改序」与「改内容」两种事实
挤在同一个布尔上。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from lib.artifact_manifest import ArtifactBasis
from lib.script_models import NarrationScriptPlanDraft, ReferenceScriptPlanDraft
from lib.script_skeleton import SKELETONS, rewrite_episode_prefix

#: script_plan 变体：drama / narration（按 content_mode）+ reference_video（按项目生成模式，
#: 跨 content_mode）。决定 script_plan 文件名与结构校验模型；三者共用同一内容确认。
#: ``lib.script_review`` 从本模块再导出，不另立一份。
ScriptPlanKind = Literal["drama", "narration", "reference_video"]

#: 剧本条目上持久化的「该条目消费的脚本规划条目内容指纹」字段名。对 LLM 隐藏
#: （``SkipJsonSchema``）、不在任何 PATCH 白名单内，只由提示词编写落盘与存量回填写入。
SCRIPT_PLAN_ENTRY_REVISION_FIELD = "script_plan_entry_revision"

#: 剧本 metadata 上持久化的「该剧本消费的脚本规划**整集**内容指纹」字段名，即条目指纹的整集
#: 对位。存量条目的回填按它开门（见 :func:`backfill_entry_revisions`），因而它与条目指纹须是
#: 同一个字面量的两处读法；``lib.script_review`` 从本模块再导出，不另立一份。
SCRIPT_PLAN_REVISION_FIELD = "script_plan_revision"

#: 条目内容 basis 的 kind 与版本。两者都参与 digest 计算且随剧本落盘，改值等于把存量剧本的
#: 全部条目判成失配、触发一轮整集重写——与 ``SCRIPT_PLAN_BASIS_KIND`` 同属持久化格式。
ENTRY_BASIS_KIND = "structured-content/script-plan-entry"
ENTRY_BASIS_KIND_VERSION = 1

#: 重写一个已存在条目时仍沿用旧值的用户字段。它们是用户对「这一条」的标注与剪辑意图，
#: 不随内容变化而失真；视觉层重出不构成删除它们的理由。``generated_assets`` /
#: ``end_frame_image`` 不在其列——内容已变，旧产物与旧尾帧不再对得上（产物文件不删，
#: 由产物清单按 ADR 0062 自然判 stale / missing）。
PRESERVED_ON_REWRITE_FIELDS: tuple[str, ...] = ("note", "transition_to_next")

#: 未变条目原样保留的全部字段——本模块不逐个挑，而是整条沿用旧条目，此清单只作契约声明，
#: 由测试锁住。
PRESERVED_ON_UNCHANGED_FIELDS: tuple[str, ...] = (
    "image_prompt",
    "video_prompt",
    "note",
    "end_frame_image",
    "transition_to_next",
    "generated_assets",
    "needs_replan",
)


class ScriptPlanEntryError(ValueError):
    """脚本规划条目集合本身不合法（缺 id / id 重复 / 引用了不存在的条目）。"""


@dataclass(frozen=True, slots=True)
class ScriptPlanVariant:
    """一个脚本规划变体的全部按变体分叉的取值。"""

    #: 剧本骨架种类（``lib.script_skeleton.SKELETONS`` 的键）。
    skeleton_kind: str
    #: 该变体中间文件里的条目数组键。与 ``skeleton_kind``（剧本侧的键）不同名：参考生视频的
    #: 规划文件用 ``units``，剧本里则是 ``video_units``。
    plan_items_key: str
    #: 进指纹的内容字段。口径是「改了它，这个条目的视觉层就该重写」：边界与时长决定这一条拍
    #: 什么、多长；资产引用列表决定画面里有谁；口播 / 原文锚 / 参考单元正文是内容真相；drama 的
    #: ``scene_description`` 是视觉层的自由文本基底，改它必须重出提示词。条目 id 与运行时状态
    #: （``generated_assets`` / ``needs_replan``）不在其列。
    content_fields: tuple[str, ...]
    #: 该变体中间文件的草稿模型。生成侧读脚本规划时用它归一条目，时效判定读同一份文件时须走
    #: 同一道归一，否则两侧摘出的指纹不同源。drama 为 ``None``：它没有草稿模型，生成侧消费原始 dict。
    draft_model: type[BaseModel] | None


#: 脚本规划变体表，是变体分叉的唯一真相源。
PLAN_VARIANTS: dict[str, ScriptPlanVariant] = {
    "drama": ScriptPlanVariant(
        skeleton_kind="scenes",
        plan_items_key="scenes",
        content_fields=(
            "duration_seconds",
            "segment_break",
            "characters_in_scene",
            "scenes",
            "props",
            "scene_description",
            "utterances",
            "source_text",
        ),
        draft_model=None,
    ),
    "narration": ScriptPlanVariant(
        skeleton_kind="segments",
        plan_items_key="segments",
        content_fields=(
            "duration_seconds",
            "segment_break",
            "novel_text",
            "characters_in_segment",
            "scenes",
            "props",
        ),
        draft_model=NarrationScriptPlanDraft,
    ),
    "reference_video": ScriptPlanVariant(
        skeleton_kind="video_units",
        plan_items_key="units",
        content_fields=(
            "duration_seconds",
            "text",
            "source_text",
        ),
        draft_model=ReferenceScriptPlanDraft,
    ),
}


def plan_variant(kind: ScriptPlanKind) -> ScriptPlanVariant:
    """变体记录；未知变体 fail-loud。"""

    variant = PLAN_VARIANTS.get(kind)
    if variant is None:
        raise ScriptPlanEntryError(f"未知的脚本规划变体: {kind!r}")
    return variant


def _skeleton(kind: ScriptPlanKind) -> tuple[str, str]:
    """返回 (骨架种类, id 字段名)；未知变体 fail-loud。"""

    skeleton_kind = plan_variant(kind).skeleton_kind
    return skeleton_kind, SKELETONS[skeleton_kind].id_field


def entry_id_field(kind: ScriptPlanKind) -> str:
    """该变体在剧本条目上的 id 字段名。"""

    return _skeleton(kind)[1]


def entry_revision(kind: ScriptPlanKind, entry: Mapping[str, object]) -> str:
    """一个脚本规划条目的内容指纹（带 ``sha256-v1:`` 前缀）。

    缺失的内容字段按缺失参与摘要（不补默认值）：脚本规划各变体的模型已经规定了哪些字段必填，
    在此补默认值会让「字段缺失」与「字段等于默认值」摘出同一个值，掩盖坏 script_plan。
    """

    fields = plan_variant(kind).content_fields
    return ArtifactBasis.build(
        ENTRY_BASIS_KIND,
        kind_version=ENTRY_BASIS_KIND_VERSION,
        inputs={
            "plan_kind": kind,
            "content": {field: entry[field] for field in fields if field in entry},
        },
    ).digest


def plan_entry_revisions(
    kind: ScriptPlanKind,
    entries: Sequence[Mapping[str, object]],
    *,
    episode: int,
) -> dict[str, str]:
    """脚本规划条目列表 → ``{落盘后的条目 id: 内容指纹}``，保持规划顺序。

    键用改写集号前缀后的 id（``rewrite_episode_prefix``），与提示词编写落盘时写进剧本的 id
    同一口径——否则规划侧与剧本侧会按两套 id 比对，整集判失配。
    """

    _, id_field = _skeleton(kind)
    revisions: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ScriptPlanEntryError(f"脚本规划条目 [{index}] 必须是对象: {entry!r}")
        raw_id = entry.get(id_field)
        if not isinstance(raw_id, str) or not raw_id:
            raise ScriptPlanEntryError(f"脚本规划条目 [{index}] 缺少 {id_field}")
        entry_id = str(rewrite_episode_prefix(raw_id, episode))
        if entry_id in revisions:
            raise ScriptPlanEntryError(f"脚本规划条目 {id_field} 重复: {entry_id}")
        revisions[entry_id] = entry_revision(kind, entry)
    return revisions


def plan_entries_from_document(kind: ScriptPlanKind, document: object) -> list[dict[str, object]]:
    """脚本规划中间文件的内容 → 条目列表；形状不符时返回空列表。

    narration / reference_video 的条目先经各自的草稿模型归一（``model_validate`` +
    ``model_dump``）——生成侧读脚本规划走的正是同一对模型，指纹若一侧摘归一后的条目、另一侧摘
    磁盘原文，一个省略了默认字段的中间文件（不带 ``source_text`` 的存量参考单元、不带
    ``segment_break`` 的分镜）就会让两侧摘出不同的值，落盘刚完成的剧本立刻被判整集失效。
    drama 无草稿模型，生成侧消费的就是原始 dict，此处同样原样返回。

    形状守卫不 fail-loud：脚本规划本身是否良构由它自己的产物状态与结构校验回答，时效判定
    读不出条目时如实退化为「没有可比对的条目」，不额外制造一类错误——归一失败（结构不合法、
    时长越界）同取这一条，退回整集口径判定。
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


def script_entries_by_id(kind: ScriptPlanKind, script: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """剧本 → ``{条目 id: 条目}``，保持剧本顺序；非对象条目与缺 id 条目跳过。

    跳过而非 fail-loud：剧本可能是校验失败降级保存的原始 dict，时效判定不该因为一条脏数据
    整份读不出来——跳过的条目在 id 序列里也不存在，会如实表现为「规划有、剧本无」。
    """

    skeleton_kind, id_field = _skeleton(kind)
    raw_items = script.get(skeleton_kind)
    entries: dict[str, dict[str, object]] = {}
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        entry_id = item.get(id_field)
        if isinstance(entry_id, str) and entry_id and entry_id not in entries:
            entries[entry_id] = item
    return entries


@dataclass(frozen=True, slots=True)
class ScriptEntryCurrency:
    """剧本条目相对当前脚本规划的时效。

    ``stale_ids`` / ``new_ids`` / ``removed_ids`` 按脚本规划顺序（``removed_ids`` 按剧本顺序）。
    """

    plan_ids: tuple[str, ...]
    stale_ids: tuple[str, ...]
    new_ids: tuple[str, ...]
    removed_ids: tuple[str, ...]
    order_changed: bool

    @property
    def outdated_ids(self) -> tuple[str, ...]:
        """需要重写视觉层的条目：失配的与新增的，按脚本规划顺序。"""

        outdated = set(self.stale_ids) | set(self.new_ids)
        return tuple(entry_id for entry_id in self.plan_ids if entry_id in outdated)

    @property
    def is_stale(self) -> bool:
        """剧本整体是否失效：任一条目失配、或条目集合 / 顺序与脚本规划不一致。"""

        return bool(self.stale_ids or self.new_ids or self.removed_ids or self.order_changed)


def evaluate_entry_currency(
    kind: ScriptPlanKind,
    *,
    script: Mapping[str, object],
    plan_revisions: Mapping[str, str],
    legacy_entries_current: bool,
) -> ScriptEntryCurrency:
    """按条目比对剧本与脚本规划。

    ``legacy_entries_current`` 是存量剧本的读时补齐口径：条目上没有指纹字段的剧本产于本机制
    之前，无从知道它消费了什么，只能退回整集口径——调用方传入「剧本 metadata 记录的整集
    脚本规划指纹是否仍等于当前值」。为真则这些条目按未变处置（不误报 stale），为假则全部
    失配，与本机制引入前的整集判定同结论。已带指纹的条目不看这个入参。
    """

    script_entries = script_entries_by_id(kind, script)
    stale: list[str] = []
    new: list[str] = []
    for entry_id, revision in plan_revisions.items():
        entry = script_entries.get(entry_id)
        if entry is None:
            new.append(entry_id)
            continue
        stored = entry.get(SCRIPT_PLAN_ENTRY_REVISION_FIELD)
        if not isinstance(stored, str) or not stored:
            if not legacy_entries_current:
                stale.append(entry_id)
            continue
        if stored != revision:
            stale.append(entry_id)

    removed = tuple(entry_id for entry_id in script_entries if entry_id not in plan_revisions)
    kept_script_order = [entry_id for entry_id in script_entries if entry_id in plan_revisions]
    kept_plan_order = [entry_id for entry_id in plan_revisions if entry_id in script_entries]
    return ScriptEntryCurrency(
        plan_ids=tuple(plan_revisions),
        stale_ids=tuple(stale),
        new_ids=tuple(new),
        removed_ids=removed,
        order_changed=kept_script_order != kept_plan_order,
    )


def backfill_entry_revisions(
    kind: ScriptPlanKind,
    *,
    script: Mapping[str, object],
    plan_revisions: Mapping[str, str],
    whole_plan_revision: str | None,
) -> tuple[str, ...]:
    """为存量剧本里没有条目指纹的条目就地补上当前脚本规划的条目指纹，返回被回填的条目 id。

    只在剧本 metadata 记录的整集脚本规划指纹仍等于 ``whole_plan_revision`` 时回填：整集相等
    即证明这份剧本消费的正是当前这份脚本规划，逐条盖章与「它当时消费了什么」等价，且盖的正是
    装配出口 (:func:`splice_entries`) 会盖的那个值——两处同取 :func:`plan_entry_revisions` 的
    产物，不另摘一份。整集不等时一律不回填：那时无从知道每一条消费了什么，只能维持整集口径回退
    （见 :func:`evaluate_entry_currency` 的 ``legacy_entries_current``）。

    回填必须发生在脚本规划被改动之前——一旦整集指纹不再相等，这扇门就关上了，接下来的一次
    提示词编写便整集重写、覆盖用户精修过的视觉层。

    已带指纹的条目不动：它记着自己当时消费的值，覆盖它等于把一个真实的失配抹平。
    """

    if not whole_plan_revision:
        return ()
    metadata = script.get("metadata")
    recorded = metadata.get(SCRIPT_PLAN_REVISION_FIELD) if isinstance(metadata, Mapping) else None
    if recorded != whole_plan_revision:
        return ()
    entries = script_entries_by_id(kind, script)
    backfilled: list[str] = []
    for entry_id, revision in plan_revisions.items():
        entry = entries.get(entry_id)
        if entry is None:
            continue
        stored = entry.get(SCRIPT_PLAN_ENTRY_REVISION_FIELD)
        if isinstance(stored, str) and stored:
            continue
        entry[SCRIPT_PLAN_ENTRY_REVISION_FIELD] = revision
        backfilled.append(entry_id)
    return tuple(backfilled)


#: ``scope`` 的两个具名取值；其余取值按「条目 id 列表」解读。
SCOPE_ALL = "all"
SCOPE_STALE = "stale"


def resolve_rewrite_ids(
    scope: str | Iterable[str] | None,
    currency: ScriptEntryCurrency,
) -> tuple[str, ...]:
    """把 ``scope`` 解析为本次要重写视觉层的条目 id，按脚本规划顺序。

    ``None`` / ``"stale"`` → 失配与新增条目（默认）；``"all"`` → 全部条目；
    条目 id 列表 → 该列表，其中任一 id 不在当前脚本规划内即 fail-loud（调用方据此在落盘前拒绝）。
    """

    if scope is None or scope == SCOPE_STALE:
        return currency.outdated_ids
    if scope == SCOPE_ALL:
        return currency.plan_ids
    if isinstance(scope, str):
        raise ScriptPlanEntryError(f"未知的 scope: {scope!r}；取值为 {SCOPE_ALL!r} / {SCOPE_STALE!r} 或条目 id 列表")
    requested = list(scope)
    unknown = sorted({entry_id for entry_id in requested if entry_id not in currency.plan_ids})
    if unknown:
        raise ScriptPlanEntryError(f"scope 指定的条目 id 不在当前脚本规划内: {unknown}")
    selected = set(requested)
    return tuple(entry_id for entry_id in currency.plan_ids if entry_id in selected)


def splice_entries(
    kind: ScriptPlanKind,
    *,
    plan_revisions: Mapping[str, str],
    rewritten: Sequence[Mapping[str, object]],
    existing: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    """按脚本规划顺序装配最终条目列表：本次重写的取新值，其余原样沿用旧条目。

    ``rewritten`` 是本次提示词编写产出并已补完元数据的条目（只含被重写的那些）。重写一个
    已存在条目时沿用其 ``PRESERVED_ON_REWRITE_FIELDS``；未被重写的条目整条沿用，因而其视觉层
    与用户字段逐字节不变。规划里存在、既没被重写也不在旧剧本里的条目 fail-loud——那是
    ``resolve_rewrite_ids`` 与本函数的调用契约被破坏，静默丢条目会让剧本悄悄少一段。
    每个条目落盘前统一盖上其消费的条目内容指纹。
    """

    _, id_field = _skeleton(kind)
    rewritten_by_id: dict[str, dict[str, object]] = {}
    for entry in rewritten:
        entry_id = entry.get(id_field)
        if not isinstance(entry_id, str) or not entry_id:
            raise ScriptPlanEntryError(f"提示词编写产出的条目缺少 {id_field}: {entry!r}")
        rewritten_by_id[entry_id] = dict(entry)

    unknown = sorted(set(rewritten_by_id) - set(plan_revisions))
    if unknown:
        raise ScriptPlanEntryError(f"提示词编写产出了当前脚本规划之外的条目 id: {unknown}")

    entries: list[dict[str, object]] = []
    for entry_id, revision in plan_revisions.items():
        entry = rewritten_by_id.get(entry_id)
        if entry is not None:
            previous = existing.get(entry_id)
            if previous is not None:
                for field in PRESERVED_ON_REWRITE_FIELDS:
                    if field in previous:
                        entry[field] = previous[field]
        else:
            previous = existing.get(entry_id)
            if previous is None:
                raise ScriptPlanEntryError(f"条目 {entry_id} 既未被重写、旧剧本中也不存在")
            entry = dict(previous)
        entry[SCRIPT_PLAN_ENTRY_REVISION_FIELD] = revision
        entries.append(entry)
    return entries


__all__ = [
    "ENTRY_BASIS_KIND",
    "ENTRY_BASIS_KIND_VERSION",
    "PLAN_VARIANTS",
    "PRESERVED_ON_REWRITE_FIELDS",
    "PRESERVED_ON_UNCHANGED_FIELDS",
    "SCOPE_ALL",
    "SCOPE_STALE",
    "SCRIPT_PLAN_ENTRY_REVISION_FIELD",
    "SCRIPT_PLAN_REVISION_FIELD",
    "ScriptEntryCurrency",
    "ScriptPlanEntryError",
    "ScriptPlanKind",
    "ScriptPlanVariant",
    "backfill_entry_revisions",
    "entry_id_field",
    "entry_revision",
    "evaluate_entry_currency",
    "plan_entries_from_document",
    "plan_entry_revisions",
    "plan_variant",
    "resolve_rewrite_ids",
    "script_entries_by_id",
    "splice_entries",
]
