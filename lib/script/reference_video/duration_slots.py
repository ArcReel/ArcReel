"""视频请求的时长取档规则（容量语义）与两条路线共用的时长投影。

模型的 ``supported_durations`` 是离散档位，请求时长基准几乎不会正好落在档位上。
取档按**容量**解读档位：申请能装下基准时长的最小合法档位，成片不做裁剪——交付时长即
档位时长。基准时长超过最大档位时按最大档位申请（成片短于请求基准）。

纯函数，无 I/O。参考生视频与分镜两条路线的报价、预检与执行都先解析当前 provider/model
与档位集，再共用 :func:`project_request_duration`，两条路线因此不会对同一份状态给出不同的
取档结论；各路线只负责把返回的 :data:`DurationProblem` 映射成自己的 problem 信封。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# 取档相对请求时长基准的偏移方向。前端 `types/reference-video.ts` 的字面量联合与此对齐，
# 用 Literal 而非裸 str 让类型检查兜住 `warning()` 与预检响应里的分支判等。
Adjustment = Literal["exact", "up", "down", "unconstrained"]

EXACT: Adjustment = "exact"
"""请求时长基准本身就是档位成员，申请值与基准一致。"""
UP: Adjustment = "up"
"""向上取档：成片长于请求时长基准。"""
DOWN: Adjustment = "down"
"""请求时长基准超过最大档位，按最大档位申请：成片短于请求基准。"""
UNCONSTRAINED: Adjustment = "unconstrained"
"""时长这一维不由 ArcReel 取档：申请值就是规划值，端点自己决定成片多长。"""

DEFAULT_PLANNED_DURATION_SECONDS = 8
"""单元未写 ``duration_seconds``、项目也没有偏好时长时的规划篇幅。

