"""视频单元的当前请求投影。

投影是 advisory/current-state 读模型：调用方传入当前 project、script、unit，已经解析出的
资产候选与请求选项，得到报价、提交预检和限流路由共用的一份不可变事实。结果不携带 token、
fingerprint 或可执行请求快照；worker 开始处理时必须重新投影当前状态。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy.exc import SQLAlchemyError

from lib.asset_types import ASSET_SPECS, AssetSpec, asset_name_comparison_key, normalize_asset_bucket
from lib.config.resolver import (
    VideoBucketCapabilityError,
    VideoCapability,
    builtin_video_audio_track,
    constrain_durations,
    get_provider_fallback,
    video_capability_satisfied,
)
from lib.narration_delivery import (
    POST_PRODUCTION as POST_PRODUCTION,
)
from lib.narration_delivery import (
    USE_TTS,
    NarrationDeliveryPreparation,
    NarrationDeliveryRequestOptions,
    TtsSettingsResolver,
    VideoRequestCostFacts,
    prepare_current_narration_delivery,
)
from lib.narration_delivery import (
    NarrationDelivery as NarrationDelivery,
)
from lib.path_safety import PathTraversalError, safe_join
from lib.reference_video.duration_slots import DurationSlot, resolve_duration_slot
from lib.reference_video.text_parser import derive_references_from_text
from lib.schema_guards import is_int
from lib.script_models import ReferenceResource
from lib.speech_composition import admit_script_unit


@dataclass(frozen=True)
class ReferenceRequestOptions(NarrationDeliveryRequestOptions):
    """影响当前 unit 请求投影、但不属于剧本内容的调用选项。

    ``current_tts_duration_seconds``、``current_visual_duration_seconds`` 与
    ``current_reusable_visual_duration_seconds`` 只允许服务端 current-state seam 注入，不会序列化进队列。
    队列保存用户选择的交付方式与明确接受的时长档位，worker 再以最新剧本、TTS 和模型能力
    重投影；档位变化后旧确认不会继续放行。
    """

    current_tts_duration_seconds: float | None = field(default=None, repr=False, compare=False)
    narration_preparation: NarrationDeliveryPreparation | None = field(default=None, repr=False, compare=False)
    current_visual_duration_seconds: int | None = field(default=None, repr=False, compare=False)
    current_reusable_visual_duration_seconds: int | None = field(default=None, repr=False, compare=False)
    _legacy_duration_confirmed: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        NarrationDeliveryRequestOptions.__post_init__(self)
        floor = self.current_tts_duration_seconds
        if floor is not None and (not math.isfinite(floor) or floor <= 0):
            raise ValueError("current_tts_duration_seconds must be positive and finite or null")
        visual_duration = self.current_visual_duration_seconds
        if visual_duration is not None and not is_int(visual_duration, minimum=1):
            raise ValueError("current_visual_duration_seconds must be a positive integer or null")
        reusable_visual_duration = self.current_reusable_visual_duration_seconds
        if reusable_visual_duration is not None and not is_int(reusable_visual_duration, minimum=1):
            raise ValueError("current_reusable_visual_duration_seconds must be a positive integer or null")
        preparation = self.narration_preparation
        if preparation is not None and preparation.delivery != self.narration_delivery:
            raise ValueError("narration preparation must describe the selected delivery")

    @property
    def legacy_duration_confirmed(self) -> bool:
        """Whether an option-less task predates explicit tier coordinates."""

        return self._legacy_duration_confirmed

    @classmethod
    def from_payload(
        cls,
        payload: object,
        *,
        key: str = "reference_request_options",
        legacy_duration_confirmed: bool = False,
    ) -> ReferenceRequestOptions:
        """宽容读取队列 payload；缺少选项字段时可按调用方兼容语义完成时长确认。"""

        root = payload if isinstance(payload, dict) else {}
        if key not in root:
            return cls(_legacy_duration_confirmed=legacy_duration_confirmed)
        raw = root.get(key)
        if not isinstance(raw, dict):
            return cls()
        durable = NarrationDeliveryRequestOptions.from_payload(
            root,
            key=key,
        )
        return cls(
            narration_delivery=durable.narration_delivery,
            confirmed_request_duration_seconds=durable.confirmed_request_duration_seconds,
        )


@dataclass(frozen=True)
class ResolvedReferenceAsset:
    """一个逻辑引用展开出的图片候选；可用性由注入的资产适配器判断。"""

    path: Path
    reference: ReferenceResource
    kind: str = "asset"


@dataclass(frozen=True)
class ProviderProjectionCandidate:
    """当前任务类型桶的供应商模型组合与请求能力事实。"""

    capability: VideoCapability
    provider_id: str
    model_id: str
    supported_durations: tuple[int, ...]
    max_reference_images: int | None
    resolution: str | None
    generate_audio: bool
    requested_generate_audio: bool
    has_audio_track: bool
    audio_switch_controllable: bool
    voice_consistency: str = "soft"
    max_reference_audio_count: int = 0
    reference_audio_per_image: bool = False
    first_frame: bool = True
    text_to_video: bool = True

    @property
    def pair_key(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


ProjectionCostFacts = VideoRequestCostFacts


@dataclass(frozen=True)
class ProjectionProblem:
    """跨 Web、Agent 与队列可比较的结构化问题。"""

    code: str
    blocking: bool
    params: tuple[tuple[str, object], ...] = ()
    reason: str | None = None
    action: str | None = None
    locations: tuple[tuple[str | int, ...], ...] | None = None

    def parameters(self) -> dict[str, object]:
        return dict(self.params)

    def to_payload(self, *, unit_id: str) -> dict[str, object]:
        """返回 Web、Agent 与报价共用的问题信封。"""

        default_action, default_paths = _PROBLEM_PRESENTATION.get(
            self.code,
            ("review_request_configuration", (("video_units", unit_id),)),
        )
        payload: dict[str, object] = {
            "code": self.code,
            "blocking": self.blocking,
            "unit_id": unit_id,
            "locations": [{"path": list(path), "line": None} for path in (self.locations or default_paths)],
            "params": self.parameters(),
            "action": self.action or default_action,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


class ReferenceProjectionBlockedError(ValueError):
    """Execution-time rejection backed by the projector's canonical problem."""

    def __init__(self, problem: ProjectionProblem) -> None:
        if not problem.blocking:
            raise ValueError("projection failure must wrap a blocking problem")
        self.problem = problem
        super().__init__(problem.code)

    @property
    def code(self) -> str:
        return self.problem.code

    @property
    def params(self) -> dict[str, object]:
        return self.problem.parameters()


