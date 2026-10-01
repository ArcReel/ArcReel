"""整本源文文件的插入、替换、编辑、删除与调序（ADR 0097）。

文件改动只影响原文范围触及这个文件的切出集，不要求从第 1 集起重新规划。原文范围按
:mod:`lib.episode.source_remap` 重映射后，各集分六类：

- **只平移**：文字没变，原文范围的偏移或在播出顺序里的先后变了；
- **原文有变化**（分有产物、没产物两类）：范围内文字变了，取映射后的范围并标 stale；
- **退下**：整段被删、已有产物，转为无原文的集，标 stale，按原相对顺序移到播出顺序末尾；
- **直接移除**：整段被删、没有产物；
- **因改类型而 stale 的脚本规划**：替换时改了源文件类型，起点在这个文件、已有脚本规划的切出集。

有受影响的集时，命令不写入，返回受影响集清单与它的 ``revision``；创作者确认后带上 ``revision`` 重新调用，
锁内重算的清单与确认过的一致才执行。没有受影响的集时直接执行。``dry_run`` 只算清单、不写入，供调用方在执行前
检查要退下或移除的集有没有在途任务。

调序时切出集按新的源文位置排列，其他来源的集跟着播出顺序中前面最近的那个切出集走（ADR 0032 的锚点规则），
前面没有切出集的留在最前。

替换、编辑与调序先核对文件的源文指纹：文件在服务之外被改动过时拒绝，删除不受限。
写源文、账本、派生集文件与快照在同一把项目锁内完成，失败时整体撤销。
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

from lib.episode.episode_ids import episode_position, episode_title
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    SourceSpan,
    compute_source_fingerprints,
    episodes_with_products,
    normalize_source_text,
    parse_positive_episode_num,
    source_range_value,
)
from lib.episode.episode_paths import episode_source_path
from lib.episode.episode_source_commands import register_whole_source_file
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    SOURCE_SNAPSHOTS_DIR,
    WHOLE_SOURCE_FILES_KEY,
    SourceOrigin,
    cut_episode_placements,
    discover_sources,
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
from lib.script import script_review

if TYPE_CHECKING:
    from lib.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)

MoveDirection = Literal["up", "down"]


class SourceFileChangeError(ValueError):
    """整本源文文件的改动被拒；``code`` 是稳定的原因码，入口据此映射状态码与文案。没有写入。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceFileImpact:
    """一次文件改动波及的集（集 ID，按改动前的播出顺序）。"""

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
    """命令结果。``applied`` 为 False 时没有任何写入，等待带 ``revision`` 确认。"""

    applied: bool
    impact: SourceFileImpact
    #: 这份受影响集清单的版本；确认时原样带回。
    revision: str


@dataclass
class _Change:
    """一次文件改动的推演结果。推演只读；``commit`` 在确认之后改写源文件与整本源文清单。"""

    #: 改动后的整本源文文件顺序（只含可读的文件），用来给切出集排源文位置。
    docs: list[SourceDoc]
    remaps: list[RangeRemap]
    #: 改写源文件与整本源文清单；每改一处盘上文件都把撤销登记进 ``undo``。
    commit: Callable[[dict[str, Any], ExitStack], None]
    #: 内容变了的文件：已记录的源文指纹随之更新或去掉。
    touched: list[str] = field(default_factory=list)
    #: 调序时为 True：切出集按新的源文位置重新排列播出顺序。
    reorder: bool = False
    #: 替换时改了类型：起点在这个文件、已有脚本规划的切出集的脚本规划判 stale。
    kind_change: tuple[str, SourceKind] | None = None


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def _filename_rel(project: Mapping[str, Any], filename: str) -> str:
    rel = f"source/{filename}"
    if "/" in filename or "\\" in filename or rel not in whole_source_files(project):
        raise SourceFileChangeError("source_file_not_found", f"整本源文里没有这个文件：{filename}")
    return rel


