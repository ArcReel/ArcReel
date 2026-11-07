"""Public resolve_generation_context tests: declared/skipped lanes, fail-loud properties, actual-identity resolution and video-request facts, atomic failure, backend caching and invalidation.

ADR 0049: real in-memory DB + tmp_path ProjectManager + fake backend replacing only assemble_backend. Do not mock ConfigResolver / ProjectManager or assert private attributes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import update

from arcreel_market_core.video_backend_contract import VideoCapabilities
from lib.backends.audio_backends.base import VoiceOption
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model, get_provider_spec
from lib.backends.image_backends.base import BOTH_IMAGE_CAPABILITIES, ImageCapability
from lib.catalog import query as catalog
from lib.catalog.errors import ModelDiscontinuedError, ModelNotInCatalogError
from lib.catalog.query import factory_snapshot
from lib.config.resolver import ConfigResolver, ProviderModel, VideoBucketCapabilityError, VoiceConsistency
from lib.custom_provider import make_provider_id
from lib.db.models.config import SystemSetting
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.generation.media_generator import MediaGenerator
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from lib.project.project_manager import ProjectManager
from server.services.tasks import generation_context
from server.services.tasks.generation_context import (
    AudioLaneRequest,
    GenerationContext,
    ImageLaneRequest,
    ImageLaneResult,
    VideoLaneRequest,
    VideoLaneResult,
    resolve_generation_context,
)
from tests.catalog_support import install_catalog, set_catalog_today, with_entry_changes
from tests.factories import make_video_request_facts
from tests.fakes import bind_session_factory, use_backend_assembler, use_project_manager


def _registry_video_model(provider_id: str) -> str:
    """Select any video model ID for this provider from catalog, avoiding hardcoded provider data."""
    return next(mid for mid, mi in catalog.list_models(provider_id).items() if mi.media_type == "video")


def _backend_video_caps(provider_id: str, model_id: str) -> VideoCapabilities:
    """Only backends declare video capability bits and reference-image limits; catalog model information lacks these fields."""
    return builtin_video_capabilities_for_model(provider_id, model_id)


@dataclass
class _FakeBackend:
    name: str
    model: str
    voices: list = field(default_factory=list)
    max_reference_images: int | None = None
    capabilities: frozenset[ImageCapability] = BOTH_IMAGE_CAPABILITIES

    async def reference_image_limit(self) -> int | None:
        """Image backend protocol includes reading reference-image limit once during image-lane resolution."""
        return self.max_reference_images

    def list_voices(self):
        """Audio backend protocol includes snapshotting voice catalog during audio-lane resolution."""
        return self.voices


@pytest.fixture
async def patched_session_factory(db_factory, monkeypatch):
    """Real in-memory DB: create all ORM tables and point lib.db.async_session_factory at it."""
    bind_session_factory(monkeypatch, db_factory)
    return db_factory


@pytest.fixture
def project_env(monkeypatch, tmp_path: Path):
    """Real ProjectManager and existing project directory under tmp_path."""
    pm = ProjectManager(tmp_path)
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    use_project_manager(monkeypatch, pm)
    return pm


@pytest.fixture(autouse=True)
def clean_backend_cache():
    """Reset backend-instance cache and per-key locks before/after each case (event-loop binding explained in ``reset_backend_cache_for_tests``)."""
    generation_context.reset_backend_cache_for_tests()
    yield
    generation_context.reset_backend_cache_for_tests()


@pytest.fixture
def fake_assemble(monkeypatch):
    """Replace backend construction; echo request identity by default and record each construction."""
    calls: list[tuple[str, str, str | None]] = []

    async def _assemble(
        *,
        provider_id,
        media_type,
        model_id,
        resolver,
        rate_limiter=None,
        generation_type=None,
        submitted_job=False,
        builtin_endpoint=None,
    ):
        calls.append((provider_id, media_type, model_id))
        return _FakeBackend(name=provider_id, model=model_id or "default-model")

    use_backend_assembler(monkeypatch, _assemble)
    return calls


async def _seed_custom_video_provider(patched_session_factory) -> str:
    """Seed a custom provider with disabled target and live default model, including resolution/duration tiers."""
    async with patched_session_factory() as session:
        provider = CustomProvider(
            display_name="Prov",
            discovery_format="openai",
            base_url="https://api.example.com",
            api_key="k",
        )
        session.add(provider)
        await session.flush()
        session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id="m-dead",
                display_name="Dead",
                endpoint="newapi-video",
                is_default=False,
                is_enabled=False,
            )
        )
        session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id="m-live",
                display_name="Live",
                endpoint="newapi-video",
                is_default=True,
                is_enabled=True,
                resolution="540p",
                capability_overrides={"durations": [4, 6, 8]},
            )
        )
        await session.commit()
        return make_provider_id(provider.id)


SEEDREAM_LITE = "doubao-seedream-5-0-lite-260128"


async def _seed_custom_image_provider(patched_session_factory, models: list[dict]) -> str:
    """Seed a custom provider with every model enabled, matching entries by name."""
    async with patched_session_factory() as session:
        provider = CustomProvider(
            display_name="Relay", discovery_format="openai", base_url="https://relay.test/v1", api_key="k"
        )
        session.add(provider)
        await session.flush()
        for model in models:
            session.add(
                CustomProviderModel(provider_id=provider.id, display_name=model["model_id"], is_enabled=True, **model)
            )
        await session.commit()
        return make_provider_id(provider.id)


class TestLaneDeclaration:
    async def test_only_declared_lanes_are_constructed(self, patched_session_factory, project_env, fake_assemble):
        """Image lane alone neither resolves nor constructs video/audio; absent video-provider configuration does not affect image tasks."""
        project = {"image_provider_t2i": "ark/img-model-x"}
        ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())

        assert [media for _, media, _ in fake_assemble] == ["image"]
        assert ctx.image.provider_model == ProviderModel("ark", "img-model-x")
        assert ctx.image.backend_name == "ark"
        assert ctx.image.backend_model == "img-model-x"
        with pytest.raises(RuntimeError, match="video lane 未声明"):
            _ = ctx.video
        with pytest.raises(RuntimeError, match="audio lane 未声明"):
            _ = ctx.audio

    async def test_no_lane_declared_still_returns_generator(self, patched_session_factory, project_env, fake_assemble):
        ctx = await resolve_generation_context("demo", None, project={})
        assert isinstance(ctx.generator, MediaGenerator)
        assert fake_assemble == []
        with pytest.raises(RuntimeError, match="image lane 未声明"):
            _ = ctx.image

    async def test_preloaded_project_path_avoids_the_default_executor(
        self,
        patched_session_factory,
        project_env,
        fake_assemble,
        monkeypatch,
    ):
        project_path = project_env.get_project_path("demo")

        async def _unexpected_to_thread(*_args, **_kwargs):
            pytest.fail("a preloaded project path must not re-enter the default executor")

        monkeypatch.setattr(generation_context.asyncio, "to_thread", _unexpected_to_thread)

        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"audio_backend": "dashscope/tts-model-x"},
            project_path=project_path,
            audio=AudioLaneRequest(),
        )

        assert ctx.generator.project_path == project_path

    async def test_i2i_capability_selects_i2i_slot(self, patched_session_factory, project_env, fake_assemble):
        project = {
            "image_provider_t2i": "ark/img-t2i",
            "image_provider_i2i": "ark/img-i2i",
        }
        ctx = await resolve_generation_context(
            "demo", None, project=project, image=ImageLaneRequest(generation_type="i2i")
        )
        assert ctx.image.provider_model == ProviderModel("ark", "img-i2i")

    async def test_all_three_lanes(self, patched_session_factory, project_env, fake_assemble):
        video_model = _registry_video_model("ark")
        project = {
            "image_provider_t2i": "ark/img-model-x",
            "video_backend": f"ark/{video_model}",
            "audio_backend": "dashscope/tts-model-x",
        }
        ctx = await resolve_generation_context(
            "demo",
            None,
            project=project,
            image=ImageLaneRequest(),
            video=VideoLaneRequest(),
            audio=AudioLaneRequest(),
        )
        assert sorted(media for _, media, _ in fake_assemble) == ["audio", "image", "video"]
        assert ctx.video.provider_model == ProviderModel("ark", video_model)
        assert ctx.audio.provider_model == ProviderModel("dashscope", "tts-model-x")


class TestVideoLane:
    async def test_capabilities_come_from_request_facts_without_a_fallback_resolution(
        self, patched_session_factory, project_env, fake_assemble
    ):
        video_model = _registry_video_model("ark")
        expected = catalog.get_model("ark", video_model)
        assert expected is not None
        ctx = await resolve_generation_context(
            "demo", None, project={"video_backend": f"ark/{video_model}"}, video=VideoLaneRequest(route="storyboard")
        )

        facts = ctx.video.request_facts
        assert isinstance(facts, VideoRequestFacts)
        assert facts.supported_durations == tuple(expected.supported_durations or [])
        assert facts.max_reference_images == _backend_video_caps("ark", video_model).max_reference_images
        assert isinstance(facts.generate_audio, bool)
        # Unset resolution is not sent: neither lane nor facts supplies a fallback tier.
        assert ctx.video.resolution is None
        assert facts.resolution is None

    async def test_resolution_from_model_settings(self, patched_session_factory, project_env, fake_assemble):
        video_model = _registry_video_model("ark")
        project = {
            "video_backend": f"ark/{video_model}",
            "model_settings": {f"ark/{video_model}": {"resolution": "480p"}},
        }
        ctx = await resolve_generation_context(
            "demo", None, project=project, video=VideoLaneRequest(route="storyboard")
        )
        assert ctx.video.resolution == "480p"
        assert isinstance(ctx.video.request_facts, VideoRequestFacts)
        assert ctx.video.request_facts.resolution == "480p"

    async def test_lane_without_a_route_evaluates_no_capabilities(
        self, patched_session_factory, project_env, monkeypatch
    ):
        """Bucket-neutral lanes without declared routes, such as resume, deliver identity only: construct even out-of-registry models without capability evaluation."""

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            return _FakeBackend(name=provider_id, model="mystery-model")

        use_backend_assembler(monkeypatch, _assemble)
        video_model = _registry_video_model("ark")
        ctx = await resolve_generation_context(
            "demo", None, project={"video_backend": f"ark/{video_model}"}, video=VideoLaneRequest()
        )
        assert ctx.video.backend_model == "mystery-model"
        assert ctx.video.request_facts is None

    async def test_requested_generate_audio_follows_project_override(
        self, patched_session_factory, project_env, fake_assemble
    ):
        """Project.json episode-mute overrides enter the lane; orchestration decides reference-audio assembly from them."""
        video_model = _registry_video_model("ark")
        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"video_backend": f"ark/{video_model}", "video_generate_audio": False},
            video=VideoLaneRequest(),
        )
        assert ctx.video.requested_generate_audio is False

    async def test_capability_failure_does_not_supply_audio_fallback(
        self, patched_session_factory, project_env, monkeypatch
    ):
        """Execution blocks request-fact failures for declared routes; audio switches have no independent degraded value."""

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            return _FakeBackend(name=provider_id, model="mystery-model")

        use_backend_assembler(monkeypatch, _assemble)
        video_model = _registry_video_model("ark")
        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"video_backend": f"ark/{video_model}", "video_generate_audio": False},
            video=VideoLaneRequest(route="storyboard"),
        )
        assert isinstance(ctx.video.request_facts, VideoRequestFactsFailure)
        with pytest.raises(VideoRequestFactsError):
            _ = ctx.video.requested_generate_audio

    async def test_payload_overrides_project(self, patched_session_factory, project_env, fake_assemble):
        """payload > project: explicit request identities, such as checkpoint replay, determine actual resolution."""
        ark_model = _registry_video_model("ark")
        grok_model = _registry_video_model("grok")
        ctx = await resolve_generation_context(
            "demo",
            {"video_provider_i2v": f"grok/{grok_model}"},
            project={"video_backend": f"ark/{ark_model}"},
            video=VideoLaneRequest(),
        )
        assert ctx.video.provider_model == ProviderModel("grok", grok_model)

    async def test_resume_guard_follows_the_endpoint_the_backend_was_built_with(
        self, patched_session_factory, project_env, monkeypatch
    ):
        """If users change endpoints after construction, the frozen guard still identifies the actual submission endpoint; resume never admits by the new endpoint."""
        model_id = "ep-guard-race"
        catalog.install_user_added_models(
            (catalog.UserAddedModel("ark", model_id, "video", endpoint="ark-seedance-2"),)
        )
        built_with: list[str | None] = []

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            built_with.append(builtin_endpoint)
            # Between construction and guard persistence, settings rebind this model to endpoint 2.5.
            catalog.install_user_added_models(
                (catalog.UserAddedModel("ark", model_id, "video", endpoint="ark-seedance-2-5"),)
            )
            return _FakeBackend(name=provider_id, model=model_id)

        use_backend_assembler(monkeypatch, _assemble)
        try:
            ctx = await resolve_generation_context(
                "demo", None, project={"video_backend": f"ark/{model_id}"}, video=VideoLaneRequest()
            )
        finally:
            catalog.install_user_added_models(())

        assert built_with == ["ark-seedance-2"]
        assert ctx.video.endpoint == "builtin:ark-seedance-2"

    @pytest.mark.parametrize(
        ("endpoint", "changed_endpoint", "has_audio"),
        [
            ("kling-video-pro-silent", "kling-video-audio", False),
            ("kling-video-audio", "kling-video-pro-silent", True),
        ],
    )
    async def test_request_facts_use_the_endpoint_the_backend_was_built_with(
        self, patched_session_factory, project_env, monkeypatch, endpoint, changed_endpoint, has_audio
    ):
        """If endpoints change during construction, facts and read-side capabilities use the endpoint actually constructed this time."""
        model_id = "endpoint-race-video"

        def install_endpoint(value):
            catalog.install_user_added_models(
                (catalog.UserAddedModel("kling", model_id, "video", fallback_durations=(5,), endpoint=value),)
            )

        install_endpoint(endpoint)
        backends = []

        async def _assemble(*, provider_id, media_type, model_id, builtin_endpoint, **kwargs):
            backend = get_provider_spec(provider_id, media_type, builtin_endpoint).build_backend(
                LoadedConfig(
                    credentials={"access_key": "test", "secret_key": "test"}, provider_meta=None, rate_limiter=None
                ),
                model_id,
            )
            backends.append(backend)
            install_endpoint(changed_endpoint)
            return backend

        use_backend_assembler(monkeypatch, _assemble)
        project = {"video_backend": f"kling/{model_id}", "video_generate_audio": True}
        try:
            ctx = await resolve_generation_context(
                "demo", None, project=project, video=VideoLaneRequest(route="storyboard")
            )
            facts = ctx.video.request_facts
            assert isinstance(facts, VideoRequestFacts)
            assert facts.has_audio_track is has_audio
            caps = await ConfigResolver(patched_session_factory).video_capabilities_for_model(
                "kling", model_id, project, generation_type="i2v", builtin_endpoint=endpoint
            )
            current_caps = await ConfigResolver(patched_session_factory).video_capabilities_for_model(
                "kling", model_id, project, generation_type="i2v"
            )
        finally:
            catalog.install_user_added_models(())

        assert ctx.video.endpoint == f"builtin:{endpoint}"
        assert backends[0].video_capabilities.audio_track.value == ("controllable" if has_audio else "always_off")
        assert facts.audio_switch_controllable is has_audio
        assert facts.generate_audio is has_audio
        assert caps["generate_audio"] is has_audio
        assert facts.voice_consistency == caps["voice_consistency"] == ("soft" if has_audio else "none")
        assert current_caps["generate_audio"] is not has_audio

    async def test_submitted_job_requires_a_pinned_identity(self, patched_session_factory, project_env, fake_assemble):
        """Resume's discontinued-model exemption applies only to submission-locked identities. Without one, fail before current-config resolution or backend construction."""
        ark_model = _registry_video_model("ark")
        with pytest.raises(ValueError, match="锁定提交时的执行身份"):
            await resolve_generation_context(
                "demo",
                None,
                project={"video_provider_i2v": f"ark/{ark_model}"},
                video=VideoLaneRequest(generation_type="i2v", submitted_job=True),
            )
        assert fake_assemble == []


