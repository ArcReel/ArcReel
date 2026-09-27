"""参考生视频单元的读侧能力：按可用参考图逐单元定桶，并给出所落桶的视频请求事实。

单元列表、内容确认面板与 Agent 能力载荷都读本模块的结果；定桶判据与执行侧
``ReferenceUnitRequestProjector`` 同源（:func:`hydrate_unit_references` + 产物清单感知的
资产可用性），界面因此不会按 r2v 取档而执行落 i2v。报价经 ``ReferenceUnitRequestProjector``
走同一次水合；同档免费复用的视觉依据摘要、规划期时长闸门与新建单元的默认时长只需要定桶结论时读
:func:`hydrate_reference_units`，同一份判据不在消费方各自重写。桶级视频请求事实由调用方给出的
按桶查找提供，同一次请求内每个桶至多求值一次，单元数不放大配置解析次数。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.config.resolver import VideoGenerationType, duration_endpoint_fixed_reason
from lib.generation.video_request_facts import VideoRequestFacts, VideoRequestFactsFailure
from lib.script.reference_video.artifact_selection import CurrentReferenceAssets
from lib.script.reference_video.request_projection import (
    ReferenceAssetAvailability,
    ReferenceRequestFactsLookup,
    ReferenceUnitHydration,
    hydrate_unit_references,
    resolve_reference_assets,
)


@dataclass(frozen=True)
class ReferenceUnitCapability:
    """一个单元此刻的定桶结论与所落桶的视频请求事实。"""

    unit_id: str
    hydration: ReferenceUnitHydration
    request_facts: VideoRequestFacts | VideoRequestFactsFailure

    @property
    def generation_type(self) -> VideoGenerationType:
        return self.hydration.hydrated_generation_type

    def to_payload(self) -> dict[str, object]:
        """Web 与 Agent 共用的逐单元信封。

        ``problem`` 是所落桶的视频请求事实失败；``problems`` 是声明引用与可用参考图分裂的
        结构化阻断问题（未登记 / 缺图 / 桶改变），两者互不折叠。
        """

        facts = self.request_facts
        resolved, failure = (facts, None) if isinstance(facts, VideoRequestFacts) else (None, facts)
        fixed = resolved is not None and resolved.duration_endpoint_fixed
        hydration = self.hydration
        return {
            "unit_id": self.unit_id,
            "declared_capability": hydration.declared_generation_type,
            "hydrated_capability": hydration.hydrated_generation_type,
            "declared_references": [
                {"type": reference.type, "name": reference.name} for reference in hydration.declared_references
            ],
            "unavailable_references": [
                {"type": reference.type, "name": reference.name} for reference in hydration.unavailable_references
            ],
            "unregistered_references": list(hydration.unregistered_references),
            "allowed_durations": list(resolved.allowed_durations) if resolved is not None else None,
            "excluded_durations": dict(resolved.excluded_durations) if resolved is not None else None,
            "duration_endpoint_fixed": fixed,
            "duration_endpoint_fixed_reason": duration_endpoint_fixed_reason(fixed),
            "problem": failure.problem_payload() if failure is not None else None,
            "problems": [problem.to_payload(unit_id=self.unit_id) for problem in hydration.problems],
        }


def hydrate_reference_units(
    project: dict,
    project_path: Path,
    units: Iterable[dict],
    *,
    availability: ReferenceAssetAvailability | None = None,
) -> tuple[ReferenceUnitHydration, ...]:
    """按执行侧同款判据逐单元水合声明引用，给出各单元此刻所落的桶与分裂问题。

    ``availability`` 缺省为产物清单感知判定（文件存在且清单认领），整批只构造一次；没有单元时
    不触碰项目资产。
    """

    pending = list(units)
    if not pending:
        return ()
    assets = availability if availability is not None else CurrentReferenceAssets(project_path, project)
    return tuple(
        hydrate_unit_references(project, unit, resolve_reference_assets(project, project_path, unit), assets)
        for unit in pending
    )


async def evaluate_reference_unit_capabilities(
    project: dict,
    project_path: Path,
    units: Iterable[dict],
    *,
    request_facts: ReferenceRequestFactsLookup,
    availability: ReferenceAssetAvailability | None = None,
) -> tuple[ReferenceUnitCapability, ...]:
    """逐单元定桶并取所落桶的视频请求事实。

    ``availability`` 缺省为执行侧同款的产物清单感知判定；``request_facts`` 按桶查找由调用方
    给出（读侧用 ``configured_reference_request_facts``）。
    """

    pending = list(units)
    results: list[ReferenceUnitCapability] = []
    for unit, hydration in zip(
        pending, hydrate_reference_units(project, project_path, pending, availability=availability), strict=True
    ):
        results.append(
            ReferenceUnitCapability(
                unit_id=str(unit.get("unit_id") or ""),
                hydration=hydration,
                request_facts=await request_facts(hydration.hydrated_generation_type),
            )
        )
    return tuple(results)


def reference_unit_capability_payloads(capabilities: Sequence[ReferenceUnitCapability]) -> dict[str, dict[str, object]]:
    """按 ``unit_id`` 索引的逐单元信封，供响应体直接挂载。"""

    return {capability.unit_id: capability.to_payload() for capability in capabilities}
