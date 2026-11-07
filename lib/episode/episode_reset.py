"""Episode-planning reset: an escape hatch returning ledger episodes cut from whole sources to unplanned state.

Only cut episodes change: standalone-source and sourceless episodes do not occupy whole sources. Both resets preserve their entries, episode files and downstream artifacts
unchanged, including relative order (see ``docs/adr/0031``).

Ledger coordinates bind specific source contents. Replaced sources or corrupted ledgers can permanently fail planning entry with out-of-bounds coordinates / invalid ranges.
Full reset (no episode specified) is the only escape: **zero prevalidation**, without reading old coordinates or parsing ranges. It must succeed regardless of ledger
corruption. Afterward cut episodes no longer occupy whole sources, source fingerprints and snapshots clear, and ``plan_episodes`` can
plan again from the beginning.

Partial reset (specifying a cut episode other than the first in broadcast order) retains preceding ledger episodes and clears that and later cut episodes.
Unlike full reset, it requires **prevalidation**: all recorded source fingerprints must match current sources, and retained ``source_range`` values must
lie within current sources and advance by source position. Any failure prevents safely determining retention/reset boundaries: refuse execution
without ledger changes and direct callers to full reset. Continuation starts derive from ledgers and retreat to the retained last cut episode's end.

Treat cleared cut episodes as replaced old episodes: those with artifacts (ledger consumed, or existing script / script_plan on disk) become sourceless/stale,
retain artifacts and manifest registrations, and move in prior relative order to broadcast-order end; remove those without artifacts from ledgers. Neither reset rolls back
historical maximum project IDs (``lib.episode.episode_ids``): cleared IDs are never reallocated, replanned episodes receive new IDs, and old-ID artifacts cannot
be claimed by new episodes.

This module deliberately avoids :class:`lib.backends.text_generator.TextGenerator`: resets never call models,
and provider misconfiguration must not disable the escape hatch. Writes share ``EpisodePlanner``'s project lock
(``ProjectManager.update_project``) and commit discipline.

Route cleared cut episode files by ledger reconstructability: ``source/episode_N.txt`` with
``source_range`` are derived and deleted directly; files without ``source_range`` may be
legacy originals (including manual contents without reconstructable coordinates), so archive by renaming instead of deleting. Never delete downstream artifacts (script JSON,
script_plan intermediates, media) or modify artifact manifests.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import formal_write_transaction
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    discover_episode_file_aliases,
    discover_product_episode_nums,
    has_downstream_products,
    mismatched_source_fingerprints,
    parse_episode_num,
    parse_source_range,
)
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    SOURCE_SNAPSHOTS_DIR,
    SourceOrigin,
    archive_episode_file_path,
    discover_sources,
    episode_entry,
    first_cut_episode_id,
    is_cut_episode,
    source_snapshot_path,
    sync_source_snapshots,
    whole_source_files,
)
from lib.project.project_manager import ProjectManager
from lib.script import script_review

logger = logging.getLogger(__name__)


class EpisodeResetError(RuntimeError):
    """Episode-planning reset failed."""


class EpisodeResetConflictError(EpisodeResetError):
    """Ledger concurrently changed during reset (consumed episodes outside confirmation inventories); commit refused."""


@dataclass
class ResetConfirmationRequired:
    """Reset affects consumed episodes; execution requires explicit confirmation (``confirm_consumed=True``).

    Returning this object entails no writes. Fields let callers truthfully explain affected episodes/files (project-relative POSIX paths) to users.
    """

    consumed_episodes: list[int]
    archived_files: list[str] = field(default_factory=list)
    #: Ledger episodes with artifacts, becoming sourceless/stale.
    retired_episodes: list[int] = field(default_factory=list)
    #: Ledger episodes without artifacts, removed from ledgers.
    removed_episodes: list[int] = field(default_factory=list)
    #: Derived episode files to delete.
    deleted_files: list[str] = field(default_factory=list)


@dataclass
class EpisodeResetResult:
    """Reset results: affected episode numbers and file dispositions (project-relative POSIX paths)."""

    #: Episodes removed from ledgers (without artifacts).
    removed_episodes: list[int]
    deleted_files: list[str]
    archived_files: list[tuple[str, str]]  # (original path, archive path)
    consumed_episodes: list[int]
    #: Episodes with artifacts, becoming sourceless/stale and moving to broadcast-order end.
    retired_episodes: list[int] = field(default_factory=list)


@dataclass
class _ResetPlan:
    """Disposition plan from one scan: affected and consumed episode numbers, file deletion/archive inventories."""

    episode_nums: list[int]
    consumed: list[int]
    deletes: list[Path]
    archives: list[Path]


_FULL_RESET_HINT = "请改用不带 episode_id 的全量重置"


def _has_recreatable_source_range(entry: Mapping[str, Any]) -> bool:
    """Whether entry source_range contains coordinate structure sufficient to reconstruct derived files.

    Check structural completeness only, without reading sources to validate bounds: reset has zero prevalidation; range validity
    belongs to planning. Treat corrupted entries with incomplete coordinate structure as unreconstructable, archiving
    rather than deleting their files: deletion is irreversible, so insufficient evidence calls for caution.
    """
    return parse_source_range(entry) is not None


def _scan(project_dir: Path, project: Mapping[str, Any], *, retained: frozenset[int] = frozenset()) -> _ResetPlan:
    """Scan ledgers and disk to derive disposition plans. Read only: never change inputs or files.

    Determine consumption by unioning ledger state and disk artifacts: ``ledger_status`` can be untrustworthy in corrupt ledgers,
    while on-disk scripts / script_plan artifacts prove episodes were consumed.

    ``retained`` holds retained episode IDs for partial resets (empty for full resets): exclude retained ledger entries, derived files and
    downstream artifacts from this scan, both consumption classification and deletion/archive candidates.
    """
    raw_episodes = project.get("episodes")
    entries: dict[int, Mapping[str, Any]] = {}
    # Corrupt ledgers may contain duplicate episode entries (first planned, later consumed, or first with
    # source_range, later without). Taking only the first loses later evidence, so consumption and
    # ledger-based file reconstructability aggregate all entries by episode number, rather than just the entry
    # retained by setdefault.
    entries_by_num: dict[int, list[Mapping[str, Any]]] = {}
    consumed_by_ledger_status: set[int] = set()
    # Corrupt episodes containers may be nonlists (for example truthy scalars). Reset promises zero prevalidation;
    # treat these as empty ledgers instead of raising, so the escape hatch itself cannot crash.
    for entry in raw_episodes if isinstance(raw_episodes, list) else []:
        if not isinstance(entry, Mapping):
            continue
        num = parse_episode_num(entry.get("episode"))
        if num is None:
            continue
        if entry.get("ledger_status") == "consumed":
            consumed_by_ledger_status.add(num)
        entries.setdefault(num, entry)
        entries_by_num.setdefault(num, []).append(entry)

    episode_file_aliases = discover_episode_file_aliases(project_dir)
    # Orphan downstream artifact candidate numbers (no ledger entry or source/episode_N.txt, but scripts/ or drafts/
    # still contains episode artifacts) directly prove consumption: has_downstream_products recognizes only
    # canonical paths calculated by episode_script_relpath, not padded aliases that discover_product_episode_nums
    # already matched permissively through regex (for example episode_01.json); include them separately.
    product_nums = discover_product_episode_nums(project_dir)
    consumed: list[int] = []
    deletes: list[Path] = []
    archives: list[Path] = []

    def _in_scope(num: int) -> bool:
        """Whether episode numbers are within this disposition scope.

        Full resets retain nothing, so every parseable number (including 0 or negatives from corrupt ledgers) is in scope,
        never silently left in supposedly cleared ledgers.
        """
        return num not in retained

    # Union ledger entries, derived disk files and downstream disk artifacts:
    # - Orphan episode files without ledger entries also need disposition. Their reconstructability is unproven, so archive;
    #   no source/ episode files inconsistent with ledgers may remain after reset.
    # - Orphan downstream artifacts also count toward consumption; otherwise clearing bypasses confirmation.
    for num in sorted(set(entries) | set(episode_file_aliases) | product_nums):
        if not _in_scope(num):
            continue
        dupes = entries_by_num.get(num) or [{}]
        # Consumption also aggregates all duplicate entries: corrupt ledgers may first point to missing canonical scripts,
        # then later script_file points to existing noncanonical paths (for example scripts/custom_name.json).
        # Passing only the setdefault-retained first entry to has_downstream_products misses these.
        is_consumed = (
            num in consumed_by_ledger_status
            or num in product_nums
            or any(has_downstream_products(project_dir, num, dupe) for dupe in dupes)
        )
        if is_consumed:
            consumed.append(num)
        paths = episode_file_aliases.get(num) or []
        if not paths:
            continue
        # One episode number may have multiple padded aliases (episode_1.txt / episode_01.txt);
        # apply the same disposition to all, or unhandled aliases remain in source/.
        # Deletability uses the reconstructability intersection across all duplicate entries for a number: if any entry cannot prove files
        # reconstructable from ledgers, treat them as unreconstructable. Deletion is irreversible; conflicting evidence calls for caution.
        can_recreate = all(_has_recreatable_source_range(dupe) for dupe in dupes)
        target = deletes if can_recreate else archives
        target.extend(paths)
    episode_nums = [num for num in sorted(entries) if _in_scope(num)]
    return _ResetPlan(episode_nums=episode_nums, consumed=consumed, deletes=deletes, archives=archives)


def _assert_source_directory_safe(project_dir: Path) -> None:
    """Reject a source directory whose children could resolve outside the project."""

    source_dir = project_dir / "source"
    if source_dir.is_symlink() or source_dir.is_junction():
        raise EpisodeResetError("source/ 不能是符号链接或目录联接，拒绝处置派生集文件")


def _apply_files(
    project_dir: Path,
    plan: _ResetPlan,
    *,
    archive_targets: Mapping[Path, Path] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Persist file dispositions: delete derived episode files, archive nonderived episode files and clean remainder files.

    Any failure raises and aborts commits (rolling back ledger writes): residual episode files would disagree with ledgers, so fail entirely and let callers
    retry. Reset is idempotent; rerunning heals completed portions.
    """
    # Refuse disposition when source/ is a symlink or native Windows junction: these reparse points let
    # paths beneath source_dir resolve outside projects, so unlink/rename may affect external files.
    # is_junction() exists in 3.12+ and always returns False on POSIX, matching EpisodePlanner._reconcile_derived_files
    # validation. Derived episode-file symlinks themselves are unaffected: unlink/rename acts on link
    # entries without following final-component targets, so dangling links also clean safely.
    _assert_source_directory_safe(project_dir)
    deleted: list[str] = []
    archived: list[tuple[str, str]] = []
    for path in plan.deletes:
        try:
            path.unlink(
                missing_ok=True
            )  # hard-delete: derived -- derived episode sources removed by resets, regenerable from episode ledgers
        except OSError as exc:
            raise EpisodeResetError(f"派生集文件删除失败，重置已中止：{path.name}: {exc}") from exc
        deleted.append(_rel(project_dir, path))
    for path in plan.archives:
        target = archive_targets[path] if archive_targets is not None else archive_episode_file_path(path)
        try:
            path.rename(target)
        except OSError as exc:
            raise EpisodeResetError(f"集文件留底改名失败，重置已中止：{path.name}: {exc}") from exc
        archived.append((_rel(project_dir, path), _rel(project_dir, target)))
    # Remainder files are not sources and record no planning progress (starts derive from ledgers); retaining them leaves
    # stale remainder text unrelated to ledgers under source/. Clean them along with reset.
    remaining = project_dir / "source" / "_remaining.txt"
    if remaining.is_file():
        try:
            remaining.unlink()  # hard-delete: derived -- derived unsplit-source files, regenerable from episode ledgers
        except OSError as exc:
            raise EpisodeResetError(f"余文文件清理失败，重置已中止：{remaining.name}: {exc}") from exc
    return deleted, archived


