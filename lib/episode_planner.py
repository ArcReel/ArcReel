"""分集规划服务：读源文窗口 → 调项目配置的文本模型 → 写分集账本并派生集文件。

plan() 从 planning_cursor 起取一个源文窗口，由文本模型一次规划出窗口内所有
剧情弧完整的集（标题/钩子/切分锚点；drama 另含分集大纲），schema 强约束 +
锚点存在性/唯一性/连续性机械校验，失败自动重试并附上一轮失败原因。用户需要
调整已规划内容时走「重置 + 重新规划」：先用 :mod:`lib.episode_reset` 把账本
退回到最早受影响的集（保留其前），再带 instructions 分批重新调用 plan()。

写入阶段在同一把项目锁内完成：写账本 + 按账本重写派生集文件 + 清理账本之外
的残留派生文件（含余文文件），下游读到的 ``source/episode_N.txt`` 永远与账本
一致。窗口字数与每批集数上限为内部默认，project.json 顶层
``planning_window_chars`` / ``planning_max_episodes`` 可覆盖。新提交的集号若
在磁盘上已有下游产物（该集实际已被消费过，见重置+重新规划场景），标 stale
而非直接覆盖状态，产物不删除。
"""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib import script_review
from lib.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    compute_source_fingerprints,
    discover_episode_files,
    discover_sources,
    episodes_without_source_range,
    has_downstream_products,
    mismatched_source_fingerprints,
    normalize_source_text,
    parse_episode_num,
    register_orphan_episode_entries,
)
from lib.episode_paths import episode_script_relpath
from lib.path_safety import PathTraversalError, safe_join
from lib.project_manager import ProjectManager, resolve_source_kind
from lib.prompt_builders_script import USER_INSTRUCTIONS_HEADER
from lib.text_backends.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    StructuredOutputExhaustedError,
    TextGenerationRequest,
    TextOutputTruncatedError,
    TextTaskType,
    truncate_for_log,
)
from lib.text_generator import TextGenerator
from lib.text_metrics import count_reading_units, reading_unit_noun
from lib.text_utils import strip_json_code_fences

logger = logging.getLogger(__name__)

# 窗口/批量内部默认值；project.json 顶层同名字段可覆盖
DEFAULT_PLANNING_WINDOW_CHARS = 30000
DEFAULT_PLANNING_MAX_EPISODES = 20

# LLM 输出未通过 schema / 机械校验时的总尝试次数（含首次）
_MAX_PLAN_ATTEMPTS = 3

# 注入 prompt 的已规划上下文条数上限（保持续写连贯，不膨胀 prompt）
_CONTEXT_EPISODES_LIMIT = 5

# 缺位置记录的集号在拒绝信息里最多逐个列出的条数（整本老项目可能每一集都缺，逐个列
# 会把错误信息撑成几百集的清单）
_MISSING_RANGE_LISTED_LIMIT = 10


class EpisodePlanningError(RuntimeError):
    """分集规划失败（源文缺失、校验重试耗尽等）。"""


class PlanningConflictError(EpisodePlanningError):
    """规划期间账本被并发修改，提交被拒绝；重新调用即可基于新状态规划。"""


@dataclass
class EpisodePlanSummary:
    """单集摘要：标题 + 钩子 + 体量（按 source_language 计的阅读单位）。"""

    episode: int
    title: str
    hook: str
    reading_units: int
    ledger_status: str


@dataclass
class LedgerStats:
    """全账本体量分布快照（机械现算，不做「多小算畸小」之类的阈值判断）。

    语义判断（是否与用户结构性偏好如「一章一集」「共 32 集」有出入）留给主 agent 做——
    这里只报分布事实。
    """

    total_episodes: int
    smallest: list[tuple[int, int]]  # (集号, 体量) 体量最小的最多 5 集，按体量升序
    median_units: int | None
    target_units: int | None


@dataclass
class PlanResult:
    episodes: list[EpisodePlanSummary]
    cursor: dict[str, Any] | None
    source_exhausted: bool = False
    stale_episodes: list[int] = field(default_factory=list)
    total_planned: int = 0
    ledger_stats: LedgerStats | None = None


@dataclass
class _PlanningProgress:
    """本批规划时的全局进度快照，仅在 instructions 非空时注入 prompt（供模型换算切分节奏）。"""

    planned_count: int
    remaining_units: int
    window_units: int


_DRAFT_CONFIG = ConfigDict(extra="forbid")


class NarrationEpisodeDraft(BaseModel):
    """narration 条目：精确切分锚点 + 钩子。"""

    model_config = _DRAFT_CONFIG

    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    end_anchor: str = Field(min_length=2)


class DramaEpisodeDraft(NarrationEpisodeDraft):
    """drama 条目加厚为分集大纲：故事节点 + 下集预告语。"""

    story_beats: list[str] = Field(min_length=1)
    next_episode_teaser: str | None = None


class NarrationPlanDraft(BaseModel):
    model_config = _DRAFT_CONFIG

    episodes: list[NarrationEpisodeDraft] = Field(min_length=1)


class DramaPlanDraft(BaseModel):
    model_config = _DRAFT_CONFIG

    episodes: list[DramaEpisodeDraft] = Field(min_length=1)


class _DraftRejected(Exception):
    """单轮 LLM 输出被 schema / 机械校验拒绝；reasons 注入下一轮重试 prompt。"""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# 全/半角标点折叠表：把同一标点的全角 / 表意形态映射到半角规范形，仅供匹配时构造
