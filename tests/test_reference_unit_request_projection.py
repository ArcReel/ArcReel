from dataclasses import replace
from pathlib import Path

import pytest

from lib.narration_delivery import prepare_narration_delivery
from lib.reference_video.request_projection import (
    POST_PRODUCTION,
    USE_TTS,
    ConfigReferenceCapabilityProjection,
    FilesystemReferenceAssets,
    ProviderProjectionCandidate,
    ReferenceRequestOptions,
    ReferenceUnitRequestProjector,
    ResolvedReferenceAsset,
    resolve_reference_assets,
)
from lib.script_models import ReferenceResource
from lib.speech_composition import admit_script_unit

pytestmark = pytest.mark.unit


class _FakeCapabilities:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def resolve_candidate(self, project: dict, capability: str) -> ProviderProjectionCandidate:
        del project
        self.calls.append(capability)
        if capability == "r2v":
            return ProviderProjectionCandidate(
                capability="r2v",
                provider_id="reference-provider",
                model_id="reference-model",
                supported_durations=(8, 16),
                max_reference_images=2,
                resolution="1080p",
                generate_audio=True,
                requested_generate_audio=True,
                has_audio_track=True,
                audio_switch_controllable=True,
            )
        return ProviderProjectionCandidate(
            capability="i2v",
            provider_id="fallback-provider",
            model_id="fallback-model",
            supported_durations=(4, 8, 16),
            max_reference_images=0,
            resolution="720p",
            generate_audio=False,
            requested_generate_audio=False,
            has_audio_track=False,
            audio_switch_controllable=False,
        )


class _FakeAssets:
    def __init__(self, missing: set[Path]) -> None:
        self._missing = missing

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        return asset.path not in self._missing


def _asset(kind: str, name: str, path: str, *, image_kind: str = "asset") -> ResolvedReferenceAsset:
    return ResolvedReferenceAsset(
        reference=ReferenceResource(type=kind, name=name),
        path=Path(path),
        kind=image_kind,
    )


def test_request_options_only_treat_payload_without_options_as_legacy_confirmed() -> None:
    legacy = ReferenceRequestOptions.from_payload({}, legacy_duration_confirmed=True)
    malformed = ReferenceRequestOptions.from_payload(
        {"reference_request_options": "bad"},
        legacy_duration_confirmed=True,
    )
    partial = ReferenceRequestOptions.from_payload(
        {"reference_request_options": {"narration_delivery": USE_TTS}},
        legacy_duration_confirmed=True,
    )

    assert legacy.legacy_duration_confirmed is True
    assert malformed.legacy_duration_confirmed is False
    assert partial.legacy_duration_confirmed is False


def test_request_options_payload_keeps_only_delivery_and_explicit_accepted_tier() -> None:
    options = ReferenceRequestOptions(
        narration_delivery=USE_TTS,
        confirmed_request_duration_seconds=16,
        current_tts_duration_seconds=9.5,
    )

    assert options.to_payload() == {
        "narration_delivery": USE_TTS,
        "confirmed_request_duration_seconds": 16,
    }
    restored = ReferenceRequestOptions.from_payload(
        {
            "reference_request_options": {
                **options.to_payload(),
                "narration_duration_floor": 123,
                "duration_confirmed": True,
            }
        }
    )
    assert restored.narration_delivery == USE_TTS
    assert restored.confirmed_request_duration_seconds == 16
    assert restored.current_tts_duration_seconds is None
    assert restored.legacy_duration_confirmed is False


