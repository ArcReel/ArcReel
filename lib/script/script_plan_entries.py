"""Project script-plan entries into formal-script entries.

Content confirmation materializes the whole plan (``ScriptGenerator.materialize_script_plan``); legacy migration
uses the same projection to backfill formal scripts and fields. Both select variants, normalize entries, and project content here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from lib.script.script_models import NarrationScriptPlanDraft, ReferenceScriptPlanDraft
from lib.script.script_skeleton import SKELETONS

#: script_plan variants: drama / narration (by content_mode) + reference_video (by project generation mode,
#: across content_mode). Determine plan filenames and validation models; all share content confirmation.
#: ``lib.script.script_review`` re-exports these, never defining duplicates.
ScriptPlanKind = Literal["drama", "narration", "reference_video"]


@dataclass(frozen=True, slots=True)
class ScriptPlanVariant:
    """All values that branch by one script-plan variant."""

    #: Script skeleton kind (a key in ``lib.script.script_skeleton.SKELETONS``).
    skeleton_kind: str
    #: Entry-array key in this variant's intermediate, distinct from script-side ``skeleton_kind``:
    #: reference plans use ``units``, while scripts use ``video_units``.
    plan_items_key: str
    #: Draft model for normalizing intermediate entries on read. Drama uses ``None``, having no draft model
    #: and consuming raw dicts.
    draft_model: type[BaseModel] | None
    #: Fields **exclusively retained** when projecting plan entries to script content; ``None`` passes through whole entries.
    script_fields: tuple[str, ...] | None = None


#: Script-plan variant table, the sole source of variant-dependent decisions.
PLAN_VARIANTS: dict[str, ScriptPlanVariant] = {
    "drama": ScriptPlanVariant(skeleton_kind="scenes", plan_items_key="scenes", draft_model=None),
    "narration": ScriptPlanVariant(
        skeleton_kind="segments",
        plan_items_key="segments",
        draft_model=NarrationScriptPlanDraft,
    ),
    "reference_video": ScriptPlanVariant(
        skeleton_kind="video_units",
        plan_items_key="units",
        draft_model=ReferenceScriptPlanDraft,
        script_fields=("unit_id", "text", "duration_seconds", "source_text"),
    ),
}


def plan_variant(kind: ScriptPlanKind) -> ScriptPlanVariant:
    """Variant record; unknown variants fail loudly."""

    variant = PLAN_VARIANTS.get(kind)
    if variant is None:
        raise ValueError(f"未知的脚本规划变体: {kind!r}")
    return variant


def entry_id_field(kind: ScriptPlanKind) -> str:
    """This variant's script entry ID field name."""

    return SKELETONS[plan_variant(kind).skeleton_kind].id_field


def plan_entry_content(kind: ScriptPlanKind, entry: Mapping[str, object]) -> dict[str, object]:
    """Project plan entry → script content layer, excluding visuals.

    Pass drama/narration through whole (drama ``scene_description`` becomes the formal script's visual-authoring
    basis); reference-to-video retains only ``unit_id`` / ``text`` / ``duration_seconds`` / ``source_text``. Return
    a new dict without mutating input.
    """

    variant = plan_variant(kind)
    if variant.script_fields is not None:
        return {field: entry[field] for field in variant.script_fields if field in entry}
    return dict(entry)


def plan_entries_from_document(kind: ScriptPlanKind, document: object) -> list[dict[str, object]]:
    """Plan intermediate document → entries; return an empty list for invalid shape or failed normalization.

    Normalize narration/reference_video through their draft models (``model_validate`` + ``model_dump``),
    filling omitted default fields in legacy intermediates (reference ``source_text``, storyboard ``segment_break``).
    Drama has no draft model and returns dict entries unchanged.
    """

    variant = plan_variant(kind)
    items_key = variant.plan_items_key
    if not isinstance(document, Mapping):
        return []
    entries = document.get(items_key)
    if not isinstance(entries, list):
        return []
    raw_entries = [entry for entry in entries if isinstance(entry, dict)]
    draft_model = variant.draft_model
    if draft_model is None or not raw_entries:
        return raw_entries
    try:
        draft = draft_model.model_validate({items_key: raw_entries})
    except ValidationError:
        return []
    return [item.model_dump() for item in getattr(draft, items_key)]


__all__ = [
    "PLAN_VARIANTS",
    "ScriptPlanKind",
    "ScriptPlanVariant",
    "entry_id_field",
    "plan_entries_from_document",
    "plan_entry_content",
    "plan_variant",
]