# 折叠副本用，绝不落库。逐字符 1:1 映射（长度不变），折叠坐标即 NFC 精确坐标。
_PUNCT_FOLD: dict[str, str] = {
    "。": ".",  # U+3002 表意句号
    "．": ".",  # U+FF0E 全角句点
    "，": ",",  # U+FF0C 全角逗号
    "！": "!",  # U+FF01 全角叹号
    "？": "?",  # U+FF1F 全角问号
    "：": ":",  # U+FF1A 全角冒号
    "；": ";",  # U+FF1B 全角分号
    "（": "(",  # U+FF08 全角左括号
    "）": ")",  # U+FF09 全角右括号
    "～": "~",  # U+FF5E 全角波浪号
    "〜": "~",  # U+301C CJK 波浪号（与 U+FF5E 同族，一并折向半角 ~）
    "“": '"',  # U+201C 左弯双引号
    "”": '"',  # U+201D 右弯双引号
    "‘": "'",  # U+2018 左弯单引号
    "’": "'",  # U+2019 右弯单引号
    "＂": '"',  # U+FF02 全角直双引号
    "＇": "'",  # U+FF07 全角直单引号
}
# 折叠值必须是单字符：_fold_for_match 的逐字符 1:1 等长依赖于此。一旦某条映射折成多字符，
# 折叠串偏移就相对 NFC 原文漂移，end = start + len(anchor) 会算出错误切点。导入期即拦截违例。
assert all(len(dst) == 1 for dst in _PUNCT_FOLD.values()), "折叠表的值必须是单字符以保证等长映射"


def _fold_for_match(text: str) -> str:
    """构造匹配用折叠副本：折叠全/半角标点 + 把每个空白字符折成一个半角空格（逐字符等长，不合并连续空白）。

    严格逐字符 1:1 映射（每个字符映射为恰好一个字符，连续空白按原数量逐个保留、不压缩），
    长度与原文一致，因而折叠串上的偏移即原文 NFC 坐标系内的精确偏移。容差只限定在标点
    全/半角与单个空白字符的宽度，不做编辑距离 / 语义级模糊匹配。
    """
    out: list[str] = []
    for ch in text:
        folded = _PUNCT_FOLD.get(ch)
        if folded is not None:
            out.append(folded)
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _find_all_overlapping(haystack: str, needle: str) -> list[int]:
    """收集 needle 在 haystack 内的全部起点（允许重叠）。

    str.count 只统计非重叠匹配，会把重叠出现（如 "aaaa" 中的 "aaa"）误判为唯一；
    步进 1 滑动查找，按允许重叠的口径判定 0/1/多次。空 needle 直接返回空列表
    （调用方的 anchor 受 min_length=2 约束不会为空，此处仅作 helper 的防御边界）。
    """
    if not needle:
        return []
    starts: list[int] = []
    found = haystack.find(needle)
    while found != -1:
        starts.append(found)
        found = haystack.find(needle, found + 1)
    return starts


def _resolve_boundaries(
    window: str,
    drafts: list[NarrationEpisodeDraft],
    *,
    snap_whitespace_tail: bool,
) -> list[int]:
    """把每集 end_anchor 解析为窗口内相对结束偏移，校验存在/唯一/连续。

    范围由锚点构造性保证连续不重叠：第 i 集 = [第 i-1 集末尾, 第 i 集锚点末尾)。
    末尾只剩空白时（``snap_whitespace_tail=True``，即 plan 命中全文结尾窗口）把
    最后一集贴齐到窗口末尾，贴齐后每个字符都归属某一集。

    锚点定位分级：先精确 ``find``（命中即与逐字节匹配完全一致，Tier1 不做任何归一）；
    精确落空时退化到容错——先对锚副本施加与 window 相同的 ``normalize_source_text`` 归一
    （NFC + 换行统一），把「window 已归一、anchor 未归一」整类失配一次性闭合，再按折叠
    全/半角标点 + 折叠空白宽度的等价口径在折叠副本上扫描。折叠对 window 为 1:1 等长映射，
    故命中起点即归一坐标系下的精确起点；跨度取归一后的锚长（``normalize_source_text`` 非
    长度保持，end 必须用归一锚长而非原 anchor 长，否则 \\r 或组合字符撑长会让偏移漂移）。
    切片仍取归一后的原文、不改写源文标点。折叠后仍多处命中时维持「无法唯一定位」拒绝。
    """
    reasons: list[str] = []
    ends: list[int] = []
    pos = 0
    ordering_valid = True
    folded_window: str | None = None  # 懒构造：仅在精确匹配落空时折叠一次
    for idx, ep in enumerate(drafts, start=1):
        anchor = ep.end_anchor
        match_len = len(anchor)  # 精确命中：跨度即原 anchor 长度（与 window 逐字节一致）
        starts = _find_all_overlapping(window, anchor)
        matched_by_fold = False
        if not starts:
            # 精确落空：退化到容错匹配。对「匹配用的锚副本」施加与 window 完全相同的归一
            # （normalize_source_text：NFC + 换行统一，window 本就经它产出），再折叠标点全/半角
            # 与空白宽度，在对 window 等长的折叠副本上定位精确偏移；Tier1 字节级行为与源文坐标
            # 不变。normalize_source_text 非长度保持，故 end 跨度取归一后锚长 match_len（不是原
            # anchor 长），与归一坐标系对齐，避免 \r / 组合字符撑长导致偏移漂移、损坏切片坐标。
            if folded_window is None:
                folded_window = _fold_for_match(window)
            normalized_anchor = normalize_source_text(anchor)
            starts = _find_all_overlapping(folded_window, _fold_for_match(normalized_anchor))
            if starts:
                matched_by_fold = True
                match_len = len(normalized_anchor)
        if not starts:
            reasons.append(f"第 {idx} 条的 end_anchor 在原文窗口中不存在（必须逐字摘抄，含标点）: {anchor!r}")
            ordering_valid = False
            continue
        if len(starts) > 1:
            if matched_by_fold:
                # 折叠路径的「出现 N 次」是标点 / 空白折叠对齐后的计数；模型按字面 anchor 逐字数
                # 往往是 0 次，必须点明计数口径，否则模型对不上、可能反复重交同一 anchor 耗尽重试
                reasons.append(
                    f"第 {idx} 条的 end_anchor 在原文窗口中按归一（NFC 与换行统一）及标点全/半角、空白折叠对齐后出现 {len(starts)} 次"
                    f"（逐字精确匹配可能为 0 次），无法唯一定位，请改用更长或更独特、与原文逐字一致的片段: {anchor!r}"
                )
            else:
                reasons.append(
                    f"第 {idx} 条的 end_anchor 在原文窗口中出现 {len(starts)} 次，无法唯一定位，"
                    f"请改用更长或更独特的片段: {anchor!r}"
                )
            ordering_valid = False
            continue
        end = starts[0] + match_len
        if ordering_valid and end <= pos:
            reasons.append(
                f"第 {idx} 条的 end_anchor 位置不在上一集结尾之后（各集范围必须连续推进、不重叠）: {anchor!r}"
            )
            ordering_valid = False
            continue
        ends.append(end)
        pos = end
    if reasons:
        raise _DraftRejected(reasons)
    if snap_whitespace_tail and ends[-1] < len(window) and not window[ends[-1] :].strip():
        ends[-1] = len(window)
    return ends


