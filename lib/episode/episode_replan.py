"""重新规划：AI 从某一集起产出候选规划，分集账本不动；创作者看过候选与变更摘要后采纳或放弃（ADR 0032）。

候选存放在 ``project.json`` 的 :data:`REPLAN_CANDIDATE_KEY` 下，同一项目同时最多一份。它记下：

- 重新规划的起点：发起的那一集的原文开头。账本里有旧拆分流程的存量集，或源文与账本记录的指纹不一致（源文已替换）
  时，只能从第一个切出集起重新规划，起点是整本源文开头。
- 生成时账本的切分结构（:func:`ledger_layout_revision`）与整本源文各文件的指纹。采纳时在项目锁内校验两者，
  任一不符就拒绝，提示重新生成。
- 候选集：标题、钩子、原文范围（剧情演绎另含分集大纲），按源文位置排列，尚未分配集 ID。生成由
  :meth:`lib.episode.episode_planner.EpisodePlanner.plan_candidate` 逐窗追加。

生成可能在中途停止（创作者主动停止、AI 找不到切分点、模型出错），已生成的部分仍是候选：可以接着生成
（:func:`resume_replan_candidate`，沿用发起时的附加指令）、采纳或放弃。找不到切分点与模型出错时，候选记下中断原因
（:func:`record_replan_interruption`）。

采纳时替换起点及以后的全部切出集，候选没有覆盖到的也一样：

- 候选集一律分配新集 ID。被替换下来的旧切出集（含原文超出候选覆盖范围的）有产物的转为无原文的集、标 stale，产物仍归它，按原相对顺序移到
  播出顺序末尾；勾选「一并删除」时，这些集在采纳后按删除一集的口径硬删除。没有产物的直接移除。
- 夹在范围里的其他来源的集按锚点落位：锚点是播出顺序中它前面最近的切出集的原文结尾。锚点不在起点之后时排在全部
  候选集之前，否则排在第一个原文结尾不早于锚点的候选集之后（候选没有覆盖到锚点时排在最后一个候选集之后）。
  锚点所在的集没有原文范围或源文已替换时，锚点失效，这些集按原相对顺序排到全部候选集之后。

采纳前先返回确认清单（:class:`ReplanConfirmationRequired`），确认文本由 :func:`render_replan_adoption_text` 成文；
创作者确认后带上清单的 ``revision`` 重新调用，锁内复核出的清单变了时退回确认，不写入。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lib.episode.episode_deletion import (
    EpisodeDeletionConfirmationRequired,
    delete_episode,
    episode_deletion_impact,
    render_episode_loss_items,
)
from lib.episode.episode_excerpts import edge_sentences
from lib.episode.episode_ids import (
    allocate_episode_ids,
    episode_display_name,
    episode_id_high_water,
)
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    compute_source_fingerprints,
    discover_product_episode_nums,
    episode_has_products,
    has_downstream_products,
    mismatched_source_fingerprints,
    parse_positive_episode_num,
    parse_source_range,
    well_formed_ledger_entries,
)
from lib.episode.episode_management import EpisodeManagementError
from lib.episode.episode_paths import episode_script_relpath, episode_source_path
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    CutPlacement,
    SourceOrigin,
    archive_episode_file_path,
    cut_episode_placements,
    discover_sources,
    episode_source_origin,
    first_cut_episode_id,
    is_cut_episode,
    legacy_cut_episode_ids,
    source_snapshot_path,
    sync_source_snapshots,
    whole_source_files,
)
from lib.infra.text_metrics import count_reading_units
from lib.project.project_manager import ProjectManager
from lib.script import script_review

logger = logging.getLogger(__name__)

#: ``project.json`` 里存放候选的键。
REPLAN_CANDIDATE_KEY = "episode_replan"

#: 生成中途停止的原因：AI 在窗口里找不到切分点、模型或其他错误。创作者主动停止不记原因。
REPLAN_INTERRUPTIONS = ("no_cut_point", "failed")


class ReplanError(ValueError):
    """重新规划被拒；``code`` 是稳定的原因码，入口据此映射状态码与文案。账本与候选都不被改动。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 候选的读取
# ---------------------------------------------------------------------------


def replan_candidate(project: Mapping[str, Any]) -> dict[str, Any] | None:
    """项目里悬而未决的候选；没有时返回 None。"""
    raw = project.get(REPLAN_CANDIDATE_KEY)
    return raw if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None


