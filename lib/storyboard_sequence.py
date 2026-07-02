"""
Helpers for storyboard sequence ordering and dependency planning.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.script_editor import resolve_items
from lib.script_skeleton import SKELETONS


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


def get_storyboard_items(script: dict) -> tuple[list[dict], str, str | None, str, str]:
    """返回 narration/drama/ad 模式剧本的分镜列表 + 各引用字段名。

    ``reference_video`` 模式没有 storyboard 一说（视频按 ``video_units`` 直出，
    见 ``server/agent_runtime/sdk_tools/enqueue_videos.py`` 的 reference 分支），
    这里硬返回空列表是「该模式下不存在 storyboard 任务」的明示，调用方据此跳过。
    该分支的 ``char_field`` 取 ``SKELETONS`` 声明的缺位（``None``）——``video_units`` 无逐条
    角色名单（角色以 ``references`` 条目形态存在），不返回假字段名让调用方 ``get()`` 静默取空。

    narration/drama 路径委托给 ``lib.script_editor.resolve_items``——与写盘咽喉
    / 编辑核心 / 元数据重算共用同一判别（``narration→segments``、``drama→scenes``、
    以及 narration 数据落 scenes 键的历史兼容）。``char_field`` 改查 ``SKELETONS`` 单一
    真相源（``.get(kind, ...)`` 静默兜底删除），第五种骨架出现时未登记即随查表报错。
    ``segments`` / ``scenes`` 键存在但值非 list（如 ``null``）时 ``resolve_items`` 抛
    ``ScriptEditError``——读取侧的调用方（``cost_estimation`` / 路由 / enqueue 工具）应让
    异常上冒，避免脏数据被静默吞成 ``TypeError: 'NoneType' is not iterable``。
    """
    if script.get("generation_mode") == "reference_video":
        unit = SKELETONS["video_units"]
        return ([], unit.id_field, unit.chars_field, "scenes", "props")

    items, id_field, kind = resolve_items(script)
    # 角色引用字段名改查 SKELETONS 单一真相源（video_units→None 强制显式决策）。
    char_field = SKELETONS[kind].chars_field
    return (items, id_field, char_field, "scenes", "props")


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
