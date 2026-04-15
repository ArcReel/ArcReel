"""
Helpers for storyboard sequence ordering and dependency planning.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoryboardTaskPlan:
    resource_id: str
    script_file: str | None
    dependency_resource_id: str | None
    dependency_group: str
    dependency_index: int


PREVIOUS_STORYBOARD_REFERENCE_LABEL = "上一分镜图（镜头衔接参考）"
PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION = (
    "仅用于延续前一镜头的构图、色调和场景连续性，不是新增角色、服装或道具设定；请以当前 prompt 为准生成当前镜头。"
)


def get_storyboard_items(script: dict) -> tuple[list[dict], str, str, str, str]:
    content_mode = script.get("content_mode", "narration")
    if content_mode == "narration" and "segments" in script:
        return (
            list(script.get("segments", [])),
            "segment_id",
            "characters_in_segment",
            "scenes",
            "props",
        )
    return (
        list(script.get("scenes", [])),
        "scene_id",
        "characters_in_scene",
        "scenes",
        "props",
    )


def find_storyboard_item(
    items: Sequence[dict],
    id_field: str,
    resource_id: str,
) -> tuple[dict, int] | None:
    for index, item in enumerate(items):
        if str(item.get(id_field)) == str(resource_id):
            return item, index
    return None


def resolve_previous_storyboard_path(
    project_path: Path,
    items: Sequence[dict],
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
    previous_id = str(previous_item.get(id_field) or "").strip()
    if not previous_id:
        return None

    previous_path = project_path / "storyboards" / f"scene_{previous_id}.png"
    if previous_path.exists():
        return previous_path
    return None


def build_previous_storyboard_reference(path: Path) -> dict:
    return {
        "image": path,
        "label": PREVIOUS_STORYBOARD_REFERENCE_LABEL,
        "description": PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION,
    }


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