@pytest.mark.asyncio
async def test_projection_canonicalizes_current_intent_and_reprojects_after_edit() -> None:
    capabilities = _FakeCapabilities()
    missing_scene = Path("/fake/scene.png")
    projector = ReferenceUnitRequestProjector(capabilities, _FakeAssets({missing_scene}))
    project = {"generation_mode": "reference_video"}
    unit = {
        "unit_id": "E1U1",
        "shots": [{"text": "镜头1：@[手袋] 放在 @[大厅]，@[阿离] 走入画面。"}],
        "references": [
            {"type": "scene", "name": "大厅"},
            {"type": "product", "name": "手袋"},
            {"type": "character", "name": "阿离"},
            {"type": "product", "name": "手袋"},
        ],
        "duration_seconds": 6,
    }
    script = {"video_units": [unit]}
    assets = [
        _asset("character", "阿离", "/fake/character.png"),
        _asset("product", "手袋", "/fake/product-original.png", image_kind="original"),
        _asset("scene", "大厅", str(missing_scene)),
        _asset("product", "手袋", "/fake/product-sheet.png", image_kind="sheet"),
    ]

    first = await projector.project_current(
        project=project,
        script=script,
        unit=unit,
        resolved_assets=assets,
        options=ReferenceRequestOptions(narration_delivery=POST_PRODUCTION),
    )

    assert [(ref.type, ref.name) for ref in first.declared_references] == [
        ("product", "手袋"),
        ("character", "阿离"),
        ("scene", "大厅"),
    ]
    assert [asset.path.name for asset in first.request_assets] == ["product-sheet.png", "product-original.png"]
    assert first.declared_capability == "r2v"
    assert first.hydrated_capability == "r2v"
    assert first.request_duration.seconds == 8
    assert first.provider_candidate is not None
    assert first.provider_candidate.pair_key == "reference-provider/reference-model"
    assert first.cost is not None
    assert first.cost.duration_seconds == 8
    assert [problem.code for problem in first.problems] == [
        "reference_asset_missing",
        "reference_images_clamped",
        "reference_duration_confirmation_required",
    ]

    edited_unit = {**unit, "references": [], "duration_seconds": 12}
    edited_script = {"video_units": [edited_unit]}
    second = await projector.project_current(
        project=project,
        script=edited_script,
        unit=edited_unit,
        resolved_assets=assets,
        options=ReferenceRequestOptions(narration_delivery=POST_PRODUCTION),
    )

    assert second.declared_references == ()
    assert second.request_assets == ()
    assert second.declared_capability == "i2v"
    assert second.hydrated_capability == "i2v"
    assert second.request_duration.seconds == 16
    assert second.provider_candidate is not None
    assert second.provider_candidate.pair_key == "fallback-provider/fallback-model"
    assert [problem.code for problem in second.problems] == ["reference_duration_confirmation_required"]
    assert capabilities.calls == ["r2v", "i2v"]


@pytest.mark.asyncio
async def test_projection_uses_tts_floor_only_for_tts_delivery() -> None:
    capabilities = _FakeCapabilities()
    projector = ReferenceUnitRequestProjector(capabilities, _FakeAssets(set()))
    unit = {"unit_id": "E1U1", "references": [], "duration_seconds": 6}
    script = {"video_units": [unit]}

    tts = await projector.project_current(
        project={},
        script=script,
        unit=unit,
        resolved_assets=[],
        options=ReferenceRequestOptions(narration_delivery=USE_TTS, current_tts_duration_seconds=9.5),
    )
    post = await projector.project_current(
        project={},
        script=script,
        unit=unit,
        resolved_assets=[],
        options=ReferenceRequestOptions(narration_delivery=POST_PRODUCTION, current_tts_duration_seconds=9.5),
    )

    assert tts.duration_input == 9.5
    assert tts.request_duration is not None and tts.request_duration.seconds == 16
    assert post.duration_input == 6
    assert post.request_duration is not None and post.request_duration.seconds == 8


@pytest.mark.asyncio
async def test_projection_requires_confirmation_for_the_current_cross_tier_only() -> None:
    projector = ReferenceUnitRequestProjector(_FakeCapabilities(), _FakeAssets(set()))
    unit = {"unit_id": "E1U1", "references": [], "duration_seconds": 6}

    missing = await projector.project_current(
        project={},
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=[],
    )
    wrong_tier = await projector.project_current(
        project={},
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=[],
        options=ReferenceRequestOptions(confirmed_request_duration_seconds=16),
    )
    accepted = await projector.project_current(
        project={},
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=[],
        options=ReferenceRequestOptions(confirmed_request_duration_seconds=8),
    )

    assert [problem.code for problem in missing.blocking_problems] == ["reference_duration_confirmation_required"]
    assert [problem.code for problem in wrong_tier.blocking_problems] == ["reference_duration_confirmation_required"]
    assert not accepted.blocking_problems


