"""纯同步视频请求事实与项目级视频能力投影（ADR 0086）。

只消费两份已就绪输入：单次解析产出的不可变能力快照，以及当前项目的纯字典偏好。
不访问数据库，也不回查配置解析器。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from arcreel_catalog_core import DurationConstraints
from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.providers import is_custom_provider
from lib.backends.video_capabilities import projected_video_capabilities
from lib.capabilities.kernel import CapabilitySnapshot, FixedDuration, IncompatibleInputsError, RequestConditions
from lib.config.resolver import (
    DurationExclusionReason,
    VideoGenerationType,
    VoiceConsistency,
    apply_duration_constraints,
    caps_generation_mode,
    derive_voice_consistency,
    duration_endpoint_fixed_reason,
    project_resolution,
)
from lib.episode.episode_target_duration import project_episode_target_duration
from lib.speech.character_voice import character_voice_binding

VideoRoute = Literal["storyboard", "reference_video"]


def plan_capability_inputs(project: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(
        project.get(key)
        for key in (
            "content_mode",
            "generation_mode",
            "video_backend",
            "video_provider_i2v",
            "video_provider_r2v",
            "model_settings",
            "video_model_settings",
            "video_generate_audio",
            "default_duration",
            "character_voice_binding",
        )
    )


CONFIGURE_VIDEO_MODEL_ACTION = "configure_video_model"

ENDPOINT_FIXED_PLANNING_DURATIONS: list[int] = [4, 8]

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


def route_failure_code(route: VideoRoute, kind: _FailureKind) -> str:
    return _ROUTE_FAILURE_CODES[route][kind]


@dataclass(frozen=True)
class ResolutionOverride:
    """预览尚未保存的分辨率：取代项目已保存的档位（``None`` = 表单选了「自动」，按项目未存档位解析）。

    只供设置页与创建向导预览编辑中的配置；执行侧与其他读侧不传，求值按已保存配置进行。
    """

    resolution: str | None


@dataclass(frozen=True)
class VideoRequestFacts:
    """一次视频请求的执行事实。

    ``resolution`` 为 None 表示请求不携带分辨率参数。``supported_durations`` 是模型声明的全集，
    ``allowed_durations`` 是按请求分辨率、参考图与尾帧收窄后的档位，``excluded_durations`` 给出全集中
    每个被排除秒数的成因。时长由端点固定时（``docs/adr/0082``）三者都是合法空集。

    音轨四项：``requested_generate_audio`` 是用户的音频开关，``generate_audio`` 是计价口径的
    有无音轨，``has_audio_track`` 是成片有无音轨，``audio_switch_controllable`` 是开关是否可控。
    计价所需的事实是执行模型、``resolution``、请求秒数与 ``generate_audio``。

    请求形态能力取自同一次能力合成，请求组装与投影直接读取：``max_reference_images`` 是每请求
    参考图上限的生效值（None = 未收录，不裁剪；0 = 不接受），``text_to_video`` / ``first_frame`` 是无图请求的能力位，
    ``voice_consistency`` 是声音一致性档位，``max_reference_audio_count`` 与
    ``reference_audio_per_image`` 描述参考音频的段数上限（None = 未收录）与是否逐段挂在参考图上。
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
    max_reference_audio_count: int | None
    reference_audio_per_image: bool
    uses_end_frame: bool = False
    service_tier: str = "default"
    request_snapshot: CapabilitySnapshot | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class VideoRequestCostFacts:
    """报价共用的供应商请求坐标：一份视频请求事实，加上这次申请的秒数与请求形态。

    ``aspect_ratio`` 是项目的视频宽高比；``has_first_frame`` 表示请求带首帧（分镜图生视频），
    ``reference_image_count`` 是发出的参考图张数（参考生视频）。三者与执行时构造的请求同源，提交前预估据此
    还原一份设想的供应商请求（:meth:`provider_request`），交给调用通道按用量契约给出计量。
    """

    request_facts: VideoRequestFacts
    duration_seconds: int
    aspect_ratio: str
    has_first_frame: bool = False
    reference_image_count: int = 0

    def provider_request(self) -> VideoGenerationRequest:
        """执行时会发给调用通道的请求的设想形态：图片只占位，不读文件，只用于求计量。

        分镜图生视频的首帧是按项目宽高比生成的分镜图；要求首帧时宽高比交给上游自适应的通道
        （``first_frame_ratio_adaptive_only``），成片宽高比即分镜图的宽高比，这里因此直接写项目宽高比。
        """
        facts = self.request_facts
        return VideoGenerationRequest(
            prompt="",
            output_path=Path("estimate.mp4"),
            aspect_ratio=self.aspect_ratio,
            duration_seconds=self.duration_seconds,
            resolution=self.request_facts.resolution,
            end_image=Path("last-frame.png") if facts.uses_end_frame else None,
            billed_audio=facts.generate_audio,
            service_tier=facts.service_tier,
            video_capabilities=projected_video_capabilities(facts.request_snapshot)
            if facts.request_snapshot is not None
            else None,
            start_image=Path("first-frame.png") if self.has_first_frame else None,
            reference_images=[Path(f"reference-{index}.png") for index in range(self.reference_image_count)] or None,
            generate_audio=facts.requested_generate_audio,
        )

    @property
    def provider_id(self) -> str:
        return self.request_facts.provider_id

    @property
    def model_id(self) -> str:
        return self.request_facts.model_id

    @property
    def resolution(self) -> str | None:
        return self.request_facts.resolution

    @property
    def generate_audio(self) -> bool:
        return self.request_facts.generate_audio


