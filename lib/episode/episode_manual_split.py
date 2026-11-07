"""Manual splitting: creators directly mark episode boundaries in whole sources and write episode ledgers without candidates (ADR 0032).

Five commands share placement and commit discipline:

- **Cut** (:func:`cut_unsplit_source`): mark an unsplit source point, making a new episode from that unsplit segment's beginning
  to the point. Place it after the nearest preceding split episode by source position (before the nearest following split if none precedes).
- **Split** (:func:`split_episode`): mark a point within a split episode; the first portion retains its ID, the second receives a new ID immediately after it.
- **Move boundary** (:func:`move_episode_boundary`): move the boundary between an episode and the immediately following split episode, preserving both IDs.
- **Merge with next** (:func:`merge_with_next_episode`): retain the first episode's ID and extend it through the next episode's end; treat the next as a replaced
  old episode. Other episodes between them remain in place, following the merged episode. Include unsplit sources between the two; when the included volume
  (reading units, matching the Episodes view) exceeds 0, return confirmation first. Creators resubmit with the confirmed volume; changed volume requires confirmation again.
- **Clear later cuts** (:func:`clear_cuts_after`): treat all split episodes after this one by source position as replaced old episodes.

Replaced old episodes with artifacts become sourceless/stale; they retain their artifacts and move to broadcast-order end in prior relative order. Remove those without artifacts.
Mark episodes stale when source ranges change and artifacts exist. If artifact-bearing episodes are affected, first return :class:`ManualSplitConfirmationRequired`;
creators resubmit confirmed episode IDs. Locked rechecks finding additional artifact-bearing episodes outside the confirmed list also return confirmation.

Points are file + in-file offset, in ``normalize_source_text`` coordinates. Episode source ranges may cross files along whole-source file
order, but must not newly cross source-kind boundaries (ADR 0097). Before range changes, check fingerprints of traversed files: reject files modified outside the service,
where splitting cannot proceed until ledger updates. Clearing later cuts does not change ranges and is exempt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.episode.episode_ids import (
    allocate_episode_ids,
    episode_display_name,
    episode_id_high_water,
)
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    compute_source_fingerprints,
    episodes_with_products,
    has_downstream_products,
    parse_positive_episode_num,
    source_range_value,
    well_formed_ledger_entries,
)
from lib.episode.episode_paths import episode_script_relpath, episode_source_path
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    CutPlacement,
    SourceOrigin,
    archive_episode_file_path,
    changed_outside_service,
    cut_episode_placements,
    cut_insert_index,
    discover_sources,
    source_snapshot_path,
    sync_source_snapshots,
    whole_source_files,
)
from lib.episode.source_kinds import whole_source_file_kind
from lib.infra.text_metrics import count_reading_units, reading_unit_noun
from lib.project.project_manager import ProjectManager
from lib.script import script_review

logger = logging.getLogger(__name__)


class ManualSplitError(ValueError):
    """Manual split refused; ``code`` is a stable reason code mapped to statuses/copy by entry points. Ledgers remain unchanged."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManualSplitImpact:
    """Episodes affected by a manual split (episode IDs in broadcast order)."""

    #: Source ranges changed with artifacts present; mark stale.
    restaled: list[int] = field(default_factory=list)
    #: Replaced, with artifacts: become sourceless/stale and move to broadcast-order end.
    retired: list[int] = field(default_factory=list)
    #: Replaced, without artifacts: remove directly.
    removed: list[int] = field(default_factory=list)
    #: Unsplit source volume included between episodes when merging with next (reading units); 0 for other commands.
    merged_units: int = 0

    @property
    def episodes_with_products(self) -> list[int]:
        return [*self.restaled, *self.retired]

    def to_dict(self) -> dict[str, Any]:
        return {
            "restaled": list(self.restaled),
            "retired": list(self.retired),
            "removed": list(self.removed),
            "merged_units": self.merged_units,
        }


