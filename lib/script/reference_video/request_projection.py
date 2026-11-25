"""视频单元的当前请求投影。

投影是 advisory/current-state 读模型：调用方传入当前 project、script、unit，已经解析出的
资产候选与请求选项，得到报价、提交预检和限流路由共用的一份不可变事实。结果不携带 token、
fingerprint 或可执行请求快照；worker 开始处理时必须重新投影当前状态。

能力只经视频请求事实读取（``docs/adr/0086``）：投影按单元落入的桶取一份事实，读侧以当前配置
为身份求值，执行侧直接交出 lane 以实际 backend 身份求得的那一份。参考图的供应商素材关联同理：读侧以当前
配置装配的调用通道核对，执行侧以实际调用通道核对（见 :data:`ReferenceAssetLinkCheck`）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.config.resolver import (
    VideoGenerationType,
    video_capability_satisfied,
)
from lib.generation.video_request_facts import (
    DEFAULT_PLANNED_DURATION_SECONDS,
    VideoRequestCostFacts,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    audio_switch_conflict,
)
from lib.infra.schema_guards import is_int
from lib.references.reference_admission import (
    UNREGISTERED_REFERENCE_CODE,
    ReferenceAdmission,
    admit_references,
)
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script.reference_video.duration_slots import (
    DurationSlot,
    project_request_duration,
)
from lib.script.reference_video.reference_assets import (
    FilesystemReferenceAssets as FilesystemReferenceAssets,
)
from lib.script.reference_video.reference_assets import (
    MemoizedReferenceAssets as MemoizedReferenceAssets,
)
from lib.script.reference_video.reference_assets import (
    ReferenceAssetAvailability as ReferenceAssetAvailability,
)
from lib.script.reference_video.reference_assets import (
    ReferenceAssetHydration as ReferenceAssetHydration,
)
from lib.script.reference_video.reference_assets import (
    ReferenceExpansionCache as ReferenceExpansionCache,
)
from lib.script.reference_video.reference_assets import (
    clamp_reference_assets as clamp_reference_assets,
)
from lib.script.reference_video.reference_assets import (
    hydrate_reference_assets as hydrate_reference_assets,
)
from lib.script.reference_video.reference_assets import (
    resolve_reference_assets as resolve_reference_assets,
)
from lib.script.reference_video.reference_assets import (
    unit_reference_declarations as unit_reference_declarations,
)
from lib.script.reference_video.reference_assets import (
    unit_text as _unit_text,
)
from lib.script.reference_video.text_parser import derive_references_from_text
from lib.script.script_models import ReferenceResource, ResolvedReferenceAsset
from lib.video_runtime.resolution import read_video_request_facts

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver


@dataclass(frozen=True)
class ReferenceRequestOptions:
    """影响当前 unit 请求投影、但不属于剧本内容的调用选项。

    队列只保存用户明确接受的时长档位；worker 再以最新剧本与模型能力重投影，档位变化后旧确认
    不会继续放行。旧版本入队的选项里可能还带着 ``narration_delivery``，读取时忽略。
    """

    confirmed_request_duration_seconds: int | None = None
    _legacy_duration_confirmed: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        confirmed = self.confirmed_request_duration_seconds
        if confirmed is not None and not is_int(confirmed, minimum=1):
            raise ValueError("confirmed_request_duration_seconds must be a positive integer or null")

    @property
    def legacy_duration_confirmed(self) -> bool:
        """Whether an option-less task predates explicit tier coordinates."""

        return self._legacy_duration_confirmed

    def to_payload(self) -> dict[str, object]:
        if self.confirmed_request_duration_seconds is None:
            return {}
        return {"confirmed_request_duration_seconds": self.confirmed_request_duration_seconds}

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
        confirmed = raw.get("confirmed_request_duration_seconds")
        return cls(confirmed_request_duration_seconds=confirmed if is_int(confirmed, minimum=1) else None)


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

    @classmethod
    def from_request_facts_failure(
        cls,
        failure: VideoRequestFactsFailure,
        *,
        capability: VideoGenerationType | None = None,
        locations: tuple[tuple[str | int, ...], ...] | None = None,
    ) -> ProjectionProblem:
        """把视频请求事实的失败折成阻断问题：问题码、参数与修复指引原样保留，只补上所落的桶。"""

        params = {**({"capability": capability} if capability is not None else {}), **failure.parameters()}
        return cls(
            code=failure.code,
            blocking=True,
            params=tuple(params.items()),
            action=failure.action,
            locations=locations,
        )

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
    declared_generation_type: VideoGenerationType
    hydrated_generation_type: VideoGenerationType
    request_facts: VideoRequestFacts | None
    planned_duration: int
    duration_input: int
    request_duration: DurationSlot | None
    cost: VideoRequestCostFacts | None
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
            "duration_input": self.duration_input,
            "request_duration": self.request_duration.seconds if self.request_duration is not None else None,
            "problems": self.problem_payloads(),
        }
        return payload


VideoRequestFactsResult = VideoRequestFacts | VideoRequestFactsFailure

ReferenceRequestFactsLookup = Callable[[VideoGenerationType], Awaitable[VideoRequestFactsResult]]


ReferenceAssetLinkCheck = Callable[
    [VideoRequestFacts, Sequence[ResolvedReferenceAsset]], Awaitable["ProjectionProblem | None"]
]


def configured_reference_request_facts(
    project: dict,
    resolver: ConfigResolver,
    *,
    initial_facts: Mapping[VideoGenerationType, VideoRequestFactsResult] | None = None,
) -> ReferenceRequestFactsLookup:
    """读侧：以当前配置为身份，对参考路线逐桶求值视频请求事实；同一查找内每个桶只求值一次。"""

    evaluated: dict[VideoGenerationType, VideoRequestFactsResult] = dict(initial_facts) if initial_facts else {}

    async def lookup(generation_type: VideoGenerationType) -> VideoRequestFactsResult:
        result = evaluated.get(generation_type)
        if result is None:
            result = await read_video_request_facts(
                project,
                route="reference_video",
                generation_type=generation_type,
                resolver=resolver,
            )
            evaluated[generation_type] = result
        return result

    return lookup


@dataclass(frozen=True)
class ReferenceUnitHydration:
    """一个单元的声明引用经水合后的定桶结论——执行侧与读侧共用的判据。

    ``declared_generation_type`` 只看正文里已登记的声明引用；``hydrated_generation_type`` 看此刻
    确实能随请求发出的可用参考图，单元实际落哪个桶以它为准。声明与可用参考图分裂（引用未登记、
    已登记但缺图、桶因此改变）时 ``problems`` 带阻断问题并点名不可用的引用，不静默换桶。
    """

    declared_references: tuple[ReferenceResource, ...]
    available_assets: tuple[ResolvedReferenceAsset, ...]
    unavailable_references: tuple[ReferenceResource, ...]
    unregistered_references: tuple[str, ...]
    declared_generation_type: VideoGenerationType
    hydrated_generation_type: VideoGenerationType
    problems: tuple[ProjectionProblem, ...]
    unreadable_files: tuple[str, ...] = ()


def hydrate_unit_references(
    project: dict,
    unit: dict,
    resolved_assets: Sequence[ResolvedReferenceAsset],
    availability: ReferenceAssetAvailability,
    *,
    catalog: ReferenceCatalog | None = None,
) -> ReferenceUnitHydration:
    """把单元正文的声明引用水合成可用参考图，并据此定桶。

    ``catalog`` 是调用方已从同一份 ``project`` 构造的引用目录，批量水合时传入以免逐单元重建。
    """

    if catalog is None:
        catalog = build_reference_catalog(project)
    references, unregistered = derive_references_from_text(_unit_text(unit), project, catalog=catalog)
    canonical = tuple(references)
    declared_generation_type: VideoGenerationType = "r2v" if canonical else "i2v"
    hydration = hydrate_reference_assets(canonical, resolved_assets, availability)
    available = hydration.available

    problems: list[ProjectionProblem] = []
    admission = _admit_unit_references(catalog, references, unregistered)
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

    hydrated_generation_type: VideoGenerationType = "r2v" if available or hydration.unreadable_files else "i2v"
    if hydrated_generation_type != declared_generation_type:
        problems.append(
            _problem(
                "reference_capability_changed",
                blocking=True,
                declared=declared_generation_type,
                hydrated=hydrated_generation_type,
            )
        )
    return ReferenceUnitHydration(
        declared_references=canonical,
        available_assets=available,
        unavailable_references=hydration.missing,
        unregistered_references=admission.unregistered,
        declared_generation_type=declared_generation_type,
        hydrated_generation_type=hydrated_generation_type,
        problems=tuple(problems),
        unreadable_files=hydration.unreadable_files,
    )


def unit_reference_admission(project: dict, unit: dict) -> ReferenceAdmission:
    """本单元正文的引用准入结论——与分镜路线共用 :mod:`lib.references.reference_admission` 的判定。

    单条生成与整批准入都经本投影器，两者因此不会对同一份正文给出相反的答复。
    """

    catalog = build_reference_catalog(project)
    references, unregistered = derive_references_from_text(_unit_text(unit), project, catalog=catalog)
    return _admit_unit_references(catalog, references, unregistered)


def _admit_unit_references(
    catalog: ReferenceCatalog, references: Sequence[ReferenceResource], unregistered: Sequence[str]
) -> ReferenceAdmission:
    return admit_references(
        catalog,
        references=[(reference.type, reference.name) for reference in references],
        unregistered=unregistered,
    )


_PROBLEM_PRESENTATION: dict[str, tuple[str, tuple[tuple[str | int, ...], ...]]] = {
    "video_reference_images_unreadable": ("repair_reference_assets", (("text",),)),
    "reference_asset_missing": ("repair_reference_assets", (("text",),)),
    UNREGISTERED_REFERENCE_CODE: ("repair_reference_assets", (("text",),)),
    "reference_capability_changed": ("repair_reference_assets", (("text",),)),
    "video_reference_images_exceeded": ("review_reference_selection", (("text",),)),
    "provider_asset_link_processing": ("review_reference_selection", (("text",),)),
    "provider_asset_link_failed": ("review_reference_selection", (("text",),)),
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
    "video_capability_reference_unavailable": ("configure_video_model", (("text",),)),
    "model_not_in_provider_catalog": ("configure_video_model", (("text",),)),
    "model_not_selected": ("configure_video_model", (("text",),)),
    "model_discontinued": ("configure_video_model", (("text",),)),
}


def _problem(code: str, *, blocking: bool, **params: object) -> ProjectionProblem:
    return ProjectionProblem(code=code, blocking=blocking, params=tuple(params.items()))


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
        asset_links: ReferenceAssetLinkCheck | None = None,
    ) -> None:
        """``asset_links`` 为 None 时不核对供应商素材关联，只用于只要定桶结论的调用方（如限流路由）。"""
        self._request_facts = request_facts
        self._assets = assets
        self._asset_links = asset_links

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
        hydration = await asyncio.to_thread(hydrate_unit_references, project, unit, resolved_assets, self._assets)
        return await self.project_hydrated(project=project, unit=unit, hydration=hydration, options=options)

    async def project_hydrated(
        self,
        *,
        project: dict,
        unit: dict,
        hydration: ReferenceUnitHydration,
        options: ReferenceRequestOptions | None = None,
    ) -> ReferenceUnitRequestProjection:
        """在调用方已完成的水合结论上投影；批量调用方可整批水合，只进一次工作线程。"""

        options = options or ReferenceRequestOptions()
        canonical = hydration.declared_references
        available = hydration.available_assets
        declared_generation_type = hydration.declared_generation_type
        hydrated_generation_type = hydration.hydrated_generation_type

        problems: list[ProjectionProblem] = list(hydration.problems)

        facts: VideoRequestFacts | None = None
        try:
            evaluated = await self._request_facts(hydrated_generation_type)
        except Exception:
            evaluated = VideoRequestFactsFailure(
                "reference_capability_unavailable", (("capability", hydrated_generation_type),)
            )
        if isinstance(evaluated, VideoRequestFactsFailure):
            problems.append(
                ProjectionProblem.from_request_facts_failure(evaluated, capability=hydrated_generation_type)
            )
        else:
            facts = evaluated

        if hydration.unreadable_files:
            problems.append(
                _problem(
                    "video_reference_images_unreadable",
                    blocking=True,
                    model=facts.model_id if facts else "?",
                    names=", ".join(hydration.unreadable_files),
                )
            )
        request_assets = available
        if facts is not None:
            if (
                hydrated_generation_type == "i2v"
                and not available
                and not video_capability_satisfied(
                    generation_type=hydrated_generation_type,
                    first_frame=facts.first_frame,
                    max_reference_images=facts.max_reference_images,
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
            limit = facts.max_reference_images
            if limit is not None and len(available) > limit:
                problems.append(
                    _problem(
                        "video_reference_images_exceeded",
                        blocking=True,
                        count=len(available),
                        limit=limit,
                        names=", ".join(dict.fromkeys(asset.reference.name for asset in available[limit:])),
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
            if self._asset_links is not None and request_assets:
                link_problem = await self._asset_links(facts, request_assets)
                if link_problem is not None:
                    problems.append(link_problem)

        planned_duration = _planned_duration(unit)
        duration_input = planned_duration
        request_duration: DurationSlot | None = None
        cost: VideoRequestCostFacts | None = None

        if facts is not None:
            projected = project_request_duration(
                planned_duration_seconds=planned_duration,
                supported_durations=facts.allowed_durations,
                duration_endpoint_fixed=facts.duration_endpoint_fixed,
                confirmed_request_duration_seconds=options.confirmed_request_duration_seconds,
                confirmation_waived=options.legacy_duration_confirmed,
            )
            duration_input = projected.duration_input
            request_duration = projected.slot
            if projected.problem == "supported_durations_missing":
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
                    )
                )
            if projected.slot is not None:
                cost = VideoRequestCostFacts(
                    request_facts=facts,
                    duration_seconds=projected.slot.seconds,
                    aspect_ratio=resolve_video_aspect_ratio(project),
                    reference_image_count=len(request_assets),
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
    resolver: ConfigResolver | None = None,
    request_facts_lookup: ReferenceRequestFactsLookup | None = None,
    shared_assets: Callable[[], ReferenceAssetAvailability] | None = None,
    asset_links: ReferenceAssetLinkCheck | None = None,
    check_asset_links: bool = True,
) -> ReferenceUnitRequestProjection:
    """生产入口：从当前项目文件与配置直接构造一次 advisory 投影。

    资产可用性与执行侧同判据（文件存在且产物清单认领），准入、单条入队、提示词预览与限流
    路由因此不会把执行时落 i2v 的单元判成 r2v。批量调用方传入 ``shared_assets``，在工作线程里
    取整批共用的判定，一批单元在同一份清单快照上判定；不传时按本单元现场构造。

    供应商素材关联默认以当前配置装配的调用通道核对；批量调用方传入整批共用的 ``asset_links``。只要定桶结论的
    调用方（限流路由）传 ``check_asset_links=False`` 跳过核对。
    """

    from lib.script.reference_video.artifact_selection import CurrentReferenceAssets

    if resolver is None:
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        resolver = ConfigResolver(async_session_factory)
    if request_facts_lookup is None:
        request_facts_lookup = configured_reference_request_facts(project, resolver)
    if not check_asset_links:
        asset_links = None
    elif asset_links is None:
        from lib.script.reference_video.provider_asset_links import configured_asset_link_check

        asset_links = configured_asset_link_check(project_path=project_path, resolver=resolver)

    def current_assets() -> tuple[ReferenceAssetAvailability, tuple[ResolvedReferenceAsset, ...]]:
        availability = CurrentReferenceAssets(project_path, project) if shared_assets is None else shared_assets()
        return availability, resolve_reference_assets(project, project_path, unit)

    availability, resolved_assets = await asyncio.to_thread(current_assets)
    projector = ReferenceUnitRequestProjector(request_facts_lookup, availability, asset_links)
    return await projector.project_current(
        project=project,
        script=script,
        unit=unit,
        resolved_assets=resolved_assets,
        options=options or ReferenceRequestOptions(),
    )
