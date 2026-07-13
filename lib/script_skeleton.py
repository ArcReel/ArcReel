"""剧本骨架知识的单一真相源（零依赖叶子模块）。

把「剧本按骨架种类如何组织」这一结构事实收归一处：一张以骨架种类（skeleton kind）为键
的窄表，加上两个把 content_mode / generation_mode 两轴交互收口的解析器。

- 窄表 ``SKELETONS``：键即剧本里的条目数组键（``segments`` / ``scenes`` / ``shots`` /
  ``video_units``），行 ``Skeleton(id_field, chars_field)``。``chars_field`` 可为 ``None``
  ——``video_units`` 无逐条角色名单（角色以 ``references`` 中 ``type == "character"`` 的条目
  形态存在），表如实声明缺位；消费方拿到 ``None`` 必须显式决策（自行派生或声明不适用），
  不提供假字段名使 ``get()`` 返回空值。
- 规范解析 ``resolve_declared_kind(content_mode, generation_mode)``：服务只有项目配置在手
  的消费方，输入为项目级已过校验的 content_mode 与 ``effective_mode`` 解析后的
  generation_mode。**fail-loud**——未知/缺失 content_mode 抛 ``ValueError``，不静默兜底。
- 取证解析 ``resolve_script_kind(script)``：服务手持剧本数据的消费方，保留数据形状优先的
  容忍阶梯（partial migration 中间态下编辑能力不可丢失）。

行为不进表：validate 钩子、Pydantic 模型映射、编辑白名单不入注册表，留各消费方本地。

设计依据（含明确不采用的复合键 / 宽表 / 静默兜底）见 ``docs/adr/0045``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Skeleton:
    """某个骨架种类的结构事实：每项 id 字段名 / 每项角色名单字段名（可缺位）。

    键（骨架种类）本身即剧本里的条目数组键，故不再单列 items_key。``chars_field`` 为
    ``None`` 表示该骨架无逐条角色名单字段——消费方拿到 ``None`` 必须显式决策，不得当空字段名
    去 ``get()``。
    """

    id_field: str
    chars_field: str | None


SKELETONS: dict[str, Skeleton] = {
    "segments": Skeleton("segment_id", "characters_in_segment"),
    "scenes": Skeleton("scene_id", "characters_in_scene"),
    "shots": Skeleton("shot_id", "characters_in_shot"),
    "video_units": Skeleton("unit_id", None),
}

# 条目名词按骨架种类硬编码——驱动分镜级事件与任务完成事件共用的通知文案（如「镜头「E1S01」」）。
# 名词 i18n 化是独立议题（与 ``_diff_named_entities`` 的「角色」/「线索」同为既有硬编码形态），
# 不在此处收敛。两套事件路径必须读同一张表，不各自维护一份。
SKELETON_ITEM_NOUNS: dict[str, str] = {
    "segments": "分镜",
    "scenes": "场景",
    "shots": "镜头",
    "video_units": "视频单元",
}

# 事件的实体类型按骨架种类推导，与 ``SKELETON_ITEM_NOUNS`` 同源。驱动前端分组标签映射
# （``ENTITY_LABELS``），使四种骨架各显分镜/场景/镜头/视频单元，而非恒为「分镜」。取值与既有
# ``entity_type`` 枚举不冲突（drama 用 ``drama_scene`` 避免与命名实体 ``scene`` 撞组）。
SKELETON_ENTITY_TYPES: dict[str, str] = {
    "segments": "segment",
    "scenes": "drama_scene",
    "shots": "shot",
    "video_units": "reference_unit",
}

# 事件的锚点类型按骨架种类推导，取值与前端各画布的滚动目标类型守卫对齐：video_units 归
# ``reference_unit``（参考生视频画布按此选中并高亮对应视频单元），其余归 ``segment``
# （narration/drama/ad 共用时间线镜头拆分视图，按 id 选中条目）。
SKELETON_ANCHOR_TYPES: dict[str, str] = {
    "segments": "segment",
    "scenes": "segment",
    "shots": "segment",
    "video_units": "reference_unit",
}


def _validate_registry() -> None:
    """import 期表自洽校验：四骨架齐全、字段合法。失败即启动炸（同 ``docs/adr/0039`` 手法）。"""
    expected = {"segments", "scenes", "shots", "video_units"}
    if set(SKELETONS) != expected:
        raise RuntimeError(f"SKELETONS 骨架种类不齐：期望 {expected}，实际 {set(SKELETONS)}")
    for kind, skeleton in SKELETONS.items():
        if not skeleton.id_field:
            raise RuntimeError(f"SKELETONS[{kind!r}].id_field 非法：{skeleton.id_field!r}")
        # chars_field 为 None 合法（video_units 显式缺位）；空串非法。
        if skeleton.chars_field is not None and not skeleton.chars_field:
            raise RuntimeError(f"SKELETONS[{kind!r}].chars_field 非法：{skeleton.chars_field!r}")
    for name, table in (
        ("SKELETON_ITEM_NOUNS", SKELETON_ITEM_NOUNS),
        ("SKELETON_ENTITY_TYPES", SKELETON_ENTITY_TYPES),
        ("SKELETON_ANCHOR_TYPES", SKELETON_ANCHOR_TYPES),
    ):
        if set(table) != expected:
            raise RuntimeError(f"{name} 骨架种类不齐：期望 {expected}，实际 {set(table)}")


_validate_registry()


def resolve_declared_kind(content_mode: str | None, generation_mode: str | None) -> str:
    """规范解析：由项目声明的 ``(content_mode, generation_mode)`` 定骨架种类。

    输入为项目级已过校验的 content_mode 与 ``effective_mode`` 解析后的 generation_mode。

    - ``ad`` → ``shots``（恒定，不随生成路径变，见 ``docs/adr/0033``）
    - ``narration`` / ``drama`` + ``generation_mode == "reference_video"`` → ``video_units``
    - ``narration`` → ``segments``，``drama`` → ``scenes``
    - 未知/缺失 content_mode → 抛 ``ValueError``（fail-loud，不静默默认到 drama/narration）

    服务只有项目配置在手的消费方；手持可能缺字段的剧本 dict 的消费方走 ``resolve_script_kind``。
    """
    if content_mode == "ad":
        return "shots"
    if content_mode == "narration":
        return "video_units" if generation_mode == "reference_video" else "segments"
    if content_mode == "drama":
        return "video_units" if generation_mode == "reference_video" else "scenes"
    raise ValueError(f"未知或缺失 content_mode: {content_mode!r}")


def resolve_script_kind(script: dict[str, Any]) -> str:
    """取证解析：由剧本 dict 判别当前的分镜数组种类。

    返回 ``"video_units"`` / ``"scenes"`` / ``"segments"`` / ``"shots"``。

    **数据形状优先，``generation_mode`` 不参与路由**：配置改了 reference 但数据还在
    ``segments`` 的 partial migration 中间态下，若让 ``generation_mode`` 单向赢，整集脚本
    通过所有 MCP 编辑工具完全不可触达（``resolve_items`` 返回空列表、按 id 编辑都报"未找到"），
    agent 看到错误也无法定位是配置/数据冲突。数据形状优先让 agent 能拿到真实存在的列表继续
    编辑；``generation_mode`` 改为信息字段，具体生成路径由 caller（``enqueue_videos`` 等）按
    它自己的 ``generation_mode`` 分流决定。

    判别顺序：
    1. ``video_units`` 在场且 ``segments`` / ``scenes`` / ``shots`` 都不在 → reference（避免
       storyboard 脚本被误塞的游离 ``video_units`` 抢走判别）
    2. ``content_mode`` 为权威（``drama`` → scenes，``narration`` → segments，``ad`` → shots）
    3. ``content_mode=narration`` 但数据落 ``scenes`` 键（无 ``segments``）的历史遗留兼容
    4. ``content_mode`` 缺失时按顶层键存在性推断

    ``script_structure_validator._select_model``（结构校验）/ ``script_editor.resolve_items``
    （编辑核心）/ 写盘统一入口的 metadata 重算共用本判别，多处只此一处真相、不漂移。
    """
    if "video_units" in script and not any(k in script for k in ("segments", "scenes", "shots")):
        return "video_units"
    content_mode = script.get("content_mode")
    if content_mode == "ad":
        return "shots"
    if content_mode == "drama":
        return "scenes"
    if content_mode == "narration":
        # 畸形脚本兼容：content_mode=narration 但数据实际落在 scenes 键下（无 segments 键）的
        # 历史遗留状态——回退去读 scenes，而非按 content_mode 字面映射到不存在的 segments。
        if "segments" not in script and "scenes" in script:
            return "scenes"
        return "segments"
    if "scenes" in script and "segments" not in script:
        return "scenes"
    if "shots" in script and "segments" not in script:
        return "shots"
    return "segments"