@dataclass(frozen=True)
class ManualSplitConfirmationRequired:
    """Artifact-bearing episodes affected, awaiting confirmation; or a preview. Returning this object entails no writes."""

    impact: ManualSplitImpact


@dataclass(frozen=True)
class ManualSplitResult:
    """Execution result. ``episode`` is the new ID allocated by cut or split, None for other commands."""

    impact: ManualSplitImpact
    episode: int | None = None


ManualSplitOutcome = ManualSplitResult | ManualSplitConfirmationRequired


@dataclass
class _Edit:
    """Ledger changes for a manual split, derived from placement and applied on commit. Offsets are whole-source global positions (see :class:`_Layout`)."""

    #: True for changed source ranges (new episodes or range changes); False when only removing cuts.
    touches_source: bool = False
    #: New source ranges for existing split episodes.
    ranges: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: New episode: source range, title and insertion index (relative to prechange ``episodes``).
    new_range: tuple[int, int] | None = None
    new_title: str = ""
    insert_before: int = 0
    #: Replaced old split episodes in broadcast order.
    dropped: list[int] = field(default_factory=list)
    #: Included unsplit source volume (reading units).
    merged_units: int = 0


class _NeedsConfirmation(Exception):
    def __init__(self, impact: ManualSplitImpact):
        super().__init__("manual split needs confirmation")
        self.impact = impact


# ---------------------------------------------------------------------------
# Placement.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Layout:
    """Whole sources and placement. Global positions are character indices in whole-source files concatenated in order."""

    docs: list[SourceDoc]
    entries: list[dict[str, Any]]
    placements: dict[int, CutPlacement]
    language: str | None = None
    #: Global source-kind boundary positions: episode source ranges cannot cross them.
    kind_walls: tuple[int, ...] = ()

    @property
    def bases(self) -> list[int]:
        bases = [0]
        for doc in self.docs:
            bases.append(bases[-1] + len(doc.text))
        return bases

    @property
    def text(self) -> str:
        return "".join(doc.text for doc in self.docs)

    def units_between(self, start: int, end: int) -> int:
        """Reading units in global range ``[start, end)``, counted per file: words never join across file boundaries."""
        bases = self.bases
        total = 0
        for index, doc in enumerate(self.docs):
            lo, hi = max(start, bases[index]), min(end, bases[index + 1])
            if lo < hi:
                total += count_reading_units(doc.text[lo - bases[index] : hi - bases[index]], self.language)
        return total

    def doc_index(self, source_file: str) -> int:
        index = next((i for i, doc in enumerate(self.docs) if doc.rel_path == source_file), None)
        if index is None:
            raise ManualSplitError("source_file_not_found", f"整本源文里没有这个可读的文件：{source_file}")
        return index

    def to_global(self, source_file: str, offset: int) -> int:
        index = self.doc_index(source_file)
        if not 0 <= offset <= len(self.docs[index].text):
            raise ManualSplitError("position_invalid", f"位置 {offset} 超出文件范围")
        return self.bases[index] + offset

    def start_point(self, position: int) -> tuple[str, int]:
        """File + in-file offset for global start positions: at file boundaries, take the following file's beginning."""
        bases = self.bases
        for index, doc in enumerate(self.docs):
            if bases[index] <= position < bases[index + 1]:
                return doc.rel_path, position - bases[index]
        return self.docs[-1].rel_path, len(self.docs[-1].text)

    def end_point(self, position: int) -> tuple[str, int]:
        """File + in-file offset for global end positions: at file boundaries, take the preceding file's end."""
        bases = self.bases
        for index, doc in enumerate(self.docs):
            if bases[index] < position <= bases[index + 1]:
                return doc.rel_path, position - bases[index]
        return self.docs[0].rel_path, 0

    def span(self, placement: CutPlacement) -> tuple[int, int]:
        bases = self.bases
        return bases[placement.file_index] + placement.start, bases[placement.end_file_index] + placement.end

    def files_between(self, start: int, end: int) -> list[SourceDoc]:
        """Files traversed by global range ``[start, end)``."""
        bases = self.bases
        return [doc for index, doc in enumerate(self.docs) if bases[index] < end and bases[index + 1] > start]

    def walls_inside(self, start: int, end: int) -> set[int]:
        return {wall for wall in self.kind_walls if start < wall < end}

    def placement(self, episode: int) -> CutPlacement:
        if all(parse_positive_episode_num(entry.get("episode")) != episode for entry in self.entries):
            raise ManualSplitError("episode_not_found", f"集（id={episode}）不在账本中")
        placement = self.placements.get(episode)
        if placement is None:
            raise ManualSplitError("episode_not_placed", f"集（id={episode}）的原文不在整本源文里，不能调整它的边界")
        return placement

    def ordered(self) -> list[CutPlacement]:
        """Placed split episodes, ordered by source position."""
        return sorted(self.placements.values(), key=lambda p: p.position)

    def entry_index(self, episode: int) -> int:
        return next(
            i for i, entry in enumerate(self.entries) if parse_positive_episode_num(entry.get("episode")) == episode
        )