def candidate_episodes(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """候选集，按源文位置。"""
    raw = candidate.get("episodes")
    return [episode for episode in raw if isinstance(episode, dict)] if isinstance(raw, list) else []


def candidate_start(candidate: Mapping[str, Any]) -> tuple[str, int]:
    start = candidate.get("start")
    if not isinstance(start, Mapping):
        raise ReplanError("candidate_invalid", "候选没有记录重新规划的起点")
    source_file, offset = start.get("source_file"), start.get("offset")
    if not isinstance(source_file, str) or not isinstance(offset, int) or isinstance(offset, bool):
        raise ReplanError("candidate_invalid", "候选记录的起点形状异常")
    return source_file, offset


def candidate_cursor(candidate: Mapping[str, Any]) -> tuple[str, int]:
    """候选接着生成的起点：最后一个候选集的原文结尾；还没有候选集时是重新规划的起点。"""
    episodes = candidate_episodes(candidate)
    if episodes:
        span = parse_source_range(episodes[-1])
        if span is None:
            raise ReplanError("candidate_invalid", "候选集的原文范围形状异常")
        return span.end_file, span.end
    return candidate_start(candidate)


def ledger_layout_revision(project: Mapping[str, Any]) -> str:
    """账本切分结构的指纹：各集的集 ID、播出顺序、原文来源与原文范围。

    标题、钩子与集规划状态不计入：它们变了不影响候选与现有分集的对应关系。
    """
    raw = project.get("episodes")
    layout = [
        [entry.get("episode"), episode_source_origin(entry).value, entry.get("source_range")]
        for entry in (raw if isinstance(raw, list) else [])
        if isinstance(entry, Mapping)
    ]
    payload = json.dumps(layout, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def candidate_staleness(project: Mapping[str, Any], candidate: Mapping[str, Any], docs: list[SourceDoc]) -> str | None:
    """候选是否已过时：账本的切分结构变了为 ``ledger_changed``，整本源文变了为 ``source_changed``，否则为 None。"""
    if candidate.get("ledger_revision") != ledger_layout_revision(project):
        return "ledger_changed"
    if candidate.get("source_fingerprints") != compute_source_fingerprints(docs):
        return "source_changed"
    return None


# ---------------------------------------------------------------------------
# 发起与放弃
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplanScope:
    """从某一集起重新规划的范围。"""

    episode: int
    #: 重新规划的起点：文件与文件内偏移。
    source_file: str
    offset: int
    #: 从整本源文开头重新规划（有旧拆分流程的存量集，或源文已替换）。
    from_beginning: bool
    #: 源文与账本记录的指纹不一致：现有分集的原文范围已对不上当前源文。
    source_replaced: bool
    #: 会被替换的切出集，按播出顺序。
    replaced: list[int] = field(default_factory=list)
    #: 其中已开始制作（有产物）的集。
    started: list[int] = field(default_factory=list)


def _entries(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = well_formed_ledger_entries(project)
    if entries is None:
        raise ReplanError("ledger_invalid", "分集账本的形状异常，不能重新规划")
    return entries


def _replaced_ids(
    entries: list[dict[str, Any]],
    placements: Mapping[int, CutPlacement],
    *,
    episode: int,
    start: tuple[int, int],
    from_beginning: bool,
) -> list[int]:
    """起点及以后的切出集：落位在起点之后的，以及播出顺序在发起集之后、落不了位的。"""
    replaced: list[int] = []
    seen_from = False
    for entry in entries:
        num = parse_positive_episode_num(entry.get("episode"))
        if num is None or not is_cut_episode(entry):
            continue
        seen_from = seen_from or num == episode
        placement = placements.get(num)
        if from_beginning or (placement.position >= start if placement is not None else seen_from):
            replaced.append(num)
    return replaced


def replan_scope(project_dir: Path, project: Mapping[str, Any], episode: int) -> ReplanScope:
    """从 ``episode`` 起重新规划的范围；不能从这一集起重新规划时抛 :class:`ReplanError`。"""
    if replan_candidate(project) is not None:
        raise ReplanError("candidate_pending", "已有一份新的分集方案等待采纳或放弃")
    entries = _entries(project)
    entry = next((e for e in entries if parse_positive_episode_num(e.get("episode")) == episode), None)
    if entry is None:
        raise ReplanError("episode_not_found", f"集（id={episode}）不在账本中")
    if not is_cut_episode(entry):
        raise ReplanError("not_cut_episode", f"集（id={episode}）不是切自整本源文的集")
    docs = discover_sources(project_dir, project)
    if not any(doc.text.strip() for doc in docs):
        raise ReplanError("whole_source_missing", "整本源文还没有可规划的原文")
    source_replaced = bool(mismatched_source_fingerprints(project.get(SOURCE_FINGERPRINTS_KEY), docs))
    from_beginning = source_replaced or bool(legacy_cut_episode_ids(project))
    placements = cut_episode_placements(project, docs)
    if from_beginning:
        if episode != first_cut_episode_id(project):
            raise ReplanError("from_first_only", "现有分集的原文范围对不上当前源文，只能从第一个切出集起重新规划")
        source_file, offset, start = docs[0].rel_path, 0, (0, 0)
    else:
        placement = placements.get(episode)
        if placement is None:
            raise ReplanError("episode_not_placed", f"集（id={episode}）的原文范围落不到整本源文里")
        source_file, offset, start = docs[placement.file_index].rel_path, placement.start, placement.position
    replaced = _replaced_ids(entries, placements, episode=episode, start=start, from_beginning=from_beginning)
    product_nums = discover_product_episode_nums(project_dir)
    by_id = {parse_positive_episode_num(e.get("episode")): e for e in entries}
    started = [num for num in replaced if episode_has_products(project_dir, num, by_id[num], product_nums=product_nums)]
    return ReplanScope(
        episode=episode,
        source_file=source_file,
        offset=offset,
        from_beginning=from_beginning,
        source_replaced=source_replaced,
        replaced=replaced,
        started=started,
    )


def create_replan_candidate(project_path: str | Path, *, episode: int, instructions: str | None) -> str:
    """登记一份空候选，返回候选 ID；候选集由逐窗生成追加。已有候选或不能从这一集起重新规划时拒绝。"""
    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)
    candidate_id = uuid.uuid4().hex

    def _commit(p: dict[str, Any]) -> None:
        scope = replan_scope(project_dir, p, episode)
        p[REPLAN_CANDIDATE_KEY] = {
            "id": candidate_id,
            "episode": episode,
            "start": {"source_file": scope.source_file, "offset": scope.offset},
            "from_beginning": scope.from_beginning,
            "source_replaced": scope.source_replaced,
            "instructions": (instructions or "").strip() or None,
            "ledger_revision": ledger_layout_revision(p),
            "source_fingerprints": compute_source_fingerprints(discover_sources(project_dir, p)),
            "episodes": [],
            "complete": False,
        }

    pm.update_project(project_dir.name, _commit)
    logger.info("已发起重新规划：项目 %s，从集 ID %s 起，候选 %s", project_dir.name, episode, candidate_id)
    return candidate_id


def discard_replan_candidate(project_path: str | Path, candidate_id: str) -> None:
    """放弃候选：分集账本什么都不变。"""
    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)

    def _commit(p: dict[str, Any]) -> None:
        _require_candidate(p, candidate_id)
        p.pop(REPLAN_CANDIDATE_KEY, None)

    pm.update_project(project_dir.name, _commit)
    logger.info("已放弃重新规划的候选：项目 %s，候选 %s", project_dir.name, candidate_id)


def record_replan_interruption(project_path: str | Path, candidate_id: str, reason: str) -> None:
    """记下候选生成中途停止的原因；候选已不在（已采纳或放弃）时什么都不做。"""
    if reason not in REPLAN_INTERRUPTIONS:
        raise ValueError(f"未知的中断原因：{reason}")
    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)

    def _matches(p: Mapping[str, Any]) -> dict[str, Any] | None:
        candidate = replan_candidate(p)
        return candidate if candidate is not None and candidate.get("id") == candidate_id else None

    if _matches(pm.load_project(project_dir.name)) is None:
        return

    def _commit(p: dict[str, Any]) -> None:
        if (candidate := _matches(p)) is not None:
            candidate["interrupted"] = reason

    pm.update_project(project_dir.name, _commit)


