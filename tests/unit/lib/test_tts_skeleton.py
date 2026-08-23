"""TTS 骨架跨层单测：路径/版本化/白名单/导出 + GeneratedAssets 字段 + generate_audio_async +
用量聚合 audio_count + worker audio lane 路由。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.audio_backends.base import AudioCapability, AudioSynthesisResult
from lib.data_validator import DataValidator
from lib.db.base import Base
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
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
        vm = VersionManager(tmp_path)
        # 目录由写路径按需创建，构造本身只读，不落盘
        assert not (tmp_path / "versions").exists()

        vm._ensure_dirs()
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
        self.ensure_calls = []

    def ensure_current_tracked(self, **kwargs):
        self.ensure_calls.append(kwargs)

    def add_version(self, **kwargs):
        self.add_calls.append(kwargs)
        return len(self.add_calls)

    def commit_staged_version(
        self,
        *,
        resource_type,
        resource_id,
        prompt,
        staged_file,
        current_file,
        on_commit=None,
        **metadata,
    ):
        if current_file.exists():
            self.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=current_file,
                prompt="",
            )
        staged_file.replace(current_file)
        version = self.add_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            source_file=current_file,
            **metadata,
        )
        if on_commit is not None:
            on_commit()
        return version


class _FakeLedgerCall:
    def __init__(self, call_id):
        self.call_id = call_id
        self.declared = False
        self.result = None

    def success(self, result):
        self.declared = True
        self.result = result


class _FakeLedger:
    """记账账本假实现：捕获记账括号入参与递交的 backend 结果对象（新主缝）。"""

    def __init__(self):
        self.started = []
        self.outcomes = []
        self._n = 0

    @asynccontextmanager
    async def record(self, **kwargs):
        self._n += 1
        self.started.append(kwargs)
        call = _FakeLedgerCall(self._n)
        try:
            yield call
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.outcomes.append({"status": "failed", "error": exc})
            raise
        else:
            if not call.declared:
                raise RuntimeError("ledger.record exited without success()")
            self.outcomes.append({"status": "success", "result": call.result})


def _build_generator(tmp_path: Path) -> MediaGenerator:
    gen = object.__new__(MediaGenerator)
    gen.project_path = tmp_path / "projects" / "demo"
    gen.project_path.mkdir(parents=True, exist_ok=True)
    gen.project_name = "demo"
    gen._rate_limiter = None
    gen._image_backend = None
    gen._video_backend = None
    gen._audio_backend = _FakeAudioBackend()
    gen._audio_provider_id = "fake-audio"
    gen._user_id = "default"
    gen._config = None
    gen.versions = _FakeVersions()
    gen.ledger = _FakeLedger()
    return gen


class TestGenerateAudioAsync:
    async def test_success(self, tmp_path: Path):
        gen = _build_generator(tmp_path)
        output_path, version = await gen.generate_audio_async(text="你好世界", resource_id="E1S01", voice="Cherry")
        assert output_path.name == "segment_E1S01.wav"
        assert output_path.read_bytes() == b"RIFFfakewav"
        assert gen._audio_backend.calls[0].output_path != output_path
        assert not gen._audio_backend.calls[0].output_path.exists()
        assert version == 1
        # 记账括号用 call_type=audio；合成字符数由真 Ledger union 分发从 result.characters 转写
        assert gen.ledger.started[0]["call_type"] == "audio"
        assert gen.ledger.started[0]["model"] == "tts-model"
        assert gen.ledger.outcomes[0]["status"] == "success"
        assert gen.ledger.outcomes[0]["result"].characters == len("你好世界")
        assert gen.versions.add_calls[0]["resource_type"] == "audio"

    async def test_before_submit_failure_prevents_audio_backend_call(self, tmp_path: Path):
        gen = _build_generator(tmp_path)

        async def _reject() -> None:
            raise ValueError("formal input is no longer admitted")

        with pytest.raises(ValueError, match="no longer admitted"):
            await gen.generate_audio_async(
                text="你好世界",
                resource_id="E1S01",
                voice="Cherry",
                before_submit=_reject,
            )

        assert gen._audio_backend.calls == []
        assert gen.ledger.outcomes[-1]["status"] == "failed"

    async def test_backend_failure_marks_failed(self, tmp_path: Path):
        gen = _build_generator(tmp_path)

        async def _raise(request):
            raise RuntimeError("boom")

        gen._audio_backend.synthesize = _raise
        with pytest.raises(RuntimeError):
            await gen.generate_audio_async(text="x", resource_id="E1S02", voice="Cherry")
        assert gen.ledger.outcomes[-1]["status"] == "failed"

    async def test_backend_failure_preserves_existing_formal_audio(self, tmp_path: Path):
        gen = _build_generator(tmp_path)
        formal = gen.project_path / "audio" / "segment_E1S04.wav"
        formal.parent.mkdir(parents=True)
        formal.write_bytes(b"paid-old-audio")

        async def _overwrite_then_raise(request):
            request.output_path.write_bytes(b"broken-new-audio")
            raise RuntimeError("boom")

        gen._audio_backend.synthesize = _overwrite_then_raise
        with pytest.raises(RuntimeError, match="boom"):
            await gen.generate_audio_async(text="new", resource_id="E1S04", voice="Cherry")

        assert formal.read_bytes() == b"paid-old-audio"
        assert list(formal.parent.iterdir()) == [formal]
        assert gen.versions.ensure_calls == []
        assert gen.versions.add_calls == []

    async def test_cancellation_preserves_existing_formal_audio(self, tmp_path: Path):
        gen = _build_generator(tmp_path)
        formal = gen.project_path / "audio" / "segment_E1S06.wav"
        formal.parent.mkdir(parents=True)
        formal.write_bytes(b"paid-old-audio")

        async def _overwrite_then_cancel(request):
            request.output_path.write_bytes(b"cancelled-new-audio")
            raise asyncio.CancelledError

        gen._audio_backend.synthesize = _overwrite_then_cancel
        with pytest.raises(asyncio.CancelledError):
            await gen.generate_audio_async(text="new", resource_id="E1S06", voice="Cherry")

        assert formal.read_bytes() == b"paid-old-audio"
        assert list(formal.parent.iterdir()) == [formal]
        assert gen.versions.ensure_calls == []
        assert gen.versions.add_calls == []

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

    @pytest.mark.parametrize("failure", [RuntimeError("manifest failed"), asyncio.CancelledError()])
    async def test_finalize_failure_or_cancellation_preserves_formal_audio_and_current_version(
        self,
        tmp_path: Path,
        failure: BaseException,
    ):
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        formal = gen.project_path / "audio" / "segment_E1S07.wav"
        formal.parent.mkdir(parents=True)
        formal.write_bytes(b"paid-old-audio")
        old_version = gen.versions.add_version("audio", "E1S07", "old text", source_file=formal)

        def _commit(staged_path: Path, output_path: Path) -> int:
            def _fail_after_promotion() -> None:
                raise failure

            return gen.versions.commit_staged_version(
                resource_type="audio",
                resource_id="E1S07",
                prompt="new text",
                staged_file=staged_path,
                current_file=output_path,
                on_commit=_fail_after_promotion,
            )

        with pytest.raises(type(failure)):
            await gen.generate_audio_async(
                text="new text",
                resource_id="E1S07",
                voice="Cherry",
                commit_staged=_commit,
            )

        assert formal.read_bytes() == b"paid-old-audio"
        history = gen.versions.get_versions("audio", "E1S07")
        assert history["current_version"] == old_version
        assert [record["prompt"] for record in history["versions"]] == ["old text"]
        assert not any(path.name.startswith(".segment_E1S07") for path in formal.parent.iterdir())

    async def test_paid_history_only_commit_does_not_require_a_formal_audio_file(self, tmp_path: Path):
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)

        def _commit(staged_path: Path, output_path: Path):
            return gen.versions.commit_staged_paid_version(
                resource_type="audio",
                resource_id="E1S08",
                prompt="late text",
                staged_file=staged_path,
                current_file=output_path,
                select_current=False,
            )

        output_path, version = await gen.generate_audio_async(
            text="late text",
            resource_id="E1S08",
            voice="Cherry",
            commit_staged=_commit,
        )

        assert version == 1
        assert not output_path.exists()
        history = gen.versions.get_versions("audio", "E1S08")
        assert history["current_version"] == 0
        assert (gen.project_path / history["versions"][0]["file"]).read_bytes() == b"RIFFfakewav"


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
                await repo.finish_call(call_id, status="success", settlement=SettlementInput(usage_tokens=1500))
                stats = await repo.get_stats(project_name="demo")
                assert stats["audio_count"] == 1
                # audio 按字符冻结费用（非 0）
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

    async def test_claim_routes_audio_to_audio_lane(self):
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
                        # 不带 project_name：payload 自报的 audio_provider 即可让真实
                        # _extract_provider 短路解析出 dashscope，无需项目与 DB 在场。
                        "payload": {"audio_provider": "dashscope", "audio_model": "qwen3-tts-flash"},
                    }
                return None

        w = GenerationWorker(
            queue=_Q(),  # type: ignore[arg-type]
            capacity=CapacityTable(
                _limits={"dashscope": {"image": 0, "video": 0, "audio": 2}},
                _defaults={"image": 5, "video": 3, "audio": 10},
            ),
        )

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


class TestDeriveExecutionModelForEnqueueAudio:
    async def test_tts_routes_to_audio_resolver(self, monkeypatch):
        from lib import generation_queue as gq
        from lib.config.resolver import ProviderModel

        class _FakeResolver:
            def __init__(self, factory):
                pass

            async def resolve_audio_backend(self, project, payload):
                return ProviderModel("dashscope", "qwen3-tts-flash")

        monkeypatch.setattr("lib.config.resolver.ConfigResolver", _FakeResolver)
        derived = await gq._derive_execution_model_for_enqueue(
            project_name=None, payload={}, task_type="tts", media_type="audio", resource_id=None
        )
        assert derived == (ProviderModel("dashscope", "qwen3-tts-flash"), None)
