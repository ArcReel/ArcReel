"""登记集原文与整本源文文件的服务命令（ADR 0031、0097）。

- 整本源文：上传或新建的源文件接在清单末尾（:func:`register_whole_source_file`）；删除时移出清单。
- 逐集原文：每个文件登记为播出顺序末尾的一集自带原文的集，分配新集 ID（:func:`add_own_source_episode`）。
- 集页填写：无原文的集填上原文后转为自带原文的集；自带原文的集改写原文；切出集的集文件是派生物，
  不经这里改写（:func:`set_episode_source_text`）。
- 源文件类型（ADR 0036）：剧情演绎项目登记原文时一并记类型，缺省为小说；整本源文文件的类型可以单独改
  （:func:`set_whole_source_file_kind`）。改类型不改源文指纹，也不动账本；会让已有脚本规划的集判 stale 时
  先确认。

写源文与改 ``project.json`` 在同一把项目锁内完成（``ProjectManager.locked_source_registration``）。
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
    """集原文登记被拒；``code`` 是稳定的原因码，入口据此映射状态码与文案。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def register_whole_source_file(
    project: dict[str, Any], rel: str, *, index: int | None = None, source_kind: SourceKind | None = None
) -> bool:
    """把直接位于 ``source/`` 下的源文件登记进整本源文，返回是否新增。

    ``index`` 是登记后文件在清单里的下标，缺省时接在末尾。已登记、不是源文文件名（点 / 下划线前缀、
    扩展名不是 .txt / .md），或是账本里某一集的集文件时不登记。剧情演绎项目新登记的文件记
    ``source_kind``，缺省为小说；已登记的文件类型不变。
    """
    if not is_whole_source_file_path(rel) or is_episode_source_file(project, rel):
        return False
    added = append_whole_source_file(project, rel, index=index)
    if added:
        record_whole_source_file_kind(project, rel, source_kind)
    return added


def unregister_source_file(project: dict[str, Any], rel: str) -> None:
    """删除源文件前的登记处置：移出整本源文清单；是自带原文的集的集文件时，该集转为无原文。

    切出集的集文件由分集规划派生，拒绝删除。
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
    """把 ``path`` 恢复成 ``content``；``content`` 为 None 表示原先没有这个文件。"""
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(content)


def write_episode_source_file(
    project_dir: Path, episode: int, text: str, *, archive_existing: bool, undo: ExitStack
) -> None:
    """写集文件，并把撤销登记进 ``undo``。``archive_existing`` 时盘上已有的同名文件不是这一集的原文，先改名留底再写。"""
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
    """在 ``locked_source_registration`` 块内登记一集自带原文的集：分配新集 ID、写集文件、接在播出顺序末尾。

    盘上与新集 ID 同名、没有登记的 ``episode_N.txt`` 先改名留底，不被覆盖。剧情演绎项目的条目记
    ``source_kind``，缺省为小说。返回新集 ID。
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
    """集页写本集原文的结果。"""

    #: 已写入。为 False 时表示改类型还需要确认，原文与类型都没有写。
    applied: bool
    #: 本集写入后（未写入时为写入前）的原文来源。
    origin: SourceOrigin
    #: 会因改类型让脚本规划判 stale 的集：本集已有脚本规划且类型变了时为本集。
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
    """集页填写或改写本集原文：写集文件，无原文的集转为自带原文的集。

    切出集的集文件由账本派生，改动要走分集规划，这里拒绝。无原文的集在盘上恰有同名文件时，那份文件不是
    本集原文，先改名留底再写。剧情演绎项目同时记 ``source_kind``：缺省时保留已有类型，没有记录时记小说。
    改类型会让已开始制作的本集（已有脚本规划）的脚本规划判 stale 时，``confirm`` 为 False 就不写入。
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
    """``source/`` 下一个没有登记的文本文件；文件名不合法、不存在或已登记时拒绝。"""
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
        # Windows 上 "C:x.txt" 这类带盘符的单段名会拼到项目外，交给 safe_join 判越界
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
    """读源文件的原始字节与 UTF-8 文本；读不出或不是 UTF-8 时拒绝。"""
    try:
        raw = path.read_bytes()
        return raw, raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EpisodeSourceError("source_file_unreadable", f"源文件不是可读的 UTF-8 文本：{path.name}") from exc


def adopt_source_file_as_whole_source(pm: ProjectManager, project_name: str, filename: str) -> None:
    """把 ``source/`` 下没有登记的文件加入整本源文，接在清单末尾。"""
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
    """把 ``source/`` 下没有登记的文件用作一集的原文，返回这一集的集 ID。

    ``episode`` 为 None 时登记为播出顺序末尾的一集新的自带原文的集；否则填给这一集，这一集须是无原文的集。
    文件内容写进集文件后，原文件删除；登记没有写回时，集文件撤销、原文件按原字节放回。
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
        # 先删原文件再写集文件：原文件恰好就是目标集文件的同名文件时，写入不会被当成占位文件改名留底
        path.unlink()
        undo.callback(_restore_file, path, original)
        if entry is None or episode is None:
            return add_own_source_episode(project_dir, project, text, undo=undo)
        write_episode_source_file(project_dir, episode, text, archive_existing=True, undo=undo)
        entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.OWN.value
        record_episode_kind(project, entry, None)
        return episode


@dataclass(frozen=True)
class SourceKindChange:
    """改整本源文文件类型的结果。"""

    #: 类型确实变了（已写入，或待确认）。
    changed: bool
    #: 已写入。``changed`` 为 True 而它为 False 时，表示还需要确认。
    applied: bool
    #: 会因改类型让脚本规划判 stale 的切出集（原文范围起点在这个文件、已有脚本规划），按播出顺序。
    affected_episodes: list[int]


def set_whole_source_file_kind(
    pm: ProjectManager, project_name: str, filename: str, source_kind: SourceKind, *, confirm: bool
) -> SourceKindChange:
    """改整本源文文件 ``source/<filename>`` 的类型。

    只改清单项上的记录，不改源文指纹，也不动账本。会让已开始制作的集（已有脚本规划）的脚本规划判
    stale 时，``confirm`` 为 False 就不写入，只返回这些集；没有这类集时直接写入。只有剧情演绎项目有类型。
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
