"""Replanning: AI generates candidate plans from an episode onward without changing ledgers; creators inspect candidates/change summaries and adopt or discard (ADR 0032).

Candidates live under :data:`REPLAN_CANDIDATE_KEY` in ``project.json``, at most one per project. They record:

- Replanning starts: source beginning of the initiating episode. With legacy split-flow episodes or source fingerprints differing from ledger records (replaced sources),
  replanning can only start at the first cut episode, beginning at the whole-source start.
- Ledger split structure at generation time (:func:`ledger_layout_revision`) and every whole-source file fingerprint. Validate both under project locks on adoption;
  reject mismatches and request regeneration.
- Candidate episodes: titles, hooks, source ranges (plus episode outlines for drama), ordered by source position, without allocated episode IDs. Generation appends
  windows through :meth:`lib.episode.episode_planner.EpisodePlanner.plan_candidate`.

Generation may stop midway (creator stops, AI finds no split point, model errors). Generated portions remain candidates and can continue
(:func:`resume_replan_candidate`, retaining original additional instructions), be adopted or discarded. No-split-point/model errors record interruption reasons
through :func:`record_replan_interruption`.

Adoption replaces all cut episodes from the start onward, including those outside candidate coverage:

- Allocate new IDs to every candidate episode. Replaced old cut episodes (including sources beyond candidate coverage) with artifacts become sourceless/stale, retain artifacts and move in prior relative order
  to broadcast-order end; selecting Delete together hard-deletes them after adoption using single-episode deletion semantics. Remove those without artifacts directly.
- Place intervening episodes of other origins by anchors: the source end of their nearest preceding cut episode in broadcast order. Anchors not after the start place them before all
  candidates; otherwise after the first candidate whose source end is no earlier than the anchor (after the last candidate if coverage does not reach it).
  Without ranges on anchor episodes, or after source replacement, anchors are invalid; place these episodes after all candidates in prior relative order.

Return confirmation inventories before adoption (:class:`ReplanConfirmationRequired`), with text from :func:`render_replan_adoption_text`;
creators resubmit the inventory's ``revision`` after confirmation. Changed inventories under locked rechecks return confirmation without writes.
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
from lib.episode.source_kinds import entry_source_kind, record_episode_kind
from lib.infra.text_metrics import count_reading_units
from lib.project.project_manager import ProjectManager
from lib.script import script_review

logger = logging.getLogger(__name__)

#: Candidate key in ``project.json``.
REPLAN_CANDIDATE_KEY = "episode_replan"

#: Reasons generation stopped midway: no split point found by AI in a window, model or other errors. Creator-initiated stops record no reason.
REPLAN_INTERRUPTIONS = ("no_cut_point", "failed")


class ReplanError(ValueError):
    """Replanning refused; ``code`` is a stable reason code mapped to statuses/copy by entry points. Ledgers and candidates remain unchanged."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Reading candidates.
# ---------------------------------------------------------------------------


def replan_candidate(project: Mapping[str, Any]) -> dict[str, Any] | None:
    """Pending project candidate, or None if absent."""
    raw = project.get(REPLAN_CANDIDATE_KEY)
    return raw if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None