@dataclass(frozen=True)
class VideoRequestFactsFailure:
    """求值失败：问题码、渲染参数与修复指引。参数键名属持久化契约，只增不改。"""

    code: str
    params: tuple[tuple[str, object], ...] = ()
    action: str = CONFIGURE_VIDEO_MODEL_ACTION
    excluded_durations: tuple[tuple[int, DurationExclusionReason], ...] = ()

    def parameters(self) -> dict[str, object]:
        return dict(self.params)

    def summary(self) -> str:
        """问题码与渲染参数的单行摘要（``code（k=v, …）``），供 Agent 回执与草稿违约文案引用。"""
        params = ", ".join(f"{key}={value}" for key, value in self.params)
        return f"{self.code}（{params}）" if params else self.code

    def problem_payload(self) -> dict[str, object]:
        """读侧问题信封：问题码、渲染参数与修复指引，Web 与 Agent 载荷同形。"""
        return {"code": self.code, "params": self.parameters(), "action": self.action}


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


def planning_durations(facts: VideoRequestFacts) -> list[int]:
    """剧本规划可选的时长档位：收窄后的档位；时长由端点固定时借 :data:`ENDPOINT_FIXED_PLANNING_DURATIONS`。

    成功事实的 ``allowed_durations`` 只在端点固定时为空（收成空集是失败），本函数因此对成功事实
    恒返回非空档位。分镜路线的剧本规划、参考路线的拆分与提示词编写都读这一处，与预检、执行同一份
    收窄结果；借用只发生在规划内部，界面与 Agent 载荷仍拿空集加端点固定标志。
    """

    if facts.allowed_durations:
        return list(facts.allowed_durations)
    if facts.duration_endpoint_fixed:
        return list(ENDPOINT_FIXED_PLANNING_DURATIONS)
    return []


def reference_migration_durations(
    with_references: VideoRequestFacts | VideoRequestFactsFailure,
    without_references: VideoRequestFacts | VideoRequestFactsFailure,
) -> list[int] | None:
    """参考路线草稿结构收编用的时长全集：r2v 与 i2v 两桶声明全集的并集；任一桶解析不出时为 None。

    内容确认转换与 web 内容确认两个在线入口共用这一处，谁先迁移落盘都得到同一组档位成员。
    """

    if isinstance(with_references, VideoRequestFacts) and isinstance(without_references, VideoRequestFacts):
        return sorted(set(with_references.supported_durations) | set(without_references.supported_durations))
    return None


