"""TTS 骨架跨层单测：路径/版本化/白名单/导出 + GeneratedAssets 字段 + generate_audio_async +
用量聚合 audio_count + worker audio lane 路由。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.audio_backends.base import AudioCapability, AudioSynthesisResult
from lib.data_validator import DataValidator
from lib.db.base import Base
from lib.db.repositories.usage_repo import UsageRepository
from lib.generation_worker import GenerationWorker, ProviderPool
from lib.media_generator import MediaGenerator
from lib.resource_paths import RESOURCE_TYPES, resource_extension, resource_relative_path
from lib.script_models import GeneratedAssets
from lib.version_manager import VersionManager


class TestResourcePaths:
    def test_audio_relative_path(self):
        assert resource_relative_path("audio", "E1S01") == "audio/segment_E1S01.wav"

    def test_audio_registered(self):
        assert "audio" in RESOURCE_TYPES
        assert resource_extension("audio") == ".wav"

    def test_existing_prefixes_unchanged(self):
        assert resource_relative_path("storyboards", "E1S01") == "storyboards/scene_E1S01.png"
        assert resource_relative_path("characters", "Alice") == "characters/Alice.png"


class TestVersionManagerAudio:
    def test_audio_in_resource_types(self):
        assert "audio" in VersionManager.RESOURCE_TYPES
        assert VersionManager.EXTENSIONS["audio"] == ".wav"

    def test_ensure_dirs_creates_audio(self, tmp_path: Path):
        VersionManager(tmp_path)
        assert (tmp_path / "versions" / "audio").is_dir()


class TestWhitelistAndExport:
    def test_audio_allowed_root_entry(self):
        assert "audio" in DataValidator.ALLOWED_ROOT_ENTRIES

    def test_audio_in_version_history_dirs(self):
        from server.services.project_archive import ProjectArchiveService

        assert "audio" in ProjectArchiveService._VERSION_HISTORY_DIRS


class TestGeneratedAssetsNarrationAudio:
    def test_default_none(self):
        assert GeneratedAssets().narration_audio is None

    def test_roundtrip(self):
        ga = GeneratedAssets(narration_audio="audio/segment_E1S01.wav")
        assert ga.narration_audio == "audio/segment_E1S01.wav"
        # extra="forbid" 下仍可序列化/反序列化往返
        restored = GeneratedAssets.model_validate(ga.model_dump())
        assert restored.narration_audio == "audio/segment_E1S01.wav"


# ── generate_audio_async ──────────────────────────────────────────────────────


class _FakeAudioBackend:
    name = "fake-audio"
    model = "tts-model"
    capabilities = {AudioCapability.TEXT_TO_SPEECH}

    def __init__(self):
        self.calls = []

    async def synthesize(self, request):
        self.calls.append(request)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"RIFFfakewav")
        return AudioSynthesisResult(
            provider=self.name, model=self.model, characters=len(request.text), output_path=request.output_path
        )


class _FakeVersions:
    def __init__(self):
        self.add_calls = []

    def ensure_current_tracked(self, **kwargs):
        pass

    def add_version(self, **kwargs):
        self.add_calls.append(kwargs)
        return len(self.add_calls)


class _FakeUsage:
    def __init__(self):
        self.started = []
        self.finished = []

    async def start_call(self, **kwargs):
        self.started.append(kwargs)
        return len(self.started)

    async def finish_call(self, **kwargs):
        self.finished.append(kwargs)


def _build_generator(tmp_path: Path) -> MediaGenerator:
    gen = object.__new__(MediaGenerator)
    gen.project_path = tmp_path / "projects" / "demo"
    gen.project_path.mkdir(parents=True, exist_ok=True)
    gen.project_name = "demo"
    gen._rate_limiter = None
    gen._image_backend = None
    gen._video_backend = None
    gen._audio_backend = _FakeAudioBackend()
    gen._user_id = "default"
    gen._config = None
    gen.versions = _FakeVersions()
    gen.usage_tracker = _FakeUsage()
    return gen


class TestGenerateAudioAsync:
    async def test_success(self, tmp_path: Path):
        gen = _build_generator(tmp_path)
        output_path, version = await gen.generate_audio_async(text="你好世界", resource_id="E1S01", voice="Cherry")
        assert output_path.name == "segment_E1S01.wav"
        assert output_path.read_bytes() == b"RIFFfakewav"
        assert version == 1
        # start_call 用 call_type=audio + 字符数承载在 finish_call.usage_tokens
        assert gen.usage_tracker.started[0]["call_type"] == "audio"
        assert gen.usage_tracker.started[0]["model"] == "tts-model"
        assert gen.usage_tracker.finished[0]["status"] == "success"
        assert gen.usage_tracker.finished[0]["usage_tokens"] == len("你好世界")
        assert gen.versions.add_calls[0]["resource_type"] == "audio"

    async def test_backend_failure_marks_failed(self, tmp_path: Path):
        gen = _build_generator(tmp_path)

        async def _raise(request):
            raise RuntimeError("boom")

        gen._audio_backend.synthesize = _raise
        try:
            await gen.generate_audio_async(text="x", resource_id="E1S02", voice="Cherry")
            raised = False
        except RuntimeError:
            raised = True
        assert raised
        assert gen.usage_tracker.finished[-1]["status"] == "failed"

    async def test_no_backend_raises(self, tmp_path: Path):
        gen = _build_generator(tmp_path)
        gen._audio_backend = None
        try:
            await gen.generate_audio_async(text="x", resource_id="E1S03", voice="Cherry")
            raised = False
        except RuntimeError:
            raised = True
        assert raised


# ── 用量聚合 audio_count ────────────────────────────────────────────────────────


class TestUsageStatsAudioCount:
    async def test_audio_count(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                repo = UsageRepository(session)
                call_id = await repo.start_call(
                    project_name="demo", call_type="audio", model="qwen3-tts-flash", provider="dashscope"
                )
                await repo.finish_call(call_id, status="success", usage_tokens=1500)
                stats = await repo.get_stats(project_name="demo")
                assert stats["audio_count"] == 1
                # audio 按字符冻结成本快照（非 0）
                assert stats["cost_by_currency"].get("CNY", 0) > 0
        finally:
            await engine.dispose()


# ── worker audio lane ───────────────────────────────────────────────────────────


class TestWorkerAudioLane:
    def test_provider_pool_audio_room(self):
        pool = ProviderPool(provider_id="dashscope", image_max=0, video_max=0, audio_max=2)
        assert pool.has_audio_room()
        pool.audio_inflight["a"] = object()  # type: ignore[assignment]
        pool.audio_inflight["b"] = object()  # type: ignore[assignment]
        assert not pool.has_audio_room()

    def test_audio_max_zero_no_room(self):
        pool = ProviderPool(provider_id="x", image_max=1, video_max=1, audio_max=0)
        assert not pool.has_audio_room()

    def test_pool_full_providers_audio(self):
        w = GenerationWorker.__new__(GenerationWorker)
        full = ProviderPool(provider_id="dashscope", image_max=0, video_max=0, audio_max=1)
        full.audio_inflight["t"] = object()  # type: ignore[assignment]
        w._pools = {"dashscope": full}
        assert w._pool_full_providers("audio") == frozenset({"dashscope"})
        assert w._any_pool_has_room("audio") is False

    async def test_claim_routes_audio_to_audio_lane(self, monkeypatch):
        from lib import generation_worker as gw

        class _Q:
            def __init__(self):
                self._given = False

            async def claim_next_task(self, media_type, pool_full_providers=None):
                if media_type == "audio" and not self._given:
                    self._given = True
                    return {
                        "task_id": "T1",
                        "task_type": "tts",
                        "media_type": "audio",
                        "project_name": "demo",
                        "payload": {},
                    }
                return None

        w = GenerationWorker.__new__(GenerationWorker)
        w.queue = _Q()
        w._pools = {"dashscope": ProviderPool(provider_id="dashscope", image_max=0, video_max=0, audio_max=2)}

        async def _fake_extract(task):
            return "dashscope"

        monkeypatch.setattr(gw, "_extract_provider", _fake_extract)

        async def _fake_process(task):
            await asyncio.sleep(0)

        w._process_task = _fake_process  # type: ignore[method-assign]

        claimed = await w._claim_tasks()
        assert claimed is True
        pool = w._pools["dashscope"]
        assert "T1" in pool.audio_inflight
        await asyncio.gather(*pool.audio_inflight.values())
