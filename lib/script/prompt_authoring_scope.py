"""Select prompt-authoring entries/visual fields and identify existing content replaced by explicit rewriting.

Gap filling operates on whole visual fields: ``image_prompt`` / ``video_prompt`` independently retain existing
values and fill missing ones, ignoring empty subfields inside structured prompts. Reference-unit bodies are visuals;
pending markers expand them even with nonempty bodies, and after clearing markers only explicit rewrite changes them. Naming entries limits selection, never authorizes overwrites.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lib.infra.content_digest import prefixed
from lib.script.script_models import PENDING_AUTHORING_FIELD

#: Visual fields by entry shape. Reference-unit bodies are their visual layer.
VISUAL_LAYER_FIELDS: dict[str, tuple[str, ...]] = {
    "segments": ("image_prompt", "video_prompt"),
    "scenes": ("image_prompt", "video_prompt"),
    "shots": ("image_prompt", "video_prompt"),
    "video_units": ("text",),
}


def visual_field_filled(value: Any) -> bool:
    """Visual field has content: a nonblank string or nonempty structured prompt."""
    return bool(value.strip() if isinstance(value, str) else value)


def visual_layer_complete(kind: str, item: Mapping[str, Any]) -> bool:
    """Every visual field in this entry has content."""
    return all(visual_field_filled(item.get(field)) for field in VISUAL_LAYER_FIELDS[kind])


@dataclass(frozen=True, slots=True)
class AuthoringEntry:
    """One authoring entry: visual fields written this time and fields replacing existing content."""

    entry_id: str
    fields: tuple[str, ...]
    overwritten: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PromptAuthoringSelection:
    """Authoring entries in script order, plus selected entries skipped because their visuals are complete."""

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
    """Select entries by range and mode.

    Empty ``entry_ids`` selects all pending-marked entries; otherwise select these IDs (caller verifies existence).
    Gap filling writes only missing visuals; ``rewrite`` writes every selected visual field. Fields already
    filled that gap-filling would leave alone become ``overwritten``, the loss items requiring explicit confirmation.
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
    """Existing visuals replaced by explicit rewriting.

    ``fingerprint`` is the read-time formal-script digest, exposed as ``revision`` and returned on overwrite consent.
    Any subsequent formal change invalidates consent, causing refusal with an updated loss list.
    """

    fingerprint: str | None
    entries: tuple[AuthoringEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": prefixed(self.fingerprint) if self.fingerprint is not None else None,
            "entries": [{"id": entry.entry_id, "fields": list(entry.overwritten)} for entry in self.entries],
        }


class PromptOverwriteRequired(ValueError):
    """Explicit rewrite replaces existing visuals without an overwrite token matching the current formal script."""

    def __init__(self, overwrite: PromptOverwrite) -> None:
        ids = "、".join(entry.entry_id for entry in overwrite.entries)
        super().__init__(f"显式重写会替换这些条目已有的视觉层内容，需先确认覆盖: {ids}")
        self.overwrite = overwrite


#: Copy keys for visual fields in loss inventories.
_FIELD_KEYS: dict[str, str] = {
    "image_prompt": "prompt_overwrite_field_image_prompt",
    "video_prompt": "prompt_overwrite_field_video_prompt",
    "text": "prompt_overwrite_field_text",
}


def render_prompt_overwrite_text(overwrite: Mapping[str, Any], translate: Callable[..., str]) -> str:
    """Render ``PromptOverwrite.to_dict()`` into loss-list text shared by Web confirmation and Agent receipts."""
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
    """Attach rendered loss-list ``text`` to the overwrite inventory."""
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
