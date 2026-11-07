"""
Helpers for storyboard sequence ordering and dependency planning.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.infra.path_safety import safe_join, try_safe_join
from lib.material.work_files import material_file_present
from lib.project.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path
from lib.script.script_editor import resolve_items
from lib.script.script_models import get_generated_assets
from lib.script.script_skeleton import SKELETONS


@dataclass(frozen=True)
class StoryboardTaskPlan:
    resource_id: str
    script_file: str | None
    dependency_resource_id: str | None
    dependency_group: str
    dependency_index: int


class StoryboardImageUnavailable(ValueError):
    """The storyboard required for video input is unavailable."""


class StoryboardImageBindingRequired(StoryboardImageUnavailable):
    """A manifest-active project cannot infer a formal storyboard by filename."""


class EndFrameImageUnavailable(ValueError):
    """The optional end-frame binding is invalid or its snapshot is unavailable."""


def get_storyboard_items(script: dict) -> tuple[list[Any], str, str | None, str, str]:
    """Return storyboard lists and reference-field names for narration, drama, and ad/short-film scripts.

    The ``video_units`` skeleton has no storyboards (video is generated directly per unit; see
    the reference-video branch in ``server/media_tools/videos.py``). Returning an empty list
    explicitly means that this skeleton has no storyboard tasks, so callers skip them. Detection
    uses the script's actual skeleton, not the project's generation mode: this structural accessor
    serves viewing, editing, and generation, and must faithfully report existing mismatched scripts.
    Generation entry points dispatch by project generation mode, while
    ``lib.script.script_skeleton.ensure_route_skeleton`` explicitly rejects mismatches.
    This branch takes the absent ``char_field`` (``None``) declared by ``SKELETONS``: ``video_units``
    has no per-item character list (characters are ``references`` entries). It must not return a
    fabricated field name that would silently make callers' ``get()`` return nothing.

    Storyboard-family skeletons delegate to ``lib.script.script_editor.resolve_items``, sharing
    detection with the disk-write boundary, editing core, and metadata recalculation
    (``narration→segments``, ``drama→scenes``, and historical narration data stored under scenes).
    ``char_field`` now uses ``SKELETONS`` as the single source of truth (the silent
    ``.get(kind, ...)`` fallback is removed), so an unregistered fifth skeleton fails on lookup.
    When ``segments`` / ``scenes`` exists but is not a list (such as ``null``), ``resolve_items``
    raises ``ScriptEditError``. Read-side callers (``cost_estimation`` / routes / enqueue tools)
    should propagate the exception so dirty data is not silently reduced to
    ``TypeError: 'NoneType' is not iterable``.
    """
    items, id_field, kind = resolve_items(script)
    # Resolve character-reference fields from the single SKELETONS source (video_units→None forces
    # an explicit decision); resolve_items supplies id_field for the same kind across all branches.
    char_field = SKELETONS[kind].chars_field
    if kind == "video_units":
        return ([], id_field, char_field, "scenes", "props")
    return (items, id_field, char_field, "scenes", "props")


def find_storyboard_item(
    items: Sequence[Any],
    id_field: str,
    resource_id: str,
) -> tuple[Any, int] | None:
    for index, item in enumerate(items):
        # Only the outer list of on-disk script JSON is validated; skip non-mappings instead of raising AttributeError.
        if isinstance(item, dict) and str(item.get(id_field)) == str(resource_id):
            return item, index
    return None


def resolve_storyboard_image_ref(project_path: Path, storyboard_rel: object) -> Path | None:
    """Validate ``generated_assets.storyboard_image`` and return its resolved absolute path
    inside ``storyboards/``. An unset field (``None``/``""``) returns ``None`` so each caller
    can fall back to its own default path.

    This field comes from on-disk project.json / script JSON and is untrusted (archive imports,
    external edits, and dirty data can all supply values). Absolute paths / ``..`` could use any
    file outside the project as a storyboard image. Check the type, explicitly reject absolute
    paths (including Windows rooted paths without a drive), then resolve with ``try_safe_join``
    and confirm containment in ``storyboards/``. Legacy grid projects may point ``storyboard_image``
    to a noncanonical filename (``scene_{id}_first.png``), which must still resolve. Therefore only
    directory containment is checked, rather than equality with the canonical path
    (unlike ``end_frame_image``).

    Raises:
        ValueError: The value is not a string, is absolute, escapes the boundary, or resolves outside ``storyboards/``.
    """
    if storyboard_rel in (None, ""):
        return None
    if not isinstance(storyboard_rel, str):
        raise ValueError(f"invalid storyboard image path: {storyboard_rel!r}")
    # `os.path.join` discards base for an absolute path. If it happens to point inside project
    # storyboards/, containment still passes, so reject it explicitly before resolution. On native
    # Windows a drive-less rooted path (such as `\Users\...`) is not absolute to `Path.is_absolute()`,
    # but os.path.join still discards base except its drive. Normalize to forward slashes and check
    # for an initial root separator as well.
    if Path(storyboard_rel).is_absolute() or storyboard_rel.replace("\\", "/").startswith("/"):
        raise ValueError(f"invalid storyboard image path: {storyboard_rel!r}")
    storyboards_root = safe_join(project_path, "storyboards", allow_base=True)
    storyboard_file = try_safe_join(project_path, storyboard_rel)
    if storyboard_file is None:
        raise ValueError(f"invalid storyboard image path: {storyboard_rel!r}")
    try:
        storyboard_file.relative_to(storyboards_root)
    except ValueError:
        # Distinguish this from boundary escapes/dirty data: the path is inside the project but
        # outside storyboards/, naturally arising only from an externally edited script. Operations
        # must be able to identify directory containment, rather than a boundary escape, from the error.
        raise ValueError(f"storyboard image path must stay under storyboards/: {storyboard_rel!r}") from None
    return storyboard_file


def resolve_storyboard_video_inputs(
    *,
    project_path: Path,
    resource_id: str,
    item: dict[str, object],
) -> tuple[Path, Path | None]:
    """Resolve the exact current storyboard and optional canonical end frame.

    The recorded binding is the only evidence of which image belongs to this
    resource; a resource without one is refused rather than matched against a
    same-named file that nothing claims.
    """

    storyboard_file = resolve_storyboard_video_image(project_path=project_path, resource_id=resource_id, item=item)
    return storyboard_file, resolve_end_frame_snapshot(project_path=project_path, resource_id=resource_id, item=item)


def resolve_storyboard_video_image(*, project_path: Path, resource_id: str, item: dict[str, object]) -> Path:
    """The storyboard bound to ``item``; raises :class:`StoryboardImageUnavailable` when it is unbound or missing."""

    storyboard_rel = get_generated_assets(item).get("storyboard_image")
    storyboard_file = resolve_storyboard_image_ref(project_path, storyboard_rel)
    if storyboard_file is None:
        raise StoryboardImageBindingRequired(f"storyboard binding missing: {resource_id}")
    if not material_file_present(storyboard_file):
        raise StoryboardImageUnavailable(f"storyboard not found: {storyboard_file.name}")
    return storyboard_file


def resolve_end_frame_snapshot(*, project_path: Path, resource_id: str, item: dict[str, object]) -> Path | None:
    """The canonical end-frame snapshot ``item`` names, or ``None`` when it names none.

    Raises :class:`EndFrameImageUnavailable` when the named snapshot is off its canonical path or missing.
    """

    end_frame_rel = item.get("end_frame_image")
    if end_frame_rel in (None, ""):
        return None
    if not isinstance(end_frame_rel, str):
        raise EndFrameImageUnavailable(f"invalid end frame snapshot path: {end_frame_rel!r}")
    normalized = end_frame_rel.strip().replace("\\", "/")
    candidate = normalized if "/" in normalized else f"{END_FRAME_RESOURCE_TYPE}/{normalized}"
    expected_rel = resource_relative_path(END_FRAME_RESOURCE_TYPE, resource_id)
    end_frame_file = try_safe_join(project_path, candidate)
    expected_file = safe_join(project_path, expected_rel)
    canonical_path_tampered = False
    current = project_path
    for component in Path(expected_rel).parts:
        current = current / component
        if current.is_symlink() or current.is_junction():
            canonical_path_tampered = True
            break
    if end_frame_file is None or end_frame_file != expected_file or canonical_path_tampered:
        raise EndFrameImageUnavailable(f"invalid end frame snapshot path: {end_frame_rel!r}")
    if not end_frame_file.is_file():
        raise EndFrameImageUnavailable(f"end frame snapshot not found: {end_frame_file.name}")
    return end_frame_file


def resolve_previous_storyboard_path(
    project_path: Path,
    items: Sequence[Any],
    id_field: str,
    resource_id: str,
) -> Path | None:
    resolved = find_storyboard_item(items, id_field, resource_id)
    if resolved is None:
        raise KeyError(f"scene/segment not found: {resource_id}")

    target_item, index = resolved
    if index == 0 or bool(target_item.get("segment_break")):
        return None

    previous_item = items[index - 1]
    if not isinstance(previous_item, dict):
        return None
    previous_id = str(previous_item.get(id_field) or "").strip()
    if not previous_id:
        return None

    previous_rel = get_generated_assets(previous_item).get("storyboard_image")
    if previous_rel in (None, ""):
        return None
    previous_path = resolve_storyboard_image_ref(project_path, previous_rel)
    return previous_path if previous_path is not None and previous_path.is_file() else None


def group_scenes_by_segment_break(items: list[dict], id_field: str) -> list[list[dict]]:
    """Groups consecutive scene dicts, breaking at segment_break=True.

    Args:
        items: List of scene/segment dicts.
        id_field: Key in each dict for the item ID (unused but kept for API consistency).

    Returns:
        List of groups, each a list of consecutive scene dicts.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    for item in items:
        if item.get("segment_break", False) and current:
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)
    return groups


