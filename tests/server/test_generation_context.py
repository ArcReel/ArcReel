"""resolve_generation_context 公开接口测试：lane 声明与跳过 / fail-loud property /
按实际身份查 resolution 与能力 / 能力查询降级空值 / 原子失败 / backend 缓存与失效。

按 ADR 0049 的测试口径：真实内存 DB + tmp_path 真 ProjectManager + fake backend
（仅替换 assemble_backend 构造缝），不 mock ConfigResolver / ProjectManager，不断言私有属性。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ProviderModel, get_provider_fallback
from lib.custom_provider import make_provider_id
from lib.db.base import Base
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.media_generator import MediaGenerator
from lib.project_manager import ProjectManager
from server.services import generation_context
from server.services.generation_context import (
    AudioLaneRequest,
    AudioLaneResult,
    GenerationContext,
    ImageLaneRequest,
    ImageLaneResult,
    VideoLaneRequest,
    VideoLaneResult,
    resolve_generation_context,
)


def _registry_video_model(provider_id: str) -> str:
    """从 registry 取该 provider 的任一视频 model id，避免硬编码供应商数据。"""
    meta = PROVIDER_REGISTRY[provider_id]
    return next(mid for mid, mi in meta.models.items() if mi.media_type == "video")


@dataclass
class _FakeBackend:
    name: str
    model: str


@pytest.fixture
async def session_factory(monkeypatch):
    """真实内存 DB：建全部 ORM 表，并把 lib.db.async_session_factory 指向它。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("lib.db.async_session_factory", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def project_env(monkeypatch, tmp_path: Path):
    """tmp_path 下的真 ProjectManager + 已存在的项目目录。"""
    pm = ProjectManager(tmp_path / "projects")
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    monkeypatch.setattr(generation_context, "get_project_manager", lambda: pm)
    return pm


@pytest.fixture(autouse=True)
def _clean_backend_cache():
    generation_context.invalidate_backend_cache()
    yield
    generation_context.invalidate_backend_cache()


@pytest.fixture
def fake_assemble(monkeypatch):
    """替换 backend 构造缝：默认按请求原样回声身份，记录每次构造。"""
    calls: list[tuple[str, str, str | None]] = []

    async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
        calls.append((provider_id, media_type, model_id))
        return _FakeBackend(name=provider_id, model=model_id or "default-model")

    monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
    return calls


async def _seed_custom_video_provider(session_factory) -> str:
    """种一个自定义供应商：目标 model 已禁用、默认 model 存活（带 resolution 与时长表）。"""
    async with session_factory() as session:
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
                supported_durations=json.dumps([4, 6, 8]),
            )
        )
        await session.commit()
        return make_provider_id(provider.id)


class TestLaneDeclaration:
    async def test_only_declared_lanes_are_constructed(self, session_factory, project_env, fake_assemble):
        """只声明 image lane：不解析、不构造 video/audio，视频供应商缺配置不影响图片任务。"""
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

    async def test_no_lane_declared_still_returns_generator(self, session_factory, project_env, fake_assemble):
        ctx = await resolve_generation_context("demo", None, project={})
        assert isinstance(ctx.generator, MediaGenerator)
        assert fake_assemble == []
        with pytest.raises(RuntimeError, match="image lane 未声明"):
            _ = ctx.image

    async def test_i2i_capability_selects_i2i_slot(self, session_factory, project_env, fake_assemble):
        project = {
            "image_provider_t2i": "ark/img-t2i",
            "image_provider_i2i": "ark/img-i2i",
        }
        ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest(capability="i2i"))
        assert ctx.image.provider_model == ProviderModel("ark", "img-i2i")

    async def test_all_three_lanes(self, session_factory, project_env, fake_assemble):
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
    async def test_registry_capabilities_and_fallback_resolution(self, session_factory, project_env, fake_assemble):
        video_model = _registry_video_model("ark")
        expected = PROVIDER_REGISTRY["ark"].models[video_model]
        ctx = await resolve_generation_context(
            "demo", None, project={"video_backend": f"ark/{video_model}"}, video=VideoLaneRequest()
        )

        assert ctx.video.supported_durations == tuple(expected.supported_durations or [])
        assert ctx.video.max_duration == max(expected.supported_durations or [0])
        assert ctx.video.max_reference_images == expected.max_reference_images
        assert ctx.video.resolution is None
        assert ctx.video.resolution_or_fallback == get_provider_fallback("ark")

    async def test_resolution_from_model_settings(self, session_factory, project_env, fake_assemble):
        video_model = _registry_video_model("ark")
        project = {
            "video_backend": f"ark/{video_model}",
            "model_settings": {f"ark/{video_model}": {"resolution": "480p"}},
        }
        ctx = await resolve_generation_context("demo", None, project=project, video=VideoLaneRequest())
        assert ctx.video.resolution == "480p"
        assert ctx.video.resolution_or_fallback == "480p"

    async def test_capability_query_failure_degrades_to_empty(self, session_factory, project_env, monkeypatch):
        """fake backend 报告 registry 之外的 model：能力查询失败降级空值，整次调用照常成功。"""

        async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            return _FakeBackend(name=provider_id, model="mystery-model")

        monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
        video_model = _registry_video_model("ark")
        ctx = await resolve_generation_context(
            "demo", None, project={"video_backend": f"ark/{video_model}"}, video=VideoLaneRequest()
        )
        assert ctx.video.supported_durations == ()
        assert ctx.video.max_duration is None
        assert ctx.video.max_reference_images is None
        assert ctx.video.backend_model == "mystery-model"

    async def test_payload_overrides_project(self, session_factory, project_env, fake_assemble):
        """payload > project：历史任务携带的 video_provider 决定实际解析身份。"""
        ark_model = _registry_video_model("ark")
        grok_model = _registry_video_model("grok")
        ctx = await resolve_generation_context(
            "demo",
            {"video_provider": "grok", "video_model": grok_model},
            project={"video_backend": f"ark/{ark_model}"},
            video=VideoLaneRequest(),
        )
        assert ctx.video.provider_model == ProviderModel("grok", grok_model)