def audio_switch_conflict(facts: VideoRequestFacts) -> VideoRequestFactsFailure | None:
    """用户关闭音频但该桶的模型成片恒有声时，返回共同的问题码。"""

    if not facts.requested_generate_audio and facts.has_audio_track and not facts.audio_switch_controllable:
        return VideoRequestFactsFailure(
            "video_audio_switch_not_supported",
            (("provider", facts.provider_id), ("model", facts.model_id)),
        )
    return None


def _effective_resolution(
    snapshot: CapabilitySnapshot,
    project: dict | None,
    resolution_override: ResolutionOverride | None,
) -> str | None:
    if resolution_override is not None:
        return resolution_override.resolution or snapshot.default_resolution
    saved = project_resolution(project, snapshot.provider_id, snapshot.model_id) if project is not None else None
    return saved or snapshot.default_resolution


def snapshot_video_request_facts(
    snapshot: CapabilitySnapshot,
    project: dict | None,
    *,
    route: VideoRoute,
    generation_type: VideoGenerationType,
    requested_audio: bool,
    uses_end_frame: bool = False,
    service_tier: str = "default",
    resolution_override: ResolutionOverride | None = None,
) -> VideoRequestFacts | VideoRequestFactsFailure:
    provider, model = snapshot.provider_id, snapshot.model_id
    resolution = _effective_resolution(snapshot, project, resolution_override)
    inputs: set[str] = {"reference_images"} if generation_type == "r2v" else set()
    if route == "storyboard":
        inputs.add("first_frame")
    if uses_end_frame:
        inputs.add("last_frame")
    identity_params = (("provider", provider), ("model", model))
    try:
        req_snapshot = snapshot.for_request(
            RequestConditions(generation_type, resolution, service_tier, frozenset(inputs))
        )
    except IncompatibleInputsError:
        return VideoRequestFactsFailure(
            "video_reference_images_with_frames_unsupported", (("model", model),), action="fix_input"
        )
    values = req_snapshot.effective
    raw_durations = values.get("durations") or ()
    if any(isinstance(d, bool) or not isinstance(d, int) or d <= 0 for d in raw_durations):
        return VideoRequestFactsFailure(_ROUTE_FAILURE_CODES[route]["invalid"], identity_params)
    supported = tuple(sorted(set(raw_durations)))
    fixed = isinstance(req_snapshot.duration, FixedDuration)
    if not supported and not fixed:
        return VideoRequestFactsFailure(_ROUTE_FAILURE_CODES[route]["missing"], identity_params)
    constraints = DurationConstraints(**values.get("duration_constraints", {}))
    durations = _request_durations(
        supported,
        constraints,
        resolution,
        "reference_images" in inputs,
        uses_end_frame,
        route,
        generation_type,
        identity_params,
    )
    if isinstance(durations, VideoRequestFactsFailure):
        return durations
    if uses_end_frame and not values["last_frame"]:
        return VideoRequestFactsFailure("video_last_frame_unsupported", identity_params, action="fix_input")
    audio = req_snapshot.audio(generation_type, requested=requested_audio)
    model_has_audio = req_snapshot.audio(generation_type, requested=True).output
    voice = derive_voice_consistency(
        reference_audio_mode=values["reference_audio_mode"],
        generation_mode=caps_generation_mode(project),
        has_audio=model_has_audio,
        character_voice_binding=character_voice_binding(project),
    )
    return VideoRequestFacts(
        route=route,
        generation_type=generation_type,
        provider_id=provider,
        model_id=model,
        resolution=resolution,
        uses_end_frame=uses_end_frame,
        service_tier=service_tier,
        request_snapshot=req_snapshot,
        supported_durations=supported,
        allowed_durations=durations[0],
        excluded_durations=durations[1],
        duration_endpoint_fixed=fixed,
        requested_generate_audio=requested_audio,
        generate_audio=audio.billed,
        has_audio_track=model_has_audio,
        audio_switch_controllable=audio.switch is not None,
        max_reference_images=req_snapshot.reference_images.to_json(),
        text_to_video=values["text_to_video"],
        first_frame=values["first_frame"],
        voice_consistency=voice,
        max_reference_audio_count=values["max_reference_audio_count"],
        reference_audio_per_image=values.get("reference_audio_per_image", False),
    )