def _layout(project_dir: Path, project: Mapping[str, Any]) -> _Layout:
    entries = well_formed_ledger_entries(project)
    if entries is None:
        raise ManualSplitError("ledger_invalid", "分集账本的形状异常，不能手工切分")
    docs = discover_sources(project_dir, project)
    kinds = [whole_source_file_kind(project, doc.rel_path) for doc in docs]
    walls: list[int] = []
    offset = 0
    for index, doc in enumerate(docs):
        if index > 0 and kinds[index] != kinds[index - 1]:
            walls.append(offset)
        offset += len(doc.text)
    return _Layout(
        docs=docs,
        entries=entries,
        placements=cut_episode_placements(project, docs),
        language=_language(project),
        kind_walls=tuple(walls),
    )


def _language(project: Mapping[str, Any]) -> str | None:
    raw = project.get("source_language")
    return raw if isinstance(raw, str) else None


def _require_text(layout: _Layout, start: int, end: int) -> None:
    if not layout.text[start:end].strip():
        raise ManualSplitError("empty_range", "分出的集没有正文")


def _require_inside(at: int, low: int, high: int) -> None:
    if not low < at < high:
        raise ManualSplitError("position_invalid", f"分集点 {at} 不在 ({low}, {high}) 之内")


def _require_same_kind(layout: _Layout, new: tuple[int, int], *old: tuple[int, int]) -> None:
    """Changed source ranges must not newly cross source-kind boundaries."""
    allowed = set().union(*(layout.walls_inside(*span) for span in old))
    if layout.walls_inside(*new) - allowed:
        raise ManualSplitError("crosses_source_kind", "一集的原文不能跨过源文件类型不同的两个文件")


def _plan_cut(layout: _Layout, *, source_file: str, end: int, title: str) -> _Edit:
    index = layout.doc_index(source_file)
    if not 0 < end <= len(layout.docs[index].text):
        raise ManualSplitError("position_invalid", f"分集点 {end} 超出文件范围")
    at = layout.to_global(source_file, end)
    spans = [layout.span(p) for p in layout.ordered()]
    if any(low < at < high for low, high in spans):
        raise ManualSplitError("inside_episode", "这个位置在一集的原文里，应当拆分这一集")
    # Cut from the nearest preceding split episode's end (whole-source beginning if none), without crossing source-kind boundaries.
    start = max([high for _low, high in spans if high <= at] + [wall for wall in layout.kind_walls if wall < at] + [0])
    _require_text(layout, start, at)
    insert_before = cut_insert_index(layout.entries, layout.placements, (index, end))
    return _Edit(touches_source=True, new_range=(start, at), new_title=title.strip(), insert_before=insert_before)