class TestActualIdentityQueries:
    async def test_custom_model_fallback_queries_by_actual_model(self, session_factory, project_env, monkeypatch):
        """自定义供应商目标 model 被禁用回退：resolution 与能力按 backend 实际 model 查询。"""
        provider_id = await _seed_custom_video_provider(session_factory)

        async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            # 模拟 load_custom_backend 的回退：请求 m-dead，实际构造出默认启用的 m-live
            return _FakeBackend(name=provider_id, model="m-live")

        monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
        ctx = await resolve_generation_context(
            "demo", None, project={"video_backend": f"{provider_id}/m-dead"}, video=VideoLaneRequest()
        )

        assert ctx.video.provider_model == ProviderModel(provider_id, "m-dead")
        assert ctx.video.backend_model == "m-live"
        # resolution 命中 m-live（实际身份）的 DB 默认，而非按解析意图 m-dead 落空
        assert ctx.video.resolution == "540p"
        # 能力同样按 m-live 查询
        assert ctx.video.supported_durations == (4, 6, 8)
        assert ctx.video.max_duration == 8
        assert ctx.video.max_reference_images == 0


class TestAudioLane:
    async def test_narration_voice_and_speed_from_project(self, session_factory, project_env, fake_assemble):
        project = {
            "audio_backend": "dashscope/tts-model-x",
            "narration_voice": "Cherry",
            "narration_speed": 1.25,
        }
        ctx = await resolve_generation_context("demo", None, project=project, audio=AudioLaneRequest())
        assert ctx.audio.narration_voice == "Cherry"
        assert ctx.audio.narration_speed == 1.25
        assert ctx.audio.backend_name == "dashscope"
        assert ctx.audio.backend_model == "tts-model-x"

    async def test_narration_defaults_when_unset(self, session_factory, project_env, fake_assemble):
        project = {"audio_backend": "dashscope/tts-model-x"}
        ctx = await resolve_generation_context("demo", None, project=project, audio=AudioLaneRequest())
        assert isinstance(ctx.audio.narration_voice, str) and ctx.audio.narration_voice
        assert ctx.audio.narration_speed is None


class TestAtomicFailure:
    async def test_declared_lane_construction_failure_fails_whole_call(self, session_factory, project_env, monkeypatch):
        """image 成功后 video 构造失败：整次调用原样上抛，无部分结果。"""

        async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            if media_type == "video":
                raise ValueError("video backend 构造失败")
            return _FakeBackend(name=provider_id, model=model_id or "default-model")

        monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
        video_model = _registry_video_model("ark")
        with pytest.raises(ValueError, match="video backend 构造失败"):
            await resolve_generation_context(
                "demo",
                None,
                project={"image_provider_t2i": "ark/img-model-x", "video_backend": f"ark/{video_model}"},
                image=ImageLaneRequest(),
                video=VideoLaneRequest(),
            )

    async def test_missing_project_dir_raises(self, session_factory, project_env, fake_assemble):
        with pytest.raises(FileNotFoundError):
            await resolve_generation_context("nope", None, project={}, image=ImageLaneRequest())


class TestBackendCache:
    async def test_backend_reused_until_invalidated(self, session_factory, project_env, fake_assemble):
        project = {"image_provider_t2i": "ark/img-model-x"}
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        assert len(fake_assemble) == 1, "第二次调用须命中缓存，不再重建 backend"

        generation_context.invalidate_backend_cache()
        await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest())
        assert len(fake_assemble) == 2, "失效后须重建 backend"


class TestValueObjectAssembly:
    def test_fake_context_assembles_from_frozen_dataclasses(self, tmp_path: Path):
        """消费方测试的拼装路径：frozen dataclass 直接构造假 context，property 原样返回。"""
        lane = VideoLaneResult(
            provider_model=ProviderModel("ark", "m"),
            backend_name="ark",
            backend_model="m",
            resolution=None,
            resolution_or_fallback="720p",
            supported_durations=(4, 8),
            max_duration=8,
            max_reference_images=9,
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
        )
        with pytest.raises(AttributeError):
            lane.resolution = "720p"  # type: ignore[misc]

    def test_audio_lane_result_shape(self):
        lane = AudioLaneResult(
            provider_model=ProviderModel("dashscope", "tts"),
            backend_name="dashscope",
            backend_model="tts",
            narration_voice="Cherry",
            narration_speed=None,
        )
        assert lane.narration_voice == "Cherry"