class TestActualIdentityQueries:
    async def test_custom_model_fallback_queries_by_actual_model(
        self, patched_session_factory, project_env, monkeypatch
    ):
        """Bucket-neutral lanes without declared routes resolve resolution by enabled default model."""
        provider_id = await _seed_custom_video_provider(patched_session_factory)

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            # Simulate load_custom_backend fallback: requested m-dead constructs enabled default m-live.
            return _FakeBackend(name=provider_id, model="m-live")

        use_backend_assembler(monkeypatch, _assemble)
        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"video_backend": f"{provider_id}/m-dead"},
            video=VideoLaneRequest(),
        )

        # Bucket-neutral identity resolution converges on enabled default model; construction uses that identity directly.
        assert ctx.video.provider_model == ProviderModel(provider_id, "m-live")
        assert ctx.video.backend_model == "m-live"
        # resolution uses m-live actual-identity DB default, never a missed m-dead lookup by intended identity.
        assert ctx.video.resolution == "540p"
        assert ctx.video.request_facts is None


class TestVideoRequestFacts:
    """Declared lane routes include execution video-request facts using actual constructed-backend identity; session and lane share them."""

    @pytest.mark.parametrize(("route", "bucket"), [("storyboard", "i2v"), ("reference_video", "r2v")])
    @pytest.mark.parametrize("explicit_bucket", [False, True])
    async def test_disabled_configured_model_fails_on_both_sides(
        self, patched_session_factory, project_env, route, bucket, explicit_bucket
    ):
        provider_id = await _seed_custom_video_provider(patched_session_factory)
        project = {
            "video_backend": f"{provider_id}/m-dead",
            "generation_mode": "reference_video" if route == "reference_video" else "storyboard",
        }
        read = await evaluate_video_request_facts(
            project,
            route=route,
            generation_type=bucket,
            identity=CONFIGURED_VIDEO_IDENTITY,
            resolver=ConfigResolver(patched_session_factory),
        )

        with pytest.raises(VideoBucketCapabilityError) as caught:
            await resolve_generation_context(
                "demo",
                None,
                project=project,
                video=VideoLaneRequest(route=route, generation_type=bucket if explicit_bucket else None),
            )

        assert read == VideoRequestFactsFailure(caught.value.code, tuple(caught.value.params.items()))
        assert read.code == "video_capability_reference_unavailable"

    @pytest.mark.parametrize("route", ["storyboard", "reference_video"])
    async def test_unreadable_audio_settings_preserve_the_facts_failure(
        self, patched_session_factory, project_env, monkeypatch, db_engine, route
    ):
        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            async with db_engine.begin() as connection:
                await connection.run_sync(SystemSetting.__table__.drop)
            return _FakeBackend(name=provider_id, model=model_id)

        use_backend_assembler(monkeypatch, _assemble)
        model_id = _registry_video_model("ark")

        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"video_provider_i2v": f"ark/{model_id}"},
            video=VideoLaneRequest(route=route, generation_type="i2v"),
        )

        prefix = "video" if route == "storyboard" else "reference"
        assert ctx.video.request_facts == VideoRequestFactsFailure(
            f"{prefix}_capability_unavailable",
            (("capability", "i2v"), ("provider", "ark"), ("model", model_id)),
        )
        assert ctx.video.resolution is None

    @pytest.mark.parametrize(
        ("route", "generation_type", "project", "allowed"),
        [
            pytest.param(
                "storyboard",
                "i2v",
                {"video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview"},
                (4, 6, 8),
                id="storyboard-i2v",
            ),
            pytest.param(
                "reference_video",
                "r2v",
                {
                    "generation_mode": "reference_video",
                    "video_provider_r2v": "gemini-aistudio/veo-3.1-generate-preview",
                    "video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview",
                },
                (8,),
                id="reference-r2v",
            ),
            pytest.param(
                "reference_video",
                "i2v",
                {
                    "generation_mode": "reference_video",
                    "video_provider_r2v": "gemini-aistudio/veo-3.1-generate-preview",
                    "video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview",
                },
                (4, 6, 8),
                id="reference-i2v",
            ),
        ],
    )
    async def test_lane_facts_equal_the_read_side_facts_for_the_same_configuration(
        self, patched_session_factory, project_env, fake_assemble, route, generation_type, project, allowed, monkeypatch
    ):
        install_catalog(
            monkeypatch,
            with_entry_changes(
                factory_snapshot(),
                "gemini-aistudio",
                "veo-3.1-generate-preview",
                discontinued=False,
                shutdown_date=None,
            ),
        )
        ctx = await resolve_generation_context(
            "demo", None, project=project, video=VideoLaneRequest(generation_type=generation_type, route=route)
        )
        read = await evaluate_video_request_facts(
            project,
            route=route,
            generation_type=generation_type,
            identity=CONFIGURED_VIDEO_IDENTITY,
            resolver=ConfigResolver(patched_session_factory),
        )

        assert isinstance(read, VideoRequestFacts)
        # Unset resolution is omitted on both paths; reference-image constraints apply only to the r2v bucket.
        assert read.resolution is None
        assert read.allowed_durations == allowed
        assert ctx.video.request_facts == read

    async def test_lane_facts_follow_the_backend_that_was_actually_built(
        self, patched_session_factory, project_env, monkeypatch
    ):
        provider_id = await _seed_custom_video_provider(patched_session_factory)

        async with patched_session_factory() as session:
            await session.execute(
                update(CustomProviderModel)
                .where(CustomProviderModel.model_id == "m-dead")
                .values(is_enabled=True, capability_overrides={"durations": [5]})
            )
            await session.commit()

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            return _FakeBackend(name=provider_id, model="m-live")

        use_backend_assembler(monkeypatch, _assemble)
        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"video_backend": f"{provider_id}/m-dead"},
            video=VideoLaneRequest(route="storyboard"),
        )

        facts = ctx.video.request_facts
        assert isinstance(facts, VideoRequestFacts)
        assert (facts.provider_id, facts.model_id, facts.generation_type) == (provider_id, "m-live", "i2v")
        assert facts.resolution == "540p"
        assert facts.allowed_durations == (4, 6, 8)

    async def test_unresolvable_facts_are_carried_as_a_failure_not_raised(
        self, patched_session_factory, project_env, monkeypatch
    ):
        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            return _FakeBackend(name=provider_id, model="mystery-model")

        use_backend_assembler(monkeypatch, _assemble)
        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"video_backend": f"ark/{_registry_video_model('ark')}"},
            video=VideoLaneRequest(generation_type="i2v", route="storyboard"),
        )

        assert ctx.video.request_facts == VideoRequestFactsFailure(
            "video_capability_unavailable",
            (("capability", "i2v"), ("provider", "ark"), ("model", "mystery-model")),
        )


