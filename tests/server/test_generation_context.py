"""resolve_generation_context 公开接口测试：lane 声明与跳过 / fail-loud property /
按实际身份查 resolution 与能力 / 能力查询降级空值 / 原子失败 / backend 缓存与失效。

按 ADR 0049 的测试口径：真实内存 DB + tmp_path 真 ProjectManager + fake backend
（仅替换 assemble_backend 构造缝），不 mock ConfigResolver / ProjectManager，不断言私有属性。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver, ProviderModel, get_provider_fallback
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
    """除清空缓存条目外，同时清空 per-key 锁。

    ``invalidate_backend_cache()`` 按设计只清条目、不清 ``_locks``（生产环境同一事件循环
    贯穿进程生命周期，key 空间有界，不清理无泄漏风险，见 ``_BackendCache`` 类文档）。但
    pytest-asyncio 按测试函数切换独立事件循环，跨测试复用同一缓存 key 时，若前一个测试
    已触发过锁竞争（``asyncio.Lock`` 首次竞争时会绑定到当时的事件循环），该 Lock 实例会
    永久绑定在已关闭的旧循环上，后续测试里再次发生竞争即抛
    ``RuntimeError: ... is bound to a different event loop``。测试隔离清空 locks 不影响
    被测生产行为。
    """
    generation_context.invalidate_backend_cache()
    generation_context._backend_cache._locks.clear()
    yield
    generation_context.invalidate_backend_cache()
    generation_context._backend_cache._locks.clear()


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

    async def test_invalidate_during_construction_discards_instance(self, monkeypatch):
        """构造中途（assemble_backend await 挂起期间）触发 invalidate：完成的实例不写回缓存。"""
        entered = asyncio.Event()
        release = asyncio.Event()
        built: list[_FakeBackend] = []

        async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            backend = _FakeBackend(name=provider_id, model=model_id or "default-model")
            built.append(backend)
            if len(built) == 1:
                entered.set()
                await release.wait()
            return backend

        monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
        resolver = cast(ConfigResolver, None)

        task = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        await entered.wait()
        generation_context.invalidate_backend_cache()
        release.set()
        stale = await task

        fresh = await generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver)
        assert len(built) == 2, "缓存中不得残留失效期间构造的实例，后续访问须重新构造"
        assert stale is built[0] and fresh is built[1]
        assert fresh is not stale

    async def test_concurrent_same_key_constructs_once(self, monkeypatch):
        """同 key 并发两次 get_or_create：只构造一次，两调用方拿到同一实例。"""
        entered = asyncio.Event()
        release = asyncio.Event()
        construct_count = 0

        async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            nonlocal construct_count
            construct_count += 1
            entered.set()
            await release.wait()
            return _FakeBackend(name=provider_id, model=model_id or "default-model")

        monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
        resolver = cast(ConfigResolver, None)

        t1 = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        t2 = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        await entered.wait()
        # 让 t2 在 t1 构造挂起期间也进入 get_or_create（并发 miss 而非先后命中）
        for _ in range(3):
            await asyncio.sleep(0)
        release.set()
        b1, b2 = await asyncio.gather(t1, t2)

        assert construct_count == 1, "同 key 并发 miss 须 single-flight，只构造一次"
        assert b1 is b2

    async def test_follower_queued_before_invalidate_discards_instance(self, monkeypatch):
        """失效边界前已排队等锁的旧代际请求（follower）：跨越失效后拿到锁，仍不得写回缓存。

        leader 持锁构造中途被打断（已有测试覆盖）之外的第三种交错：follower 在 leader 构造期间、
        invalidate() 之前就已进入 get_or_create 并排队等锁，只有在 invalidate() 之后才轮到它拿锁。
        它的调用参数（factory 闭包）仍是失效前的旧配置，因此即使拿锁时看到的是新代数，也必须按
        「进入时（等锁前）捕获的旧代数」判定为过期，不写回缓存——否则旧配置构造的 backend 会被
        误标为新代际有效实例，污染后续同 key 请求。
        """
        entered = asyncio.Event()
        release = asyncio.Event()
        built: list[_FakeBackend] = []

        async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            backend = _FakeBackend(name=provider_id, model=model_id or "default-model")
            built.append(backend)
            if len(built) == 1:
                entered.set()
                await release.wait()
            return backend

        monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
        resolver = cast(ConfigResolver, None)

        leader = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        await entered.wait()  # leader 已持锁，正在构造中途挂起

        follower = asyncio.create_task(generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver))
        # 让 follower 跑到「捕获代数 + 排队等锁」这一步（尚未轮到它拿锁）
        for _ in range(3):
            await asyncio.sleep(0)

        generation_context.invalidate_backend_cache()  # 失效边界：此时 follower 已排队，代数已翻篇
        release.set()  # 放行 leader，完成构造
        stale = await leader
        stale_from_follower = await follower

        assert stale is built[0] and stale_from_follower is built[1]
        assert stale is not stale_from_follower

        fresh = await generation_context._get_or_create_video_backend("ark", {"model": "m"}, resolver)
        assert len(built) == 3, "leader 与 follower 的旧代际实例均不得写回，后续访问须重新构造"
        assert fresh is built[2]
        assert fresh is not stale and fresh is not stale_from_follower


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