def _rel(project_dir: Path, path: Path) -> str:
    return path.relative_to(project_dir).as_posix()


@dataclass(frozen=True)
class _PartialReset:
    """Partial-reset boundaries: retained episode IDs (broadcast order) and normalized full texts of sources referenced by retained entries."""

    retained: tuple[int, ...]
    texts: Mapping[str, str]


def _resolve_partial_reset(project_dir: Path, project: Mapping[str, Any], *, episode_id: int) -> _PartialReset:
    """Validate partial-reset prerequisites and return retained portions.

    Ledger order is broadcast order: entries before ``episode_id`` are retained; it and later cut episodes clear. Any failed condition
    prevents safe retention/reset boundaries; raise :class:`EpisodeResetError` and direct callers
    to full reset. Callers guarantee unchanged ledgers on failed validation:

    - Ledger shape must be clean (``episodes`` a list, entries objects, episode IDs parseable positive integers
      without duplicates). Unlike full resets, partial resets do not tolerate corruption through zero prevalidation.
    - ``episode_id`` must exist in ledgers and not be the first episode in broadcast order (that is full reset).
    - All recorded source fingerprints must match current sources (``mismatched_source_fingerprints``, sharing
      ``EpisodePlanner``'s commit-gate escape hatch).
    - Retained cut episodes with trustworthy ``source_range`` must lie within current corresponding file lengths and be nonempty (``start < end``),
      advancing through source positions in broadcast order: in the same file, subsequent ``start`` must not precede prior ``end``; across files,
      advance to later whole-source manifest files. Unsplit gaps between episodes are valid; overlap or backward movement means corrupt ledgers.

    - All retained cut episodes must have structurally complete ``source_range``: absent ranges indicate legacy cut episodes that still block
      planning when retained, which partial resets cannot solve.

    Standalone-source and sourceless episodes occupy no source positions and skip coordinate validation.
    """
    raw_episodes = project.get("episodes")
    if not isinstance(raw_episodes, list):
        raise EpisodeResetError(f"账本 episodes 字段形状异常，无法安全推算部分重置边界，{_FULL_RESET_HINT}")

    ordered: list[tuple[int, Mapping[str, Any]]] = []
    seen: set[int] = set()
    for entry in raw_episodes:
        if not isinstance(entry, Mapping):
            raise EpisodeResetError(f"账本存在非法条目（非对象），无法安全推算部分重置边界，{_FULL_RESET_HINT}")
        num = parse_episode_num(entry.get("episode"))
        if num is None:
            raise EpisodeResetError(f"账本存在无法解析集 ID 的条目，无法安全推算部分重置边界，{_FULL_RESET_HINT}")
        if num < 1:
            raise EpisodeResetError(
                f"账本存在非法集 ID {num}（须为正整数），无法安全推算部分重置边界，{_FULL_RESET_HINT}"
            )
        if num in seen:
            raise EpisodeResetError(f"账本存在重复集 ID {num}，无法安全推算部分重置边界，{_FULL_RESET_HINT}")
        seen.add(num)
        ordered.append((num, entry))

    boundary = next((index for index, (num, _entry) in enumerate(ordered) if num == episode_id), None)
    if boundary is None:
        raise EpisodeResetError(f"集 ID {episode_id} 不在账本中，无法从此处部分重置")
    retained = ordered[:boundary]

    current_sources = discover_sources(project_dir, project)
    mismatched = mismatched_source_fingerprints(project.get(SOURCE_FINGERPRINTS_KEY), current_sources)
    if mismatched:
        raise EpisodeResetError(
            f"源文件已被修改或移除：{'、'.join(mismatched)}；账本坐标绑定的是修改前的原文内容，"
            f"无法安全部分重置，{_FULL_RESET_HINT}"
        )

    text_by_rel = {doc.rel_path: doc.text for doc in current_sources}
    source_order = {doc.rel_path: idx for idx, doc in enumerate(current_sources)}
    prev: tuple[int, int] | None = None
    for num, entry in retained:
        if not is_cut_episode(entry):
            continue
        span = parse_source_range(entry)
        if span is None:
            raise EpisodeResetError(
                f"保留段里的集 ID {num} 是没有带可信原文范围记录（source_range）的切出集，部分重置后仍无法接续"
                f"分集规划，{_FULL_RESET_HINT}"
            )
        first, last = source_order.get(span.source_file), source_order.get(span.end_file)
        start_text, end_text = text_by_rel.get(span.source_file), text_by_rel.get(span.end_file)
        if (
            first is None
            or last is None
            or start_text is None
            or end_text is None
            or last < first
            or not 0 <= span.start <= len(start_text)
            or not 0 <= span.end <= len(end_text)
            or (first == last and span.start >= span.end)
        ):
            length = len(start_text) if start_text is not None else 0
            raise EpisodeResetError(
                f"集 ID {num} 的原文范围无效（源文件 {span.source_file} 当前长度 {length}，"
                f"记录范围 [{span.start}, {span.end})），无法安全部分重置，{_FULL_RESET_HINT}"
            )
        if prev is not None and (first, span.start) < prev:
            raise EpisodeResetError(
                f"集 ID {num} 的原文范围与播出顺序中前一个切出集重叠或倒退，账本可能已损坏，"
                f"无法安全部分重置，{_FULL_RESET_HINT}"
            )
        prev = (last, span.end)

    return _PartialReset(retained=tuple(num for num, _entry in retained), texts=text_by_rel)


