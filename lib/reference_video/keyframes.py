"""Reference-video keyframe identity, inline tags, and unit lookup helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from lib.script_models import ReferenceResource

MAX_KEYFRAMES_PER_UNIT = 5
KEYFRAME_MENTION_PREFIX = "关键分镜 "
_PLACEHOLDER_RE = re.compile(r"\[\[关键分镜([1-5])\]\]")


def keyframe_id(unit_id: str, index: int) -> str:
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError("unit_id must be non-empty")
    if not 1 <= index <= MAX_KEYFRAMES_PER_UNIT:
        raise ValueError("keyframe index is out of range")
    return f"{unit_id}K{index:02d}"


def keyframe_mention(keyframe_id_value: str) -> str:
    return f"@[{KEYFRAME_MENTION_PREFIX}{keyframe_id_value}]"


def keyframe_id_from_mention_name(name: str) -> str | None:
    if not name.startswith(KEYFRAME_MENTION_PREFIX):
        return None
    value = name.removeprefix(KEYFRAME_MENTION_PREFIX).strip()
    return value or None


def materialize_keyframes(
    unit_id: str,
    text: str,
    descriptions: Sequence[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Replace ordered LLM placeholders with stable inline mentions."""

    if len(descriptions) > MAX_KEYFRAMES_PER_UNIT:
        raise ValueError(f"one video unit may contain at most {MAX_KEYFRAMES_PER_UNIT} keyframes")
    matches = list(_PLACEHOLDER_RE.finditer(text))
    expected = list(range(1, len(descriptions) + 1))
    actual = [int(match.group(1)) for match in matches]
    if actual != expected:
        raise ValueError(
            "keyframe placeholders must appear exactly once in order as "
            + ", ".join(f"[[关键分镜{i}]]" for i in expected)
        )
    if not descriptions and matches:
        raise ValueError("keyframe placeholders require matching keyframe descriptions")

    keyframes: list[dict[str, Any]] = []
    rendered = text
    for index, description in reversed(list(enumerate(descriptions, start=1))):
        stable_id = keyframe_id(unit_id, index)
        rendered = rendered.replace(f"[[关键分镜{index}]]", keyframe_mention(stable_id), 1)
        keyframes.append(
            {
                "keyframe_id": stable_id,
                "description": description,
                "image_path": None,
            }
        )
    keyframes.reverse()
    return rendered, keyframes


def iter_unit_keyframes(unit: dict[str, Any]) -> Iterable[dict[str, Any]]:
    value = unit.get("keyframes")
    if not isinstance(value, list):
        return ()
    return (item for item in value if isinstance(item, dict))


def find_keyframe(script: dict[str, Any], keyframe_id_value: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    units = script.get("video_units")
    if not isinstance(units, list):
        return None
    for unit in units:
        if not isinstance(unit, dict):
            continue
        for keyframe in iter_unit_keyframes(unit):
            if keyframe.get("keyframe_id") == keyframe_id_value:
                return unit, keyframe
    return None


def keyframe_references_in_text(unit: dict[str, Any]) -> dict[str, ReferenceResource]:
    """Return valid keyframes owned by a unit, keyed by their inline mention name."""

    result: dict[str, ReferenceResource] = {}
    for item in iter_unit_keyframes(unit):
        value = item.get("keyframe_id")
        if isinstance(value, str) and value:
            result[f"{KEYFRAME_MENTION_PREFIX}{value}"] = ReferenceResource(type="keyframe", name=value)
    return result

