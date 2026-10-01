"""集原文来源与整本源文的文件清单：由项目显式登记，不按 ``source/`` 目录内容推断（ADR 0031、0097）。

- 每个账本条目用 :data:`SOURCE_ORIGIN_FIELD` 记录集原文的来源：切自整本源文、自带原文、无原文。
  切自整本源文的集文件 ``source/episode_N.txt`` 是账本的派生物；自带原文的集文件就是该集的源文；
  无原文的集没有集文件，即使 ``source/`` 里恰好有同名文件也不算。
- ``project.json`` 顶层 :data:`WHOLE_SOURCE_FILES_KEY` 按创作者排定的顺序列出整本源文的文件，新文件
  接在末尾。``source/`` 里没有登记的文件不属于整本源文，也不是任何一集的原文。
- 接续规划的起点由账本推导：按源文位置（文件在清单中的先后，再按文件内偏移）排在最后的那个切出集的
  结尾，不另存游标。
- 登记过切出集的文件在 ``source/snapshots/`` 下保留一份规范化文本快照，是服务之外改动文件时的对齐基准。

本模块只读写 ``project.json`` 的内存形态与快照文件，不取锁；调用方在项目锁内使用。
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, TypeGuard

from lib.episode.episode_ledger import (
    SOURCE_TEXT_SUFFIXES,
    SourceDoc,
    is_derived_episode_name,
    normalize_source_text,
    parse_positive_episode_num,
    parse_source_range,
)
from lib.episode.episode_paths import episode_source_relpath

logger = logging.getLogger(__name__)

#: 账本条目字段：集原文的来源。
SOURCE_ORIGIN_FIELD = "source_origin"

#: ``project.json`` 顶层字段：整本源文的文件清单，元素形如 ``{"source_file": "source/novel.txt"}``。
WHOLE_SOURCE_FILES_KEY = "whole_source_files"

#: 规范化文本快照所在目录（相对项目根）。
SOURCE_SNAPSHOTS_DIR = "source/snapshots"


class SourceOrigin(StrEnum):
    """一集的集原文来源。"""

    #: 由分集规划从整本源文切出一段范围。
    WHOLE_SOURCE = "whole_source"
    #: 创作者逐集上传或直接填写的一份原文，集文件就是该集的源文。
    OWN = "own"
    #: 没有原文。
    NONE = "none"


SOURCE_ORIGINS: tuple[str, ...] = tuple(origin.value for origin in SourceOrigin)


def episode_source_origin(entry: Mapping[str, Any]) -> SourceOrigin:
    """条目记录的集原文来源。

    字段缺失或取值非法时，带 ``source_range`` 字段（不论结构是否完整）的按切自整本源文处理，其余按无原文
    处理；不看磁盘上有没有集文件。结构损坏的原文范围因此仍落在切出集的校验里，不会被当成无原文放过。
    """
    raw = entry.get(SOURCE_ORIGIN_FIELD)
    if isinstance(raw, str) and raw in SOURCE_ORIGINS:
        return SourceOrigin(raw)
    return SourceOrigin.WHOLE_SOURCE if entry.get("source_range") is not None else SourceOrigin.NONE


def is_cut_episode(entry: Mapping[str, Any]) -> bool:
    """条目是否为切自整本源文的集（含没有原文范围记录的旧拆分流程存量集）。"""
    return episode_source_origin(entry) is SourceOrigin.WHOLE_SOURCE


def legacy_cut_episode_ids(project: Mapping[str, Any]) -> list[int]:
    """切自整本源文、却没有原文范围记录的集 ID（旧拆分流程的存量集），按播出顺序。

    这类集照常消费，但无法据以续接规划：分集规划遇到它们拒绝执行，要重新规划须从第 1 集起。
    自带原文与无原文的集本来就不占用整本源文，不在此列。
    """
    ids: list[int] = []
    for entry in _entries(project):
        episode_id = parse_positive_episode_num(entry.get("episode"))
        if episode_id is not None and is_cut_episode(entry) and parse_source_range(entry) is None:
            ids.append(episode_id)
    return ids


def first_cut_episode_id(project: Mapping[str, Any]) -> int | None:
    """播出顺序中第一个切出集的集 ID；从它起重置即全量重置。"""
    return next(
        (parse_positive_episode_num(entry.get("episode")) for entry in _entries(project) if is_cut_episode(entry)),
        None,
    )


def episode_entry(project: Mapping[str, Any], episode_id: int) -> Mapping[str, Any] | None:
    """账本里集 ID 为 ``episode_id`` 的第一个条目。"""
    return next(
        (entry for entry in _entries(project) if parse_positive_episode_num(entry.get("episode")) == episode_id),
        None,
    )


def is_episode_source_file(project: Mapping[str, Any], rel: str) -> bool:
    """``rel`` 是否为账本里某一集的集文件 ``source/episode_N.txt``（派生物或自带原文）。

    无原文的集没有集文件，盘上与它同名的文件不算。
    """
    return any(
        (episode_id := parse_positive_episode_num(entry.get("episode"))) is not None
        and rel == episode_source_relpath(episode_id)
        and episode_source_origin(entry) is not SourceOrigin.NONE
        for entry in _entries(project)
    )


def _entries(project: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    raw = project.get("episodes")
    return [entry for entry in raw if isinstance(entry, Mapping)] if isinstance(raw, list) else []


# ---------------------------------------------------------------------------
# 整本源文的文件清单
# ---------------------------------------------------------------------------


def is_whole_source_file_path(value: object) -> TypeGuard[str]:
    """直接位于 ``source/`` 下、扩展名合法、非点 / 下划线前缀的项目相对 POSIX 路径。

    ``episode_N.txt`` 留给集文件：切出集的集文件由规划按集 ID 写出，整本源文用这个名字会被覆盖。
    """
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        len(path.parts) == 2
        and path.parts[0] == "source"
        and not path.name.startswith((".", "_"))
        and path.suffix.lower() in SOURCE_TEXT_SUFFIXES
        and not is_derived_episode_name(path.name)
    )


def whole_source_files(project: Mapping[str, Any]) -> list[str]:
    """整本源文的文件（项目相对路径），按清单顺序；形状非法或重复的元素跳过。"""
    raw = project.get(WHOLE_SOURCE_FILES_KEY)
    files: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        rel = item.get("source_file") if isinstance(item, Mapping) else None
        if is_whole_source_file_path(rel) and rel not in files:
            files.append(rel)
    return files


def append_whole_source_file(project: dict[str, Any], rel: str, *, index: int | None = None) -> bool:
    """把文件登记进整本源文清单；已在清单里时不动，返回是否新增。

    ``index`` 是登记后文件在 :func:`whole_source_files` 里的下标，缺省或超出末尾时接在末尾，负数按 0 处理。
    """
    if not is_whole_source_file_path(rel):
        raise ValueError(f"整本源文的文件须直接位于 source/ 下且为 .txt / .md：{rel}")
    files = whole_source_files(project)
    if rel in files:
        return False
    raw = project.get(WHOLE_SOURCE_FILES_KEY)
    items = list(raw) if isinstance(raw, list) else []
    position = len(items)
    if index is not None and index < len(files):
        # 清单里可能夹着形状非法或重复的元素，按合法文件定位到原始列表里的插入处
        anchor = files[max(index, 0)]
        position = next(
            i for i, item in enumerate(items) if isinstance(item, Mapping) and item.get("source_file") == anchor
        )
    items.insert(position, {"source_file": rel})
    project[WHOLE_SOURCE_FILES_KEY] = items
    return True


def remove_whole_source_file(project: dict[str, Any], rel: str) -> bool:
    """从整本源文清单移除文件，返回是否移除。"""
    raw = project.get(WHOLE_SOURCE_FILES_KEY)
    if not isinstance(raw, list):
        return False
    kept = [item for item in raw if not (isinstance(item, Mapping) and item.get("source_file") == rel)]
    if len(kept) == len(raw):
        return False
    project[WHOLE_SOURCE_FILES_KEY] = kept
    return True


def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("源文件读取失败，按缺失处理：%s: %s", path, exc)
        return None


def discover_sources(project_dir: Path, project: Mapping[str, Any]) -> list[SourceDoc]:
    """整本源文的各个文件，按清单顺序给出规范化全文。

    读不到的文件（不存在、符号链接、非 UTF-8）跳过；``source/`` 目录本身是符号链接或目录联接时视为没有源文。
    """
    source_dir = project_dir / "source"
    if source_dir.is_symlink() or source_dir.is_junction() or not source_dir.is_dir():
        return []
    docs: list[SourceDoc] = []
    for rel in whole_source_files(project):
        path = project_dir / rel
        if path.is_symlink() or not path.is_file():
            continue
        text = _read_text_or_none(path)
        if text is not None:
            docs.append(SourceDoc(rel_path=rel, text=normalize_source_text(text)))
    return docs


# ---------------------------------------------------------------------------
# 由账本推导的规划起点
# ---------------------------------------------------------------------------


def planning_start(project: Mapping[str, Any], docs: list[SourceDoc]) -> tuple[str, int] | None:
    """接续规划的起点：按源文位置排在最后的切出集的结尾；还没有切出集时是第一个文件的开头。

    原文范围所在文件不在 ``docs`` 里的切出集不参与推导；文件按 NFC 归一后的路径对应。整本源文没有文件时返回 None。
    """
    order = {unicodedata.normalize("NFC", doc.rel_path): index for index, doc in enumerate(docs)}
    last: tuple[int, int, str] | None = None
    for entry in _entries(project):
        if not is_cut_episode(entry):
            continue
        coords = parse_source_range(entry)
        index = None if coords is None else order.get(unicodedata.normalize("NFC", coords[0]))
        if coords is None or index is None:
            continue
        _rel, _start, end = coords
        position = (index, end, docs[index].rel_path)
        if last is None or position[:2] > last[:2]:
            last = position
    if last is not None:
        return last[2], last[1]
    return (docs[0].rel_path, 0) if docs else None


@dataclass(frozen=True)
class CutPlacement:
    """一个切出集在整本源文里的位置：``docs`` 里的文件下标与文件内的 ``[start, end)``。"""

    episode: int
    file_index: int
    start: int
    end: int

    @property
    def position(self) -> tuple[int, int]:
        """按源文位置排序的键：文件先后，再按文件内起点。"""
        return self.file_index, self.start


def cut_episode_placements(project: Mapping[str, Any], docs: list[SourceDoc]) -> dict[int, CutPlacement]:
    """能落进整本源文的切出集，按集 ID 索引。

    原文范围所在文件不在 ``docs`` 里、起点越界的不落位；文件按 NFC 归一后的路径对应。同一文件里按起点排序，
    与前一集重叠的不落位。终点超出文件长度时截到文件末尾。「分集」视图与手工切分按同一份落位认集。
    """
    order = {unicodedata.normalize("NFC", doc.rel_path): index for index, doc in enumerate(docs)}
    per_file: dict[int, list[CutPlacement]] = {}
    for entry in _entries(project):
        episode = parse_positive_episode_num(entry.get("episode"))
        coords = parse_source_range(entry)
        if episode is None or coords is None or not is_cut_episode(entry):
            continue
        rel, start, end = coords
        index = order.get(unicodedata.normalize("NFC", rel))
        if index is None or start < 0 or end < start or start > len(docs[index].text):
            continue
        per_file.setdefault(index, []).append(
            CutPlacement(episode=episode, file_index=index, start=start, end=min(end, len(docs[index].text)))
        )
    placed: dict[int, CutPlacement] = {}
    for items in per_file.values():
        cursor = 0
        for item in sorted(items, key=lambda p: (p.start, p.end)):
            if item.start < cursor or item.episode in placed:
                continue
            placed[item.episode] = item
            cursor = item.end
    return placed


def unsplit_range_ending_at(
    placements: Mapping[int, CutPlacement], *, file_index: int, end: int
) -> tuple[int, int] | None:
    """文件里以 ``end`` 为终点的那段未切分原文 ``[start, end)``：从前面最近的切出集结尾（没有时从文件开头）起。

    ``end`` 落在某个切出集的原文里时返回 None。
    """
    in_file = [p for p in placements.values() if p.file_index == file_index]
    if any(p.start < end < p.end for p in in_file):
        return None
    start = max((p.end for p in in_file if p.end <= end), default=0)
    return start, end


def cut_insert_index(
    entries: Sequence[object], placements: Mapping[int, CutPlacement], position: tuple[int, int]
) -> int:
    """源文位置为 ``position`` 的新切出集在账本里的插入下标。

    排在按源文位置前面最近的切出集之后；前面没有切出集时排在后面最近的切出集之前；一个落位的切出集都没有时
    排在最后一个切出集之后，账本里还没有切出集时排在末尾。
    """
    ordered = sorted(placements.values(), key=lambda p: p.position)
    before = [p for p in ordered if p.position < position]
    after = [p for p in ordered if p.position >= position]

    def _index(episode: int) -> int:
        return next(
            i
            for i, entry in enumerate(entries)
            if isinstance(entry, Mapping) and parse_positive_episode_num(entry.get("episode")) == episode
        )

    if before:
        return _index(before[-1].episode) + 1
    if after:
        return _index(after[0].episode)
    return next(
        (
            i + 1
            for i in range(len(entries) - 1, -1, -1)
            if isinstance(entry := entries[i], Mapping) and is_cut_episode(entry)
        ),
        len(entries),
    )


def unplanned_text_remains(project: Mapping[str, Any], docs: list[SourceDoc]) -> bool:
    """规划起点之后是否还有非空白的原文。"""
    start = planning_start(project, docs)
    if start is None:
        return False
    rel, offset = start
    index = next(i for i, doc in enumerate(docs) if doc.rel_path == rel)
    if docs[index].text[offset:].strip():
        return True
    return any(doc.text.strip() for doc in docs[index + 1 :])


def archive_episode_file_path(path: Path) -> Path:
    """集文件的留底路径：下划线前缀 + ``.bak`` 尾缀，同名时追加序号。

    下划线前缀与 ``.bak`` 尾缀让留底文件既不是整本源文文件名，也不匹配 ``episode_N.txt``。
    """
    base = f"_{path.name}"
    candidate = path.with_name(f"{base}.bak")
    index = 1
    # exists() 对悬空符号链接返回 False，留底路径本身可能就是悬空链接，须一并视为占用
    while candidate.exists() or candidate.is_symlink():
        candidate = path.with_name(f"{base}.{index}.bak")
        index += 1
    return candidate


# ---------------------------------------------------------------------------
# 规范化文本快照
# ---------------------------------------------------------------------------


def source_snapshot_path(project_dir: Path, rel: str) -> Path:
    """整本源文文件 ``rel`` 的快照路径。"""
    return project_dir / SOURCE_SNAPSHOTS_DIR / PurePosixPath(rel).name


def cut_episode_source_files(project: Mapping[str, Any]) -> list[str]:
    """登记过切出集的整本源文文件，按首次出现的播出顺序去重。"""
    files: list[str] = []
    for entry in _entries(project):
        coords = parse_source_range(entry) if is_cut_episode(entry) else None
        if coords is not None and is_whole_source_file_path(coords[0]) and coords[0] not in files:
            files.append(coords[0])
    return files


def sync_source_snapshots(project_dir: Path, project: Mapping[str, Any], texts: Mapping[str, str]) -> None:
    """让快照与账本一致：登记过切出集的文件写入 ``texts`` 里的规范化全文，其余快照删除。

    ``texts`` 里没有的文件保留已有快照不动。
    """
    wanted = cut_episode_source_files(project)
    snapshot_dir = project_dir / SOURCE_SNAPSHOTS_DIR
    for rel in wanted:
        text = texts.get(rel)
        if text is None:
            continue
        path = source_snapshot_path(project_dir, rel)
        if path.is_symlink():
            raise ValueError(f"源文快照不能是符号链接：{path.name}")
        if path.is_file() and _read_text_or_none(path) == text:
            continue
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
        return
    keep = {PurePosixPath(rel).name for rel in wanted}
    for path in snapshot_dir.iterdir():
        if path.name not in keep and (path.is_file() or path.is_symlink()):
            path.unlink(missing_ok=True)


__all__ = [
    "SOURCE_ORIGINS",
    "SOURCE_ORIGIN_FIELD",
    "SOURCE_SNAPSHOTS_DIR",
    "WHOLE_SOURCE_FILES_KEY",
    "CutPlacement",
    "SourceOrigin",
    "append_whole_source_file",
    "cut_episode_placements",
    "cut_episode_source_files",
    "discover_sources",
    "episode_entry",
    "episode_source_origin",
    "first_cut_episode_id",
    "is_cut_episode",
    "is_episode_source_file",
    "is_whole_source_file_path",
    "legacy_cut_episode_ids",
    "planning_start",
    "remove_whole_source_file",
    "source_snapshot_path",
    "sync_source_snapshots",
    "unplanned_text_remains",
    "whole_source_files",
]