不是任何模型的档位声明，只是取档前必须有个基准；档位集非空时它照常参与取档。
"""


@dataclass(frozen=True)
class DurationSlot:
    """取档结果。``seconds`` 是向 backend 申请的秒数，``total_seconds`` 是请求时长基准。"""

    seconds: int
    total_seconds: int | float
    adjustment: Adjustment

    @property
    def needs_confirmation(self) -> bool:
        """申请秒数与请求时长基准不一致时需用户确认。"""
        return self.adjustment in (UP, DOWN)

    def warning(self, *, model: str) -> dict | None:
        """取档偏移了请求时长基准时的任务 warning（i18n key + 参数）；未偏移返回 None。"""
        if not self.needs_confirmation:
            return None
        key = "ref_duration_rounded_up" if self.adjustment == UP else "ref_duration_exceeded"
        return {
            "key": key,
            "params": {"total": self.total_seconds, "duration": self.seconds, "model": model},
        }


def resolve_duration_slot(total_seconds: int | float, supported_durations: Sequence[int]) -> DurationSlot:
    """按容量语义为请求时长基准选择申请档位。

    档位集为空时原样透传总时长。可执行的参考生视频请求不得
    依赖该分支：``ReferenceUnitRequestProjector`` 对缺失、空或无效的档位先返回结构化
    blocker。非空档位集不要求有序、允许重复。

    非整数秒总时长（如 4.5）同样按「能装下」比较，取 ≥ 它的最小档位；不做截断式
    归一化，避免把本该向上取的时长静默缩短。
    """
    slots = sorted({int(d) for d in supported_durations})
    if not slots:
        return DurationSlot(seconds=int(total_seconds), total_seconds=total_seconds, adjustment=UNCONSTRAINED)
    fitting = [d for d in slots if d >= total_seconds]
    adjustment: Adjustment
    if fitting:
        chosen = fitting[0]
        adjustment = EXACT if chosen == total_seconds else UP
    else:
        chosen = slots[-1]
        adjustment = DOWN
    return DurationSlot(seconds=chosen, total_seconds=total_seconds, adjustment=adjustment)


#: 共享时长投影给出的问题类别。调用方按自己的信封映射成 problem code：类别是判定，
#: code 与文案属各路线的对外契约。
DurationProblem = Literal[
    "tts_duration_endpoint_fixed",
    "supported_durations_missing",
    "needs_replan",
    "confirmation_required",
]


@dataclass(frozen=True)
class RequestDurationProjection:
    """一次请求的时长结论：申请档位、取档偏移与唯一的阻断类别。

    ``slot`` 为 None 表示这次请求算不出可申请的秒数（档位声明缺失，或 TTS 旁白交付撞上
    端点固定时长）；此时 ``problem`` 必然非空。``endpoint_fixed`` 为真表示这一维不由
    ArcReel 驱动，``slot.seconds`` 是原样透传的规划秒数而非档位成员。
    """

    duration_input: int | float
    slot: DurationSlot | None
    endpoint_fixed: bool
    problem: DurationProblem | None


def request_duration_input(
    planned_duration_seconds: int,
    narration_duration_floor: float | None = None,
) -> int | float:
    """请求时长基准：规划秒数与当前 TTS 时长下限取大。"""

    return max(planned_duration_seconds, narration_duration_floor or 0)


def project_request_duration(
    *,
    planned_duration_seconds: int,
    supported_durations: Sequence[int],
    narration_duration_floor: float | None = None,
    duration_endpoint_fixed: bool = False,
    uses_tts: bool = False,
    current_visual_duration_seconds: int | None = None,
    confirmed_request_duration_seconds: int | None = None,
    confirmation_waived: bool = False,
) -> RequestDurationProjection:
    """把规划篇幅、当前 TTS 下限与模型档位投影成一次请求的时长结论。

    ``duration_endpoint_fixed`` 为真时档位集是空的合法状态（时长这一维由端点固定，见
    ``docs/adr/0082``）：不收窄、不要求确认，规划秒数原样透传，端点会按 workflow 自己那档出片。
    这一维不由 ArcReel 驱动也就意味着无法为一段 TTS 申请足够长的成片，``uses_tts`` 为真时
    因此判「不支持」而不是让请求带着注定装不下旁白的时长进队列。

    不带 ``duration_endpoint_fixed`` 的空集是档位声明缺失（``docs/adr/0018``），仍按
    ``supported_durations_missing`` 阻断。
    """

    if isinstance(planned_duration_seconds, bool) or planned_duration_seconds <= 0:
        raise ValueError("planned_duration_seconds must be a positive integer")
    if narration_duration_floor is not None and (
        not math.isfinite(narration_duration_floor) or narration_duration_floor <= 0
    ):
        raise ValueError("narration_duration_floor must be positive and finite or null")
    if current_visual_duration_seconds is not None and (
        isinstance(current_visual_duration_seconds, bool) or current_visual_duration_seconds <= 0
    ):
        raise ValueError("current_visual_duration_seconds must be a positive integer or null")

    duration_input = request_duration_input(planned_duration_seconds, narration_duration_floor)
    if duration_endpoint_fixed:
        if uses_tts:
            return RequestDurationProjection(
                duration_input=duration_input,
                slot=None,
                endpoint_fixed=True,
                problem="tts_duration_endpoint_fixed",
            )
        return RequestDurationProjection(
            duration_input=duration_input,
            slot=DurationSlot(
                seconds=planned_duration_seconds,
                total_seconds=duration_input,
                adjustment=UNCONSTRAINED,
            ),
            endpoint_fixed=True,
            problem=None,
        )

    durations = tuple(
        sorted({duration for duration in supported_durations if not isinstance(duration, bool) and duration > 0})
    )
    if not durations:
        return RequestDurationProjection(
            duration_input=duration_input,
            slot=None,
            endpoint_fixed=False,
            problem="supported_durations_missing",
        )

    slot = resolve_duration_slot(duration_input, durations)
    problem: DurationProblem | None = None
    if slot.adjustment == DOWN:
        problem = "needs_replan"
    elif (
        slot.seconds != (current_visual_duration_seconds or planned_duration_seconds)
        and not confirmation_waived
        and confirmed_request_duration_seconds != slot.seconds
    ):
        problem = "confirmation_required"
    return RequestDurationProjection(
        duration_input=duration_input,
        slot=slot,
        endpoint_fixed=False,
        problem=problem,
    )