def _ledger_entry_from_draft(
    draft_ep: NarrationEpisodeDraft,
    *,
    num: int,
    source_rel: str,
    start: int,
    end: int,
    status: str,
) -> dict[str, Any]:
    """把单集草稿物化为账本条目。"""
    entry: dict[str, Any] = {
        "episode": num,
        "title": draft_ep.title,
        "script_file": episode_script_relpath(num),
        "source_range": {"source_file": source_rel, "start": start, "end": end},
        "hook": draft_ep.hook,
        "ledger_status": status,
    }
    if isinstance(draft_ep, DramaEpisodeDraft):
        entry["outline"] = {
            "story_beats": list(draft_ep.story_beats),
            "next_episode_teaser": draft_ep.next_episode_teaser,
        }
    return entry


def _language_of(project: Mapping[str, Any]) -> str | None:
    language = project.get("source_language")
    return language if isinstance(language, str) else None


def _source_changed_error(paths: list[str]) -> EpisodePlanningError:
    """构造源文已变动的拒绝错误：指名变动文件并指路全量重置。"""
    return EpisodePlanningError(
        f"源文件已被修改或移除：{'、'.join(paths)}。账本坐标绑定的是修改前的原文内容，继续规划会静默切出"
        "错误内容；请先调用 reset_episode_planning 做全量重置，再重新规划。"
    )


def _missing_source_range_error(nums: list[int]) -> EpisodePlanningError:
    """构造账本缺位置记录的拒绝错误：指名集号并指路全量重置。"""
    listed = "、".join(str(num) for num in nums[:_MISSING_RANGE_LISTED_LIMIT])
    if len(nums) > _MISSING_RANGE_LISTED_LIMIT:
        listed += f" 等 {len(nums)} 集"
    return EpisodePlanningError(
        f"账本中第 {listed} 没有原文范围记录（source_range），无法据此续接规划——这类条目的物理集文件"
        "就是它们的最终记录，既无法重造也无法确定下一批的起点；请先调用 reset_episode_planning 做"
        "全量重置（这些集的集文件会改名留底、下游产物不删），再重新规划。"
    )


# plan_episodes 开篇定位：novel 走「切分 / 创作」，screenplay 翻为「尊重作者分集 / 提取」。
# screenplay 二分支——剧本自带分集（任意形态）照用作者边界，无分集才按剧情弧语义切，
# 绝不按字数机械切；不依赖任何固定分集标记，靠模型语义识别作者写下的分集形态。
_PLAN_INTRO_NOVEL: tuple[str, ...] = (
    "你是短视频分集规划师。请把下面的小说原文片段切分为若干集，每一集都必须是一个完整的剧情弧，",
    "并在集尾留下让观众想看下一集的钩子。",
)
_PLAN_INTRO_SCREENPLAY: tuple[str, ...] = (
    "你是短视频分集规划师。下面是作者已写好的成品剧本片段，请尊重作者自带的分集、提取而非重切：",
    "- 若剧本自带分集（任意形态——分集标记、结构表、标题体系、分隔等，不要依赖任何固定标记或正则识别），",
    "  照用作者划定的每一集边界，title、hook 与分集大纲都取自剧本原文。",
    "- 若剧本没有任何分集线索，再按完整剧情弧语义切分，每一集都是一个完整的故事段落，绝不按字数机械切碎。",
)
# screenplay 在「切分规则」段补一条把上述意图落到 end_anchor 层的具体指令。
_PLAN_RULE_SCREENPLAY: str = (
    "- 优先照用作者的分集：剧本已划定每集边界时，end_anchor 取作者每集结尾处的原文片段，"
    "title / hook 也取自剧本（作者写明的集标题、集尾钩子）；剧本未分集时才按剧情弧自行切，绝不按字数硬凑集数。"
)
# drama 大纲条目说明：screenplay 优先照搬作者写下的故事节点 / 下集预告。
_PLAN_DRAMA_OUTLINE_NOVEL: str = (
    "- 每一集另给出 story_beats（本集故事节点列表，按顺序）"
    "与 next_episode_teaser（下集预告语；最后一集若后续未知可为 null）。"
)
_PLAN_DRAMA_OUTLINE_SCREENPLAY: str = (
    "- 每一集另给出 story_beats（本集故事节点列表，按顺序）"
    "与 next_episode_teaser（下集预告语；最后一集若后续未知可为 null）；"
    "剧本已写明本集节点 / 下集预告时照搬其原文，未写明再自行提炼。"
)


