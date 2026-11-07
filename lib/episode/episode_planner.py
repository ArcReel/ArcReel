"""Episode-planning service: read source windows → call project-configured text models → write ledgers and derive episode files.

plan() reads windows from ledger-derived planning starts (last cut episode's end by source position; see
:func:`lib.episode.episode_sources.planning_start`). Text models plan all complete-story-arc episodes within each window
(titles/hooks/split anchors; drama also includes outlines), enforced by schemas and mechanical anchor existence/uniqueness/continuity
checks. Failures retry automatically with prior rejection reasons. Whole-source files follow project manifest order. Standalone-source and sourceless
episodes neither occupy whole sources nor block planning; newly cut episodes follow the last cut episode, or append if no cut episodes exist.
Without complete-story-arc split points in windows, models return empty lists and batches raise :class:`NoCutPointError`.

Writes share one project lock: ledgers + new batch episode files (other episode files unchanged) + remainder cleanup + source
snapshot synchronization. Replanning candidates generate window by window through :meth:`EpisodePlanner.plan_candidate`, writing candidates instead of ledgers (see
:mod:`lib.episode.episode_replan`). Windows use fixed
:data:`PLANNING_WINDOW_CHARS`; batch episode counts derive from effective text-model output limits (see :func:`episodes_per_batch`).
Neither is a creator parameter or overridable project setting (see docs/adr/0032, 0044). New IDs with existing downstream disk artifacts
(historical remnants) become stale instead of directly overwriting statuses; artifacts remain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    StructuredOutputExhaustedError,
    TextGenerationRequest,
    TextOutputTruncatedError,
    TextTaskType,
    truncate_for_log,
)
from lib.backends.text_generator import TextGenerator
from lib.episode.episode_excerpts import edge_sentences
from lib.episode.episode_ids import allocate_episode_ids, episode_id_high_water
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    SourceSpan,
    compute_source_fingerprints,
    discover_episode_file_aliases,
    has_downstream_products,
    mismatched_source_fingerprints,
    normalize_source_text,
    parse_episode_num,
    parse_source_range,
)
from lib.episode.episode_paths import episode_script_relpath, episode_source_path
from lib.episode.episode_replan import (
    ReplanError,
    candidate_cursor,
    candidate_episodes,
    candidate_start,
    replan_candidate,
)
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    SourceOrigin,
    archive_episode_file_path,
    cut_episode_placements,
    cut_insert_index,
    discover_sources,
    is_cut_episode,
    legacy_cut_episode_ids,
    planning_start,
    source_snapshot_path,
    span_text,
    sync_source_snapshots,
    unsplit_range_ending_at,
    whole_source_files,
)
from lib.episode.episode_target_volume import EpisodeTargetVolume, resolve_episode_target_volume
from lib.episode.source_kinds import DEFAULT_SOURCE_KIND, whole_source_file_kind
from lib.infra.async_thread import run_sync_transaction
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.text_metrics import count_reading_units, reading_unit_noun
from lib.infra.text_utils import strip_json_code_fences
from lib.project.project_manager import ProjectManager
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.script import script_review

logger = logging.getLogger(__name__)

# Characters per source window, never creator parameters; Web, Agent and project settings cannot override.
PLANNING_WINDOW_CHARS = 50000

# Estimated structured-output tokens per episode (title, hook, anchors; drama also story beats and next-episode teasers),
# conservatively high; actual drama output approximately 250–450 tokens.
_EPISODE_OUTPUT_TOKENS = {"drama": 500, "narration": 200}

# Output-limit proportion reserved for episode entries: reasoning-model thinking tokens and JSON wrappers share the same limit.
_OUTPUT_SAFETY_FACTOR = 0.5

# Total attempts, including initial, when LLM outputs fail schema/mechanical checks.
_MAX_PLAN_ATTEMPTS = 3

# Maximum planned-context entries injected into prompts (coherent continuation without prompt inflation).
_CONTEXT_EPISODES_LIMIT = 5

# Maximum individually listed episode numbers missing positional records in refusals (every episode in old whole-source projects may lack them;
# listing all would inflate errors to hundreds of episodes).
_MISSING_RANGE_LISTED_LIMIT = 10


class EpisodePlanningError(RuntimeError):
    """Episode planning failed (missing sources, exhausted validation retries, etc.)."""


class PlanningConflictError(EpisodePlanningError):
    """Ledgers changed concurrently during planning; commit refused. Calling again plans against new state."""


class NoCutPointError(EpisodePlanningError):
    """No complete-story-arc split point within windows. ``source_file`` / ``offset`` locate this batch's unsplit source beginning."""

    def __init__(self, *, source_file: str, offset: int):
        self.source_file = source_file
        self.offset = offset
        super().__init__(
            f"{source_file} 从偏移 {offset} 起的这一段原文里找不到剧情弧完整的切分点；"
            "可以先手工切出这一段，再从切分处继续规划。"
        )


class PlanningOutputTruncatedError(EpisodePlanningError, TextOutputTruncatedError):
    """Text-model output truncated during episode planning: both planning failure and :class:`TextOutputTruncatedError`, retaining the latter's fields."""

    def __init__(self, cause: TextOutputTruncatedError):
        TextOutputTruncatedError.__init__(
            self,
            provider=cause.provider,
            model=cause.model,
            output_tokens=cause.output_tokens,
            provider_id=cause.provider_id,
            custom_model=cause.custom_model,
        )


