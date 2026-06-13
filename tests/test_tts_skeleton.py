"""TTS 骨架跨层单测：路径/版本化/白名单/导出 + GeneratedAssets 字段 + generate_audio_async +
用量聚合 audio_count + worker audio lane 路由。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.audio_backends.base import AudioCapability, AudioSynthesisResult
from lib.data_validator import DataValidator
from lib.db.base import Base
from lib.db.repositories.usage_repo import UsageRepository
from lib.generation_worker import CapacityTable, GenerationWorker, SlotTable
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
        with pytest.raises(RuntimeError):
            await gen.generate_audio_async(text="x", resource_id="E1S02", voice="Cherry")
        assert gen.usage_tracker.finished[-1]["status"] == "failed"

    async def test_no_backend_raises(self, tmp_path: Path):
        gen = _build_generator(tmp_path)
        gen._audio_backend = None
        with pytest.raises(RuntimeError):
            await gen.generate_audio_async(text="x", resource_id="E1S03", voice="Cherry")

    async def test_regenerate_tracks_existing_file(self, tmp_path: Path):
        # 重新生成时旧文件须先经 ensure_current_tracked 记录进版本历史
        gen = _build_generator(tmp_path)
        tracked = []
        gen.versions.ensure_current_tracked = lambda **kw: tracked.append(kw)
        out1, _ = await gen.generate_audio_async(text="第一次", resource_id="E1S05", voice="Cherry")
        assert out1.exists()
        assert tracked == []
        await gen.generate_audio_async(text="第二次", resource_id="E1S05", voice="Cherry")
        assert tracked and tracked[0]["resource_type"] == "audio"


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
    def test_lane_limits_audio_projection(self):
        # 支持 audio 的 provider 投影出上限，不支持的 lane → 0
        assert CapacityTable._lane_limits({"audio"}, 5, 3, 10) == {"image": 0, "video": 0, "audio": 10}
        assert CapacityTable._lane_limits({"image", "video"}, 5, 3, 10)["audio"] == 0

    async def test_audio_room_via_slot_table(self):
        slots = SlotTable()
        dummy = asyncio.get_running_loop().create_future()
        dummy.set_result(None)
        assert slots.has_room("dashscope", "audio", 2)
        slots.register("dashscope", "audio", "a", dummy)
        slots.register("dashscope", "audio", "b", dummy)
        assert not slots.has_room("dashscope", "audio", 2)
        # cap=0（provider 不支持 audio）始终无空位
        assert not slots.has_room("x", "audio", 0)

    async def test_pool_full_providers_audio(self):
        class _Q:
            async def claim_next_task(self, media_type, **_kwargs):
                return None

        w = GenerationWorker(
            queue=_Q(),  # type: ignore[arg-type]
            capacity=CapacityTable(
                _limits={"dashscope": {"image": 0, "video": 0, "audio": 1}},
                _defaults={"image": 5, "video": 3, "audio": 10},
            ),
        )
        dummy = asyncio.get_running_loop().create_future()
        dummy.set_result(None)
        w._slots.register("dashscope", "audio", "t", dummy)
        assert w._pool_full_providers("audio") == frozenset({"dashscope"})

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

        w = GenerationWorker(
            queue=_Q(),  # type: ignore[arg-type]
            capacity=CapacityTable(
                _limits={"dashscope": {"image": 0, "video": 0, "audio": 2}},
                _defaults={"image": 5, "video": 3, "audio": 10},
            ),
        )

        async def _fake_extract(task):
            return "dashscope"

        monkeypatch.setattr(gw, "_extract_provider", _fake_extract)

        async def _fake_process(task):
            await asyncio.sleep(0)

        w._process_task = _fake_process  # type: ignore[method-assign]

        claimed = await w._claim_tasks()
        assert claimed is True
        assert w._slots.occupied("dashscope", "audio") == 1
        assert w._slots.find_by_task("T1") is not None
        await asyncio.gather(*w._slots.all_active_tasks(), return_exceptions=True)


class TestExtractProviderAudio:
    async def test_audio_payload_provider_routes_to_audio_resolver(self):
        from lib.generation_worker import _extract_provider

        # payload 携带历史 audio_provider → audio lane 投影短路取到
        task = {
            "payload": {"audio_provider": "dashscope", "audio_model": "qwen3-tts-flash"},
            "task_type": "tts",
        }
        assert await _extract_provider(task) == "dashscope"


class TestOrphanAudioRestartLost:
    async def test_orphan_audio_running_marked_restart_lost(self):
        # audio 同步无 resume 入口：running 孤儿降级 [restart_lost]，不重新提交以免重复计费
        class _Q:
            def __init__(self):
                self.failed = []
                self.cancelled = []

            async def list_orphan_tasks_on_start(self):
                return [
                    {
                        "task_id": "A1",
                        "status": "running",
                        "task_type": "tts",
                        "media_type": None,
                        "payload": {},
                    }
                ]

            async def mark_task_failed(self, task_id, error):
                self.failed.append((task_id, error))
                return 1

            async def mark_task_cancelled(self, task_id, cancelled_by="user"):
                self.cancelled.append(task_id)

        q = _Q()
        w = GenerationWorker(
            queue=q,  # type: ignore[arg-type]
            capacity=CapacityTable(_limits={}, _defaults={"image": 5, "video": 3, "audio": 10}),
        )
        await w._handle_orphan_tasks_on_start()
        assert q.failed == [("A1", "[restart_lost_audio]")]
        assert q.cancelled == []


class TestDeriveProviderIdForEnqueueAudio:
    async def test_tts_routes_to_audio_resolver(self, monkeypatch):
        from lib import generation_queue as gq
        from lib.config.resolver import ProviderModel

        class _FakeResolver:
            def __init__(self, factory):
                pass

            async def resolve_audio_backend(self, project, payload):
                return ProviderModel("dashscope", "qwen3-tts-flash")

        monkeypatch.setattr("lib.config.resolver.ConfigResolver", _FakeResolver)
        pid = await gq._derive_provider_id_for_enqueue(
            project_name=None, payload={}, task_type="tts", media_type="audio"
        )
        assert pid == "dashscope"