def _point(layout: _Layout, placement: CutPlacement, at: int, source_file: str | None) -> int:
    """Global manual-split point; absent files default to the file containing this episode's start."""
    return layout.to_global(source_file or layout.docs[placement.file_index].rel_path, at)


def _plan_split(layout: _Layout, *, episode: int, at: int, source_file: str | None) -> _Edit:
    placement = layout.placement(episode)
    low, high = layout.span(placement)
    point = _point(layout, placement, at, source_file)
    _require_inside(point, low, high)
    _require_text(layout, low, point)
    _require_text(layout, point, high)
    return _Edit(
        touches_source=True,
        ranges={episode: (low, point)},
        new_range=(point, high),
        insert_before=layout.entry_index(episode) + 1,
    )


def _plan_move(layout: _Layout, *, episode: int, at: int, source_file: str | None) -> _Edit:
    left = layout.placement(episode)
    left_span = layout.span(left)
    right = next(
        (p for p in layout.ordered() if (span := layout.span(p))[0] == left_span[1] and span[1] > span[0]),
        None,
    )
    if right is None:
        raise ManualSplitError("no_adjacent_episode", f"集（id={episode}）与之后的切出集之间没有相连的分界")
    right_span = layout.span(right)
    point = layout.to_global(source_file or layout.docs[left.end_file_index].rel_path, at)
    _require_inside(point, left_span[0], right_span[1])
    if point == left_span[1]:
        raise ManualSplitError("position_invalid", "分界没有移动")
    _require_text(layout, left_span[0], point)
    _require_text(layout, point, right_span[1])
    _require_same_kind(layout, (left_span[0], point), left_span)
    _require_same_kind(layout, (point, right_span[1]), right_span)
    return _Edit(
        touches_source=True,
        ranges={left.episode: (left_span[0], point), right.episode: (point, right_span[1])},
    )


def _plan_merge(layout: _Layout, *, episode: int) -> _Edit:
    placement = layout.placement(episode)
    ordered = layout.ordered()
    following = ordered[ordered.index(placement) + 1 :]
    if not following:
        raise ManualSplitError("no_next_episode", f"集（id={episode}）之后没有切出集")
    nxt = following[0]
    own, nxt_span = layout.span(placement), layout.span(nxt)
    merged = (own[0], nxt_span[1])
    if layout.walls_inside(*merged) - layout.walls_inside(*own) - layout.walls_inside(*nxt_span):
        raise ManualSplitError("merge_across_kinds", f"集（id={episode}）的下一集在源文件类型不同的文件里")
    return _Edit(
        touches_source=True,
        ranges={episode: merged},
        dropped=[nxt.episode],
        merged_units=layout.units_between(own[1], nxt_span[0]),
    )


def _plan_clear_after(layout: _Layout, *, episode: int) -> _Edit:
    placement = layout.placement(episode)
    after = {p.episode for p in layout.ordered() if p.position > placement.position}
    if not after:
        raise ManualSplitError("nothing_after", f"集（id={episode}）之后没有切出集")
    return _Edit(
        dropped=[num for entry in layout.entries if (num := parse_positive_episode_num(entry.get("episode"))) in after]
    )


# ---------------------------------------------------------------------------
# Commit.
# ---------------------------------------------------------------------------


def _impact(project_dir: Path, layout: _Layout, edit: _Edit) -> ManualSplitImpact:
    restaled: list[int] = []
    retired: list[int] = []
    removed: list[int] = []
    with_products = episodes_with_products(project_dir, layout.entries)
    for entry in layout.entries:
        episode = parse_positive_episode_num(entry.get("episode"))
        if episode is None:
            continue
        if episode in edit.dropped:
            (retired if episode in with_products else removed).append(episode)
        elif episode in edit.ranges:
            changed = edit.ranges[episode] != layout.span(layout.placements[episode])
            if changed and episode in with_products:
                restaled.append(episode)
    return ManualSplitImpact(restaled=restaled, retired=retired, removed=removed, merged_units=edit.merged_units)


