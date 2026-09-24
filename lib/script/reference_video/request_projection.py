"""视频单元的当前请求投影。

投影是 advisory/current-state 读模型：调用方传入当前 project、script、unit，已经解析出的
资产候选与请求选项，得到报价、提交预检和限流路由共用的一份不可变事实。结果不携带 token、
fingerprint 或可执行请求快照；worker 开始处理时必须重新投影当前状态。

能力只经视频请求事实读取（``docs/adr/0086``）：投影按单元落入的桶取一份事实，读侧以当前配置
为身份求值，执行侧直接交出 lane 以实际 backend 身份求得的那一份。
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from lib.config.resolver import (
    VideoGenerationType,
    video_capability_satisfied,
)
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    audio_switch_conflict,
    evaluate_video_request_facts,
)
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.schema_guards import is_int
from lib.project.asset_types import AssetSpec, asset_name_comparison_key
from lib.references.reference_admission import (
    SHEET_REQUIRED_ASSET_TYPES,
    UNREGISTERED_REFERENCE_CODE,
    ReferenceAdmission,
    admit_references,
)
from lib.references.reference_catalog import build_reference_catalog
from lib.script.reference_video.duration_slots import (
    DEFAULT_PLANNED_DURATION_SECONDS,
    DurationSlot,
    project_request_duration,
    request_duration_input,
)
from lib.script.reference_video.text_parser import derive_references_from_text
from lib.script.script_models import ReferenceResource
from lib.speech.narration_delivery import (
    POST_PRODUCTION as POST_PRODUCTION,
)
from lib.speech.narration_delivery import (
    USE_TTS,
    NarrationDeliveryPreparation,
    NarrationDeliveryRequestOptions,
    TtsSettingsResolver,
    VideoRequestCostFacts,
    prepare_current_narration_delivery,
)
from lib.speech.narration_delivery import (
    NarrationDelivery as NarrationDelivery,
)
from lib.speech.speech_composition import admit_script_unit

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver


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
    #: 两者的对外载荷键固定为 ``declared_capability`` / ``hydrated_capability``（API 契约）。
    declared_generation_type: VideoGenerationType
    hydrated_generation_type: VideoGenerationType
    #: 单元所落桶的视频请求事实；求值失败时为 None，失败已折成 ``problems`` 里的阻断项。
    request_facts: VideoRequestFacts | None
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
        return self.request_facts.provider_id if self.request_facts is not None else None

    @property
    def model_id(self) -> str | None:
        return self.request_facts.model_id if self.request_facts is not None else None

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
            "declared_capability": self.declared_generation_type,
            "hydrated_capability": self.hydrated_generation_type,
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


VideoRequestFactsResult = VideoRequestFacts | VideoRequestFactsFailure

#: 按任务类型桶取视频请求事实。读侧用 :func:`configured_reference_request_facts`，执行侧交出
#: lane 已求得的那一份；测试直接返回构造好的结果对象或失败对象。
ReferenceRequestFactsLookup = Callable[[VideoGenerationType], Awaitable[VideoRequestFactsResult]]


def configured_reference_request_facts(project: dict, resolver: ConfigResolver) -> ReferenceRequestFactsLookup:
    """读侧：以当前配置为身份，对参考路线逐桶求值视频请求事实；同一查找内每个桶只求值一次。"""

    evaluated: dict[VideoGenerationType, VideoRequestFactsResult] = {}

    async def lookup(generation_type: VideoGenerationType) -> VideoRequestFactsResult:
        result = evaluated.get(generation_type)
        if result is None:
            result = await evaluate_video_request_facts(
                project,
                route="reference_video",
                generation_type=generation_type,
                identity=CONFIGURED_VIDEO_IDENTITY,
                resolver=resolver,
            )
            evaluated[generation_type] = result
        return result

    return lookup


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


def unit_reference_admission(project: dict, unit: dict) -> ReferenceAdmission:
    """本单元正文的引用准入结论——与分镜路线共用 :mod:`lib.references.reference_admission` 的判定。

    单条生成与整批准入都经本投影器，两者因此不会对同一份正文给出相反的答复。
    """

    raw_text = unit.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    references, unregistered = derive_references_from_text(text, project)
    return admit_references(
        build_reference_catalog(project),
        references=[(reference.type, reference.name) for reference in references],
        unregistered=unregistered,
    )


def resolve_reference_assets(project: dict, project_path: Path, unit: dict) -> tuple[ResolvedReferenceAsset, ...]:
    """把正文派生的逻辑引用展开为图片候选，不把「路径已登记」误当成「文件存在」。

    有资产图就用资产图。没有资产图时只有商品退到它的全部原图——原图是商品的保真验收锚点
    （见 ADR 0034）；角色、场景、道具的原图只是生成资产图的输入，不进视频请求（见 ADR 0073），
    没有资产图就不产生候选、由 projector 报成阻断。有资产图时任何类型都不额外注入原图，
    也不按类型排序（见 ADR 0064）。缺字段、未登记或越界路径不制造候选，由 projector 对照
    派生引用统一产出 ``reference_asset_missing``。
    """

    catalog = build_reference_catalog(project)
    result: list[ResolvedReferenceAsset] = []
    for reference in unit_reference_declarations(project, unit):
        catalog_entry = catalog.lookup(reference.type, reference.name)
        if catalog_entry is None:
            continue
        entry = catalog_entry.asset
        if not isinstance(entry, dict):
            continue
        spec = catalog_entry.spec
        sheet = _candidate_path(project_path, entry.get(spec.sheet_field))
        if sheet is not None:
            result.append(ResolvedReferenceAsset(path=sheet, reference=reference, kind="sheet"))
            continue
        if catalog_entry.asset_type in SHEET_REQUIRED_ASSET_TYPES:
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


def clamp_reference_assets(
    assets: Sequence[ResolvedReferenceAsset], max_references: int | None
) -> tuple[ResolvedReferenceAsset, ...]:
    """超过上限时按正文的提及顺序保留前若干张——没有类型优先级。"""

    if max_references is None or len(assets) <= max_references:
        return tuple(assets)
    return tuple(assets[: max(0, max_references)])


_PROBLEM_PRESENTATION: dict[str, tuple[str, tuple[tuple[str | int, ...], ...]]] = {
    "reference_asset_missing": ("repair_reference_assets", (("text",),)),
    UNREGISTERED_REFERENCE_CODE: ("repair_reference_assets", (("text",),)),
    "reference_capability_changed": ("repair_reference_assets", (("text",),)),
    "reference_images_clamped": ("review_reference_selection", (("text",),)),
    "video_audio_switch_not_supported": (
        "enable_model_audio",
        (("generation_settings", "generate_audio"),),
    ),
    "reference_duration_confirmation_required": ("confirm_duration", (("duration_seconds",),)),
    "tts_duration_endpoint_fixed": ("choose_post_production", (("narration_delivery",),)),
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
    raw = unit.get("duration_seconds", DEFAULT_PLANNED_DURATION_SECONDS)
    if isinstance(raw, bool):
        raise ValueError("duration_seconds must be a positive integer")
    value = int(raw or DEFAULT_PLANNED_DURATION_SECONDS)
    if value <= 0:
        raise ValueError("duration_seconds must be a positive integer")
    return value


class ReferenceUnitRequestProjector:
    """把当前 unit 意图投影成所有读侧共用的规范请求事实。"""

    def __init__(
        self,
        request_facts: ReferenceRequestFactsLookup,
        assets: ReferenceAssetAvailability,
    ) -> None:
        self._request_facts = request_facts
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
        declared_generation_type: VideoGenerationType = "r2v" if canonical else "i2v"
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
        # 未登记引用不产生派生引用，因而不会出现在 ``hydration.missing`` 里，须在此单独阻断并
        # 列名。无资产图的角色 / 场景 / 道具不需要在此另报——它们不退回原图，展开时就不产生
        # 候选，一律落进 ``hydration.missing``。
        admission = unit_reference_admission(project, unit)
        if admission.unregistered:
            problems.append(
                _problem(
                    UNREGISTERED_REFERENCE_CODE,
                    blocking=True,
                    unregistered=admission.unregistered,
                    missing_text=admission.unregistered_text(),
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

        hydrated_generation_type: VideoGenerationType = "r2v" if available else "i2v"
        if hydrated_generation_type != declared_generation_type:
            problems.append(
                _problem(
                    "reference_capability_changed",
                    blocking=True,
                    declared=declared_generation_type,
                    hydrated=hydrated_generation_type,
                )
            )

        facts: VideoRequestFacts | None = None
        try:
            evaluated = await self._request_facts(hydrated_generation_type)
        except Exception:
            evaluated = VideoRequestFactsFailure(
                "reference_capability_unavailable", (("capability", hydrated_generation_type),)
            )
        if isinstance(evaluated, VideoRequestFactsFailure):
            problems.append(
                _problem(
                    evaluated.code,
                    blocking=True,
                    **{"capability": hydrated_generation_type, **evaluated.parameters()},
                )
            )
        else:
            facts = evaluated

        request_assets = available
        if facts is not None:
            if (
                hydrated_generation_type == "i2v"
                and not available
                and not video_capability_satisfied(
                    generation_type=hydrated_generation_type,
                    first_frame=facts.first_frame,
                    max_reference_images=facts.max_reference_images or 0,
                    text_to_video=facts.text_to_video,
                    has_image=False,
                )
            ):
                problems.append(
                    _problem(
                        "video_capability_missing_t2v",
                        blocking=True,
                        provider=facts.provider_id,
                        model=facts.model_id,
                    )
                )
            request_assets = clamp_reference_assets(available, facts.max_reference_images)
            if len(request_assets) < len(available):
                problems.append(
                    _problem(
                        "reference_images_clamped",
                        blocking=False,
                        count=len(available),
                        max_count=facts.max_reference_images,
                        provider=facts.provider_id,
                        model=facts.model_id,
                    )
                )
            if audio_switch_conflict(facts) is not None:
                problems.append(
                    _problem(
                        "video_audio_switch_not_supported",
                        blocking=True,
                        provider=facts.provider_id,
                        model=facts.model_id,
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
        duration_input: int | float = request_duration_input(planned_duration, narration_floor)
        request_duration: DurationSlot | None = None
        cost: ProjectionCostFacts | None = None

        if facts is not None:
            # 取档判定与分镜路线同一份实现；这里只把它的结论映射成参考生视频的 problem code。
            projected = project_request_duration(
                planned_duration_seconds=planned_duration,
                supported_durations=facts.allowed_durations,
                narration_duration_floor=narration_floor,
                duration_endpoint_fixed=facts.duration_endpoint_fixed,
                uses_tts=options.narration_delivery == USE_TTS,
                current_visual_duration_seconds=options.current_visual_duration_seconds,
                confirmed_request_duration_seconds=options.confirmed_request_duration_seconds,
                confirmation_waived=options.legacy_duration_confirmed,
            )
            duration_input = projected.duration_input
            request_duration = projected.slot
            if projected.problem == "tts_duration_endpoint_fixed":
                # 排在旁白交付带过来的问题之前：读侧取首条阻断项作为指引，而「配好 TTS / 等它
                # 生成完」在这种模型上做完也仍然不能用 use_tts，唯一出路是改选后期配音。
                problems.insert(
                    0,
                    _problem(
                        "tts_duration_endpoint_fixed",
                        blocking=True,
                        provider=facts.provider_id,
                        model=facts.model_id,
                    ),
                )
            elif projected.problem == "supported_durations_missing":
                problems.append(
                    _problem(
                        "reference_supported_durations_missing",
                        blocking=True,
                        provider=facts.provider_id,
                        model=facts.model_id,
                    )
                )
            elif projected.problem == "needs_replan" and projected.slot is not None:
                problems.append(
                    _problem(
                        "needs_replan",
                        blocking=True,
                        duration_input=projected.duration_input,
                        maximum_duration=projected.slot.seconds,
                    )
                )
            elif projected.problem == "confirmation_required" and projected.slot is not None:
                problems.append(
                    _problem(
                        "reference_duration_confirmation_required",
                        blocking=True,
                        script_duration=planned_duration,
                        duration_input=projected.duration_input,
                        request_duration=projected.slot.seconds,
                        adjustment=projected.slot.adjustment,
                        current_visual_duration=options.current_visual_duration_seconds,
                    )
                )
            if projected.slot is not None:
                cost = ProjectionCostFacts(
                    request_facts=facts,
                    duration_seconds=projected.slot.seconds,
                )

        return ReferenceUnitRequestProjection(
            unit_id=str(unit.get("unit_id") or ""),
            declared_references=canonical,
            available_assets=available,
            request_assets=request_assets,
            declared_generation_type=declared_generation_type,
            hydrated_generation_type=hydrated_generation_type,
            request_facts=facts,
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
    resolver: ConfigResolver | None = None,
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
        configured_reference_request_facts(project, resolver),
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
