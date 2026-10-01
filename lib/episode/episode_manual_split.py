"""手工切分：创作者在整本源文上直接划定集的边界，直接写入分集账本，不经候选（ADR 0032）。

五个命令共用同一套落位与提交纪律：

- **切分**（:func:`cut_unsplit_source`）：在未切分的原文上落点，从这段未切分原文的开头切到落点，成为新的一集，
  按源文位置排在前面最近的切出集之后（前面没有切出集时排在后面最近的切出集之前）。
- **拆分**（:func:`split_episode`）：在切出集内落点，前一段保留集 ID，后一段分配新集 ID，紧接在前一段之后。
- **移动分界**（:func:`move_episode_boundary`）：移动一集与紧接其后的切出集之间的分界，两侧保留集 ID。
- **与下一集合并**（:func:`merge_with_next_episode`）：前一集保留集 ID 并延伸到下一集的结尾；下一集按被替换的
  旧集处理，夹在两集之间的其他集原位不动，落在合并后的集之后。
- **清除之后的切分**（:func:`clear_cuts_after`）：按源文位置排在这一集之后的切出集全部按被替换的旧集处理。

被替换的旧集有产物的转为无原文的集、标 stale，产物仍归它，按原相对顺序移到播出顺序末尾；没有产物的直接移除。
原文范围变了且有产物的集标 stale。波及有产物的集时先返回 :class:`ManualSplitConfirmationRequired`，
创作者确认后带上确认过的集 ID 重新调用；锁内复核出确认清单之外的有产物集时同样退回确认。

偏移落在 ``normalize_source_text`` 的坐标系内（文件内的字符下标），一集的原文范围不跨文件。改动原文范围前先核对
源文指纹：文件在服务之外被改动过时拒绝，账本更新前不能在它上面切分；清除之后的切分不改原文范围，不受限。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.episode.episode_ids import allocate_episode_ids, episode_id_high_water, episode_position, episode_title
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    compute_source_fingerprints,
    discover_product_episode_nums,
    has_downstream_products,
    parse_positive_episode_num,
)
from lib.episode.episode_paths import episode_script_relpath, episode_source_path
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    CutPlacement,
    SourceOrigin,
    archive_episode_file_path,
    cut_episode_placements,
    discover_sources,
    is_cut_episode,
    source_snapshot_path,
    sync_source_snapshots,
    whole_source_files,
)
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

    @property
    def episodes_with_products(self) -> list[int]:
        return [*self.restaled, *self.retired]

    def to_dict(self) -> dict[str, list[int]]:
        return {"restaled": list(self.restaled), "retired": list(self.retired), "removed": list(self.removed)}


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
    """一次手工切分对账本的改动，由落位推出，提交时照此改写。"""

    #: 原文范围所在的文件；只移除切分时为 None（不改任何原文范围）。
    source: SourceDoc | None = None
    #: 已有切出集的新原文范围。
    ranges: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: 新的一集：原文范围、标题，以及插在第几个条目之前（下标指改动前的 ``episodes``）。
    new_range: tuple[int, int] | None = None
    new_title: str = ""
    insert_before: int = 0
    #: 被替换下来的旧切出集，按播出顺序。
    dropped: list[int] = field(default_factory=list)


class _NeedsConfirmation(Exception):
    def __init__(self, impact: ManualSplitImpact):
        super().__init__("manual split needs confirmation")
        self.impact = impact


# ---------------------------------------------------------------------------
# 落位
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Layout:
    docs: list[SourceDoc]
    entries: list[dict[str, Any]]
    placements: dict[int, CutPlacement]

    def doc_index(self, source_file: str) -> int:
        index = next((i for i, doc in enumerate(self.docs) if doc.rel_path == source_file), None)
        if index is None:
            raise ManualSplitError("source_file_not_found", f"整本源文里没有这个可读的文件：{source_file}")
        return index

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
    raw = project.get("episodes")
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        raise ManualSplitError("ledger_invalid", "分集账本的形状异常，不能手工切分")
    docs = discover_sources(project_dir, project)
    return _Layout(docs=docs, entries=list(raw), placements=cut_episode_placements(project, docs))


def _require_text(doc: SourceDoc, start: int, end: int) -> None:
    if not doc.text[start:end].strip():
        raise ManualSplitError("empty_range", "分出的集没有正文")


def _require_inside(at: int, low: int, high: int) -> None:
    if not low < at < high:
        raise ManualSplitError("position_invalid", f"分集点 {at} 不在 ({low}, {high}) 之内")


def _plan_cut(layout: _Layout, *, source_file: str, end: int, title: str) -> _Edit:
    index = layout.doc_index(source_file)
    doc = layout.docs[index]
    if not 0 < end <= len(doc.text):
        raise ManualSplitError("position_invalid", f"分集点 {end} 超出文件范围")
    in_file = [p for p in layout.ordered() if p.file_index == index]
    if any(p.start < end < p.end for p in in_file):
        raise ManualSplitError("inside_episode", "这个位置在一集的原文里，应当拆分这一集")
    start = max((p.end for p in in_file if p.end <= end), default=0)
    _require_text(doc, start, end)
    before = [p for p in layout.ordered() if p.position < (index, end)]
    after = [p for p in layout.ordered() if p.position >= (index, end)]
    if before:
        insert_before = layout.entry_index(before[-1].episode) + 1
    elif after:
        insert_before = layout.entry_index(after[0].episode)
    else:
        # 没有落位的切出集时，与分集规划同口径：排在最后一个切出集之后，账本里还没有切出集时排在末尾
        insert_before = next(
            (i + 1 for i in range(len(layout.entries) - 1, -1, -1) if is_cut_episode(layout.entries[i])),
            len(layout.entries),
        )
    return _Edit(source=doc, new_range=(start, end), new_title=title.strip(), insert_before=insert_before)


def _plan_split(layout: _Layout, *, episode: int, at: int) -> _Edit:
    placement = layout.placement(episode)
    doc = layout.docs[placement.file_index]
    _require_inside(at, placement.start, placement.end)
    _require_text(doc, placement.start, at)
    _require_text(doc, at, placement.end)
    return _Edit(
        source=doc,
        ranges={episode: (placement.start, at)},
        new_range=(at, placement.end),
        insert_before=layout.entry_index(episode) + 1,
    )


def _plan_move(layout: _Layout, *, episode: int, at: int) -> _Edit:
    left = layout.placement(episode)
    right = next(
        (p for p in layout.ordered() if p.file_index == left.file_index and p.start == left.end and p.end > p.start),
        None,
    )
    if right is None:
        raise ManualSplitError("no_adjacent_episode", f"集（id={episode}）与之后的切出集之间没有相连的分界")
    doc = layout.docs[left.file_index]
    _require_inside(at, left.start, right.end)
    if at == left.end:
        raise ManualSplitError("position_invalid", "分界没有移动")
    _require_text(doc, left.start, at)
    _require_text(doc, at, right.end)
    return _Edit(source=doc, ranges={left.episode: (left.start, at), right.episode: (at, right.end)})


def _plan_merge(layout: _Layout, *, episode: int) -> _Edit:
    placement = layout.placement(episode)
    ordered = layout.ordered()
    following = ordered[ordered.index(placement) + 1 :]
    if not following:
        raise ManualSplitError("no_next_episode", f"集（id={episode}）之后没有切出集")
    nxt = following[0]
    if nxt.file_index != placement.file_index:
        raise ManualSplitError("merge_across_files", f"集（id={episode}）的下一集在另一个文件里")
    return _Edit(
        source=layout.docs[placement.file_index],
        ranges={episode: (placement.start, nxt.end)},
        dropped=[nxt.episode],
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
    placements = layout.placements
    # 有产物：账本标 consumed，或磁盘上已有剧本 / script_plan（含补零的剧本文件名），与重置同一口径
    product_nums = discover_product_episode_nums(project_dir)

    def _has_products(entry: Mapping[str, Any], episode: int) -> bool:
        return (
            entry.get("ledger_status") == "consumed"
            or episode in product_nums
            or has_downstream_products(project_dir, episode, entry)
        )

    for entry in layout.entries:
        episode = parse_positive_episode_num(entry.get("episode"))
        if episode is None:
            continue
        if episode in edit.dropped:
            (retired if _has_products(entry, episode) else removed).append(episode)
        elif episode in edit.ranges:
            placement = placements[episode]
            changed = edit.ranges[episode] != (placement.start, placement.end)
            if changed and _has_products(entry, episode):
                restaled.append(episode)
    return ManualSplitImpact(restaled=restaled, retired=retired, removed=removed)


def _check_fingerprint(project: dict[str, Any], doc: SourceDoc) -> None:
    """原文范围绑定这个文件的当前文本：已记录的指纹不符时拒绝，没记录时补记。"""
    current = compute_source_fingerprints([doc])[doc.rel_path]
    raw = project.get(SOURCE_FINGERPRINTS_KEY)
    recorded = dict(raw) if isinstance(raw, Mapping) else {}
    previous = recorded.get(doc.rel_path)
    if isinstance(previous, str) and previous != current:
        raise ManualSplitError("source_changed", f"源文件在服务之外被改动过：{doc.rel_path}")
    recorded[doc.rel_path] = current
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


def _apply(
    project_dir: Path, project: dict[str, Any], layout: _Layout, edit: _Edit, impact: ManualSplitImpact
) -> int | None:
    """按改动改写 ``project`` 并落盘集文件与快照，返回新集 ID。"""
    doc = edit.source
    if doc is not None:
        _check_fingerprint(project, doc)
    by_id = {parse_positive_episode_num(entry.get("episode")): entry for entry in layout.entries}
    # 只移除切分时不带文件，也不改任何原文范围
    if doc is not None:
        for episode, (start, end) in edit.ranges.items():
            entry = by_id[episode]
            entry["source_range"] = {"source_file": doc.rel_path, "start": start, "end": end}
            if episode in impact.restaled:
                script_review.mark_ledger_stale(project_dir, project, entry, episode)
    new_episode: int | None = None
    entries = list(layout.entries)
    if edit.new_range is not None and doc is not None:
        (new_episode,) = allocate_episode_ids(project, 1)
        start, end = edit.new_range
        new_entry: dict[str, Any] = {
            "episode": new_episode,
            "title": edit.new_title,
            "script_file": episode_script_relpath(new_episode),
            SOURCE_ORIGIN_FIELD: SourceOrigin.WHOLE_SOURCE.value,
            "source_range": {"source_file": doc.rel_path, "start": start, "end": end},
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

    if doc is not None:
        for episode, (start, end) in edit.ranges.items():
            _write_derived(project_dir, episode, doc.text[start:end], fresh=False)
        if new_episode is not None and edit.new_range is not None:
            start, end = edit.new_range
            _write_derived(project_dir, new_episode, doc.text[start:end], fresh=True)
    for episode in edit.dropped:
        _remove_derived(project_dir, episode)
    sync_source_snapshots(project_dir, project, {doc.rel_path: doc.text} if doc is not None else {})
    return new_episode


def _run(
    project_path: str | Path,
    plan: Callable[[_Layout], _Edit],
    *,
    confirm_episodes: Collection[int],
    dry_run: bool,
) -> ManualSplitOutcome:
    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)
    project_name = project_dir.name
    confirmed = frozenset(confirm_episodes)

    def _needs_confirmation(impact: ManualSplitImpact) -> bool:
        return any(episode not in confirmed for episode in impact.episodes_with_products)

    # 锁外预演只为确认与快速失败：拒绝或需要确认时零写入返回
    project = pm.load_project(project_name)
    layout = _layout(project_dir, project)
    edit = plan(layout)
    if edit.source is not None:
        _check_fingerprint(project, edit.source)
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
    confirm_episodes: Collection[int] = (),
    dry_run: bool = False,
) -> ManualSplitOutcome:
    """在切出集内 ``at`` 处拆分：前一段保留集 ID，后一段是新的一集，紧接在前一段之后。"""
    return _run(
        project_path,
        lambda layout: _plan_split(layout, episode=episode, at=at),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
    )


def move_episode_boundary(
    project_path: str | Path,
    episode: int,
    *,
    at: int,
    confirm_episodes: Collection[int] = (),
    dry_run: bool = False,
) -> ManualSplitOutcome:
    """把这一集与紧接其后的切出集之间的分界移到 ``at``，两侧保留集 ID。"""
    return _run(
        project_path,
        lambda layout: _plan_move(layout, episode=episode, at=at),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
    )


def merge_with_next_episode(
    project_path: str | Path,
    episode: int,
    *,
    confirm_episodes: Collection[int] = (),
    dry_run: bool = False,
) -> ManualSplitOutcome:
    """把这一集与按源文位置紧接其后的切出集合并：这一集保留集 ID，下一集按被替换的旧集处理。"""
    return _run(
        project_path,
        lambda layout: _plan_merge(layout, episode=episode),
        confirm_episodes=confirm_episodes,
        dry_run=dry_run,
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
    impact: Mapping[str, Iterable[int]], project: Mapping[str, Any], translate: Callable[..., str]
) -> str:
    """把波及清单渲染成确认文本：集以标题或播出位置指称。Web 确认框只呈现这份文本。"""

    def name(episode: int) -> str:
        title = episode_title(project, episode)
        if title:
            return title
        position = episode_position(project, episode)
        return translate("episode_position_name", position=position) if position else translate("episode_unlisted_name")

    separator = translate("manual_split_impact_separator")
    groups = {key: [name(episode) for episode in impact.get(key) or ()] for key, _ in _IMPACT_LINES}
    with_products = len(groups["restaled"]) + len(groups["retired"])
    lines = [translate("manual_split_impact_summary", count=with_products)] if with_products else []
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
