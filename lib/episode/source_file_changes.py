"""Whole-source insertion/replacement/edit/deletion/reordering (ADR 0097).

Only episodes touching files change, never requiring replanning from episode 1. After
:mod:`lib.episode.source_remap`, classify six outcomes:

- **Shift only**: unchanged text, moved offsets/broadcast positions;
- **Changed source**, separately with/without artifacts: mapped changed ranges become stale;
- **Retire**: entire deleted ranges with artifacts turn sourceless/stale, appended in prior relative order;
- **Remove**: entirely deleted without artifacts;
- **Kind-stale script plans**: replacement changes types for existing planned splits starting in changed files.

Externally changed fingerprints/snapshots trigger :func:`accept_external_source_change`, aligning old snapshots/current text
through identical remapping, confirming before ledger/fingerprint/snapshot updates without file edits.

Affected episodes return inventories/``revision`` without writes; creators confirm by resubmitting ``revision``.
Locked recomputed inventories must match. Unaffected commands apply directly; ``dry_run`` computes only, allowing
callers to check in-flight tasks on retired/removed episodes before execution.

Moves reorder split episodes by new source positions; other origins follow nearest preceding split anchors (ADR 0032),
otherwise stay first.

Replacement/edit/move reject external changes, deletion does not.
Sources/ledgers/derived episodes/snapshots write atomically under project locks, fully rolling back failures.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lib.episode.episode_ids import episode_display_name
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    SourceSpan,
    compute_source_fingerprints,
    episodes_with_products,
    normalize_source_text,
    parse_positive_episode_num,
    parse_source_range,
    source_range_value,
    well_formed_ledger_entries,
)
from lib.episode.episode_paths import episode_source_path
from lib.episode.episode_source_commands import register_whole_source_file
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    SOURCE_SNAPSHOTS_DIR,
    WHOLE_SOURCE_FILES_KEY,
    SourceOrigin,
    changed_outside_service,
    cut_episode_placements,
    cut_episode_source_files,
    discover_sources,
    is_cut_episode,
    read_source_snapshot,
    remove_whole_source_file,
    span_text,
    sync_source_snapshots,
    whole_source_files,
)
from lib.episode.source_kinds import (
    SourceKind,
    record_whole_source_file_kind,
    source_kind_applies,
    whole_source_file_kind,
)
from lib.episode.source_remap import (
    RangeRemap,
    remap_for_delete,
    remap_for_edit,
    remap_for_insert,
    remap_for_move,
)
from lib.episode.source_trash import trash_source_file
from lib.script import script_review

if TYPE_CHECKING:
    from lib.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)

MoveDirection = Literal["up", "down"]


class SourceFileChangeError(ValueError):
    """Rejected whole-source changes; stable ``code`` maps statuses/wording. No writes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceFileImpact:
    """Affected episode IDs in prior broadcast order."""

    shifted: list[int] = field(default_factory=list)
    changed_with_products: list[int] = field(default_factory=list)
    changed_without_products: list[int] = field(default_factory=list)
    retired: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    kind_stale: list[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(self.to_dict().values())

    def to_dict(self) -> dict[str, list[int]]:
        return {
            "shifted": list(self.shifted),
            "changed_with_products": list(self.changed_with_products),
            "changed_without_products": list(self.changed_without_products),
            "retired": list(self.retired),
            "removed": list(self.removed),
            "kind_stale": list(self.kind_stale),
        }


@dataclass(frozen=True)
class SourceFileChangeOutcome:
    """Command results; false ``applied`` means no writes, awaiting ``revision`` confirmation."""

    applied: bool
    impact: SourceFileImpact
    #: Impact revisions, returned unchanged on confirmation.
    revision: str


@dataclass
class _Change:
    """Readonly file-change simulations; ``commit`` writes sources/manifests after confirmation."""

    #: New readable whole-source order for split positioning.
    docs: list[SourceDoc]
    remaps: list[RangeRemap]
    #: Source/manifest commits register every disk-write undo in ``undo``.
    commit: Callable[[dict[str, Any], ExitStack], None]
    #: Content-changed files update/remove recorded fingerprints.
    touched: list[str] = field(default_factory=list)
    #: True for moves, reordering splits by source positions.
    reorder: bool = False
    #: Replacement-kind changes stale planned splits starting here.
    kind_change: tuple[str, SourceKind] | None = None


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------


def _filename_rel(project: Mapping[str, Any], filename: str) -> str:
    rel = f"source/{filename}"
    if "/" in filename or "\\" in filename or rel not in whole_source_files(project):
        raise SourceFileChangeError("source_file_not_found", f"整本源文里没有这个文件：{filename}")
    return rel


def _entries(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = well_formed_ledger_entries(project)
    if entries is None:
        raise SourceFileChangeError("ledger_invalid", "分集账本的形状异常，不能改动整本源文的文件")
    return entries


def _spans(project: Mapping[str, Any], docs: list[SourceDoc]) -> dict[int, SourceSpan]:
    """Placeable split ranges, clamping beyond-file ends to lengths."""
    return {
        episode: SourceSpan(
            source_file=docs[p.file_index].rel_path,
            start=p.start,
            end_file=docs[p.end_file_index].rel_path,
            end=p.end,
        )
        for episode, p in cut_episode_placements(project, docs).items()
    }


def _require_doc(docs: list[SourceDoc], rel: str) -> SourceDoc:
    doc = next((doc for doc in docs if doc.rel_path == rel), None)
    if doc is None:
        raise SourceFileChangeError("source_file_unreadable", f"源文件不是可读的 UTF-8 文本：{rel}")
    return doc


def _check_fingerprint(project_dir: Path, project: Mapping[str, Any], doc: SourceDoc) -> None:
    """Reject external changes without updated ledgers."""
    if changed_outside_service(project_dir, project, doc):
        raise SourceFileChangeError("source_changed", f"源文件在服务之外被改动过：{doc.rel_path}")


def _require_text(text: str) -> str:
    normalized = normalize_source_text(text)
    if not normalized.strip():
        raise SourceFileChangeError("source_text_empty", "源文件不能为空；要去掉这个文件请删除它")
    return normalized


# ---------------------------------------------------------------------------
# Impact.
# ---------------------------------------------------------------------------


def _order_after(
    entries: Sequence[Mapping[str, Any]], change: _Change, impact_groups: Mapping[str, set[int]]
) -> list[int]:
    """New broadcast IDs excluding retired/removed; retired append separately."""
    positions = {
        remap.episode: (
            [doc.rel_path for doc in change.docs].index(remap.after.source_file),
            remap.after.start,
        )
        for remap in change.remaps
        if remap.after is not None
    }
    head: list[int] = []
    groups: list[list[int]] = []
    for entry in entries:
        episode = parse_positive_episode_num(entry.get("episode"))
        if episode is None or episode in impact_groups["retired"] or episode in impact_groups["removed"]:
            continue
        if episode in positions:
            groups.append([episode])
        elif groups:
            groups[-1].append(episode)
        else:
            head.append(episode)
    if change.reorder:
        groups.sort(key=lambda group: positions[group[0]])
    return [*head, *(episode for group in groups for episode in group)]


def _impact(
    project_dir: Path, project: Mapping[str, Any], entries: list[dict[str, Any]], change: _Change
) -> tuple[SourceFileImpact, list[int]]:
    """Affected inventories/new order excluding retired/removed."""
    with_products = episodes_with_products(project_dir, entries)
    by_episode = {remap.episode: remap for remap in change.remaps}
    groups: dict[str, set[int]] = {key: set() for key in SourceFileImpact().to_dict()}
    for remap in change.remaps:
        has_products = remap.episode in with_products
        if remap.after is None:
            groups["retired" if has_products else "removed"].add(remap.episode)
        elif remap.text_changed:
            groups["changed_with_products" if has_products else "changed_without_products"].add(remap.episode)
    order = _order_after(entries, change, groups)
    before = [
        episode
        for entry in entries
        if (episode := parse_positive_episode_num(entry.get("episode"))) is not None and episode in order
    ]
    for index, episode in enumerate(order):
        if any(episode in groups[key] for key in ("changed_with_products", "changed_without_products")):
            continue
        remap = by_episode.get(episode)
        moved = remap is not None and remap.after != remap.before
        if moved or set(order[:index]) != set(before[: before.index(episode)]):
            groups["shifted"].add(episode)
    if change.kind_change is not None:
        rel, _kind = change.kind_change
        settled = set().union(*(groups[key] for key in ("changed_with_products", "changed_without_products")))
        for remap in change.remaps:
            if remap.after is None or remap.after.source_file != rel or remap.episode in settled:
                continue
            plan = script_review.script_plan_path(project_dir, dict(project), remap.episode)
            if plan is not None and plan.is_file():
                groups["kind_stale"].add(remap.episode)
    ledger_order = [
        episode for entry in entries if (episode := parse_positive_episode_num(entry.get("episode"))) is not None
    ]
    impact = SourceFileImpact(
        **{key: [episode for episode in ledger_order if episode in members] for key, members in groups.items()}
    )
    return impact, order


def _revision(operation: Mapping[str, Any], impact: SourceFileImpact) -> str:
    payload = json.dumps({"operation": operation, "impact": impact.to_dict()}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Commit.
# ---------------------------------------------------------------------------


def _restore_file(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)  # hard-delete: rollback -- remove transaction-new files during undo
    else:
        path.write_bytes(content)


def _remember(path: Path, undo: ExitStack) -> None:
    """Record current ``path`` contents for rollback."""
    undo.callback(_restore_file, path, path.read_bytes() if path.is_file() and not path.is_symlink() else None)


def _write_text(path: Path, text: str, undo: ExitStack) -> None:
    if path.is_symlink():
        raise SourceFileChangeError("episode_source_symlink", f"文件是符号链接，拒绝写入：{path.name}")
    _remember(path, undo)
    path.write_text(text, encoding="utf-8", newline="\n")


def _sync_snapshots(
    project_dir: Path, project: Mapping[str, Any], docs: list[SourceDoc], touched: Iterable[str], undo: ExitStack
) -> None:
    snapshot_dir = project_dir / SOURCE_SNAPSHOTS_DIR
    if snapshot_dir.is_dir() and not snapshot_dir.is_symlink():
        for path in snapshot_dir.iterdir():
            if path.is_file() and not path.is_symlink():
                _remember(path, undo)
    for doc in docs:
        _remember(snapshot_dir / Path(doc.rel_path).name, undo)
    sync_source_snapshots(project_dir, project, {doc.rel_path: doc.text for doc in docs}, refreshed=set(touched))


def _record_fingerprints(project: dict[str, Any], docs: list[SourceDoc], touched: Iterable[str]) -> None:
    """Changed files update existing fingerprints; files leaving whole sources lose records."""
    raw = project.get(SOURCE_FINGERPRINTS_KEY)
    if not isinstance(raw, Mapping):
        return
    recorded = dict(raw)
    current = compute_source_fingerprints(docs)
    for rel in touched:
        if rel not in current:
            recorded.pop(rel, None)
        elif rel in recorded:
            recorded[rel] = current[rel]
    project[SOURCE_FINGERPRINTS_KEY] = recorded


def _apply(
    project_dir: Path,
    project: dict[str, Any],
    entries: list[dict[str, Any]],
    change: _Change,
    impact: SourceFileImpact,
    order: list[int],
    undo: ExitStack,
) -> None:
    by_id = {parse_positive_episode_num(entry.get("episode")): entry for entry in entries}
    changed = {*impact.changed_with_products, *impact.changed_without_products}
    for remap in change.remaps:
        if remap.after is not None and remap.after != remap.before:
            after = remap.after
            by_id[remap.episode]["source_range"] = source_range_value(
                after.source_file, after.start, after.end_file, after.end
            )
    for episode in changed:
        script_review.mark_ledger_stale(project_dir, project, by_id[episode], episode)
    retired: list[dict[str, Any]] = []
    for episode in impact.retired:
        entry = by_id[episode]
        entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.NONE.value
        entry.pop("source_range", None)
        script_review.mark_ledger_stale(project_dir, project, entry, episode)
        retired.append(entry)
    project["episodes"] = [*(by_id[episode] for episode in order), *retired]

    docs = discover_sources(project_dir, project)
    texts = {doc.rel_path: doc.text for doc in docs}
    rels = [doc.rel_path for doc in docs]
    for remap in change.remaps:
        if remap.episode in changed and remap.after is not None:
            text = span_text(texts, rels, remap.after)
            if text is None:
                raise SourceFileChangeError("conflict", f"集（id={remap.episode}）的原文范围落不进改动后的整本源文")
            _write_text(episode_source_path(project_dir, remap.episode), text, undo)
    for episode in [*impact.retired, *impact.removed]:
        path = episode_source_path(project_dir, episode)
        if path.is_file() and not path.is_symlink():
            _remember(path, undo)
            path.unlink()  # hard-delete: derived -- retired derived sources regenerable from ledgers after whole-source changes
    _sync_snapshots(project_dir, project, docs, change.touched, undo)
    _record_fingerprints(project, docs, change.touched)


def _run(
    pm: ProjectManager,
    project_name: str,
    operation: Mapping[str, Any],
    plan: Callable[[Path, dict[str, Any]], _Change],
    *,
    revision: str | None,
    dry_run: bool = False,
) -> SourceFileChangeOutcome:
    """Simulate/compute impact under locks, commit confirmed/unaffected changes; ``dry_run`` never writes."""
    project_dir = pm.get_project_path(project_name)
    with pm.locked_source_registration(project_name) as (_source_dir, project, undo):
        entries = _entries(project)
        change = plan(project_dir, project)
        impact, order = _impact(project_dir, project, entries, change)
        current = _revision(operation, impact)
        if dry_run or (not impact.is_empty and revision != current):
            return SourceFileChangeOutcome(applied=False, impact=impact, revision=current)
        change.commit(project, undo)
        _apply(project_dir, project, entries, change, impact, order, undo)
    logger.info("整本源文文件已改动：项目 %s，%s，受影响 %s", project_name, dict(operation), impact.to_dict())
    return SourceFileChangeOutcome(applied=True, impact=impact, revision=current)


# ---------------------------------------------------------------------------
# Commands.
# ---------------------------------------------------------------------------


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _plan_rewrite(
    filename: str, text: str, *, replace: bool, source_kind: SourceKind | None = None
) -> Callable[[Path, dict[str, Any]], _Change]:
    def plan(project_dir: Path, project: dict[str, Any]) -> _Change:
        rel = _filename_rel(project, filename)
        docs = discover_sources(project_dir, project)
        _check_fingerprint(project_dir, project, _require_doc(docs, rel))
        remaps = remap_for_edit(docs, _spans(project, docs), rel=rel, new_text=text)
        kind_change = None
        if (
            source_kind is not None
            and source_kind_applies(project)
            and whole_source_file_kind(project, rel) != source_kind
        ):
            kind_change = (rel, source_kind)

        def commit(p: dict[str, Any], undo: ExitStack) -> None:
            if replace:
                # Replacement uploads trash originals/raw uploads, whose contents no longer match replacements.
                trash_source_file(project_dir, rel, undo)
            _write_text(project_dir / rel, text, undo)
            if source_kind is not None:
                record_whole_source_file_kind(p, rel, source_kind)

        new_docs = [SourceDoc(rel_path=rel, text=text) if doc.rel_path == rel else doc for doc in docs]
        return _Change(docs=new_docs, remaps=remaps, commit=commit, touched=[rel], kind_change=kind_change)

    return plan


def edit_whole_source_file(
    pm: ProjectManager,
    project_name: str,
    filename: str,
    text: str,
    *,
    revision: str | None = None,
    dry_run: bool = False,
) -> SourceFileChangeOutcome:
    """Edit entire ``source/<filename>``, aligning old/new to remap touching episodes."""
    normalized = _require_text(text)
    operation = {"action": "edit", "file": filename, "text": _text_digest(normalized)}
    plan = _plan_rewrite(filename, normalized, replace=False)
    return _run(pm, project_name, operation, plan, revision=revision, dry_run=dry_run)


def replace_whole_source_file(
    pm: ProjectManager,
    project_name: str,
    filename: str,
    text: str,
    *,
    source_kind: SourceKind | None = None,
    revision: str | None = None,
    dry_run: bool = False,
) -> SourceFileChangeOutcome:
    """Replace ``source/<filename>`` content, preserving positions/names and kinds when omitted.

    Remap like edits; changed kinds stale planned splits starting here.
    Move originals/raw backups into project trash.
    """
    normalized = _require_text(text)
    operation = {"action": "replace", "file": filename, "text": _text_digest(normalized), "kind": source_kind}
    plan = _plan_rewrite(filename, normalized, replace=True, source_kind=source_kind)
    return _run(pm, project_name, operation, plan, revision=revision, dry_run=dry_run)


def delete_whole_source_file(
    pm: ProjectManager, project_name: str, filename: str, *, revision: str | None = None, dry_run: bool = False
) -> SourceFileChangeOutcome:
    """Delete ``source/<filename>`` text/remove manifests, moving files/raw originals into trash.

    Allow external changes. Missing/unreadable files entirely remove episodes whose start/end is in them,
    including portions crossing other files because original lengths are unknown.
    """

    def plan(project_dir: Path, project: dict[str, Any]) -> _Change:
        rel = _filename_rel(project, filename)
        docs = discover_sources(project_dir, project)
        spans = _spans(project, docs)
        if any(doc.rel_path == rel for doc in docs):
            remaps = remap_for_delete(docs, spans, rel=rel)
        else:
            remaps = [RangeRemap(episode=e, before=s, after=s, text_changed=False) for e, s in spans.items()]
            remaps.extend(
                RangeRemap(episode=episode, before=span, after=None, text_changed=True)
                for entry in _entries(project)
                if (episode := parse_positive_episode_num(entry.get("episode"))) is not None
                and episode not in spans
                and is_cut_episode(entry)
                and (span := parse_source_range(entry)) is not None
                and rel in (span.source_file, span.end_file)
            )

        def commit(p: dict[str, Any], undo: ExitStack) -> None:
            remove_whole_source_file(p, rel)
            trash_source_file(project_dir, rel, undo)

        return _Change(docs=[doc for doc in docs if doc.rel_path != rel], remaps=remaps, commit=commit, touched=[rel])

    return _run(pm, project_name, {"action": "delete", "file": filename}, plan, revision=revision, dry_run=dry_run)


def move_whole_source_file(
    pm: ProjectManager,
    project_name: str,
    filename: str,
    *,
    direction: MoveDirection,
    revision: str | None = None,
    dry_run: bool = False,
) -> SourceFileChangeOutcome:
    """Move ``source/<filename>`` one manifest position up/down.

    Splits move as broadcast blocks without stale flags; spanning ranges retain still-connected starting-file portions only.
    """

    def plan(project_dir: Path, project: dict[str, Any]) -> _Change:
        rel = _filename_rel(project, filename)
        files = whole_source_files(project)
        index = files.index(rel)
        other = index - 1 if direction == "up" else index + 1
        if not 0 <= other < len(files):
            raise SourceFileChangeError(
                "move_out_of_range", f"{filename} 已经在整本源文的{'最前' if direction == 'up' else '最后'}"
            )
        docs = discover_sources(project_dir, project)
        for doc in docs:
            if doc.rel_path in (rel, files[other]):
                _check_fingerprint(project_dir, project, doc)
        new_files = list(files)
        new_files[index], new_files[other] = new_files[other], new_files[index]
        by_rel = {doc.rel_path: doc for doc in docs}
        new_docs = [by_rel[f] for f in new_files if f in by_rel]
        spans = _spans(project, docs)
        if rel in by_rel:
            remaps = remap_for_move(docs, spans, rel=rel, new_index=[d.rel_path for d in new_docs].index(rel))
        else:
            remaps = [RangeRemap(episode=e, before=s, after=s, text_changed=False) for e, s in spans.items()]

        def commit(p: dict[str, Any], _undo: ExitStack) -> None:
            raw = p.get(WHOLE_SOURCE_FILES_KEY)
            items = list(raw) if isinstance(raw, list) else []
            positions = {item.get("source_file"): i for i, item in enumerate(items) if isinstance(item, Mapping)}
            a, b = positions[rel], positions[files[other]]
            items[a], items[b] = items[b], items[a]
            p[WHOLE_SOURCE_FILES_KEY] = items

        return _Change(docs=new_docs, remaps=remaps, commit=commit, reorder=True)

    operation = {"action": "move", "file": filename, "direction": direction}
    return _run(pm, project_name, operation, plan, revision=revision, dry_run=dry_run)


def accept_external_source_change(
    pm: ProjectManager,
    project_name: str,
    filename: str,
    *,
    revision: str | None = None,
    dry_run: bool = False,
) -> SourceFileChangeOutcome:
    """Update ledgers for externally changed ``source/<filename>``.

    Align snapshots/current text, remapping touching episodes under edit confirmation rules; replace fingerprints/snapshots afterward.
    Unchanged rejects as ``source_not_changed``. Splits without snapshots cannot align (``source_snapshot_missing``),
    requiring deletion or planning reset.
    """

    def plan(project_dir: Path, project: dict[str, Any]) -> _Change:
        rel = _filename_rel(project, filename)
        docs = discover_sources(project_dir, project)
        doc = _require_doc(docs, rel)
        if not changed_outside_service(project_dir, project, doc):
            raise SourceFileChangeError("source_not_changed", f"源文件没有在服务之外被改动过：{rel}")
        snapshot = read_source_snapshot(project_dir, rel)
        if snapshot is None:
            if rel in cut_episode_source_files(project):
                raise SourceFileChangeError("source_snapshot_missing", f"源文件没有快照，无法对齐：{rel}")
            snapshot = doc.text
        old_docs = [SourceDoc(rel_path=rel, text=snapshot) if d.rel_path == rel else d for d in docs]
        remaps = remap_for_edit(old_docs, _spans(project, old_docs), rel=rel, new_text=doc.text)

        def commit(_p: dict[str, Any], _undo: ExitStack) -> None:
            pass

        return _Change(docs=docs, remaps=remaps, commit=commit, touched=[rel])

    def current_digest() -> str:
        path = pm.get_project_path(project_name) / "source" / filename
        try:
            return _text_digest(normalize_source_text(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            return ""

    operation = {"action": "accept_external", "file": filename, "text": current_digest()}
    return _run(pm, project_name, operation, plan, revision=revision, dry_run=dry_run)


def insert_whole_source_file(
    pm: ProjectManager,
    project_name: str,
    write: Callable[[Path, ExitStack], str],
    *,
    index: int | None,
    source_kind: SourceKind | None = None,
    revision: str | None = None,
) -> tuple[SourceFileChangeOutcome, str | None]:
    """Insert at manifest ``index``, default last, returning results/project-relative registered paths.

    Confirm before ``write(source_dir, undo)`` creates files/returns paths, recording rollback in ``undo``. Inside
    spanning episodes, new files join/change those sources; otherwise affect none and apply directly.
    """
    written: dict[str, str] = {}

    def plan(project_dir: Path, project: dict[str, Any]) -> _Change:
        files = whole_source_files(project)
        position = len(files) if index is None else min(max(index, 0), len(files))
        docs = discover_sources(project_dir, project)
        readable = {doc.rel_path for doc in docs}
        doc_index = sum(1 for rel in files[:position] if rel in readable)
        remaps = remap_for_insert(docs, _spans(project, docs), index=doc_index)

        def commit(p: dict[str, Any], undo: ExitStack) -> None:
            rel = write(project_dir / "source", undo)
            register_whole_source_file(p, rel, index=position, source_kind=source_kind)
            written["rel"] = rel

        return _Change(docs=docs, remaps=remaps, commit=commit)

    operation = {"action": "insert", "index": index}
    outcome = _run(pm, project_name, operation, plan, revision=revision)
    return outcome, written.get("rel")


# ---------------------------------------------------------------------------
# Confirmation inventories.
# ---------------------------------------------------------------------------

_IMPACT_LINES = (
    ("shifted", "source_file_impact_shifted"),
    ("changed_with_products", "source_file_impact_changed_with_products"),
    ("changed_without_products", "source_file_impact_changed_without_products"),
    ("retired", "source_file_impact_retired"),
    ("removed", "source_file_impact_removed"),
    ("kind_stale", "source_file_impact_kind_stale"),
)


def render_source_file_impact_text(
    impact: Mapping[str, Iterable[int]], project: Mapping[str, Any], translate: Callable[..., str]
) -> str:
    """Render impact groups as confirmation, episodes by titles/positions; Web displays unchanged."""

    def name(episode: int) -> str:
        return episode_display_name(project, episode, translate)

    separator = translate("manual_split_impact_separator")
    return "\n".join(
        translate(line_key, episodes=separator.join(name(episode) for episode in episodes))
        for key, line_key in _IMPACT_LINES
        if (episodes := list(impact.get(key) or ()))
    )


__all__ = [
    "MoveDirection",
    "SourceFileChangeError",
    "SourceFileChangeOutcome",
    "SourceFileImpact",
    "accept_external_source_change",
    "delete_whole_source_file",
    "edit_whole_source_file",
    "insert_whole_source_file",
    "move_whole_source_file",
    "render_source_file_impact_text",
    "replace_whole_source_file",
]