class TestAudioLane:
    async def test_backend_follows_project_tts_snapshot(self, patched_session_factory, project_env, fake_assemble):
        project = {
            "audio_backend": "dashscope/tts-model-x",
            "narration_voice": "Cherry",
            "narration_speed": 1.25,
        }
        ctx = await resolve_generation_context("demo", None, project=project, audio=AudioLaneRequest())
        assert ctx.audio.backend_name == "dashscope"
        assert ctx.audio.backend_model == "tts-model-x"

    async def test_voice_catalog_snapshot_passed_through(self, patched_session_factory, project_env, monkeypatch):
        """ctx.audio.voices is a real backend.list_voices() snapshot, not a default empty tuple. Field existence alone is insufficient; prove resolve_generation_context forwards the voice catalog."""

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            return _FakeBackend(
                name=provider_id, model=model_id or "default-model", voices=[VoiceOption(id="Cherry", label="Cherry")]
            )

        use_backend_assembler(monkeypatch, _assemble)
        project = {"audio_backend": "dashscope/tts-model-x"}
        ctx = await resolve_generation_context("demo", None, project=project, audio=AudioLaneRequest())
        assert tuple(v.id for v in ctx.audio.voices) == ("Cherry",)


class TestAtomicFailure:
    async def test_declared_lane_construction_failure_fails_whole_call(
        self, patched_session_factory, project_env, monkeypatch
    ):
        """If image construction succeeds but video construction fails, propagate the original exception for the entire call without partial results."""

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            if media_type == "video":
                raise ValueError("video backend 构造失败")
            return _FakeBackend(name=provider_id, model=model_id or "default-model")

        use_backend_assembler(monkeypatch, _assemble)
        video_model = _registry_video_model("ark")
        with pytest.raises(ValueError, match="video backend 构造失败"):
            await resolve_generation_context(
                "demo",
                None,
                project={"image_provider_t2i": "ark/img-model-x", "video_backend": f"ark/{video_model}"},
                image=ImageLaneRequest(),
                video=VideoLaneRequest(),
            )

    async def test_missing_project_dir_raises(self, patched_session_factory, project_env, fake_assemble):
        with pytest.raises(FileNotFoundError):
            await resolve_generation_context("nope", None, project={}, image=ImageLaneRequest())