class EpisodePlanner:
    """分集规划器。``generator`` 为 None 时仅可构造，调用 plan() 会报错。"""

    def __init__(
        self,
        project_path: str | Path,
        generator: TextGenerator | None = None,
        *,
        max_attempts: int = _MAX_PLAN_ATTEMPTS,
    ):
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self.generator = generator
        self.max_attempts = max_attempts
        self.pm = ProjectManager(str(self.project_path.parent))

    @classmethod
    async def create(cls, project_path: str | Path) -> EpisodePlanner:
        """异步工厂：按项目配置创建文本后端（与剧本生成同一条 SCRIPT 任务配置链）。"""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name)
        return cls(project_path, generator)

    # ---------------------------------------------------------------- plan

    async def plan(self, instructions: str | None = None) -> PlanResult:
        """规划下一批集：从 planning_cursor 起的窗口产出剧情弧完整的集并提交账本。

        当前源文件已无剩余有效内容时按文件名序自动推进到下一个源文件；
        ``source_exhausted=True`` 表示全部源文件都已规划完毕。

        ``instructions`` 是可选的用户分集意见（如按章节对齐切分），strip 后为空视同未传；
        非空则原样注入规划 prompt 的中性「用户意见」分节，遵循强度由意见正文自行表达。规划按窗口
        分多批、意见不持久化，调用方须在每批 plan 调用都重复带上。

        新提交的集号若在磁盘上已有下游产物（剧本/step1/媒体，见
        :func:`lib.episode_ledger.has_downstream_products`），说明该集实际已被消费过
        （典型场景：先 ``reset_episode_planning`` 部分重置到更早集号、再带新 ``instructions``
        重新规划，新布局与原消费范围重叠）；这类集提交时直接标 ``stale``（产物不删除），
        随 ``PlanResult.stale_episodes`` 返回，不再需要额外确认——重置阶段已完成过一次
        已消费集确认。
        """
        planning_instructions = (instructions or "").strip() or None
        project = self.pm.load_project(self.project_name)
        # 手动预拆分上传等场景下磁盘可能已有账本无条目的孤儿派生集文件：不先补建条目，
        # 门禁看见空账本会直接放行，规划随后会把该集内容当无主原文重新生成并覆盖
        project = register_orphan_episode_entries(self.project_path, project)
        self._check_source_ranges(project)
        pre_call_sources = discover_sources(self.project_path)
        self._check_source_fingerprints(project, sources=pre_call_sources)
        # 提交时复核的基线只留指纹摘要，不为暂不参与本批规划的源文件常驻其全文：
        # discover_sources 已读入全部候选源文的原文用于计算这批指纹，本函数下方
        # 显式 del 释放该列表，避免大型多源项目在跨模型调用的等待期间叠加持有整套原文
        used_fingerprints = compute_source_fingerprints(pre_call_sources)

        def _pre_call_text(rel: str) -> str | None:
            return next((doc.text for doc in pre_call_sources if doc.rel_path == rel), None)

        start_ref = self._effective_start(project)
        source_rel, start = start_ref
        text = _pre_call_text(source_rel)
        if text is None:
            text = self._load_normalized_source(source_rel)
            used_fingerprints[source_rel] = compute_source_fingerprints([SourceDoc(rel_path=source_rel, text=text)])[
                source_rel
            ]
        if start > len(text):
            raise EpisodePlanningError(f"规划起点越界：{source_rel} 长度 {len(text)}，起点 {start}；请检查账本")
        while not text[start:].strip():
            next_rel = self._next_source_rel(source_rel)
            if next_rel is None:
                project = self._backfill_source_fingerprints_if_missing(project, used_fingerprints=used_fingerprints)
                return PlanResult(
                    episodes=[],
                    cursor=project.get("planning_cursor"),
                    source_exhausted=True,
                    total_planned=_count_planned_episodes(project),
                    ledger_stats=self._compute_ledger_stats(project),
                )
            source_rel, start = next_rel, 0
            text = _pre_call_text(source_rel)
            if text is None:
                text = self._load_normalized_source(source_rel)
                used_fingerprints[source_rel] = compute_source_fingerprints(
                    [SourceDoc(rel_path=source_rel, text=text)]
                )[source_rel]
        pre_call_sources = []  # 之后只需 used_fingerprints（摘要）与本批实际使用的 text，显式释放原文引用

        window_chars = self._setting_int(project, "planning_window_chars", DEFAULT_PLANNING_WINDOW_CHARS)
        max_episodes = self._setting_int(project, "planning_max_episodes", DEFAULT_PLANNING_MAX_EPISODES)
        remaining_chars = len(text) - start
        # 窗口弹性：剩余全文不足 1.2 倍窗口时直接吃到底，避免下一批只剩孤儿残余
        # 被迫单独成集（畸小集的机械成因）。系数 1.2 换来的浮动幅度足够小，
        # 不会让常规批次显著超出窗口设置的预期体量。
        window_end = len(text) if remaining_chars <= window_chars * 1.2 else start + window_chars
        window = text[start:window_end]
        window_is_final = window_end >= len(text)
        content_mode = str(project.get("content_mode") or "narration")
        draft_model: type[BaseModel] = DramaPlanDraft if content_mode == "drama" else NarrationPlanDraft
        language = _language_of(project)
        # 全局进度仅在有 instructions 时算、仅在有 instructions 时注入 prompt：
        # 无指令路径的 prompt 必须逐字保持不变（CONTEXT.md 对该路径有逐字一致的承诺）。
        progress: _PlanningProgress | None = None
        if planning_instructions:
            progress = _PlanningProgress(
                planned_count=_count_planned_episodes(project),
                remaining_units=self._remaining_units_from(source_rel, start, text, language),
                window_units=count_reading_units(window, language),
            )

        def _prompt(failure: list[str] | None) -> str:
            return _build_planning_prompt(
                project=project,
                window=window,
                window_is_final=window_is_final,
                max_episodes=max_episodes,
                content_mode=content_mode,
                context_entries=_context_entries(project),
                instructions=planning_instructions,
                progress=progress,
                failure=failure,
            )

        drafts, ends = await self._request_validated_drafts(
            draft_model,
            _prompt,
            window,
            snap_whitespace_tail=window_is_final,
            max_episodes=max_episodes,
        )

        summaries: list[EpisodePlanSummary] = []
        committed: dict[str, Any] = {"stale": []}

        def _commit(p: dict) -> None:
            # 锁内复核缺位置记录的条目：与指纹复核同一套逃生口——模型调用期间账本可能被
            # 并发写入（如另一条链路补建了手动预拆分集的条目），锁外那次快照不足以放行提交；
            # 先重跑一次孤儿登记补齐同一并发窗口内新出现的孤儿派生文件，再校验
            healed = register_orphan_episode_entries(self.project_path, p)
            p.clear()
            p.update(healed)
            self._check_source_ranges(p)
            if self._effective_start(p) != start_ref:
                raise PlanningConflictError("规划期间账本进度被并发修改，本次结果作废；请重新调用规划")
            # 锁内复核：模型调用期间源文件可能被外部改动，与锁外预检查同一套逃生口，
            # 复用同一错误提示——重试只会再次命中同一比对，须先重置
            current_sources = discover_sources(self.project_path)
            self._check_source_fingerprints(p, sources=current_sources)
            # 指纹比对只覆盖「已记录」的文件，存量项目补记路径上恒为空；而切分坐标与派生
            # 文件都基于本次调用读入的 used_fingerprints（覆盖入口快照 + 循环中途新发现的
            # 源文件），故直接比指纹堵住补记路径裸露的窗口——本次调用之前已锚定其它集号
            # 的源文、以及 _next_source_rel() 在快照之后才发现并读入的新源文件，若在模型
            # 调用期间被改动，同样会被这里拦下
            current_fingerprints = compute_source_fingerprints(current_sources)
            changed = sorted(rel for rel, fp in used_fingerprints.items() if current_fingerprints.get(rel) != fp)
            if changed:
                raise _source_changed_error(changed)
            episodes_list = [e for e in (p.get("episodes") or []) if e is not None]
            nums = [parse_episode_num(e.get("episode")) for e in episodes_list if isinstance(e, dict)]
            # 集号只在正整数域上推进：负数/0 集号属脏数据，不让它把新集编号拖成非正数
            next_num = max((n for n in nums if n is not None and n > 0), default=0) + 1
            prev = start
            for offset_idx, (draft_ep, rel_end) in enumerate(zip(drafts, ends, strict=True)):
                num = next_num + offset_idx
                abs_end = start + rel_end
                entry = _ledger_entry_from_draft(
                    draft_ep, num=num, source_rel=source_rel, start=prev, end=abs_end, status="planned"
                )
                # 新集号若在磁盘上已有剧本/step1/媒体产物（如重置到更早集号后重新规划、
                # 新布局与原消费范围重叠），说明该集实际已被消费过；标 stale 提示主 agent
                # 需重做下游产物，产物本身不删除
                if has_downstream_products(self.project_path, num, entry):
                    entry["ledger_status"] = "stale"
                    step1_path = script_review.step1_path(self.project_path, p, num)
                    entry[script_review.STALE_STEP1_REVISION_FIELD] = (
                        script_review.content_fingerprint(step1_path) if step1_path is not None else None
                    )
                    committed["stale"].append(num)
                episodes_list.append(entry)
                summaries.append(
                    EpisodePlanSummary(
                        episode=num,
                        title=draft_ep.title,
                        hook=draft_ep.hook,
                        reading_units=count_reading_units(text[prev:abs_end], language),
                        ledger_status=entry["ledger_status"],
                    )
                )
                prev = abs_end
            _sort_episodes_if_possible(episodes_list)
            p["episodes"] = episodes_list
            p["planning_cursor"] = {"source_file": source_rel, "offset": start + ends[-1]}
            p[SOURCE_FINGERPRINTS_KEY] = current_fingerprints
            self._reconcile_derived_files(p, {source_rel: text})
            committed["cursor"] = p["planning_cursor"]
            committed["exhausted"] = (
                window_is_final and not text[start + ends[-1] :].strip() and self._next_source_rel(source_rel) is None
            )

        final_project = self.pm.update_project(self.project_name, _commit)
        exhausted = bool(committed["exhausted"])
        return PlanResult(
            episodes=summaries,
            cursor=committed["cursor"],
            source_exhausted=exhausted,
            stale_episodes=list(committed["stale"]),
            total_planned=_count_planned_episodes(final_project),
            # 全局核对材料只在末批即耗尽时附上；常规批次只报「累计已规划 N 集」，
            # 避免主 agent 上下文被逐批膨胀（工具层渲染 total_planned 的那一行）
            ledger_stats=self._compute_ledger_stats(final_project) if exhausted else None,
        )

    # ------------------------------------------------------------- helpers

    async def _request_validated_drafts(
        self,
        draft_model: type[BaseModel],
        prompt_builder: Callable[[list[str] | None], str],
        window: str,
        *,
        snap_whitespace_tail: bool,
        max_episodes: int | None,
    ) -> tuple[list[NarrationEpisodeDraft], list[int]]:
        """LLM 调用 + schema/机械校验循环；重试 prompt 附上一轮失败原因。

        结构化输出被输出上限截断时 :class:`TextOutputTruncatedError` 直接短路本循环——
        重发同一份必然再截断的请求没有意义；追加本规划器特有的杠杆提示（调小窗口字数 /
        每批集数）后转为 :class:`EpisodePlanningError` 冒泡（见 docs/adr/0044）。

        后端结构化输出降级链耗尽的 :class:`StructuredOutputExhaustedError` 同样短路本循环，
        转为 :class:`EpisodePlanningError`，让智能体拿到「供应商结构化输出能力不足」的可读
        话术而非后端内部异常原文。
        """
        if self.generator is None:
            raise RuntimeError("TextGenerator 未初始化，请使用 EpisodePlanner.create() 工厂方法")
        failure: list[str] | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = await self.generator.generate(
                    TextGenerationRequest(
                        prompt=prompt_builder(failure),
                        response_schema=draft_model,
                        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                    ),
                    project_name=self.project_name,
                )
            except TextOutputTruncatedError as exc:
                raise EpisodePlanningError(
                    f"{exc}也可调小项目设置 planning_window_chars（单批窗口字数）或 "
                    "planning_max_episodes（单批集数上限）以缩小本批输出体量后重试。"
                ) from exc
            except StructuredOutputExhaustedError as exc:
                # 后端的降级链已把各档与档内重试都走完，本层再重试只是重复同一条必败路径。
                raise EpisodePlanningError(str(exc)) from exc
            try:
                draft = self._parse_draft(result.text, draft_model)
                drafts = list(getattr(draft, "episodes"))
                if max_episodes is not None and len(drafts) > max_episodes:
                    logger.warning(
                        "规划输出 %d 集超过每批上限 %d，截断保留前 %d 集（其余留给下一批）",
                        len(drafts),
                        max_episodes,
                        max_episodes,
                    )
                    drafts = drafts[:max_episodes]
                ends = _resolve_boundaries(window, drafts, snap_whitespace_tail=snap_whitespace_tail)
                return drafts, ends
            except _DraftRejected as exc:
                failure = exc.reasons
                logger.warning("分集规划第 %d/%d 次尝试未通过校验：%s", attempt, self.max_attempts, exc)
        raise EpisodePlanningError(
            f"分集规划连续 {self.max_attempts} 次未通过校验，最后一轮原因：{'; '.join(failure or [])}"
        )

    @staticmethod
    def _parse_draft(response_text: str, draft_model: type[BaseModel]) -> BaseModel:
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("分集规划输出不是合法 JSON（%s）；模型原始输出：%s", exc, truncate_for_log(response_text))
            raise _DraftRejected([f"输出不是合法 JSON：{exc}"]) from exc
        try:
            return draft_model.model_validate(data)
        except ValidationError as exc:
            issues = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
            logger.warning("分集规划输出不符合 schema（%s）；模型原始输出：%s", issues, truncate_for_log(response_text))
            raise _DraftRejected([f"输出不符合 schema：{issues}"]) from exc

    def _effective_start(self, project: Mapping[str, Any]) -> tuple[str, int]:
        """下一批规划起点：以账本中最后一个锚定集的范围末尾为准，游标更靠后时取游标。"""
        last: tuple[str, int] | None = None
        best_num: int | None = None
        for entry in project.get("episodes") or []:
            if not isinstance(entry, dict):
                continue
            num = parse_episode_num(entry.get("episode"))
            source_range = entry.get("source_range")
            if num is None or not isinstance(source_range, Mapping):
                continue
            rel = source_range.get("source_file")
            end = source_range.get("end")
            if isinstance(rel, str) and isinstance(end, int) and not isinstance(end, bool):
                if best_num is None or num > best_num:
                    best_num = num
                    last = (rel, end)
        cursor = project.get("planning_cursor")
        cur: tuple[str, int] | None = None
        if isinstance(cursor, Mapping):
            rel = cursor.get("source_file")
            offset = cursor.get("offset")
            # 负 offset 会让后续切片静默从尾部取段，按非法游标忽略
            if isinstance(rel, str) and isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0:
                cur = (rel, offset)
        if last is not None and cur is not None:
            if cur[0] == last[0]:
                return (last[0], max(last[1], cur[1]))
            # 文件不同时按源文件顺序取更靠后者，与同文件 max 语义一致：游标滞后取账本末尾，
            # 游标已合法推进到后一个文件则取游标，避免重复规划该文件前缀
            rels = [doc.rel_path for doc in discover_sources(self.project_path)]
            last_idx = rels.index(last[0]) if last[0] in rels else None
            cur_idx = rels.index(cur[0]) if cur[0] in rels else None
            if last_idx is None:
                return cur if cur_idx is not None else last
            if cur_idx is None or cur_idx < last_idx:
                return last
            return cur
        if last is not None:
            return last
        if cur is not None:
            return cur
        sources = discover_sources(self.project_path)
        if not sources:
            raise EpisodePlanningError("source/ 下没有可规划的源文件（.txt/.md），请先上传小说原文")
        return (sources[0].rel_path, 0)

    def _remaining_units_from(self, source_rel: str, start: int, text: str, language: str | None) -> int:
        """当前源文窗口起点之后全部 + 后续源文件总量，按阅读单位计（全局进度提示用）。

        ``discover_sources`` 只调一次：它已返回全部源文件的归一化全文，定位到
        ``source_rel`` 后直接复用后续文件的 ``doc.text``，避免按源文件数循环重复
        调用 ``discover_sources``（每次都会重新读取并归一化目录下全部源文件）。
        """
        total = count_reading_units(text[start:], language)
        sources = discover_sources(self.project_path)
        rels = [doc.rel_path for doc in sources]
        try:
            idx = rels.index(source_rel)
        except ValueError:
            return total
        for doc in sources[idx + 1 :]:
            total += count_reading_units(doc.text, language)
        return total

    def _next_source_rel(self, rel: str) -> str | None:
        """按文件名序返回 ``rel`` 之后的下一个候选源文件；``rel`` 不在候选或已是最后一个时返回 None。"""
        rels = [doc.rel_path for doc in discover_sources(self.project_path)]
        try:
            idx = rels.index(rel)
        except ValueError:
            return None
        return rels[idx + 1] if idx + 1 < len(rels) else None

    def _load_normalized_source(self, rel: str) -> str:
        try:
            path = safe_join(self.project_path, rel, allow_base=True)
        except PathTraversalError as exc:
            raise EpisodePlanningError(f"源文件路径越出项目目录：{rel}") from exc
        if not path.is_file():
            raise EpisodePlanningError(f"源文件不存在：{rel}")
        try:
            return normalize_source_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise EpisodePlanningError(f"源文件读取失败：{rel}: {exc}") from exc

    def _check_source_fingerprints(self, project: Mapping[str, Any], *, sources: list[SourceDoc] | None = None) -> None:
        """比对账本记录的源文指纹与当前源文，不一致（含记录文件已消失）即拒绝规划。

        存量项目无记录 / 新源文件尚未记录时不比对（首次 plan 只补记不报错）。``sources`` 由
        调用方传入以复用已读取的源文快照，缺省时现读一次。
        """
        docs = sources if sources is not None else discover_sources(self.project_path)
        mismatched = mismatched_source_fingerprints(project.get(SOURCE_FINGERPRINTS_KEY), docs)
        if mismatched:
            raise _source_changed_error(mismatched)

    @staticmethod
    def _check_source_ranges(project: Mapping[str, Any]) -> None:
        """账本存在无位置记录的条目即拒绝规划（老项目遗留，指路全量重置）。

        没有 ``source_range`` 的集既无法重造派生文件、也无法证明其覆盖的原文范围，
        续接规划只会与它重叠或遗漏。消费链路（剧本 / 媒体 / 状态 / 导出）不受影响。
        """
        missing = episodes_without_source_range(project)
        if missing:
            raise _missing_source_range_error(missing)

    def _backfill_source_fingerprints_if_missing(
        self, project: Mapping[str, Any], *, used_fingerprints: dict[str, str]
    ) -> dict:
        """存量项目在 ``source_exhausted`` 早退路径上补记指纹：该路径不经过 ``plan()`` 的
        提交闭包，若跳过会让「首次 plan 补记指纹」对已耗尽游标的存量项目失效——后续等长
        编辑旧正文都因无基线可比而放行。已有指纹的项目直接原样返回，不重复计算。

        ``used_fingerprints`` 是本次 plan() 调用锁外读入的源文摘要基线（入口快照 + 循环
        中途新发现的源文件）：锁内复核与常规提交路径同一套逃生口——若 plan() 保存快照后、
        本闭包读取前源文被改动，直接把变更内容登记为基线会让这次变更永久失去可比对象，
        须先拒绝。
        """
        if project.get(SOURCE_FINGERPRINTS_KEY) is not None:
            return dict(project)

        def _commit(p: dict) -> None:
            current_sources = discover_sources(self.project_path)
            self._check_source_fingerprints(p, sources=current_sources)
            current_fingerprints = compute_source_fingerprints(current_sources)
            changed = sorted(rel for rel, fp in used_fingerprints.items() if current_fingerprints.get(rel) != fp)
            if changed:
                raise _source_changed_error(changed)
            p[SOURCE_FINGERPRINTS_KEY] = current_fingerprints

        return self.pm.update_project(self.project_name, _commit)

    @staticmethod
    def _setting_int(project: Mapping[str, Any], key: str, default: int) -> int:
        value = project.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value
        if value is not None:
            logger.warning("项目设置 %s=%r 非法，回退内部默认 %d", key, value, default)
        return default

    def _reconcile_derived_files(self, project: Mapping[str, Any], text_cache: dict[str, str]) -> None:
        """按账本全量对账派生集文件：重写每一集、删除账本之外的残留。

        账本条目此刻必然都带 source_range（提交前经 ``_check_source_ranges`` 门禁），
        缺记录仍按损坏中止提交。余文文件 ``_remaining.txt`` 已由账本游标取代，一并
        清理。每次提交全量对账使中途崩溃后重跑即可自愈。

        两阶段执行：先全量校验并构建写入计划，全部通过后再统一落盘——锚定集
        原文范围非法或源文不可读时在校验阶段抛错中止提交（账本写回随之回滚，
        派生文件未动），保证"提交成功 ⇒ 账本内每一集的派生文件已按账本重写"；
        落盘阶段的环境性失败（磁盘等）靠重跑自愈。残留文件删除失败仅告警——
        残留不在账本中，账本驱动的下游不会读到它。
        """
        source_dir = self.project_path / "source"
        # source/ 是符号链接时拒绝写入：派生文件会落到链接目标（可能在项目外）
        if source_dir.is_symlink():
            raise EpisodePlanningError("source/ 不能是符号链接，拒绝派生集文件")
        keep: set[int] = set()
        writes: list[tuple[Path, str]] = []
        for entry in project.get("episodes") or []:
            if not isinstance(entry, dict):
                continue
            num = parse_episode_num(entry.get("episode"))
            if num is None:
                continue
            keep.add(num)
            source_range = entry.get("source_range")
            if not isinstance(source_range, Mapping):
                raise EpisodePlanningError(f"第 {num} 集缺少原文范围记录，无法完成派生文件对账，提交已中止")
            rel = source_range.get("source_file")
            seg_start = source_range.get("start")
            seg_end = source_range.get("end")
            if (
                not isinstance(rel, str)
                or not isinstance(seg_start, int)
                or not isinstance(seg_end, int)
                or isinstance(seg_start, bool)
                or isinstance(seg_end, bool)
            ):
                raise EpisodePlanningError(f"第 {num} 集原文范围记录非法，无法完成派生文件对账，提交已中止")
            text = text_cache.get(rel)
            if text is None:
                try:
                    text = self._load_normalized_source(rel)
                except EpisodePlanningError as exc:
                    raise EpisodePlanningError(f"第 {num} 集派生文件重写失败，提交已中止：{exc}") from exc
                text_cache[rel] = text
            # Python 切片对负值/越界静默容忍，脏坐标会写出与账本不符的内容，必须显式拦截
            if not 0 <= seg_start <= seg_end <= len(text):
                raise EpisodePlanningError(
                    f"第 {num} 集原文范围越界（start={seg_start}，end={seg_end}，源文长度 {len(text)}），"
                    "无法完成派生文件对账，提交已中止"
                )
            episode_path = source_dir / f"episode_{num}.txt"
            # 文件级符号链接同样拒绝：write_text 会跟随链接把内容写到链接目标（可能在项目外）
            if episode_path.is_symlink():
                raise EpisodePlanningError(f"第 {num} 集派生文件是符号链接，拒绝写入，提交已中止")
            writes.append((episode_path, text[seg_start:seg_end]))
        # 校验全部通过后统一落盘：校验类失败不会留下按新布局部分重写的派生文件
        source_dir.mkdir(exist_ok=True)
        for episode_path, content in writes:
            episode_path.write_text(content, encoding="utf-8")
        for num, path in discover_episode_files(self.project_path).items():
            if num not in keep:
                try:
                    path.unlink()
                except OSError as exc:
                    logger.warning("残留派生文件清理失败（不阻断提交）：%s: %s", path, exc)
        remaining = source_dir / "_remaining.txt"
        if remaining.is_file():
            try:
                remaining.unlink()
            except OSError as exc:
                logger.warning("余文文件清理失败（不阻断提交）：%s: %s", remaining, exc)

    def _compute_ledger_stats(self, project: Mapping[str, Any]) -> LedgerStats:
        """账本现算全局体量分布：累计集数、最小 5 集、体量中位数（供偏差核对用）。

        读原文按 source_range 切片计体量。无位置记录或原文读取失败的条目跳过，
        不阻断整体统计（核对材料本身是尽力而为、非提交前置校验）。
        """
        language = _language_of(project)
        text_cache: dict[str, str] = {}
        units_by_episode: dict[int, int] = {}
        for entry in project.get("episodes") or []:
            if not isinstance(entry, dict):
                continue
            num = parse_episode_num(entry.get("episode"))
            if num is None:
                continue
            source_range = entry.get("source_range")
            if not isinstance(source_range, Mapping):
                continue
            rel = source_range.get("source_file")
            start = source_range.get("start")
            end = source_range.get("end")
            if (
                not isinstance(rel, str)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or isinstance(start, bool)
                or isinstance(end, bool)
            ):
                continue
            text = text_cache.get(rel)
            if text is None:
                try:
                    text = self._load_normalized_source(rel)
                except EpisodePlanningError:
                    continue
                text_cache[rel] = text
            if not 0 <= start <= end <= len(text):
                continue
            units_by_episode[num] = count_reading_units(text[start:end], language)

        ordered = sorted(units_by_episode.items(), key=lambda pair: (pair[1], pair[0]))
        values = sorted(units_by_episode.values())
        target = project.get("episode_target_units")
        return LedgerStats(
            total_episodes=_count_planned_episodes(project),
            smallest=ordered[:5],
            median_units=int(round(statistics.median(values))) if values else None,
            target_units=target if isinstance(target, int) and not isinstance(target, bool) and target >= 1 else None,
        )


