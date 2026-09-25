"""逐单元定桶：按可用参考图落桶、取所落桶事实、点名分裂的引用。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.reference_video import request_projection
from lib.script.reference_video.artifact_selection import CurrentReferenceAssets
from lib.script.reference_video.request_projection import (
    ReferenceUnitRequestProjector,
    ResolvedReferenceAsset,
    configured_reference_request_facts,
    resolve_reference_assets,
)
from lib.script.reference_video.unit_capabilities import (
    evaluate_reference_unit_capabilities,
    reference_unit_capability_payloads,
)
from tests.factories import make_video_request_facts

I2V_FACTS = make_video_request_facts(
    route="reference_video",
    generation_type="i2v",
    provider_id="image-provider",
    model_id="image-model",
    supported_durations=(4, 6, 8),
    allowed_durations=(4, 6),
    excluded_durations=(("8", "resolution"),),
)
R2V_FACTS = make_video_request_facts(
    route="reference_video",
    generation_type="r2v",
    provider_id="reference-provider",
    model_id="reference-model",
    supported_durations=(8, 16),
    allowed_durations=(8, 16),
)


class _CountingFacts:
    def __init__(self, *, failures: dict[str, VideoRequestFactsFailure] | None = None) -> None:
        self.calls: list[str] = []
        self._failures = failures or {}

    async def __call__(self, generation_type: str):
        self.calls.append(generation_type)
        if generation_type in self._failures:
            return self._failures[generation_type]
        return R2V_FACTS if generation_type == "r2v" else I2V_FACTS


class _AvailableAssets:
    def __init__(self, missing: Iterable[Path] = ()) -> None:
        self._missing = set(missing)

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        return asset.path not in self._missing


def _problems(payload: dict[str, object]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], payload["problems"])


def _project(tmp_path: Path, *, sheet_present: bool) -> dict:
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "generation_mode": "reference_video",
        "characters": {"阿离": {"description": "少女", "character_sheet": "characters/阿离.png"}},
        "scenes": {},
        "props": {},
        "products": {},
    }
    if sheet_present:
        (tmp_path / "characters").mkdir(exist_ok=True)
        (tmp_path / "characters" / "阿离.png").write_bytes(b"png")
    (tmp_path / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return project


@pytest.mark.asyncio
async def test_registered_reference_without_image_lands_in_i2v_with_its_tiers(tmp_path: Path) -> None:
    """已登记但缺图的引用：单元落 i2v、档位取 i2v 事实，并点名不可用引用与桶改变。"""
    project = _project(tmp_path, sheet_present=False)
    unit = {"unit_id": "E1U1", "text": "@[阿离] 抬头。", "duration_seconds": 8}

    (capability,) = await evaluate_reference_unit_capabilities(
        project,
        tmp_path,
        [unit],
        request_facts=_CountingFacts(),
        availability=_AvailableAssets({tmp_path / "characters" / "阿离.png"}),
    )

    payload = capability.to_payload()
    assert (payload["declared_capability"], payload["hydrated_capability"]) == ("r2v", "i2v")
    assert payload["allowed_durations"] == [4, 6]
    assert payload["excluded_durations"] == {"8": "resolution"}
    assert payload["declared_references"] == [{"type": "character", "name": "阿离"}]
    assert payload["unavailable_references"] == [{"type": "character", "name": "阿离"}]
    assert payload["unregistered_references"] == []
    assert payload["problem"] is None
    problems = _problems(payload)
    assert [(problem["code"], problem["blocking"]) for problem in problems] == [
        ("reference_asset_missing", True),
        ("reference_capability_changed", True),
    ]
    assert problems[0]["params"] == {"missing": (("character", "阿离"),), "missing_text": "character: 阿离"}
    assert problems[1]["params"] == {"declared": "r2v", "hydrated": "i2v"}
    assert problems[0]["action"] == "repair_reference_assets"


@pytest.mark.asyncio
async def test_available_reference_lands_in_r2v_without_split(tmp_path: Path) -> None:
    project = _project(tmp_path, sheet_present=True)
    unit = {"unit_id": "E1U1", "text": "@[阿离] 抬头。", "duration_seconds": 8}

    (capability,) = await evaluate_reference_unit_capabilities(
        project, tmp_path, [unit], request_facts=_CountingFacts(), availability=_AvailableAssets()
    )

    payload = capability.to_payload()
    assert (payload["declared_capability"], payload["hydrated_capability"]) == ("r2v", "r2v")
    assert payload["allowed_durations"] == [8, 16]
    assert payload["unavailable_references"] == []
    assert payload["problems"] == []


@pytest.mark.asyncio
async def test_unregistered_mention_is_named_and_blocks(tmp_path: Path) -> None:
    project = _project(tmp_path, sheet_present=True)
    unit = {"unit_id": "E1U1", "text": "@[路人] 走过。", "duration_seconds": 8}

    (capability,) = await evaluate_reference_unit_capabilities(
        project, tmp_path, [unit], request_facts=_CountingFacts(), availability=_AvailableAssets()
    )

    payload = capability.to_payload()
    assert payload["hydrated_capability"] == "i2v"
    assert payload["unregistered_references"] == ["路人"]
    assert [problem["code"] for problem in _problems(payload)] == ["reference_asset_unregistered"]


@pytest.mark.asyncio
async def test_bucket_facts_failure_is_reported_per_unit(tmp_path: Path) -> None:
    project = _project(tmp_path, sheet_present=True)
    unit = {"unit_id": "E1U1", "text": "空镜：晨光。", "duration_seconds": 8}
    failure = VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),))

    (capability,) = await evaluate_reference_unit_capabilities(
        project,
        tmp_path,
        [unit],
        request_facts=_CountingFacts(failures={"i2v": failure}),
        availability=_AvailableAssets(),
    )

    payload = capability.to_payload()
    assert payload["hydrated_capability"] == "i2v"
    assert payload["allowed_durations"] is None
    assert payload["excluded_durations"] is None
    assert payload["duration_endpoint_fixed"] is False
    assert payload["problem"] == {
        "code": "reference_capability_unavailable",
        "params": {"capability": "i2v"},
        "action": "configure_video_model",
    }
    assert payload["problems"] == []


@pytest.mark.asyncio
async def test_endpoint_fixed_bucket_reports_flag_and_reason(tmp_path: Path) -> None:
    project = _project(tmp_path, sheet_present=True)
    unit = {"unit_id": "E1U1", "text": "空镜：晨光。", "duration_seconds": 8}
    fixed = make_video_request_facts(
        route="reference_video",
        generation_type="i2v",
        supported_durations=(),
        allowed_durations=(),
        duration_endpoint_fixed=True,
    )

    async def lookup(_generation_type: str):
        return fixed

    (capability,) = await evaluate_reference_unit_capabilities(
        project, tmp_path, [unit], request_facts=lookup, availability=_AvailableAssets()
    )

    payload = capability.to_payload()
    assert payload["allowed_durations"] == []
    assert payload["duration_endpoint_fixed"] is True
    assert payload["duration_endpoint_fixed_reason"] == "endpoint"


@pytest.mark.asyncio
async def test_configured_lookup_evaluates_each_bucket_once_for_many_units(
    tmp_path: Path, set_video_request_facts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读侧按桶查找在同一次请求内每桶至多求值一次：单元数不放大配置解析次数。"""
    project = _project(tmp_path, sheet_present=True)
    set_video_request_facts({"i2v": I2V_FACTS, "r2v": R2V_FACTS})
    evaluate = cast(AsyncMock, request_projection.evaluate_video_request_facts)
    units = [
        {"unit_id": f"E1U{index}", "text": "@[阿离] 抬头。" if index % 2 else "空镜。", "duration_seconds": 8}
        for index in range(1, 9)
    ]

    capabilities = await evaluate_reference_unit_capabilities(
        project,
        tmp_path,
        units,
        request_facts=configured_reference_request_facts(project, resolver=None),
        availability=_AvailableAssets(),
    )

    assert [capability.generation_type for capability in capabilities] == ["r2v", "i2v"] * 4
    assert sorted(call.kwargs["generation_type"] for call in evaluate.await_args_list) == ["i2v", "r2v"]
    assert set(reference_unit_capability_payloads(capabilities)) == {unit["unit_id"] for unit in units}


