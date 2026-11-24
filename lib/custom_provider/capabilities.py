"""自定义供应商视频能力的唯一合成点：endpoint spec 系统判定与模型条目两侧求值 ⊕ 模型级用户覆盖。

系统判定是调用端点的声明（通道事实）；它与模型对应条目上的输入能力（模型事实）两侧求值
（:func:`lib.backends.video_capabilities.effective_video_capabilities`），能力覆盖在两侧求值之后整维替换；音轨覆盖只改变模型出声事实，不改变端点的开关事实。
ComfyUI 端点的能力只来自节点绑定，不读条目（docs/adr/0082、0104）。

合成语义只此一份：需要生效能力的调用方一律走这里，不得自行合并覆盖，否则「界面允许的
操作在执行期反悔」。执行层由工厂构建 backend 时注入。系统判定本身不落库，随注册表升级
自动变化；用户覆盖是稀疏字典（`CustomProviderModel.capability_overrides`），键缺席即该
维度跟随系统判定。

同一个覆盖字典里还有模型事实维度（可选时长、文生图等，见 :mod:`lib.custom_provider.model_facts`），
视频的模型事实键名与通道事实不重叠，这里跳过它们；图片的参考图上限共用同名键。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import fields, replace
from enum import Enum
from types import UnionType
from typing import TYPE_CHECKING, get_args, get_type_hints

from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoAudioFacts,
    VideoCapabilities,
    VideoRoute,
    audio_capability_pair_is_coherent,
)
from lib.backends.video_capabilities import (
    channel_projection,
    entry_video_facts,
    projected_video_capabilities,
    resolve_video_model,
)
from lib.capabilities.kernel import CapabilitySnapshot
from lib.capabilities.override_schema import LEGACY_AUDIO_KEYS
from lib.custom_provider.definition_capabilities import decode_definition
from lib.custom_provider.duration_presets import infer_supported_durations
from lib.custom_provider.model_facts import ALL_MODEL_FACT_OVERRIDE_KEYS, MODEL_FACT_OVERRIDE_KEYS, model_entry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from lib.custom_provider.endpoints import EndpointSpec


# 走 get_type_hints 而非 field.type：base.py 启用 PEP 563，后者只给注解字符串。
_CAPABILITY_TYPE_HINTS = get_type_hints(VideoCapabilities)
_INTEGER_ONLY_OVERRIDES = frozenset({"max_reference_images", "max_reference_audio_count"})
CAPABILITY_OVERRIDE_FIELDS: dict[str, object] = {
    f.name: int if f.name in _INTEGER_ONLY_OVERRIDES else _CAPABILITY_TYPE_HINTS[f.name]
    for f in fields(VideoCapabilities)
}

CHANNEL_FACT_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {
        "text_to_video",
        "first_frame",
        "last_frame",
        "max_reference_images",
        "reference_audio_mode",
        "max_reference_audio_count",
        "max_reference_audio_total_seconds",
        "max_prompt_chars",
        "audio_track",
        "reference_route_audio_track",
    }
)


AUDIO_TRACK_OVERRIDE_KEYS: frozenset[str] = frozenset({"audio_track", "reference_route_audio_track"})


if (
    CHANNEL_FACT_OVERRIDE_KEYS - CAPABILITY_OVERRIDE_FIELDS.keys()
    or CHANNEL_FACT_OVERRIDE_KEYS & (MODEL_FACT_OVERRIDE_KEYS["video"])
):
    raise RuntimeError("通道事实覆盖键须是 VideoCapabilities 字段，且不与视频模型事实覆盖键重名")


def _unwrap_optional(expected: object) -> tuple[object, bool]:
    """把 ``T | None`` 拆成 ``(T, True)``，非可选注解原样返回 ``(expected, False)``。

    覆盖 schema 直接从 dataclass 反射派生，字段一旦声明为可选（如
    ``max_reference_audio_total_seconds: float | None``），拿到的注解就是 ``types.UnionType``
    而非 ``type``——它没有 ``__name__``，也不能交给按具体类型分派的判定。所有消费 schema 的
    地方都先过这里，新增可选维度才不必逐处改判定。
    """
    if not isinstance(expected, UnionType):
        return expected, False
    members = [arg for arg in get_args(expected) if arg is not type(None)]
    if len(members) != 1:
        return expected, True
    return members[0], True


def capability_type_name(expected: object) -> str:
    """能力维度类型的展示名，供校验失败的日志与 422 文案使用。

    ``types.UnionType`` 没有 ``__name__``，直接取属性会让「值不合法」的提示路径本身抛
    ``AttributeError``——校验降级的分支不该比被校验的值更脆。
    """
    inner, optional = _unwrap_optional(expected)
    name = getattr(inner, "__name__", None) or str(inner)
    return f"{name} | None" if optional else name


def system_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> VideoCapabilities:
    """读取端点的通道声明；模型绑定用于选择对应的端点档案。"""
    from lib.custom_provider.endpoints import get_endpoint_spec

    spec = endpoint_spec or get_endpoint_spec(endpoint)
    entry = None if spec.kind == COMFYUI_KIND else model_entry(model_id, "video", entry_binding)
    return channel_projection(spec.video_description(model_id, entry))


def custom_video_snapshot(
    *,
    endpoint: str,
    model_id: str,
    overrides: object = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> CapabilitySnapshot:
    from lib.custom_provider.endpoints import get_endpoint_spec

    spec = endpoint_spec or get_endpoint_spec(endpoint)
    entry = None if spec.kind == COMFYUI_KIND else model_entry(model_id, "video", entry_binding)
    facts = entry_video_facts(entry)
    if entry is None and spec.kind != COMFYUI_KIND:
        facts = replace(facts, durations=tuple(infer_supported_durations(model_id)))
    return resolve_video_model(spec.video_description(model_id, entry), facts, overrides, model_id=model_id)


def two_sided_video_capabilities(
    *, endpoint: str, model_id: str, endpoint_spec: EndpointSpec | None = None, entry_binding: str | None = None
) -> VideoCapabilities:
    return projected_video_capabilities(
        custom_video_snapshot(
            endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec, entry_binding=entry_binding
        )
    )


def video_capabilities_from_definition(definition: Mapping[str, object]) -> VideoCapabilities:
    """从声明式定义机械合成协议能力；必需图输入决定是否支持纯文生请求。"""
    raw_capabilities = definition.get("capabilities")
    capabilities = raw_capabilities if isinstance(raw_capabilities, Mapping) else {}
    applied: dict[str, object] = {}
    for key, value in capabilities.items():
        if key == "max_reference_images" and isinstance(value, float) and value.is_integer():
            value = int(value)
        expected = CAPABILITY_OVERRIDE_FIELDS.get(key)
        if expected is None or not capability_value_matches(value, expected):
            raise ValueError(f"invalid capability in endpoint definition: {key}={value!r}")
        applied[key] = value

    return channel_projection(decode_definition({**definition, "kind": "declarative", "capabilities": applied}))


def filter_valid_overrides(
    *, endpoint: str, model_id: str, overrides: object | None, endpoint_spec: EndpointSpec | None = None
) -> dict[str, object]:
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
    from lib.custom_provider.endpoints import get_endpoint_spec

    if overrides is None:
        return {}
    if endpoint_spec is not None and endpoint_spec.kind == COMFYUI_KIND:
        if overrides:
            logger.warning("忽略 %s/%s 的能力覆盖：ComfyUI 端点的能力只从节点绑定推导", endpoint, model_id)
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
            if key not in ALL_MODEL_FACT_OVERRIDE_KEYS:
                logger.warning("忽略 %s/%s 的未知能力覆盖键 %r", endpoint, model_id, key)
            continue
        if not capability_value_matches(value, expected):
            logger.warning(
                "忽略 %s/%s 的能力覆盖 %s=%r：期望 %s，该维度回退系统判定",
                endpoint,
                model_id,
                key,
                value,
                capability_type_name(expected),
            )
            continue
        if key == "last_frame" and value is True:
            try:
                end_image_capable = (endpoint_spec or get_endpoint_spec(endpoint)).end_image_capable
            except ValueError:
                end_image_capable = False
            if not end_image_capable:
                logger.warning(
                    "忽略 %s/%s 的能力覆盖 last_frame=True：endpoint 不支持尾帧生成，该维度回退系统判定",
                    endpoint,
                    model_id,
                )
                continue
        if key == "reference_audio_mode" and value == ReferenceAudioMode.DIRECT.value:
            try:
                audio_capable = (endpoint_spec or get_endpoint_spec(endpoint)).reference_audio_capable
            except ValueError:
                audio_capable = False
            if not audio_capable:
                logger.warning(
                    "忽略 %s/%s 的能力覆盖 reference_audio_mode=direct：endpoint 不下传参考音频，该维度回退系统判定",
                    endpoint,
                    model_id,
                )
                continue
        applied[key] = value

    return applied


def video_audio_facts(channel: VideoCapabilities, effective: VideoCapabilities, route: VideoRoute) -> VideoAudioFacts:
    """开关只取端点声明，成片音轨取模型覆盖或端点无声信号；缺少无声信号时默认有声。"""
    return VideoAudioFacts(
        has_audio_track=effective.audio_track_for_route(route).has_audio_track,
        audio_switch_controllable=channel.audio_track_for_route(route).audio_switch_controllable,
    )


def synthesize_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> VideoCapabilities:
    """系统判定与模型条目两侧求值 ⊕ 用户覆盖 → 生效能力。

    Raises:
        ValueError: 系统判定本身不可得（见 :func:`system_video_capabilities`）。
    """
    caps, _applied = synthesize_video_capabilities_with_overrides(
        endpoint=endpoint,
        model_id=model_id,
        overrides=overrides,
        endpoint_spec=endpoint_spec,
        entry_binding=entry_binding,
    )
    return caps


def synthesize_video_capabilities_with_overrides(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> tuple[VideoCapabilities, dict[str, object]]:
    """返回生效能力与采用的通道覆盖，覆盖键保留旧接口格式。"""
    applied = filter_valid_overrides(
        endpoint=endpoint, model_id=model_id, overrides=overrides, endpoint_spec=endpoint_spec
    )
    snapshot = custom_video_snapshot(
        endpoint=endpoint,
        model_id=model_id,
        endpoint_spec=endpoint_spec,
        entry_binding=entry_binding,
        overrides=overrides,
    )
    if applied.get("reference_audio_mode") == "direct" and "reference_audio_mode" not in snapshot.overrides:
        logger.warning(
            "忽略 %s/%s 的 reference_audio_mode=direct：max_reference_audio_count 不允许参考音频", endpoint, model_id
        )
    return projected_video_capabilities(snapshot), {
        key: value for key, value in applied.items() if LEGACY_AUDIO_KEYS.get(key, key) in snapshot.overrides
    }


AUDIO_OVERRIDE_KEYS = ("reference_audio_mode", "max_reference_audio_count")


def resolve_audio_pair(
    overrides: dict[str, object],
    *,
    endpoint: str,
    model_id: str,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> tuple[object, int | None]:
    """把稀疏的音频覆盖补齐成完整的（模式, 段数上限）二元组，未覆盖的维度取两侧求值的结果。

    不变式判定要的是合并后的两维，而覆盖字典只带用户显式改过的那些键；写入侧与回显侧都需要
    这一步补齐，故收在一处而不是各写一份 ``overrides.get(..., system_caps...)`` 级联。
    endpoint / model_id 不可解析时按最保守的"不支持音色输入"补齐。
    """
    try:
        system_caps = two_sided_video_capabilities(
            endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec, entry_binding=entry_binding
        )
    except ValueError:
        system_caps = None
    mode = overrides.get(
        "reference_audio_mode",
        system_caps.reference_audio_mode if system_caps is not None else ReferenceAudioMode.NONE,
    )
    count = overrides.get(
        "max_reference_audio_count",
        system_caps.max_reference_audio_count if system_caps is not None else 0,
    )
    if count is None:
        return mode, None
    return mode, int(count) if isinstance(count, int) else 0


def strip_incoherent_audio_overrides(
    overrides: dict[str, object],
    *,
    endpoint: str,
    model_id: str,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> dict[str, object]:
    """过滤旧格式覆盖中导致参考音频模式与段数冲突的键，供设置接口回显。"""
    if not any(key in overrides for key in AUDIO_OVERRIDE_KEYS):
        return overrides
    mode, count = resolve_audio_pair(
        overrides, endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec, entry_binding=entry_binding
    )
    if audio_capability_pair_is_coherent(mode=mode, count=count):
        return overrides
    return {key: value for key, value in overrides.items() if key not in AUDIO_OVERRIDE_KEYS}


def capability_value_matches(value: object, expected: object) -> bool:
    """覆盖值是否可直接落入该能力维度。

    bool 是 int 的子类，两个方向都要显式排除，否则 ``True`` 会被当成 1 张参考图上限、
    ``1`` 会被当成布尔真——这类宽松真值是语义猜测，不做。数值维度另拒负数。

    可选维度（``T | None``）额外接受 ``None``，语义是「该后端不声明这项约束」，与字段默认值
    同义；其余取值按内层类型判定。浮点维度接受整数字面量——JSON 的 ``15`` 与 ``15.0`` 是同一
    个数，拒收前者只会让配置踩坑，与 bool/int 那种语义不同的类型混淆不是一回事。

    枚举维度只认该枚举的合法取值字面量（覆盖字典从 JSON 列读出，成员本身不会跨序列化存活），
    不做大小写归一或近义词映射：词表外的值一律判否、回退系统判定，而不是猜一个最像的成员。

    写入侧校验（API 层白名单）与此处的合成必须用同一判定：两边一旦漂移，写入放行的值会被
    合成静默忽略，正是本模块要消灭的「界面允许、执行反悔」。故此函数公开供 API 层复用。
    """
    inner, optional = _unwrap_optional(expected)
    if value is None:
        return optional
    if inner is bool:
        return isinstance(value, bool)
    if inner is int:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if inner is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    if isinstance(inner, type) and issubclass(inner, Enum):
        return any(value == member.value for member in inner)
    return isinstance(value, inner) if isinstance(inner, type) else False