@dataclass(frozen=True)
class ReferenceUnitRequestProjection:
    """一个 unit 在调用瞬间的规范请求投影。"""

    unit_id: str
    declared_references: tuple[ReferenceResource, ...]
    available_assets: tuple[ResolvedReferenceAsset, ...]
    request_assets: tuple[ResolvedReferenceAsset, ...]
    declared_capability: VideoCapability
    hydrated_capability: VideoCapability
    provider_candidate: ProviderProjectionCandidate | None
    planned_duration: int
    narration_duration_floor: float | None
    current_visual_duration: int | None
    duration_input: int | float
    request_duration: DurationSlot | None
    cost: ProjectionCostFacts | None
    narration_preparation: NarrationDeliveryPreparation | None
    problems: tuple[ProjectionProblem, ...]

    @property
    def provider_id(self) -> str | None:
        return self.provider_candidate.provider_id if self.provider_candidate is not None else None

    @property
    def model_id(self) -> str | None:
        return self.provider_candidate.model_id if self.provider_candidate is not None else None

    @property
    def blocking_problems(self) -> tuple[ProjectionProblem, ...]:
        return tuple(problem for problem in self.problems if problem.blocking)

    def problem_payloads(self) -> list[dict[str, object]]:
        return [problem.to_payload(unit_id=self.unit_id) for problem in self.problems]

    def to_advisory_payload(self) -> dict[str, object]:
        """序列化跨入口可比较的 current-state 投影事实。"""

        payload: dict[str, object] = {
            "allowed": not self.blocking_problems,
            "kind": "reference_request_projection",
            "advisory": True,
            "unit_id": self.unit_id,
            "declared_capability": self.declared_capability,
            "hydrated_capability": self.hydrated_capability,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "planned_duration": self.planned_duration,
            "current_visual_duration": self.current_visual_duration,
            "duration_input": self.duration_input,
            "request_duration": self.request_duration.seconds if self.request_duration is not None else None,
            "problems": self.problem_payloads(),
        }
        if self.narration_preparation is not None:
            payload["narration_delivery"] = self.narration_preparation.to_payload()
        return payload


