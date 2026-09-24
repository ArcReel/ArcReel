"""视频请求事实求值接口：真实 ConfigResolver + 内存测试库，按「项目 × 路线 × 桶 × 身份来源」断言事实或失败。"""

from __future__ import annotations

import dataclasses
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import update

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.custom_provider import make_provider_id
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    ExecutionVideoIdentity,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from tests.factories import comfyui_endpoint_definition

VEO_PROVIDER, VEO_MODEL = "gemini-aistudio", "veo-3.1-generate-preview"
VEO = f"{VEO_PROVIDER}/{VEO_MODEL}"
VIDU2 = "vidu/vidu2.0"


@pytest.fixture
def resolver(db_factory) -> ConfigResolver:
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
                    endpoint="newapi-video",
                    **row,
                )
            )
        await session.commit()
        return make_provider_id(provider.id)


async def _seed_comfyui_video_model(db_factory) -> str:
    async with db_factory() as session:
        definition = comfyui_endpoint_definition()
        definition["bindings"]["start_image"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]
        endpoint = CustomEndpoint(
            definition=definition,
            kind="comfyui",
            schema_version="1.0.0",
            media_type="video",
            display_name="ComfyUI",
        )
        session.add(endpoint)
        provider = CustomProvider(
            display_name="Comfy", discovery_format="comfyui", base_url="http://comfy.test:8188", api_key=""
        )
        session.add(provider)
        await session.flush()
        session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id="wan-workflow",
                display_name="Workflow",
                endpoint=f"ce-{endpoint.id}",
                supported_durations="[]",
                is_default=True,
                is_enabled=True,
            )
        )
        await session.commit()
        return f"{make_provider_id(provider.id)}/wan-workflow"


async def _read(resolver, project: dict, *, route="storyboard", generation_type="i2v"):
    return await evaluate_video_request_facts(
        project,
        route=route,
        generation_type=generation_type,
        identity=CONFIGURED_VIDEO_IDENTITY,
        resolver=resolver,
    )


