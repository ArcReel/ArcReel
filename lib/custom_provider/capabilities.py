"""自定义供应商视频能力的唯一合成点：endpoint spec 系统判定 ⊕ 模型级用户覆盖。

合成语义只此一份：需要生效能力的调用方一律走这里，不得自行合并覆盖，否则「界面允许的
操作在执行期反悔」。执行层由工厂构建 backend 时注入。系统判定本身不落库，随注册表升级
自动变化；用户覆盖是稀疏字典（`CustomProviderModel.capability_overrides`），键缺席即该
维度跟随系统判定。
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


def filter_valid_overrides(*, endpoint: str, model_id: str, overrides: object | None) -> dict[str, object]:
    """按写入侧同一判定过滤 overrides，丢弃执行层不会采用的键值，返回真正生效的子集。

    ``overrides`` 为 ``CustomProviderModel.capability_overrides`` 的原始值（DB 里可能是任何
    形状）。不被识别的键、类型不符的值一律忽略并告警：合成是执行链路的最后一道，一条脏配置
    不该让整个生成路径不可用。合法性由 API 层白名单在写入侧把关。

    :func:`synthesize_video_capabilities` 与 API 响应边界（``server/routers/custom_providers.py``
    的 ``_model_to_response``）共用此过滤：后者据此裁剪回显给客户端的 ``capability_overrides``，
    存量脏数据因此不会被界面呈现为已生效、而执行时静默忽略。回显侧在此之上再按开放白名单收窄
    一道，故回显是执行层采用集合的子集：未开放维度的键只能由手工改库产生，界面既无入口呈现也
    无入口删除，回显隐去它以保证 GET 与写入落库的键集合一致。不校验 endpoint / media_type
    合法性，调用方已各自处理（见 :func:`system_video_capabilities` 与 ``_system_capabilities_for``）。
    """
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        logger.warning(
            "忽略 %s/%s 的能力覆盖：期望字典，实际 %s",
            endpoint,
            model_id,
            type(overrides).__name__,
        )
        return {}

    applied: dict[str, object] = {}
    for key, value in overrides.items():
        expected = CAPABILITY_OVERRIDE_FIELDS.get(key)
        if expected is None:
            logger.warning("忽略 %s/%s 的未知能力覆盖键 %r", endpoint, model_id, key)
            continue
        if not capability_value_matches(value, expected):
            logger.warning(
                "忽略 %s/%s 的能力覆盖 %s=%r：期望 %s，该维度回退系统判定",
                endpoint,
                model_id,
                key,
                value,
                expected.__name__,
            )
            continue
        # 与写入侧同一判定（见 server/routers/custom_providers.py::_check_capability_overrides）：
        # last_frame=True 但 endpoint 的 delegate 不下传 end_image 时，覆盖只会让合成层宣称
        # 支持、执行层仍静默丢帧。写入侧已挡住新配置，这里再挡一遍存量行 / 非 API 写入路径。
        # endpoint 本身可能已从注册表下线（升级移除）：get_endpoint_spec 此时抛 ValueError，
        # 视同不支持尾帧处理——存量脏配置不该让响应过滤这道最后防线也跟着炸。
        if key == "last_frame" and value is True:
            try:
                end_image_capable = get_endpoint_spec(endpoint).end_image_capable
            except ValueError:
                end_image_capable = False
            if not end_image_capable:
                logger.warning(
                    "忽略 %s/%s 的能力覆盖 last_frame=True：endpoint 不支持尾帧生成，该维度回退系统判定",
                    endpoint,
                    model_id,
                )
                continue
        applied[key] = value

    return applied


def synthesize_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
) -> VideoCapabilities:
    """系统判定 ⊕ 用户覆盖 → 生效能力。

    Raises:
        ValueError: 系统判定本身不可得（见 :func:`system_video_capabilities`）。
    """
    caps, _applied = synthesize_video_capabilities_with_overrides(
        endpoint=endpoint, model_id=model_id, overrides=overrides
    )
    return caps


def synthesize_video_capabilities_with_overrides(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
) -> tuple[VideoCapabilities, dict[str, object]]:
    """同 :func:`synthesize_video_capabilities`，额外返回过滤后的稀疏覆盖字典。

    工厂需要这份稀疏覆盖单独下传给请求期档位查询（见
    ``CustomVideoBackend.video_capabilities_for_tier``）：该查询以被包装 backend 的档位感知
    结果为基底，只应叠加真正被用户覆盖的字段，不能整体替换为本函数合并出的完整对象——那是
    context-free 的系统判定（对 Kling 等档位敏感 backend 而言是保守声明），会让档位查询短路
    回到未实现档位感知时的效果。

    Raises:
        ValueError: 系统判定本身不可得（见 :func:`system_video_capabilities`）。
    """
    caps = system_video_capabilities(endpoint=endpoint, model_id=model_id)
    applied = filter_valid_overrides(endpoint=endpoint, model_id=model_id, overrides=overrides)
    merged = replace(caps, **applied) if applied else caps
    return merged, applied


def capability_value_matches(value: object, expected: type) -> bool:
    """覆盖值是否可直接落入该能力维度。

    bool 是 int 的子类，两个方向都要显式排除，否则 ``True`` 会被当成 1 张参考图上限、
    ``1`` 会被当成布尔真——这类宽松真值是语义猜测，不做。数值维度另拒负数。

    写入侧校验（API 层白名单）与此处的合成必须用同一判定：两边一旦漂移，写入放行的值会被
    合成静默忽略，正是本模块要消灭的「界面允许、执行反悔」。故此函数公开供 API 层复用。
    """
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return isinstance(value, expected)
