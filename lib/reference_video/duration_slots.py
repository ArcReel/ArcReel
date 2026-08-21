"""参考生视频的时长取档规则（容量语义）。

模型的 ``supported_durations`` 是离散档位，请求时长基准几乎不会正好落在档位上。
取档按**容量**解读档位：申请能装下基准时长的最小合法档位，成片不做裁剪——交付时长即
档位时长。基准时长超过最大档位时按最大档位申请（成片短于请求基准）。

纯函数，无 I/O。参考生视频的报价、预检与执行都由 request projection 先解析当前
provider/model 与非空档位集，再共用本规则，避免各路径判断漂移。
"""

from __future__ import annotations

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
"""兼容性空档位结果；request projection 会在调用本函数前把空档位转为 blocker。"""


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