@pytest.mark.parametrize(
    ("project", "route", "generation_type", "resolution", "allowed", "excluded"),
    [
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
    pair = await _seed_comfyui_video_model(db_factory)

    facts = await _read(resolver, {"video_provider_i2v": pair})

    assert isinstance(facts, VideoRequestFacts)
    assert facts.duration_endpoint_fixed is True
    assert facts.supported_durations == ()
    assert facts.allowed_durations == ()
    assert facts.excluded_durations == ()


async def test_unresolvable_bucket_fails_with_the_capability_gate_code(resolver):
    facts = await _read(resolver, {"video_provider_i2v": f"{VEO_PROVIDER}/retired-model"})

    assert facts == VideoRequestFactsFailure(
        "video_capability_reference_unavailable",
        (("provider", VEO_PROVIDER), ("model", "retired-model")),
    )


async def test_no_configured_video_model_fails_as_capability_unavailable(resolver):
    facts = await _read(resolver, {})

    assert isinstance(facts, VideoRequestFactsFailure)
    assert facts.code == "video_capability_unavailable"
    assert facts.parameters() == {"capability": "i2v"}
    assert facts.action == "configure_video_model"


@pytest.mark.parametrize("route", ["storyboard", "reference_video"])
@pytest.mark.parametrize("execution", [False, True], ids=["configured", "execution"])
async def test_database_query_failure_returns_unavailable_facts(resolver, db_factory, db_engine, route, execution):
    provider_id = await _seed_custom_video_models(
        db_factory, {"model_id": "m", "supported_durations": "[5]", "is_default": True}
    )
    async with db_engine.begin() as connection:
        await connection.run_sync(CustomProviderModel.__table__.drop)

    result = await evaluate_video_request_facts(
        {"video_provider_i2v": f"{provider_id}/m"},
        route=route,
        generation_type="i2v",
        identity=ExecutionVideoIdentity(provider_id, "m") if execution else CONFIGURED_VIDEO_IDENTITY,
        resolver=resolver,
    )

    params = (("capability", "i2v"),)
    if execution:
        params += (("provider", provider_id), ("model", "m"))
    prefix = "video" if route == "storyboard" else "reference"
    assert result == VideoRequestFactsFailure(f"{prefix}_capability_unavailable", params)


@pytest.mark.parametrize(
    ("route", "supported_durations", "code"),
    [
        pytest.param("storyboard", "[]", "video_supported_durations_missing", id="storyboard-missing"),
        pytest.param("storyboard", "not-json", "video_supported_durations_invalid", id="storyboard-unparseable"),
        pytest.param("storyboard", "{}", "video_supported_durations_invalid", id="storyboard-not-array"),
        pytest.param("storyboard", '["5"]', "video_supported_durations_invalid", id="storyboard-string-tier"),
        pytest.param("storyboard", "[4.5]", "video_supported_durations_invalid", id="storyboard-fractional-tier"),
        pytest.param("storyboard", "[0, 5]", "video_supported_durations_invalid", id="storyboard-non-positive"),
        pytest.param("reference_video", "[]", "reference_supported_durations_missing", id="reference-missing"),
        pytest.param("reference_video", "[0, 5]", "reference_supported_durations_invalid", id="reference-invalid"),
    ],
)
async def test_tier_declaration_failures_use_the_route_code_family(
    resolver, db_factory, route, supported_durations, code
):
    provider_id = await _seed_custom_video_models(
        db_factory, {"model_id": "m", "supported_durations": supported_durations, "is_default": True}
    )

    facts = await _read(resolver, {"video_provider_i2v": f"{provider_id}/m"}, route=route)

    assert facts == VideoRequestFactsFailure(code, (("provider", provider_id), ("model", "m")))


async def test_constraints_narrowing_to_nothing_fail_instead_of_falling_back(resolver, monkeypatch):
    models = PROVIDER_REGISTRY[VEO_PROVIDER].models
    monkeypatch.setitem(
        models, VEO_MODEL, dataclasses.replace(models[VEO_MODEL], duration_resolution_constraints={"1080p": [10]})
    )

    facts = await _read(resolver, _with_resolution({"video_provider_i2v": VEO}, VEO, "1080p"))

    assert facts == VideoRequestFactsFailure(
        "video_supported_durations_incompatible",
        (("provider", VEO_PROVIDER), ("model", VEO_MODEL), ("resolution", "1080p"), ("capability", "i2v")),
    )


@pytest.mark.parametrize("route", ["storyboard", "reference_video"])
async def test_unavailable_execution_identity_fails_instead_of_using_the_default_model(resolver, db_factory, route):
    provider_id = await _seed_custom_video_models(
        db_factory,
        {"model_id": "m-dead", "is_enabled": False, "supported_durations": "[5]"},
        {"model_id": "m-live", "is_default": True, "supported_durations": "[8]"},
    )

    result = await evaluate_video_request_facts(
        {},
        route=route,
        generation_type="i2v",
        identity=ExecutionVideoIdentity(provider_id, "m-dead"),
        resolver=resolver,
    )

    prefix = "video" if route == "storyboard" else "reference"
    assert result == VideoRequestFactsFailure(
        f"{prefix}_capability_unavailable",
        (("capability", "i2v"), ("provider", provider_id), ("model", "m-dead")),
    )


@pytest.mark.parametrize(("route", "generation_type"), [("storyboard", "i2v"), ("reference_video", "r2v")])
async def test_configured_identity_change_after_bucket_validation_fails(db_factory, route, generation_type):
    provider_id = await _seed_custom_video_models(
        db_factory,
        {
            "model_id": "m-selected",
            "supported_durations": "[5]",
            "capability_overrides": {"first_frame": True, "max_reference_images": 4},
        },
        {
            "model_id": "m-default",
            "is_default": True,
            "supported_durations": "[8]",
            "capability_overrides": {"first_frame": False, "max_reference_images": 0},
        },
    )
    disabled = []

    @asynccontextmanager
    async def changing_sessions():
        async with db_factory() as session:
            yield session
        # 在身份与桶校验完成的 session 关闭后提交配置变更，下一次能力读取会看到它。
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

    prefix = "video" if route == "storyboard" else "reference"
    assert result == VideoRequestFactsFailure(
        f"{prefix}_capability_unavailable",
        (("capability", generation_type), ("provider", provider_id), ("model", "m-selected")),
    )


async def test_execution_identity_follows_the_backend_when_it_diverges_from_configuration(resolver, db_factory):
    """配置身份已禁用时读侧报悬空；调用方给定的有效执行身份独立求值。"""
    provider_id = await _seed_custom_video_models(
        db_factory,
        {"model_id": "m-dead", "is_enabled": False, "supported_durations": json.dumps([5])},
        {"model_id": "m-live", "is_default": True, "resolution": "540p", "supported_durations": json.dumps([4, 6])},
    )
    project = {"video_provider_i2v": f"{provider_id}/m-dead"}

    read = await _read(resolver, project)
    executed = await evaluate_video_request_facts(
        project,
        route="storyboard",
        generation_type="i2v",
        identity=ExecutionVideoIdentity(provider_id, "m-live"),
        resolver=resolver,
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

    executed = await evaluate_video_request_facts(
        project,
        route=route,
        generation_type=generation_type,
        identity=ExecutionVideoIdentity(read.provider_id, read.model_id),
        resolver=resolver,
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
        executed = await evaluate_video_request_facts(
            project,
            route="reference_video",
            generation_type=bucket,
            identity=ExecutionVideoIdentity(read.provider_id, read.model_id),
            resolver=resolver,
        )
        assert executed == read


@pytest.mark.parametrize(
    ("pair", "generation_type", "requested", "expected", "voice_consistency"),
    [
        (VEO, "i2v", False, (False, True, True, False), "soft"),
        ("dashscope/wan2.7-i2v", "i2v", False, (False, False, True, False), "soft"),
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
