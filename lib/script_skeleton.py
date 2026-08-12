"""剧本骨架知识的单一真相源（零依赖叶子模块）。

把「剧本按骨架种类如何组织」这一结构事实收归一处：一张以骨架种类（skeleton kind）为键
的窄表，加上两个把 content_mode / generation_mode 两轴交互收口的解析器。

- 窄表 ``SKELETONS``：键即剧本里的条目数组键（``segments`` / ``scenes`` / ``shots`` /
  ``video_units``），行 ``Skeleton(id_field, chars_field)``。``chars_field`` 可为 ``None``
  ——``video_units`` 无逐条角色名单（角色以 ``references`` 中 ``type == "character"`` 的条目
  形态存在），表如实声明缺位；消费方拿到 ``None`` 必须显式决策（自行派生或声明不适用），
  不提供假字段名使 ``get()`` 返回空值。
- 规范解析 ``resolve_declared_kind(content_mode, generation_mode)``：服务只有项目配置在手
  的消费方，输入为项目级已过校验的 content_mode 与项目声明的 generation_mode。**fail-loud**——未知/缺失 content_mode 抛 ``ValueError``，不静默兜底。
- 取证解析 ``resolve_script_kind(script)``：服务手持剧本数据的消费方（编辑 / 查看 / 导出），
  数据形状优先——回答的是「这份剧本现在长什么样」，与项目声明无关。
- 路线闸门 ``ensure_route_skeleton(script, content_mode, generation_mode)``：生成入口用，
  剧本实际骨架与项目路线要求的骨架不属同一族时拒绝，并给出重拆指引。

行为不进表：validate 钩子、Pydantic 模型映射、编辑白名单不入注册表，留各消费方本地。

设计依据（含明确不采用的复合键 / 宽表 / 静默兜底）见 ``docs/adr/0045``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from lib.validation_messages import MessageRef, ValidationMessage

STORYBOARD_ITEM_ID_PATTERN = re.compile(r"^E\d+S\d+(?:_\d+)?$")


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

    输入为项目级已过校验的 content_mode 与项目声明的 generation_mode（``project.json`` 字段）。

    - 任意内容模式 + ``generation_mode == "reference_video"`` → ``video_units``
    - ``ad`` + ``storyboard`` → ``shots``
    - ``narration`` → ``segments``，``drama`` → ``scenes``
    - 未知/缺失 content_mode → 抛 ``ValueError``（fail-loud，不静默默认到 drama/narration）

    服务只有项目配置在手的消费方；手持可能缺字段的剧本 dict 的消费方走 ``resolve_script_kind``。
    """
    if content_mode == "ad":
        return "video_units" if generation_mode == "reference_video" else "shots"
    if content_mode == "narration":
        return "video_units" if generation_mode == "reference_video" else "segments"
    if content_mode == "drama":
        return "video_units" if generation_mode == "reference_video" else "scenes"
    raise ValueError(f"未知或缺失 content_mode: {content_mode!r}")


def resolve_script_kind(script: dict[str, Any]) -> str:
    """取证解析：由剧本 dict 判别当前的分镜数组种类。

    返回 ``"video_units"`` / ``"scenes"`` / ``"segments"`` / ``"shots"``。

    **只看数据形状**：本解析回答的是取证提问「这份剧本现在长什么样」，服务于编辑 / 查看 /
    导出——这些能力对任何一份磁盘上的剧本都必须成立，包括骨架与项目路线不符的失配剧本。
    若改由项目路线单向定夺，失配集通过所有 MCP 编辑工具
    完全不可触达（``resolve_items`` 返回空列表、按 id 编辑都报"未找到"），agent 看到错误也
    无法定位成因。生成路径不走本解析：由生成入口按项目路线分派，失配由 ``ensure_route_skeleton``
    显式拒绝。

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


# 路线要求的骨架族：参考生视频路线要 ``video_units``，其余路线要分镜族骨架
# （``segments`` / ``scenes`` / ``shots``）。族内差异（如 narration 数据落 ``scenes`` 键的历史
# 形态）不构成失配，只有跨族才是——跨族意味着生成侧要读的数组根本不在剧本里。
_REFERENCE_ROUTE_SKELETON = "video_units"
_STORYBOARD_ROUTE_SKELETONS = ("segments", "scenes", "shots")


class SkeletonRouteMismatchError(ValueError):
    """剧本骨架与项目生成路线不属同一族——生成被拒。

    失配剧本的唯一出路是重拆重生成：路线创建时锁定，剧本不可就地换族。查看 / 编辑 /
    导出不经本闸门，失配剧本仍可读可改可导出。

    ``actual`` 为 ``None`` 表示剧本一个骨架数组都没有（畸形或半成品剧本），与「有数组但
    属另一族」分开措辞——后者要重拆换族，前者要先拆出内容。
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
        # 渲染推迟到取文本时：本模块是零依赖叶子，构造期渲染会把 ``lib.i18n``（及其
        # fastapi 依赖）拉进不需要它的轻量入口。
        return self._message.render()

    def to_validation_message(self) -> ValidationMessage:
        """结构化形态，供校验器把失配事实按请求语言报告给用户。"""
        return self._message


def ensure_route_skeleton(script: dict[str, Any], content_mode: str | None, generation_mode: str | None) -> str:
    """生成入口的路线闸门：确认剧本骨架属于项目路线要求的族，返回剧本实际骨架种类。

    生成分派一律按项目路线（``resolve_declared_kind``），剧本自身不携带路线信息。骨架与路线
    失配的剧本，按路线生成时要读的数组不存在，静默降档与悄悄换路径都不可接受——此处显式拒绝
    并给出重拆指引。

    判据是「路线要读的那个数组在不在」，不是「取证解析的答案等不等于声明值」，两个方向对称按
    键在场性判定：

    - 参考路线只问 ``video_units`` 键在不在。剧本同时残留分镜族数组时取证解析会按形状优先答
      ``segments``，但参考路线的生成侧读的就是 ``video_units``，残留数组不参与投票（费用估算
      同此口径：``is_reference_video_project`` 定路径，形状不投票）。
    - 分镜路线只问 ``segments`` / ``scenes`` / ``shots`` 有没有一个在场。族内的历史形态差异
      （narration 数据落 ``scenes`` 键）照实放行并原样返回取证解析的答案，闸门只管跨族；而
      三个键全缺时不能放行——``resolve_script_kind`` 会按 ``content_mode`` 合成一个族内答案，
      顺着走下去分镜图入队会落进"✨ 所有片段的分镜图都已生成"的假成功。

    两个分支都只问键在不在、不问值的类型：``"video_units": {}`` 这类脏数据是类型错误、不是
    路线失配，报错权归下游的「必须是数组」校验，闸门不越俎代庖（否则会报出「要求 video_units、
    当前 video_units」的自相矛盾文案）。

    Raises:
        SkeletonRouteMismatchError: 剧本骨架与路线要求的骨架不属同一族，或剧本没有任何骨架数组。
        ValueError: content_mode 未知或缺失（由 ``resolve_declared_kind`` fail-loud）。
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