def _entries(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = project.get("episodes")
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        raise SourceFileChangeError("ledger_invalid", "分集账本的形状异常，不能改动整本源文的文件")
    return list(raw)


def _spans(project: Mapping[str, Any], docs: list[SourceDoc]) -> dict[int, SourceSpan]:
    """能落进整本源文的切出集的原文范围（终点超出文件长度时已截到文件末尾）。"""
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


def _check_fingerprint(project: Mapping[str, Any], doc: SourceDoc) -> None:
    """文件在服务之外被改动过（已记录的指纹与当前文本不符）时拒绝。"""
    raw = project.get(SOURCE_FINGERPRINTS_KEY)
    recorded = raw.get(doc.rel_path) if isinstance(raw, Mapping) else None
    if isinstance(recorded, str) and recorded != compute_source_fingerprints([doc])[doc.rel_path]:
        raise SourceFileChangeError("source_changed", f"源文件在服务之外被改动过：{doc.rel_path}")


def _require_text(text: str) -> str:
    normalized = normalize_source_text(text)
    if not normalized.strip():
        raise SourceFileChangeError("source_text_empty", "源文件不能为空；要去掉这个文件请删除它")
    return normalized


# ---------------------------------------------------------------------------
# 受影响集
# ---------------------------------------------------------------------------


def _order_after(
    entries: Sequence[Mapping[str, Any]], change: _Change, impact_groups: Mapping[str, set[int]]
) -> list[int]:
    """改动后的播出顺序（集 ID），不含退下与移除的集；退下的集另接在末尾。"""
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
    """受影响集清单，以及改动后的播出顺序（不含退下与移除的集）。"""
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
# 提交
# ---------------------------------------------------------------------------


def _restore_file(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(content)


def _remember(path: Path, undo: ExitStack) -> None:
    """登记 ``path`` 现在的内容，撤销时恢复。"""
    undo.callback(_restore_file, path, path.read_bytes() if path.is_file() and not path.is_symlink() else None)


def _write_text(path: Path, text: str, undo: ExitStack) -> None:
    if path.is_symlink():
        raise SourceFileChangeError("episode_source_symlink", f"文件是符号链接，拒绝写入：{path.name}")
    _remember(path, undo)
    path.write_text(text, encoding="utf-8", newline="\n")


def _sync_snapshots(project_dir: Path, project: Mapping[str, Any], docs: list[SourceDoc], undo: ExitStack) -> None:
    snapshot_dir = project_dir / SOURCE_SNAPSHOTS_DIR
    if snapshot_dir.is_dir() and not snapshot_dir.is_symlink():
        for path in snapshot_dir.iterdir():
            if path.is_file() and not path.is_symlink():
                _remember(path, undo)
    for doc in docs:
        _remember(snapshot_dir / Path(doc.rel_path).name, undo)
    sync_source_snapshots(project_dir, project, {doc.rel_path: doc.text for doc in docs})


def _record_fingerprints(project: dict[str, Any], docs: list[SourceDoc], touched: Iterable[str]) -> None:
    """改动过的文件：已记录指纹的更新为新文本的指纹；移出整本源文的去掉记录。"""
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
            path.unlink()
    _sync_snapshots(project_dir, project, docs, undo)
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
    """在项目锁内推演改动、算受影响集；确认过（或没有受影响的集）时提交。``dry_run`` 时只算不写。"""
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
# 命令
# ---------------------------------------------------------------------------


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _purge_raw_backups(project_dir: Path, rel: str, undo: ExitStack) -> None:
    """去掉文件的原件备份（``source/raw/`` 下同主干的文件）：内容已换，旧原件不再对应这个文件。"""
    raw_dir = project_dir / "source" / "raw"
    if raw_dir.is_symlink() or not raw_dir.is_dir():
        return
    stem = Path(rel).stem
    for path in raw_dir.iterdir():
        if path.stem == stem and path.is_file() and not path.is_symlink():
            _remember(path, undo)
            path.unlink()


def _plan_rewrite(
    filename: str, text: str, *, replace: bool, source_kind: SourceKind | None = None
) -> Callable[[Path, dict[str, Any]], _Change]:
    def plan(project_dir: Path, project: dict[str, Any]) -> _Change:
        rel = _filename_rel(project, filename)
        docs = discover_sources(project_dir, project)
        _check_fingerprint(project, _require_doc(docs, rel))
        remaps = remap_for_edit(docs, _spans(project, docs), rel=rel, new_text=text)
        kind_change = None
        if (
            source_kind is not None
            and source_kind_applies(project)
            and whole_source_file_kind(project, rel) != source_kind
        ):
            kind_change = (rel, source_kind)

        def commit(p: dict[str, Any], undo: ExitStack) -> None:
            _write_text(project_dir / rel, text, undo)
            if replace:
                _purge_raw_backups(project_dir, rel, undo)
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
    """编辑整本源文文件 ``source/<filename>`` 的全文：按改动前后的对齐重映射触及它的切出集。"""
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
    """用新内容替换整本源文文件 ``source/<filename>``：保留它的位置与文件名，类型缺省时不变。

    重映射与编辑相同。改了类型时，起点在这个文件、已有脚本规划的切出集归入「因改类型而 stale 的脚本规划」。
    原件备份随之去掉。
    """
    normalized = _require_text(text)
    operation = {"action": "replace", "file": filename, "text": _text_digest(normalized), "kind": source_kind}
    plan = _plan_rewrite(filename, normalized, replace=True, source_kind=source_kind)
    return _run(pm, project_name, operation, plan, revision=revision, dry_run=dry_run)


def delete_whole_source_file(
    pm: ProjectManager, project_name: str, filename: str, *, revision: str | None = None, dry_run: bool = False
) -> SourceFileChangeOutcome:
    """删除整本源文文件 ``source/<filename>``：等同于删掉它的全部文字，再移出整本源文清单、删掉文件与原件备份。

    文件在服务之外被改动过时同样可以删除。
    """

    def plan(project_dir: Path, project: dict[str, Any]) -> _Change:
        rel = _filename_rel(project, filename)
        docs = discover_sources(project_dir, project)
        spans = _spans(project, docs)
        if any(doc.rel_path == rel for doc in docs):
            remaps = remap_for_delete(docs, spans, rel=rel)
        else:
            remaps = [RangeRemap(episode=e, before=s, after=s, text_changed=False) for e, s in spans.items()]

        def commit(p: dict[str, Any], undo: ExitStack) -> None:
            remove_whole_source_file(p, rel)
            path = project_dir / rel
            if path.is_file() and not path.is_symlink():
                _remember(path, undo)
                path.unlink()
            _purge_raw_backups(project_dir, rel, undo)

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
    """把整本源文文件 ``source/<filename>`` 在清单里上移或下移一位。

    其中的切出集在播出顺序里整块跟着走，不标 stale；跨过被移动文件边界的集只保留起点所在文件里仍相连的部分。
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
                _check_fingerprint(project, doc)
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


def insert_whole_source_file(
    pm: ProjectManager,
    project_name: str,
    write: Callable[[Path, ExitStack], str],
    *,
    index: int | None,
    source_kind: SourceKind | None = None,
    revision: str | None = None,
) -> tuple[SourceFileChangeOutcome, str | None]:
    """在整本源文清单第 ``index`` 位插入一个新文件（缺省接在末尾），返回结果与登记的项目相对路径。

    ``write(source_dir, undo)`` 在确认之后写出新文件并返回它的项目相对路径，写入的撤销登记进 ``undo``。插入处落在一个
    跨文件的集内部时，新文件归这一集，这一集的原文有变化；否则不动任何集，直接执行。
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
# 确认清单
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
    """把受影响集清单按类渲染成确认文本：集以标题或播出位置指称。Web 确认框只呈现这份文本。"""

    def name(episode: int) -> str:
        title = episode_title(project, episode)
        if title:
            return title
        position = episode_position(project, episode)
        return translate("episode_position_name", position=position) if position else translate("episode_unlisted_name")

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
    "delete_whole_source_file",
    "edit_whole_source_file",
    "insert_whole_source_file",
    "move_whole_source_file",
    "render_source_file_impact_text",
    "replace_whole_source_file",
]