class ReferenceAssetAvailability(Protocol):
    """资产可用性适配器；生产实现检查项目内文件，测试可用内存替身。"""

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        raise NotImplementedError


class ReferenceCapabilityProjection(Protocol):
    """当前供应商模型组合能力的异步适配器。"""

    async def resolve_candidate(self, project: dict, capability: VideoCapability) -> ProviderProjectionCandidate:
        raise NotImplementedError


@dataclass(frozen=True)
class ReferenceAssetHydration:
    available: tuple[ResolvedReferenceAsset, ...]
    missing: tuple[ReferenceResource, ...]


def hydrate_reference_assets(
    declared: Sequence[ReferenceResource],
    resolved_assets: Sequence[ResolvedReferenceAsset],
    availability: ReferenceAssetAvailability,
) -> ReferenceAssetHydration:
    """把候选按声明范围过滤并给出实际可用图片与缺图逻辑引用。"""

    declared_keys = {(ref.type, asset_name_comparison_key(ref.name)) for ref in declared}
    candidates = tuple(asset for asset in resolved_assets if _asset_key(asset) in declared_keys)
    available = tuple(asset for asset in candidates if availability.is_available(asset))
    available_keys = {_asset_key(asset) for asset in available}
    missing = tuple(ref for ref in declared if (ref.type, asset_name_comparison_key(ref.name)) not in available_keys)
    return ReferenceAssetHydration(available=available, missing=missing)


class ProjectionResolutionError(ValueError):
    """生产适配器解析失败；``code`` 可直接进入结构化 problem。"""

    def __init__(self, code: str, **params: object) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


def reference_audio_model_facts(
    provider_id: str,
    model_id: str,
    *,
    voice_consistency: str,
    capability: VideoCapability,
) -> tuple[bool, bool]:
    """返回 ``(has_audio_track, audio_switch_controllable)`` 的模型级事实。

    ``capability`` 定的是执行路径：音轨形态按子路径分叉，参考生视频的镜头必须按 r2v 取值，否则
    可灵 v3-omni 这类「图生可控、参考生无开关」的型号会被当成开关可控（用户的音频配置在多图
    主体子路径上根本发不出去）。自定义供应商与未登记模型没有逐模型声明，按无信号不收紧。
    """

    audio_track = builtin_video_audio_track(provider_id, model_id, capability=capability)
    if audio_track is None:
        return voice_consistency != "none", True
    return audio_track != "always_off", audio_track == "controllable"


def strict_reference_durations(
    *,
    provider_id: str,
    model_id: str,
    durations: Sequence[int | float | str],
    resolution: str | None,
    capability: VideoCapability,
) -> tuple[int, ...]:
    """校验并按当前请求条件收窄时长；缺失或矛盾一律 fail loud。"""

    normalized_values: set[int] = set()
    for value in durations:
        if isinstance(value, bool):
            raise ProjectionResolutionError(
                "reference_supported_durations_invalid",
                provider=provider_id,
                model=model_id,
            )
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProjectionResolutionError(
                "reference_supported_durations_invalid",
                provider=provider_id,
                model=model_id,
            ) from exc
        if isinstance(value, float) and (not math.isfinite(value) or float(parsed) != value):
            raise ProjectionResolutionError(
                "reference_supported_durations_invalid",
                provider=provider_id,
                model=model_id,
            )
        if parsed <= 0:
            raise ProjectionResolutionError(
                "reference_supported_durations_invalid",
                provider=provider_id,
                model=model_id,
            )
        normalized_values.add(parsed)
    normalized = tuple(sorted(normalized_values))
    if not normalized:
        raise ProjectionResolutionError("reference_supported_durations_missing", provider=provider_id, model=model_id)
    allowed = constrain_durations(
        provider_id,
        model_id,
        list(normalized),
        resolution=resolution,
        uses_reference_images=capability == "r2v",
        fallback_on_empty=False,
    )
    if not allowed:
        raise ProjectionResolutionError(
            "reference_supported_durations_incompatible",
            provider=provider_id,
            model=model_id,
            resolution=resolution,
            capability=capability,
        )
    return tuple(allowed)