def _other_origin_episode_ids(project: Mapping[str, Any]) -> frozenset[int]:
    """Standalone-source and sourceless episode IDs: neither reset changes them. Tolerate ledger corruption using parseable entries."""
    raw_episodes = project.get("episodes")
    return frozenset(
        num
        for entry in (raw_episodes if isinstance(raw_episodes, list) else [])
        if isinstance(entry, Mapping)
        and not is_cut_episode(entry)
        and (num := parse_episode_num(entry.get("episode"))) is not None
    )


def reset_episode_planning(
    project_path: str | Path,
    *,
    episode_id: int | None = None,
    confirm_consumed: bool = False,
) -> EpisodeResetResult | ResetConfirmationRequired:
    """Reset episode-planning ledgers.

    No ``episode_id`` (or the first cut episode in broadcast order) means full reset: zero prevalidation, succeeding regardless of
    ledger corruption (see module docs). Other specified episodes mean partial reset, retaining earlier broadcast-order episodes;
    see :func:`_resolve_partial_reset` prevalidation. Failed validation names specific reasons and directs callers to full
    reset without ledger changes. Cleared IDs are never reallocated; replanned episodes receive new IDs beyond historical maxima.

    Both modes refrain from execution when consumed episodes are affected (ledger consumed or on-disk script / script_plan artifacts) without
    ``confirm_consumed``; return :class:`ResetConfirmationRequired` awaiting
    explicit confirmation. After confirmation, consumed episodes become sourceless/stale, retaining all downstream artifacts and manifest registrations.

    Raises:
        EpisodeResetError: Nonpositive-integer ``episode_id``, failed partial-reset prevalidation, or
            failed file disposition; all guarantee unchanged ledgers.
        EpisodeResetConflictError: Consumed episodes outside confirmation inventories appeared during reset.
    """
    if episode_id is not None and episode_id < 1:
        raise EpisodeResetError(f"episode_id 必须是正整数，收到 {episode_id}")

    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)
    project_name = project_dir.name

    # Unlocked prescans/prevalidation only support two-stage confirmation and fast failure: return without writes on failure or required confirmation,
    # without locking or touching files.
    project = pm.load_project(project_name)
    if (
        episode_id is not None
        and (target := episode_entry(project, episode_id)) is not None
        and not is_cut_episode(target)
    ):
        raise EpisodeResetError(f"集（id={episode_id}）不是切自整本源文的集；分集规划重置只能从切出集起算")
    partial_from = None if episode_id is None or episode_id == first_cut_episode_id(project) else episode_id

    def _boundary(p: Mapping[str, Any]) -> _PartialReset | None:
        return None if partial_from is None else _resolve_partial_reset(project_dir, p, episode_id=partial_from)

    def _retained(p: Mapping[str, Any], boundary: _PartialReset | None) -> frozenset[int]:
        kept = _other_origin_episode_ids(p)
        return kept | frozenset(boundary.retained) if boundary is not None else kept

    boundary = _boundary(project)
    plan = _scan(project_dir, project, retained=_retained(project, boundary))
    if plan.consumed and not confirm_consumed:
        retired = [num for num in plan.episode_nums if num in plan.consumed and num > 0]
        return ResetConfirmationRequired(
            consumed_episodes=plan.consumed,
            archived_files=[_rel(project_dir, path) for path in plan.archives],
            retired_episodes=retired,
            removed_episodes=[num for num in plan.episode_nums if num not in retired],
            deleted_files=[_rel(project_dir, path) for path in plan.deletes],
        )

    # Assemble results only under locks using actual rescanned dispositions; return through closure variables.
    committed: list[EpisodeResetResult] = []
    commit_plan: _ResetPlan | None = None
    retired_nums: list[int] = []
    snapshot_texts: dict[str, str] = {}
    committed_project: dict[str, Any] = {}

    def _commit(p: dict[str, Any]) -> None:
        nonlocal commit_plan, retired_nums, snapshot_texts
        # Revalidate/rescan under locks: confirmation inventories and prevalidation snapshot the earlier unlocked read; sources
        # may since have changed externally, or new consumed episodes appeared outside inventories.
        locked_boundary = _boundary(p)
        retained = _retained(p, locked_boundary)
        current = _scan(project_dir, p, retained=retained)
        if any(num not in plan.consumed for num in current.consumed):
            raise EpisodeResetConflictError("重置期间出现新的已消费集，需重新确认后再执行")
        raw_episodes = p.get("episodes")
        consumed = set(current.consumed)
        kept: list[Any] = []
        retired: list[dict[str, Any]] = []
        for entry in raw_episodes if isinstance(raw_episodes, list) else []:
            if not isinstance(entry, Mapping):
                continue
            num = parse_episode_num(entry.get("episode"))
            if num in retained:
                kept.append(entry)
            elif (
                isinstance(entry, dict) and num is not None and num > 0 and num in consumed and num not in retired_nums
            ):
                # Treat cut episodes with artifacts as replaced old episodes: become sourceless/stale, retaining artifacts and registrations.
                entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.NONE.value
                entry.pop("source_range", None)
                script_review.mark_ledger_stale(project_dir, p, entry, num)
                retired.append(entry)
                retired_nums.append(num)
        p["episodes"] = [*kept, *retired]
        if locked_boundary is not None:
            snapshot_texts = dict(locked_boundary.texts)
        else:
            p.pop(SOURCE_FINGERPRINTS_KEY, None)
        commit_plan = current
        committed_project.update(p)

    def _commit_side_effects(_project_file: Path) -> None:
        if commit_plan is None:  # pragma: no cover - update_project calls mutate before on_commit
            raise EpisodeResetError("重置未执行：文件处置计划未生成")
        _assert_source_directory_safe(project_dir)
        archive_targets = {path: archive_episode_file_path(path) for path in commit_plan.archives}
        remaining = project_dir / "source" / "_remaining.txt"
        transaction_paths = [*commit_plan.deletes, remaining]
        for source, target in archive_targets.items():
            transaction_paths.extend((source, target))
        snapshot_dir = project_dir / SOURCE_SNAPSHOTS_DIR
        if snapshot_dir.is_dir() and not snapshot_dir.is_symlink():
            transaction_paths.extend(path for path in snapshot_dir.iterdir() if path.is_file())
        transaction_paths.extend(
            source_snapshot_path(project_dir, rel) for rel in whole_source_files(committed_project)
        )

        with formal_write_transaction(*transaction_paths):
            deleted, archived = _apply_files(
                project_dir,
                commit_plan,
                archive_targets=archive_targets,
            )
            # Reset never changes sources: preserve externally changed file snapshots for alignment when updating episode ledgers.
            sync_source_snapshots(project_dir, committed_project, snapshot_texts, refreshed=())
        committed.append(
            EpisodeResetResult(
                removed_episodes=[num for num in commit_plan.episode_nums if num not in retired_nums],
                deleted_files=deleted,
                archived_files=archived,
                consumed_episodes=commit_plan.consumed,
                retired_episodes=list(retired_nums),
            )
        )

    pm.update_project(project_name, _commit, on_commit=_commit_side_effects)
    if not committed:  # pragma: no cover - update_project necessarily calls mutate_fn
        raise EpisodeResetError("重置未执行：账本更新回调未被调用")
    result = committed[0]
    logger.info(
        "分集规划已%s重置：项目 %s，移出 %d 集，转为无原文 %d 集，删除派生文件 %d 个，留底 %d 个",
        "全量" if partial_from is None else f"部分（从集 ID {partial_from} 起）",
        project_name,
        len(result.removed_episodes),
        len(result.retired_episodes),
        len(result.deleted_files),
        len(result.archived_files),
    )
    return result


__all__ = [
    "EpisodeResetConflictError",
    "EpisodeResetError",
    "EpisodeResetResult",
    "ResetConfirmationRequired",
    "reset_episode_planning",
]