@pytest.mark.asyncio
async def test_read_side_bucket_matches_execution_projector_for_missing_sheet(tmp_path: Path) -> None:
    """画布读到的桶与执行投影的桶出自同一判据：登记了角色图却缺文件的单元两侧都落 i2v。"""
    project = _project(tmp_path, sheet_present=False)
    unit = {"unit_id": "E1U1", "text": "@[阿离] 抬头。", "duration_seconds": 8}
    facts = _CountingFacts()

    (capability,) = await evaluate_reference_unit_capabilities(project, tmp_path, [unit], request_facts=facts)
    projection = await ReferenceUnitRequestProjector(facts, CurrentReferenceAssets(tmp_path, project)).project_current(
        project=project,
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=resolve_reference_assets(project, tmp_path, unit),
    )

    assert capability.generation_type == projection.hydrated_generation_type == "i2v"
    assert capability.request_facts == projection.request_facts
    hydration_codes = [problem.code for problem in capability.hydration.problems]
    assert hydration_codes == ["reference_asset_missing", "reference_capability_changed"]
    assert [problem.code for problem in projection.problems[: len(hydration_codes)]] == hydration_codes


@pytest.mark.asyncio
async def test_no_units_do_not_touch_project_assets(tmp_path: Path) -> None:
    facts = _CountingFacts()

    assert await evaluate_reference_unit_capabilities({}, tmp_path, [], request_facts=facts) == ()
    assert facts.calls == []