class FilesystemReferenceAssets:
    """以项目目录为边界检查图片候选实际存在且为普通文件。"""

    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        try:
            safe_join(self._project_path, asset.path, require_file=True)
        except (FileNotFoundError, OSError, PathTraversalError, TypeError):
            return False
        return True


def _candidate_path(project_path: Path, value: object) -> Path | None:
    if not isinstance(value, (str, Path)) or not str(value):
        return None
    try:
        return safe_join(project_path, value)
    except (OSError, PathTraversalError, TypeError):
        return None


def unit_reference_declarations(project: dict, unit: dict) -> tuple[ReferenceResource, ...]:
    """视频单元正文 → 该单元生成所用的逻辑参考图引用，按首次提及顺序。

    正文是唯一真相：引用不落盘，读侧一律经本函数派生，商品与其它资产走同一条规则、
    没有类型优先级（见 ADR 0064）。未登记的名字不产生引用——它只在渲染与预览侧发一条
    非阻断 warning，不挡住这次生成。
    """

    raw_text = unit.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    references, _missing = derive_references_from_text(text, project)
    return tuple(references)


def resolve_reference_assets(project: dict, project_path: Path, unit: dict) -> tuple[ResolvedReferenceAsset, ...]:
    """把正文派生的逻辑引用展开为图片候选，不把「路径已登记」误当成「文件存在」。

    每件资产同一条规则：有资产图就用资产图，没有才退到该资产的全部原图。商品与其它资产
    共用这条规则，不按类型排序、也不在有资产图时额外注入原图（见 ADR 0064 与 ADR 0034）。
    缺字段、未登记或越界路径不制造候选，由 projector 对照派生引用统一产出
    ``reference_asset_missing``。
    """

    result: list[ResolvedReferenceAsset] = []
    for reference in unit_reference_declarations(project, unit):
        spec = ASSET_SPECS[reference.type]
        bucket = normalize_asset_bucket(project.get(spec.bucket_key))
        entry = bucket.get(asset_name_comparison_key(reference.name))
        if not isinstance(entry, dict):
            continue
        sheet = _candidate_path(project_path, entry.get(spec.sheet_field))
        if sheet is not None:
            result.append(ResolvedReferenceAsset(path=sheet, reference=reference, kind="sheet"))
            continue
        for raw_path in _original_image_paths(entry, spec):
            original = _candidate_path(project_path, raw_path)
            if original is not None:
                result.append(ResolvedReferenceAsset(path=original, reference=reference, kind="original"))
    return tuple(result)


def _original_image_paths(entry: dict, spec: AssetSpec) -> list[object]:
    """该资产条目登记的全部原图路径，按声明顺序。"""

    paths: list[object] = []
    for field_name in spec.original_image_fields:
        value = entry.get(field_name)
        if isinstance(value, list):
            paths.extend(value)
        elif value:
            paths.append(value)
    return paths