@pytest.mark.asyncio
async def test_projection_requires_exact_confirmation_when_fresh_tts_lands_on_a_larger_existing_tier() -> None:
    projector = ReferenceUnitRequestProjector(_FakeCapabilities(), _FakeAssets(set()))
    unit = {"unit_id": "E1U1", "references": [], "duration_seconds": 4}

    missing = await projector.project_current(
        project={},
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=[],
        options=ReferenceRequestOptions(
            narration_delivery=USE_TTS,
            current_tts_duration_seconds=8.0,
        ),
    )
    accepted = await projector.project_current(
        project={},
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=[],
        options=ReferenceRequestOptions(
            narration_delivery=USE_TTS,
            current_tts_duration_seconds=8.0,
            confirmed_request_duration_seconds=8,
        ),
    )

    assert missing.request_duration is not None and missing.request_duration.seconds == 8
    assert [problem.code for problem in missing.blocking_problems] == ["reference_duration_confirmation_required"]
    assert not accepted.blocking_problems


@pytest.mark.asyncio
async def test_projection_compares_the_request_tier_to_the_selected_visual_not_the_planning_duration() -> None:
    projector = ReferenceUnitRequestProjector(_FakeCapabilities(), _FakeAssets(set()))
    planned_four = {"unit_id": "E1U1", "references": [], "duration_seconds": 4}
    planned_eight = {"unit_id": "E1U2", "references": [], "duration_seconds": 8}

    reusable = await projector.project_current(
        project={},
        script={"video_units": [planned_four]},
        unit=planned_four,
        resolved_assets=[],
        options=ReferenceRequestOptions(
            narration_delivery=USE_TTS,
            current_tts_duration_seconds=8.0,
            current_visual_duration_seconds=8,
        ),
    )
    replacement = await projector.project_current(
        project={},
        script={"video_units": [planned_eight]},
        unit=planned_eight,
        resolved_assets=[],
        options=ReferenceRequestOptions(
            narration_delivery=USE_TTS,
            current_tts_duration_seconds=8.0,
            current_visual_duration_seconds=4,
        ),
    )

    assert reusable.request_duration is not None and reusable.request_duration.seconds == 8
    assert not reusable.blocking_problems
    assert [problem.code for problem in replacement.blocking_problems] == ["reference_duration_confirmation_required"]
    assert replacement.blocking_problems[0].parameters()["current_visual_duration"] == 4


@pytest.mark.asyncio
async def test_projection_rejects_duration_above_maximum_as_needs_replan() -> None:
    projector = ReferenceUnitRequestProjector(_FakeCapabilities(), _FakeAssets(set()))
    unit = {"unit_id": "E1U1", "references": [], "duration_seconds": 18}

    result = await projector.project_current(
        project={},
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=[],
        options=ReferenceRequestOptions(confirmed_request_duration_seconds=16),
    )

    assert result.request_duration is not None
    assert result.request_duration.seconds == 16
    assert [problem.code for problem in result.blocking_problems] == ["needs_replan"]
    assert result.blocking_problems[0].parameters() == {
        "duration_input": 18,
        "maximum_duration": 16,
    }
    assert result.problem_payloads()[0]["action"] == "replan_unit"


@pytest.mark.asyncio
async def test_projection_carries_shared_narration_delivery_blockers() -> None:
    projector = ReferenceUnitRequestProjector(_FakeCapabilities(), _FakeAssets(set()))
    unit = {
        "unit_id": "E1U1",
        "shots": [{"text": "镜头1：海面。\n{旁白内容。}"}],
        "references": [],
        "duration_seconds": 8,
    }
    preparation = admit_script_unit("video_units", unit).preparation
    delivery = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=preparation,
        artifact_path="audio/segment_E1U1.wav",
        settings=None,
        evidence=None,
    )

    result = await projector.project_current(
        project={},
        script={"episode": 1, "video_units": [unit]},
        unit=unit,
        resolved_assets=[],
        options=ReferenceRequestOptions(
            narration_delivery=USE_TTS,
            narration_preparation=delivery,
        ),
    )

    assert result.narration_preparation is delivery
    assert [problem.code for problem in result.blocking_problems] == ["tts_not_configured"]
    assert result.problem_payloads()[0] == {
        "code": "tts_not_configured",
        "blocking": True,
        "unit_id": "E1U1",
        "locations": [{"path": ["generation_settings", "audio_backend"], "line": None}],
        "params": {},
        "reason": "tts_provider_unavailable",
        "action": "configure_tts",
    }
    assert result.to_advisory_payload()["narration_delivery"] == delivery.to_payload()


