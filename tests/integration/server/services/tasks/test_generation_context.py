"""resolve_generation_context 公开接口测试：lane 声明与跳过 / fail-loud property /
按实际身份查 resolution 与视频请求事实 / 原子失败 / backend 缓存与失效。

按 ADR 0049 的测试口径：真实内存 DB + tmp_path 真 ProjectManager + fake backend
（仅替换 assemble_backend 构造缝），不 mock ConfigResolver / ProjectManager，不断言私有属性。
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoCapabilityError
from lib.backends.audio_backends.base import VoiceOption
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model, get_provider_spec
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
)
from lib.catalog import query as catalog
from lib.catalog.errors import ModelDiscontinuedError, ModelNotInCatalogError
from lib.config.resolver import (
    ConfigResolver,
    ImageBucketCapabilityError,
    ProviderModel,
    VideoBucketCapabilityError,
    VoiceConsistency,
)
from lib.config.service import ConfigService
from lib.custom_provider import make_provider_id
from lib.db.models.config import SystemSetting
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.generation.media_generator import MediaGenerator
from lib.generation.video_request_facts import (
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
)
from lib.project.project_manager import ProjectManager
from lib.video_runtime.image_resolution import ImageRuntime
from lib.video_runtime.resolution import read_video_request_facts
from server.services.tasks import generation_context
from server.services.tasks.generation_context import (
    AudioLaneRequest,
    GenerationContext,
    ImageLaneRequest,
    ImageLaneResult,
    SubmittedVideoLaneRequest,
    VideoLaneRequest,
    VideoLaneResult,
    resolve_generation_context,
)
from tests.catalog_support import install_catalog, set_catalog_today, with_entry_changes, with_entry_facts
from tests.factories import make_video_request_facts
from tests.fakes import bind_session_factory, use_project_manager
from tests.fakes import use_image_assembler as _patch_image_assembler
from tests.http_capture import capture_http, only_request, request_form_fields, request_json


def use_video_assembler(monkeypatch, assemble):
    async def assemble_spec(spec):
        return await assemble(
            provider_id=spec.snapshot.provider_id,
            model_id=spec.snapshot.model_id,
            media_type="video",
            builtin_endpoint=spec.endpoint,
            resolver=None,
        )

    monkeypatch.setattr(generation_context, "assemble_video", assemble_spec)


def use_audio_assembler(monkeypatch, assemble):
    async def assemble_spec(spec):
        return await assemble(
            provider_id=spec.provider_id,
            model_id=spec.model_id,
            media_type="audio",
            builtin_endpoint=spec.endpoint,
            resolver=None,
        )

    monkeypatch.setattr(generation_context, "assemble_audio", assemble_spec)


def use_image_assembler(monkeypatch, assemble):
    async def assemble_spec(spec):
        return await assemble(
            provider_id=spec.snapshot.provider_id,
            model_id=spec.snapshot.model_id,
            media_type="image",
            builtin_endpoint=spec.endpoint,
            resolver=None,
        )

    _patch_image_assembler(monkeypatch, assemble_spec)


def _registry_image_model(provider_id: str) -> str:
    return next(mid for mid, mi in catalog.list_models(provider_id).items() if mi.media_type == "image")


def _registry_video_model(provider_id: str) -> str:
    """从目录取该 provider 的任一视频 model id，避免硬编码供应商数据。"""
    return next(mid for mid, mi in catalog.list_models(provider_id).items() if mi.media_type == "video")


def _registry_audio_model(provider_id: str) -> str:
    """从目录取该 provider 的任一音频 model id，避免硬编码供应商数据。"""
    return next(mid for mid, mi in catalog.list_models(provider_id).items() if mi.media_type == "audio")


def _backend_video_caps(provider_id: str, model_id: str) -> VideoCapabilities:
    """视频能力位与参考图上限的唯一声明处是 backend，目录的模型信息不带这些字段。"""
    return builtin_video_capabilities_for_model(provider_id, model_id)


@dataclass
class _FakeBackend:
    name: str
    model: str
    voices: list = field(default_factory=list)
    max_reference_images: int | None = None
    capabilities: frozenset[ImageCapability] = BOTH_IMAGE_CAPABILITIES

    async def reference_image_limit(self) -> int | None:
        """image backend 协议的一部分：image lane 解析时读一次参考图上限。"""
        return self.max_reference_images

    def list_voices(self):
        """audio backend 协议的一部分：audio lane 解析时会取音色目录快照。"""
        return self.voices


@pytest.fixture
async def patched_session_factory(db_factory, monkeypatch):
    """真实内存 DB：建全部 ORM 表，并把 lib.db.async_session_factory 指向它。"""
    bind_session_factory(monkeypatch, db_factory)
    return db_factory


@pytest.fixture
def project_env(monkeypatch, tmp_path: Path):
    """tmp_path 下的真 ProjectManager + 已存在的项目目录。"""
    pm = ProjectManager(tmp_path)
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    use_project_manager(monkeypatch, pm)
    return pm


@pytest.fixture(autouse=True)
def clean_backend_cache():
    """每个用例前后清空 backend 实例缓存与 per-key 锁（锁绑定事件循环的原因见 ``reset_backend_cache_for_tests``）。"""
    generation_context.reset_backend_cache_for_tests()
    yield
    generation_context.reset_backend_cache_for_tests()


@pytest.fixture
def fake_assemble(monkeypatch):
    """替换 backend 构造缝：默认按请求原样回声身份，记录每次构造。"""
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

    use_image_assembler(monkeypatch, _assemble)
    use_video_assembler(monkeypatch, _assemble)
    use_audio_assembler(monkeypatch, _assemble)
    return calls


async def _seed_custom_video_provider(patched_session_factory) -> str:
    """种一个自定义供应商：目标 model 已禁用、默认 model 存活（带 resolution 与时长表）。"""
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
    """种一个自定义供应商，模型行均已启用；按名字对应条目。"""
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
        """只声明 image lane：不解析、不构造 video/audio，视频供应商缺配置不影响图片任务。"""
        image_model = _registry_image_model("ark")
        project = {"image_provider_t2i": f"ark/{image_model}"}
        ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())

        assert [media for _, media, _ in fake_assemble] == ["image"]
        assert ctx.image.provider_model == ProviderModel("ark", image_model)
        assert ctx.image.backend_name == "ark"
        assert ctx.image.backend_model == image_model
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

        audio_model = _registry_audio_model("dashscope")
        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"audio_backend": f"dashscope/{audio_model}"},
            project_path=project_path,
            audio=AudioLaneRequest(),
        )

        assert ctx.generator.project_path == project_path

    async def test_i2i_capability_selects_i2i_slot(self, patched_session_factory, project_env, fake_assemble):
        i2i_model = _registry_image_model("ark")
        project = {
            "image_provider_t2i": "ark/img-t2i",
            "image_provider_i2i": f"ark/{i2i_model}",
        }
        ctx = await resolve_generation_context(
            "demo", None, project=project, image=ImageLaneRequest(generation_type="i2i")
        )
        assert ctx.image.provider_model == ProviderModel("ark", i2i_model)

    async def test_all_three_lanes(self, patched_session_factory, project_env, fake_assemble):
        image_model = _registry_image_model("ark")
        video_model = _registry_video_model("ark")
        audio_model = _registry_audio_model("dashscope")
        project = {
            "image_provider_t2i": f"ark/{image_model}",
            "video_backend": f"ark/{video_model}",
            "audio_backend": f"dashscope/{audio_model}",
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
        assert ctx.audio.provider_model == ProviderModel("dashscope", audio_model)


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

    async def test_lane_without_a_route_returns_only_the_resolved_identity(
        self, patched_session_factory, project_env, fake_assemble
    ):
        video_model = _registry_video_model("ark")
        ctx = await resolve_generation_context(
            "demo", None, project={"video_backend": f"ark/{video_model}"}, video=VideoLaneRequest()
        )
        assert ctx.video.backend_model == video_model
        assert ctx.video.resolution is None
        assert ctx.video.request_facts is None

    async def test_requested_generate_audio_follows_project_override(
        self, patched_session_factory, project_env, fake_assemble
    ):
        """本集无声开关随 project.json 覆盖进 lane，编排层据此决定要不要组装参考音频。"""
        video_model = _registry_video_model("ark")
        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"video_backend": f"ark/{video_model}", "video_generate_audio": False},
            video=VideoLaneRequest(route="storyboard"),
        )
        assert isinstance(ctx.video.request_facts, VideoRequestFacts)
        assert ctx.video.request_facts.requested_generate_audio is False

    async def test_payload_overrides_project(self, patched_session_factory, project_env, fake_assemble):
        """payload > project：显式的请求身份（如 checkpoint 回放）决定实际解析身份。"""
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
        """构造之后用户改了模型挂接的端点：冻结的守卫仍是实际提交任务的那个端点，续跑不会按新端点放行。"""
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
            catalog.install_user_added_models(
                (catalog.UserAddedModel("ark", model_id, "video", endpoint="ark-seedance-2-5"),)
            )
            return _FakeBackend(name=provider_id, model=model_id)

        use_video_assembler(monkeypatch, _assemble)
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
        """构造期间切换端点，事实和读侧能力仍按本次实际构造的端点求值。"""
        model_id = "endpoint-race-video"

        def install_endpoint(value):
            catalog.install_user_added_models(
                (catalog.UserAddedModel("kling", model_id, "video", fallback_durations=(5,), endpoint=value),)
            )

        install_endpoint(endpoint)
        backends = []

        async def _assemble(*, provider_id, media_type, model_id, builtin_endpoint, **kwargs):
            from lib.video_runtime.builtin import build_builtin_backend

            backend = build_builtin_backend(
                get_provider_spec(provider_id, media_type, builtin_endpoint),
                LoadedConfig(
                    credentials={"access_key": "test", "secret_key": "test"}, provider_meta=None, rate_limiter=None
                ),
                model_id,
            )
            backends.append(backend)
            install_endpoint(changed_endpoint)
            return backend

        use_video_assembler(monkeypatch, _assemble)
        project = {"video_backend": f"kling/{model_id}", "video_generate_audio": True}
        try:
            ctx = await resolve_generation_context(
                "demo", None, project=project, video=VideoLaneRequest(route="storyboard")
            )
            facts = ctx.video.request_facts
            assert isinstance(facts, VideoRequestFacts)
            assert facts.has_audio_track is has_audio
            from server.services.tasks.video_caps import resolve_project_video_capabilities

            current_caps = await resolve_project_video_capabilities(
                project, generation_type="i2v", config_resolver=ConfigResolver(patched_session_factory)
            )
        finally:
            catalog.install_user_added_models(())

        assert ctx.video.endpoint == f"builtin:{endpoint}"
        assert backends[0].video_capabilities.audio_track.value == ("controllable" if has_audio else "always_off")
        assert facts.audio_switch_controllable is has_audio
        assert facts.generate_audio is has_audio
        assert facts.voice_consistency == ("soft" if has_audio else "none")
        assert current_caps["generate_audio"] is not has_audio

    async def test_submitted_job_requires_a_pinned_identity(self, patched_session_factory, project_env, fake_assemble):
        """续跑的下线豁免只给提交时锁定的身份：没有锁定身份时不按当前配置解析，直接报错，也不构造 backend。"""
        ark_model = _registry_video_model("ark")
        with pytest.raises(ValueError, match="locked video provider and model"):
            await resolve_generation_context(
                "demo",
                None,
                project={"video_provider_i2v": f"ark/{ark_model}"},
                video=SubmittedVideoLaneRequest(ProviderModel("ark", ""), None, "job"),
            )
        assert fake_assemble == []


class TestActualIdentityQueries:
    async def test_unbucketed_custom_model_falls_back_to_enabled_default(self, patched_session_factory, project_env):
        provider_id = await _seed_custom_video_provider(patched_session_factory)
        ctx = await resolve_generation_context(
            "demo",
            None,
            project={"video_backend": f"{provider_id}/m-dead"},
            video=VideoLaneRequest(),
        )

        assert ctx.video.provider_model == ProviderModel(provider_id, "m-live")
        assert ctx.video.backend_model == "m-live"
        assert ctx.video.resolution is None
        assert ctx.video.request_facts is None


class TestVideoRequestFacts:
    """lane 声明路线时附带执行侧视频请求事实：身份取实际构造的 backend，session 与 lane 共用。"""

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
        read = await read_video_request_facts(
            project,
            route=route,
            generation_type=bucket,
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
        self, patched_session_factory, project_env, fake_assemble, db_engine, route
    ):
        async with db_engine.begin() as connection:
            await connection.run_sync(SystemSetting.__table__.drop)
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
                catalog.factory_snapshot(),
                "gemini-aistudio",
                "veo-3.1-generate-preview",
                discontinued=False,
                shutdown_date=None,
            ),
        )
        ctx = await resolve_generation_context(
            "demo", None, project=project, video=VideoLaneRequest(generation_type=generation_type, route=route)
        )
        read = await read_video_request_facts(
            project,
            route=route,
            generation_type=generation_type,
            resolver=ConfigResolver(patched_session_factory),
        )

        assert isinstance(read, VideoRequestFacts)
        assert read.resolution is None
        assert read.allowed_durations == allowed
        assert ctx.video.request_facts == read


class TestAudioLane:
    async def test_backend_follows_project_tts_snapshot(self, patched_session_factory, project_env, fake_assemble):
        audio_model = _registry_audio_model("dashscope")
        project = {
            "audio_backend": f"dashscope/{audio_model}",
            "narration_voice": "Cherry",
            "narration_speed": 1.25,
        }
        ctx = await resolve_generation_context("demo", None, project=project, audio=AudioLaneRequest())
        assert ctx.audio.backend_name == "dashscope"
        assert ctx.audio.backend_model == audio_model

    async def test_voice_catalog_snapshot_passed_through(self, patched_session_factory, project_env, monkeypatch):
        """ctx.audio.voices 须是 backend.list_voices() 的真实快照，而非默认的空元组——
        断言字段存在不够，须证明 resolve_generation_context 确实转发了 backend 的音色目录。
        """

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

        use_audio_assembler(monkeypatch, _assemble)
        audio_model = _registry_audio_model("dashscope")
        project = {"audio_backend": f"dashscope/{audio_model}"}
        ctx = await resolve_generation_context("demo", None, project=project, audio=AudioLaneRequest())
        assert tuple(v.id for v in ctx.audio.voices) == ("Cherry",)


class TestAtomicFailure:
    async def test_declared_lane_construction_failure_fails_whole_call(
        self, patched_session_factory, project_env, monkeypatch
    ):
        """image 成功后 video 构造失败：整次调用原样上抛，无部分结果。"""

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

        use_image_assembler(monkeypatch, _assemble)
        use_video_assembler(monkeypatch, _assemble)
        image_model = _registry_image_model("ark")
        video_model = _registry_video_model("ark")
        with pytest.raises(ValueError, match="video backend 构造失败"):
            await resolve_generation_context(
                "demo",
                None,
                project={"image_provider_t2i": f"ark/{image_model}", "video_backend": f"ark/{video_model}"},
                image=ImageLaneRequest(),
                video=VideoLaneRequest(),
            )

    async def test_missing_project_dir_raises(self, patched_session_factory, project_env, fake_assemble):
        with pytest.raises(FileNotFoundError):
            await resolve_generation_context("nope", None, project={}, image=ImageLaneRequest())


class TestBackendCache:
    async def test_backend_reused_until_invalidated(self, patched_session_factory, project_env, fake_assemble):
        image_model = _registry_image_model("ark")
        project = {"image_provider_t2i": f"ark/{image_model}"}
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        assert len(fake_assemble) == 1, "第二次调用须命中缓存，不再重建 backend"

        generation_context.invalidate_backend_cache()
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        assert len(fake_assemble) == 2, "失效后须重建 backend"

    async def test_cached_backend_is_not_reused_once_the_model_is_discontinued(self, db_factory, monkeypatch):
        """同一进程跨过关停日期：缓存里关停前构造的 backend 不再被取用，调用报下线；没给模型的调用始终报错。"""
        default = catalog.default_model("ark", "image")
        assert default is not None
        install_catalog(
            monkeypatch,
            with_entry_changes(
                catalog.factory_snapshot(), "ark", default, shutdown_date=date(2026, 11, 24), successor="bytedance/next"
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

        use_image_assembler(monkeypatch, _assemble)
        resolver = ConfigResolver(db_factory)
        runtime = ImageRuntime(resolver, capture_generation=generation_context._backend_cache.generation)

        async def _get(model_id: str | None):
            project = {"image_provider_t2i": f"ark/{model_id}" if model_id else "ark"}
            return await generation_context._image_backend_for(await runtime.resolve(project, None, bucket="t2i"))

        set_catalog_today(monkeypatch, date(2026, 11, 23))
        explicit = await _get(default)
        assert (explicit.model, built) == (default, [default])
        with pytest.raises(ModelNotInCatalogError):
            await _get(None)

        set_catalog_today(monkeypatch, date(2026, 11, 24))
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            await _get(default)
        assert excinfo.value.params["successor"] == "bytedance/next"
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            await _get(None)
        assert excinfo.value.code == "model_not_selected"
        assert built == [default], "跨过关停日期后不再构造，也不取缓存"

    async def test_cached_custom_backend_is_not_reused_once_its_entry_is_discontinued(
        self, patched_session_factory, monkeypatch
    ):
        """自定义供应商同样在查缓存前检查：跨过关停日期后，显式引用报下线，默认模型也不再沿用缓存里的实例。"""
        install_catalog(
            monkeypatch,
            with_entry_changes(
                catalog.factory_snapshot(),
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

        use_image_assembler(monkeypatch, _assemble)
        resolver = ConfigResolver(patched_session_factory)
        runtime = ImageRuntime(resolver, capture_generation=generation_context._backend_cache.generation)

        async def _get(model_id: str | None):
            project = {"image_provider_t2i": f"{provider_id}/{model_id or 'missing-model'}"}
            return await generation_context._image_backend_for(await runtime.resolve(project, None, bucket="t2i"))

        set_catalog_today(monkeypatch, date(2026, 11, 23))
        explicit = await _get(SEEDREAM_LITE)
        implicit = await _get(None)
        assert (explicit.model, implicit.model, built) == (SEEDREAM_LITE, SEEDREAM_LITE, [SEEDREAM_LITE])

        set_catalog_today(monkeypatch, date(2026, 11, 24))
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            await _get(SEEDREAM_LITE)
        assert excinfo.value.params["successor"] == "bytedance/doubao-seedream-5-0-pro"
        with pytest.raises(ValueError, match="没有默认 t2i image 模型"):
            await _get(None)
        live = await _get("relay-image")
        assert live.model == "relay-image"
        assert built == [SEEDREAM_LITE, "relay-image"], "已下线的引用不再构造，也不取缓存"

    async def test_image_buckets_do_not_share_a_cache_entry(self, patched_session_factory, monkeypatch):
        """同 (media_type, provider, model) 的 t2i 与 i2i 各自构造：桶参与构造，须各占一条缓存。

        自定义供应商回退默认模型时 model_id 为空，两个桶装载出的是两个不同模型的 backend；共用
        一条缓存会让先跑的那个桶把另一个桶的 backend 挤掉。
        """
        provider_id = await _seed_custom_image_provider(
            patched_session_factory,
            [
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
            seen.append(model_id)
            return _FakeBackend(name=provider_id, model=f"{model_id}")

        use_image_assembler(monkeypatch, _assemble)
        resolver = ConfigResolver(patched_session_factory)
        runtime = ImageRuntime(resolver, capture_generation=generation_context._backend_cache.generation)

        t2i = await generation_context._image_backend_for(
            await runtime.resolve({"image_provider_t2i": f"{provider_id}/missing-model"}, None, bucket="t2i")
        )
        i2i = await generation_context._image_backend_for(
            await runtime.resolve({"image_provider_i2i": f"{provider_id}/missing-model"}, None, bucket="i2i")
        )
        t2i_again = await generation_context._image_backend_for(
            await runtime.resolve({"image_provider_t2i": f"{provider_id}/missing-model"}, None, bucket="t2i")
        )

        assert seen == ["t2i-m", "i2i-m"], "换桶须重新构造，同桶重复请求须命中缓存"
        assert (t2i.model, i2i.model) == ("t2i-m", "i2i-m")
        assert t2i_again is t2i

    async def test_changed_workflow_with_same_bindings_replaces_cached_image_backend(self, db_factory):
        from dataclasses import asdict

        from lib.custom_provider.endpoint_resolution import derive_mirror_columns
        from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
        from lib.video_runtime.image_resolution import ImageRuntime
        from tests.factories import comfyui_endpoint_definition

        definition = comfyui_endpoint_definition(media_type="image")
        columns = asdict(derive_mirror_columns(definition))
        async with db_factory.begin() as session:
            endpoint = await CustomEndpointRepository(session).create(definition=definition, **columns)
            endpoint_id = endpoint.id
        provider = await _seed_custom_image_provider(
            db_factory, [{"model_id": "workflow", "endpoint": f"ce-{endpoint_id}", "is_default": True}]
        )
        runtime = ImageRuntime(
            ConfigResolver(db_factory), capture_generation=generation_context._backend_cache.generation
        )
        project = {"image_provider_t2i": f"{provider}/workflow"}

        async def backend():
            target = await runtime.resolve(project, None, bucket="t2i")
            return await generation_context._image_backend_for(target)

        original = await backend()
        assert await backend() is original
        definition["workflow"]["3"]["inputs"]["steps"] = 51
        async with db_factory.begin() as session:
            await CustomEndpointRepository(session).update(endpoint_id, definition=definition, **columns)
        assert await backend() is not original

    async def test_image_quote_identity_uses_executions_bucket_default(self, db_factory):
        from lib.video_runtime.image_resolution import ImageRuntime

        provider = await _seed_custom_image_provider(
            db_factory,
            [{"model_id": "replacement", "endpoint": "openai-images", "is_default": True, "resolution": "2K"}],
        )
        project = {"image_provider_i2i": f"{provider}/removed"}
        runtime = ImageRuntime(ConfigResolver(db_factory))
        identity, resolution = await runtime.read_identity(project, "i2i")
        target = await runtime.resolve(project, None, bucket="i2i")
        assert identity.model_id == target.snapshot.model_id == "replacement"
        assert resolution == target.snapshot.default_resolution == "2K"

    async def test_a_changed_endpoint_is_not_served_from_the_cache(self, db_factory, monkeypatch):
        """模型挂接的端点变了（如官方目录同步不经缓存失效），下一次取到的是按新端点构造的实例。"""
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

        use_image_assembler(monkeypatch, _assemble)
        resolver = ConfigResolver(db_factory)
        runtime = ImageRuntime(resolver, capture_generation=generation_context._backend_cache.generation)
        model_id = "ep-image-endpoint"
        try:
            for endpoint in ("ark-seedream-3", "ark-seedream-4"):
                catalog.install_user_added_models(
                    (catalog.UserAddedModel("ark", model_id, "image", endpoint=endpoint),)
                )
                target = await runtime.resolve({"image_provider_t2i": f"ark/{model_id}"}, None, bucket="t2i")
                await generation_context._image_backend_for(target)
        finally:
            catalog.install_user_added_models(())

        assert built_with == ["ark-seedream-3", "ark-seedream-4"]

    async def test_invalidation_during_config_read_never_replaces_new_generation(
        self, patched_session_factory, project_env, monkeypatch
    ):
        provider = "ark"
        model = _registry_video_model(provider)
        project = {"video_backend": f"{provider}/{model}"}
        entered, release = asyncio.Event(), asyncio.Event()
        original = ConfigResolver.provider_config_with_credential
        reads = 0

        async def read(resolver, provider_id):
            nonlocal reads
            config, credential = await original(resolver, provider_id)
            reads += 1
            if reads == 1:
                entered.set()
                await release.wait()
            return config, credential

        monkeypatch.setattr(ConfigResolver, "provider_config_with_credential", read)
        built = []

        async def assemble(spec):
            key = spec.config.credentials.get("api_key")
            built.append(key)
            return _FakeBackend(name=key or "original", model=model)

        monkeypatch.setattr(generation_context, "assemble_video", assemble)

        async def execute():
            return (
                await resolve_generation_context(
                    "demo", None, project=project, video=VideoLaneRequest(route="storyboard")
                )
            ).video

        stale = asyncio.create_task(execute())
        entered_task = asyncio.create_task(entered.wait())
        try:
            done, _ = await asyncio.wait({entered_task, stale}, return_when=asyncio.FIRST_COMPLETED)
            if stale in done:
                stale.result()
                raise AssertionError("stale request finished before reading the config")
            async with patched_session_factory() as session:
                await ConfigService(session).set_provider_config(provider, "api_key", "new")
                await session.commit()
            generation_context.invalidate_backend_cache()
            first, second = await asyncio.gather(execute(), execute())
        finally:
            release.set()
            entered_task.cancel()
            with suppress(asyncio.CancelledError):
                await entered_task
            if not stale.done():
                await asyncio.gather(stale, return_exceptions=True)
        old = await stale
        assert first.backend_name == second.backend_name == "new"
        assert old.backend_name != "new"
        assert (await execute()).backend_name == "new"
        assert built.count("new") == 1
        assert len(built) == 2
        async with patched_session_factory() as session:
            await ConfigService(session).set_provider_config(provider, "api_key", "third")
            await session.commit()
        assert (await execute()).backend_name == "third"
        assert reads == 5
        for _ in range(3):
            generation_context.invalidate_backend_cache()
            assert not generation_context._backend_cache._locks
            await execute()


class TestValueObjectAssembly:
    def test_fake_context_assembles_from_frozen_dataclasses(self, tmp_path: Path):
        """消费方测试的拼装路径：frozen dataclass 直接构造假 context，property 原样返回。"""
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
            ("none", True, True),
            ("soft", False, True),
            ("none", False, True),
        ],
    )
    def test_video_lane_is_silent_covers_both_paths(
        self, voice_consistency: VoiceConsistency, requested_generate_audio: bool, expected: bool
    ):
        """无声判据合并模型档与本集开关两条路径，渲染层据此决定是否注入声音风格。"""
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


_QUALITIES = ("low", "medium", "high")


def _with_qualities(monkeypatch) -> None:
    install_catalog(
        monkeypatch, with_entry_facts(catalog.factory_snapshot(), "openai", "gpt-image-2", qualities=_QUALITIES)
    )


async def _send(
    ctx: GenerationContext, tmp_path: Path, *, refs: bool, base_url: str = "https://api.openai.com/v1"
) -> dict:
    """经过真实生成器和 SDK，返回 HTTP 请求中的尺寸与画质。"""
    references: list[Path] = []
    if refs:
        ref = tmp_path / "ref.png"
        Image.new("RGB", (64, 64)).save(ref)
        references = [ref]
    with capture_http() as router:
        path = "edits" if refs else "generations"
        route = router.post(f"{base_url}/images/{path}").respond(
            json={"created": 0, "data": [{"b64_json": base64.b64encode(b"img").decode()}]}
        )
        output, version = await ctx.generator.generate_image_async(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            reference_images=references,
            aspect_ratio="9:16",
            image_size=ctx.image.resolution,
            quality=ctx.image.quality,
        )
        request = only_request(route)
        fields = request_form_fields(request) if refs else request_json(request)
    assert output.read_bytes() == b"img"
    assert version >= 1
    return {key: fields[key] for key in ("size", "quality") if key in fields}


class TestImageQuality:
    @pytest.fixture(autouse=True)
    async def provider_credentials(self, patched_session_factory):
        async with patched_session_factory() as session:
            for provider in ("openai", "vidu"):
                await ConfigService(session).set_provider_config(provider, "api_key", "test-key")
            await session.commit()

    @pytest.mark.parametrize("generation_type", ["t2i", "i2i"])
    async def test_builtin_openai_sends_the_stored_quality_with_the_tier_size(
        self, patched_session_factory, project_env, monkeypatch, tmp_path, generation_type
    ):
        _with_qualities(monkeypatch)

        async def _sent(resolution: str, quality: str) -> dict:
            generation_context.reset_backend_cache_for_tests()
            project = {
                f"image_provider_{generation_type}": "openai/gpt-image-2",
                "model_settings": {"openai/gpt-image-2": {"resolution": resolution, "quality": quality}},
            }
            ctx = await resolve_generation_context(
                "demo", None, project=project, image=ImageLaneRequest(generation_type=generation_type)
            )
            return await _send(ctx, tmp_path, refs=generation_type == "i2i")

        assert await _sent("1K", " low ") == {"size": "1008x1792", "quality": "low"}
        assert await _sent("1K", "high") == {"size": "1008x1792", "quality": "high"}
        assert await _sent("2K", "low") == {"size": "1440x2560", "quality": "low"}
        assert await _sent("1K", "LOW") == {"size": "1008x1792", "quality": "LOW"}
        assert await _sent("512px", "medium") == {"size": "576x1024", "quality": "medium"}

    async def test_stored_quality_is_ignored_without_options(
        self, patched_session_factory, project_env, monkeypatch, tmp_path
    ):
        """条目未收录画质取值：存值不生效，``quality`` 按档位推出（1K → medium）。"""
        project = {
            "image_provider_t2i": "openai/gpt-image-2",
            "model_settings": {"openai/gpt-image-2": {"resolution": "1K", "quality": "low"}},
        }
        ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())

        assert ctx.image.quality is None
        assert await _send(ctx, tmp_path, refs=False) == {
            "size": "1008x1792",
            "quality": "medium",
        }

    async def test_unselected_quality_keeps_the_tier_mapping(
        self, patched_session_factory, project_env, monkeypatch, tmp_path
    ):
        _with_qualities(monkeypatch)
        project = {
            "image_provider_t2i": "openai/gpt-image-2",
            "model_settings": {"openai/gpt-image-2": {"resolution": "2K"}},
        }
        ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())

        assert ctx.image.quality is None
        assert await _send(ctx, tmp_path, refs=False) == {
            "size": "1440x2560",
            "quality": "high",
        }

    @pytest.mark.parametrize(
        ("endpoint", "paths"),
        [
            ("openai-images", {"generate": True, "edit": True}),
            ("openai-images-generations", {"generate": True, "edit": False}),
            ("openai-images-edits", {"generate": False, "edit": True}),
        ],
    )
    async def test_custom_openai_images_endpoints_send_the_stored_quality(
        self, patched_session_factory, project_env, monkeypatch, tmp_path, endpoint, paths
    ):
        _with_qualities(monkeypatch)
        provider_id = await _seed_custom_image_provider(
            patched_session_factory, [{"model_id": "gpt-image-2", "endpoint": endpoint, "is_default": True}]
        )
        key = f"{provider_id}/gpt-image-2"

        async def _sent(resolution: str, quality: str, *, refs: bool) -> dict:
            generation_context.reset_backend_cache_for_tests()
            project = {
                "image_provider_t2i": key,
                "image_provider_i2i": key,
                "model_settings": {key: {"resolution": resolution, "quality": quality}},
            }
            ctx = await resolve_generation_context(
                "demo", None, project=project, image=ImageLaneRequest(generation_type="i2i" if refs else "t2i")
            )
            return await _send(ctx, tmp_path, refs=refs, base_url="https://relay.test/v1")

        for refs, supported in ((False, paths["generate"]), (True, paths["edit"])):
            if not supported:
                with pytest.raises(ImageBucketCapabilityError):
                    await _sent("1K", "low", refs=refs)
                continue
            assert await _sent("1K", "low", refs=refs) == {"size": "1008x1792", "quality": "low"}
            assert await _sent("1K", "high", refs=refs) == {"size": "1008x1792", "quality": "high"}
            assert await _sent("2K", "low", refs=refs) == {"size": "1440x2560", "quality": "low"}

    async def test_openrouter_endpoint_offers_no_quality_choice(
        self, patched_session_factory, project_env, monkeypatch
    ):
        """OpenRouter 调用通道不接受显式画质：条目收录了画质取值、项目存了画质，lane 也不带画质。"""
        _with_qualities(monkeypatch)
        provider_id = await _seed_custom_image_provider(
            patched_session_factory, [{"model_id": "gpt-image-2", "endpoint": "openrouter-images", "is_default": True}]
        )
        key = f"{provider_id}/gpt-image-2"
        project = {"image_provider_t2i": key, "model_settings": {key: {"resolution": "1K", "quality": "low"}}}

        with capture_http() as router:
            router.get("https://relay.test/v1/images/models").respond(json={"data": []})
            ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())

        assert (ctx.image.resolution, ctx.image.quality) == ("1K", None)

    async def test_channel_without_explicit_quality_ignores_the_stored_value(
        self, patched_session_factory, project_env, monkeypatch
    ):
        """非 OpenAI 兼容的调用通道：条目收录了画质取值、项目存了画质，lane 也不带画质，请求不含它。"""
        install_catalog(
            monkeypatch, with_entry_facts(catalog.factory_snapshot(), "vidu", "viduq2", qualities=_QUALITIES)
        )
        project = {"image_provider_t2i": "vidu/viduq2", "model_settings": {"vidu/viduq2": {"quality": "low"}}}

        ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())

        assert ctx.image.quality is None


async def test_video_execution_reads_one_configuration_through_provider_submission(
    patched_session_factory, project_env, tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock, Mock

    async with patched_session_factory() as session:
        await ConfigService(session).set_provider_config("grok", "api_key", "local")
        await ConfigService(session).set_setting("default_video_backend", "grok/grok-imagine-video")
        await ConfigService(session).set_setting("video_generate_audio", "false")
        await session.commit()

    model = "grok-imagine-video"
    asset = tmp_path / "frame.png"
    Image.new("RGB", (64, 64)).save(asset)
    client = Mock()
    client.video.generate = AsyncMock(return_value=Mock(url="https://local.test/video.mp4", duration=5))
    monkeypatch.setattr("xai_sdk.AsyncClient", lambda **kwargs: client)

    configs, views = [], []
    global_settings = AsyncMock(side_effect=ConfigService.get_all_settings)

    async def settings(service):
        return await global_settings(service)

    monkeypatch.setattr(ConfigService, "get_all_settings", settings)
    read_config = ConfigService.get_provider_config
    read_view = catalog.callable_model_view

    async def config(service, provider):
        configs.append(provider)
        return await read_config(service, provider)

    def view(provider, model, *args, **kwargs):
        views.append((provider, model))
        return read_view(provider, model, *args, **kwargs)

    def unexpected(*args, **kwargs):
        raise AssertionError("execution queried an already resolved video capability or preference")

    monkeypatch.setattr(ConfigService, "get_provider_config", config)
    monkeypatch.setattr(catalog, "callable_model_view", view)
    for method in (
        "reference_payload_limits",
        "video_generate_audio",
    ):
        monkeypatch.setattr(ConfigResolver, method, unexpected)
    monkeypatch.setattr("lib.backends.backend_assembly.specs.builtin_request_video_capabilities", unexpected)
    project = {
        "model_settings": {f"grok/{model}": {"resolution": "720p"}},
    }
    ctx = await resolve_generation_context("demo", None, project=project, video=VideoLaneRequest(route="storyboard"))
    facts = ctx.video.request_facts
    assert isinstance(facts, VideoRequestFacts)
    assert facts.resolution == "720p"
    assert not facts.requested_generate_audio
    with capture_http() as http:
        http.get("https://local.test/video.mp4").respond(content=b"finished")
        path, *_ = await ctx.generator.generate_video_async(
            "p",
            "videos",
            "E1S01",
            start_image=asset,
            duration_seconds=5,
            resolution=facts.resolution,
            generate_audio=facts.requested_generate_audio,
        )
    assert path.read_bytes() == b"finished"
    sent = client.video.generate.await_args.kwargs
    assert sent["resolution"] == "720p"
    assert sent["generate_audio"] is False
    assert configs == ["grok"]
    assert views == [("grok", model)]
    global_settings.assert_awaited_once()

    with pytest.raises(VideoCapabilityError, match="video_reference_images_with_frames_unsupported"):
        await ctx.generator.generate_video_async("p", "videos", "E1S02", start_image=asset, reference_images=[asset])
    with pytest.raises(VideoCapabilityError, match="video_reference_images_unsupported"):
        await ctx.generator.generate_video_async("p", "videos", "E1S02", resolution="1080p", reference_images=[asset])
    client.video.generate.assert_awaited_once()


@pytest.mark.parametrize(("provider", "endpoint"), [("openai", "openai-video"), ("agnes", "agnes-video-2-5")])
async def test_unlisted_model_keeps_frozen_entry_through_video_submission(
    patched_session_factory, project_env, monkeypatch, provider, endpoint
):
    import httpx
    from openai import BadRequestError

    model = "unlisted-video"
    catalog.install_user_added_models(
        (catalog.UserAddedModel(provider, model, "video", endpoint=endpoint, fallback_durations=(4,)),)
    )
    try:
        async with patched_session_factory.begin() as session:
            await ConfigService(session).set_provider_config(provider, "api_key", "local")
        ctx = await resolve_generation_context(
            "demo", None, project={"video_backend": f"{provider}/{model}"}, video=VideoLaneRequest(route="storyboard")
        )
        assert isinstance(ctx.video.request_facts, VideoRequestFacts)

        def unexpected(*args, **kwargs):
            pytest.fail("provider adapter queried a frozen missing catalog entry")

        monkeypatch.setattr(catalog, "find_entry", unexpected)
        with capture_http() as http:
            submit = http.post(path="/v1/videos").respond(400, json={"error": {"message": "mock rejection"}})
            with pytest.raises((httpx.HTTPStatusError, BadRequestError)):
                await ctx.generator.generate_video_async(
                    "prompt", "videos", "E1S01", duration_seconds=4, generate_audio=True
                )
            assert submit.call_count == 1
    finally:
        catalog.install_user_added_models(())