def candidate_episodes(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Candidate episodes in source order."""
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
    """Candidate continuation start: last candidate episode source end, or replanning start if no candidates exist."""
    episodes = candidate_episodes(candidate)
    if episodes:
        span = parse_source_range(episodes[-1])
        if span is None:
            raise ReplanError("candidate_invalid", "候选集的原文范围形状异常")
        return span.end_file, span.end
    return candidate_start(candidate)


def ledger_layout_revision(project: Mapping[str, Any]) -> str:
    """Fingerprint of ledger split structure: episode IDs, broadcast order, source origins and ranges.

    Exclude titles, hooks and planning statuses: changes do not affect correspondence between candidates and existing episodes.
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
    """Candidate staleness: ``ledger_changed`` for changed ledger split structures, ``source_changed`` for changed whole sources, otherwise None."""
    if candidate.get("ledger_revision") != ledger_layout_revision(project):
        return "ledger_changed"
    if candidate.get("source_fingerprints") != compute_source_fingerprints(docs):
        return "source_changed"
    return None


# ---------------------------------------------------------------------------
# Initiation and discard.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplanScope:
    """Replanning scope from an episode onward."""

    episode: int
    #: Replanning start: file and in-file offset.
    source_file: str
    offset: int
    #: Replan from whole-source beginning (legacy split-flow episodes or replaced sources).
    from_beginning: bool
    #: Source fingerprints differ from ledger records: existing episode ranges no longer align with current sources.
    source_replaced: bool
    #: Cut episodes to replace, in broadcast order.
    replaced: list[int] = field(default_factory=list)
    #: Among them, production already started (artifacts present).
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
    """Cut episodes from the start onward: placed after the start, plus unplaceable episodes following the initiating episode in broadcast order."""
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
    """Replanning scope from ``episode``; raise :class:`ReplanError` when replanning cannot start there."""
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
    """Register empty candidates and return candidate IDs; window generation appends episodes. Reject existing candidates or invalid initiating episodes."""
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
    """Discard candidates, leaving episode ledgers entirely unchanged."""
    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)

    def _commit(p: dict[str, Any]) -> None:
        _require_candidate(p, candidate_id)
        p.pop(REPLAN_CANDIDATE_KEY, None)

    pm.update_project(project_dir.name, _commit)
    logger.info("已放弃重新规划的候选：项目 %s，候选 %s", project_dir.name, candidate_id)


