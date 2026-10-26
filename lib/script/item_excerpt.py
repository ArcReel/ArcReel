"""分镜与视频单元在创作者面前的称呼素材：本集数组里的位置（从 1 起）与正文摘录。

条目 ID 只分配不复用，对创作者没有意义；界面按位置称呼条目，配上正文前十几个字区分。正文的取法与前端
``frontend/src/utils/episode-item.ts`` 的 ``itemExcerpt`` 同口径。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: 剧本条目数组与它的 ID 字段，四种骨架各一。
_ITEM_ARRAYS: tuple[tuple[str, str], ...] = (
    ("segments", "segment_id"),
    ("scenes", "scene_id"),
    ("shots", "shot_id"),
    ("video_units", "unit_id"),
)

_EXCERPT_LENGTH = 16
_MENTION = re.compile(r"@\[([^\]]*)\]")


def find_item(script: Mapping[str, Any], item_id: str) -> tuple[int, Mapping[str, Any]] | None:
    """条目在本集剧本里的位置（从 1 起）与条目本身；不在剧本里时为 None。"""
    for array_key, id_field in _ITEM_ARRAYS:
        items = script.get(array_key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items, start=1):
            if isinstance(item, Mapping) and item.get(id_field) == item_id:
                return index, item
    return None


def item_excerpt(item: Mapping[str, Any]) -> str:
    """正文前十几个字，空白折成一个空格，按码点截断，截断时以「…」收尾。"""
    body = " ".join(_body(item).split())
    return body if len(body) <= _EXCERPT_LENGTH else f"{body[:_EXCERPT_LENGTH]}…"


def position_snapshot(script: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    """删除记录存下的称呼快照 ``{"item_position", "item_excerpt"}``；条目不在剧本里时为空。"""
    found = find_item(script, item_id)
    if found is None:
        return {}
    position, item = found
    return {"item_position": position, "item_excerpt": item_excerpt(item)}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _image_prompt_scene(item: Mapping[str, Any]) -> str:
    prompt = item.get("image_prompt")
    if isinstance(prompt, Mapping):
        return _text(prompt.get("scene"))
    return _text(prompt)


def _body(item: Mapping[str, Any]) -> str:
    """条目正文：旁白取原文，剧情演绎取画面描述，广告/短片取口播，视频单元取正文。"""
    if "segment_id" in item:
        return _text(item.get("novel_text"))
    if "unit_id" in item:
        return _MENTION.sub(r"@\1", _text(item.get("text")))
    if "shot_id" in item:
        return _text(item.get("voiceover_text")) or _image_prompt_scene(item)
    return _text(item.get("scene_description")) or _image_prompt_scene(item) or _text(item.get("source_text"))


__all__ = ["find_item", "item_excerpt", "position_snapshot"]