def build_storyboard_dependency_plan(
    items: Sequence[dict],
    id_field: str,
    selected_ids: Iterable[str],
    script_file: str | None,
) -> list[StoryboardTaskPlan]:
    selected_set = {str(item_id) for item_id in selected_ids}
    if not selected_set:
        return []

    plans: list[StoryboardTaskPlan] = []
    group_counter = 0
    current_group = ""
    current_group_index = 0

    for index, item in enumerate(items):
        resource_id = str(item.get(id_field) or "").strip()
        if not resource_id or resource_id not in selected_set:
            continue

        previous_resource_id: str | None = None
        if index > 0:
            previous_resource_id = str(items[index - 1].get(id_field) or "").strip() or None

        starts_new_group = (
            bool(item.get("segment_break")) or not previous_resource_id or previous_resource_id not in selected_set
        )

        if starts_new_group:
            group_counter += 1
            current_group = f"{script_file or 'storyboard'}:group:{group_counter}"
            current_group_index = 0
            dependency_resource_id = None
        else:
            current_group_index += 1
            dependency_resource_id = previous_resource_id

        plans.append(
            StoryboardTaskPlan(
                resource_id=resource_id,
                script_file=script_file,
                dependency_resource_id=dependency_resource_id,
                dependency_group=current_group,
                dependency_index=current_group_index,
            )
        )

    return plans
