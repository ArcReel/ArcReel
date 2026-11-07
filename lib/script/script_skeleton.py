"""Single source of script skeleton knowledge (zero-dependency leaf module).

Centralize structural facts about script organization: one narrow table keyed by skeleton kind,
and two resolvers containing content_mode / generation_mode interactions.

- Narrow ``SKELETONS`` table: keys are script arrays (``segments`` / ``scenes`` / ``shots`` /
  ``video_units``), rows ``Skeleton(id_field, chars_field)``. ``chars_field`` may be ``None``:
  ``video_units`` has no per-entry character list (derive characters from body ``text`` ``@[名称]`` mentions
  on read). The table explicitly declares absence; consumers receiving ``None`` must explicitly derive or
  declare inapplicability, never use fake field names that make ``get()`` return empty.
- Canonical ``resolve_declared_kind(content_mode, generation_mode)`` serves consumers with project configuration
  only, taking validated project content_mode and declared generation_mode. **Fail loudly** for unknown/missing content_mode with ``ValueError``, never silent fallback.
- Forensic ``resolve_script_kind(script)`` serves consumers holding script data (edit / inspect / export),
  prioritizing data shape: what this script currently looks like, independent of project declarations.
- Generation gate ``ensure_route_skeleton(script, content_mode, generation_mode)`` rejects skeletons outside
  the project's required family and provides resplitting guidance at generation entry points.

Keep behavior outside the table: validation hooks, Pydantic mappings, and edit allowlists remain local to consumers.

See ``docs/adr/0045`` for rationale, including rejected composite keys / wide tables / silent defaults.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from arcreel_market_core.validation_messages import MessageRef, ValidationMessage
from lib.infra.validation_messages import default_translate

logger = logging.getLogger(__name__)

STORYBOARD_ITEM_ID_PATTERN = re.compile(r"^E\d+S\d+(?:_\d+)?$")

# Episode prefix: match only `E{数字}` immediately followed by S/U (segments/scenes S, video_units U),
# preserving suffixes (such as `E1S03_2` → `E2S03_2`). See lib/script/script_models.py for the contract.
_EPISODE_PREFIX_RE = re.compile(r"^E\d+(?=[SU])")


def rewrite_episode_prefix(item_id: object, episode: int) -> object:
    """Force item ``E{数字}`` prefixes to ``E{episode}``; return nonstrings or unmatched prefixes unchanged.

    Defend against incorrect LLM prefixes despite injected episode numbers. Pre-save rewriting and the IDs
    used to archive plan-entry fingerprints are one concern, colocated with skeleton ID knowledge and shared
    by generation/freshness checks.
    """
    if not isinstance(item_id, str):
        return item_id
    rewritten, count = _EPISODE_PREFIX_RE.subn(f"E{episode}", item_id)
    if count and rewritten != item_id:
        logger.warning("episode prefix rewritten: %s → %s", item_id, rewritten)
    return rewritten


@dataclass(frozen=True)
class Skeleton:
    """Structural facts for one skeleton: per-entry ID field / optional character-list field.

    The skeleton key already identifies the script array, so no separate items_key is needed. ``chars_field``
    of ``None`` explicitly means no per-entry character list; consumers receiving ``None`` must decide explicitly, never call
    ``get()`` with a fake empty field name.
    """

    id_field: str
    chars_field: str | None


SKELETONS: dict[str, Skeleton] = {
    "segments": Skeleton("segment_id", "characters_in_segment"),
    "scenes": Skeleton("scene_id", "characters_in_scene"),
    "shots": Skeleton("shot_id", "characters_in_shot"),
    "video_units": Skeleton("unit_id", None),
}

# Derive item-label i18n keys by skeleton, sharing notification copy between shot events and task completion
# (such as “shot E1S01”). Localized copy lives under lib/i18n ``event_label_*``; register stable identifiers only.
# Both event paths must read this table rather than maintaining duplicates.
SKELETON_ITEM_LABEL_KEYS: dict[str, str] = {
    "segments": "skeleton_segments",
    "scenes": "skeleton_scenes",
    "shots": "skeleton_shots",
    "video_units": "skeleton_video_units",
}

# Derive event entity types by skeleton alongside ``SKELETON_ITEM_LABEL_KEYS``, driving frontend grouping
# (``ENTITY_LABELS``): three storyboard skeletons display shots, reference skeleton displays video units. Values
# avoid existing ``entity_type`` conflicts (drama uses ``drama_scene`` to avoid named-asset ``scene`` grouping).
SKELETON_ENTITY_TYPES: dict[str, str] = {
    "segments": "segment",
    "scenes": "drama_scene",
    "shots": "shot",
    "video_units": "reference_unit",
}

# Derive anchor types by skeleton, matching frontend canvas scroll-target guards: video_units uses
# ``reference_unit`` to select/highlight reference units; others use ``segment``
# (narration/drama/ad share timeline storyboard views selecting entries by ID).
SKELETON_ANCHOR_TYPES: dict[str, str] = {
    "segments": "segment",
    "scenes": "segment",
    "shots": "segment",
    "video_units": "reference_unit",
}


def _validate_registry() -> None:
    """Import-time table consistency: all four skeletons and valid fields. Failure aborts startup (``docs/adr/0039`` approach)."""
    expected = {"segments", "scenes", "shots", "video_units"}
    if set(SKELETONS) != expected:
        raise RuntimeError(f"SKELETONS 骨架种类不齐：期望 {expected}，实际 {set(SKELETONS)}")
    for kind, skeleton in SKELETONS.items():
        if not skeleton.id_field:
            raise RuntimeError(f"SKELETONS[{kind!r}].id_field 非法：{skeleton.id_field!r}")
        # chars_field None is valid (explicit video_units absence); empty strings are invalid.
        if skeleton.chars_field is not None and not skeleton.chars_field:
            raise RuntimeError(f"SKELETONS[{kind!r}].chars_field 非法：{skeleton.chars_field!r}")
    for name, table in (
        ("SKELETON_ITEM_LABEL_KEYS", SKELETON_ITEM_LABEL_KEYS),
        ("SKELETON_ENTITY_TYPES", SKELETON_ENTITY_TYPES),
        ("SKELETON_ANCHOR_TYPES", SKELETON_ANCHOR_TYPES),
    ):
        if set(table) != expected:
            raise RuntimeError(f"{name} 骨架种类不齐：期望 {expected}，实际 {set(table)}")


_validate_registry()


def resolve_declared_kind(content_mode: str | None, generation_mode: str | None) -> str:
    """Canonically resolve skeleton kind from declared ``(content_mode, generation_mode)``.

    Inputs are validated project content_mode and declared generation_mode (``project.json`` fields).

    - Any content type + ``generation_mode == "reference_video"`` → ``video_units``
    - ``ad`` + ``storyboard`` → ``shots``
    - ``narration`` → ``segments``, ``drama`` → ``scenes``
    - Unknown/missing content_mode → ``ValueError`` (fail loudly, never silently default to drama/narration)

    Serves consumers holding project configuration only; consumers holding potentially incomplete script dicts use ``resolve_script_kind``.
    """
    if content_mode == "ad":
        return "video_units" if generation_mode == "reference_video" else "shots"
    if content_mode == "narration":
        return "video_units" if generation_mode == "reference_video" else "segments"
    if content_mode == "drama":
        return "video_units" if generation_mode == "reference_video" else "scenes"
    raise ValueError(f"未知或缺失 content_mode: {content_mode!r}")


def resolve_script_kind(script: dict[str, Any]) -> str:
    """Forensically identify the current script dict's entry-array kind.

    Return ``"video_units"`` / ``"scenes"`` / ``"segments"`` / ``"shots"``.

    **Inspect data shape only**: editing/inspection/export must work for any on-disk script, including those
    mismatching project generation mode. Project-only resolution would make mismatched episodes unreachable
    through all MCP editors (``resolve_items`` empty; ID editing reports not found), preventing Agents from
    diagnosing causes. Generation bypasses this resolver: entry points dispatch by project generation mode
    and ``ensure_route_skeleton`` explicitly rejects mismatches.

    Resolution order:
    1. ``video_units`` present with no ``segments`` / ``scenes`` / ``shots`` → reference (prevent stray
       ``video_units`` accidentally inserted into storyboards from hijacking detection)
    2. ``content_mode`` is authoritative (``drama`` → scenes, ``narration`` → segments, ``ad`` → shots)
    3. Legacy ``content_mode=narration`` stored under ``scenes`` without ``segments``
    4. Missing ``content_mode`` infers from top-level key presence

    ``script_structure_validator._select_model`` validation, ``script_editor.resolve_items`` editing, and
    centralized persistence metadata recalculation share this one decision, avoiding drift.
    """
    if "video_units" in script and not any(k in script for k in ("segments", "scenes", "shots")):
        return "video_units"
    content_mode = script.get("content_mode")
    if content_mode == "ad":
        return "shots"
    if content_mode == "drama":
        return "scenes"
    if content_mode == "narration":
        # Legacy malformed-script compatibility: content_mode=narration but data under scenes without segments.
        # Read scenes rather than mapping content_mode literally to nonexistent segments.
        if "segments" not in script and "scenes" in script:
            return "scenes"
        return "segments"
    if "scenes" in script and "segments" not in script:
        return "scenes"
    if "shots" in script and "segments" not in script:
        return "shots"
    return "segments"


def resolve_kind_items(script: dict[str, Any], *, kind: str | None = None) -> tuple[Any, str, str]:
    """Single access point for skeleton arrays and ID fields: return ``(items, id_field, kind)``.

    Without ``kind``, use forensic ``resolve_script_kind`` data-shape detection. Callers already holding a
    declared/gate-resolved kind (``resolve_declared_kind`` / ``ensure_route_skeleton`` return) may pass it
    explicitly to avoid repeated resolution.

    Return ``script.get(kind)`` unchanged: **no type validation or nonlist→empty fallback**. Missing keys
    yield ``None``; invalid nonlist values (including ``null``) remain unchanged. This function centralizes
    only array/ID-field structural knowledge; each consumer owns malformed-data failure/degradation and
    empty-array validity policies. Per-entry ``chars_field`` is not array access; continue looking up
    ``SKELETONS`` directly.
    """
    resolved_kind = kind if kind is not None else resolve_script_kind(script)
    return script.get(resolved_kind), SKELETONS[resolved_kind].id_field, resolved_kind


# Required skeleton families: reference-to-video needs ``video_units``, others need storyboard skeletons
# (``segments`` / ``scenes`` / ``shots``). Within-family legacy differences (narration under ``scenes``)
# are not mismatches; cross-family differences mean generation's required array is absent.
_REFERENCE_ROUTE_SKELETON = "video_units"
_STORYBOARD_ROUTE_SKELETONS = ("segments", "scenes", "shots")


class SkeletonRouteMismatchError(ValueError):
    """Script skeleton and project generation mode belong to different families; generation refused.

    Resplit/regenerate mismatched scripts: generation mode locks at creation and scripts cannot switch
    families in place. Inspection/editing/export bypass this gate and remain available for mismatched scripts.

    ``actual`` of ``None`` means no skeleton arrays exist (malformed/incomplete scripts), distinct from
    an existing wrong-family array: the latter needs family-changing resplitting, the former initial content splitting.
    """

    def __init__(self, *, expected: str, actual: str | None, generation_mode: str | None) -> None:
        self.expected = expected
        self.actual = actual
        guidance = "reference" if expected == _REFERENCE_ROUTE_SKELETON else "storyboard"
        params: dict[str, Any] = {
            "route": MessageRef(
                "val_route_reference_video" if generation_mode == "reference_video" else "val_route_storyboard"
            ),
            "expected": expected,
            "expected_noun": MessageRef(f"val_skeleton_noun_{expected}"),
        }
        if actual is not None:
            params["actual"] = actual
            params["actual_noun"] = MessageRef(f"val_skeleton_noun_{actual}")
        self._message = ValidationMessage(
            f"val_skeleton_mismatch_{guidance}_{'known' if actual is not None else 'none'}",
            params,
        )
        super().__init__()

    def __str__(self) -> str:
        return self._message.render(default_translate)

    def to_validation_message(self) -> ValidationMessage:
        """Structured form for validators to report mismatches in the request language."""
        return self._message


def ensure_route_skeleton(script: dict[str, Any], content_mode: str | None, generation_mode: str | None) -> str:
    """Generation gate: verify the script contains the project-required family and return the actual kind.

    Dispatch always uses project mode (``resolve_declared_kind``); scripts carry no generation-mode metadata.
    In mismatches the needed array does not exist; silent downgrading or route switching is unacceptable,
    so reject explicitly with resplitting guidance.

    Check presence of the array generation reads, not equality of forensic and declared kinds. Both
    directions symmetrically inspect key presence:

    - Reference-to-video checks only ``video_units``. If storyboard arrays remain, shape-first forensic detection
      may answer ``segments``, but reference generation reads ``video_units``; leftover arrays do not vote
      (same as cost estimation: ``is_reference_video_project`` selects the route, not shape).
    - Storyboard-to-video checks for any ``segments`` / ``scenes`` / ``shots``. Accept historical differences
      within the family (narration under ``scenes``), returning the forensic answer unchanged; gate only
      cross-family mismatches. Reject if all three are absent: ``resolve_script_kind`` can synthesize an in-family
      answer from ``content_mode``, which otherwise creates false “all storyboard images generated” success.

    Both branches check keys, not value types: malformed ``"video_units": {}`` is a type error rather than
    mode mismatch. Downstream array validation owns that error; otherwise this gate would produce the
    contradictory “expected video_units, actual video_units”.

    Raises:
        SkeletonRouteMismatchError: wrong skeleton family or no skeleton arrays.
        ValueError: missing/unknown content_mode (``resolve_declared_kind`` fails loudly).
    """
    expected = resolve_declared_kind(content_mode, generation_mode)
    has_reference = _REFERENCE_ROUTE_SKELETON in script
    has_storyboard = any(key in script for key in _STORYBOARD_ROUTE_SKELETONS)
    if expected == _REFERENCE_ROUTE_SKELETON:
        if has_reference:
            return _REFERENCE_ROUTE_SKELETON
        actual = resolve_script_kind(script) if has_storyboard else None
    else:
        if has_storyboard:
            return resolve_script_kind(script)
        actual = _REFERENCE_ROUTE_SKELETON if has_reference else None
    raise SkeletonRouteMismatchError(expected=expected, actual=actual, generation_mode=generation_mode)