class ConfigReferenceCapabilityProjection:
    """把 ``ConfigResolver`` 的当前配置解析成投影候选。"""

    def __init__(self, resolver: object) -> None:
        self._resolver = resolver
        self._cache: dict[VideoCapability, ProviderProjectionCandidate] = {}
        self._failures: dict[VideoCapability, ProjectionResolutionError] = {}

    async def resolve_candidate(self, project: dict, capability: VideoCapability) -> ProviderProjectionCandidate:
        cached = self._cache.get(capability)
        if cached is not None:
            return cached
        failure = self._failures.get(capability)
        if failure is not None:
            raise failure
        try:
            candidate = await self._resolve_uncached(project, capability)
        except ProjectionResolutionError as exc:
            self._failures[capability] = exc
            raise
        self._cache[capability] = candidate
        return candidate

    async def _resolve_uncached(self, project: dict, capability: VideoCapability) -> ProviderProjectionCandidate:
        try:
            caps = await self._resolver.video_capabilities_for_project(project, capability=capability)  # type: ignore[attr-defined]
        except VideoBucketCapabilityError as exc:
            raise ProjectionResolutionError(exc.code, **exc.params) from exc
        except (SQLAlchemyError, ValueError) as exc:
            message = str(exc)
            if "supported_durations" not in message:
                raise ProjectionResolutionError("reference_capability_unavailable", capability=capability) from exc
            code = (
                "reference_supported_durations_missing"
                if "is empty" in message
                else "reference_supported_durations_invalid"
            )
            raise ProjectionResolutionError(code, provider="unknown", model="unknown") from exc

        provider_id = str(caps.get("provider_id") or "")
        model_id = str(caps.get("model") or "")
        raw_durations = caps.get("supported_durations")
        if not isinstance(raw_durations, list):
            raise ProjectionResolutionError(
                "reference_supported_durations_invalid", provider=provider_id, model=model_id
            )
        try:
            resolution = await self._resolver.resolve_resolution(project, provider_id, model_id)  # type: ignore[attr-defined]
        except (SQLAlchemyError, ValueError) as exc:
            raise ProjectionResolutionError(
                "reference_capability_unavailable",
                capability=capability,
                provider=provider_id,
                model=model_id,
            ) from exc
        resolution = resolution or get_provider_fallback(provider_id)

        durations = strict_reference_durations(
            provider_id=provider_id,
            model_id=model_id,
            durations=raw_durations,
            resolution=resolution,
            capability=capability,
        )

        has_audio_track, audio_switch_controllable = reference_audio_model_facts(
            provider_id,
            model_id,
            voice_consistency=str(caps.get("voice_consistency") or "soft"),
            capability=capability,
        )
        max_references = caps.get("max_reference_images")
        return ProviderProjectionCandidate(
            capability=capability,
            provider_id=provider_id,
            model_id=model_id,
            supported_durations=durations,
            max_reference_images=int(max_references) if max_references is not None else None,
            resolution=resolution,
            generate_audio=bool(caps.get("generate_audio")),
            requested_generate_audio=bool(caps.get("requested_generate_audio")),
            has_audio_track=has_audio_track,
            audio_switch_controllable=audio_switch_controllable,
            voice_consistency=str(caps.get("voice_consistency") or "soft"),
            max_reference_audio_count=int(caps.get("max_reference_audio_count") or 0),
            reference_audio_per_image=bool(caps.get("reference_audio_per_image") or False),
            first_frame=bool(caps.get("first_frame")),
            text_to_video=bool(caps.get("text_to_video", True)),
        )


def clamp_reference_assets(
    assets: Sequence[ResolvedReferenceAsset], max_references: int | None
) -> tuple[ResolvedReferenceAsset, ...]:
    """超过上限时按正文的提及顺序保留前若干张——没有类型优先级。"""

    if max_references is None or len(assets) <= max_references:
        return tuple(assets)
    return tuple(assets[: max(0, max_references)])


_PROBLEM_PRESENTATION: dict[str, tuple[str, tuple[tuple[str | int, ...], ...]]] = {
    "reference_asset_missing": ("repair_reference_assets", (("text",),)),
    "reference_capability_changed": ("repair_reference_assets", (("text",),)),
    "reference_images_clamped": ("review_reference_selection", (("text",),)),
    "video_audio_switch_not_supported": (
        "enable_model_audio",
        (("generation_settings", "generate_audio"),),
    ),
    "reference_duration_confirmation_required": ("confirm_duration", (("duration_seconds",),)),
    "needs_replan": ("replan_unit", (("duration_seconds",),)),
    "reference_supported_durations_missing": ("configure_video_model", (("duration_seconds",),)),
    "reference_supported_durations_invalid": ("configure_video_model", (("duration_seconds",),)),
    "reference_supported_durations_incompatible": ("configure_video_model", (("duration_seconds",),)),
    "reference_capability_unavailable": ("configure_video_model", (("text",),)),
    "video_capability_missing_i2v": ("configure_video_model", (("text",),)),
    "video_capability_missing_r2v": ("configure_video_model", (("text",),)),
    "video_capability_missing_t2v": ("configure_video_model", (("text",),)),
}


def _problem(code: str, *, blocking: bool, **params: object) -> ProjectionProblem:
    return ProjectionProblem(code=code, blocking=blocking, params=tuple(params.items()))


def _asset_key(asset: ResolvedReferenceAsset) -> tuple[str, str]:
    return asset.reference.type, asset_name_comparison_key(asset.reference.name)


def _planned_duration(unit: dict) -> int:
    raw = unit.get("duration_seconds", 8)
    if isinstance(raw, bool):
        raise ValueError("duration_seconds must be a positive integer")
    value = int(raw or 8)
    if value <= 0:
        raise ValueError("duration_seconds must be a positive integer")
    return value


