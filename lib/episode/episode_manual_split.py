"""手工切分：创作者在整本源文上直接划定集的边界，直接写入分集账本，不经候选（ADR 0032）。

五个命令共用同一套落位与提交纪律：

- **切分**（:func:`cut_unsplit_source`）：在未切分的原文上落点，从这段未切分原文的开头切到落点，成为新的一集，
  按源文位置排在前面最近的切出集之后（前面没有切出集时排在后面最近的切出集之前）。
- **拆分**（:func:`split_episode`）：在切出集内落点，前一段保留集 ID，后一段分配新集 ID，紧接在前一段之后。
- **移动分界**（:func:`move_episode_boundary`）：移动一集与紧接其后的切出集之间的分界，两侧保留集 ID。
- **与下一集合并**（:func:`merge_with_next_episode`）：前一集保留集 ID 并延伸到下一集的结尾；下一集按被替换的
  旧集处理，夹在两集之间的其他集原位不动，落在合并后的集之后。两集之间未切分的原文一并并入，并入的体量
  （阅读单位，与「分集」视图同一口径）大于 0 时先返回确认，创作者确认后带上确认过的体量重新调用，体量变了时退回确认。
- **清除之后的切分**（:func:`clear_cuts_after`）：按源文位置排在这一集之后的切出集全部按被替换的旧集处理。

被替换的旧集有产物的转为无原文的集、标 stale，产物仍归它，按原相对顺序移到播出顺序末尾；没有产物的直接移除。
原文范围变了且有产物的集标 stale。波及有产物的集时先返回 :class:`ManualSplitConfirmationRequired`，
创作者确认后带上确认过的集 ID 重新调用；锁内复核出确认清单之外的有产物集时同样退回确认。

落点是「文件 + 文件内偏移」，偏移落在 ``normalize_source_text`` 的坐标系内。一集的原文范围可以沿整本源文的文件
顺序跨文件，但不新跨过源文件类型的切换处（ADR 0097）。改动原文范围前先核对范围经过的文件的源文指纹：文件在服务之外
被改动过时拒绝，账本更新前不能在它上面切分；清除之后的切分不改原文范围，不受限。
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
    """手工切分被拒；``code`` 是稳定的原因码，入口据此映射状态码与文案。账本不被改动。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManualSplitImpact:
    """一次手工切分波及的集（集 ID，按播出顺序）。"""

    #: 原文范围变了且有产物，标 stale。
    restaled: list[int] = field(default_factory=list)
    #: 被替换下来、有产物，转为无原文的集并标 stale，移到播出顺序末尾。
    retired: list[int] = field(default_factory=list)
    #: 被替换下来、没有产物，直接移除。
    removed: list[int] = field(default_factory=list)
    #: 与下一集合并时并入的两集之间未切分原文的体量（阅读单位）；其余命令为 0。
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
    """波及有产物的集，等待确认；或者是预览。返回本对象时没有发生任何写入。"""

    impact: ManualSplitImpact


@dataclass(frozen=True)
class ManualSplitResult:
    """执行结果。``episode`` 是切分或拆分分配的新集 ID，其余命令为 None。"""

    impact: ManualSplitImpact
    episode: int | None = None


ManualSplitOutcome = ManualSplitResult | ManualSplitConfirmationRequired


@dataclass
class _Edit:
    """一次手工切分对账本的改动，由落位推出，提交时照此改写。偏移是整本源文的全局位置（见 :class:`_Layout`）。"""

    #: 改动原文范围（有新集或范围变了）时为 True；只移除切分时为 False。
    touches_source: bool = False
    #: 已有切出集的新原文范围。
    ranges: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: 新的一集：原文范围、标题，以及插在第几个条目之前（下标指改动前的 ``episodes``）。
    new_range: tuple[int, int] | None = None
    new_title: str = ""
    insert_before: int = 0
    #: 被替换下来的旧切出集，按播出顺序。
    dropped: list[int] = field(default_factory=list)
    #: 并入的未切分原文的体量（阅读单位）。
    merged_units: int = 0


class _NeedsConfirmation(Exception):
    def __init__(self, impact: ManualSplitImpact):
        super().__init__("manual split needs confirmation")
        self.impact = impact


