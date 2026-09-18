"""节点绑定的语义键名录：推断、校验与运行时填值共读这一份。

语义键与声明式端点共用命名（``prompt`` / ``start_image`` / ``reference_images`` …），这样同一个
维度在两种 ``kind`` 上叫同一个名字。哪些键属于哪种媒体类型是名录里的事实，不是各处各写一份
``if media_type == "image"``——白名单漂移会让保存期放行的绑定在填值期落空。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .workflow import node_inputs

logger = logging.getLogger(__name__)

#: 视频端点可用的全部语义键，也是 schema 里 ``bindings`` 的封闭键集。
VIDEO_BINDING_KEYS = (
    "prompt",
    "negative_prompt",
    "start_image",
    "end_image",
    "reference_images",
    "width",
    "height",
    "frames",
    "fps",
    "seed",
    "output",
)

#: 图像端点可用的语义键：没有首尾帧，也没有帧数与帧率这两个时间轴维度。
IMAGE_BINDING_KEYS = (
    "prompt",
    "negative_prompt",
    "reference_images",
    "width",
    "height",
    "seed",
    "output",
)

#: ``media_type`` → 该媒体类型允许的语义键。
BINDING_KEYS_BY_MEDIA_TYPE: dict[str, frozenset[str]] = {
    "image": frozenset(IMAGE_BINDING_KEYS),
    "video": frozenset(VIDEO_BINDING_KEYS),
}

#: 两种媒体类型都必须绑定的语义键：没有提示词无从下笔，没有产物取不到成片。
REQUIRED_BINDING_KEYS = ("prompt", "output")


def targets_of(raw: object) -> tuple[Mapping[str, Any], ...]:
    """一个语义键的目标列表。

    三态里「空列表」与「键缺失」在此同形：两者都是「没有目标」，区别只在导入时要不要重跑推断，
    与填值、校验、能力推导都无关。非列表值与列表里的非对象条目一并丢掉——schema 已把形状挡在
    保存期之前，这里只保证读侧拿到的每一项都可当条目用。
    """
    if not isinstance(raw, list):
        return ()
    return tuple(target for target in raw if isinstance(target, Mapping))


def literal_of(workflow: Mapping[str, Any], target: Mapping[str, Any]) -> object:
    """一个目标当前的字面值；节点或字段已不在图里则 ``None``。

    填值、校验与能力推导读的是同一份字面值，判据因此只此一处：三个消费方各写一份的话，「节点不在
    图里」这类边界迟早各判各的，而它们本该对同一份 workflow 得出同一个结论。
    """
    node = workflow.get(str(target["node"]))
    name = target.get("input")
    if not isinstance(node, Mapping) or not isinstance(name, str):
        return None
    return node_inputs(node).get(name)


def int_literal_of(workflow: Mapping[str, Any], target: Mapping[str, Any]) -> int | None:
    """字面值取整数；布尔不算数——``True`` 是 ``int`` 的子类，当尺寸或帧数用会静默变成 1。"""
    raw = literal_of(workflow, target)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def positive_number(raw: object) -> float | None:
    """正数值，否则 ``None``。帧率与尺寸都只在正数时有意义。"""
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0 else None


def bound_fps(workflow: Mapping[str, Any], bindings: Mapping[str, Any]) -> float | None:
    """``fps`` 只读绑定读出的、这份 workflow 实际在用的帧率。

    多个只读绑定读出不同字面值时取第一个：那是一份自相矛盾的定义，保存期已由
    ``comfyui_fps_conflict`` 挡下（``lib/custom_provider/comfyui/validator.py``），到这里只剩存量
    数据，取谁都是猜，取第一个至少让填值与推导口径一致。
    """
    for target in targets_of(bindings.get("fps")):
        fps = positive_number(literal_of(workflow, target))
        if fps is not None:
            return fps
    return None


def step_of(target: Mapping[str, Any]) -> int:
    """条目声明的步长；未声明按 1 看待——没有步长信息时不替 workflow 作者假设一个。"""
    raw = target.get("step")
    return raw if isinstance(raw, int) and raw >= 1 else 1


def align_frames(frames: int, step: int) -> int:
    """向下对齐到 ``frames ≡ 1 (mod step)``，下限 ``1 + step``。

    下限只留日志不报错：时长短到连一个步长都凑不出时，提交最小合法帧数仍能出片，把这次生成拒了
    反而不如让用户看见一段比预期短的成片。

    填值与能力推导共读这一份：前者据它写帧数，后者据它判「这一档选中之后还是不是原来那份图」。
    """
    aligned = frames - (frames - 1) % step
    if aligned < 1 + step:
        logger.info("帧数 %d 低于步长 %d 的最小合法值，按 %d 提交", frames, step, 1 + step)
        return 1 + step
    return aligned