class TestBackendCache:
    async def test_backend_reused_until_invalidated(self, patched_session_factory, project_env, fake_assemble):
        project = {"image_provider_t2i": "ark/img-model-x"}
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        assert len(fake_assemble) == 1, "第二次调用须命中缓存，不再重建 backend"

        generation_context.invalidate_backend_cache()
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        assert len(fake_assemble) == 2, "失效后须重建 backend"

    async def test_cached_backend_is_not_reused_once_the_model_is_discontinued(self, db_factory, monkeypatch):
        """After this process crosses a shutdown date, stop using pre-shutdown cached backends; explicit calls fail discontinued and defaults no longer reuse them."""
        default = catalog.default_model("ark", "image")
        assert default is not None
        install_catalog(
            monkeypatch,
            with_entry_changes(
                factory_snapshot(), "ark", default, shutdown_date=date(2026, 11, 24), successor="bytedance/next"
            ),
        )
        built: list[str | None] = []

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            built.append(model_id)
            return _FakeBackend(name=provider_id, model=model_id or "default-model")

        use_backend_assembler(monkeypatch, _assemble)
        resolver = ConfigResolver(
            db_factory
        )  # Without a model, fill defaults using catalog defaults and disabled state.

        set_catalog_today(monkeypatch, date(2026, 11, 23))
        explicit = await generation_context._get_or_create_image_backend("ark", {"model": default}, resolver)
        implicit = await generation_context._get_or_create_image_backend("ark", {}, resolver)
        assert (explicit.model, implicit.model, built) == (default, default, [default])

        set_catalog_today(monkeypatch, date(2026, 11, 24))
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            await generation_context._get_or_create_image_backend("ark", {"model": default}, resolver)
        assert excinfo.value.params["successor"] == "bytedance/next"
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            await generation_context._get_or_create_image_backend("ark", {}, resolver)
        assert excinfo.value.code == "provider_unsupported_media"
        assert built == [default], "跨过关停日期后不再构造，也不取缓存"

    async def test_cached_custom_backend_is_not_reused_once_its_entry_is_discontinued(
        self, patched_session_factory, monkeypatch
    ):
        """Custom providers also check before cache lookup: after shutdown, explicit references fail discontinued and defaults no longer reuse cached instances."""
        install_catalog(
            monkeypatch,
            with_entry_changes(
                factory_snapshot(),
                "ark",
                SEEDREAM_LITE,
                shutdown_date=date(2026, 11, 24),
                successor="bytedance/doubao-seedream-5-0-pro",
            ),
        )
        provider_id = await _seed_custom_image_provider(
            patched_session_factory,
            [
                {"model_id": SEEDREAM_LITE, "endpoint": "openai-images", "is_default": True},
                {"model_id": "relay-image", "endpoint": "openai-images"},
            ],
        )
        built: list[str | None] = []

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            built.append(model_id)
            return _FakeBackend(name=provider_id, model=model_id or SEEDREAM_LITE)

        use_backend_assembler(monkeypatch, _assemble)
        resolver = ConfigResolver(patched_session_factory)

        set_catalog_today(monkeypatch, date(2026, 11, 23))
        explicit = await generation_context._get_or_create_image_backend(
            provider_id, {"model": SEEDREAM_LITE}, resolver, generation_type="t2i"
        )
        implicit = await generation_context._get_or_create_image_backend(
            provider_id, {}, resolver, generation_type="t2i"
        )
        assert (explicit.model, implicit.model, built) == (SEEDREAM_LITE, SEEDREAM_LITE, [SEEDREAM_LITE, None])

        set_catalog_today(monkeypatch, date(2026, 11, 24))
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            await generation_context._get_or_create_image_backend(
                provider_id, {"model": SEEDREAM_LITE}, resolver, generation_type="t2i"
            )
        assert excinfo.value.params["successor"] == "bytedance/doubao-seedream-5-0-pro"
        with pytest.raises(ValueError, match="没有默认 t2i image 模型"):
            await generation_context._get_or_create_image_backend(provider_id, {}, resolver, generation_type="t2i")
        live = await generation_context._get_or_create_image_backend(
            provider_id, {"model": "relay-image"}, resolver, generation_type="t2i"
        )
        assert live.model == "relay-image"
        assert built == [SEEDREAM_LITE, None, "relay-image"], "已下线的引用不再构造，也不取缓存"

    async def test_image_buckets_do_not_share_a_cache_entry(self, patched_session_factory, monkeypatch):
        """Construct t2i and i2i separately for the same (media_type, provider, model): buckets affect construction and require separate cache entries.

        Custom-provider default fallback leaves model_id empty while loading different backends per bucket. Sharing one entry lets the first bucket displace the other's backend."""
        provider_id = await _seed_custom_image_provider(
            patched_session_factory,
            [
                # openai-images-generations declares only t2i; openai-images-edits only i2i.
                {"model_id": "t2i-m", "endpoint": "openai-images-generations", "is_default": True},
                {"model_id": "i2i-m", "endpoint": "openai-images-edits", "is_default": True},
            ],
        )
        seen: list[str | None] = []

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            seen.append(generation_type)
            return _FakeBackend(name=provider_id, model=f"{generation_type}-model")

        use_backend_assembler(monkeypatch, _assemble)
        resolver = ConfigResolver(patched_session_factory)

        t2i = await generation_context._get_or_create_image_backend(provider_id, {}, resolver, generation_type="t2i")
        i2i = await generation_context._get_or_create_image_backend(provider_id, {}, resolver, generation_type="i2i")
        t2i_again = await generation_context._get_or_create_image_backend(
            provider_id, {}, resolver, generation_type="t2i"
        )

        assert seen == ["t2i", "i2i"], "换桶须重新构造，同桶重复请求须命中缓存"
        assert (t2i.model, i2i.model) == ("t2i-model", "i2i-model")
        assert t2i_again is t2i

    async def test_a_changed_endpoint_is_not_served_from_the_cache(self, monkeypatch):
        """After model-endpoint rebinding (such as official sync without cache invalidation), next lookup constructs using the new endpoint."""
        built_with: list[str | None] = []

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            built_with.append(builtin_endpoint)
            return _FakeBackend(name=provider_id, model=model_id)

        use_backend_assembler(monkeypatch, _assemble)
        resolver = cast(ConfigResolver, None)
        model_id = "ep-image-endpoint"
        try:
            for endpoint in ("ark-seedream-3", "ark-seedream-4"):
                catalog.install_user_added_models(
                    (catalog.UserAddedModel("ark", model_id, "image", endpoint=endpoint),)
                )
                await generation_context._get_or_create_image_backend("ark", {"model": model_id}, resolver)
        finally:
            catalog.install_user_added_models(())

        assert built_with == ["ark-seedream-3", "ark-seedream-4"]

    async def test_invalidate_during_construction_discards_instance(self, monkeypatch):
        """If invalidate occurs during suspended assemble_backend construction, never cache the completed instance."""
        entered = asyncio.Event()
        release = asyncio.Event()
        built: list[_FakeBackend] = []

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            backend = _FakeBackend(name=provider_id, model=model_id or "default-model")
            built.append(backend)
            if len(built) == 1:
                entered.set()
                await release.wait()
            return backend

        use_backend_assembler(monkeypatch, _assemble)
        resolver = cast(ConfigResolver, None)

        task = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        await entered.wait()
        generation_context.invalidate_backend_cache()
        release.set()
        stale, _ = await task

        fresh, _ = await generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver)
        assert len(built) == 2, "缓存中不得残留失效期间构造的实例，后续访问须重新构造"
        assert stale is built[0]
        assert fresh is built[1]
        assert fresh is not stale

    async def test_concurrent_same_key_constructs_once(self, monkeypatch):
        """Concurrent same-key get_or_create calls construct once and return the same instance to both callers."""
        entered = asyncio.Event()
        release = asyncio.Event()
        construct_count = 0

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            nonlocal construct_count
            construct_count += 1
            entered.set()
            await release.wait()
            return _FakeBackend(name=provider_id, model=model_id or "default-model")

        use_backend_assembler(monkeypatch, _assemble)
        resolver = cast(ConfigResolver, None)

        t1 = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        t2 = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        await entered.wait()
        # Let t2 enter get_or_create while t1 construction is suspended: concurrent misses, not sequential hits.
        for _ in range(3):
            await asyncio.sleep(0)
        release.set()
        (b1, _), (b2, _) = await asyncio.gather(t1, t2)

        assert construct_count == 1, "同 key 并发 miss 须 single-flight，只构造一次"
        assert b1 is b2

    async def test_follower_queued_before_invalidate_discards_instance(self, monkeypatch):
        """Old-generation followers queued before invalidation still must not write cache after crossing that boundary.

        Beyond the already tested interruption of a lock-holding leader, a follower may enter get_or_create during leader construction before invalidate(), then acquire only afterward. Its factory closure still captures old configuration; even if it sees the new generation on acquisition, judge staleness by the generation captured on entry before waiting. Otherwise an old-config backend would be mislabeled current and contaminate later same-key requests."""
        entered = asyncio.Event()
        release = asyncio.Event()
        built: list[_FakeBackend] = []

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            backend = _FakeBackend(name=provider_id, model=model_id or "default-model")
            built.append(backend)
            if len(built) == 1:
                entered.set()
                await release.wait()
            return backend

        use_backend_assembler(monkeypatch, _assemble)
        resolver = cast(ConfigResolver, None)

        leader = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        await entered.wait()  # Leader holds the lock and is suspended mid-construction.

        follower = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        # Let follower capture generation and queue for the lock, without acquiring it yet.
        for _ in range(3):
            await asyncio.sleep(0)

        generation_context.invalidate_backend_cache()  # Invalidation boundary: follower is queued and generation advances.
        release.set()  # Release leader to finish construction.
        stale, _ = await leader
        stale_from_follower, _ = await follower

        assert stale is built[0]
        assert stale_from_follower is built[1]
        assert stale is not stale_from_follower

        fresh, _ = await generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver)
        assert len(built) == 3, "leader 与 follower 的旧代际实例均不得写回，后续访问须重新构造"
        assert fresh is built[2]
        assert fresh is not stale
        assert fresh is not stale_from_follower


