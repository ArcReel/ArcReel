"""集 ID 与播出顺序（ADR 0096）。

整数集号是一集的内部集 ID：贯穿路径、条目 ID（``E3S01``）、产物清单与任务记录，只分配、不复用。
分集账本 ``episodes[]`` 的数组顺序就是播出顺序；创作者以标题与播出位置认集。

项目历史最高号记在 ``project.json`` 顶层 :data:`EPISODE_ID_HIGH_WATER_KEY`，与账本分开持久化：
重置或删集把条目移出账本后它不回退，新集 ID 恒取它的下一个。``ProjectManager`` 每次写
``project.json`` 时把它抬到账本最大集 ID（:func:`raise_episode_id_high_water`），
任何写入路径登记的集 ID 都被它覆盖。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from lib.episode.episode_ledger import parse_positive_episode_num

#: ``project.json`` 顶层字段：项目内出现过的最大集 ID。
EPISODE_ID_HIGH_WATER_KEY = "episode_id_high_water"

#: 条目 ID 的集 ID 前缀（``E3S01`` / ``E3U02`` / ``E3G01``）。
_ITEM_ID_PREFIX_RE = re.compile(r"^E(\d+)(?=[A-Z]\d)")
#: 名字里的集 ID：``episode_3`` 形态（剧本、草稿目录、派生集文件及其留底、呈现与字幕目录），
#: 以及媒体文件名里的 ``E3S01`` / ``E3U02`` 前缀（``scene_E3S01.png``）。
_NAME_EPISODE_RE = re.compile(r"(?:^|[^A-Za-z0-9])episode[_-](\d+)(?![0-9])")
_NAME_ITEM_ID_RE = re.compile(r"(?:^|[^A-Za-z0-9])E(\d+)[A-Z]\d")


def _stored_high_water(project: Mapping[str, Any]) -> int:
    value = project.get(EPISODE_ID_HIGH_WATER_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _ledger_entries(project: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    raw = project.get("episodes")
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, Mapping):
            yield entry


def ledger_episode_ids(project: Mapping[str, Any]) -> list[int]:
    """账本里可解析的集 ID，按播出顺序。"""

    ids: list[int] = []
    for entry in _ledger_entries(project):
        episode_id = parse_positive_episode_num(entry.get("episode"))
        if episode_id is not None:
            ids.append(episode_id)
    return ids


def episode_id_high_water(project: Mapping[str, Any]) -> int:
    """项目历史最高号：已记录值与账本最大集 ID 取大。"""

    return max(_stored_high_water(project), *ledger_episode_ids(project), 0)


def raise_episode_id_high_water(project: dict[str, Any], *recorded: int) -> None:
    """把历史最高号抬到账本与 ``recorded`` 中的最大集 ID；只升不降，没有集 ID 时不写字段。"""

    high_water = max(episode_id_high_water(project), *recorded, 0)
    if high_water > _stored_high_water(project):
        project[EPISODE_ID_HIGH_WATER_KEY] = high_water


def allocate_episode_ids(project: dict[str, Any], count: int) -> list[int]:
    """为 ``count`` 个新集分配集 ID，并把历史最高号推进到最后一个。"""

    first = episode_id_high_water(project) + 1
    ids = list(range(first, first + count))
    if ids:
        raise_episode_id_high_water(project, ids[-1])
    return ids


def episode_position(project: Mapping[str, Any], episode_id: int) -> int | None:
    """一集在播出顺序中的位置（从 1 起）；不在账本里时返回 None。"""

    for index, candidate in enumerate(ledger_episode_ids(project), start=1):
        if candidate == episode_id:
            return index
    return None


def find_ledger_entry(project: Mapping[str, Any], episode_id: int) -> Mapping[str, Any] | None:
    for entry in _ledger_entries(project):
        if parse_positive_episode_num(entry.get("episode")) == episode_id:
            return entry
    return None


def following_ledger_entry(project: Mapping[str, Any], episode_id: int) -> Mapping[str, Any] | None:
    """播出顺序中紧接 ``episode_id`` 的那一集；它是末集或不在账本里时返回 None。"""

    entries = [entry for entry in _ledger_entries(project) if parse_positive_episode_num(entry.get("episode"))]
    for index, entry in enumerate(entries[:-1]):
        if parse_positive_episode_num(entry.get("episode")) == episode_id:
            return entries[index + 1]
    return None


def episode_title(project: Mapping[str, Any], episode_id: int) -> str:
    entry = find_ledger_entry(project, episode_id)
    title = entry.get("title") if entry is not None else None
    return title.strip() if isinstance(title, str) else ""


def default_episode_title(project: Mapping[str, Any], episode_id: int) -> str:
    """需要落一个非空标题、而模型或草稿没给时的兜底：账本标题，其次是当下的播出位置。

    标题会显示给创作者并回写账本，所以兜底里不出现集 ID。
    """

    title = episode_title(project, episode_id)
    if title:
        return title
    position = episode_position(project, episode_id)
    return f"第 {position} 集" if position is not None else "未命名集"


def episode_file_label(project: Mapping[str, Any], episode_id: int) -> str | None:
    """文件与文件夹名里的集指称：``{两位播出位置}_{集标题}``；集不在账本里时返回 None。"""

    position = episode_position(project, episode_id)
    if position is None:
        return None
    return f"{position:02d}_{default_episode_title(project, episode_id)}"


def describe_episode_for_agent(project: Mapping[str, Any], episode_id: int) -> str:
    """Agent 工具输出里的集指称：``《标题》（第 N 个，id=X）``。

    没有标题时省去书名号，不在账本里时只给 ID。
    """

    position = episode_position(project, episode_id)
    if position is None:
        return f"集（id={episode_id}）"
    title = episode_title(project, episode_id)
    locator = f"（第 {position} 个，id={episode_id}）"
    return f"《{title}》{locator}" if title else f"未命名集{locator}"


def item_id_within_episode(item_id: str) -> str:
    """条目 ID 去掉集 ID 前缀后的集内部分：``E3S01`` → ``S01``；没有前缀时原样返回。"""

    return _ITEM_ID_PREFIX_RE.sub("", item_id, count=1)


def item_id_episode(item_id: str) -> int | None:
    match = _ITEM_ID_PREFIX_RE.match(item_id)
    return int(match.group(1)) if match else None


def episode_item_ref(project: Mapping[str, Any], item_id: str) -> dict[str, Any] | None:
    """集页面之外指称条目所需的结构：所属集的标题与播出位置、集内 ID（``S01``）。

    界面按用户语言拼成「标题 · S01」，标题为空时用播出位置。ID 不带集前缀、或所属集已不在账本里
    时返回 None，Web 调用方以未命名集与集内 ID 兜底。
    """

    episode_id = item_id_episode(item_id)
    position = episode_position(project, episode_id) if episode_id is not None else None
    if episode_id is None or position is None:
        return None
    return {
        "episode_title": episode_title(project, episode_id),
        "episode_position": position,
        "item_id": item_id_within_episode(item_id),
    }


def episode_ids_in_names(names: Iterable[str]) -> set[int]:
    """名字（文件名、相对路径、资源 ID）里出现的集 ID。"""

    found: set[int] = set()
    for name in names:
        found.update(int(value) for value in _NAME_EPISODE_RE.findall(name))
        found.update(int(value) for value in _NAME_ITEM_ID_RE.findall(name))
    return {value for value in found if value > 0}


def episode_ids_in_record(value: object) -> set[int]:
    """持久化结构里的路径、资源键与显式集 ID；不解析提示词与创作正文。"""
    if isinstance(value, str):
        return episode_ids_in_names([value])
    found: set[int] = set()
    if isinstance(value, dict):
        found.update(episode_ids_in_names(str(key) for key in value))
        for key, item in value.items():
            if key in {"episode", "episode_id", "artifact_episode"}:
                episode_id = parse_positive_episode_num(item)
                if episode_id is not None:
                    found.add(episode_id)
            elif key not in {"prompt", "text", "source_text", "novel_text", "instructions", "description", "title"}:
                found.update(episode_ids_in_record(item))
    elif isinstance(value, list):
        for item in value:
            found.update(episode_ids_in_record(item))
    return found


def episode_ids_on_disk(project_dir: Path) -> set[int]:
    """项目目录里仍带集 ID 的文件与目录名（剧本、草稿目录、源文留底、媒体名前缀等）。"""

    project_dir = Path(project_dir)
    found = episode_ids_in_names(path.name for path in project_dir.rglob("*"))
    # 版本历史与宫格记录可以在媒体删去后独自留在盘上，文件名本身不一定带集 ID。
    for directory in (project_dir / "versions", project_dir / "grids"):
        for path in directory.rglob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            found.update(episode_ids_in_record(record))
    return found


__all__ = [
    "EPISODE_ID_HIGH_WATER_KEY",
    "allocate_episode_ids",
    "default_episode_title",
    "describe_episode_for_agent",
    "episode_file_label",
    "episode_id_high_water",
    "episode_ids_in_names",
    "episode_ids_in_record",
    "episode_ids_on_disk",
    "episode_item_ref",
    "episode_position",
    "episode_title",
    "find_ledger_entry",
    "following_ledger_entry",
    "item_id_episode",
    "item_id_within_episode",
    "ledger_episode_ids",
    "raise_episode_id_high_water",
]
