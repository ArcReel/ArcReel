"""提示词编写的对象选择：本次为哪些条目写哪些视觉层字段，显式重写会替换哪些已有内容。

补缺以整份视觉层字段为单位：分镜类条目的 ``image_prompt`` / ``video_prompt`` 各自判断，已有的保留，
只补缺失的那一份，不看结构化提示词内部的空字段；参考生视频单元的正文即视觉层，按待编写标记展开，
正文非空也照常改写，标记清除后只有显式重写才改写。点名条目只划定范围，不等于授权覆盖。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lib.script.script_models import PENDING_AUTHORING_FIELD

#: 各条目形态的视觉层字段。参考生视频单元的正文即其视觉层。
VISUAL_LAYER_FIELDS: dict[str, tuple[str, ...]] = {
    "segments": ("image_prompt", "video_prompt"),
    "scenes": ("image_prompt", "video_prompt"),
    "shots": ("image_prompt", "video_prompt"),
    "video_units": ("text",),
}


def visual_field_filled(value: Any) -> bool:
    """视觉层字段已有内容：非空白字符串，或非空的结构化提示词。"""
    return bool(value.strip() if isinstance(value, str) else value)


def visual_layer_complete(kind: str, item: Mapping[str, Any]) -> bool:
    """条目的视觉层字段都已写入内容。"""
    return all(visual_field_filled(item.get(field)) for field in VISUAL_LAYER_FIELDS[kind])


@dataclass(frozen=True, slots=True)
class AuthoringEntry:
    """一个编写条目：本次写回的视觉层字段，及其中会替换已有内容的字段。"""

    entry_id: str
    fields: tuple[str, ...]
    overwritten: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PromptAuthoringSelection:
    """一次提示词编写的对象（按剧本顺序），与范围内因视觉层已齐而不写的条目。"""

    entries: tuple[AuthoringEntry, ...]
    skipped: tuple[str, ...]

    @property
    def overwritten(self) -> tuple[AuthoringEntry, ...]:
        return tuple(entry for entry in self.entries if entry.overwritten)


def _fill_fields(kind: str, item: Mapping[str, Any]) -> tuple[str, ...]:
    if kind == "video_units":
        return VISUAL_LAYER_FIELDS[kind] if item.get(PENDING_AUTHORING_FIELD) is True else ()
    return tuple(field for field in VISUAL_LAYER_FIELDS[kind] if not visual_field_filled(item.get(field)))


def select_prompt_authoring(
    items: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    id_field: str,
    entry_ids: Iterable[str] = (),
    rewrite: bool = False,
) -> PromptAuthoringSelection:
    """按范围与模式选出编写对象。

    ``entry_ids`` 为空时范围是全部带待编写标记的条目，否则是这些条目（调用方先确认它们都在剧本里）。
    补缺只写缺失的视觉层字段；``rewrite`` 写范围内条目的全部视觉层字段，其中补缺本不会写、
    却已有内容的字段记为 ``overwritten``，即显式重写需要确认的丢失项。
    """
    requested = set(entry_ids)
    entries: list[AuthoringEntry] = []
    skipped: list[str] = []
    for item in items:
        entry_id = str(item[id_field])
        in_scope = entry_id in requested if requested else item.get(PENDING_AUTHORING_FIELD) is True
        if not in_scope:
            continue
        fill = _fill_fields(kind, item)
        if rewrite:
            overwritten = tuple(
                field
                for field in VISUAL_LAYER_FIELDS[kind]
                if field not in fill and visual_field_filled(item.get(field))
            )
            entries.append(AuthoringEntry(entry_id, VISUAL_LAYER_FIELDS[kind], overwritten))
        elif fill:
            entries.append(AuthoringEntry(entry_id, fill))
        else:
            skipped.append(entry_id)
    return PromptAuthoringSelection(entries=tuple(entries), skipped=tuple(skipped))


@dataclass(frozen=True, slots=True)
class PromptOverwrite:
    """显式重写将替换的已有视觉层内容。

    ``fingerprint`` 是读取时正式脚本的内容指纹，对外作 ``revision``：调用方认可覆盖时回传它，
    正式脚本此后又有变化即视为未认可，按新清单重新拒绝。
    """

    fingerprint: str | None
    entries: tuple[AuthoringEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.fingerprint,
            "entries": [{"id": entry.entry_id, "fields": list(entry.overwritten)} for entry in self.entries],
        }


class PromptOverwriteRequired(ValueError):
    """显式重写会替换已有视觉层内容，而调用方没有给出与当前正式脚本相符的覆盖令牌。"""

    def __init__(self, overwrite: PromptOverwrite) -> None:
        ids = "、".join(entry.entry_id for entry in overwrite.entries)
        super().__init__(f"显式重写会替换这些条目已有的视觉层内容，需先确认覆盖: {ids}")
        self.overwrite = overwrite


#: 丢失清单里视觉层字段的文案 key。
_FIELD_KEYS: dict[str, str] = {
    "image_prompt": "prompt_overwrite_field_image_prompt",
    "video_prompt": "prompt_overwrite_field_video_prompt",
    "text": "prompt_overwrite_field_text",
}


def render_prompt_overwrite_text(overwrite: Mapping[str, Any], translate: Callable[..., str]) -> str:
    """把 ``PromptOverwrite.to_dict()`` 渲染成丢失清单文本，Web 确认框与 Agent 回执同用这一份。"""
    entries: list[Mapping[str, Any]] = list(overwrite.get("entries") or ())
    separator = translate("script_overwrite_separator")
    rendered = [
        translate(
            "prompt_overwrite_entry",
            id=entry.get("id", ""),
            fields=separator.join(translate(_FIELD_KEYS[field]) for field in entry.get("fields") or ()),
        )
        for entry in entries
    ]
    return "\n".join(
        [
            translate("prompt_overwrite_summary", count=len(entries)),
            translate("prompt_overwrite_entries", entries=separator.join(rendered)),
        ]
    )


def prompt_overwrite_with_text(overwrite: Mapping[str, Any], translate: Callable[..., str]) -> dict[str, Any]:
    """覆盖清单附上渲染好的丢失清单文本（``text``）。"""
    return {**overwrite, "text": render_prompt_overwrite_text(overwrite, translate)}


__all__ = [
    "VISUAL_LAYER_FIELDS",
    "AuthoringEntry",
    "PromptAuthoringSelection",
    "PromptOverwrite",
    "PromptOverwriteRequired",
    "prompt_overwrite_with_text",
    "render_prompt_overwrite_text",
    "select_prompt_authoring",
    "visual_field_filled",
    "visual_layer_complete",
]
