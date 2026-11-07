"""Video-request fact evaluation with real ConfigResolver and memory DB; assert facts/failures by project × route × bucket × identity source."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import update

from arcreel_catalog_core import DurationConstraints
from lib.catalog.query import catalog_data, factory_snapshot
from lib.config.resolver import ConfigResolver
from lib.custom_provider import make_provider_id
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    ExecutionVideoIdentity,
    ResolutionOverride,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from tests.catalog_support import install_catalog, with_entry_changes, with_entry_facts
from tests.factories import seed_endpoint_fixed_video_model

VEO_PROVIDER, VEO_MODEL = "gemini-aistudio", "veo-3.1-generate-preview"
VEO = f"{VEO_PROVIDER}/{VEO_MODEL}"
VIDU2 = "vidu/vidu2.0"


@pytest.fixture
def resolver(db_factory, monkeypatch) -> ConfigResolver:
    # Request-fact cases isolate tiers/capabilities from model shutdown dates.
    install_catalog(
        monkeypatch,
        with_entry_changes(factory_snapshot(), VEO_PROVIDER, VEO_MODEL, discontinued=False, shutdown_date=None),
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
                    endpoint="newapi-video",
                    **row,
                )
            )
        await session.commit()
        return make_provider_id(provider.id)


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
@pytest.mark.parametrize("execution", [False, True], ids=["configured", "execution"])
async def test_database_query_failure_returns_unavailable_facts(resolver, db_factory, db_engine, route, execution):
    provider_id = await _seed_custom_video_models(
        db_factory, {"model_id": "m", "capability_overrides": {"durations": [5]}, "is_default": True}
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


async def test_custom_model_without_durations_falls_back_to_name_preset(resolver, db_factory):
    """Unmatched entries without row durations use name-inferred runtime presets; no matching family falls back to [4,8]."""
    provider_id = await _seed_custom_video_models(db_factory, {"model_id": "m", "is_default": True})

    facts = await _read(resolver, {"video_provider_i2v": f"{provider_id}/m"})

    assert isinstance(facts, VideoRequestFacts)
    assert facts.supported_durations == (4, 8)


async def test_custom_model_matched_to_entry_takes_entry_durations_and_constraints(resolver, db_factory):
    """Name-matched custom models use entry durations and coupling constraints (Veo at 1080p narrows to eight seconds)."""
    provider_id = await _seed_custom_video_models(
        db_factory, {"model_id": "vendor/Veo-3.1-Generate-001", "resolution": "1080p", "is_default": True}
    )

    facts = await _read(resolver, {"video_provider_i2v": f"{provider_id}/vendor/Veo-3.1-Generate-001"})

    assert isinstance(facts, VideoRequestFacts)
    assert facts.supported_durations == (4, 6, 8)
    assert facts.allowed_durations == (8,)
    assert facts.excluded_durations == ((4, "resolution"), (6, "resolution"))


async def test_custom_model_constraint_override_replaces_entry_constraints(resolver, db_factory):
    """Capability overrides replace the whole duration-constraint dimension; compare resolution keys lowercase and replace reference-image constraints with it."""
    provider_id = await _seed_custom_video_models(
        db_factory,
        {
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
    )


@pytest.mark.parametrize("route", ["storyboard", "reference_video"])
async def test_unavailable_execution_identity_fails_instead_of_using_the_default_model(resolver, db_factory, route):
    provider_id = await _seed_custom_video_models(
        db_factory,
        {"model_id": "m-dead", "is_enabled": False, "capability_overrides": {"durations": [5]}},
        {"model_id": "m-live", "is_default": True, "capability_overrides": {"durations": [8]}},
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
        # Commit configuration changes after identity/bucket session closes; next capability read sees them.
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
    """Disabled configured identities read as dangling; valid caller-supplied execution identities evaluate independently."""
    provider_id = await _seed_custom_video_models(
        db_factory,
        {"model_id": "m-dead", "is_enabled": False, "capability_overrides": {"durations": [5]}},
        {"model_id": "m-live", "is_default": True, "resolution": "540p", "capability_overrides": {"durations": [4, 6]}},
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
    """Read paths (preflight/quotes) and execution return identical tiers and request resolution for one configuration."""
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
    """Resolution overrides replace saved project tiers as request resolution, leaving other evaluation unchanged."""
    project = _with_resolution({f"video_provider_{generation_type}": VEO}, VEO, "1080p")

    preview = await evaluate_video_request_facts(
        project,
        route="storyboard" if generation_type == "i2v" else "reference_video",
        generation_type=generation_type,
        identity=CONFIGURED_VIDEO_IDENTITY,
        resolver=resolver,
        resolution_override=ResolutionOverride(override),
    )

    assert isinstance(preview, VideoRequestFacts)
    assert (preview.provider_id, preview.model_id) == (VEO_PROVIDER, VEO_MODEL)
    assert preview.resolution == resolution
    assert preview.allowed_durations == allowed
    assert preview.excluded_durations == excluded


async def test_resolution_override_leaves_read_and_execution_evaluations_on_the_saved_resolution(resolver):
    """After previewing unsaved resolution, reads without overrides and execution still use saved tiers."""
    project = _with_resolution({"video_provider_i2v": VEO}, VEO, "1080p")
    await evaluate_video_request_facts(
        project,
        route="storyboard",
        generation_type="i2v",
        identity=CONFIGURED_VIDEO_IDENTITY,
        resolver=resolver,
        resolution_override=ResolutionOverride("720p"),
    )

    read = await _read(resolver, project)
    executed = await evaluate_video_request_facts(
        project,
        route="storyboard",
        generation_type="i2v",
        identity=ExecutionVideoIdentity(VEO_PROVIDER, VEO_MODEL),
        resolver=resolver,
    )

    assert isinstance(read, VideoRequestFacts)
    assert (read.resolution, read.allowed_durations) == ("1080p", (8,))
    assert executed == read
    assert project["model_settings"] == {VEO: {"resolution": "1080p"}}


async def test_auto_resolution_override_falls_back_to_the_custom_model_default(resolver, db_factory):
    """Preview automatic means no saved project tier; custom models still use default model tiers, matching saved evaluation."""
    provider_id = await _seed_custom_video_models(
        db_factory,
        {"model_id": "m", "is_default": True, "resolution": "540p", "capability_overrides": {"durations": [4, 6]}},
    )
    pair = f"{provider_id}/m"

    preview = await evaluate_video_request_facts(
        _with_resolution({"video_provider_i2v": pair}, pair, "1080p"),
        route="storyboard",
        generation_type="i2v",
        identity=CONFIGURED_VIDEO_IDENTITY,
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
    """One evaluation returns reference limits, imageless capability bits and reference-audio shape for direct request assembly."""
    from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

    facts = await _read(resolver, {"video_provider_r2v": VEO}, route="reference_video", generation_type="r2v")

    declared = builtin_video_capabilities_for_model(VEO_PROVIDER, VEO_MODEL)
    assert isinstance(facts, VideoRequestFacts)
    assert facts.max_reference_images == declared.max_reference_images
    assert (facts.text_to_video, facts.first_frame) == (declared.text_to_video, declared.first_frame)
    assert facts.max_reference_audio_count == declared.max_reference_audio_count
    assert facts.reference_audio_per_image is declared.reference_audio_per_image
