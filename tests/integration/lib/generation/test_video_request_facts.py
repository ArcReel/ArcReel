"""视频请求事实求值接口：真实 ConfigResolver + 内存测试库，按「项目 × 路线 × 桶 × 身份来源」断言事实或失败。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import update

from arcreel_catalog_core import DurationConstraints
from lib.catalog import query as catalog
from lib.catalog.query import catalog_data
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.custom_provider import make_provider_id
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.generation.video_request_facts import (
    ResolutionOverride,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    snapshot_video_request_facts,
)
from lib.video_runtime.resolution import VideoRuntime, read_video_request_facts
from tests.catalog_support import install_catalog, with_entry_changes, with_entry_facts
from tests.factories import seed_endpoint_fixed_video_model

VEO_PROVIDER, VEO_MODEL = "gemini-aistudio", "veo-3.1-generate-preview"
VEO = f"{VEO_PROVIDER}/{VEO_MODEL}"
VIDU2 = "vidu/vidu2.0"


@pytest.fixture
def resolver(db_factory, monkeypatch) -> ConfigResolver:
    install_catalog(
        monkeypatch,
        with_entry_changes(catalog.factory_snapshot(), VEO_PROVIDER, VEO_MODEL, discontinued=False, shutdown_date=None),
    )
    return ConfigResolver(db_factory)


def _with_resolution(project: dict, pair: str, resolution: str) -> dict:
    return {**project, "model_settings": {pair: {"resolution": resolution}}}


async def _seed_custom_video_models(db_factory, *rows: dict) -> str:
    async with db_factory() as session:
        provider = CustomProvider(
            display_name="Prov", discovery_format="openai", base_url="https://api.example.com", api_key="k"
        )
        session.add(provider)
        await session.flush()
        for row in rows:
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    display_name=row["model_id"],
                    **{"endpoint": "newapi-video", **row},
                )
            )
        await session.commit()
        return make_provider_id(provider.id)


async def _read(resolver, project: dict, *, route="storyboard", generation_type="i2v"):
    return await read_video_request_facts(
        project,
        route=route,
        generation_type=generation_type,
        resolver=resolver,
    )


async def _materialized_facts(
    resolver: ConfigResolver,
    project: dict,
    provider_id: str,
    model_id: str,
    *,
    route="storyboard",
    generation_type="i2v",
    uses_end_frame: bool = False,
):
    target = await VideoRuntime(resolver).materialized(ProviderModel(provider_id, model_id))
    requested_audio = await resolver.video_generate_audio_for_project(project)
    return snapshot_video_request_facts(
        target.snapshot,
        project,
        route=route,
        generation_type=generation_type,
        requested_audio=requested_audio,
        uses_end_frame=uses_end_frame,
    )


@pytest.mark.parametrize(
    ("project", "route", "generation_type", "resolution", "allowed", "excluded"),
    [
        pytest.param(
            {"video_provider_r2v": VEO, "video_provider_i2v": "ark/doubao-seedance-2-0-260128"},
            "reference_video",
            "i2v",
            None,
            tuple(range(4, 16)),
            (),
            id="no-image-uses-wider-i2v-tiers",
        ),
        pytest.param({"video_provider_i2v": VEO}, "storyboard", "i2v", None, (4, 6, 8), (), id="veo-unset"),
        pytest.param(
            _with_resolution({"video_provider_i2v": VEO}, VEO, "1080p"),
            "storyboard",
            "i2v",
            "1080p",
            (8,),
            ((4, "resolution"), (6, "resolution")),
            id="veo-1080p",
        ),
        pytest.param(
            {"video_provider_r2v": VEO},
            "reference_video",
            "r2v",
            None,
            (8,),
            ((4, "reference"), (6, "reference")),
            id="veo-with-reference-images",
        ),
        pytest.param(
            _with_resolution({"video_provider_r2v": VEO}, VEO, "1080p"),
            "reference_video",
            "r2v",
            "1080p",
            (8,),
            ((4, "reference"), (6, "reference")),
            id="veo-both-constraints-report-reference",
        ),
        pytest.param(
            {"video_provider_i2v": VEO},
            "reference_video",
            "i2v",
            None,
            (4, 6, 8),
            (),
            id="veo-without-reference-images",
        ),
        pytest.param({"video_provider_i2v": VIDU2}, "storyboard", "i2v", None, (4, 8), (), id="vidu2-unset"),
        pytest.param(
            _with_resolution({"video_provider_i2v": VIDU2}, VIDU2, "1080p"),
            "storyboard",
            "i2v",
            "1080p",
            (4,),
            ((8, "resolution"),),
            id="vidu2-1080p",
        ),
    ],
)
async def test_resolution_and_reference_images_narrow_the_tiers(
    resolver, project, route, generation_type, resolution, allowed, excluded
):
    facts = await _read(resolver, project, route=route, generation_type=generation_type)

    assert isinstance(facts, VideoRequestFacts)
    assert facts.resolution == resolution
    assert facts.allowed_durations == allowed
    assert facts.excluded_durations == excluded
    assert facts.duration_endpoint_fixed is False


async def test_facts_name_the_configured_execution_model(resolver):
    facts = await _read(resolver, {"video_provider_i2v": VEO, "video_backend": VIDU2})

    assert isinstance(facts, VideoRequestFacts)
    assert (facts.route, facts.generation_type) == ("storyboard", "i2v")
    assert (facts.provider_id, facts.model_id) == (VEO_PROVIDER, VEO_MODEL)
    assert facts.supported_durations == (4, 6, 8)


async def test_endpoint_fixed_duration_is_a_legal_empty_tier_set(resolver, db_factory):
    pair = await seed_endpoint_fixed_video_model(db_factory)

    facts = await _read(resolver, {"video_provider_i2v": pair})

    assert isinstance(facts, VideoRequestFacts)
    assert facts.duration_endpoint_fixed is True
    assert facts.supported_durations == ()
    assert facts.allowed_durations == ()
    assert facts.excluded_durations == ()


async def test_model_missing_from_catalog_fails_asking_to_reselect(resolver):
    facts = await _read(resolver, {"video_provider_i2v": f"{VEO_PROVIDER}/retired-model"})

    assert facts == VideoRequestFactsFailure(
        "model_not_in_provider_catalog",
        (("provider", VEO_PROVIDER), ("model", "retired-model")),
    )


async def test_no_configured_video_model_fails_as_capability_unavailable(resolver):
    facts = await _read(resolver, {})

    assert isinstance(facts, VideoRequestFactsFailure)
    assert facts.code == "video_capability_unavailable"
    assert facts.parameters() == {"capability": "i2v"}
    assert facts.action == "configure_video_model"


@pytest.mark.parametrize("route", ["storyboard", "reference_video"])
async def test_database_query_failure_returns_unavailable_facts(resolver, db_factory, db_engine, route):
    provider_id = await _seed_custom_video_models(
        db_factory, {"model_id": "m", "capability_overrides": {"durations": [5]}, "is_default": True}
    )
    async with db_engine.begin() as connection:
        await connection.run_sync(CustomProviderModel.__table__.drop)

    result = await read_video_request_facts(
        {"video_provider_i2v": f"{provider_id}/m"},
        route=route,
        generation_type="i2v",
        resolver=resolver,
    )

    prefix = "video" if route == "storyboard" else "reference"
    assert result == VideoRequestFactsFailure(f"{prefix}_capability_unavailable", (("capability", "i2v"),))


async def test_custom_model_without_durations_falls_back_to_name_preset(resolver, db_factory):
    """对应不上条目、行上也没有可选时长：运行时取按名字推断的预设，一个族都匹配不上时为 [4, 8]。"""
    provider_id = await _seed_custom_video_models(db_factory, {"model_id": "m", "is_default": True})

    facts = await _read(resolver, {"video_provider_i2v": f"{provider_id}/m"})

    assert isinstance(facts, VideoRequestFacts)
    assert facts.supported_durations == (4, 8)


async def test_custom_model_matched_to_entry_takes_entry_durations_and_constraints(resolver, db_factory):
    """名字对上条目的自定义模型：可选时长与时长联动约束取条目（Veo 在 1080p 下只剩 8 秒）。"""
    provider_id = await _seed_custom_video_models(
        db_factory, {"model_id": "vendor/Veo-3.1-Generate-001", "resolution": "1080p", "is_default": True}
    )

    facts = await _read(resolver, {"video_provider_i2v": f"{provider_id}/vendor/Veo-3.1-Generate-001"})

    assert isinstance(facts, VideoRequestFacts)
    assert facts.supported_durations == (4, 6, 8)
    assert facts.allowed_durations == (8,)
    assert facts.excluded_durations == ((4, "resolution"), (6, "resolution"))


async def test_custom_model_constraint_override_replaces_entry_constraints(resolver, db_factory):
    """能力覆盖整维替换时长联动约束：分辨率键按小写比较，参考图约束随这一维一起被替换掉。"""
    provider_id = await _seed_custom_video_models(
        db_factory,
        {
            "endpoint": "v2-video-generations",
            "model_id": "vendor/Veo-3.1-Generate-001",
            "resolution": "1080p",
            "is_default": True,
            "capability_overrides": {
                "duration_constraints": {"by_resolution": {"1080P": [6, 8]}},
                "max_reference_images": 3,
            },
        },
    )

    facts = await _read(
        resolver, {"video_provider_r2v": f"{provider_id}/vendor/Veo-3.1-Generate-001"}, generation_type="r2v"
    )

    assert isinstance(facts, VideoRequestFacts)
    assert facts.allowed_durations == (6, 8)


async def test_constraints_narrowing_to_nothing_fail_instead_of_falling_back(resolver, monkeypatch):
    constraints = DurationConstraints(by_resolution={"1080p": (10,)}, with_reference_images=(8,))
    install_catalog(
        monkeypatch, with_entry_facts(catalog_data(), VEO_PROVIDER, VEO_MODEL, duration_constraints=constraints)
    )

    facts = await _read(resolver, _with_resolution({"video_provider_i2v": VEO}, VEO, "1080p"))

    assert facts == VideoRequestFactsFailure(
        "video_supported_durations_incompatible",
        (("provider", VEO_PROVIDER), ("model", VEO_MODEL), ("resolution", "1080p"), ("capability", "i2v")),
        excluded_durations=((4, "resolution"), (6, "resolution"), (8, "resolution")),
    )


@pytest.mark.parametrize("route", ["storyboard", "reference_video"])
async def test_unavailable_execution_identity_fails_instead_of_using_the_default_model(resolver, db_factory, route):
    provider_id = await _seed_custom_video_models(
        db_factory,
        {"model_id": "m-dead", "is_enabled": False, "capability_overrides": {"durations": [5]}},
        {"model_id": "m-live", "is_default": True, "capability_overrides": {"durations": [8]}},
    )

    with pytest.raises(ValueError, match="custom model not found"):
        await _materialized_facts(resolver, {}, provider_id, "m-dead", route=route, generation_type="i2v")


@pytest.mark.parametrize(("route", "generation_type"), [("storyboard", "i2v"), ("reference_video", "r2v")])
async def test_configured_identity_read_snapshot_is_single_session_consistent(db_factory, route, generation_type):
    provider_id = await _seed_custom_video_models(
        db_factory,
        {
            "endpoint": "v2-video-generations",
            "model_id": "m-selected",
            "capability_overrides": {"durations": [5], "first_frame": True, "max_reference_images": 4},
        },
        {
            "model_id": "m-default",
            "is_default": True,
            "capability_overrides": {"durations": [8], "first_frame": False, "max_reference_images": 0},
        },
    )
    disabled = []

    @asynccontextmanager
    async def changing_sessions():
        async with db_factory() as session:
            yield session
        if not disabled:
            disabled.append(True)
            async with db_factory() as session:
                await session.execute(
                    update(CustomProviderModel)
                    .where(CustomProviderModel.model_id == "m-selected")
                    .values(is_enabled=False)
                )
                await session.commit()

    result = await _read(
        ConfigResolver(changing_sessions),
        {f"video_provider_{generation_type}": f"{provider_id}/m-selected"},
        route=route,
        generation_type=generation_type,
    )

    assert isinstance(result, VideoRequestFacts)
    assert result.model_id == "m-selected"
    assert result.allowed_durations == (5,)


async def test_execution_identity_follows_the_backend_when_it_diverges_from_configuration(resolver, db_factory):
    """配置身份已禁用时读侧报悬空；调用方给定的有效执行身份独立求值。"""
    provider_id = await _seed_custom_video_models(
        db_factory,
        {"model_id": "m-dead", "is_enabled": False, "capability_overrides": {"durations": [5]}},
        {"model_id": "m-live", "is_default": True, "resolution": "540p", "capability_overrides": {"durations": [4, 6]}},
    )
    project = {"video_provider_i2v": f"{provider_id}/m-dead"}

    read = await _read(resolver, project)
    executed = await _materialized_facts(
        resolver, project, provider_id, "m-live", route="storyboard", generation_type="i2v"
    )

    assert isinstance(read, VideoRequestFactsFailure)
    assert read.code == "video_capability_reference_unavailable"
    assert isinstance(executed, VideoRequestFacts)
    assert (executed.model_id, executed.resolution, executed.allowed_durations) == ("m-live", "540p", (4, 6))


@pytest.mark.parametrize(
    ("project", "route", "generation_type"),
    [
        pytest.param({"video_provider_i2v": VEO}, "storyboard", "i2v", id="veo-unset"),
        pytest.param(_with_resolution({"video_provider_i2v": VEO}, VEO, "720p"), "storyboard", "i2v", id="veo-720p"),
        pytest.param({"video_provider_i2v": VIDU2}, "storyboard", "i2v", id="vidu2-unset"),
        pytest.param(
            {"video_provider_r2v": VEO, "generation_mode": "reference_video"}, "reference_video", "r2v", id="veo-r2v"
        ),
        pytest.param(
            {"video_provider_i2v": VEO, "generation_mode": "reference_video"},
            "reference_video",
            "i2v",
            id="veo-ref-i2v",
        ),
    ],
)
async def test_read_and_execution_sides_agree_when_identities_match(resolver, project, route, generation_type):
    """读侧（预检、报价）与执行侧对同一份配置给出同一组档位与请求分辨率。"""
    read = await _read(resolver, project, route=route, generation_type=generation_type)
    assert isinstance(read, VideoRequestFacts)

    executed = await _materialized_facts(
        resolver, project, read.provider_id, read.model_id, route=route, generation_type=generation_type
    )

    assert executed == read


async def test_reference_buckets_use_their_own_model_resolution_on_both_sides(resolver):
    project = {
        "generation_mode": "reference_video",
        "video_provider_i2v": VIDU2,
        "video_provider_r2v": VEO,
        "model_settings": {VIDU2: {"resolution": "720p"}, VEO: {"resolution": "1080p"}},
    }
    for bucket, resolution, allowed in (("i2v", "720p", (4, 8)), ("r2v", "1080p", (8,))):
        read = await _read(resolver, project, route="reference_video", generation_type=bucket)
        assert isinstance(read, VideoRequestFacts)
        assert read.resolution == resolution
        assert read.allowed_durations == allowed
        executed = await _materialized_facts(
            resolver, project, read.provider_id, read.model_id, route="reference_video", generation_type=bucket
        )
        assert executed == read


@pytest.mark.parametrize(
    ("generation_type", "override", "resolution", "allowed", "excluded"),
    [
        pytest.param("i2v", "720p", "720p", (4, 6, 8), (), id="unsaved-720p"),
        pytest.param("i2v", None, None, (4, 6, 8), (), id="unsaved-auto"),
        pytest.param("r2v", "720p", "720p", (8,), ((4, "reference"), (6, "reference")), id="unsaved-720p-r2v"),
        pytest.param("r2v", None, None, (8,), ((4, "reference"), (6, "reference")), id="unsaved-auto-r2v"),
    ],
)
async def test_resolution_override_previews_an_unsaved_resolution(
    resolver, generation_type, override, resolution, allowed, excluded
):
    """覆盖分辨率取代项目已保存的档位作为请求分辨率，其余求值不变。"""
    project = _with_resolution({f"video_provider_{generation_type}": VEO}, VEO, "1080p")

    preview = await read_video_request_facts(
        project,
        route="storyboard" if generation_type == "i2v" else "reference_video",
        generation_type=generation_type,
        resolver=resolver,
        resolution_override=ResolutionOverride(override),
    )

    assert isinstance(preview, VideoRequestFacts)
    assert (preview.provider_id, preview.model_id) == (VEO_PROVIDER, VEO_MODEL)
    assert preview.resolution == resolution
    assert preview.allowed_durations == allowed
    assert preview.excluded_durations == excluded


async def test_resolution_override_leaves_read_and_execution_evaluations_on_the_saved_resolution(resolver):
    """预览过未保存的分辨率后，不传覆盖的读侧与执行侧仍按已保存档位求值。"""
    project = _with_resolution({"video_provider_i2v": VEO}, VEO, "1080p")
    await read_video_request_facts(
        project,
        route="storyboard",
        generation_type="i2v",
        resolver=resolver,
        resolution_override=ResolutionOverride("720p"),
    )

    read = await _read(resolver, project)
    executed = await _materialized_facts(
        resolver, project, VEO_PROVIDER, VEO_MODEL, route="storyboard", generation_type="i2v"
    )

    assert isinstance(read, VideoRequestFacts)
    assert (read.resolution, read.allowed_durations) == ("1080p", (8,))
    assert executed == read
    assert project["model_settings"] == {VEO: {"resolution": "1080p"}}


async def test_auto_resolution_override_falls_back_to_the_custom_model_default(resolver, db_factory):
    """预览「自动」等于项目未存档位：自定义供应商仍取模型默认档，与保存后的求值一致。"""
    provider_id = await _seed_custom_video_models(
        db_factory,
        {"model_id": "m", "is_default": True, "resolution": "540p", "capability_overrides": {"durations": [4, 6]}},
    )
    pair = f"{provider_id}/m"

    preview = await read_video_request_facts(
        _with_resolution({"video_provider_i2v": pair}, pair, "1080p"),
        route="storyboard",
        generation_type="i2v",
        resolver=resolver,
        resolution_override=ResolutionOverride(None),
    )
    saved = await _read(resolver, {"video_provider_i2v": pair})

    assert isinstance(preview, VideoRequestFacts)
    assert preview.resolution == "540p"
    assert preview == saved


@pytest.mark.parametrize(
    ("pair", "generation_type", "requested", "expected", "voice_consistency"),
    [
        (VEO, "i2v", False, (False, True, True, False), "soft"),
        ("dashscope/wan2.7-i2v", "i2v", False, (False, True, True, False), "soft"),
        ("kling/kling-v3-omni", "i2v", False, (False, False, True, True), "soft"),
        ("kling/kling-v3-omni", "r2v", False, (False, False, False, False), "none"),
        ("kling/kling-v3-omni", "r2v", True, (True, False, False, False), "none"),
    ],
)
async def test_audio_facts_follow_the_request_bucket(
    resolver, pair, generation_type, requested, expected, voice_consistency
):
    facts = await _read(
        resolver,
        {f"video_provider_{generation_type}": pair, "video_generate_audio": requested},
        route="reference_video",
        generation_type=generation_type,
    )

    assert isinstance(facts, VideoRequestFacts)
    assert (
        facts.requested_generate_audio,
        facts.generate_audio,
        facts.has_audio_track,
        facts.audio_switch_controllable,
    ) == expected
    assert facts.voice_consistency == voice_consistency


async def test_request_shaping_capabilities_come_from_the_same_evaluation(resolver):
    """参考图上限、无图能力位与参考音频形态随同一次求值给出，请求组装直接消费求值结果。"""
    from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

    facts = await _read(resolver, {"video_provider_r2v": VEO}, route="reference_video", generation_type="r2v")

    declared = builtin_video_capabilities_for_model(VEO_PROVIDER, VEO_MODEL)
    assert isinstance(facts, VideoRequestFacts)
    assert facts.max_reference_images == declared.max_reference_images
    assert (facts.text_to_video, facts.first_frame) == (declared.text_to_video, declared.first_frame)
    assert facts.max_reference_audio_count == declared.max_reference_audio_count
    assert facts.reference_audio_per_image is declared.reference_audio_per_image


@pytest.mark.parametrize(
    ("endpoint", "overrides", "mode", "has_audio", "controllable"),
    [
        ("dashscope-video", {}, "always_on", True, False),
        ("openai-video", {}, "always_on", True, False),
        ("gemini-interactions-video", {}, "always_on", True, False),
        ("kling-video-model-path", {}, "always_on", True, False),
        ("minimax-h3", {}, "always_on", True, False),
        ("minimax-hailuo-v1", {}, "always_off", False, False),
        ("minimax-s2v-01", {}, "always_off", False, False),
        ("dashscope-video", {"audio_track": "always_off"}, "always_off", False, False),
        ("newapi-video", {}, "controllable", True, True),
        ("newapi-video", {"audio_track": "always_off"}, "always_off", False, True),
        ("newapi-video", {"audio_track": "always_on"}, "controllable", True, True),
        ("dashscope-video", {"audio_track": "controllable"}, "always_on", True, False),
    ],
)
async def test_custom_audio_facts_agree_across_settings_preflight_and_voice(
    resolver, db_factory, endpoint, overrides, mode, has_audio, controllable
):
    from pathlib import Path

    from arcreel_market_core.video_backend_contract import VideoGenerationRequest
    from lib.custom_provider.capabilities import synthesize_video_capabilities
    from lib.custom_provider.capability_view import custom_capability_view
    from lib.custom_provider.endpoints import get_endpoint_spec
    from lib.custom_provider.metering import custom_video_usage, video_metering_for
    from lib.generation.video_request_facts import audio_switch_conflict
    from tests.http_capture import capture_http

    provider = await _seed_custom_video_models(
        db_factory,
        {
            "model_id": "unrecorded-model",
            "endpoint": endpoint,
            "capability_overrides": {"durations": [5], **overrides},
            "is_default": True,
        },
    )
    view = custom_capability_view(
        model_id="unrecorded-model",
        endpoint=endpoint,
        spec=get_endpoint_spec(endpoint),
        raw_overrides=overrides,
        applied_overrides=overrides,
        entry_binding=None,
    )
    assert view.effective["has_audio_track"] is has_audio
    assert view.effective["reference_route_has_audio_track"] is has_audio
    assert view.effective["audio_switch_controllable"] is controllable
    generation_type = "r2v" if endpoint == "minimax-s2v-01" else "i2v"
    with capture_http() as http:
        facts = await _read(
            resolver,
            {
                f"video_provider_{generation_type}": f"{provider}/unrecorded-model",
                "video_generate_audio": False,
            },
            generation_type=generation_type,
        )
        assert isinstance(facts, VideoRequestFacts)
        assert (facts.has_audio_track, facts.audio_switch_controllable) == (has_audio, controllable)
        assert facts.voice_consistency == ("soft" if has_audio else "none")
        failure = audio_switch_conflict(facts)
        assert (failure.code if failure else None) == (
            "video_audio_switch_not_supported" if has_audio and not controllable else None
        )
        assert not http.calls
    spec = get_endpoint_spec(endpoint)
    request = VideoGenerationRequest(
        prompt="p",
        output_path=Path("unused.mp4"),
        generate_audio=True,
        video_capabilities=synthesize_video_capabilities(
            endpoint=endpoint, model_id="unrecorded-model", overrides=overrides
        ),
    )
    assert request.video_capabilities
    assert request.video_capabilities.audio_track == mode
    usage = custom_video_usage(video_metering_for(spec.metering_backend, spec.definition), "unrecorded-model", request)
    assert usage.dimensions["audio"] is has_audio


async def test_comfyui_silent_binding_reaches_preflight_and_voice(resolver, db_factory):
    pair = await seed_endpoint_fixed_video_model(db_factory)
    facts = await _read(resolver, {"video_provider_i2v": pair, "video_generate_audio": True})
    assert isinstance(facts, VideoRequestFacts)
    assert (facts.has_audio_track, facts.audio_switch_controllable, facts.generate_audio) == (False, False, False)
    assert facts.voice_consistency == "none"

    from pathlib import Path

    from arcreel_market_core.video_backend_contract import VideoGenerationRequest
    from lib.custom_provider.capabilities import synthesize_video_capabilities
    from lib.custom_provider.capability_view import custom_capability_view
    from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
    from lib.custom_provider.metering import custom_video_usage
    from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    async with db_factory() as session:
        model = await CustomProviderRepository(session).get_model_by_ids(
            int(pair.split("/")[0].removeprefix("custom-")), "wan-workflow"
        )
        assert model is not None
        spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
        caps = synthesize_video_capabilities(
            endpoint=model.endpoint, model_id=model.model_id, overrides=None, endpoint_spec=spec
        )
        view = custom_capability_view(
            model_id=model.model_id,
            endpoint=model.endpoint,
            spec=spec,
            raw_overrides=None,
            applied_overrides=None,
            entry_binding=None,
        )
    assert view.effective["has_audio_track"] is False
    usage = custom_video_usage(
        None,
        "wan-workflow",
        VideoGenerationRequest(
            prompt="p",
            output_path=Path("unused.mp4"),
            generate_audio=True,
            video_capabilities=caps,
        ),
    )
    assert usage.dimensions["audio"] is False


async def test_end_frame_narrows_request_but_not_planning(resolver, monkeypatch):
    install_catalog(
        monkeypatch,
        with_entry_facts(
            catalog_data(),
            VEO_PROVIDER,
            VEO_MODEL,
            duration_constraints=DurationConstraints(with_end_frame=(4, 6)),
        ),
    )
    project = {"video_provider_i2v": VEO}
    planning = await _read(resolver, project)
    request = await read_video_request_facts(
        project,
        route="storyboard",
        generation_type="i2v",
        resolver=resolver,
        uses_end_frame=True,
    )
    assert isinstance(planning, VideoRequestFacts)
    assert planning.allowed_durations == (4, 6, 8)
    assert isinstance(request, VideoRequestFacts)
    assert request.allowed_durations == (4, 6)
    assert request.excluded_durations == ((8, "end_frame"),)


async def test_end_frame_intersects_resolution_and_reference_constraints(resolver, monkeypatch):
    install_catalog(
        monkeypatch,
        with_entry_facts(
            catalog_data(),
            VEO_PROVIDER,
            VEO_MODEL,
            duration_constraints=DurationConstraints(
                by_resolution={"1080p": (6, 8)},
                with_reference_images=(4, 8),
                with_end_frame=(4, 6),
            ),
        ),
    )
    failure = await read_video_request_facts(
        _with_resolution({"video_provider_r2v": VEO}, VEO, "1080p"),
        route="reference_video",
        generation_type="r2v",
        resolver=resolver,
        uses_end_frame=True,
    )
    assert isinstance(failure, VideoRequestFactsFailure)
    assert failure.code == "video_end_frame_durations_incompatible"
    assert failure.action == "fix_input"
    assert dict(failure.excluded_durations)[8] == "end_frame"


async def test_custom_model_end_frame_override_replaces_entry_constraints(resolver, db_factory):
    provider = await _seed_custom_video_models(
        db_factory,
        {
            "model_id": "vendor/Veo-3.1-Generate-001",
            "is_default": True,
            "endpoint": "gemini-video",
            "capability_overrides": {"last_frame": True, "duration_constraints": {"with_end_frame": [4, 6]}},
        },
    )
    result = await read_video_request_facts(
        {"video_provider_i2v": f"{provider}/vendor/Veo-3.1-Generate-001"},
        route="storyboard",
        generation_type="i2v",
        resolver=resolver,
        uses_end_frame=True,
    )
    assert isinstance(result, VideoRequestFacts)
    assert result.allowed_durations == (4, 6)
    assert result.excluded_durations == ((8, "end_frame"),)


@pytest.mark.parametrize("requested", [True, False])
@pytest.mark.parametrize("has_audio", [True, False])
async def test_aistudio_bills_audio_in_estimate_and_ledger(resolver, db_factory, requested, has_audio):
    from pathlib import Path

    from arcreel_market_core.video_backend_contract import VideoGenerationResult
    from lib.backends.video_backends.gemini import GeminiVideoBackend
    from lib.billing.ledger import Ledger
    from lib.db.models.api_call import ApiCall
    from lib.generation.video_request_facts import VideoRequestCostFacts
    from server.services.admission.cost_estimation import quote_video_request

    model = "relay-veo"
    catalog.install_user_added_models(
        [
            catalog.UserAddedModel(
                VEO_PROVIDER,
                model,
                "video",
                endpoint="gemini-video",
                entry_binding="google/veo-3.1-generate-preview",
                capability_overrides={"has_audio_track": has_audio, "durations": [8]},
            )
        ]
    )
    try:
        project = {"video_provider_i2v": f"{VEO_PROVIDER}/{model}", "video_generate_audio": requested}
        facts = await _read(resolver, project)
        assert isinstance(facts, VideoRequestFacts)
        assert facts.has_audio_track is has_audio
        cost = VideoRequestCostFacts(facts, 8, "16:9", has_first_frame=True)
        request = cost.provider_request()
        quote = await quote_video_request(cost, db_factory)
        assert quote is not None
        assert quote.amount == pytest.approx(3.2)
        result = VideoGenerationResult(Path("out.mp4"), VEO_PROVIDER, model, 8, generate_audio=False)
        assert request.generate_audio is requested
        ledger = Ledger(session_factory=db_factory)
        async with ledger.record(project_name="demo", model=model, provider=VEO_PROVIDER, call_type="video") as call:
            call.success(result, usage=GeminiVideoBackend.metered_usage(model, request, result))
        async with db_factory() as session:
            row = await session.get(ApiCall, call.call_id)
            assert row is not None
            assert row.generate_audio is True
            assert row.cost_amount == pytest.approx(quote.amount)
    finally:
        catalog.install_user_added_models(())


async def test_vidu_tail_frame_quote_preserves_submission_inputs(resolver, db_factory, tmp_path):
    from dataclasses import replace

    import httpx

    from lib.backends.video_backends.vidu import Vidu2VideoBackend
    from lib.generation.video_request_facts import VideoRequestCostFacts
    from server.services.admission.cost_estimation import quote_video_request
    from tests.factories import image_bytes
    from tests.http_capture import capture_http, only_request, request_json

    facts = await read_video_request_facts(
        {"video_provider_i2v": VIDU2}, route="storyboard", generation_type="i2v", resolver=resolver, uses_end_frame=True
    )
    assert isinstance(facts, VideoRequestFacts)
    cost = VideoRequestCostFacts(facts, 7, "16:9", has_first_frame=True)
    quote = await quote_video_request(cost, db_factory)
    assert quote is not None
    assert quote.request_duration_seconds == 8
    request = cost.provider_request()
    frame = tmp_path / "frame.png"
    frame.write_bytes(image_bytes())
    real_request = replace(
        request,
        output_path=tmp_path / "clip.mp4",
        start_image=frame if request.start_image else None,
        end_image=frame if request.end_image else None,
    )
    backend = Vidu2VideoBackend(api_key="test", model="vidu2.0")
    with capture_http(assert_all_called=True) as http:
        submit = http.post(url__regex=r".*/start-end2video$").mock(
            return_value=httpx.Response(200, json={"task_id": "vidu-job"})
        )
        http.get(url__regex=r".*/tasks/vidu-job/creations$").mock(
            return_value=httpx.Response(
                200, json={"state": "success", "creations": [{"url": "https://cdn.test/video.mp4"}]}
            )
        )
        http.get("https://cdn.test/video.mp4").mock(return_value=httpx.Response(200, content=b"local-video"))
        result = await backend.generate(real_request)
    payload = request_json(only_request(submit))
    assert payload["duration"] == quote.request_duration_seconds
    assert len(payload["images"]) == 2
    assert result.duration_seconds == quote.request_duration_seconds


@pytest.mark.parametrize(
    ("resolution", "tier", "mode", "supports_tail"),
    [
        ("1080p", "std", "pro", True),
        ("720p", "pro", "pro", True),
        ("4k", "pro", "4k", False),
        ("720p", "std", "std", False),
    ],
)
async def test_kling_request_tier_matches_quote_and_payload(resolver, resolution, tier, mode, supports_tail, tmp_path):
    from dataclasses import replace

    import httpx

    from lib.backends.video_backends.kling import KLING_BACKENDS
    from lib.generation.video_request_facts import VideoRequestCostFacts
    from lib.video_runtime.builtin import builtin_video_metered_usage
    from tests.factories import image_bytes
    from tests.http_capture import capture_http, only_request, request_json

    model = "relay-kling"
    catalog.install_user_added_models(
        [
            catalog.UserAddedModel(
                "kling",
                model,
                "video",
                endpoint="kling-video-pro-audio",
                capability_overrides={"last_frame": True, "durations": [5]},
            )
        ]
    )
    try:
        project = _with_resolution({"video_provider_i2v": f"kling/{model}"}, f"kling/{model}", resolution)

        async def evaluate(has_tail):
            return await read_video_request_facts(
                project,
                route="storyboard",
                generation_type="i2v",
                resolver=resolver,
                uses_end_frame=has_tail,
                service_tier=tier,
            )

        with_tail = await evaluate(True)
        if not supports_tail:
            assert isinstance(with_tail, VideoRequestFactsFailure)
            assert with_tail.code == "video_last_frame_unsupported"
        facts = with_tail if supports_tail else await evaluate(False)
        assert isinstance(facts, VideoRequestFacts)
        request = VideoRequestCostFacts(facts, 5, "16:9", has_first_frame=True).provider_request()
        backend = KLING_BACKENDS["kling-video-pro-audio"](auth_mode="bearer", api_key="test", model=model)
        frame = tmp_path / "frame.png"
        frame.write_bytes(image_bytes())
        real_request = replace(
            request,
            output_path=tmp_path / "clip.mp4",
            start_image=frame,
            end_image=frame if request.end_image else None,
        )
        with capture_http(assert_all_called=True) as http:
            submit = http.post(url__regex=r".*/videos/image2video$").mock(
                return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "kling-job"}})
            )
            http.get(url__regex=r".*/videos/image2video/kling-job$").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "task_status": "succeed",
                            "task_result": {"videos": [{"url": "https://cdn.test/video.mp4"}]},
                        },
                    },
                )
            )
            http.get("https://cdn.test/video.mp4").mock(return_value=httpx.Response(200, content=b"local-video"))
            await backend.generate(real_request)
        payload = request_json(only_request(submit))
        assert payload["mode"] == mode
        assert ("image_tail" in payload) is supports_tail
        assert (payload.get("sound") == "on") is supports_tail
        assert builtin_video_metered_usage("kling", model, request).dimensions["audio"] is supports_tail
    finally:
        catalog.install_user_added_models(())


@pytest.mark.parametrize("tail", [False, True])
async def test_kling_rejects_reference_images_with_frames_before_quoting(resolver, tail, tmp_path):
    from arcreel_market_core.video_backend_contract import VideoCapabilityError, VideoGenerationRequest
    from lib.backends.video_backends.kling import KLING_BACKENDS
    from tests.http_capture import capture_http

    model = "kling-v3-omni"
    facts = await read_video_request_facts(
        {"video_provider_r2v": f"kling/{model}"},
        route="reference_video" if tail else "storyboard",
        generation_type="r2v",
        uses_end_frame=tail,
        resolver=resolver,
    )
    assert isinstance(facts, VideoRequestFactsFailure)
    assert facts.code == "video_reference_images_with_frames_unsupported"
    frame = tmp_path / "frame.png"
    request = VideoGenerationRequest(
        prompt="p",
        output_path=tmp_path / "clip.mp4",
        reference_images=[frame],
        start_image=None if tail else frame,
        end_image=frame if tail else None,
    )
    backend = KLING_BACKENDS["kling-video-audio"](auth_mode="bearer", api_key="test", model=model)
    with capture_http(assert_all_called=True) as http, pytest.raises(VideoCapabilityError) as exc:
        await backend.generate(request)
    assert exc.value.code == facts.code
    assert len(http.calls) == 0
