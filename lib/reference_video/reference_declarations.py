"""Lightweight logical-reference derivation shared by preview and execution."""

from __future__ import annotations

from lib.asset_types import asset_name_comparison_key
from lib.reference_video.keyframes import (
    keyframe_id_from_mention_name,
    keyframe_references_in_text,
)
from lib.reference_video.text_parser import extract_mentions, resolve_references, strip_speech_marks
from lib.script_models import ReferenceResource


def unit_reference_declarations(project: dict, unit: dict) -> tuple[ReferenceResource, ...]:
    """Return a unit's registered logical image references in first-mention order.

    A confirmed Video Unit Storyboard Sheet is always first. Keyframe mentions
    are valid only when the referenced keyframe belongs to the current unit.
    Other mentions resolve through the project's registered asset buckets.
    Unknown names are intentionally omitted so callers can surface them as
    non-blocking warnings.
    """

    raw_text = unit.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    owned_keyframes = keyframe_references_in_text(unit)
    references: list[ReferenceResource] = []
    seen: set[tuple[str, str]] = set()
    sheet = unit.get("storyboard_sheet")
    if isinstance(sheet, dict) and sheet.get("status") == "confirmed":
        unit_id = str(unit.get("unit_id") or "").strip()
        if unit_id:
            references.append(ReferenceResource(type="storyboard_sheet", name=unit_id))
            seen.add(("storyboard_sheet", asset_name_comparison_key(unit_id)))
    for name in extract_mentions(strip_speech_marks(text)):
        keyframe = owned_keyframes.get(name)
        if keyframe is not None:
            candidate = keyframe
        elif keyframe_id_from_mention_name(name) is not None:
            continue
        else:
            resolved, _missing = resolve_references([name], project)
            if not resolved:
                continue
            candidate = resolved[0]
        identity = (candidate.type, asset_name_comparison_key(candidate.name))
        if identity in seen:
            continue
        seen.add(identity)
        references.append(candidate)
    return tuple(references)