class TestValueObjectAssembly:
    def test_fake_context_assembles_from_frozen_dataclasses(self, tmp_path: Path):
        """Consumer-test assembly: construct fake contexts directly as frozen dataclasses; properties return unchanged."""
        lane = VideoLaneResult(
            provider_model=ProviderModel("ark", "m"),
            backend_name="ark",
            backend_model="m",
            resolution=None,
            request_facts=make_video_request_facts(provider_id="ark", model_id="m"),
        )
        ctx = GenerationContext(generator=MediaGenerator(tmp_path / "p"), video_lane=lane)
        assert ctx.video is lane
        with pytest.raises(RuntimeError, match="image lane 未声明"):
            _ = ctx.image

    def test_lane_results_are_frozen(self):
        lane = ImageLaneResult(
            provider_model=ProviderModel("ark", "m"),
            backend_name="ark",
            backend_model="m",
            resolution=None,
            max_reference_images=None,
        )
        with pytest.raises(AttributeError):
            lane.resolution = "720p"

    @pytest.mark.parametrize(
        ("voice_consistency", "requested_generate_audio", "expected"),
        [
            ("soft", True, False),
            ("native", True, False),
            # Class C models produce no audio.
            ("none", True, True),
            # Episode audio disabled: even a model audio track is inaudible.
            ("soft", False, True),
            ("none", False, True),
        ],
    )
    def test_video_lane_is_silent_covers_both_paths(
        self, voice_consistency: VoiceConsistency, requested_generate_audio: bool, expected: bool
    ):
        """Mute criteria combine model tiers and episode switches; renderers decide whether to inject voice styles."""
        lane = VideoLaneResult(
            provider_model=ProviderModel("ark", "m"),
            backend_name="ark",
            backend_model="m",
            resolution=None,
            request_facts=make_video_request_facts(
                voice_consistency=voice_consistency, requested_generate_audio=requested_generate_audio
            ),
        )
        assert lane.is_silent is expected

    def test_video_lane_failure_does_not_supply_audio_fallback(self):
        lane = VideoLaneResult(
            provider_model=ProviderModel("ark", "m"),
            backend_name="ark",
            backend_model="m",
            resolution=None,
            request_facts=VideoRequestFactsFailure("video_capability_unavailable"),
        )
        with pytest.raises(VideoRequestFactsError, match="video_capability_unavailable"):
            _ = lane.is_silent
