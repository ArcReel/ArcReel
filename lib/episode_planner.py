"""分集规划服务：读源文窗口 → 调项目配置的文本模型 → 写分集账本并派生集文件。

plan() 从 planning_cursor 起取一个源文窗口，由文本模型一次规划出窗口内所有
剧情弧完整的集（标题/钩子/切分锚点；drama 另含分集大纲），schema 强约束 +
锚点存在性/唯一性/连续性机械校验，失败自动重试并附上一轮失败原因。
replan(from_episode, instructions) 在已规划范围内按用户自由文本意见局部重排；
范围跨多个源文件时按文件拆为多个片段独立重切（单集不跨文件，文件边界即集边界）。

写入阶段在同一把项目锁内完成：写账本 + 按账本重写派生集文件 + 清理账本之外
的残留派生文件（含余文文件），下游读到的 ``source/episode_N.txt`` 永远与账本
一致。窗口字数与每批集数上限为内部默认，project.json 顶层
``planning_window_chars`` / ``planning_max_episodes`` 可覆盖。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.episode_ledger import (
    backfill_episode_ledger,
    discover_episode_files,
    discover_sources,
    normalize_source_text,
    parse_episode_num,
)
from lib.project_manager import ProjectManager, resolve_source_kind
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_metrics import count_reading_units
from lib.text_utils import strip_json_code_fences

logger = logging.getLogger(__name__)

# 窗口/批量内部默认值；project.json 顶层同名字段可覆盖
DEFAULT_PLANNING_WINDOW_CHARS = 30000
DEFAULT_PLANNING_MAX_EPISODES = 20

PLANNING_MAX_OUTPUT_TOKENS = 16000

# LLM 输出未通过 schema / 机械校验时的总尝试次数（含首次）
_MAX_PLAN_ATTEMPTS = 3

# 注入 prompt 的已规划上下文条数上限（保持续写连贯，不膨胀 prompt）
_CONTEXT_EPISODES_LIMIT = 5


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
class PlanResult:
    episodes: list[EpisodePlanSummary]
    cursor: dict[str, Any] | None
    source_exhausted: bool = False
    stale_episodes: list[int] = field(default_factory=list)
    settings_updated: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplanConfirmationRequired:
    """重排波及已消费集，需要显式确认后（confirm_consumed=True）才执行。"""

    consumed_episodes: list[int]


@dataclass
class _ReplanSlice:
    """单个源文件内的重排片段：闭合原文区间，独立重切。"""

    source_rel: str
    start: int
    end: int


@dataclass
class _ReplanScope:
    """重排范围：受影响条目（按集号升序）及其覆盖的文件内片段（按集号序逐文件分段）。"""

    slices: list[_ReplanSlice]
    affected: list[tuple[int, dict[str, Any]]]


@dataclass
class _PlannedEpisode:
    """重排新布局中的一集：草稿 + 源文件内绝对范围（集号按列表顺序自 from_episode 推导）。"""

    draft: NarrationEpisodeDraft
    source_rel: str
    start: int
    end: int


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


class NarrationReplanDraft(NarrationPlanDraft):
    """replan 额外承载全局性意见的结构化回写（每集体量）。"""

    episode_target_units: int | None = Field(default=None, ge=1)


class DramaReplanDraft(DramaPlanDraft):
    episode_target_units: int | None = Field(default=None, ge=1)


class _DraftRejected(Exception):
    """单轮 LLM 输出被 schema / 机械校验拒绝；reasons 注入下一轮重试 prompt。"""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _resolve_boundaries(
    window: str,
    drafts: list[NarrationEpisodeDraft],
    *,
    cover_to_end: bool,
    snap_whitespace_tail: bool = False,
) -> list[int]:
    """把每集 end_anchor 解析为窗口内相对结束偏移，校验存在/唯一/连续。

    范围由锚点构造性保证连续不重叠：第 i 集 = [第 i-1 集末尾, 第 i 集锚点末尾)。
    末尾只剩空白时把最后一集贴齐到窗口末尾（``cover_to_end`` 或
    ``snap_whitespace_tail`` 任一生效——前者是 replan 闭合范围，后者是 plan 的
    全文结尾窗口，贴齐后每个字符都归属某一集）。``cover_to_end=True`` 时残留
    非空白尾巴视为校验失败。
    """
    reasons: list[str] = []
    ends: list[int] = []
    pos = 0
    ordering_valid = True
    for idx, ep in enumerate(drafts, start=1):
        anchor = ep.end_anchor
        # str.count 只统计非重叠匹配，会把重叠出现（如 "aaaa" 中的 "aaa"）误判为唯一；
        # 步进 1 滑动查找收集所有起点，按允许重叠的口径判定 0/1/多次
        starts: list[int] = []
        found = window.find(anchor)
        while found != -1:
            starts.append(found)
            found = window.find(anchor, found + 1)
        if not starts:
            reasons.append(f"第 {idx} 条的 end_anchor 在原文窗口中不存在（必须逐字摘抄，含标点）: {anchor!r}")
            ordering_valid = False
            continue
        if len(starts) > 1:
            reasons.append(
                f"第 {idx} 条的 end_anchor 在原文窗口中出现 {len(starts)} 次，无法唯一定位，"
                f"请改用更长或更独特的片段: {anchor!r}"
            )
            ordering_valid = False
            continue
        end = starts[0] + len(anchor)
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
    if (cover_to_end or snap_whitespace_tail) and ends[-1] < len(window) and not window[ends[-1] :].strip():
        ends[-1] = len(window)
    if cover_to_end and ends[-1] < len(window):
        raise _DraftRejected(["最后一集的 end_anchor 必须覆盖到这段原文的末尾（本次范围是闭合的，不能留尾巴）"])
    return ends


def _ledger_entry_from_draft(
    draft_ep: NarrationEpisodeDraft,
    *,
    num: int,
    source_rel: str,
    start: int,
    end: int,
    status: str,
    script_file: str | None = None,
) -> dict[str, Any]:
    """把单集草稿物化为账本条目（plan / replan 共用）。"""
    entry: dict[str, Any] = {
        "episode": num,
        "title": draft_ep.title,
        "script_file": script_file or f"scripts/episode_{num}.json",
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
    """分集规划器。``generator`` 为 None 时仅可构造，调用 plan/replan 会报错。"""

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

    async def plan(self) -> PlanResult:
        """规划下一批集：从 planning_cursor 起的窗口产出剧情弧完整的集并提交账本。

        当前源文件已无剩余有效内容时按文件名序自动推进到下一个源文件；
        ``source_exhausted=True`` 表示全部源文件都已规划完毕。
        """
        project = backfill_episode_ledger(self.project_path, self.pm.load_project(self.project_name))
        start_ref = self._effective_start(project)
        source_rel, start = start_ref
        text = self._load_normalized_source(source_rel)
        if start > len(text):
            raise EpisodePlanningError(f"规划起点越界：{source_rel} 长度 {len(text)}，起点 {start}；请检查账本")
        while not text[start:].strip():
            next_rel = self._next_source_rel(source_rel)
            if next_rel is None:
                return PlanResult(episodes=[], cursor=project.get("planning_cursor"), source_exhausted=True)
            source_rel, start = next_rel, 0
            text = self._load_normalized_source(source_rel)

        window_chars = self._setting_int(project, "planning_window_chars", DEFAULT_PLANNING_WINDOW_CHARS)
        max_episodes = self._setting_int(project, "planning_max_episodes", DEFAULT_PLANNING_MAX_EPISODES)
        window = text[start : start + window_chars]
        window_is_final = start + window_chars >= len(text)
        content_mode = str(project.get("content_mode") or "narration")
        draft_model: type[BaseModel] = DramaPlanDraft if content_mode == "drama" else NarrationPlanDraft

        def _prompt(failure: list[str] | None) -> str:
            return _build_planning_prompt(
                project=project,
                window=window,
                window_is_final=window_is_final,
                max_episodes=max_episodes,
                content_mode=content_mode,
                context_entries=_context_entries(project),
                instructions=None,
                fixed_boundary=False,
                failure=failure,
            )

        drafts, ends, _draft = await self._request_validated_drafts(
            draft_model,
            _prompt,
            window,
            cover_to_end=False,
            snap_whitespace_tail=window_is_final,
            max_episodes=max_episodes,
        )

        language = _language_of(project)
        summaries: list[EpisodePlanSummary] = []
        committed: dict[str, Any] = {}

        def _commit(p: dict) -> None:
            fresh = backfill_episode_ledger(self.project_path, p)
            p.clear()
            p.update(fresh)
            if self._effective_start(p) != start_ref:
                raise PlanningConflictError("规划期间账本进度被并发修改，本次结果作废；请重新调用规划")
            episodes_list = [e for e in (p.get("episodes") or []) if e is not None]
            nums = [parse_episode_num(e.get("episode")) for e in episodes_list if isinstance(e, dict)]
            # 集号只在正整数域上推进：负数/0 集号属脏数据，不让它把新集编号拖成非正数
            next_num = max((n for n in nums if n is not None and n > 0), default=0) + 1
            prev = start
            for offset_idx, (draft_ep, rel_end) in enumerate(zip(drafts, ends, strict=True)):
                num = next_num + offset_idx
                abs_end = start + rel_end
                episodes_list.append(
                    _ledger_entry_from_draft(
                        draft_ep, num=num, source_rel=source_rel, start=prev, end=abs_end, status="planned"
                    )
                )
                summaries.append(
                    EpisodePlanSummary(
                        episode=num,
                        title=draft_ep.title,
                        hook=draft_ep.hook,
                        reading_units=count_reading_units(text[prev:abs_end], language),
                        ledger_status="planned",
                    )
                )
                prev = abs_end
            _sort_episodes_if_possible(episodes_list)
            p["episodes"] = episodes_list
            p["planning_cursor"] = {"source_file": source_rel, "offset": start + ends[-1]}
            self._reconcile_derived_files(p, {source_rel: text})
            committed["cursor"] = p["planning_cursor"]
            committed["exhausted"] = (
                window_is_final and not text[start + ends[-1] :].strip() and self._next_source_rel(source_rel) is None
            )

        self.pm.update_project(self.project_name, _commit)
        return PlanResult(
            episodes=summaries,
            cursor=committed["cursor"],
            source_exhausted=bool(committed["exhausted"]),
        )

    # --------------------------------------------------------------- replan

    async def replan(
        self,
        from_episode: int,
        instructions: str,
        *,
        confirm_consumed: bool = False,
    ) -> PlanResult | ReplanConfirmationRequired:
        """按用户自由文本意见局部重排 ``from_episode`` 起的已规划范围。

        重排范围是闭合的（到当前已规划末尾），新布局必须完整覆盖；之前的集作为
        已定上下文输入。范围跨多个源文件时按文件拆为多个片段独立重切（单集不跨
        文件，文件边界即集边界），集号跨片段连续编号、每个片段各自闭合。波及
        已消费集且未 ``confirm_consumed`` 时不执行，返回受影响清单等待显式确认；
        确认后这些集号在新布局中标 stale。全局性意见（每集体量）回写项目设置，
        后续批次自动继承。
        """
        project = backfill_episode_ledger(self.project_path, self.pm.load_project(self.project_name))
        scope = self._replan_scope(project, from_episode)
        consumed = [num for num, entry in scope.affected if entry.get("ledger_status") == "consumed"]
        if consumed and not confirm_consumed:
            return ReplanConfirmationRequired(consumed_episodes=consumed)

        texts: dict[str, str] = {}
        for sl in scope.slices:
            if sl.source_rel not in texts:
                texts[sl.source_rel] = self._load_normalized_source(sl.source_rel)
            # Python 切片对负值/越界静默容忍，脏范围必须在烧模型调用之前显式拦截
            if not 0 <= sl.start < sl.end <= len(texts[sl.source_rel]):
                raise EpisodePlanningError(
                    f"账本重排范围无效：{sl.source_rel} 长度 {len(texts[sl.source_rel])}，"
                    f"片段 [{sl.start}, {sl.end})；请检查账本"
                )
        content_mode = str(project.get("content_mode") or "narration")
        draft_model: type[BaseModel] = DramaReplanDraft if content_mode == "drama" else NarrationReplanDraft

        base_context = _context_entries(project, before_episode=from_episode)
        planned: list[_PlannedEpisode] = []
        target_units: int | None = None
        total_slices = len(scope.slices)
        for slice_idx, sl in enumerate(scope.slices):
            window = texts[sl.source_rel][sl.start : sl.end]
            recent = [
                {"episode": from_episode + idx, "title": ep.draft.title, "hook": ep.draft.hook}
                for idx, ep in enumerate(planned)
            ]
            context = (base_context + recent)[-_CONTEXT_EPISODES_LIMIT:]

            # 闭包在本轮迭代内被 _request_validated_drafts 消费完毕，捕获循环变量无晚绑定风险
            def _prompt(failure: list[str] | None) -> str:
                return _build_planning_prompt(
                    project=project,
                    window=window,
                    window_is_final=False,
                    max_episodes=None,
                    content_mode=content_mode,
                    context_entries=context,
                    instructions=instructions,
                    fixed_boundary=True,
                    slice_position=(slice_idx + 1, total_slices),
                    failure=failure,
                )

            drafts, ends, draft = await self._request_validated_drafts(
                draft_model,
                _prompt,
                window,
                cover_to_end=True,
                max_episodes=None,
            )
            slice_units = getattr(draft, "episode_target_units", None)
            if slice_units is not None:
                # 各段对同一份全局意见的解读偶有出入属模型噪音：告警留痕，以后一段为准，不阻塞
                if target_units is not None and target_units != slice_units:
                    logger.warning(
                        "跨文件重排各段回报的每集体量不一致（%s → %s），以后一段为准", target_units, slice_units
                    )
                target_units = slice_units
            prev = sl.start
            for draft_ep, rel_end in zip(drafts, ends, strict=True):
                abs_end = sl.start + rel_end
                planned.append(_PlannedEpisode(draft=draft_ep, source_rel=sl.source_rel, start=prev, end=abs_end))
                prev = abs_end

        language = _language_of(project)
        summaries: list[EpisodePlanSummary] = []
        committed: dict[str, Any] = {"stale": [], "settings": {}}

        def _commit(p: dict) -> None:
            fresh = backfill_episode_ledger(self.project_path, p)
            p.clear()
            p.update(fresh)
            # 锁外已成功解析过一次，锁内解析失败只可能源于并发修改，按冲突上报（可重试）
            try:
                current = self._replan_scope(p, from_episode)
            except EpisodePlanningError as exc:
                raise PlanningConflictError("重排期间账本被并发修改，本次结果作废；请重新调用重排") from exc
            # 比较 affected 原始条目而非合并后的 slices：并发重排若改了内部切分但闭合范围相同，
            # slices 比不出差异，会静默覆盖对方刚提交的新切法（状态变化由下方已消费分支单独处理）
            if [(num, entry.get("source_range")) for num, entry in current.affected] != [
                (num, entry.get("source_range")) for num, entry in scope.affected
            ]:
                raise PlanningConflictError("重排期间账本被并发修改，本次结果作废；请重新调用重排")
            now_consumed = [num for num, entry in current.affected if entry.get("ledger_status") == "consumed"]
            # 用户确认的是读取时刻的已消费清单，期间新消费的集不在确认范围内，必须重新确认
            if any(num not in consumed for num in now_consumed):
                raise PlanningConflictError("重排期间出现新的已消费集，需重新确认后再执行")
            old_status = {num: str(entry.get("ledger_status") or "") for num, entry in current.affected}
            old_script_file = {num: entry.get("script_file") for num, entry in current.affected}
            affected_nums = {num for num, _ in current.affected}
            episodes_list = [
                e
                for e in (p.get("episodes") or [])
                if not (isinstance(e, dict) and parse_episode_num(e.get("episode")) in affected_nums)
            ]
            for offset_idx, ep in enumerate(planned):
                num = from_episode + offset_idx
                status = "stale" if old_status.get(num) in ("consumed", "stale") else "planned"
                script_file = old_script_file.get(num)
                episodes_list.append(
                    _ledger_entry_from_draft(
                        ep.draft,
                        num=num,
                        source_rel=ep.source_rel,
                        start=ep.start,
                        end=ep.end,
                        status=status,
                        script_file=script_file if isinstance(script_file, str) else None,
                    )
                )
                if status == "stale":
                    committed["stale"].append(num)
                summaries.append(
                    EpisodePlanSummary(
                        episode=num,
                        title=ep.draft.title,
                        hook=ep.draft.hook,
                        reading_units=count_reading_units(texts[ep.source_rel][ep.start : ep.end], language),
                        ledger_status=status,
                    )
                )
            _sort_episodes_if_possible(episodes_list)
            p["episodes"] = episodes_list
            if target_units is not None:
                p["episode_target_units"] = target_units
                committed["settings"] = {"episode_target_units": target_units}
            self._reconcile_derived_files(p, texts)
            committed["cursor"] = p.get("planning_cursor")

        self.pm.update_project(self.project_name, _commit)
        return PlanResult(
            episodes=summaries,
            cursor=committed["cursor"],
            stale_episodes=list(committed["stale"]),
            settings_updated=dict(committed["settings"]),
        )

    def _replan_scope(self, project: Mapping[str, Any], from_episode: int) -> _ReplanScope:
        """解析重排范围：from_episode 起的全部账本条目 + 按集号序逐源文件分段的闭合原文片段。

        范围跨多个源文件时按文件拆为多个片段（单集不跨文件，文件边界必然是集边界），
        每个片段后续独立重切。
        """
        affected: list[tuple[int, dict[str, Any]]] = []
        for entry in project.get("episodes") or []:
            if not isinstance(entry, dict):
                continue
            num = parse_episode_num(entry.get("episode"))
            if num is None or num < from_episode:
                continue
            affected.append((num, entry))
        affected.sort(key=lambda pair: pair[0])
        if not affected or affected[0][0] != from_episode:
            raise EpisodePlanningError(f"from_episode={from_episode} 不在账本中，无法重排")
        unanchored = [num for num, entry in affected if entry.get("ledger_status") == "unanchored"]
        if unanchored:
            raise EpisodePlanningError(
                f"重排范围波及失锚（unanchored）集 {unanchored}，这些集已锁定不参与重排；请调大 from_episode"
            )
        slices: list[_ReplanSlice] = []
        seen_rels: set[str] = set()
        for num, entry in affected:
            source_range = entry.get("source_range")
            if not isinstance(source_range, Mapping):
                raise EpisodePlanningError(f"第 {num} 集缺少原文范围记录，无法重排")
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
                raise EpisodePlanningError(f"第 {num} 集原文范围记录不完整，无法重排")
            # 零宽/反向范围是脏数据：反向条目若恰好与前一集首尾相接会把合并 slice 的 end 拉回，
            # 静默缩小重排覆盖范围并绕过 slice 级校验，必须在条目级拦截
            if seg_start >= seg_end:
                raise EpisodePlanningError(f"第 {num} 集原文范围无效（start={seg_start} >= end={seg_end}），无法重排")
            if slices and slices[-1].source_rel == rel:
                # 同文件相邻条目必须首尾相接：断档/重叠意味着账本与原文覆盖不一致，
                # 静默合并会把范围之外的原文一并重切，必须 fail-fast
                if seg_start != slices[-1].end:
                    raise EpisodePlanningError(
                        f"第 {num} 集与上一集在源文件 {rel} 中的范围不连续"
                        f"（上一集止于 {slices[-1].end}，本集起于 {seg_start}），账本数据异常，无法重排"
                    )
                slices[-1].end = seg_end
            else:
                # 同一源文件在范围内非连续出现说明集号与源文件顺序错乱：片段会重叠/穿插，必须 fail-fast
                if rel in seen_rels:
                    raise EpisodePlanningError(
                        f"第 {num} 集的源文件 {rel} 在重排范围内非连续出现，账本集号与源文件顺序不一致，"
                        "无法重排；请调大 from_episode 使范围避开顺序错乱的集"
                    )
                seen_rels.add(rel)
                slices.append(_ReplanSlice(source_rel=rel, start=seg_start, end=seg_end))
        return _ReplanScope(slices=slices, affected=affected)

    # ------------------------------------------------------------- helpers

    async def _request_validated_drafts(
        self,
        draft_model: type[BaseModel],
        prompt_builder: Callable[[list[str] | None], str],
        window: str,
        *,
        cover_to_end: bool,
        snap_whitespace_tail: bool = False,
        max_episodes: int | None,
    ) -> tuple[list[NarrationEpisodeDraft], list[int], BaseModel]:
        """LLM 调用 + schema/机械校验循环；重试 prompt 附上一轮失败原因。"""
        if self.generator is None:
            raise RuntimeError("TextGenerator 未初始化，请使用 EpisodePlanner.create() 工厂方法")
        failure: list[str] | None = None
        for attempt in range(1, self.max_attempts + 1):
            result = await self.generator.generate(
                TextGenerationRequest(
                    prompt=prompt_builder(failure),
                    response_schema=draft_model,
                    max_output_tokens=PLANNING_MAX_OUTPUT_TOKENS,
                ),
                project_name=self.project_name,
            )
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
                ends = _resolve_boundaries(
                    window, drafts, cover_to_end=cover_to_end, snap_whitespace_tail=snap_whitespace_tail
                )
                return drafts, ends, draft
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
            raise _DraftRejected([f"输出不是合法 JSON：{exc}"]) from exc
        try:
            return draft_model.model_validate(data)
        except ValidationError as exc:
            issues = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
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
            # 游标已合法推进到后一个文件（如升级回填的失锚项目）则取游标，避免重复规划该文件前缀
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

    def _next_source_rel(self, rel: str) -> str | None:
        """按文件名序返回 ``rel`` 之后的下一个候选源文件；``rel`` 不在候选或已是最后一个时返回 None。"""
        rels = [doc.rel_path for doc in discover_sources(self.project_path)]
        try:
            idx = rels.index(rel)
        except ValueError:
            return None
        return rels[idx + 1] if idx + 1 < len(rels) else None

    def _load_normalized_source(self, rel: str) -> str:
        path = self.project_path / rel
        base = self.project_path.resolve()
        candidate = path.resolve()
        if candidate != base and not candidate.is_relative_to(base):
            raise EpisodePlanningError(f"源文件路径越出项目目录：{rel}")
        if not path.is_file():
            raise EpisodePlanningError(f"源文件不存在：{rel}")
        try:
            return normalize_source_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise EpisodePlanningError(f"源文件读取失败：{rel}: {exc}") from exc

    @staticmethod
    def _setting_int(project: Mapping[str, Any], key: str, default: int) -> int:
        value = project.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value
        if value is not None:
            logger.warning("项目设置 %s=%r 非法，回退内部默认 %d", key, value, default)
        return default

    def _reconcile_derived_files(self, project: Mapping[str, Any], text_cache: dict[str, str]) -> None:
        """按账本全量对账派生集文件：重写有 source_range 的集、删除账本之外的残留。

        unanchored 集的物理文件即其最终记录，既不重写也不删除。余文文件
        ``_remaining.txt`` 已由账本游标取代，一并清理。每次提交全量对账使
        中途崩溃后重跑即可自愈。

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
            if entry.get("ledger_status") == "unanchored":
                continue
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


