"""内置供应商图片调用的预估用量（docs/adr/0100）。

预估与结算用同一个计算器：结算时调用通道上报实际发生的计量项，预估时由同一条调用通道按请求事实推出
（见各内置调用通道的 ``estimate_usage``），选择维度取自它会发给供应商的请求字段（docs/adr/0056）。
"""

from __future__ import annotations

from lib.backends.backend_assembly.specs import get_provider_spec
from lib.backends.image_backends.base import ImageRequestFacts
from lib.backends.image_backends.registry import channel_usage_estimator
from lib.billing.metering import Usage


def estimate_builtin_image_usage(provider_id: str, facts: ImageRequestFacts) -> Usage:
    """内置供应商一次图片调用的预估用量；供应商没有图片调用通道时抛 ``ValueError``。"""
    return channel_usage_estimator(get_provider_spec(provider_id, "image").registry_backend)(facts)
