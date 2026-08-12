"""参考视频 unit 的当前请求投影。

投影是 advisory/current-state 读模型：调用方传入当前 project、script、unit，已经解析出的
资产候选与请求选项，得到报价、提交预检和限流路由共用的一份不可变事实。结果不携带 token、
fingerprint 或可执行请求快照；worker 开始处理时必须重新投影当前状态。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from sqlalchemy.exc import SQLAlchemyError

from lib.asset_types import ASSET_SPECS, asset_name_comparison_key, normalize_asset_bucket
from lib.config.registry import (
    model_audio_switch_controllable,
    model_has_audio_track,
    model_info_for,
)
from lib.config.resolver import VideoBucketCapabilityError, VideoCapability, get_provider_fallback
from lib.path_safety import PathTraversalError, safe_join
from lib.reference_video.duration_slots import DurationSlot, resolve_duration_slot
from lib.script_models import ReferenceResource

POST_PRODUCTION = "post_production"
USE_TTS = "use_tts"
NarrationDelivery = Literal["post_production", "use_tts"]


@dataclass(frozen=True)
class ReferenceRequestOptions:
    """影响当前 unit 请求投影、但不属于剧本内容的调用选项。

    ``narration_duration_floor`` 是上游已经求出的实际旁白音频时长。本模块不查询 TTS 状态，
    也不拥有旁白生命周期；只有调用方明确选择 ``use_tts`` 时才把它作为申请时长下限。
    """

    narration_delivery: NarrationDelivery = POST_PRODUCTION
    narration_duration_floor: float | None = None
    duration_confirmed: bool = False

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "narration_delivery": self.narration_delivery,
            "duration_confirmed": self.duration_confirmed,
        }
        if self.narration_duration_floor is not None:
            payload["narration_duration_floor"] = self.narration_duration_floor
        return payload

    @classmethod
    def from_payload(cls, payload: object, *, legacy_duration_confirmed: bool = False) -> ReferenceRequestOptions:
        """宽容读取队列 payload；缺少选项字段时可按调用方兼容语义完成时长确认。"""

        root = payload if isinstance(payload, dict) else {}
        if "reference_request_options" not in root:
            return cls(duration_confirmed=legacy_duration_confirmed)
        raw = root.get("reference_request_options")
        if not isinstance(raw, dict):
            return cls()
        delivery = raw.get("narration_delivery")
        if delivery not in (POST_PRODUCTION, USE_TTS):
            delivery = POST_PRODUCTION
        floor = raw.get("narration_duration_floor")
        normalized_floor: float | None = None
        if isinstance(floor, (int, float)) and not isinstance(floor, bool):
            try:
                candidate_floor = float(floor)
            except (OverflowError, ValueError):
                pass
            else:
                if math.isfinite(candidate_floor) and candidate_floor > 0:
                    normalized_floor = candidate_floor
        confirmed = raw.get("duration_confirmed")
        return cls(
            narration_delivery=delivery,
            narration_duration_floor=normalized_floor,
            duration_confirmed=confirmed if isinstance(confirmed, bool) else False,
        )


@dataclass(frozen=True)
class ResolvedReferenceAsset:
    """一个逻辑引用展开出的图片候选；可用性由注入的资产适配器判断。"""

    path: Path
    reference: ReferenceResource
    kind: str = "asset"


@dataclass(frozen=True)
class ProviderProjectionCandidate:
    """当前能力桶的 provider/model 与请求能力事实。"""

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

    @property
    def pair_key(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


@dataclass(frozen=True)
class ProjectionCostFacts:
    """报价器所需的规范化事实；金额仍由各价格表按这组事实计算。"""

    provider_id: str
    model_id: str
    resolution: str | None
    duration_seconds: int
    generate_audio: bool


@dataclass(frozen=True)
class ProjectionProblem:
    """跨 Web、Agent 与队列可比较的结构化问题。"""

    code: str
    blocking: bool
    params: tuple[tuple[str, object], ...] = ()

    def parameters(self) -> dict[str, object]:
        return dict(self.params)

    def to_payload(self, *, unit_id: str) -> dict[str, object]:
        """返回 Web、Agent 与报价共用的问题信封。"""

        action, paths = _PROBLEM_PRESENTATION.get(
            self.code,
            ("review_request_configuration", (("video_units", unit_id),)),
        )
        return {
            "code": self.code,
            "blocking": self.blocking,
            "unit_id": unit_id,
            "locations": [{"path": list(path), "line": None} for path in paths],
            "params": self.parameters(),
            "action": action,
        }


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
    duration_input: int | float
    request_duration: DurationSlot | None
    cost: ProjectionCostFacts | None
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

        return {
            "allowed": not self.blocking_problems,
            "kind": "reference_request_projection",
            "advisory": True,
            "unit_id": self.unit_id,
            "declared_capability": self.declared_capability,
            "hydrated_capability": self.hydrated_capability,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "planned_duration": self.planned_duration,
            "duration_input": self.duration_input,
            "request_duration": self.request_duration.seconds if self.request_duration is not None else None,
            "problems": self.problem_payloads(),
        }


class ReferenceAssetAvailability(Protocol):
    """资产可用性适配器；生产实现检查项目内文件，测试可用内存替身。"""

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        raise NotImplementedError


class ReferenceCapabilityProjection(Protocol):
    """当前 provider/model 能力的异步适配器。"""

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
) -> tuple[bool, bool]:
    """返回 ``(has_audio_track, audio_switch_controllable)`` 的模型级事实。"""

    model_info = model_info_for(provider_id, model_id)
    if model_info is None:
        return voice_consistency != "none", True
    return model_has_audio_track(provider_id, model_info), model_audio_switch_controllable(model_info)


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
    model_info = model_info_for(provider_id, model_id)
    if model_info is None:
        return normalized
    allowed = list(normalized)
    if capability == "r2v" and model_info.reference_image_durations:
        allowed = [value for value in allowed if value in model_info.reference_image_durations]
    by_resolution = model_info.duration_resolution_constraints.get(resolution.strip().lower()) if resolution else None
    if by_resolution:
        allowed = [value for value in allowed if value in by_resolution]
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


def resolve_reference_assets(project: dict, project_path: Path, unit: dict) -> tuple[ResolvedReferenceAsset, ...]:
    """把当前逻辑引用展开为图片候选，不把“路径已登记”误当成“文件存在”。

    产品按 sheet → 原图展开；其它资产各展开一张 sheet。缺字段、未登记或越界路径不制造
    候选，由 projector 对照声明引用统一产出 ``reference_asset_missing``。
    """

    references = canonicalize_references(unit.get("references"))
    result: list[ResolvedReferenceAsset] = []
    for reference in references:
        spec = ASSET_SPECS[reference.type]
        bucket = normalize_asset_bucket(project.get(spec.bucket_key))
        entry = bucket.get(asset_name_comparison_key(reference.name))
        if not isinstance(entry, dict):
            continue
        if reference.type == "product":
            sheet = _candidate_path(project_path, entry.get(spec.sheet_field))
            if sheet is not None:
                result.append(ResolvedReferenceAsset(path=sheet, reference=reference, kind="sheet"))
            originals = entry.get("reference_images")
            if isinstance(originals, list):
                for raw_path in originals:
                    original = _candidate_path(project_path, raw_path)
                    if original is not None:
                        result.append(ResolvedReferenceAsset(path=original, reference=reference, kind="original"))
            continue
        sheet = _candidate_path(project_path, entry.get(spec.sheet_field))
        if sheet is not None:
            result.append(ResolvedReferenceAsset(path=sheet, reference=reference))
    return tuple(result)


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
        )
        max_references = caps.get("max_reference_images")
        candidate = ProviderProjectionCandidate(
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
        )
        return candidate


def canonicalize_references(references: object) -> tuple[ReferenceResource, ...]:
    """按 product → character → scene → prop 稳定排序、按类型与规范名去重。"""

    priority = {"product": 0, "character": 1, "scene": 2, "prop": 3}
    raw = references if isinstance(references, list) else []
    ordered = sorted(
        enumerate(raw),
        key=lambda pair: (
            priority.get(str(pair[1].get("type")), len(priority)) if isinstance(pair[1], dict) else len(priority),
            pair[0],
        ),
    )
    seen: set[tuple[str, str]] = set()
    result: list[ReferenceResource] = []
    for _index, item in ordered:
        if not isinstance(item, dict):
            continue
        asset_type = item.get("type")
        name = item.get("name")
        if not isinstance(asset_type, str) or asset_type not in priority or not isinstance(name, str):
            continue
        canonical_name = asset_name_comparison_key(name)
        key = (asset_type, canonical_name)
        if not canonical_name or key in seen:
            continue
        seen.add(key)
        result.append(
            ReferenceResource(
                type=cast(Literal["product", "character", "scene", "prop"], asset_type),
                name=canonical_name,
            )
        )
    return tuple(result)


def clamp_reference_assets(
    assets: Sequence[ResolvedReferenceAsset], max_references: int | None
) -> tuple[ResolvedReferenceAsset, ...]:
    """超过上限时优先保留产品 sheet，其次产品原图，最后其它资产。"""

    if max_references is None or len(assets) <= max_references:
        return tuple(assets)
    ordered = sorted(
        enumerate(assets),
        key=lambda pair: (
            0
            if pair[1].reference.type == "product" and pair[1].kind == "sheet"
            else 1
            if pair[1].reference.type == "product" and pair[1].kind == "original"
            else 2,
            pair[0],
        ),
    )
    return tuple(asset for _index, asset in ordered[: max(0, max_references)])


_PROBLEM_PRESENTATION: dict[str, tuple[str, tuple[tuple[str | int, ...], ...]]] = {
    "reference_declaration_invalid": ("repair_reference_declaration", (("references",),)),
    "reference_asset_missing": ("repair_reference_assets", (("references",),)),
    "reference_capability_changed": ("repair_reference_assets", (("references",),)),
    "reference_images_clamped": ("review_reference_selection", (("references",),)),
    "video_audio_switch_not_supported": (
        "enable_model_audio",
        (("generation_settings", "generate_audio"),),
    ),
    "reference_duration_confirmation_required": ("confirm_duration", (("duration_seconds",),)),
    "reference_supported_durations_missing": ("configure_video_model", (("duration_seconds",),)),
    "reference_supported_durations_invalid": ("configure_video_model", (("duration_seconds",),)),
    "reference_supported_durations_incompatible": ("configure_video_model", (("duration_seconds",),)),
    "reference_capability_unavailable": ("configure_video_model", (("references",),)),
    "video_capability_missing_i2v": ("configure_video_model", (("references",),)),
    "video_capability_missing_r2v": ("configure_video_model", (("references",),)),
}


def _problem(code: str, *, blocking: bool, **params: object) -> ProjectionProblem:
    return ProjectionProblem(code=code, blocking=blocking, params=tuple(params.items()))


def _asset_key(asset: ResolvedReferenceAsset) -> tuple[str, str]:
    return asset.reference.type, asset_name_comparison_key(asset.reference.name)


def _invalid_reference_declaration_count(references: object) -> int:
    """Count malformed declarations without repairing or rewriting the source unit."""

    if not isinstance(references, list):
        return 1
    valid_types = frozenset(ASSET_SPECS)
    invalid = 0
    for item in references:
        if not isinstance(item, dict):
            invalid += 1
            continue
        asset_type = item.get("type")
        name = item.get("name")
        if (
            not isinstance(asset_type, str)
            or asset_type not in valid_types
            or not isinstance(name, str)
            or not asset_name_comparison_key(name)
        ):
            invalid += 1
    return invalid


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
        raw_references = unit["references"] if "references" in unit else []
        canonical = canonicalize_references(raw_references)
        declared_capability: VideoCapability = "r2v" if canonical else "i2v"
        hydration = hydrate_reference_assets(canonical, resolved_assets, self._assets)
        available = hydration.available

        problems: list[ProjectionProblem] = []
        invalid_reference_count = _invalid_reference_declaration_count(raw_references)
        if invalid_reference_count:
            problems.append(
                _problem(
                    "reference_declaration_invalid",
                    blocking=True,
                    count=invalid_reference_count,
                )
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
        narration_floor = (
            options.narration_duration_floor
            if options.narration_delivery == USE_TTS and options.narration_duration_floor is not None
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
                if slot.needs_confirmation and not options.duration_confirmed:
                    problems.append(
                        _problem(
                            "reference_duration_confirmation_required",
                            blocking=True,
                            script_duration=planned_duration,
                            duration_input=duration_input,
                            request_duration=slot.seconds,
                            adjustment=slot.adjustment,
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
            duration_input=duration_input,
            request_duration=request_duration,
            cost=cost,
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
) -> ReferenceUnitRequestProjection:
    """生产入口：从当前项目文件与配置直接构造一次 advisory 投影。"""

    if resolver is None:
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        resolver = ConfigResolver(async_session_factory)
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