def duration_constraints_payload(facts: VideoRequestFacts, *, include_planning: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "resolution": facts.resolution,
        "uses_reference_images": facts.generation_type == "r2v",
        "allowed": list(facts.allowed_durations),
        "excluded": dict(facts.excluded_durations),
    }
    if include_planning:
        payload["planning"] = planning_durations(facts)
    return payload


def end_frame_unit_durations_payload(
    without_end_frame: VideoRequestFacts,
    with_end_frame: VideoRequestFacts | VideoRequestFactsFailure,
) -> dict[str, Any]:
    return {
        "without_end_frame": list(without_end_frame.allowed_durations),
        "with_end_frame": list(with_end_frame.allowed_durations)
        if isinstance(with_end_frame, VideoRequestFacts)
        else [],
        "excluded": dict(with_end_frame.excluded_durations),
        "problem": None if isinstance(with_end_frame, VideoRequestFacts) else with_end_frame.problem_payload(),
    }


def _project_default_duration(project: dict | None) -> int | None:
    if project is None:
        return None
    raw_default = project.get("default_duration")
    if isinstance(raw_default, int) and not isinstance(raw_default, bool):
        return raw_default
    if isinstance(raw_default, str) and raw_default.strip().isdigit():
        return int(raw_default.strip())
    return None


def _project_content_mode(project: dict | None) -> str | None:
    if project is None:
        return None
    cm = project.get("content_mode")
    return cm if isinstance(cm, str) and cm else None


def project_video_capabilities_with_facts(
    snapshot: CapabilitySnapshot,
    project: dict | None,
    *,
    generation_type: VideoGenerationType,
    requested_audio: bool,
    resolution_override: ResolutionOverride | None = None,
    service_tier: str = "default",
    route: VideoRoute | None = None,
    include_planning: bool = False,
) -> tuple[dict[str, Any], VideoRequestFacts]:
    resolved_route: VideoRoute = route or (
        "reference_video"
        if generation_type == "r2v" or caps_generation_mode(project) == "reference_video"
        else "storyboard"
    )
    facts = require_video_request_facts(
        snapshot_video_request_facts(
            snapshot,
            project,
            route=resolved_route,
            generation_type=generation_type,
            requested_audio=requested_audio,
            uses_end_frame=False,
            service_tier=service_tier,
            resolution_override=resolution_override,
        )
    )
    with_end_frame = snapshot_video_request_facts(
        snapshot,
        project,
        route=resolved_route,
        generation_type=generation_type,
        requested_audio=requested_audio,
        uses_end_frame=True,
        service_tier=service_tier,
        resolution_override=resolution_override,
    )
    ef_inputs = ({"reference_images"} if generation_type == "r2v" else {"first_frame"}) | {"last_frame"}
    try:
        ef_snapshot = snapshot.for_request(
            RequestConditions(generation_type, facts.resolution, service_tier, frozenset(ef_inputs))
        )
        last_frame = bool(ef_snapshot.effective["last_frame"])
    except IncompatibleInputsError:
        last_frame = False

    supported = list(facts.supported_durations)
    fixed = facts.duration_endpoint_fixed
    payload = {
        "provider_id": facts.provider_id,
        "model": facts.model_id,
        "supported_durations": supported,
        "max_duration": max(supported, default=0),
        "max_reference_images": facts.max_reference_images,
        "text_to_video": facts.text_to_video,
        "first_frame": facts.first_frame,
        "last_frame": last_frame,
        "generate_audio": facts.generate_audio,
        "requested_generate_audio": facts.requested_generate_audio,
        "max_reference_audio_count": facts.max_reference_audio_count,
        "reference_audio_per_image": facts.reference_audio_per_image,
        "source": "custom" if is_custom_provider(facts.provider_id) else "registry",
        "duration_endpoint_fixed": fixed,
        "duration_endpoint_fixed_reason": duration_endpoint_fixed_reason(fixed),
        "default_duration": _project_default_duration(project),
        "episode_target_duration": project_episode_target_duration(project),
        "content_mode": _project_content_mode(project),
        "generation_mode": caps_generation_mode(project),
        "voice_consistency": facts.voice_consistency,
        "duration_constraints": duration_constraints_payload(facts, include_planning=include_planning),
        "end_frame_unit_durations": end_frame_unit_durations_payload(facts, with_end_frame),
    }
    return payload, facts


