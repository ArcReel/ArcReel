"""登记集原文与整本源文文件的服务命令（ADR 0031、0097）。

- 整本源文：上传或新建的源文件接在清单末尾（:func:`register_whole_source_file`）；删除时移出清单。
- 逐集原文：每个文件登记为播出顺序末尾的一集自带原文的集，分配新集 ID（:func:`add_own_source_episode`）。
- 集页填写：无原文的集填上原文后转为自带原文的集；自带原文的集改写原文；切出集的集文件是派生物，
  不经这里改写（:func:`set_episode_source_text`）。

写源文与改 ``project.json`` 在同一把项目锁内完成（``ProjectManager.locked_source_registration``）。
"""

from __future__ import annotations

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

if TYPE_CHECKING:
    from lib.project.project_manager import ProjectManager


class EpisodeSourceError(ValueError):
    """集原文登记被拒；``code`` 是稳定的原因码，入口据此映射状态码与文案。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def register_whole_source_file(project: dict[str, Any], rel: str, *, index: int | None = None) -> bool:
    """把直接位于 ``source/`` 下的源文件登记进整本源文，返回是否新增。

    ``index`` 是登记后文件在清单里的下标，缺省时接在末尾。已登记、不是源文文件名（点 / 下划线前缀、
    扩展名不是 .txt / .md），或是账本里某一集的集文件时不登记。
    """
    if not is_whole_source_file_path(rel) or is_episode_source_file(project, rel):
        return False
    return append_whole_source_file(project, rel, index=index)


def unregister_source_file(project: dict[str, Any], rel: str) -> None:
    """源文件被删除后的登记处置：移出整本源文清单；是自带原文的集的集文件时，该集转为无原文。"""
    remove_whole_source_file(project, rel)
    raw_episodes = project.get("episodes")
    for entry in raw_episodes if isinstance(raw_episodes, list) else []:
        if not isinstance(entry, dict) or episode_source_origin(entry) is not SourceOrigin.OWN:
            continue
        episode = parse_positive_episode_num(entry.get("episode"))
        if episode is not None and episode_source_relpath(episode) == rel:
            entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.NONE.value


def _write_episode_file(project_dir: Path, episode: int, text: str, *, archive_existing: bool) -> None:
    """写集文件。``archive_existing`` 时盘上已有的同名文件不是这一集的原文，先改名留底再写。"""
    path = episode_source_path(project_dir, episode)
    if path.is_symlink():
        raise EpisodeSourceError("episode_source_symlink", f"集（id={episode}）的集文件是符号链接，拒绝写入")
    if archive_existing and path.exists():
        path.rename(archive_episode_file_path(path))
    path.write_text(text, encoding="utf-8", newline="\n")


def _require_text(text: str) -> str:
    normalized = normalize_source_text(text)
    if not normalized.strip():
        raise EpisodeSourceError("episode_source_empty", "集原文不能为空")
    return normalized


def add_own_source_episode(project_dir: Path, project: dict[str, Any], text: str, *, title: str = "") -> int:
    """在已持项目锁的临界区内登记一集自带原文的集：分配新集 ID、写集文件、接在播出顺序末尾。

    盘上与新集 ID 同名、没有登记的 ``episode_N.txt`` 先改名留底，不被覆盖。返回新集 ID。
    """
    normalized = _require_text(text)
    (episode,) = allocate_episode_ids(project, 1)
    _write_episode_file(project_dir, episode, normalized, archive_existing=True)
    raw_episodes = project.get("episodes")
    episodes = list(raw_episodes) if isinstance(raw_episodes, list) else []
    episodes.append(
        {
            "episode": episode,
            "title": title,
            "script_file": episode_script_relpath(episode),
            SOURCE_ORIGIN_FIELD: SourceOrigin.OWN.value,
        }
    )
    project["episodes"] = episodes
    return episode


def set_episode_source_text(pm: ProjectManager, project_name: str, episode: int, text: str) -> SourceOrigin:
    """集页填写或改写本集原文：写集文件，无原文的集转为自带原文的集。

    切出集的集文件由账本派生，改动要走分集规划，这里拒绝。无原文的集在盘上恰有同名文件时，那份文件不是
    本集原文，先改名留底再写。返回写入后的来源。
    """
    normalized = _require_text(text)
    project_dir = pm.get_project_path(project_name)
    with pm.locked_source_registration(project_name) as (_source_dir, project):
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
        _write_episode_file(project_dir, episode, normalized, archive_existing=origin is SourceOrigin.NONE)
        entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.OWN.value
    return SourceOrigin.OWN


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
    source_path = project_dir / "source" / filename
    if source_path.is_symlink() or not source_path.is_file():
        raise EpisodeSourceError("source_file_not_found", f"没有这个源文件：{filename}")
    if rel in whole_source_files(project) or is_episode_source_file(project, rel):
        raise EpisodeSourceError("source_file_registered", f"源文件已经登记过：{filename}")
    return source_path


def _read_source_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EpisodeSourceError("source_file_unreadable", f"源文件不是可读的 UTF-8 文本：{path.name}") from exc


def adopt_source_file_as_whole_source(pm: ProjectManager, project_name: str, filename: str) -> None:
    """把 ``source/`` 下没有登记的文件加入整本源文，接在清单末尾。"""
    project_dir = pm.get_project_path(project_name)
    with pm.locked_source_registration(project_name) as (_source_dir, project):
        path = _unregistered_source_path(project_dir, project, filename)
        rel = f"source/{filename}"
        if not is_whole_source_file_path(rel):
            raise EpisodeSourceError("source_name_not_whole_source", f"这个文件名不能用作整本源文：{filename}")
        _read_source_text(path)
        append_whole_source_file(project, rel)


def adopt_source_file_as_episode(pm: ProjectManager, project_name: str, filename: str, episode: int | None) -> int:
    """把 ``source/`` 下没有登记的文件用作一集的原文，返回这一集的集 ID。

    ``episode`` 为 None 时登记为播出顺序末尾的一集新的自带原文的集；否则填给这一集，这一集须是无原文的集。
    文件内容写进集文件后，原文件删除。
    """
    project_dir = pm.get_project_path(project_name)
    with pm.locked_source_registration(project_name) as (_source_dir, project):
        path = _unregistered_source_path(project_dir, project, filename)
        original = _read_source_text(path)
        text = _require_text(original)
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
        try:
            if entry is None or episode is None:
                return add_own_source_episode(project_dir, project, text)
            _write_episode_file(project_dir, episode, text, archive_existing=True)
        except Exception:
            # 登记失败时锁块不写回 project.json，原文件也放回原处
            if not path.exists():
                path.write_text(original, encoding="utf-8", newline="")
            raise
        entry[SOURCE_ORIGIN_FIELD] = SourceOrigin.OWN.value
        return episode


__all__ = [
    "EpisodeSourceError",
    "add_own_source_episode",
    "adopt_source_file_as_episode",
    "adopt_source_file_as_whole_source",
    "register_whole_source_file",
    "set_episode_source_text",
    "unregister_source_file",
]
