"""分镜与视频单元的编号：只分配、不复用（参照 ``docs/adr/0096`` 的集 ID）。

条目 ID 形如 ``E3S05`` / ``E3U5``：集 ID 前缀 + 字母（分镜族 ``S``，参考生视频单元 ``U``）+ 主序号，
存量拆分条目还可能带 ``_k`` 后缀。每集用过的最大主序号记在 ``project.json`` 该集账本条目的
:data:`ITEM_ID_HIGH_WATER_FIELD`，只升不降；新条目一律从它往后取号，两种字母共用一个序号线。

剧本每次落盘时，``ProjectManager`` 把它抬到剧本里的最大主序号；剧本之外先占号的写入点（脚本规划
草稿）经 ``ProjectManager.reserve_item_ids`` 取号。升级前的存量由迁移按剧本、版本历史、产物清单、
项目回收站与剪辑时间线五个来源写入。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

#: 集账本条目上的字段：本集条目编号用过的最大主序号。
ITEM_ID_HIGH_WATER_FIELD = "item_id_high_water"

#: 条目数组键 → 编号字母。
ITEM_ID_LETTERS: dict[str, str] = {"segments": "S", "scenes": "S", "shots": "S", "video_units": "U"}

ITEM_ID_RE = re.compile(r"^E(\d+)([SU])(\d+)(?:_\d+)?$")


def item_id_number(item_id: object, episode: int | None = None) -> int | None:
    """条目 ID 的主序号；不是条目 ID 形态，或集 ID 前缀不是 ``episode`` 时返回 None。"""

    if not isinstance(item_id, str) or (match := ITEM_ID_RE.match(item_id)) is None:
        return None
    if episode is not None and int(match.group(1)) != episode:
        return None
    return int(match.group(3))


def max_item_number(item_ids: Iterable[object], episode: int) -> int:
    """``item_ids`` 中属于 ``episode`` 的最大主序号，没有时为 0。"""

    return max((number for item_id in item_ids if (number := item_id_number(item_id, episode)) is not None), default=0)


def format_item_id(episode: int, kind: str, number: int) -> str:
    """分镜族为 ``E{集}S{序号:02d}``，参考生视频单元为 ``E{集}U{序号}``。"""

    letter = ITEM_ID_LETTERS[kind]
    return f"E{episode}S{number:02d}" if letter == "S" else f"E{episode}U{number}"


def stored_item_id_high_water(entry: Mapping[str, Any] | None) -> int:
    value = entry.get(ITEM_ID_HIGH_WATER_FIELD) if entry is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def raise_item_id_high_water(entry: dict[str, Any], *numbers: int) -> None:
    """把集条目上的最大号抬到 ``numbers`` 中的最大值；只升不降，没有更大的号时不写字段。"""

    high_water = max(stored_item_id_high_water(entry), *numbers, 0)
    if high_water > stored_item_id_high_water(entry):
        entry[ITEM_ID_HIGH_WATER_FIELD] = high_water


def episode_item_id_high_water(project: Mapping[str, Any], episode: int) -> int:
    """账本里该集记录的最大号；集不在账本里时为 0。"""

    episodes = project.get("episodes")
    for entry in episodes if isinstance(episodes, list) else []:
        if isinstance(entry, Mapping) and entry.get("episode") == episode:
            return stored_item_id_high_water(entry)
    return 0


class ItemIdIssuer:
    """按 ``floor``（已用过的最大主序号）往后依次发号。"""

    def __init__(self, episode: int, kind: str, floor: int) -> None:
        self.episode = episode
        self.kind = kind
        self.last = max(floor, 0)

    def issue(self) -> str:
        self.last += 1
        return format_item_id(self.episode, self.kind, self.last)


def reissue_item_ids(items: list[Any], id_field: str, issuer: ItemIdIssuer) -> dict[str, str]:
    """按数组顺序给每个条目发新号，就地改写；返回 ``{原 ID: 新 ID}``（原 ID 缺失或重复的不进映射）。"""

    renamed: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        previous = item.get(id_field)
        item[id_field] = issuer.issue()
        if isinstance(previous, str) and previous and previous not in renamed:
            renamed[previous] = item[id_field]
    return renamed


__all__ = [
    "ITEM_ID_HIGH_WATER_FIELD",
    "ITEM_ID_LETTERS",
    "ITEM_ID_RE",
    "ItemIdIssuer",
    "episode_item_id_high_water",
    "format_item_id",
    "item_id_number",
    "max_item_number",
    "raise_item_id_high_water",
    "reissue_item_ids",
    "stored_item_id_high_water",
]