class ReferenceUnitRequestProjector:
    """把当前 unit 意图投影成所有读侧共用的规范请求事实。"""

    def __init__(
        self,
        capabilities: ReferenceCapabilityProjection,
        assets: ReferenceAssetAvailability,
    ) -> None:
        self._capabilities = capabilities
        self._assets = assets

    async def project_current(
        self,
        *,
        project: dict,
        script: dict,
        unit: dict,
        resolved_assets: Sequence[ResolvedReferenceAsset],
        options: ReferenceRequestOptions | None = None,
    ) -> ReferenceUnitRequestProjection:
        """投影调用瞬间状态；``script`` 显式入参锁定公共缝的完整上下文契约。"""

        del script
        options = options or ReferenceRequestOptions()
        canonical = unit_reference_declarations(project, unit)
        declared_capability: VideoCapability = "r2v" if canonical else "i2v"
        hydration = hydrate_reference_assets(canonical, resolved_assets, self._assets)
        available = hydration.available

        problems: list[ProjectionProblem] = []
        if options.narration_preparation is not None:
            problems.extend(
                ProjectionProblem(
                    code=delivery_problem.code,
                    blocking=delivery_problem.blocking,
                    params=delivery_problem.params,
                    reason=delivery_problem.reason,
                    action=delivery_problem.action,
                    locations=tuple(location.path for location in delivery_problem.locations),
                )
                for delivery_problem in options.narration_preparation.problems
            )
        if hydration.missing:
            missing = tuple((ref.type, ref.name) for ref in hydration.missing)
            problems.append(
                _problem(
                    "reference_asset_missing",
                    blocking=True,
                    missing=missing,
                    missing_text=", ".join(f"{asset_type}: {name}" for asset_type, name in missing),
                )
            )

        hydrated_capability: VideoCapability = "r2v" if available else "i2v"
        if hydrated_capability != declared_capability:
            problems.append(
                _problem(
                    "reference_capability_changed",
                    blocking=True,
                    declared=declared_capability,
                    hydrated=hydrated_capability,
                )
            )

        candidate: ProviderProjectionCandidate | None = None
        try:
            candidate = await self._capabilities.resolve_candidate(project, hydrated_capability)
        except ProjectionResolutionError as exc:
            code = exc.code
            error_params = {"capability": hydrated_capability, **exc.params}
            problems.append(
                _problem(
                    code,
                    blocking=True,
                    **error_params,
                )
            )
        except Exception:
            problems.append(
                _problem(
                    "reference_capability_unavailable",
                    blocking=True,
                    capability=hydrated_capability,
                )
            )

        request_assets = available
        if candidate is not None:
            if (
                hydrated_capability == "i2v"
                and not available
                and not video_capability_satisfied(
                    capability=hydrated_capability,
                    first_frame=candidate.first_frame,
                    max_reference_images=candidate.max_reference_images or 0,
                    text_to_video=candidate.text_to_video,
                    has_image=False,
                )
            ):
                problems.append(
                    _problem(
                        "video_capability_missing_t2v",
                        blocking=True,
                        provider=candidate.provider_id,
                        model=candidate.model_id,
                    )
                )
            request_assets = clamp_reference_assets(available, candidate.max_reference_images)
            if len(request_assets) < len(available):
                problems.append(
                    _problem(
                        "reference_images_clamped",
                        blocking=False,
                        count=len(available),
                        max_count=candidate.max_reference_images,
                        provider=candidate.provider_id,
                        model=candidate.model_id,
                    )
                )
            if (
                not candidate.requested_generate_audio
                and candidate.has_audio_track
                and not candidate.audio_switch_controllable
            ):
                problems.append(
                    _problem(
                        "video_audio_switch_not_supported",
                        blocking=True,
                        provider=candidate.provider_id,
                        model=candidate.model_id,
                    )
                )

        planned_duration = _planned_duration(unit)
        prepared_floor = (
            options.narration_preparation.duration_floor if options.narration_preparation is not None else None
        )
        narration_floor = (
            (prepared_floor if prepared_floor is not None else options.current_tts_duration_seconds)
            if options.narration_delivery == USE_TTS
            else None
        )
        if narration_floor is not None and (not math.isfinite(narration_floor) or narration_floor <= 0):
            raise ValueError("narration_duration_floor must be positive")
        duration_input: int | float = max(planned_duration, narration_floor or 0)
        request_duration: DurationSlot | None = None
        cost: ProjectionCostFacts | None = None

        if candidate is not None:
            if not candidate.supported_durations:
                problems.append(
                    _problem(
                        "reference_supported_durations_missing",
                        blocking=True,
                        provider=candidate.provider_id,
                        model=candidate.model_id,
                    )
                )
            else:
                slot = resolve_duration_slot(duration_input, candidate.supported_durations)
                request_duration = slot
                if slot.adjustment == "down":
                    problems.append(
                        _problem(
                            "needs_replan",
                            blocking=True,
                            duration_input=duration_input,
                            maximum_duration=slot.seconds,
                        )
                    )
                elif (
                    slot.seconds != (options.current_visual_duration_seconds or planned_duration)
                    and not options.legacy_duration_confirmed
                    and options.confirmed_request_duration_seconds != slot.seconds
                ):
                    problems.append(
                        _problem(
                            "reference_duration_confirmation_required",
                            blocking=True,
                            script_duration=planned_duration,
                            duration_input=duration_input,
                            request_duration=slot.seconds,
                            adjustment=slot.adjustment,
                            current_visual_duration=options.current_visual_duration_seconds,
                        )
                    )
                cost = ProjectionCostFacts(
                    provider_id=candidate.provider_id,
                    model_id=candidate.model_id,
                    resolution=candidate.resolution,
                    duration_seconds=slot.seconds,
                    generate_audio=candidate.generate_audio,
                )

        return ReferenceUnitRequestProjection(
            unit_id=str(unit.get("unit_id") or ""),
            declared_references=canonical,
            available_assets=available,
            request_assets=request_assets,
            declared_capability=declared_capability,
            hydrated_capability=hydrated_capability,
            provider_candidate=candidate,
            planned_duration=planned_duration,
            narration_duration_floor=narration_floor,
            current_visual_duration=options.current_visual_duration_seconds,
            duration_input=duration_input,
            request_duration=request_duration,
            cost=cost,
            narration_preparation=options.narration_preparation,
            problems=tuple(problems),
        )


