"""自定义供应商视频能力的唯一合成点：endpoint spec 系统判定 ⊕ 模型级用户覆盖。

执行层（工厂构建 backend 时注入）与展示层都从这里取生效能力，两侧严格同源——避免
「界面允许的操作在执行期反悔」。系统判定本身不落库，随注册表升级自动变化；用户覆盖是
稀疏字典（`CustomProviderModel.capability_overrides`），键缺席即该维度跟随系统判定。
"""

from __future__ import annotations

import logging
from dataclasses import fields, replace
from typing import get_type_hints

from lib.custom_provider.endpoints import get_endpoint_spec
from lib.video_backends.base import VideoCapabilities

logger = logging.getLogger(__name__)


# 覆盖键 → 值类型，直接从 VideoCapabilities dataclass 派生：新增能力维度自动进入覆盖
# schema，无需 DB 迁移，也不存在手写副本与 dataclass 漂移的可能。
# 走 get_type_hints 而非 field.type：base.py 启用 PEP 563，后者只给注解字符串。
_CAPABILITY_TYPE_HINTS = get_type_hints(VideoCapabilities)
CAPABILITY_OVERRIDE_FIELDS: dict[str, type] = {
    f.name: _CAPABILITY_TYPE_HINTS[f.name] for f in fields(VideoCapabilities)
}


def system_video_capabilities(*, endpoint: str, model_id: str) -> VideoCapabilities:
    """读 endpoint spec 得出系统对该模型视频能力的判定。

    两条声明形式（注册表不变式保证恰填其一）：
    - ``video_caps_for_model``：backend 的 per-model 纯函数，四字段全量由 backend 声明；
    - ``video_max_reference_images``：endpoint 维度硬上限，参考图布尔位由上限推出（>0 即
      支持），首帧/尾帧取 ``VideoCapabilities`` 默认。

    不构造 SDK client、不查 DB，故 api_key 缺失也可调用。

    Raises:
        ValueError: endpoint 不存在、非 video 类、或两种声明都缺失 / 上限为负。
    """
    spec = get_endpoint_spec(endpoint)
    if spec.media_type != "video":
        raise ValueError(f"endpoint {endpoint!r} is {spec.media_type}, not video")

    caps_fn = spec.video_caps_for_model
    if caps_fn is not None:
        caps = caps_fn(model_id)
        if caps.max_reference_images < 0:
            raise ValueError(
                f"invalid backend max_reference_images: endpoint={endpoint!r} model={model_id!r} "
                f"value={caps.max_reference_images!r}"
            )
        return caps

    endpoint_cap = spec.video_max_reference_images
    if endpoint_cap is None:
        raise ValueError(
            f"video endpoint {endpoint!r} declares neither video_max_reference_images nor video_caps_for_model"
        )
    if endpoint_cap < 0:
        raise ValueError(f"invalid video_max_reference_images on endpoint {endpoint!r}: {endpoint_cap!r}")
    return VideoCapabilities(reference_images=endpoint_cap > 0, max_reference_images=endpoint_cap)


def synthesize_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
) -> VideoCapabilities:
    """系统判定 ⊕ 用户覆盖 → 生效能力。

    ``overrides`` 为 ``CustomProviderModel.capability_overrides`` 的原始值（DB 里可能是任何
    形状）。不被识别的键、类型不符的值一律忽略并告警，降级为该维度跟随系统判定：合成是执行
    链路的最后一道，一条脏配置不该让整个生成路径不可用。合法性由 API 层白名单在写入侧把关。

    Raises:
        ValueError: 系统判定本身不可得（见 :func:`system_video_capabilities`）。
    """
    caps = system_video_capabilities(endpoint=endpoint, model_id=model_id)
    if overrides is None:
        return caps
    if not isinstance(overrides, dict):
        logger.warning(
            "忽略 %s/%s 的能力覆盖：期望字典，实际 %s",
            endpoint,
            model_id,
            type(overrides).__name__,
        )
        return caps

    applied: dict[str, object] = {}
    for key, value in overrides.items():
        expected = CAPABILITY_OVERRIDE_FIELDS.get(key)
        if expected is None:
            logger.warning("忽略 %s/%s 的未知能力覆盖键 %r", endpoint, model_id, key)
            continue
        if not _value_matches(value, expected):
            logger.warning(
                "忽略 %s/%s 的能力覆盖 %s=%r：期望 %s，该维度回退系统判定",
                endpoint,
                model_id,
                key,
                value,
                expected.__name__,
            )
            continue
        applied[key] = value

    return replace(caps, **applied) if applied else caps


def _value_matches(value: object, expected: type) -> bool:
    """覆盖值是否可直接落入该能力维度。

    bool 是 int 的子类，两个方向都要显式排除，否则 ``True`` 会被当成 1 张参考图上限、
    ``1`` 会被当成布尔真——这类宽松真值是语义猜测，不做。数值维度另拒负数。
    """
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return isinstance(value, expected)