@pytest.mark.asyncio
async def test_projection_exposes_declared_to_hydrated_bucket_change() -> None:
    capabilities = _FakeCapabilities()
    missing = Path("/fake/missing.png")
    projector = ReferenceUnitRequestProjector(capabilities, _FakeAssets({missing}))
    unit = {
        "unit_id": "E1U1",
        "references": [{"type": "character", "name": "阿离"}],
        "duration_seconds": 8,
    }

    result = await projector.project_current(
        project={},
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=[_asset("character", "阿离", str(missing))],
    )

    assert (result.declared_capability, result.hydrated_capability) == ("r2v", "i2v")
    assert result.provider_candidate is not None
    assert result.provider_candidate.pair_key == "fallback-provider/fallback-model"
    assert [problem.code for problem in result.problems[:2]] == [
        "reference_asset_missing",
        "reference_capability_changed",
    ]
    assert all(problem.blocking for problem in result.problems[:2])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("references", "expected_names", "expected_capability", "invalid_count"),
    [
        ({"type": "character", "name": "阿离"}, [], "i2v", 1),
        ("character: 阿离", [], "i2v", 1),
        (
            [
                {"type": "character", "name": "阿离"},
                "bad-entry",
                {"type": "unknown", "name": "未登记类型"},
                {"type": "scene", "name": ""},
            ],
            ["阿离"],
            "r2v",
            3,
        ),
    ],
)
async def test_projection_blocks_malformed_references_without_discarding_valid_entries(
    references: object,
    expected_names: list[str],
    expected_capability: str,
    invalid_count: int,
) -> None:
    capabilities = _FakeCapabilities()
    projector = ReferenceUnitRequestProjector(capabilities, _FakeAssets(set()))
    unit = {"unit_id": "E1U1", "references": references, "duration_seconds": 8}
    resolved_assets = [_asset("character", "阿离", "/fake/character.png")] if isinstance(references, list) else []

    result = await projector.project_current(
        project={},
        script={"video_units": [unit]},
        unit=unit,
        resolved_assets=resolved_assets,
    )

    assert [reference.name for reference in result.declared_references] == expected_names
    assert result.hydrated_capability == expected_capability
    assert result.blocking_problems
    problem = result.problems[0]
    assert (problem.code, problem.blocking, problem.parameters()) == (
        "reference_declaration_invalid",
        True,
        {"count": invalid_count},
    )


@pytest.mark.asyncio
async def test_projection_blocks_empty_duration_metadata_without_cost_facts() -> None:
    base = await _FakeCapabilities().resolve_candidate({}, "i2v")

    class _MissingDurations:
        async def resolve_candidate(self, project: dict, capability: str) -> ProviderProjectionCandidate:
            del project, capability
            return replace(base, supported_durations=())

    projector = ReferenceUnitRequestProjector(_MissingDurations(), _FakeAssets(set()))
    unit = {"unit_id": "E1U1", "references": [], "duration_seconds": 8}
    result = await projector.project_current(project={}, script={"video_units": [unit]}, unit=unit, resolved_assets=[])

    assert result.request_duration is None
    assert result.cost is None
    assert [(problem.code, problem.blocking) for problem in result.problems] == [
        ("reference_supported_durations_missing", True)
    ]


@pytest.mark.asyncio
async def test_projection_sanitizes_unexpected_capability_failures() -> None:
    class _BrokenCapabilities:
        async def resolve_candidate(self, project: dict, capability: str) -> ProviderProjectionCandidate:
            del project, capability
            raise RuntimeError("database password leaked by driver")

    projector = ReferenceUnitRequestProjector(_BrokenCapabilities(), _FakeAssets(set()))
    unit = {"unit_id": "E1U1", "references": [], "duration_seconds": 8}

    result = await projector.project_current(project={}, script={"video_units": [unit]}, unit=unit, resolved_assets=[])

    assert result.cost is None
    assert len(result.problems) == 1
    assert result.problems[0].code == "reference_capability_unavailable"
    assert result.problems[0].parameters() == {"capability": "i2v"}


