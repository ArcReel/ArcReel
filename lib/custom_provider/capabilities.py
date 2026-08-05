"""自定义供应商视频能力的唯一合成点：endpoint spec 系统判定 ⊕ 模型级用户覆盖。

合成语义只此一份：需要生效能力的调用方一律走这里，不得自行合并覆盖，否则「界面允许的
操作在执行期反悔」。执行层由工厂构建 backend 时注入。系统判定本身不落库，随注册表升级
自动变化；用户覆盖是稀疏字典（`CustomProviderModel.capability_overrides`），键缺席即该
维度跟随系统判定。
"""

from __future__ import annotations

import logging
from dataclasses import fields, replace
from enum import Enum
from types import UnionType
from typing import get_args, get_type_hints

from lib.custom_provider.endpoints import get_endpoint_spec
from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities

logger = logging.getLogger(__name__)


# 覆盖键 → 值类型，直接从 VideoCapabilities dataclass 派生：新增能力维度自动进入覆盖
# schema，无需 DB 迁移，也不存在手写副本与 dataclass 漂移的可能。
# 走 get_type_hints 而非 field.type：base.py 启用 PEP 563，后者只给注解字符串。
_CAPABILITY_TYPE_HINTS = get_type_hints(VideoCapabilities)
CAPABILITY_OVERRIDE_FIELDS: dict[str, object] = {
    f.name: _CAPABILITY_TYPE_HINTS[f.name] for f in fields(VideoCapabilities)
}


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


