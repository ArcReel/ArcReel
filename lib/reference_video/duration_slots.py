"""参考生视频的时长取档规则（容量语义）。

模型的 ``supported_durations`` 是离散档位，剧本分组的总时长几乎不会正好落在档位上。
取档按**容量**解读档位：申请能装下总时长的最小合法档位，成片不做裁剪——交付时长即
档位时长。总时长超过最大档位时按最大档位申请（成片短于剧本编排）。

纯函数，无 I/O：执行层（按执行时解析出的真实 model 能力取档）与入队前预检
（按项目当前配置近似取档，供用户确认）共用同一规则，避免两处判断漂移。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# 取档相对剧本总时长的偏移方向
EXACT = "exact"
"""总时长本身就是档位成员，申请值与剧本一致。"""
UP = "up"
"""向上取档：成片长于剧本编排。"""
DOWN = "down"
"""总时长超过最大档位，按最大档位申请：成片短于剧本编排。"""
UNCONSTRAINED = "unconstrained"
"""能力不可解析（档位集为空），总时长原样透传，决策交给 backend。"""


@dataclass(frozen=True)
class DurationSlot:
    """取档结果。``seconds`` 是向 backend 申请的秒数，``total_seconds`` 是剧本总时长。"""

    seconds: int
    total_seconds: int | float
    adjustment: str

    @property
    def needs_confirmation(self) -> bool:
        """申请秒数与剧本总时长不一致时需用户确认（能力不可解析时沿用现状放行）。"""
        return self.adjustment in (UP, DOWN)

    def warning(self, *, model: str) -> dict | None:
        """取档偏移了剧本编排时的任务 warning（i18n key + 参数）；未偏移返回 None。"""
        if not self.needs_confirmation:
            return None
        key = "ref_duration_rounded_up" if self.adjustment == UP else "ref_duration_exceeded"
        return {
            "key": key,
            "params": {"total": self.total_seconds, "duration": self.seconds, "model": model},
        }


def resolve_duration_slot(total_seconds: int | float, supported_durations: Sequence[int]) -> DurationSlot:
    """按容量语义为剧本总时长选择申请档位。

    档位集为空（能力不可解析）时原样透传总时长，与 ``assert_duration_supported`` 的
    空集放行口径一致——不因能力查询失败阻塞出片。档位集不要求有序、允许重复。

    非整数秒总时长（如 4.5）同样按「能装下」比较，取 ≥ 它的最小档位；不做截断式
    归一化，避免把本该向上取的时长静默缩短。
    """
    slots = sorted({int(d) for d in supported_durations})
    if not slots:
        return DurationSlot(seconds=int(total_seconds), total_seconds=total_seconds, adjustment=UNCONSTRAINED)
    fitting = [d for d in slots if d >= total_seconds]
    if fitting:
        chosen = fitting[0]
        adjustment = EXACT if chosen == total_seconds else UP
    else:
        chosen = slots[-1]
        adjustment = DOWN
    return DurationSlot(seconds=chosen, total_seconds=total_seconds, adjustment=adjustment)