@pytest.mark.asyncio
async def test_projection_owns_audio_switch_conflict() -> None:
    base = await _FakeCapabilities().resolve_candidate({}, "i2v")

    class _AlwaysAudible:
        async def resolve_candidate(self, project: dict, capability: str) -> ProviderProjectionCandidate:
            del project, capability
            return replace(
                base,
                requested_generate_audio=False,
                has_audio_track=True,
                audio_switch_controllable=False,
            )

    projector = ReferenceUnitRequestProjector(_AlwaysAudible(), _FakeAssets(set()))
    unit = {"unit_id": "E1U1", "references": [], "duration_seconds": 8}
    result = await projector.project_current(project={}, script={"video_units": [unit]}, unit=unit, resolved_assets=[])

    assert any(problem.code == "video_audio_switch_not_supported" for problem in result.blocking_problems)


def test_asset_adapter_expands_products_and_preserves_missing_candidates(tmp_path: Path) -> None:
    for rel in ("products/bag-sheet.png", "products/bag-original.png", "characters/a.png"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"image")
    project = {
        "products": {
            "手袋": {
                "product_sheet": "products/bag-sheet.png",
                "reference_images": ["products/bag-original.png"],
            }
        },
        "characters": {"阿离": {"character_sheet": "characters/a.png"}},
        "scenes": {"大厅": {"scene_sheet": "scenes/missing.png"}},
    }
    unit = {
        "references": [
            {"type": "scene", "name": "大厅"},
            {"type": "product", "name": "手袋"},
            {"type": "character", "name": "阿离"},
        ]
    }

    assets = resolve_reference_assets(project, tmp_path, unit)

    assert [(item.reference.type, item.kind, item.path.name) for item in assets] == [
        ("product", "sheet", "bag-sheet.png"),
        ("product", "original", "bag-original.png"),
        ("character", "asset", "a.png"),
        ("scene", "asset", "missing.png"),
    ]
    availability = FilesystemReferenceAssets(tmp_path)
    assert [availability.is_available(item) for item in assets] == [True, True, True, False]


@pytest.mark.asyncio
async def test_config_adapter_resolves_candidate_and_rejects_missing_durations() -> None:
    class _Resolver:
        empty = False

        async def video_capabilities_for_project(self, project: dict, *, capability: str) -> dict:
            del project
            return {
                "provider_id": "ark",
                "model": "m",
                "supported_durations": [] if self.empty else [4, 8],
                "max_reference_images": 3,
                "generate_audio": True,
                "requested_generate_audio": True,
                "voice_consistency": "native",
            }

        async def resolve_resolution(self, project: dict, provider_id: str, model_id: str) -> str:
            del project, provider_id, model_id
            return "1080p"

    resolver = _Resolver()
    adapter = ConfigReferenceCapabilityProjection(resolver)
    candidate = await adapter.resolve_candidate({}, "r2v")
    assert candidate.pair_key == "ark/m"
    assert candidate.supported_durations == (4, 8)
    assert candidate.resolution == "1080p"

    resolver.empty = True
    adapter = ConfigReferenceCapabilityProjection(resolver)
    with pytest.raises(ValueError) as exc_info:
        await adapter.resolve_candidate({}, "r2v")
    assert getattr(exc_info.value, "code") == "reference_supported_durations_missing"

    class _InvalidResolver(_Resolver):
        async def video_capabilities_for_project(self, project: dict, *, capability: str) -> dict:
            del project, capability
            raise ValueError("supported_durations contains malformed JSON")

    with pytest.raises(ValueError) as invalid_exc:
        await ConfigReferenceCapabilityProjection(_InvalidResolver()).resolve_candidate({}, "r2v")
    assert getattr(invalid_exc.value, "code") == "reference_supported_durations_invalid"

    class _InvalidValuesResolver(_Resolver):
        async def video_capabilities_for_project(self, project: dict, *, capability: str) -> dict:
            del project, capability
            payload = await super().video_capabilities_for_project({}, capability="r2v")
            payload["supported_durations"] = [4, "bad"]
            return payload

    with pytest.raises(ValueError) as invalid_values_exc:
        await ConfigReferenceCapabilityProjection(_InvalidValuesResolver()).resolve_candidate({}, "r2v")
    assert getattr(invalid_values_exc.value, "code") == "reference_supported_durations_invalid"