def record_replan_interruption(project_path: str | Path, candidate_id: str, reason: str) -> None:
    """Record why candidate generation stopped midway; do nothing if candidates no longer exist (adopted or discarded)."""
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
    """Continue interrupted candidates: clear interruption reasons and return original additional instructions.

    Reject candidates already covering whole-source ends (``candidate_complete``) or stale (``ledger_changed`` / ``source_changed``).
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
# Simulating adoption.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Adoption:
    """Ledger changes for one adoption, derived from candidates and current ledgers."""

    #: Replaced cut episodes in broadcast order.
    replaced: list[int]
    retired: list[int]
    removed: list[int]
    #: Production started, without an exactly matching source-range episode in candidates.
    needs_review: list[int]
    #: Replaced episodes beyond candidate source coverage (generation stopped before whole-source ends).
    uncovered: list[int]
    #: Other episodes with changed broadcast positions: (episode ID, old position, new position).
    moved: list[tuple[int, int, int]]
    #: Postadoption broadcast order: ``("old", 集 ID)`` or ``("new", 候选集下标)``; retired episodes at the end.
    order: list[tuple[str, int]]


def _source_position(order: Mapping[str, int], source_range: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """Map candidate episode source ranges to (file index, start, end); planning windows are per file, so candidates never span files."""
    rel, start, end = source_range.get("source_file"), source_range.get("start"), source_range.get("end")
    if not isinstance(rel, str) or rel not in order or not isinstance(start, int) or not isinstance(end, int):
        return None
    if source_range.get("end_file", rel) != rel:
        return None
    return order[rel], start, end


def _candidate_positions(candidate: Mapping[str, Any], docs: list[SourceDoc]) -> list[tuple[int, int, int]]:
    """Candidate source positions: (file index, start, end)."""
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

    # Place other-origin episodes by anchors; entries before the first replaced episode remain in place.
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
    """Change inventories for adopting candidates. ``revision`` fingerprints this inventory and is returned on confirmation."""

    candidate: str
    #: Episode initiating replanning.
    episode: int
    #: Replaced cut-episode and candidate-episode counts.
    old_count: int
    new_count: int
    #: Production started; become sourceless/stale and move to broadcast-order end.
    retired: list[int]
    #: No artifacts; remove directly.
    removed: list[int]
    #: Production started and source ranges changed.
    needs_review: list[int]
    #: Episodes beyond new-plan source coverage, also treated as replaced (new plans stopped before whole-source ends).
    uncovered: list[int]
    #: Other episodes with changed broadcast positions: (episode ID, old position, new position).
    moved: list[tuple[int, int, int]]
    #: Losses for retired episodes when selecting Delete together: episode ID → single-episode deletion loss inventory.
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
    """Awaiting confirmation; returning this object entails no writes."""

    impact: ReplanAdoptionImpact


@dataclass(frozen=True)
class ReplanAdoptionResult:
    impact: ReplanAdoptionImpact
    #: New IDs allocated to candidates, in source order.
    episodes: list[int]
    #: Episodes deleted after selecting Delete together.
    deleted: list[int] = field(default_factory=list)


class _StaleConfirmation(Exception):
    def __init__(self, impact: ReplanAdoptionImpact):
        super().__init__("replan adoption needs confirmation")
        self.impact = impact


# ---------------------------------------------------------------------------
# Adoption.
# ---------------------------------------------------------------------------


def _write_new_episode_file(project_dir: Path, episode: int, text: str) -> None:
    path = episode_source_path(project_dir, episode)
    if path.exists() or path.is_symlink():
        # Same-name files for new IDs are absent from ledgers and not any episode's source; archive by renaming first.
        path.rename(archive_episode_file_path(path))
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _apply_adoption(
    project_dir: Path,
    project: dict[str, Any],
    candidate: Mapping[str, Any],
    adoption: _Adoption,
    *,
    delete_retired: bool = False,
) -> list[int]:
    """Apply simulations to ``project``, persisting episode files and snapshots; return new candidate episode IDs.

    With ``delete_retired``, retired episodes keep episode files and become standalone-source episodes: subsequent deletion records those sources, which remain their sources after restoration.
    """
    docs = discover_sources(project_dir, project)
    texts = {doc.rel_path: doc.text for doc in docs}
    entries = _entries(project)
    by_id = {parse_positive_episode_num(entry.get("episode")): entry for entry in entries}
    # Legacy split-flow episodes lack ranges; episode files are their only source records and cannot regenerate from ledgers.
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
        # Mark new IDs with existing disk artifacts (manual remnants beyond historical maximum IDs) stale; never delete artifacts.
        if has_downstream_products(project_dir, num, entry):
            script_review.mark_ledger_stale(project_dir, project, entry, num)
        new_entries.append(entry)
    for num in adoption.retired:
        entry = by_id[num]
        kind = entry_source_kind(project, entry)
        entry[SOURCE_ORIGIN_FIELD] = (SourceOrigin.OWN if delete_retired else SourceOrigin.NONE).value
        if delete_retired:
            record_episode_kind(project, entry, kind)
        entry.pop("source_range", None)
        script_review.mark_ledger_stale(project_dir, project, entry, num)
    project["episodes"] = [by_id[num] if kind == "old" else new_entries[num] for kind, num in adoption.order]
    project[SOURCE_FINGERPRINTS_KEY] = dict(candidate.get("source_fingerprints") or {})
    project.pop(REPLAN_CANDIDATE_KEY, None)

    for num in adoption.replaced:
        path = episode_source_path(project_dir, num)
        if delete_retired and num in adoption.retired:
            continue
        if num in legacy and path.is_file() and not path.is_symlink():
            path.rename(archive_episode_file_path(path))
        else:
            # hard-delete: derived -- derived episode sources replaced by replanning adoption, regenerable from episode ledgers
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
    """Adopt candidates. Missing ``revision`` or mismatch with current change-inventory fingerprints returns inventories only, without writes.

    When ``delete_retired`` is True, move retired episodes individually to project trash using single-episode deletion semantics after adoption commits;
    failed deletions retain standalone-source episodes.
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
        committed["episodes"] = _apply_adoption(
            project_dir, p, locked_candidate, locked_adoption, delete_retired=delete_retired
        )

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
# Summaries and confirmation text.
# ---------------------------------------------------------------------------


def replan_candidate_summary(project_dir: Path, project: Mapping[str, Any]) -> dict[str, Any] | None:
    """New episode-plan summary and per-episode changes; None without candidates.

    Non-None ``stale`` means outdated candidates: show candidate contents only, without simulating adoption.
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
    """Render change inventories as confirmation text: ``text`` describes adoption consequences; ``delete_text`` describes losses when selecting Delete together.

    Refer to episodes by titles or preadoption broadcast positions. Web confirmation dialogs only display these two texts.
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