def resume_replan_candidate(project_path: str | Path, candidate_id: str) -> str | None:
    """接着生成中途停止的候选：清掉中断原因，返回发起时的附加指令。

    候选已覆盖到整本源文结尾（``candidate_complete``）或已过时（``ledger_changed`` / ``source_changed``）时拒绝。
    """
    project_dir = Path(project_path)
    resumed: dict[str, str | None] = {}

    def _commit(p: dict[str, Any]) -> None:
        candidate = _require_candidate(p, candidate_id)
        if candidate.get("complete"):
            raise ReplanError("candidate_complete", "新的分集方案已覆盖到整本源文结尾")
        stale = candidate_staleness(p, candidate, discover_sources(project_dir, p))
        if stale is not None:
            raise ReplanError(stale, "生成这份方案之后分集或源文有改动，需要重新生成")
        candidate.pop("interrupted", None)
        instructions = candidate.get("instructions")
        resumed["instructions"] = instructions if isinstance(instructions, str) else None

    ProjectManager.for_project_dir(project_dir).update_project(project_dir.name, _commit)
    logger.info("接着生成重新规划的候选：项目 %s，候选 %s", project_dir.name, candidate_id)
    return resumed["instructions"]


def _require_candidate(project: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    candidate = replan_candidate(project)
    if candidate is None or candidate.get("id") != candidate_id:
        raise ReplanError("candidate_not_found", "这份新的分集方案已经不在了")
    return candidate


# ---------------------------------------------------------------------------
# 采纳的推演
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Adoption:
    """一次采纳对账本的改动，由候选与当前账本推出。"""

    #: 被替换的切出集，按播出顺序。
    replaced: list[int]
    retired: list[int]
    removed: list[int]
    #: 已开始制作、且原文范围在候选里找不到一模一样的一集。
    needs_review: list[int]
    #: 原文超出候选覆盖范围的被替换集（候选没有生成到整本源文结尾时）。
    uncovered: list[int]
    #: 播出位置变了的其他集：（集 ID，原位置，新位置）。
    moved: list[tuple[int, int, int]]
    #: 采纳后的播出顺序：``("old", 集 ID)`` 或 ``("new", 候选集下标)``；退下的集在末尾。
    order: list[tuple[str, int]]


def _source_position(order: Mapping[str, int], source_range: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """候选集的原文范围（分集规划逐个文件取窗口，候选集不跨文件）落到（文件下标，起点，结尾）。"""
    rel, start, end = source_range.get("source_file"), source_range.get("start"), source_range.get("end")
    if not isinstance(rel, str) or rel not in order or not isinstance(start, int) or not isinstance(end, int):
        return None
    if source_range.get("end_file", rel) != rel:
        return None
    return order[rel], start, end


def _candidate_positions(candidate: Mapping[str, Any], docs: list[SourceDoc]) -> list[tuple[int, int, int]]:
    """候选集的源文位置：（文件下标，起点，结尾）。"""
    order = {doc.rel_path: index for index, doc in enumerate(docs)}
    positions: list[tuple[int, int, int]] = []
    for episode in candidate_episodes(candidate):
        raw = episode.get("source_range")
        position = _source_position(order, raw) if isinstance(raw, Mapping) else None
        if position is None:
            raise ReplanError("candidate_invalid", "候选集的原文范围落不到整本源文里")
        positions.append(position)
    return positions


def _plan_adoption(project_dir: Path, project: Mapping[str, Any], candidate: Mapping[str, Any]) -> _Adoption:
    docs = discover_sources(project_dir, project)
    stale = candidate_staleness(project, candidate, docs)
    if stale is not None:
        raise ReplanError(stale, "生成这份方案之后分集或源文有改动，需要重新生成")
    positions = _candidate_positions(candidate, docs)
    if not positions:
        raise ReplanError("candidate_empty", "新的分集方案里还没有集")
    entries = _entries(project)
    placements = cut_episode_placements(project, docs)
    order = {doc.rel_path: index for index, doc in enumerate(docs)}
    source_file, offset = candidate_start(candidate)
    if source_file not in order:
        raise ReplanError("source_changed", "重新规划的起点所在文件已不在整本源文里")
    start = (order[source_file], offset)
    replaced = _replaced_ids(
        entries,
        placements,
        episode=int(candidate.get("episode") or 0),
        start=start,
        from_beginning=bool(candidate.get("from_beginning")),
    )
    replaced_set = set(replaced)
    product_nums = discover_product_episode_nums(project_dir)
    ids = [parse_positive_episode_num(entry.get("episode")) for entry in entries]
    by_id = {num: entry for num, entry in zip(ids, entries, strict=True) if num is not None}
    retired = [num for num in replaced if episode_has_products(project_dir, num, by_id[num], product_nums=product_nums)]
    removed = [num for num in replaced if num not in retired]
    exact = {(fi, s, e) for fi, s, e in positions}
    anchors_valid = not candidate.get("source_replaced")
    needs_review = [
        num
        for num in retired
        if not anchors_valid
        or (placement := placements.get(num)) is None
        or placement.crosses_files
        or (placement.file_index, placement.start, placement.end) not in exact
    ]

    uncovered: list[int] = []
    if not candidate.get("complete"):
        cursor_file, cursor_offset = candidate_cursor(candidate)
        cursor = (order.get(cursor_file, len(order)), cursor_offset)
        uncovered = [
            num for num in replaced if (placement := placements.get(num)) is None or placement.end_position > cursor
        ]

    # 其他来源的集按锚点落位；第一个被替换的集之前的条目原位不动
    first = min((index for index, num in enumerate(ids) if num in replaced_set), default=len(entries))
    before: list[int] = []
    attached: list[list[int]] = [[] for _ in positions]
    unanchored: list[int] = []
    previous_cut: int | None = None
    for index, (num, entry) in enumerate(zip(ids, entries, strict=True)):
        if num is None:
            continue
        if is_cut_episode(entry):
            previous_cut = num
        if index < first or num in replaced_set:
            continue
        placement = placements.get(previous_cut) if previous_cut is not None else None
        if previous_cut is None:
            before.append(num)
        elif placement is None or not anchors_valid:
            unanchored.append(num)
        elif placement.end_position <= start:
            before.append(num)
        else:
            anchor = placement.end_position
            slot = next(
                (i for i, (fi, _s, end) in enumerate(positions) if (fi, end) >= anchor),
                len(positions) - 1,
            )
            attached[slot].append(num)

    new_order: list[tuple[str, int]] = [("old", num) for num in ids[:first] if num is not None]
    new_order.extend(("old", num) for num in before)
    for index, group in enumerate(attached):
        new_order.append(("new", index))
        new_order.extend(("old", num) for num in group)
    new_order.extend(("old", num) for num in unanchored)
    new_order.extend(("old", num) for num in retired)

    old_positions = {num: index for index, num in enumerate((n for n in ids if n is not None), start=1)}
    moved = [
        (num, old_positions[num], new_position)
        for new_position, (kind, num) in enumerate(new_order, start=1)
        if kind == "old" and num not in replaced_set and old_positions[num] != new_position
    ]
    return _Adoption(
        replaced=replaced,
        retired=retired,
        removed=removed,
        needs_review=needs_review,
        uncovered=uncovered,
        moved=moved,
        order=new_order,
    )


@dataclass(frozen=True)
class ReplanAdoptionImpact:
    """采纳一份候选的变更清单。``revision`` 是这份清单的指纹，确认时回传。"""

    candidate: str
    #: 发起重新规划的那一集。
    episode: int
    #: 被替换的切出集数与候选集数。
    old_count: int
    new_count: int
    #: 已开始制作，转为无原文的集并标 stale，移到播出顺序末尾。
    retired: list[int]
    #: 没有产物，直接移除。
    removed: list[int]
    #: 已开始制作且原文范围有变化。
    needs_review: list[int]
    #: 原文超出新方案覆盖范围、同样按被替换处理的集（新方案没有生成到整本源文结尾时）。
    uncovered: list[int]
    #: 播出位置变了的其他集：（集 ID，原位置，新位置）。
    moved: list[tuple[int, int, int]]
    #: 勾选「一并删除」时退下的集会丢失的内容：集 ID → 删除一集的丢失清单。
    losses: dict[int, dict[str, Any]]

    @property
    def revision(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["moved"] = [{"episode": num, "from": old, "to": new} for num, old, new in self.moved]
        data["losses"] = {str(num): loss for num, loss in self.losses.items()}
        data["revision"] = self.revision
        return data


def _adoption_impact(
    project_dir: Path, project: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[_Adoption, ReplanAdoptionImpact]:
    adoption = _plan_adoption(project_dir, project, candidate)
    losses = {num: episode_deletion_impact(project_dir, project, num).to_dict() for num in adoption.retired}
    impact = ReplanAdoptionImpact(
        candidate=str(candidate["id"]),
        episode=int(candidate.get("episode") or 0),
        old_count=len(adoption.replaced),
        new_count=len(candidate_episodes(candidate)),
        retired=adoption.retired,
        removed=adoption.removed,
        needs_review=adoption.needs_review,
        uncovered=adoption.uncovered,
        moved=adoption.moved,
        losses=losses,
    )
    return adoption, impact


@dataclass(frozen=True)
class ReplanConfirmationRequired:
    """等待确认；返回本对象时没有发生任何写入。"""

    impact: ReplanAdoptionImpact


@dataclass(frozen=True)
class ReplanAdoptionResult:
    impact: ReplanAdoptionImpact
    #: 候选集分配到的新集 ID，按源文位置。
    episodes: list[int]
    #: 勾选「一并删除」后删掉的集。
    deleted: list[int] = field(default_factory=list)


class _StaleConfirmation(Exception):
    def __init__(self, impact: ReplanAdoptionImpact):
        super().__init__("replan adoption needs confirmation")
        self.impact = impact


# ---------------------------------------------------------------------------
# 采纳
# ---------------------------------------------------------------------------


def _write_new_episode_file(project_dir: Path, episode: int, text: str) -> None:
    path = episode_source_path(project_dir, episode)
    if path.exists() or path.is_symlink():
        # 新集 ID 的同名文件不在账本里，不是任何一集的原文，先改名留底
        path.rename(archive_episode_file_path(path))
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _apply_adoption(
    project_dir: Path, project: dict[str, Any], candidate: Mapping[str, Any], adoption: _Adoption
) -> list[int]:
    """按推演改写 ``project`` 并落盘集文件与快照，返回候选集的新集 ID。"""
    docs = discover_sources(project_dir, project)
    texts = {doc.rel_path: doc.text for doc in docs}
    entries = _entries(project)
    by_id = {parse_positive_episode_num(entry.get("episode")): entry for entry in entries}
    # 旧拆分流程的存量集没有原文范围，集文件是它唯一的原文记录，无法从账本重造
    legacy = {num for num in adoption.replaced if parse_source_range(by_id[num]) is None}
    drafts = candidate_episodes(candidate)
    new_ids = allocate_episode_ids(project, len(drafts))
    new_entries: list[dict[str, Any]] = []
    for num, draft in zip(new_ids, drafts, strict=True):
        entry: dict[str, Any] = {
            "episode": num,
            "title": draft.get("title") or "",
            "script_file": episode_script_relpath(num),
            SOURCE_ORIGIN_FIELD: SourceOrigin.WHOLE_SOURCE.value,
            "source_range": dict(draft["source_range"]),
            "hook": draft.get("hook") or "",
            "ledger_status": "planned",
        }
        if isinstance(draft.get("outline"), Mapping):
            entry["outline"] = dict(draft["outline"])
        # 新集 ID 在磁盘上已有产物（历史最高号之外的手工残留）时标 stale，产物不删除
        if has_downstream_products(project_dir, num, entry):
            script_review.mark_ledger_stale(project_dir, project, entry, num)
        new_entries.append(entry)
    for num in adoption.retired:
        entry = by_id[num]
        entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.NONE.value
        entry.pop("source_range", None)
        script_review.mark_ledger_stale(project_dir, project, entry, num)
    project["episodes"] = [by_id[num] if kind == "old" else new_entries[num] for kind, num in adoption.order]
    project[SOURCE_FINGERPRINTS_KEY] = dict(candidate.get("source_fingerprints") or {})
    project.pop(REPLAN_CANDIDATE_KEY, None)

    for num in adoption.replaced:
        path = episode_source_path(project_dir, num)
        if num in legacy and path.is_file() and not path.is_symlink():
            path.rename(archive_episode_file_path(path))
        else:
            path.unlink(missing_ok=True)
    for num, entry in zip(new_ids, new_entries, strict=True):
        source_range = entry["source_range"]
        text = texts[source_range["source_file"]][source_range["start"] : source_range["end"]]
        _write_new_episode_file(project_dir, num, text)
    refreshed = {draft["source_range"]["source_file"] for draft in drafts}
    sync_source_snapshots(project_dir, project, texts, refreshed=refreshed)
    return new_ids


def adopt_replan_candidate(
    project_path: str | Path, candidate_id: str, *, revision: str | None = None, delete_retired: bool = False
) -> ReplanAdoptionResult | ReplanConfirmationRequired:
    """采纳候选。``revision`` 与当前变更清单的指纹不符（含未传）时只返回清单，不写入。

    ``delete_retired`` 为 True 时，退下的集在采纳提交后逐个按删除一集的口径硬删除；删除失败的集保留为无原文的集。
    """
    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)
    project_name = project_dir.name
    project = pm.load_project(project_name)
    candidate = _require_candidate(project, candidate_id)
    adoption, impact = _adoption_impact(project_dir, project, candidate)
    if revision != impact.revision:
        return ReplanConfirmationRequired(impact=impact)

    planned_first = episode_id_high_water(project) + 1
    touched = [*adoption.replaced, *range(planned_first, planned_first + impact.new_count)]
    formal_paths = [episode_source_path(project_dir, num) for num in touched]
    formal_paths.extend(source_snapshot_path(project_dir, rel) for rel in whole_source_files(project))
    committed: dict[str, Any] = {}

    def _commit(p: dict[str, Any]) -> None:
        locked_candidate = _require_candidate(p, candidate_id)
        locked_adoption, locked_impact = _adoption_impact(project_dir, p, locked_candidate)
        if locked_impact.revision != revision:
            raise _StaleConfirmation(locked_impact)
        if episode_id_high_water(p) + 1 != planned_first:
            raise ReplanError("conflict", "分集账本刚被改动，这次采纳没有执行")
        committed["episodes"] = _apply_adoption(project_dir, p, locked_candidate, locked_adoption)

    try:
        pm.update_project(project_name, _commit, formal_paths=formal_paths)
    except _StaleConfirmation as exc:
        return ReplanConfirmationRequired(impact=exc.impact)
    deleted = _delete_retired(pm, project_name, impact.retired) if delete_retired else []
    logger.info(
        "已采纳重新规划：项目 %s，新集 %s，退下 %s，移除 %s，删除 %s",
        project_name,
        committed["episodes"],
        impact.retired,
        impact.removed,
        deleted,
    )
    return ReplanAdoptionResult(impact=impact, episodes=committed["episodes"], deleted=deleted)


def _delete_retired(pm: ProjectManager, project_name: str, episodes: Iterable[int]) -> list[int]:
    deleted: list[int] = []
    for episode in episodes:
        try:
            outcome = delete_episode(pm, project_name, episode)
            if isinstance(outcome, EpisodeDeletionConfirmationRequired):
                outcome = delete_episode(pm, project_name, episode, revision=outcome.impact.revision)
        except (EpisodeManagementError, OSError, ValueError):
            logger.warning("采纳后删除退下的集失败：项目 %s，集 ID %s", project_name, episode, exc_info=True)
            continue
        if not isinstance(outcome, EpisodeDeletionConfirmationRequired):
            deleted.append(episode)
    return deleted


# ---------------------------------------------------------------------------
# 摘要与确认文本
# ---------------------------------------------------------------------------


def replan_candidate_summary(project_dir: Path, project: Mapping[str, Any]) -> dict[str, Any] | None:
    """「新的分集方案」的摘要与逐集变化；没有候选时返回 None。

    ``stale`` 不为 None 时候选已过时，只给出候选本身的内容，不推演采纳。
    """
    candidate = replan_candidate(project)
    if candidate is None:
        return None
    docs = discover_sources(project_dir, project)
    texts = {doc.rel_path: doc.text for doc in docs}
    language = project.get("source_language") if isinstance(project.get("source_language"), str) else None
    stale = candidate_staleness(project, candidate, docs)
    placements = cut_episode_placements(project, docs) if stale is None else {}
    order = {doc.rel_path: index for index, doc in enumerate(docs)}
    adoption: _Adoption | None = None
    if stale is None and candidate_episodes(candidate):
        try:
            adoption = _plan_adoption(project_dir, project, candidate)
        except ReplanError:
            adoption = None
    replaced = set(adoption.replaced) if adoption is not None else set()
    anchors_valid = not candidate.get("source_replaced")

    episodes: list[dict[str, Any]] = []
    total_units = 0
    for draft in candidate_episodes(candidate):
        span = parse_source_range(draft)
        if span is None or span.crosses_files:
            continue
        rel, start, end = span.source_file, span.start, span.end
        segment = texts.get(rel, "")[start:end]
        units = count_reading_units(segment, language)
        total_units += units
        first_sentence, last_sentence = edge_sentences(segment)
        overlaps: list[int] = []
        same_as: int | None = None
        if anchors_valid and rel in order:
            for num in sorted(replaced, key=lambda n: placements[n].position if n in placements else (0, 0)):
                placement = placements.get(num)
                if placement is None:
                    continue
                if placement.position < (order[rel], end) and (order[rel], start) < placement.end_position:
                    overlaps.append(num)
                if not placement.crosses_files and placement.position == (order[rel], start) and placement.end == end:
                    same_as = num
        episodes.append(
            {
                "title": draft.get("title") or "",
                "hook": draft.get("hook") or "",
                "source_file": rel,
                "start": start,
                "end": end,
                "units": units,
                "first_sentence": first_sentence,
                "last_sentence": last_sentence,
                "same_as": same_as,
                "overlaps": overlaps,
            }
        )
    source_file, offset = candidate_start(candidate)
    cursor_file, cursor_offset = candidate_cursor(candidate)
    interrupted = candidate.get("interrupted")
    return {
        "id": candidate["id"],
        "episode": candidate.get("episode"),
        "instructions": candidate.get("instructions"),
        "complete": bool(candidate.get("complete")),
        "interrupted": interrupted if interrupted in REPLAN_INTERRUPTIONS else None,
        "stale": stale,
        "start": {"source_file": source_file, "offset": offset},
        "end": {"source_file": cursor_file, "offset": cursor_offset},
        "old_count": len(adoption.replaced) if adoption is not None else None,
        "new_count": len(episodes),
        "units": total_units,
        "average_units": round(total_units / len(episodes)) if episodes else None,
        "retired": adoption.retired if adoption is not None else [],
        "removed": adoption.removed if adoption is not None else [],
        "needs_review": adoption.needs_review if adoption is not None else [],
        "uncovered": adoption.uncovered if adoption is not None else [],
        "moved": [
            {"episode": num, "from": old, "to": new}
            for num, old, new in (adoption.moved if adoption is not None else [])
        ],
        "episodes": episodes,
    }


def render_replan_adoption_text(
    impact: Mapping[str, Any] | ReplanAdoptionImpact, project: Mapping[str, Any], translate: Callable[..., str]
) -> dict[str, str]:
    """把变更清单渲染成确认文本：``text`` 是采纳的后果，``delete_text`` 是勾选「一并删除」时会丢失的内容。

    集以标题或采纳前的播出位置指称。Web 确认框只呈现这两份文本。
    """
    data = impact.to_dict() if isinstance(impact, ReplanAdoptionImpact) else dict(impact)

    def name(episode: int) -> str:
        return episode_display_name(project, episode, translate)

    separator = translate("episode_replan_adopt_separator")
    lines = [
        translate(
            "episode_replan_adopt_summary",
            name=name(int(data["episode"])),
            old=data["old_count"],
            new=data["new_count"],
        )
    ]
    if data.get("uncovered"):
        lines.append(
            translate("episode_replan_adopt_uncovered", episodes=separator.join(name(num) for num in data["uncovered"]))
        )
    if data["retired"]:
        lines.append(
            translate("episode_replan_adopt_retired", episodes=separator.join(name(num) for num in data["retired"]))
        )
    if data["removed"]:
        lines.append(
            translate("episode_replan_adopt_removed", episodes=separator.join(name(num) for num in data["removed"]))
        )
    if data["moved"]:
        moved = separator.join(
            translate("episode_replan_adopt_moved_item", name=name(item["episode"]), old=item["from"], new=item["to"])
            for item in data["moved"]
        )
        lines.append(translate("episode_replan_adopt_moved", episodes=moved))
    delete_lines: list[str] = []
    if data["retired"]:
        delete_lines.append(translate("episode_replan_delete_heading"))
        losses = data.get("losses") or {}
        for num in data["retired"]:
            loss = losses.get(str(num)) or losses.get(num)
            items = render_episode_loss_items(loss, project, translate) if isinstance(loss, Mapping) else []
            delete_lines.append(
                translate(
                    "episode_replan_delete_item",
                    name=name(num),
                    items=translate("episode_delete_separator").join(items),
                )
                if items
                else name(num)
            )
    return {"text": "\n".join(lines), "delete_text": "\n".join(delete_lines)}


__all__ = [
    "REPLAN_CANDIDATE_KEY",
    "REPLAN_INTERRUPTIONS",
    "ReplanAdoptionImpact",
    "ReplanAdoptionResult",
    "ReplanConfirmationRequired",
    "ReplanError",
    "ReplanScope",
    "adopt_replan_candidate",
    "candidate_cursor",
    "candidate_episodes",
    "candidate_staleness",
    "candidate_start",
    "create_replan_candidate",
    "discard_replan_candidate",
    "ledger_layout_revision",
    "record_replan_interruption",
    "render_replan_adoption_text",
    "replan_candidate",
    "replan_candidate_summary",
    "replan_scope",
    "resume_replan_candidate",
]