def _sort_episodes_if_possible(episodes: list[Any]) -> None:
    """全部集号可解析时按集号排序（与回填同口径），否则保持原序。"""
    if all(isinstance(e, dict) and parse_episode_num(e.get("episode")) is not None for e in episodes):
        episodes.sort(key=lambda e: parse_episode_num(e["episode"]) or 0)


def _context_entries(project: Mapping[str, Any], *, before_episode: int | None = None) -> list[dict[str, Any]]:
    """已规划末尾若干集的 标题+钩子，作为续写连贯性上下文。

    ``before_episode`` 限定只取该集号之前的集（replan 的已定上下文）。
    """
    anchored: list[tuple[int, dict[str, Any]]] = []
    for entry in project.get("episodes") or []:
        if not isinstance(entry, dict):
            continue
        num = parse_episode_num(entry.get("episode"))
        if num is None or entry.get("ledger_status") in (None, "unanchored"):
            continue
        if before_episode is not None and num >= before_episode:
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
    fixed_boundary: bool,
    failure: list[str] | None,
    slice_position: tuple[int, int] | None = None,
) -> str:
    """plan / replan 共用的规划 prompt。仅面向文本模型，不做 i18n。

    ``slice_position=(第几段, 总段数)`` 标记当前 prompt 在重排范围中的位置；
    总段数大于 1（范围跨多个源文件）时注入跨文件说明，提示模型用户意见中与
    本段无关的部分由其他段落实。
    """
    overview = project.get("overview") or {}
    language = str(project.get("source_language") or "zh")
    unit_name = "词" if language in ("en", "vi") else "字"
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
        lines += ["", "# 用户重排意见（必须全部落实）", instructions]
    if slice_position is not None and slice_position[1] > 1:
        current, total = slice_position
        lines += [
            "",
            "# 跨源文件重排说明",
            f"- 本次重排范围跨 {total} 个源文件，已按文件拆成 {total} 段分别重切（文件边界必然是集边界），"
            f"当前是第 {current} 段。",
            "- 用户意见中与本段原文无关的部分由其他段落实，本段不要硬凑。",
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
    if fixed_boundary:
        lines.append("- 这段原文范围是闭合的：最后一集的 end_anchor 必须取这段原文的结尾片段，每个字都要归属某一集。")
    elif window_is_final:
        lines.append("- 这段原文已包含全文结尾：请规划到结尾，最后一集的 end_anchor 取全文结尾处的片段，不要留尾巴。")
    else:
        lines.append("- 这段原文只是全文的一个窗口：窗口尾部剧情弧不完整的内容不要硬凑成集，留给下一批规划即可。")
    lines.append("- 只输出符合 schema 的 JSON，不要输出其他内容。")

    if failure:
        lines += ["", "# 上一轮输出未通过校验，请针对性修正后重新输出"]
        lines += [f"- {reason}" for reason in failure]

    lines += ["", "# 剧本原文片段" if is_screenplay else "# 小说原文片段", "---", window, "---"]
    return "\n".join(lines)
