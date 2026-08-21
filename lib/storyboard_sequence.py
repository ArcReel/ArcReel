"""
Helpers for storyboard sequence ordering and dependency planning.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.path_safety import safe_join, try_safe_join
from lib.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path
from lib.script_editor import resolve_items
from lib.script_models import get_generated_assets
from lib.script_skeleton import SKELETONS


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


PREVIOUS_STORYBOARD_REFERENCE_LABEL = "上一分镜图（镜头衔接参考）"
PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION = (
    "仅用于延续前一镜头的构图、色调和场景连续性，不是新增角色、服装或道具设定；请以当前 prompt 为准生成当前镜头。"
)


def get_storyboard_items(script: dict) -> tuple[list[dict], str, str | None, str, str]:
    """返回 旁白/解说、剧情演绎与广告/短片剧本的分镜列表 + 各引用字段名。

    ``video_units`` 骨架没有 storyboard 一说（视频按 unit 直出，见
    ``server/agent_runtime/sdk_tools/enqueue_videos.py`` 的参考生视频分支），这里硬返回空列表是
    「该骨架下不存在 storyboard 任务」的明示，调用方据此跳过。判别只看剧本实际骨架、不看项目
    生成模式：本函数是查看 / 编辑 / 生成共用的结构访问器，对存量失配剧本也要如实回答；生成分派按
    项目生成模式在各生成入口做，失配由 ``lib.script_skeleton.ensure_route_skeleton`` 显式拒绝。
    该分支的 ``char_field`` 取 ``SKELETONS`` 声明的缺位（``None``）——``video_units`` 无逐条
    角色名单（角色以 ``references`` 条目形态存在），不返回假字段名让调用方 ``get()`` 静默取空。

    分镜族骨架委托给 ``lib.script_editor.resolve_items``——与写盘咽喉
    / 编辑核心 / 元数据重算共用同一判别（``narration→segments``、``drama→scenes``、
    以及 narration 数据落 scenes 键的历史兼容）。``char_field`` 改查 ``SKELETONS`` 单一
    真相源（``.get(kind, ...)`` 静默兜底删除），第五种骨架出现时未登记即随查表报错。
    ``segments`` / ``scenes`` 键存在但值非 list（如 ``null``）时 ``resolve_items`` 抛
    ``ScriptEditError``——读取侧的调用方（``cost_estimation`` / 路由 / enqueue 工具）应让
    异常上冒，避免脏数据被静默吞成 ``TypeError: 'NoneType' is not iterable``。
    """
    items, id_field, kind = resolve_items(script)
    # 角色引用字段名查 SKELETONS 单一真相源（video_units→None 强制显式决策）；id_field 由
    # resolve_items 按同一 kind 一并给出，各分支共用。
    char_field = SKELETONS[kind].chars_field
    if kind == "video_units":
        return ([], id_field, char_field, "scenes", "props")
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


def resolve_storyboard_image_ref(project_path: Path, storyboard_rel: object) -> Path | None:
    """校验 ``generated_assets.storyboard_image`` 字段值，返回解析后落在 ``storyboards/``
    内的绝对路径；字段未设置（``None``/``""``）返回 ``None``，由调用方按各自默认路径回退。

    该字段来自磁盘上的 project.json / 剧本 JSON，不可信任（归档导入、外部编辑、脏数据都能
    落值）：绝对路径 / ``..`` 会把项目外任意文件当作分镜图使用，须先做类型检查、显式拒绝
    绝对路径（含 Windows 无盘符根路径），再用 ``try_safe_join`` 解析并确认结果落在
    ``storyboards/`` 目录内。旧宫格项目 ``storyboard_image`` 指向非 canonical 文件名
    （``scene_{id}_first.png``）仍需正常解析，故只做目录归属校验，不与 canonical 路径逐一
    比对（这点与 ``end_frame_image`` 不同）。

    Raises:
        ValueError: 字段值非字符串、是绝对路径、越界、或解析结果不在 ``storyboards/`` 内。
    """
    if storyboard_rel in (None, ""):
        return None
    if not isinstance(storyboard_rel, str):
        raise ValueError(f"invalid storyboard image path: {storyboard_rel!r}")
    # `os.path.join` 遇到绝对路径会丢弃 base，若该绝对路径恰好落在项目 storyboards/ 内仍会
    # 通过越界检查，须在解析前显式挡掉。Windows 原生运行时无盘符的「根路径」（如 `\Users\...`）
    # 对 `Path.is_absolute()` 不算绝对，但 os.path.join 遇到它同样会丢弃 base（仅保留 base 的
    # 盘符），需按正斜杠归一化后再查是否以根分隔符开头。
    if Path(storyboard_rel).is_absolute() or storyboard_rel.replace("\\", "/").startswith("/"):
        raise ValueError(f"invalid storyboard image path: {storyboard_rel!r}")
    storyboards_root = safe_join(project_path, "storyboards", allow_base=True)
    storyboard_file = try_safe_join(project_path, storyboard_rel)
    if storyboard_file is None:
        raise ValueError(f"invalid storyboard image path: {storyboard_rel!r}")
    try:
        storyboard_file.relative_to(storyboards_root)
    except ValueError:
        # 与越界/脏数据分开措辞：这一支是「项目内但不在 storyboards/」，唯一能自然落进来的
        # 是外部编辑过的剧本，运维需要从失败原因直接看出是目录归属而非路径越界。
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

    storyboard_rel = get_generated_assets(item).get("storyboard_image")
    storyboard_file = resolve_storyboard_image_ref(project_path, storyboard_rel)
    if storyboard_file is None:
        raise StoryboardImageBindingRequired(f"storyboard binding missing: {resource_id}")
    if not storyboard_file.is_file():
        raise StoryboardImageUnavailable(f"storyboard not found: {storyboard_file.name}")

    end_frame_rel = item.get("end_frame_image")
    if end_frame_rel in (None, ""):
        return storyboard_file, None
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
    return storyboard_file, end_frame_file


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

    previous_rel = get_generated_assets(previous_item).get("storyboard_image")
    if previous_rel in (None, ""):
        return None
    previous_path = resolve_storyboard_image_ref(project_path, previous_rel)
    return previous_path if previous_path is not None and previous_path.is_file() else None


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