async def project_reference_unit_request(
    *,
    project: dict,
    script: dict,
    unit: dict,
    project_path: Path,
    options: ReferenceRequestOptions | None = None,
    resolver: object | None = None,
    tts_settings_resolver: TtsSettingsResolver | None = None,
    tts_in_progress: bool = False,
    current_options_materialized: bool = False,
) -> ReferenceUnitRequestProjection:
    """生产入口：从当前项目文件与配置直接构造一次 advisory 投影。"""

    if resolver is None:
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        resolver = ConfigResolver(async_session_factory)
    options = options or ReferenceRequestOptions()
    if not current_options_materialized:
        options = await materialize_current_reference_request_options(
            project=project,
            script=script,
            unit=unit,
            project_path=project_path,
            options=options,
            resolver=tts_settings_resolver or cast(TtsSettingsResolver, resolver),
            tts_in_progress=tts_in_progress,
        )
    projector = ReferenceUnitRequestProjector(
        ConfigReferenceCapabilityProjection(resolver),
        FilesystemReferenceAssets(project_path),
    )
    return await projector.project_current(
        project=project,
        script=script,
        unit=unit,
        resolved_assets=resolve_reference_assets(project, project_path, unit),
        options=options,
    )


async def materialize_current_reference_request_options(
    *,
    project: dict,
    script: dict,
    unit: dict,
    project_path: Path,
    options: ReferenceRequestOptions,
    resolver: TtsSettingsResolver,
    tts_in_progress: bool = False,
    episode: int | None = None,
) -> ReferenceRequestOptions:
    """Attach current, server-owned TTS facts without changing durable request facts."""

    if options.narration_delivery != USE_TTS:
        return replace(
            options,
            current_tts_duration_seconds=None,
            narration_preparation=None,
        )
    if not isinstance(episode, int) or isinstance(episode, bool):
        raise ValueError("reference video script requires an integer episode for TTS delivery")
    admission = admit_script_unit("video_units", unit)
    preparation = await prepare_current_narration_delivery(
        project=project,
        episode=episode,
        preparation=admission.preparation,
        project_path=project_path,
        delivery=options.narration_delivery,
        resolver=resolver,
        tts_in_progress=tts_in_progress,
    )
    return replace(
        options,
        current_tts_duration_seconds=preparation.duration_floor,
        narration_preparation=preparation,
    )