def _touched_docs(layout: _Layout, edit: _Edit) -> list[SourceDoc]:
    """Files traversed by changed source ranges."""
    spans = [*edit.ranges.values(), *([edit.new_range] if edit.new_range is not None else [])]
    return [doc for doc in layout.docs if any(doc in layout.files_between(*span) for span in spans)]


def _check_fingerprints(project_dir: Path, project: dict[str, Any], docs: list[SourceDoc]) -> None:
    """Bind source ranges to current file texts: reject externally modified files; record fingerprints if absent."""
    raw = project.get(SOURCE_FINGERPRINTS_KEY)
    recorded = dict(raw) if isinstance(raw, Mapping) else {}
    for doc in docs:
        if changed_outside_service(project_dir, project, doc):
            raise ManualSplitError("source_changed", f"源文件在服务之外被改动过：{doc.rel_path}")
        recorded[doc.rel_path] = compute_source_fingerprints([doc])[doc.rel_path]
    if docs:
        project[SOURCE_FINGERPRINTS_KEY] = recorded


def _write_derived(project_dir: Path, episode: int, text: str, *, fresh: bool) -> None:
    path = episode_source_path(project_dir, episode)
    if path.is_symlink() and not fresh:
        raise ManualSplitError("episode_source_symlink", f"集（id={episode}）的集文件是符号链接，拒绝写入")
    if fresh and (path.exists() or path.is_symlink()):
        # Same-name files for new episode IDs are absent from ledgers and not any episode's source; archive by renaming first.
        path.rename(archive_episode_file_path(path))
    path.write_text(text, encoding="utf-8", newline="\n")


def _remove_derived(project_dir: Path, episode: int) -> None:
    # hard-delete: derived -- episode sources discarded by manual splitting, regenerable from episode ledgers
    episode_source_path(project_dir, episode).unlink(missing_ok=True)


def _range_value(layout: _Layout, start: int, end: int) -> dict[str, Any]:
    start_file, start_offset = layout.start_point(start)
    end_file, end_offset = layout.end_point(end)
    return source_range_value(start_file, start_offset, end_file, end_offset)


def _apply(
    project_dir: Path, project: dict[str, Any], layout: _Layout, edit: _Edit, impact: ManualSplitImpact
) -> int | None:
    """Apply changes to ``project``, persisting episode files and snapshots; return new episode IDs."""
    if edit.touches_source:
        _check_fingerprints(project_dir, project, _touched_docs(layout, edit))
    by_id = {parse_positive_episode_num(entry.get("episode")): entry for entry in layout.entries}
    for episode, (start, end) in edit.ranges.items():
        entry = by_id[episode]
        entry["source_range"] = _range_value(layout, start, end)
        if episode in impact.restaled:
            script_review.mark_ledger_stale(project_dir, project, entry, episode)
    new_episode: int | None = None
    entries = list(layout.entries)
    if edit.new_range is not None:
        (new_episode,) = allocate_episode_ids(project, 1)
        new_entry: dict[str, Any] = {
            "episode": new_episode,
            "title": edit.new_title,
            "script_file": episode_script_relpath(new_episode),
            SOURCE_ORIGIN_FIELD: SourceOrigin.WHOLE_SOURCE.value,
            "source_range": _range_value(layout, *edit.new_range),
            "ledger_status": "planned",
        }
        if has_downstream_products(project_dir, new_episode, new_entry):
            script_review.mark_ledger_stale(project_dir, project, new_entry, new_episode)
        entries.insert(edit.insert_before, new_entry)
    retired_entries: list[dict[str, Any]] = []
    for episode in edit.dropped:
        entry = by_id[episode]
        entries.remove(entry)
        if episode in impact.retired:
            entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.NONE.value
            entry.pop("source_range", None)
            script_review.mark_ledger_stale(project_dir, project, entry, episode)
            retired_entries.append(entry)
    project["episodes"] = [*entries, *retired_entries]

    text = layout.text
    for episode, (start, end) in edit.ranges.items():
        _write_derived(project_dir, episode, text[start:end], fresh=False)
    if new_episode is not None and edit.new_range is not None:
        start, end = edit.new_range
        _write_derived(project_dir, new_episode, text[start:end], fresh=True)
    for episode in edit.dropped:
        _remove_derived(project_dir, episode)
    sync_source_snapshots(project_dir, project, {doc.rel_path: doc.text for doc in layout.docs}, refreshed=())
    return new_episode