def episodes_per_batch(max_output_tokens: int, content_mode: str) -> int:
    """Maximum episodes per batch = effective output limit × safety factor ÷ estimated output per episode, at least 1."""
    estimate = _EPISODE_OUTPUT_TOKENS["drama" if content_mode == "drama" else "narration"]
    return max(1, int(max_output_tokens * _OUTPUT_SAFETY_FACTOR) // estimate)


@dataclass
class EpisodePlanSummary:
    """Episode summary: title + hook + volume (reading units by source_language) + first/last source sentences."""

    episode: int
    title: str
    hook: str
    reading_units: int
    ledger_status: str
    first_sentence: str
    last_sentence: str


@dataclass
class LedgerStats:
    """Whole-ledger volume-distribution snapshot (mechanically calculated, without thresholds such as what counts as unusually small).

    Main Agents handle semantic judgments (deviation from structural user preferences such as one chapter per episode or 32 episodes total);
    report distribution facts only here.
    """

    total_episodes: int
    smallest: list[tuple[int, int]]  # (episode number, volume) Up to five smallest episodes, ascending by volume.
    median_units: int | None
    target_volume: EpisodeTargetVolume | None


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
    """Global batch-planning progress snapshot, injected into prompts only for nonempty instructions (model splitting-pace calculations)."""

    planned_count: int
    remaining_units: int
    window_units: int


@dataclass(frozen=True)
class CandidateEpisodeSummary:
    """Candidate episode summary, without allocated episode IDs."""

    title: str
    hook: str
    reading_units: int
    first_sentence: str
    last_sentence: str


@dataclass
class CandidatePlanResult:
    """One candidate-generation batch result: appended episodes, whether candidates cover whole-source ends and candidate episode counts."""

    episodes: list[CandidateEpisodeSummary]
    source_exhausted: bool
    total: int


@dataclass(frozen=True)
class _Window:
    """Sources read for this batch: one file's text from ``start`` to ``limit``, window beginning at ``start``."""

    source_rel: str
    text: str
    start: int
    limit: int
    #: No unplanned sources after ``limit``: last whole-source file, or gap endpoint when planning gaps.
    reaches_end: bool
    #: Existing episodes immediately after ``limit`` (gap planning): final windows must reach ``limit`` without leftover tails.
    followed_by_episode: bool = False

    def end(self, window_chars: int) -> int:
        # Flexible windows: consume to the end when full remaining text is below 1.2 times window size, preventing orphan leftovers
        # from becoming tiny separate episodes in the next batch (a mechanical cause of unusually small episodes). Factor 1.2 allows small enough variation
        # that ordinary batches do not substantially exceed expected window volume.
        return self.limit if self.limit - self.start <= window_chars * 1.2 else self.start + window_chars

    def is_final(self, window_chars: int) -> bool:
        return self.end(window_chars) >= self.limit


_DRAFT_CONFIG = ConfigDict(extra="forbid")


class NarrationEpisodeDraft(BaseModel):
    """Narration entries: exact split anchors + hooks."""

    model_config = _DRAFT_CONFIG

    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    end_anchor: str = Field(min_length=2)


class DramaEpisodeDraft(NarrationEpisodeDraft):
    """Drama entries expand into episode outlines: story beats + next-episode teasers."""

    story_beats: list[str] = Field(min_length=1)
    next_episode_teaser: str | None = None


class NarrationPlanDraft(BaseModel):
    """Empty lists mean no complete-story-arc split points found within windows."""

    model_config = _DRAFT_CONFIG

    episodes: list[NarrationEpisodeDraft]


class DramaPlanDraft(BaseModel):
    """Empty lists mean no complete-story-arc split points found within windows."""

    model_config = _DRAFT_CONFIG

    episodes: list[DramaEpisodeDraft]


class _DraftRejected(Exception):
    """One LLM output rejected by schema/mechanical validation; reasons feed the next retry prompt."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# Full-/halfwidth punctuation folding: map fullwidth/ideographic punctuation to canonical halfwidth forms, solely to build matching
# copies, never persist. Characterwise equal-length 1:1 mapping makes folded coordinates exact NFC coordinates.
_PUNCT_FOLD: dict[str, str] = {
    "。": ".",  # U+3002 IDEOGRAPHIC FULL STOP
    "．": ".",  # U+FF0E FULLWIDTH FULL STOP
    "，": ",",  # U+FF0C FULLWIDTH COMMA
    "！": "!",  # U+FF01 FULLWIDTH EXCLAMATION MARK
    "？": "?",  # U+FF1F FULLWIDTH QUESTION MARK
    "：": ":",  # U+FF1A FULLWIDTH COLON
    "；": ";",  # U+FF1B FULLWIDTH SEMICOLON
    "（": "(",  # U+FF08 FULLWIDTH LEFT PARENTHESIS
    "）": ")",  # U+FF09 FULLWIDTH RIGHT PARENTHESIS
    "～": "~",  # U+FF5E FULLWIDTH TILDE
    "〜": "~",  # U+301C CJK wave dash (same family as U+FF5E, both fold to halfwidth ~)
    "“": '"',  # U+201C LEFT DOUBLE QUOTATION MARK
    "”": '"',  # U+201D RIGHT DOUBLE QUOTATION MARK
    "‘": "'",  # U+2018 LEFT SINGLE QUOTATION MARK
    "’": "'",  # U+2019 RIGHT SINGLE QUOTATION MARK
    "＂": '"',  # U+FF02 FULLWIDTH QUOTATION MARK
    "＇": "'",  # U+FF07 FULLWIDTH APOSTROPHE
}
# Folded values must be single characters: _fold_for_match's equal-length 1:1 mapping depends on this. Multicharacter mappings shift
# folded offsets relative to original NFC text, making end = start + len(anchor) wrong. Reject violations at import time.
assert all(len(dst) == 1 for dst in _PUNCT_FOLD.values()), "折叠表的值必须是单字符以保证等长映射"


def _fold_for_match(text: str) -> str:
    """Build folded matching copies: fold full-/halfwidth punctuation and each whitespace character into one ASCII space (characterwise equal length, never merge consecutive whitespace).

    Strict 1:1 character mapping (exactly one output character per input; retain consecutive whitespace counts without compression)
    preserves source length, so folded-string offsets exactly match original NFC coordinates. Tolerance covers only full-/halfwidth
    punctuation and individual whitespace widths, never edit-distance / semantic fuzzy matching.
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
    """Collect every needle start in haystack, including overlaps.

    str.count counts nonoverlapping matches only, misclassifying overlapping occurrences ("aaa" in "aaaa") as unique.
    Slide by 1, classifying 0/1/multiple matches with overlaps. Empty needles return empty lists directly
    (caller anchors have min_length=2 and cannot be empty; defensive helper boundary only).
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
    """Resolve episode end_anchor values to window-relative end offsets, checking existence/uniqueness/continuity.

    Anchors construct contiguous nonoverlapping ranges: episode i = [episode i-1 end, episode i anchor end).
    If only whitespace remains (``snap_whitespace_tail=True``, plan's final full-text window), align
    the final episode to window end, assigning every character to an episode.

    Tiered anchor lookup: exact ``find`` first (hits identical to bytewise matching; Tier1 performs no normalization).
    On exact misses, tolerate differences: apply window-equivalent ``normalize_source_text`` to anchor copies
    (NFC + unified newlines), closing all normalized-window/unnormalized-anchor mismatches, then scan folded copies
    with equivalent full-/halfwidth punctuation and whitespace-width folding. Window folding is equal-length 1:1,
    so hit starts are exact normalized-coordinate starts. Use normalized anchor lengths for spans (``normalize_source_text`` is not
    length-preserving; ends must use normalized lengths, never original anchor lengths, or \\r/combining-character expansion shifts offsets).
    Slice normalized original text without rewriting source punctuation. Multiple folded hits still reject as nonunique.
    """
    reasons: list[str] = []
    ends: list[int] = []
    pos = 0
    ordering_valid = True
    folded_window: str | None = None  # Lazy construction: fold once only after exact matching misses.
    for idx, ep in enumerate(drafts, start=1):
        anchor = ep.end_anchor
        match_len = len(anchor)  # Exact hits: spans equal original anchor lengths (byte-identical to window).
        starts = _find_all_overlapping(window, anchor)
        matched_by_fold = False
        if not starts:
            # Exact misses fall back to tolerant matching. Normalize matching anchor copies exactly as windows
            # (normalize_source_text: NFC + unified newlines; windows already use it), then fold full-/halfwidth punctuation
            # and whitespace width to locate exact offsets on equal-length folded window copies. Tier1 byte behavior/source coordinates
            # remain unchanged. normalize_source_text is not length-preserving, so end spans use normalized anchor match_len (never original
            # anchor length), aligned with normalized coordinates, avoiding \r / combining-character expansion shifting/damaging slice coordinates.
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
                # Folded-path occurrence counts follow punctuation/whitespace alignment. Literal model anchor counts
                # are often 0; state counting rules explicitly or models may resubmit identical anchors and exhaust retries.
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
    """Materialize episode drafts as ledger entries."""
    entry: dict[str, Any] = {
        "episode": num,
        "title": draft_ep.title,
        "script_file": episode_script_relpath(num),
        SOURCE_ORIGIN_FIELD: SourceOrigin.WHOLE_SOURCE.value,
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


def _candidate_entry_from_draft(
    draft_ep: NarrationEpisodeDraft, *, source_rel: str, start: int, end: int
) -> dict[str, Any]:
    """Materialize episode drafts as candidates: same titles, hooks, source ranges and outlines as ledger entries; allocate IDs on adoption."""
    entry: dict[str, Any] = {
        "title": draft_ep.title,
        "hook": draft_ep.hook,
        "source_range": {"source_file": source_rel, "start": start, "end": end},
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


#: Two replanning entries from the first cut episode: both Web and Agent see planning failure reasons.
_REPLAN_FROM_FIRST = "在「分集」视图选中第一个切出集，选「从这一集开始重新规划」；或由 Agent 调用 reset_episode_planning（不带 episode_id）"


def _source_changed_error(paths: list[str]) -> EpisodePlanningError:
    """Build source-change refusals naming changed files and directing to full reset."""
    return EpisodePlanningError(
        f"源文件已被修改或移除：{'、'.join(paths)}。账本坐标绑定的是修改前的原文内容，继续规划会静默切出"
        f"错误内容；需要从第一个切出集起重新规划：{_REPLAN_FROM_FIRST}。"
    )


def _missing_source_range_error(nums: list[int]) -> EpisodePlanningError:
    """Build refusals for legacy split-flow ledger episodes, naming IDs and directing to full reset."""
    listed = "、".join(str(num) for num in nums[:_MISSING_RANGE_LISTED_LIMIT])
    if len(nums) > _MISSING_RANGE_LISTED_LIMIT:
        listed += f" 等 {len(nums)} 集"
    return EpisodePlanningError(
        f"账本中集 ID 为 {listed} 的切出集没有原文范围记录（source_range），无法据此续接规划——它们由旧拆分"
        "流程切出，物理集文件就是它们的最终记录，既无法重造也无法确定下一批的起点。这些集照常可以做脚本"
        f"规划；确需重新切分时才从第一个切出集起重新规划：{_REPLAN_FROM_FIRST}（这些集的集文件会改名留底、"
        "下游产物不删）。"
    )


class EpisodePlanner:
    """Episode planner. None ``generator`` permits construction only; plan() raises.

    ``window_chars`` only shrinks windows for tests; production always uses :data:`PLANNING_WINDOW_CHARS`.
    """

    def __init__(
        self,
        project_path: str | Path,
        generator: TextGenerator | None = None,
        *,
        max_attempts: int = _MAX_PLAN_ATTEMPTS,
        window_chars: int = PLANNING_WINDOW_CHARS,
    ):
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self.generator = generator
        self.max_attempts = max_attempts
        self.window_chars = window_chars
        self.pm = ProjectManager.for_project_dir(self.project_path)

    @classmethod
    async def create(cls, project_path: str | Path) -> EpisodePlanner:
        """Async factory: create text backends from project configuration, using the same SCRIPT-task chain as script generation."""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name, purpose=CallPurpose.EPISODE_PLANNING)
        return await asyncio.to_thread(cls, project_path, generator)

    # ---------------------------------------------------------------- plan

    async def plan(
        self,
        instructions: str | None = None,
        *,
        on_more_to_plan: Callable[[], Awaitable[None]] | None = None,
        gap: tuple[str, int] | None = None,
    ) -> PlanResult:
        """Plan the next episode batch: read windows at ledger-derived starts, produce complete-story-arc episodes and commit ledgers.

        Advance through whole-source manifest order when current files have no remaining effective content;
        ``source_exhausted=True`` means all source files are fully planned.

        Call ``on_more_to_plan`` once when whole sources retain unplanned text after this batch: before model requests if windows
        exclude whole-source ends; otherwise when model batches do not reach the end (for example batch episode limits truncate them).
        Never call when sources are fully planned or batches fail.

        ``gap`` is an unsplit segment end ``(源文件, 偏移)``: plan only the segment ending there (gaps from deleted cut episodes, etc.),
        starting at the nearest preceding cut episode's end in the same file. Never plan beyond this endpoint; insert new episodes by source position without replacing any.
        Here ``source_exhausted=True`` means this segment is fully planned.

        ``instructions`` are optional additional user planning instructions (for example chapter-aligned cuts), absent when empty after strip;
        inject nonempty values unchanged into neutral additional-instruction prompt sections, with adherence strength expressed by their own text. Planning uses window
        batches without persisting additional instructions; callers must repeat them with every plan call.

        New episode numbers with existing downstream disk artifacts (script/script_plan/media, see
        :func:`lib.episode.episode_ledger.has_downstream_products`) were actually consumed already
        (typically ``reset_episode_planning`` partially reset to earlier numbers, then new ``instructions``
        replan layouts overlapping prior consumed ranges). Commit them directly as ``stale`` without deleting artifacts,
        returning them in ``PlanResult.stale_episodes`` without further confirmation: reset already completed consumed-episode
        confirmation.
        """
        planning_instructions = (instructions or "").strip() or None

        def snapshot() -> tuple[dict, list[SourceDoc], dict[str, str]]:
            project = self.pm.load_project(self.project_name)
            self._check_source_ranges(project)
            sources = discover_sources(self.project_path, project)
            self._check_source_fingerprints(project, sources=sources)
            return project, sources, compute_source_fingerprints(sources)

        # Retain fingerprint digests only for commit rechecks, not full source texts unused by this batch:
        # discover_sources already read every whole-source file to calculate these fingerprints. Explicitly release
        # that list below, avoiding full-source retention throughout model-call waits in large multisource projects.
        project, pre_call_sources, used_fingerprints = await asyncio.to_thread(snapshot)
        source_order = [doc.rel_path for doc in pre_call_sources]

        start_ref = (
            self._effective_start(project, pre_call_sources)
            if gap is None
            else _gap_start(project, pre_call_sources, gap)
        )
        source_rel, start = start_ref
        text = next(doc.text for doc in pre_call_sources if doc.rel_path == source_rel)
        if start > len(text):
            raise EpisodePlanningError(f"规划起点越界：{source_rel} 长度 {len(text)}，起点 {start}；请检查账本")
        # Current-file planning end: gap end when planning gaps, otherwise file end.
        limit = len(text) if gap is None else gap[1]
        while not text[start:limit].strip():
            next_rel = None if gap is not None else _next_source_rel(source_order, source_rel)
            if next_rel is None:
                project = await self._backfill_source_fingerprints_if_missing(
                    project,
                    used_fingerprints=used_fingerprints,
                )
                return PlanResult(
                    episodes=[],
                    cursor={"source_file": source_rel, "offset": start},
                    source_exhausted=True,
                    total_planned=_count_planned_episodes(project),
                    ledger_stats=await asyncio.to_thread(self._compute_ledger_stats, project),
                )
            source_rel, start = next_rel, 0
            text = next(doc.text for doc in pre_call_sources if doc.rel_path == source_rel)
            limit = len(text)
        # Inject global progress only with additional instructions; compute later-file volumes before releasing sources.
        later_units = (
            sum(
                count_reading_units(doc.text, _language_of(project))
                for doc in pre_call_sources[source_order.index(source_rel) + 1 :]
            )
            if planning_instructions and gap is None
            else 0
        )
        context_entries = (
            _context_entries(project) if gap is None else _gap_context_entries(project, pre_call_sources, start_ref)
        )
        pre_call_sources = []  # Only used_fingerprints (digests) and this batch's actual text remain necessary; explicitly release source references.

        window = _Window(
            source_rel=source_rel,
            text=text,
            start=start,
            limit=limit,
            reaches_end=gap is not None or _next_source_rel(source_order, source_rel) is None,
            followed_by_episode=gap is not None,
        )
        drafts, ends = await self._draft_window(
            project,
            window,
            context_entries=context_entries,
            instructions=planning_instructions,
            planned_count=_count_planned_episodes(project),
            later_units=later_units,
            on_more_to_plan=on_more_to_plan,
        )
        language = _language_of(project)
        window_is_final = window.is_final(self.window_chars)

        summaries: list[EpisodePlanSummary] = []
        committed: dict[str, Any] = {"stale": []}
        # Budget transaction-protected derived paths from unlocked snapshots; IDs allocated under locks must match.
        next_num = episode_id_high_water(project) + 1
        protected_ids = list(range(next_num, next_num + len(drafts)))

        def _commit(p: dict) -> None:
            # Locked recheck: ledgers/sources may change concurrently during model calls (newly registered legacy split episodes, externally changed
            # sources). Share unlocked prechecks' escape hatches and refusal messages; retries repeat identical comparisons, requiring reset first.
            self._check_source_ranges(p)
            current_sources = discover_sources(self.project_path, p)
            self._check_source_fingerprints(p, sources=current_sources)
            locked_start = (
                self._effective_start(p, current_sources) if gap is None else _gap_start(p, current_sources, gap)
            )
            if locked_start != start_ref:
                raise PlanningConflictError("规划期间账本进度被并发修改，本次结果作废；请重新调用规划")
            # Fingerprint comparisons cover recorded files only, always empty for legacy backfill paths. Split coordinates and derived
            # files use this call's used_fingerprints, so compare them directly to close the exposed backfill window:
            # any source read by this call that changes during model calls is also blocked here.
            current_fingerprints = compute_source_fingerprints(current_sources)
            changed = sorted(rel for rel, fp in used_fingerprints.items() if current_fingerprints.get(rel) != fp)
            if changed:
                raise _source_changed_error(changed)
            episodes_list = [e for e in (p.get("episodes") or []) if e is not None]
            # New episodes always receive IDs beyond historical maxima, following the last cut episode (append if none);
            # gap planning inserts by source position between cut episodes flanking gaps.
            if gap is None:
                insert_at = next(
                    (
                        index + 1
                        for index in range(len(episodes_list) - 1, -1, -1)
                        if isinstance(episodes_list[index], Mapping) and is_cut_episode(episodes_list[index])
                    ),
                    len(episodes_list),
                )
            else:
                file_index = next(i for i, doc in enumerate(current_sources) if doc.rel_path == source_rel)
                insert_at = cut_insert_index(
                    episodes_list, cut_episode_placements(p, current_sources), (file_index, start)
                )
            new_entries: list[dict[str, Any]] = []
            new_ids = allocate_episode_ids(p, len(drafts))
            if new_ids != protected_ids:
                raise PlanningConflictError("规划期间有并发写入分配了新的集 ID，本次结果作废；请重新调用规划")
            prev = start
            for num, draft_ep, rel_end in zip(new_ids, drafts, ends, strict=True):
                abs_end = start + rel_end
                entry = _ledger_entry_from_draft(
                    draft_ep, num=num, source_rel=source_rel, start=prev, end=abs_end, status="planned"
                )
                # New IDs with existing disk script/script_plan artifacts (manual remnants beyond historical maxima) were actually
                # consumed; mark stale to tell main Agents to redo downstream artifacts, never delete artifacts themselves.
                if has_downstream_products(self.project_path, num, entry):
                    script_review.mark_ledger_stale(self.project_path, p, entry, num)
                    committed["stale"].append(num)
                new_entries.append(entry)
                segment = text[prev:abs_end]
                first_sentence, last_sentence = edge_sentences(segment)
                summaries.append(
                    EpisodePlanSummary(
                        episode=num,
                        title=draft_ep.title,
                        hook=draft_ep.hook,
                        reading_units=count_reading_units(segment, language),
                        ledger_status=entry["ledger_status"],
                        first_sentence=first_sentence,
                        last_sentence=last_sentence,
                    )
                )
                prev = abs_end
            p["episodes"] = [*episodes_list[:insert_at], *new_entries, *episodes_list[insert_at:]]
            p[SOURCE_FINGERPRINTS_KEY] = current_fingerprints
            text_cache = {source_rel: text}
            self._write_new_episode_files(p, text_cache, new_ids=frozenset(new_ids))
            sync_source_snapshots(self.project_path, p, text_cache)
            committed["cursor"] = {"source_file": source_rel, "offset": start + ends[-1]}
            committed["exhausted"] = (
                window_is_final
                and not text[start + ends[-1] : limit].strip()
                and (gap is not None or _next_source_rel(source_order, source_rel) is None)
            )

        def protected_paths() -> tuple[Path, ...]:
            aliases = discover_episode_file_aliases(self.project_path)
            formal_paths = {episode_source_path(self.project_path, num) for num in protected_ids}
            formal_paths.update(path for num in protected_ids for path in aliases.get(num, []))
            formal_paths.add(self.project_path / "source" / "_remaining.txt")
            formal_paths.update(source_snapshot_path(self.project_path, rel) for rel in whole_source_files(project))
            return tuple(sorted(formal_paths))

        formal_paths = await asyncio.to_thread(protected_paths)
        final_project = await self._update_project(_commit, formal_paths=formal_paths)
        exhausted = bool(committed["exhausted"])
        return PlanResult(
            episodes=summaries,
            cursor=committed["cursor"],
            source_exhausted=exhausted,
            stale_episodes=list(committed["stale"]),
            total_planned=_count_planned_episodes(final_project),
            # Attach global checking material only on final exhausted batches; ordinary batches report total planned N only,
            # avoiding per-batch main-Agent context inflation (tool-layer total_planned rendering).
            ledger_stats=await asyncio.to_thread(self._compute_ledger_stats, final_project) if exhausted else None,
        )

    # ------------------------------------------------------------ candidate

    async def plan_candidate(
        self,
        candidate_id: str,
        instructions: str | None = None,
        *,
        on_more_to_plan: Callable[[], Awaitable[None]] | None = None,
    ) -> CandidatePlanResult:
        """Generate the next replanning-candidate batch: read windows from candidate ends, append generated episodes to candidates without ledger changes.

        See :mod:`lib.episode.episode_replan`. Refuse absent candidates (discarded/adopted), concurrent appends, or whole-source changes
        since candidate generation. Legacy split-flow ledger episodes and recorded source fingerprints do not block candidate generation: both situations start candidates
        at whole-source beginning. ``on_more_to_plan`` matches :meth:`plan`; mark candidates complete when covering whole-source ends.
        """
        planning_instructions = (instructions or "").strip() or None

        def snapshot() -> tuple[dict, dict[str, Any], list[SourceDoc], dict[str, str]]:
            project = self.pm.load_project(self.project_name)
            candidate = _require_candidate(project, candidate_id)
            sources = discover_sources(self.project_path, project)
            _check_candidate_sources(candidate, sources)
            return project, candidate, sources, compute_source_fingerprints(sources)

        project, candidate, sources, fingerprints = await asyncio.to_thread(snapshot)
        cursor = _candidate_cursor(candidate)
        source_order = [doc.rel_path for doc in sources]
        source_rel, start = cursor
        if source_rel not in source_order:
            raise EpisodePlanningError(f"整本源文里没有这个可读的文件：{source_rel}")
        text = sources[source_order.index(source_rel)].text
        while not text[start:].strip():
            next_rel = _next_source_rel(source_order, source_rel)
            if next_rel is None:
                total = await self._commit_candidate(
                    candidate_id, cursor, entries=[], complete=True, fingerprints=fingerprints
                )
                return CandidatePlanResult(episodes=[], source_exhausted=True, total=total)
            source_rel, start = next_rel, 0
            text = sources[source_order.index(source_rel)].text
        language = _language_of(project)
        later_units = (
            sum(count_reading_units(doc.text, language) for doc in sources[source_order.index(source_rel) + 1 :])
            if planning_instructions
            else 0
        )
        before = _candidate_context_before(project, sources, candidate)
        drafted = candidate_episodes(candidate)
        numbered = [
            {"episode": index, "title": entry.get("title"), "hook": entry.get("hook")}
            for index, entry in enumerate([*before, *drafted], start=1)
        ]
        sources = []  # Only this batch's actual text remains necessary; explicitly release source references.

        window = _Window(
            source_rel=source_rel,
            text=text,
            start=start,
            limit=len(text),
            reaches_end=_next_source_rel(source_order, source_rel) is None,
        )
        drafts, ends = await self._draft_window(
            project,
            window,
            context_entries=numbered[-_CONTEXT_EPISODES_LIMIT:],
            instructions=planning_instructions,
            planned_count=len(numbered),
            later_units=later_units,
            on_more_to_plan=on_more_to_plan,
        )
        entries: list[dict[str, Any]] = []
        summaries: list[CandidateEpisodeSummary] = []
        prev = start
        for draft_ep, rel_end in zip(drafts, ends, strict=True):
            abs_end = start + rel_end
            entries.append(_candidate_entry_from_draft(draft_ep, source_rel=source_rel, start=prev, end=abs_end))
            segment = text[prev:abs_end]
            first_sentence, last_sentence = edge_sentences(segment)
            summaries.append(
                CandidateEpisodeSummary(
                    title=draft_ep.title,
                    hook=draft_ep.hook,
                    reading_units=count_reading_units(segment, language),
                    first_sentence=first_sentence,
                    last_sentence=last_sentence,
                )
            )
            prev = abs_end
        exhausted = window.is_final(self.window_chars) and not text[prev:].strip() and window.reaches_end
        total = await self._commit_candidate(
            candidate_id, cursor, entries=entries, complete=exhausted, fingerprints=fingerprints
        )
        return CandidatePlanResult(episodes=summaries, source_exhausted=exhausted, total=total)

    async def _commit_candidate(
        self,
        candidate_id: str,
        cursor: tuple[str, int],
        *,
        entries: list[dict[str, Any]],
        complete: bool,
        fingerprints: Mapping[str, str],
    ) -> int:
        """Append this batch to candidates under project locks; return candidate episode counts."""
        committed: dict[str, int] = {}

        def _commit(p: dict) -> None:
            locked = _require_candidate(p, candidate_id)
            if _candidate_cursor(locked) != cursor:
                raise PlanningConflictError("新的分集方案在生成期间被并发追加，本批结果作废")
            current = compute_source_fingerprints(discover_sources(self.project_path, p))
            if locked.get("source_fingerprints") != current or current != dict(fingerprints):
                raise _candidate_source_changed_error()
            locked["episodes"] = [*candidate_episodes(locked), *entries]
            locked["complete"] = complete
            committed["total"] = len(locked["episodes"])

        await self._update_project(_commit)
        return committed["total"]

    async def _draft_window(
        self,
        project: Mapping[str, Any],
        window: _Window,
        *,
        context_entries: list[dict[str, Any]],
        instructions: str | None,
        planned_count: int,
        later_units: int,
        on_more_to_plan: Callable[[], Awaitable[None]] | None,
    ) -> tuple[list[NarrationEpisodeDraft], list[int]]:
        """Read windows, request models and validate; return batch episode drafts and their window-relative end offsets.

        No complete-story-arc split points in windows raise :class:`NoCutPointError`. See :meth:`plan` for ``on_more_to_plan`` timing.
        """
        if self.generator is None:
            raise RuntimeError("TextGenerator 未初始化，请使用 EpisodePlanner.create() 工厂方法")
        content_mode = "drama" if project.get("content_mode") == "drama" else "narration"
        max_episodes = episodes_per_batch(self.generator.max_output_tokens, content_mode)
        window_end = window.end(self.window_chars)
        window_text = window.text[window.start : window_end]
        window_is_final = window_end >= window.limit
        window_reaches_end = window_is_final and window.reaches_end
        if on_more_to_plan is not None and not window_reaches_end:
            await on_more_to_plan()
        draft_model: type[NarrationPlanDraft | DramaPlanDraft] = (
            DramaPlanDraft if content_mode == "drama" else NarrationPlanDraft
        )
        language = _language_of(project)
        # Compute/inject global progress only with instructions:
        # Without instructions, prompts must remain byte-identical: batch planning requires reproducible no-additional-instruction behavior within batches.
        # Global progress changes prompts, so calculate/inject it only with instructions.
        progress: _PlanningProgress | None = None
        if instructions:
            progress = _PlanningProgress(
                planned_count=planned_count,
                remaining_units=count_reading_units(window.text[window.start : window.limit], language) + later_units,
                window_units=count_reading_units(window_text, language),
            )

        # Windows read one file only, so each contains one source kind.
        source_kind = whole_source_file_kind(project, window.source_rel) or DEFAULT_SOURCE_KIND

        def _prompt(failure: list[str] | None) -> str:
            return _build_planning_prompt(
                project=project,
                source_kind=source_kind,
                window=window_text,
                window_is_final=window_is_final,
                followed_by_episode=window.followed_by_episode and window_is_final,
                max_episodes=max_episodes,
                content_mode=content_mode,
                context_entries=context_entries,
                instructions=instructions,
                progress=progress,
                failure=failure,
            )

        drafts, ends = await self._request_validated_drafts(
            draft_model,
            _prompt,
            window_text,
            snap_whitespace_tail=window_is_final,
            max_episodes=max_episodes,
        )
        if not drafts:
            raise NoCutPointError(source_file=window.source_rel, offset=window.start)
        if on_more_to_plan is not None and window_reaches_end and window_text[ends[-1] :].strip():
            await on_more_to_plan()
        return drafts, ends

    # ------------------------------------------------------------- helpers

    async def _request_validated_drafts(
        self,
        draft_model: type[NarrationPlanDraft | DramaPlanDraft],
        prompt_builder: Callable[[list[str] | None], str],
        window: str,
        *,
        snap_whitespace_tail: bool,
        max_episodes: int | None,
    ) -> tuple[list[NarrationEpisodeDraft], list[int]]:
        """LLM call + schema/mechanical validation loop; retry prompts include prior rejection reasons. Return empty model lists unchanged.

        Structured output truncated at output limits raises :class:`TextOutputTruncatedError`, immediately short-circuiting this loop:
        resending identical requests certain to truncate again is pointless. Convert to :class:`PlanningOutputTruncatedError` and propagate,
        including model information needed for remedies (see docs/adr/0044).

        Backend structured-output fallback exhaustion, :class:`StructuredOutputExhaustedError`, also short-circuits this loop;
        convert to :class:`EpisodePlanningError` so Agents receive readable insufficient-provider-structured-output-capability
        explanations instead of raw backend internal exceptions.
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
                raise PlanningOutputTruncatedError(exc) from exc
            except StructuredOutputExhaustedError as exc:
                # Backend fallback exhausted all tiers and their retries; retrying here only repeats the same certain-failure path.
                raise EpisodePlanningError(str(exc)) from exc
            try:
                draft = self._parse_draft(result.text, draft_model)
                drafts: list[NarrationEpisodeDraft] = list(draft.episodes)
                if not drafts:
                    return [], []
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
    def _parse_draft(
        response_text: str, draft_model: type[NarrationPlanDraft | DramaPlanDraft]
    ) -> NarrationPlanDraft | DramaPlanDraft:
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

    @staticmethod
    def _effective_start(project: Mapping[str, Any], sources: list[SourceDoc]) -> tuple[str, int]:
        """Next batch start derived from ledgers; see :func:`lib.episode.episode_sources.planning_start`."""
        start = planning_start(project, sources)
        if start is None:
            raise EpisodePlanningError("整本源文还没有文件，请先上传小说原文")
        return start

    @staticmethod
    def _span_files(order: list[str], span: SourceSpan) -> list[str]:
        """Files traversed by source ranges in whole-source order; if endpoint files are absent from manifests, return only those two."""
        if span.source_file in order and span.end_file in order:
            return order[order.index(span.source_file) : order.index(span.end_file) + 1]
        return list(dict.fromkeys((span.source_file, span.end_file)))

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
        """Compare ledger-recorded fingerprints with current sources, refusing planning on mismatches (including missing recorded files).

        Skip unrecorded legacy projects / new source files (initial plan backfills without errors). Callers supply ``sources``
        to reuse read snapshots; otherwise read once now.
        """
        docs = sources if sources is not None else discover_sources(self.project_path, project)
        mismatched = mismatched_source_fingerprints(project.get(SOURCE_FINGERPRINTS_KEY), docs)
        if mismatched:
            raise _source_changed_error(mismatched)

    @staticmethod
    def _check_source_ranges(project: Mapping[str, Any]) -> None:
        """Refuse planning if cut ledger episodes lack positional records (legacy split-flow remnants; direct to full reset).

        Cut episodes without ``source_range`` cannot reconstruct derived files or prove covered source ranges;
        continued planning would overlap or omit them. Consumption (scripts / media / statuses / export) remains unaffected;
        standalone-source and sourceless episodes do not occupy whole sources and are excluded.
        """
        missing = legacy_cut_episode_ids(project)
        if missing:
            raise _missing_source_range_error(missing)

    async def _backfill_source_fingerprints_if_missing(
        self,
        project: Mapping[str, Any],
        *,
        used_fingerprints: dict[str, str],
    ) -> dict:
        """Backfill legacy-project fingerprints on ``source_exhausted`` early return: this bypasses ``plan()``'s
        commit closure. Skipping would break initial-plan backfill for fully planned legacy sources; later same-length
        edits to old text would pass without comparison baselines. Projects with fingerprints return unchanged without recomputation.

        ``used_fingerprints`` is the source-digest baseline read outside locks in this plan() call (entry snapshots + newly discovered
        files midloop). Locked rechecks share regular commit-path escape hatches: if sources changed after plan() saved snapshots
        but before this closure reads, registering changed contents directly as baselines permanently loses comparison evidence for this edit;
        refuse first.
        """
        if project.get(SOURCE_FINGERPRINTS_KEY) is not None:
            return dict(project)

        def _commit(p: dict) -> None:
            current_sources = discover_sources(self.project_path, p)
            self._check_source_fingerprints(p, sources=current_sources)
            current_fingerprints = compute_source_fingerprints(current_sources)
            changed = sorted(rel for rel, fp in used_fingerprints.items() if current_fingerprints.get(rel) != fp)
            if changed:
                raise _source_changed_error(changed)
            p[SOURCE_FINGERPRINTS_KEY] = current_fingerprints

        return await self._update_project(_commit)

    async def _update_project(
        self,
        mutate: Callable[[dict], None],
        *,
        formal_paths: tuple[Path, ...] = (),
    ) -> dict:
        return await run_sync_transaction(
            self.pm.update_project,
            self.project_name,
            mutate,
            formal_paths=formal_paths,
        )

    def _write_new_episode_files(
        self, project: Mapping[str, Any], text_cache: dict[str, str], *, new_ids: frozenset[int]
    ) -> None:
        """Write derived files for newly planned episodes only; leave others unchanged.

        Same-name files for new IDs are absent from ledgers and not any episode's source; archive by renaming before writing new derived files. Remainder
        ``_remaining.txt`` is not source and records no planning progress; clean it too.

        Execute in two stages: validate/build write plans first, then persist all after validation passes. Invalid ranges/unreadable sources raise during validation and abort
        commits (rolling back ledgers with episode files untouched).
        """
        source_dir = self.project_path / "source"
        # Refuse writes when source/ is a symlink: derived files would land at link targets, potentially outside projects.
        if source_dir.is_symlink():
            raise EpisodePlanningError("source/ 不能是符号链接，拒绝派生集文件")
        writes: list[tuple[Path, str]] = []
        order = whole_source_files(project)
        for entry in project.get("episodes") or []:
            if not isinstance(entry, dict):
                continue
            num = parse_episode_num(entry.get("episode"))
            if num is None or num not in new_ids:
                continue
            span = parse_source_range(entry)
            if span is None:
                raise EpisodePlanningError(f"集（id={num}）原文范围记录非法，无法写入集文件，提交已中止")
            for rel in self._span_files(order, span):
                if rel not in text_cache:
                    try:
                        text_cache[rel] = self._load_normalized_source(rel)
                    except EpisodePlanningError as exc:
                        raise EpisodePlanningError(f"集（id={num}）集文件写入失败，提交已中止：{exc}") from exc
            # Python slicing silently tolerates negatives/out-of-bounds; corrupt coordinates produce ledger-inconsistent contents, so reject explicitly.
            content = span_text(text_cache, order, span)
            if content is None:
                raise EpisodePlanningError(
                    f"集（id={num}）原文范围越界（{span.source_file} start={span.start}，{span.end_file} end={span.end}），"
                    "无法写入集文件，提交已中止"
                )
            writes.append((episode_source_path(self.project_path, num), content))
        source_dir.mkdir(exist_ok=True)
        for num, aliases in discover_episode_file_aliases(self.project_path).items():
            if num in new_ids:
                for alias in aliases:
                    alias.rename(archive_episode_file_path(alias))
        for episode_path, content in writes:
            # Derived-file bytes match ledger slice text: no platform newline translation, ensuring stable byte-recomputed Manifest bases.
            episode_path.write_text(content, encoding="utf-8", newline="\n")
        remaining = source_dir / "_remaining.txt"
        if remaining.is_file():
            try:
                remaining.unlink()  # hard-delete: derived -- derived unsplit-source files, regenerable from episode ledgers
            except OSError as exc:
                logger.warning("余文文件清理失败（不阻断提交）：%s: %s", remaining, exc)

    def _compute_ledger_stats(self, project: Mapping[str, Any]) -> LedgerStats:
        """Compute global ledger volume distributions: total episodes, five smallest episodes and median volume (for deviation checks).

        Slice sources by source_range for volume. Skip entries without positions or readable sources,
        without blocking overall statistics (best-effort checking material, never commit prevalidation).
        """
        language = _language_of(project)
        text_cache: dict[str, str] = {}
        order = whole_source_files(project)
        units_by_episode: dict[int, int] = {}
        for entry in project.get("episodes") or []:
            if not isinstance(entry, dict):
                continue
            num = parse_episode_num(entry.get("episode"))
            if num is None:
                continue
            span = parse_source_range(entry)
            if span is None:
                continue
            try:
                for rel in self._span_files(order, span):
                    if rel not in text_cache:
                        text_cache[rel] = self._load_normalized_source(rel)
            except EpisodePlanningError:
                continue
            text = span_text(text_cache, order, span)
            if text is None:
                continue
            units_by_episode[num] = count_reading_units(text, language)

        ordered = sorted(units_by_episode.items(), key=lambda pair: (pair[1], pair[0]))
        values = sorted(units_by_episode.values())
        return LedgerStats(
            total_episodes=_count_planned_episodes(project),
            smallest=ordered[:5],
            median_units=round(statistics.median(values)) if values else None,
            target_volume=resolve_episode_target_volume(project, language=language),
        )


def _next_source_rel(source_order: list[str], rel: str) -> str | None:
    """Next whole-source manifest file after ``rel``; None if ``rel`` is absent or last."""
    try:
        index = source_order.index(rel)
    except ValueError:
        return None
    return source_order[index + 1] if index + 1 < len(source_order) else None


def _gap_start(project: Mapping[str, Any], sources: list[SourceDoc], gap: tuple[str, int]) -> tuple[str, int]:
    """Beginning of the unsplit source segment ending at ``gap``; refuse if no longer unsplit."""
    rel, end = gap
    index = next((i for i, doc in enumerate(sources) if doc.rel_path == rel), None)
    if index is None:
        raise EpisodePlanningError(f"整本源文里没有这个可读的文件：{rel}")
    if not 0 < end <= len(sources[index].text):
        raise EpisodePlanningError(f"未切分原文的终点越界：{rel} 长度 {len(sources[index].text)}，终点 {end}")
    found = unsplit_range_ending_at(cut_episode_placements(project, sources), file_index=index, end=end)
    if found is None:
        raise EpisodePlanningError("这段原文已经切成集，不再是未切分的原文；请刷新后重试")
    return rel, found[0]


def _gap_context_entries(
    project: Mapping[str, Any], sources: list[SourceDoc], start_ref: tuple[str, int]
) -> list[dict[str, Any]]:
    """Continuation context for gap planning: the last few cut episodes preceding gaps by source position."""
    placements = cut_episode_placements(project, sources)
    index = next(i for i, doc in enumerate(sources) if doc.rel_path == start_ref[0])
    before = sorted((p for p in placements.values() if p.position < (index, start_ref[1])), key=lambda p: p.position)
    by_id = {
        parse_episode_num(entry.get("episode")): entry
        for entry in project.get("episodes") or []
        if isinstance(entry, dict)
    }
    return [by_id[p.episode] for p in before[-_CONTEXT_EPISODES_LIMIT:] if p.episode in by_id]


def _candidate_source_changed_error() -> EpisodePlanningError:
    return EpisodePlanningError("整本源文在生成新的分集方案后有改动，这份方案已过时；请放弃后重新规划")


def _require_candidate(project: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    candidate = replan_candidate(project)
    if candidate is None or candidate.get("id") != candidate_id:
        raise PlanningConflictError("这份新的分集方案已经被采纳或放弃，本批结果作废")
    return candidate


def _candidate_cursor(candidate: Mapping[str, Any]) -> tuple[str, int]:
    try:
        return candidate_cursor(candidate)
    except ReplanError as exc:
        raise EpisodePlanningError(str(exc)) from exc


def _check_candidate_sources(candidate: Mapping[str, Any], sources: list[SourceDoc]) -> None:
    if candidate.get("source_fingerprints") != compute_source_fingerprints(sources):
        raise _candidate_source_changed_error()


def _candidate_context_before(
    project: Mapping[str, Any], sources: list[SourceDoc], candidate: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    """Cut episodes before candidate starts in source order; empty when replanning from whole-source beginning."""
    if candidate.get("from_beginning"):
        return []
    try:
        source_file, offset = candidate_start(candidate)
    except ReplanError as exc:
        raise EpisodePlanningError(str(exc)) from exc
    index = next((i for i, doc in enumerate(sources) if doc.rel_path == source_file), None)
    if index is None:
        return []
    placements = cut_episode_placements(project, sources)
    before = sorted((p for p in placements.values() if p.position < (index, offset)), key=lambda p: p.position)
    by_id = {
        parse_episode_num(entry.get("episode")): entry
        for entry in project.get("episodes") or []
        if isinstance(entry, dict)
    }
    return [by_id[p.episode] for p in before if p.episode in by_id]


def _count_planned_episodes(project: Mapping[str, Any]) -> int:
    """Current ledger cut-episode count (all ledger_status values) for global progress prompts, excluding other origins."""
    return sum(
        1
        for entry in (project.get("episodes") or [])
        if isinstance(entry, dict) and parse_episode_num(entry.get("episode")) is not None and is_cut_episode(entry)
    )


def _context_entries(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Titles/hooks from the last few planned episodes as coherent continuation context.

    Only cut episodes with positional records: other-origin episodes and legacy splits without source_range did not come from this mechanism;
    their titles/hooks may follow different planning rules and must not guide continuation.
    """
    anchored = [
        entry
        for entry in project.get("episodes") or []
        if isinstance(entry, dict)
        and parse_episode_num(entry.get("episode")) is not None
        and is_cut_episode(entry)
        and isinstance(entry.get("source_range"), Mapping)
    ]
    return anchored[-_CONTEXT_EPISODES_LIMIT:]


def _build_planning_prompt(
    *,
    project: Mapping[str, Any],
    source_kind: str,
    window: str,
    window_is_final: bool,
    max_episodes: int | None,
    followed_by_episode: bool = False,
    content_mode: str,
    context_entries: list[dict[str, Any]],
    instructions: str | None,
    failure: list[str] | None,
    progress: _PlanningProgress | None = None,
) -> str:
    """Planning prompts for text models only, without i18n.

    Empty ``instructions`` omits additional-instruction sections, keeping prompts byte-identical to no additional instructions. Non-None ``progress``
    injects global-progress sections (callers pass it only with nonempty instructions).
    """
    overview = project.get("overview")
    if not isinstance(overview, Mapping):
        overview = {}
    language = _language_of(project)
    target_volume = resolve_episode_target_volume(project, language=language)
    return builtin_templates.render(
        "text/episode_plan",
        content_mode=content_mode,
        source_kind=source_kind,
        synopsis=overview.get("synopsis") or None,
        genre=overview.get("genre") or None,
        unit_noun=reading_unit_noun(language),
        target_volume=None
        if target_volume is None
        else {
            "units": target_volume.units,
            "seconds": target_volume.seconds,
            "units_per_second": None
            if target_volume.units_per_second is None
            else f"{target_volume.units_per_second:g}",
        },
        max_episodes=max_episodes,
        context_entries=[
            {"episode": entry.get("episode"), "title": entry.get("title") or None, "hook": entry.get("hook") or ""}
            for entry in context_entries
        ],
        instructions=instructions or None,
        progress=None if progress is None else asdict(progress),
        window_is_final=window_is_final,
        followed_by_episode=followed_by_episode,
        failure=failure or None,
        window=window,
    )
