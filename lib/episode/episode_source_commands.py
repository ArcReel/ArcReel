"""Episode/whole-source registration commands (ADRs 0031 and 0097).

- Whole sources append uploaded/new files (:func:`register_whole_source_file`), removing registrations on deletion.
- Per-episode uploads create standalone-source episodes last in broadcast order with new IDs (:func:`add_own_source_episode`).
- Episode pages turn sourceless into standalone episodes or rewrite standalone sources; split episode files are derived,
  never rewritten here (:func:`set_episode_source_text`).
- Source kinds (ADR 0036): drama registrations record kinds, default novel; whole-source kinds change separately
  (:func:`set_whole_source_file_kind`). Kinds never change fingerprints/ledgers; confirm before
  making existing episode plans stale.

Sources/``project.json`` write under shared project locks (``ProjectManager.locked_source_registration``).
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from lib.episode.episode_ids import allocate_episode_ids
from lib.episode.episode_ledger import SOURCE_TEXT_SUFFIXES, normalize_source_text, parse_positive_episode_num
from lib.episode.episode_paths import episode_script_relpath, episode_source_path, episode_source_relpath
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    SourceOrigin,
    append_whole_source_file,
    archive_episode_file_path,
    changed_outside_service,
    discover_sources,
    episode_source_origin,
    is_episode_source_file,
    is_whole_source_file_path,
    remove_whole_source_file,
    whole_source_files,
)
from lib.episode.source_kinds import (
    DEFAULT_SOURCE_KIND,
    SourceKind,
    entry_source_kind,
    episodes_from_whole_source_file,
    record_episode_kind,
    record_whole_source_file_kind,
    source_kind_applies,
    whole_source_file_kind,
)
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.script import script_review

if TYPE_CHECKING:
    from lib.project.project_manager import ProjectManager


class EpisodeSourceError(ValueError):
    """Rejected source registration; stable ``code`` maps entry-point statuses/wording."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def register_whole_source_file(
    project: dict[str, Any], rel: str, *, index: int | None = None, source_kind: SourceKind | None = None
) -> bool:
    """Register direct ``source/`` files as whole sources; return whether added.

    ``index`` is resulting manifest positions, default last. Skip existing registrations, invalid source names (dot/underscore
    prefixes, extensions other than .txt/.md), or any ledger episode filenames. New drama entries record
    ``source_kind``, default novel; existing kinds remain.
    """
    if not is_whole_source_file_path(rel) or is_episode_source_file(project, rel):
        return False
    added = append_whole_source_file(project, rel, index=index)
    if added:
        record_whole_source_file_kind(project, rel, source_kind)
    return added


def unregister_source_file(project: dict[str, Any], rel: str) -> None:
    """Before deletion, remove whole-source registrations; standalone episode files turn episodes sourceless.

    Reject deletion of planning-derived split episode files.
    """
    raw_episodes = project.get("episodes")
    owners = [
        entry
        for entry in (raw_episodes if isinstance(raw_episodes, list) else [])
        if isinstance(entry, dict)
        and (episode := parse_positive_episode_num(entry.get("episode"))) is not None
        and episode_source_relpath(episode) == rel
    ]
    if any(episode_source_origin(entry) is SourceOrigin.WHOLE_SOURCE for entry in owners):
        raise EpisodeSourceError("episode_source_derived", f"{rel} 是切出集的集文件，由分集规划派生，不能直接删除")
    remove_whole_source_file(project, rel)
    for entry in owners:
        if episode_source_origin(entry) is SourceOrigin.OWN:
            entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.NONE.value


def _restore_file(path: Path, content: bytes | None) -> None:
    """Restore ``path`` to ``content``; ``content`` None means previously absent."""
    if content is None:
        path.unlink(missing_ok=True)  # hard-delete: rollback -- remove files absent before transactions during undo
    else:
        path.write_bytes(content)


def write_episode_source_file(
    project_dir: Path, episode: int, text: str, *, archive_existing: bool, undo: ExitStack
) -> None:
    """Write episode sources/register ``undo``; ``archive_existing`` preserves same-name nonowned files by renaming first."""
    path = episode_source_path(project_dir, episode)
    if path.is_symlink():
        raise EpisodeSourceError("episode_source_symlink", f"集（id={episode}）的集文件是符号链接，拒绝写入")
    previous: bytes | None = None
    if archive_existing and path.exists():
        archived = archive_episode_file_path(path)
        path.rename(archived)
        undo.callback(archived.rename, path)
    elif path.exists():
        previous = path.read_bytes()
    undo.callback(_restore_file, path, previous)
    path.write_text(text, encoding="utf-8", newline="\n")


def _require_text(text: str) -> str:
    normalized = normalize_source_text(text)
    if not normalized.strip():
        raise EpisodeSourceError("episode_source_empty", "集原文不能为空")
    return normalized