def _run(
    project_path: str | Path,
    plan: Callable[[_Layout], _Edit],
    *,
    confirm_episodes: Collection[int],
    dry_run: bool,
    confirm_merged_units: int = 0,
) -> ManualSplitOutcome:
    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)
    project_name = project_dir.name
    confirmed = frozenset(confirm_episodes)

    def _needs_confirmation(impact: ManualSplitImpact) -> bool:
        if impact.merged_units and impact.merged_units != confirm_merged_units:
            return True
        return any(episode not in confirmed for episode in impact.episodes_with_products)

    # Unlocked simulation only confirms/fails fast: return without writes when refused or confirmation required.
    project = pm.load_project(project_name)
    layout = _layout(project_dir, project)
    edit = plan(layout)
    if edit.touches_source:
        _check_fingerprints(project_dir, project, _touched_docs(layout, edit))
    impact = _impact(project_dir, layout, edit)
    if dry_run or _needs_confirmation(impact):
        return ManualSplitConfirmationRequired(impact=impact)

    planned_new_id = episode_id_high_water(project) + 1
    touched = {*edit.ranges, *edit.dropped, planned_new_id}
    formal_paths = [episode_source_path(project_dir, episode) for episode in sorted(touched)]
    formal_paths.extend(source_snapshot_path(project_dir, rel) for rel in whole_source_files(project))
    committed: dict[str, Any] = {}

    def _commit(p: dict[str, Any]) -> None:
        # Recompute against latest ledgers under locks: confirmation lists and placements snapshot the earlier unlocked read.
        locked_layout = _layout(project_dir, p)
        locked_edit = plan(locked_layout)
        locked_impact = _impact(project_dir, locked_layout, locked_edit)
        if _needs_confirmation(locked_impact):
            raise _NeedsConfirmation(locked_impact)
        locked_touched = {*locked_edit.ranges, *locked_edit.dropped}
        if locked_edit.new_range is not None:
            locked_touched.add(episode_id_high_water(p) + 1)
        if not locked_touched <= touched:
            raise ManualSplitError("conflict", "分集账本刚被改动，本次调整没有执行")
        committed["episode"] = _apply(project_dir, p, locked_layout, locked_edit, locked_impact)
        committed["impact"] = locked_impact

    try:
        pm.update_project(project_name, _commit, formal_paths=formal_paths)
    except _NeedsConfirmation as exc:
        return ManualSplitConfirmationRequired(impact=exc.impact)
    result_impact: ManualSplitImpact = committed["impact"]
    logger.info(
        "手工切分已写入账本：项目 %s，标 stale %s，退下 %s，移除 %s",
        project_name,
        result_impact.restaled,
        result_impact.retired,
        result_impact.removed,
    )
    return ManualSplitResult(impact=result_impact, episode=committed["episode"])


def cut_unsplit_source(
    project_path: str | Path,
    *,
    source_file: str,
    end: int,
    title: str = "",
    confirm_episodes: Collection[int] = (),
    dry_run: bool = False,
) -> ManualSplitOutcome:
    """Cut unsplit sources: from this unsplit segment's beginning in ``source_file`` through ``end`` becomes a new episode."""
    return _run(
        project_path,
        lambda layout: _plan_cut(layout, source_file=source_file, end=end, title=title),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
    )