def project_video_capabilities(
    snapshot: CapabilitySnapshot,
    project: dict | None,
    *,
    generation_type: VideoGenerationType,
    requested_audio: bool,
    resolution_override: ResolutionOverride | None = None,
    service_tier: str = "default",
    route: VideoRoute | None = None,
    include_planning: bool = False,
) -> dict[str, Any]:
    payload, _ = project_video_capabilities_with_facts(
        snapshot,
        project,
        generation_type=generation_type,
        requested_audio=requested_audio,
        resolution_override=resolution_override,
        service_tier=service_tier,
        route=route,
        include_planning=include_planning,
    )
    return payload


def _request_durations(
    supported: tuple[int, ...],
    constraints: DurationConstraints | None,
    resolution: str | None,
    uses_reference_images: bool,
    uses_end_frame: bool,
    route: VideoRoute,
    generation_type: VideoGenerationType,
    identity_params: tuple[tuple[str, str], ...],
):
    codes = _ROUTE_FAILURE_CODES[route]
    allowed = tuple(
        apply_duration_constraints(
            list(supported),
            constraints,
            resolution=resolution,
            uses_reference_images=uses_reference_images,
            uses_end_frame=uses_end_frame,
        )
    )
    reference_allowed = (
        apply_duration_constraints(list(supported), constraints, uses_reference_images=True)
        if uses_reference_images
        else list(supported)
    )
    end_frame_allowed = (
        apply_duration_constraints(list(supported), constraints, uses_end_frame=True)
        if uses_end_frame
        else list(supported)
    )
    excluded: tuple[tuple[int, DurationExclusionReason], ...] = tuple(
        (d, "end_frame" if d not in end_frame_allowed else "reference" if d not in reference_allowed else "resolution")
        for d in supported
        if d not in allowed
    )

    if supported and not allowed:
        if uses_end_frame and apply_duration_constraints(
            list(supported), constraints, resolution=resolution, uses_reference_images=uses_reference_images
        ):
            return VideoRequestFactsFailure(
                "video_end_frame_durations_incompatible",
                identity_params,
                action="fix_input",
                excluded_durations=excluded,
            )
        return VideoRequestFactsFailure(
            codes["incompatible"],
            (*identity_params, ("resolution", resolution), ("capability", generation_type)),
            excluded_durations=excluded,
        )

    return allowed, excluded


def end_frame_duration_failure(facts: VideoRequestFacts, duration: object) -> VideoRequestFactsFailure | None:
    """已选秒数被尾帧约束剔除时明确拒绝；不替用户改秒数。"""
    if not isinstance(duration, int | float | str) or isinstance(duration, bool):
        return None
    try:
        seconds = float(duration)
    except ValueError:
        return None
    if not seconds.is_integer() or dict(facts.excluded_durations).get(int(seconds)) != "end_frame":
        return None
    return VideoRequestFactsFailure(
        "video_duration_end_frame_not_supported",
        (("duration", int(seconds)), ("supported", ", ".join(str(d) for d in facts.allowed_durations))),
        action="fix_input",
    )