def _sort_episodes_if_possible(episodes: list[Any]) -> None:
    """全部集号可解析时按集号排序，否则保持原序。"""
    if all(isinstance(e, dict) and parse_episode_num(e.get("episode")) is not None for e in episodes):
        episodes.sort(key=lambda e: parse_episode_num(e["episode"]) or 0)


def _count_planned_episodes(project: Mapping[str, Any]) -> int:
    """账本现算已规划集数（含全部 ledger_status），供全局进度提示使用。"""
    return sum(
        1
        for entry in (project.get("episodes") or [])
        if isinstance(entry, dict) and parse_episode_num(entry.get("episode")) is not None
    )


def _context_entries(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    """已规划末尾若干集的 标题+钩子，作为续写连贯性上下文。

    只取有位置记录的条目：没有 source_range 的集不是本机制规划出来的，它的标题/钩子
    未必出自同一套分集口径，不拿来当续写基准。
    """
    anchored: list[tuple[int, dict[str, Any]]] = []
    for entry in project.get("episodes") or []:
        if not isinstance(entry, dict):
            continue
        num = parse_episode_num(entry.get("episode"))
        if num is None or not isinstance(entry.get("source_range"), Mapping):
            continue
        anchored.append((num, entry))
    anchored.sort(key=lambda pair: pair[0])
    return [e for _, e in anchored[-_CONTEXT_EPISODES_LIMIT:]]


def _build_planning_prompt(
    *,
    project: Mapping[str, Any],
    window: str,
    window_is_final: bool,
    max_episodes: int | None,
    content_mode: str,
    context_entries: list[dict[str, Any]],
    instructions: str | None,
    failure: list[str] | None,
    progress: _PlanningProgress | None = None,
) -> str:
    """规划 prompt。仅面向文本模型，不做 i18n。

    ``instructions`` 非空时注入一个中性的「用户意见」分节（遵循强度由意见正文自行表达，
    模板不加强度限定词）；为空则不注入，prompt 与无意见时逐字一致。``progress`` 非 None 时
    注入「全局进度」分节（调用方只在 instructions 非空时传入）。
    """
    overview = project.get("overview") or {}
    unit_name = reading_unit_noun(_language_of(project))
    target_units = project.get("episode_target_units")
    is_screenplay = resolve_source_kind(project) == "screenplay"

    lines: list[str] = [
        *(_PLAN_INTRO_SCREENPLAY if is_screenplay else _PLAN_INTRO_NOVEL),
        "",
        "# 项目信息",
        f"- 内容模式：{'剧集动画（drama）' if content_mode == 'drama' else '说书旁白（narration）'}",
    ]
    synopsis = overview.get("synopsis") if isinstance(overview, Mapping) else None
    if synopsis:
        lines.append(f"- 故事概述：{synopsis}")
    genre = overview.get("genre") if isinstance(overview, Mapping) else None
    if genre:
        lines.append(f"- 题材：{genre}")
    if isinstance(target_units, int) and not isinstance(target_units, bool) and target_units >= 1:
        lines.append(f"- 每集目标体量：约 {target_units} {unit_name}（允许为剧情完整性上下浮动）")
    else:
        lines.append("- 每集目标体量：未设置，请按短视频节奏自行把握（以剧情弧完整优先）")
    if max_episodes is not None:
        lines.append(f"- 本批最多规划 {max_episodes} 集")

    if context_entries:
        lines += ["", "# 已规划的前情（已定上下文，不可改动，续着它往下规划）"]
        for entry in context_entries:
            title = entry.get("title") or "（无标题）"
            hook = entry.get("hook") or ""
            lines.append(f"- 第 {entry.get('episode')} 集《{title}》 钩子：{hook}")

    if instructions:
        lines += ["", USER_INSTRUCTIONS_HEADER, instructions]
    if progress is not None:
        lines += [
            "",
            "# 全局进度",
            f"- 已规划 {progress.planned_count} 集",
            f"- 未规划余量约 {progress.remaining_units} {unit_name}（含本窗口，含后续源文件）",
            f"- 本窗口为其中前 {progress.window_units} {unit_name}",
            "- 若用户意见含总集数、按章节对齐等全局约束，请结合以上进度与余量换算本批的切分节奏与集数。",
        ]

    lines += [
        "",
        "# 切分规则",
        "- 每一集给出 title（吸引人的短标题）、hook（集尾钩子说明：这一刀为什么切在这、给观众留了什么悬念）、",
        "  end_anchor（本集结尾处的原文片段，10~30 个字符，必须从下方原文中逐字摘抄、含标点，且在整段原文中唯一出现；",
        "  本集内容 = 上一集结尾之后到该片段末尾为止的全部原文）。",
    ]
    if is_screenplay:
        lines.append(_PLAN_RULE_SCREENPLAY)
    if content_mode == "drama":
        lines.append(_PLAN_DRAMA_OUTLINE_SCREENPLAY if is_screenplay else _PLAN_DRAMA_OUTLINE_NOVEL)
    lines += [
        "- 各集按顺序排列，end_anchor 位置必须严格递增（范围连续、不重叠、不留空洞）。",
    ]
    if window_is_final:
        lines.append("- 这段原文已包含全文结尾：请规划到结尾，最后一集的 end_anchor 取全文结尾处的片段，不要留尾巴。")
    else:
        lines.append("- 这段原文只是全文的一个窗口：窗口尾部剧情弧不完整的内容不要硬凑成集，留给下一批规划即可。")
    lines.append("- 只输出符合 schema 的 JSON，不要输出其他内容。")

    if failure:
        lines += ["", "# 上一轮输出未通过校验，请针对性修正后重新输出"]
        lines += [f"- {reason}" for reason in failure]

    lines += ["", "# 剧本原文片段" if is_screenplay else "# 小说原文片段", "---", window, "---"]
    return "\n".join(lines)
