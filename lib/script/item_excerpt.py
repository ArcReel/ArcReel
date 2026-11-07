"""Creator-facing storyboard/unit labels: one-based episode-array positions and body excerpts.

Item IDs are allocated without reuse and carry no creator meaning; the UI identifies items by position plus the first
roughly dozen body characters. Extraction matches frontend ``frontend/src/utils/episode-item.ts`` ``itemExcerpt``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: Entry arrays and ID fields, one per skeleton.
_ITEM_ARRAYS: tuple[tuple[str, str], ...] = (
    ("segments", "segment_id"),
    ("scenes", "scene_id"),
    ("shots", "shot_id"),
    ("video_units", "unit_id"),
)

_EXCERPT_LENGTH = 16
_MENTION = re.compile(r"@\[([^\]]*)\]")


def find_item(script: Mapping[str, Any], item_id: str) -> tuple[int, Mapping[str, Any]] | None:
    """One-based position and item in this episode script, or None if absent."""
    for array_key, id_field in _ITEM_ARRAYS:
        items = script.get(array_key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items, start=1):
            if isinstance(item, Mapping) and item.get(id_field) == item_id:
                return index, item
    return None


def item_excerpt(item: Mapping[str, Any]) -> str:
    """First roughly dozen body characters, collapsing whitespace, truncating by code point with a final “…” when shortened."""
    body = " ".join(_body(item).split())
    return body if len(body) <= _EXCERPT_LENGTH else f"{body[:_EXCERPT_LENGTH]}…"


def position_snapshot(script: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    """Deletion-record label snapshot ``{"item_position", "item_excerpt"}``; empty when the item is absent."""
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
    """Item body: narration source, drama visuals, ad speech, or video-unit text."""
    if "segment_id" in item:
        return _text(item.get("novel_text"))
    if "unit_id" in item:
        return _MENTION.sub(r"@\1", _text(item.get("text")))
    if "shot_id" in item:
        return _text(item.get("voiceover_text")) or _image_prompt_scene(item)
    return _text(item.get("scene_description")) or _image_prompt_scene(item) or _text(item.get("source_text"))


__all__ = ["find_item", "item_excerpt", "position_snapshot"]