def system_video_capabilities(*, endpoint: str, model_id: str) -> VideoCapabilities:
    """读 endpoint spec 得出系统对该模型视频能力的判定。

    两条声明形式（注册表不变式保证恰填其一）：
    - ``video_caps_for_model``：backend 的 per-model 纯函数，四字段全量由 backend 声明；
    - ``video_max_reference_images``：endpoint 维度硬上限，其余维度（首帧/尾帧/参考音频）
      取 ``VideoCapabilities`` 默认。

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
    return VideoCapabilities(max_reference_images=endpoint_cap)


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
                capability_type_name(expected),
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
        # 同构于上：endpoint 的 delegate 不会把 reference_audio_files 组装进请求时，把模式
        # 覆盖成 direct 只会让合成层宣称支持音色输入，执行层照样生成随机音色的视频。
        if key == "reference_audio_mode" and value == ReferenceAudioMode.DIRECT.value:
            try:
                audio_capable = get_endpoint_spec(endpoint).reference_audio_capable
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
    merged = merge_overrides(caps, applied)
    return enforce_audio_capability_invariant(merged, endpoint=endpoint, model_id=model_id), applied


def merge_overrides(caps: VideoCapabilities, applied: dict[str, object]) -> VideoCapabilities:
    """把稀疏覆盖并进能力对象，枚举维度还原成枚举成员、浮点维度的整数字面量还原成 float。

    覆盖字典从 JSON 列读出，枚举维度到这里还是字面量字符串；直接 ``replace`` 会让声明为
    枚举类型的字段实际持有 ``str``。当前枚举都是 ``StrEnum``，``==`` 侥幸仍成立，但
    ``is`` 比较与 ``.value`` 取值会分别静默判否和抛 ``AttributeError``——差别只在调用方
    碰巧用了哪种写法。还原是纯机械的：值已由 :func:`capability_value_matches` 按精确字面量
    校验过，这里不做任何二次判定。
    """
    if not applied:
        return caps
    coerced: dict[str, object] = {}
    for key, value in applied.items():
        inner, _optional = _unwrap_optional(CAPABILITY_OVERRIDE_FIELDS.get(key))
        if isinstance(inner, type) and issubclass(inner, Enum) and not isinstance(value, inner):
            coerced[key] = inner(value)
        elif inner is float and isinstance(value, int) and not isinstance(value, bool):
            coerced[key] = float(value)
        else:
            coerced[key] = value
    return replace(caps, **coerced)


AUDIO_OVERRIDE_KEYS = ("reference_audio_mode", "max_reference_audio_count")


def resolve_audio_pair(overrides: dict[str, object], *, endpoint: str, model_id: str) -> tuple[object, int]:
    """把稀疏的音频覆盖补齐成完整的（模式, 段数上限）二元组，未覆盖的维度取系统判定。

    不变式判定要的是合并后的两维，而覆盖字典只带用户显式改过的那些键；写入侧与回显侧都需要
    这一步补齐，故收在一处而不是各写一份 ``overrides.get(..., system_caps...)`` 级联。
    endpoint / model_id 不可解析时按最保守的"不支持音色输入"补齐。
    """
    try:
        system_caps = system_video_capabilities(endpoint=endpoint, model_id=model_id)
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
    return mode, int(count) if isinstance(count, int) else 0


def strip_incoherent_audio_overrides(
    overrides: dict[str, object], *, endpoint: str, model_id: str
) -> dict[str, object]:
    """剔除合并后违反音频两维不变式的覆盖键。

    :func:`enforce_audio_capability_invariant` 只把执行期能力降到 ``none``，覆盖字典本身仍
    留着被降级的值。存量行或手工改库留下的 ``direct`` ⊕ 上限 0 若原样回显，界面会显示"直传
    音色已生效"而生成其实不带音色输入；客户端把它随一次与音频无关的编辑原样回传，还会撞上
    写入侧的同一条不变式，把整次保存拒成 422。两处后果与 ``last_frame`` 的既有过滤同源。
    """
    if not any(key in overrides for key in AUDIO_OVERRIDE_KEYS):
        return overrides
    mode, count = resolve_audio_pair(overrides, endpoint=endpoint, model_id=model_id)
    if audio_capability_pair_is_coherent(mode=mode, count=count):
        return overrides
    return {key: value for key, value in overrides.items() if key not in AUDIO_OVERRIDE_KEYS}


def audio_capability_pair_is_coherent(*, mode: object, count: int) -> bool:
    """音频两维的合并后不变式：声明支持音色输入就必须给出正的段数上限。

    两维各自合法、合起来无意义的组合只有这一种（``direct`` ⊕ 上限 0）：稀疏覆盖只写其中
    一维就能凑出——覆盖 ``reference_audio_mode=direct`` 而不动系统判定的 0，或反过来把
    ``max_reference_audio_count`` 压成 0 而模式仍是系统判定的 ``direct``。反向组合
    （``none`` ⊕ 正上限）不算违约：模式为 ``none`` 时上限本就不参与判定，且"关掉音色输入"
    是正当意图，判违约反会把用户明确关掉的能力顶回开启。

    不修正这组的后果是 ``gate_video_request`` 先过模式判定、再撞上限 0，把"该模型不支持
    参考音频"报成"最多支持 0 段参考音频"——用户按提示去减角色数量，减到零段也过不了。

    写入侧（``server/routers/custom_providers.py``）与合成侧共用此判定，两边不得各写一份。
    """
    return mode == ReferenceAudioMode.NONE.value or mode == ReferenceAudioMode.NONE or count > 0


def enforce_audio_capability_invariant(caps: VideoCapabilities, *, endpoint: str, model_id: str) -> VideoCapabilities:
    """把违反 :func:`audio_capability_pair_is_coherent` 的合并结果降到 ``none``。

    写入侧已挡住新配置，这里兜住存量行与非 API 写入路径。降级方向取"不支持"而非"补一个
    上限"：上限该是多少属供应商事实，系统无从替用户猜。
    """
    if audio_capability_pair_is_coherent(mode=caps.reference_audio_mode, count=caps.max_reference_audio_count):
        return caps
    logger.warning(
        "%s/%s 的合并能力 reference_audio_mode=%s 但 max_reference_audio_count=%d，按不支持参考音频处理",
        endpoint,
        model_id,
        caps.reference_audio_mode.value,
        caps.max_reference_audio_count,
    )
    return replace(caps, reference_audio_mode=ReferenceAudioMode.NONE)


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