def split_episode(
    project_path: str | Path,
    episode: int,
    *,
    at: int,
    source_file: str | None = None,
    confirm_episodes: Collection[int] = (),
    dry_run: bool = False,
) -> ManualSplitOutcome:
    """Split this split episode at ``at`` in ``source_file``: the first portion retains its ID, the second becomes a new episode immediately after it.

    Absent ``source_file`` defaults to the file containing this episode's start.
    """
    return _run(
        project_path,
        lambda layout: _plan_split(layout, episode=episode, at=at, source_file=source_file),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
    )


def move_episode_boundary(
    project_path: str | Path,
    episode: int,
    *,
    at: int,
    source_file: str | None = None,
    confirm_episodes: Collection[int] = (),
    dry_run: bool = False,
) -> ManualSplitOutcome:
    """Move the boundary between this episode and the immediately following split episode to ``at`` in ``source_file``, retaining both IDs.

    Absent ``source_file`` defaults to the file containing this episode's end.
    """
    return _run(
        project_path,
        lambda layout: _plan_move(layout, episode=episode, at=at, source_file=source_file),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
    )


def merge_with_next_episode(
    project_path: str | Path,
    episode: int,
    *,
    confirm_episodes: Collection[int] = (),
    confirm_merged_units: int = 0,
    dry_run: bool = False,
) -> ManualSplitOutcome:
    """Merge this episode with the immediately following split episode by source position: retain this ID, treating the next as a replaced old episode.

    Include unsplit sources between them; ``confirm_merged_units`` is the included volume creators saw in the confirmation list.
    """
    return _run(
        project_path,
        lambda layout: _plan_merge(layout, episode=episode),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
        confirm_merged_units=confirm_merged_units,
    )


def clear_cuts_after(
    project_path: str | Path,
    episode: int,
    *,
    confirm_episodes: Collection[int] = (),
    dry_run: bool = False,
) -> ManualSplitOutcome:
    """Clear all cuts after this episode by source position, treating those episodes as replaced old episodes."""
    return _run(
        project_path,
        lambda layout: _plan_clear_after(layout, episode=episode),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Confirmation lists.
# ---------------------------------------------------------------------------

_IMPACT_LINES = (
    ("restaled", "manual_split_impact_restaled"),
    ("retired", "manual_split_impact_retired"),
    ("removed", "manual_split_impact_removed"),
)


def render_manual_split_impact_text(
    impact: Mapping[str, Any], project: Mapping[str, Any], translate: Callable[..., str]
) -> str:
    """Render affected inventories as confirmation text, referring to episodes by title or broadcast position. Web confirmation dialogs only display this text."""

    def name(episode: int) -> str:
        return episode_display_name(project, episode, translate)

    separator = translate("manual_split_impact_separator")
    groups = {key: [name(episode) for episode in impact.get(key) or ()] for key, _ in _IMPACT_LINES}
    with_products = len(groups["restaled"]) + len(groups["retired"])
    lines: list[str] = []
    merged = impact.get("merged_units")
    if isinstance(merged, int) and merged > 0:
        unit = "words" if reading_unit_noun(_language(project)) == "词" else "chars"
        lines.append(translate(f"manual_split_impact_merged_{unit}", count=merged))
    if with_products:
        lines.append(translate("manual_split_impact_summary", count=with_products))
    lines.extend(
        translate(line_key, episodes=separator.join(names)) for key, line_key in _IMPACT_LINES if (names := groups[key])
    )
    return "\n".join(lines)


__all__ = [
    "ManualSplitConfirmationRequired",
    "ManualSplitError",
    "ManualSplitImpact",
    "ManualSplitOutcome",
    "ManualSplitResult",
    "clear_cuts_after",
    "cut_unsplit_source",
    "merge_with_next_episode",
    "move_episode_boundary",
    "render_manual_split_impact_text",
    "split_episode",
]