# ---------------------------------------------------------------------------
# 落位
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Layout:
    """整本源文与落位。全局位置是把整本源文的文件按顺序接起来之后的字符下标。"""

    docs: list[SourceDoc]
    entries: list[dict[str, Any]]
    placements: dict[int, CutPlacement]
    language: str | None = None
    #: 源文件类型切换处的全局位置：一集的原文范围不跨过它。
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
        """全局范围 ``[start, end)`` 的阅读单位数，逐文件计数：词不会跨文件边界连成一个。"""
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
        """全局位置作为起点时的「文件 + 文件内偏移」：落在文件交界上时取后一个文件的开头。"""
        bases = self.bases
        for index, doc in enumerate(self.docs):
            if bases[index] <= position < bases[index + 1]:
                return doc.rel_path, position - bases[index]
        return self.docs[-1].rel_path, len(self.docs[-1].text)

    def end_point(self, position: int) -> tuple[str, int]:
        """全局位置作为终点时的「文件 + 文件内偏移」：落在文件交界上时取前一个文件的末尾。"""
        bases = self.bases
        for index, doc in enumerate(self.docs):
            if bases[index] < position <= bases[index + 1]:
                return doc.rel_path, position - bases[index]
        return self.docs[0].rel_path, 0

    def span(self, placement: CutPlacement) -> tuple[int, int]:
        bases = self.bases
        return bases[placement.file_index] + placement.start, bases[placement.end_file_index] + placement.end

    def files_between(self, start: int, end: int) -> list[SourceDoc]:
        """全局范围 ``[start, end)`` 经过的文件。"""
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
        """落位的切出集，按源文位置。"""
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
    """改动后的原文范围不能新跨过源文件类型的切换处。"""
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
    # 从前面最近的切出集结尾（没有时从整本源文开头）切起，不跨过源文件类型的切换处
    start = max([high for _low, high in spans if high <= at] + [wall for wall in layout.kind_walls if wall < at] + [0])
    _require_text(layout, start, at)
    insert_before = cut_insert_index(layout.entries, layout.placements, (index, end))
    return _Edit(touches_source=True, new_range=(start, at), new_title=title.strip(), insert_before=insert_before)


def _point(layout: _Layout, placement: CutPlacement, at: int, source_file: str | None) -> int:
    """手工切分落点的全局位置；没给文件时按这一集起点所在的文件算。"""
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
# 提交
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
    """改动的原文范围经过的文件。"""
    spans = [*edit.ranges.values(), *([edit.new_range] if edit.new_range is not None else [])]
    return [doc for doc in layout.docs if any(doc in layout.files_between(*span) for span in spans)]


def _check_fingerprints(project_dir: Path, project: dict[str, Any], docs: list[SourceDoc]) -> None:
    """原文范围绑定这些文件的当前文本：文件在服务之外被改动过时拒绝，没记录指纹时补记。"""
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
        # 新集 ID 的同名文件不在账本里，不是任何一集的原文，先改名留底
        path.rename(archive_episode_file_path(path))
    path.write_text(text, encoding="utf-8", newline="\n")


def _remove_derived(project_dir: Path, episode: int) -> None:
    episode_source_path(project_dir, episode).unlink(missing_ok=True)


def _range_value(layout: _Layout, start: int, end: int) -> dict[str, Any]:
    start_file, start_offset = layout.start_point(start)
    end_file, end_offset = layout.end_point(end)
    return source_range_value(start_file, start_offset, end_file, end_offset)


def _apply(
    project_dir: Path, project: dict[str, Any], layout: _Layout, edit: _Edit, impact: ManualSplitImpact
) -> int | None:
    """按改动改写 ``project`` 并落盘集文件与快照，返回新集 ID。"""
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

    # 锁外预演只为确认与快速失败：拒绝或需要确认时零写入返回
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
        # 锁内按最新账本重新推演：确认清单与落位都是锁外读取时刻的快照
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
    """在未切分的原文上切分：``source_file`` 里从这段未切分原文的开头到 ``end`` 成为新的一集。"""
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
    """在切出集内的 ``source_file`` 第 ``at`` 处拆分：前一段保留集 ID，后一段是新的一集，紧接在前一段之后。

    ``source_file`` 缺省时按这一集起点所在的文件算。
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
    """把这一集与紧接其后的切出集之间的分界移到 ``source_file`` 第 ``at`` 处，两侧保留集 ID。

    ``source_file`` 缺省时按这一集终点所在的文件算。
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
    """把这一集与按源文位置紧接其后的切出集合并：这一集保留集 ID，下一集按被替换的旧集处理。

    两集之间未切分的原文一并并入；``confirm_merged_units`` 是创作者在确认清单里看过的并入体量。
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
    """清除按源文位置排在这一集之后的全部切分，这些集按被替换的旧集处理。"""
    return _run(
        project_path,
        lambda layout: _plan_clear_after(layout, episode=episode),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# 确认清单
# ---------------------------------------------------------------------------

_IMPACT_LINES = (
    ("restaled", "manual_split_impact_restaled"),
    ("retired", "manual_split_impact_retired"),
    ("removed", "manual_split_impact_removed"),
)


def render_manual_split_impact_text(
    impact: Mapping[str, Any], project: Mapping[str, Any], translate: Callable[..., str]
) -> str:
    """把波及清单渲染成确认文本：集以标题或播出位置指称。Web 确认框只呈现这份文本。"""

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