def add_own_source_episode(
    project_dir: Path,
    project: dict[str, Any],
    text: str,
    *,
    undo: ExitStack,
    title: str = "",
    source_kind: SourceKind | None = None,
) -> int:
    """Within ``locked_source_registration``, allocate IDs/write files/append standalone episodes.

    Preserve unregistered same-ID ``episode_N.txt`` by renaming, never overwrite. Drama records
    ``source_kind``, default novel. Return new IDs.
    """
    normalized = _require_text(text)
    (episode,) = allocate_episode_ids(project, 1)
    write_episode_source_file(project_dir, episode, normalized, archive_existing=True, undo=undo)
    raw_episodes = project.get("episodes")
    episodes = list(raw_episodes) if isinstance(raw_episodes, list) else []
    entry: dict[str, Any] = {
        "episode": episode,
        "title": title,
        "script_file": episode_script_relpath(episode),
        SOURCE_ORIGIN_FIELD: SourceOrigin.OWN.value,
    }
    record_episode_kind(project, entry, source_kind)
    episodes.append(entry)
    project["episodes"] = episodes
    return episode


@dataclass(frozen=True)
class EpisodeSourceWrite:
    """Episode-page source-write results."""

    #: Written; False means kinds still require confirmation, with neither source nor kind written.
    applied: bool
    #: Source origins after writes, before writes if unapplied.
    origin: SourceOrigin
    #: Episodes stale from kind changes: current episodes when planned and types changed.
    affected_episodes: list[int]


def set_episode_source_text(
    pm: ProjectManager,
    project_name: str,
    episode: int,
    text: str,
    *,
    source_kind: SourceKind | None = None,
    confirm: bool = False,
) -> EpisodeSourceWrite:
    """Fill/rewrite episode sources, turning sourceless episodes standalone.

    Reject derived split files, which require planning. Same-name disk files for sourceless episodes are not owned,
    so archive first. Drama records ``source_kind``, preserving existing when omitted, novel if absent.
    If changed kinds stale existing script plans, false ``confirm`` prevents writes.
    """
    normalized = _require_text(text)
    project_dir = pm.get_project_path(project_name)
    with pm.locked_source_registration(project_name) as (_source_dir, project, undo):
        raw_episodes = project.get("episodes")
        entry = next(
            (
                item
                for item in (raw_episodes if isinstance(raw_episodes, list) else [])
                if isinstance(item, dict) and parse_positive_episode_num(item.get("episode")) == episode
            ),
            None,
        )
        if entry is None:
            raise EpisodeSourceError("episode_not_found", f"集（id={episode}）不在账本中")
        origin = episode_source_origin(entry)
        if origin is SourceOrigin.WHOLE_SOURCE:
            raise EpisodeSourceError(
                "episode_source_derived",
                f"集（id={episode}）切自整本源文，集原文由分集规划派生，不能直接改写",
            )
        affected = (
            [episode]
            if source_kind is not None
            and source_kind_applies(project)
            and (entry_source_kind(project, entry) or DEFAULT_SOURCE_KIND) != source_kind
            and (plan := script_review.script_plan_path(project_dir, project, episode)) is not None
            and plan.is_file()
            else []
        )
        if affected and not confirm:
            return EpisodeSourceWrite(applied=False, origin=origin, affected_episodes=affected)
        write_episode_source_file(
            project_dir, episode, normalized, archive_existing=origin is SourceOrigin.NONE, undo=undo
        )
        entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.OWN.value
        record_episode_kind(project, entry, source_kind)
    return EpisodeSourceWrite(applied=True, origin=SourceOrigin.OWN, affected_episodes=affected)


def _unregistered_source_path(project_dir: Path, project: dict[str, Any], filename: str) -> Path:
    """Unregistered direct ``source/`` text files; reject invalid/missing/registered names."""
    path = PurePosixPath(filename)
    rel = f"source/{filename}"
    if (
        len(path.parts) != 1
        or filename in {".", ".."}
        or filename.startswith(".")
        or "\\" in filename
        or path.suffix.lower() not in SOURCE_TEXT_SUFFIXES
    ):
        raise EpisodeSourceError("source_file_not_found", f"没有这个源文件：{filename}")
    try:
        # Windows drive names like C:x.txt can escape projects; safe_join validates boundaries.
        safe_join(project_dir / "source", filename)
    except PathTraversalError as exc:
        raise EpisodeSourceError("source_file_not_found", f"没有这个源文件：{filename}") from exc
    source_path = project_dir / "source" / filename
    if source_path.is_symlink() or not source_path.is_file():
        raise EpisodeSourceError("source_file_not_found", f"没有这个源文件：{filename}")
    if rel in whole_source_files(project) or is_episode_source_file(project, rel):
        raise EpisodeSourceError("source_file_registered", f"源文件已经登记过：{filename}")
    return source_path


def _read_source_bytes(path: Path) -> tuple[bytes, str]:
    """Read raw bytes/UTF-8 text, rejecting unreadable/non-UTF-8."""
    try:
        raw = path.read_bytes()
        return raw, raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EpisodeSourceError("source_file_unreadable", f"源文件不是可读的 UTF-8 文本：{path.name}") from exc


