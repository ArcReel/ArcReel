"""视频请求事实：一次视频生成请求的执行事实，每次请求只求值一次（``docs/adr/0086``）。

求值维度是「项目 × 路线 × 任务类型桶 × 身份来源」。读侧（报价、预检、界面、Agent）以当前配置
解析出的执行模型为身份，执行侧以 lane 实际构造的 backend 身份为准（``docs/adr/0049`` ③）；除身份
来源外两侧求值完全相同，消费方直接读取求值结果中的分辨率、档位与音轨。

- 分辨率未设置即不下发：请求分辨率就是时长联动约束所用的分辨率，没有兜底档位。
- 收窄严格：约束收成空集是失败，不回退到未收窄的全集。
- 失败只带类型（问题码、参数与修复指引），不降级、不回退，由消费方按阶段处理。

本模块是路线中立层：不依赖参考生视频子包，也不依赖服务端（``pyproject.toml`` import-linter 契约）；
不在模块级绑定数据库 session，调用方注入 resolver——执行期传入 GenerationContext 已绑定 session
的那一个。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from lib.config.resolver import (
    ENDPOINT_FIXED_PLANNING_DURATIONS,
    ConfigResolver,
    DurationExclusionReason,
    VideoBucketCapabilityError,
    VideoGenerationType,
    VideoSupportedDurationsError,
    VoiceConsistency,
    builtin_video_audio_track,
    constrain_durations,
)

#: 视频路线：分镜（首帧驱动）与参考生视频。问题码按路线分族，已记录的码不改名。
VideoRoute = Literal["storyboard", "reference_video"]

#: 求值失败的修复指引：都指向视频模型配置。
CONFIGURE_VIDEO_MODEL_ACTION = "configure_video_model"

#: 单元未写时长、项目也无偏好时长时的规划基准：取剧本规划借用档位里最长的一档。
#:
#: 不代表任何模型的可选档位。它与 :data:`ENDPOINT_FIXED_PLANNING_DURATIONS` 同源，端点固定的
#: 单元在分镜与参考两条路线、TTS 与非 TTS 两条路径上因此按同一个篇幅规划。
DEFAULT_PLANNED_DURATION_SECONDS = max(ENDPOINT_FIXED_PLANNING_DURATIONS)

_FailureKind = Literal["missing", "invalid", "incompatible", "unavailable"]

_ROUTE_FAILURE_CODES: dict[VideoRoute, dict[_FailureKind, str]] = {
    "storyboard": {
        "missing": "video_supported_durations_missing",
        "invalid": "video_supported_durations_invalid",
        "incompatible": "video_supported_durations_incompatible",
        "unavailable": "video_capability_unavailable",
    },
    "reference_video": {
        "missing": "reference_supported_durations_missing",
        "invalid": "reference_supported_durations_invalid",
        "incompatible": "reference_supported_durations_incompatible",
        "unavailable": "reference_capability_unavailable",
    },
}


@dataclass(frozen=True)
class ConfiguredVideoIdentity:
    """读侧身份：按当前配置解析该桶的执行模型（过桶能力闸）。"""


@dataclass(frozen=True)
class ExecutionVideoIdentity:
    """执行侧身份：lane 实际构造的 backend 的规范 ``provider_id`` 与实际 ``.model``。"""

    provider_id: str
    model_id: str


CONFIGURED_VIDEO_IDENTITY = ConfiguredVideoIdentity()

VideoRequestIdentity = ConfiguredVideoIdentity | ExecutionVideoIdentity


@dataclass(frozen=True)
class VideoRequestFacts:
    """一次视频请求的执行事实。

    ``resolution`` 为 None 表示请求不携带分辨率参数。``supported_durations`` 是模型声明的全集，
    ``allowed_durations`` 是按请求分辨率与参考图收窄后的档位，``excluded_durations`` 给出全集中
    每个被排除秒数的成因。时长由端点固定时（``docs/adr/0082``）三者都是合法空集。

    音轨四项：``requested_generate_audio`` 是用户的音频开关，``generate_audio`` 是计价口径的
    有无音轨，``has_audio_track`` 是成片有无音轨，``audio_switch_controllable`` 是开关是否可控。
    计价所需的事实是执行模型、``resolution``、请求秒数与 ``generate_audio``。

    请求形态能力取自同一次能力合成，请求组装与投影直接读取：``max_reference_images`` 是每请求
    参考图上限（None = 不裁剪），``text_to_video`` / ``first_frame`` 是无图请求的能力位，
    ``voice_consistency`` 是声音一致性档位，``max_reference_audio_count`` 与
    ``reference_audio_per_image`` 描述参考音频的段数上限与是否逐段挂在参考图上。
    """

    route: VideoRoute
    generation_type: VideoGenerationType
    provider_id: str
    model_id: str
    resolution: str | None
    supported_durations: tuple[int, ...]
    allowed_durations: tuple[int, ...]
    excluded_durations: tuple[tuple[int, DurationExclusionReason], ...]
    duration_endpoint_fixed: bool
    requested_generate_audio: bool
    generate_audio: bool
    has_audio_track: bool
    audio_switch_controllable: bool
    max_reference_images: int | None
    text_to_video: bool
    first_frame: bool
    voice_consistency: VoiceConsistency
    max_reference_audio_count: int
    reference_audio_per_image: bool


@dataclass(frozen=True)
class VideoRequestFactsFailure:
    """求值失败：问题码、渲染参数与修复指引。参数键名属持久化契约，只增不改。"""

    code: str
    params: tuple[tuple[str, object], ...] = ()
    action: str = CONFIGURE_VIDEO_MODEL_ACTION

    def parameters(self) -> dict[str, object]:
        return dict(self.params)


class VideoRequestFactsError(ValueError):
    """消费方在需要成功事实的阶段拿到失败时抛出；``code`` / ``params`` 可直接进结构化错误与失败编码。"""

    def __init__(self, failure: VideoRequestFactsFailure) -> None:
        self.failure = failure
        super().__init__(failure.code)

    @property
    def code(self) -> str:
        return self.failure.code

    @property
    def params(self) -> dict[str, object]:
        return self.failure.parameters()


def require_video_request_facts(result: VideoRequestFacts | VideoRequestFactsFailure) -> VideoRequestFacts:
    """取出成功事实；失败即抛 :class:`VideoRequestFactsError`。"""

    if isinstance(result, VideoRequestFactsFailure):
        raise VideoRequestFactsError(result)
    return result


def audio_switch_conflict(facts: VideoRequestFacts) -> VideoRequestFactsFailure | None:
    """用户关闭音频但该桶的模型成片恒有声时，返回共同的问题码。"""

    if not facts.requested_generate_audio and facts.has_audio_track and not facts.audio_switch_controllable:
        return VideoRequestFactsFailure(
            "video_audio_switch_not_supported",
            (("provider", facts.provider_id), ("model", facts.model_id)),
        )
    return None


def video_audio_model_facts(
    provider_id: str,
    model_id: str,
    *,
    voice_consistency: str,
    generation_type: VideoGenerationType,
) -> tuple[bool, bool]:
    """返回 ``(has_audio_track, audio_switch_controllable)`` 的模型级事实。

    音轨形态按执行子路径分叉，须按落入的桶取值（可灵 v3-omni 这类「图生可控、参考生无开关」的
    型号）。自定义供应商与未登记模型没有逐模型声明，按无信号不收紧。
    """

    audio_track = builtin_video_audio_track(provider_id, model_id, generation_type=generation_type)
    if audio_track is None:
        return voice_consistency != "none", True
    return audio_track != "always_off", audio_track == "controllable"


async def evaluate_video_request_facts(
    project: dict,
    *,
    route: VideoRoute,
    generation_type: VideoGenerationType,
    identity: VideoRequestIdentity,
    resolver: ConfigResolver,
) -> VideoRequestFacts | VideoRequestFactsFailure:
    """对一次视频请求求值执行事实；解析不出时返回带类型的失败，不抛出、不回退。"""

    codes = _ROUTE_FAILURE_CODES[route]
    if isinstance(identity, ExecutionVideoIdentity):
        provider_id, model_id = identity.provider_id, identity.model_id
    else:
        try:
            selected = await resolver.resolve_video_backend(project, None, generation_type=generation_type)
        except VideoBucketCapabilityError as exc:
            return VideoRequestFactsFailure(exc.code, tuple(exc.params.items()))
        except (ValueError, SQLAlchemyError):
            return VideoRequestFactsFailure(codes["unavailable"], (("capability", generation_type),))
        provider_id, model_id = selected.provider_id, selected.model_id

    try:
        caps = await resolver.video_capabilities_for_model(
            provider_id, model_id, project, generation_type=generation_type
        )
    except VideoSupportedDurationsError as exc:
        return VideoRequestFactsFailure(codes[exc.kind], (("provider", exc.provider_id), ("model", exc.model_id)))
    except (ValueError, SQLAlchemyError):
        return VideoRequestFactsFailure(
            codes["unavailable"],
            (("capability", generation_type), ("provider", provider_id), ("model", model_id)),
        )
    capability_identity = str(caps["provider_id"]), str(caps["model"])
    if capability_identity != (provider_id, model_id):
        return VideoRequestFactsFailure(
            codes["unavailable"],
            (("capability", generation_type), ("provider", provider_id), ("model", model_id)),
        )
    identity_params = (("provider", provider_id), ("model", model_id))

    raw_durations = caps.get("supported_durations") or []
    if any(isinstance(d, bool) or not isinstance(d, int) or d <= 0 for d in raw_durations):
        return VideoRequestFactsFailure(codes["invalid"], identity_params)
    supported = tuple(sorted(set(raw_durations)))
    endpoint_fixed = bool(caps.get("duration_endpoint_fixed"))
    if not supported and not endpoint_fixed:
        return VideoRequestFactsFailure(codes["missing"], identity_params)

    try:
        resolution = await resolver.resolve_resolution(project, provider_id, model_id)
    except (ValueError, SQLAlchemyError):
        return VideoRequestFactsFailure(codes["unavailable"], (("capability", generation_type), *identity_params))

    # 参考图约束随桶生效：单元按可用参考图定桶，落 r2v 即带参考图、落 i2v 即不带。
    uses_reference_images = generation_type == "r2v"
    allowed = tuple(
        constrain_durations(
            provider_id,
            model_id,
            list(supported),
            resolution=resolution,
            uses_reference_images=uses_reference_images,
        )
    )
    if supported and not allowed:
        return VideoRequestFactsFailure(
            codes["incompatible"],
            (*identity_params, ("resolution", resolution), ("capability", generation_type)),
        )
    # 两条约束都排除同一秒数时报参考图：改分辨率救不回它。
    reference_allowed = (
        constrain_durations(provider_id, model_id, list(supported), uses_reference_images=True)
        if uses_reference_images
        else list(supported)
    )
    excluded: tuple[tuple[int, DurationExclusionReason], ...] = tuple(
        (d, "reference" if d not in reference_allowed else "resolution") for d in supported if d not in allowed
    )

    voice_consistency: VoiceConsistency = caps.get("voice_consistency") or "soft"
    has_audio_track, audio_switch_controllable = video_audio_model_facts(
        provider_id,
        model_id,
        voice_consistency=voice_consistency,
        generation_type=generation_type,
    )
    max_reference_images = caps.get("max_reference_images")
    return VideoRequestFacts(
        route=route,
        generation_type=generation_type,
        provider_id=provider_id,
        model_id=model_id,
        resolution=resolution,
        supported_durations=supported,
        allowed_durations=allowed,
        excluded_durations=excluded,
        duration_endpoint_fixed=endpoint_fixed,
        requested_generate_audio=bool(caps.get("requested_generate_audio")),
        generate_audio=bool(caps.get("generate_audio")) and has_audio_track,
        has_audio_track=has_audio_track,
        audio_switch_controllable=audio_switch_controllable,
        max_reference_images=int(max_reference_images) if max_reference_images is not None else None,
        text_to_video=bool(caps.get("text_to_video", True)),
        first_frame=bool(caps.get("first_frame")),
        voice_consistency=voice_consistency,
        max_reference_audio_count=int(caps.get("max_reference_audio_count") or 0),
        reference_audio_per_image=bool(caps.get("reference_audio_per_image")),
    )