def adopt_source_file_as_whole_source(pm: ProjectManager, project_name: str, filename: str) -> None:
    """Append unregistered ``source/`` files to whole-source manifests."""
    project_dir = pm.get_project_path(project_name)
    with pm.locked_source_registration(project_name) as (_source_dir, project, _undo):
        path = _unregistered_source_path(project_dir, project, filename)
        rel = f"source/{filename}"
        if not is_whole_source_file_path(rel):
            raise EpisodeSourceError("source_name_not_whole_source", f"这个文件名不能用作整本源文：{filename}")
        _read_source_bytes(path)
        append_whole_source_file(project, rel)
        record_whole_source_file_kind(project, rel, None)


def adopt_source_file_as_episode(pm: ProjectManager, project_name: str, filename: str, episode: int | None) -> int:
    """Adopt unregistered ``source/`` files into episode sources, returning IDs.

    ``episode`` None creates standalone episodes last; otherwise targets must be sourceless.
    After writing episode sources, delete originals; uncommitted registrations undo sources and restore original bytes.
    """
    project_dir = pm.get_project_path(project_name)
    with pm.locked_source_registration(project_name) as (_source_dir, project, undo):
        path = _unregistered_source_path(project_dir, project, filename)
        original, decoded = _read_source_bytes(path)
        text = _require_text(decoded)
        entry: dict[str, Any] | None = None
        if episode is not None:
            raw_episodes = project.get("episodes")
            entry = next(
                (
                    item
                    for item in (raw_episodes if isinstance(raw_episodes, list) else [])
                    if isinstance(item, dict) and parse_positive_episode_num(item.get("episode")) == episode
                ),
                None,
            )
            if entry is None:
                raise EpisodeSourceError("episode_not_found", f"集（id={episode}）不在账本中")
            if episode_source_origin(entry) is not SourceOrigin.NONE:
                raise EpisodeSourceError("episode_source_present", f"集（id={episode}）已经有原文")
        # Delete originals before episode writes so same-name targets are not mistaken for placeholders requiring archives.
        path.unlink()  # hard-delete: derived -- adopted sources moved into episode files with NFC/unified newlines, full text preserved
        undo.callback(_restore_file, path, original)
        if entry is None or episode is None:
            return add_own_source_episode(project_dir, project, text, undo=undo)
        write_episode_source_file(project_dir, episode, text, archive_existing=True, undo=undo)
        entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.OWN.value
        record_episode_kind(project, entry, None)
        return episode


@dataclass(frozen=True)
class SourceKindChange:
    """Whole-source kind-change results."""

    #: Kinds actually changed, applied or awaiting confirmation.
    changed: bool
    #: Written; True ``changed`` with False here requires confirmation.
    applied: bool
    #: Planned splits stale from kinds, starting in changed files, in broadcast order.
    affected_episodes: list[int]


def set_whole_source_file_kind(
    pm: ProjectManager, project_name: str, filename: str, source_kind: SourceKind, *, confirm: bool
) -> SourceKindChange:
    """Change whole-source ``source/<filename>`` kinds.

    Manifest records only, never fingerprints/ledgers. Reject externally changed sources without ledger updates. If changes stale existing script plans,
    false ``confirm`` returns affected episodes without writes; otherwise write immediately. Drama only.
    """
    project_dir = pm.get_project_path(project_name)
    rel = f"source/{filename}"
    with pm.locked_source_registration(project_name) as (_source_dir, project, _undo):
        if not source_kind_applies(project):
            raise EpisodeSourceError("source_kind_not_applicable", "只有剧情演绎项目有源文件类型")
        if rel not in whole_source_files(project):
            raise EpisodeSourceError("source_file_not_found", f"整本源文里没有这个文件：{filename}")
        if whole_source_file_kind(project, rel) == source_kind:
            return SourceKindChange(changed=False, applied=False, affected_episodes=[])
        if any(
            doc.rel_path == rel and changed_outside_service(project_dir, project, doc)
            for doc in discover_sources(project_dir, project)
        ):
            raise EpisodeSourceError("source_changed_outside", f"源文件在服务之外被改动过：{filename}")
        affected = [
            episode
            for episode in episodes_from_whole_source_file(project, rel)
            if (plan := script_review.script_plan_path(project_dir, project, episode)) is not None and plan.is_file()
        ]
        if affected and not confirm:
            return SourceKindChange(changed=True, applied=False, affected_episodes=affected)
        record_whole_source_file_kind(project, rel, source_kind)
    return SourceKindChange(changed=True, applied=True, affected_episodes=affected)


__all__ = [
    "EpisodeSourceError",
    "EpisodeSourceWrite",
    "SourceKindChange",
    "add_own_source_episode",
    "adopt_source_file_as_episode",
    "adopt_source_file_as_whole_source",
    "register_whole_source_file",
    "set_episode_source_text",
    "set_whole_source_file_kind",
    "unregister_source_file",
    "write_episode_source_file",
]
