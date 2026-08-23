import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.image_backends.base import ImageCapability, ImageGenerationResult
from lib.media_generator import (
    MediaGenerator,
    cleanup_staged_video_output,
    segment_id_for,
    task_image_staging_path,
    task_video_staging_path,
)
from lib.version_manager import PaidVersionCommit
from tests.fakes import select_formal_video


class _FakeImageBackend:
    """Fake ImageBackend conforming to the protocol."""

    name = "fake-image"
    model = "img-model"
    capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def __init__(self):
        self.calls = []

    async def generate(self, request):
        self.calls.append(request)
        # Touch the output file so version tracking works
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"fake-image-data")
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self.name,
            model=self.model,
            usage_tokens=8,
        )


class _FakeVideoResult:
    def __init__(self, duration_seconds: int = 8):
        self.video_uri = "video-uri"
        self.usage_tokens = 0
        self.generate_audio = True
        self.duration_seconds = duration_seconds


class _FakeVideoBackend:
    """Fake VideoBackend conforming to the protocol."""

    name = "fake-video"
    model = "video-model"

    def __init__(self, result_duration_seconds: int | None = None, video_capabilities=None):
        self.calls = []
        # None = 回显请求时长（多数后端行为）；指定值 = 模拟 provider 回报的实际计费时长
        self._result_duration_seconds = result_duration_seconds
        self.video_capabilities = video_capabilities

    async def generate(self, request):
        self.calls.append(request)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"fake-video-data")
        duration = (
            self._result_duration_seconds if self._result_duration_seconds is not None else request.duration_seconds
        )
        return _FakeVideoResult(duration_seconds=duration)


class _FakeVersions:
    def __init__(self):
        self.ensure_calls = []
        self.add_calls = []

    def ensure_current_tracked(self, **kwargs):
        self.ensure_calls.append(kwargs)

    def add_version(self, **kwargs):
        self.add_calls.append(kwargs)
        return len(self.add_calls)

    def get_versions(self, resource_type, resource_id):
        return {
            "current_version": len(self.add_calls),
            "versions": [{"created_at": "2026-01-01T00:00:00Z"}] * max(1, len(self.add_calls)),
        }


class _FakeLedgerCall:
    def __init__(self, call_id):
        self.call_id = call_id
        self.declared = False
        self.result = None

    def success(self, result):
        self.declared = True
        self.result = result


class _FakeLedger:
    """记账账本假实现：捕获记账括号入参（started）与终态结果（outcomes）——新主缝。

    括号语义与真 Ledger 一致：CancelledError 穿透、Exception 记 failed 后重抛、
    正常退出未声明成功抛 RuntimeError。usage 字段的提取归属真 Ledger 的 union 分发，
    此处只捕获调用点递交的 backend 结果对象。
    """

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


class _FakeConfigResolver:
    """Fake ConfigResolver，返回可控的配置值。"""

    def __init__(self, video_generate_audio: bool = False):
        self._video_generate_audio = video_generate_audio

    async def video_generate_audio(self, project_name=None):
        return self._video_generate_audio

    async def reference_payload_limits(self, provider_id=None):
        # 与真实 resolver 同契约：provider_id 为 None 或未配置时返回 service 层保守默认。
        from lib.config.service import (
            _DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            _DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
        )

        return _DEFAULT_REFERENCE_TOTAL_MAX_BYTES, _DEFAULT_REFERENCE_SINGLE_MAX_BYTES


def _build_generator(tmp_path: Path) -> MediaGenerator:
    gen = object.__new__(MediaGenerator)
    gen.project_path = tmp_path / "projects" / "demo"
    gen.project_path.mkdir(parents=True, exist_ok=True)
    gen.project_name = "demo"
    gen._rate_limiter = None
    gen._image_backend = _FakeImageBackend()
    gen._video_backend = _FakeVideoBackend()
    gen._user_id = "default"
    gen._config = _FakeConfigResolver()
    gen._image_provider_id = None
    gen._video_provider_id = None
    gen.versions = _FakeVersions()
    gen.ledger = _FakeLedger()
    return gen


class TestSegmentIdFor:
    """segment_id_for 是 image/video/audio 三条记账路径共用的单点判定函数。"""

    @pytest.mark.parametrize(
        "resource_type",
        ["storyboards", "videos", "grids"],
    )
    def test_image_whitelist_hit(self, resource_type):
        assert segment_id_for("image", resource_type, "E1S01") == "E1S01"

    def test_image_whitelist_miss(self):
        assert segment_id_for("image", "characters", "Alice") is None

    @pytest.mark.parametrize(
        "resource_type",
        ["storyboards", "videos", "reference_videos"],
    )
    def test_video_whitelist_hit(self, resource_type):
        assert segment_id_for("video", resource_type, "E1S01") == "E1S01"

    def test_video_whitelist_miss(self):
        # grids 只在图片记账白名单内，视频记账应落 None。
        assert segment_id_for("video", "grids", "E1G1") is None

    @pytest.mark.parametrize(
        "resource_type",
        ["audio", "storyboards", "characters"],
    )
    def test_audio_unconditional(self, resource_type):
        assert segment_id_for("audio", resource_type, "E1S01") == "E1S01"

    def test_unknown_call_type_raises(self):
        # 未接入记账白名单的通道显式报错，避免 segment_id 静默丢失。
        with pytest.raises(ValueError, match="unknown ledger channel"):
            segment_id_for("text", "storyboards", "E1S01")


class TestMediaGenerator:
    def test_get_output_path_and_invalid_type(self, tmp_path):
        gen = _build_generator(tmp_path)
        assert gen._get_output_path("storyboards", "E1S01").name == "scene_E1S01.png"
        assert gen._get_output_path("videos", "E1S01").name == "scene_E1S01.mp4"
        assert gen._get_output_path("characters", "Alice").name == "Alice.png"
        assert gen._get_output_path("reference_videos", "E1U1").name == "E1U1.mp4"
        with pytest.raises(ValueError):
            gen._get_output_path("bad", "x")

    async def test_cancelled_formal_image_generation_never_replaces_the_canonical_file(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend_written = asyncio.Event()
        keep_running = asyncio.Event()

        class _BlockingImageBackend(_FakeImageBackend):
            async def generate(self, request) -> ImageGenerationResult:
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(b"new-image")
                backend_written.set()
                await keep_running.wait()
                return ImageGenerationResult(
                    image_path=request.output_path,
                    provider=self.name,
                    model=self.model,
                    usage_tokens=8,
                )

        gen._image_backend = _BlockingImageBackend()
        canonical = gen._get_output_path("storyboards", "E1S01")
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"old-image")
        committed: list[Path] = []

        task = asyncio.create_task(
            gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
                formal_output=True,
                task_id="image-task",
                commit_formal_output=lambda staged, _current, _metadata: committed.append(staged) or 1,
            )
        )
        await backend_written.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert canonical.read_bytes() == b"old-image"
        assert committed == []
        assert gen.versions.add_calls == []
        assert not any(canonical.parent.glob(".*.task-output.png"))

    async def test_image_before_submit_runs_at_the_backend_boundary(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend = _FakeImageBackend()
        gen._image_backend = backend
        events: list[str] = []
        original_generate = backend.generate

        async def _generate(request):
            events.append("provider")
            return await original_generate(request)

        async def _before_submit() -> None:
            events.append("admission")

        backend.generate = _generate

        await gen.generate_image_async(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            before_submit=_before_submit,
        )

        assert events == ["admission", "provider"]

    async def test_invalid_formal_image_call_preserves_a_previous_staged_output(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend = _FakeImageBackend()
        gen._image_backend = backend
        canonical = gen._get_output_path("storyboards", "E1S01")
        canonical.parent.mkdir(parents=True, exist_ok=True)
        staged = task_image_staging_path(canonical, "image-task")
        staged.write_bytes(b"recoverable-output")

        with pytest.raises(ValueError, match="artifact commit callback"):
            await gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
                formal_output=True,
                task_id="image-task",
            )

        assert staged.read_bytes() == b"recoverable-output"
        assert backend.calls == []

    def test_generate_image_success_and_failure(self, tmp_path):
        gen = _build_generator(tmp_path)
        output_path, version = gen.generate_image(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            aspect_ratio="9:16",
        )

        assert output_path.name == "scene_E1S01.png"
        assert version == 1
        assert gen.ledger.started[0]["call_type"] == "image"
        assert gen.ledger.outcomes[0]["status"] == "success"
        # usage_tokens 提取归属真 Ledger union 分发；此处确认调用点递交了 backend 结果对象
        assert gen.ledger.outcomes[0]["result"].usage_tokens == 8

        async def _raise(request):
            raise RuntimeError("boom")

        gen._image_backend.generate = _raise
        with pytest.raises(RuntimeError):
            gen.generate_image(prompt="p", resource_type="characters", resource_id="A")

        assert any(o["status"] == "failed" for o in gen.ledger.outcomes)

    @pytest.mark.asyncio
    async def test_generate_video_sync_and_async(self, tmp_path):
        gen = _build_generator(tmp_path)

        video_path, version, video_ref, video_uri = gen.generate_video(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            duration_seconds="bad",
        )
        assert video_path.name == "scene_E1S01.mp4"
        assert version == 1
        assert video_ref is None
        assert video_uri == "video-uri"

        video_path2, version2, _, _ = await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S02",
            duration_seconds="6",
        )
        assert video_path2.name == "scene_E1S02.mp4"
        assert version2 == 2
        assert gen.ledger.started[-1]["call_type"] == "video"

    @pytest.mark.asyncio
    async def test_video_before_submit_runs_once_immediately_before_first_backend_call(self, tmp_path):
        from lib.version_manager import VersionManager

        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        events: list[object] = []

        class _Backend(_FakeVideoBackend):
            async def generate(self, request):
                events.append("provider")
                return await super().generate(request)

        gen._video_backend = _Backend()

        async def _checkpoint(call_id: int) -> dict[str, object]:
            events.append(("checkpoint", call_id))
            return {"execution_api_call_id": call_id}

        await gen.generate_video_async(
            prompt="p",
            resource_type="reference_videos",
            resource_id="E1U1",
            before_submit=_checkpoint,
        )

        assert events == [("checkpoint", 1), "provider"]
        history = gen.versions.get_versions("reference_videos", "E1U1")
        assert history["versions"][0]["execution_api_call_id"] == 1

    @pytest.mark.asyncio
    async def test_video_before_submit_failure_prevents_provider_call(self, tmp_path):
        gen = _build_generator(tmp_path)

        async def _checkpoint(_call_id: int) -> None:
            raise RuntimeError("checkpoint unavailable")

        with pytest.raises(RuntimeError, match="checkpoint unavailable"):
            await gen.generate_video_async(
                prompt="p",
                resource_type="reference_videos",
                resource_id="E1U1",
                before_submit=_checkpoint,
            )

        assert gen._video_backend.calls == []
        assert [outcome["status"] for outcome in gen.ledger.outcomes] == ["failed"]

    @pytest.mark.asyncio
    async def test_video_before_submit_is_not_repeated_for_413_compression_retry(self, tmp_path):
        gen = _build_generator(tmp_path)
        ref = _solid_png(tmp_path, "ref-checkpoint.png", 16, 16)

        class _RetryBackend(_FakeVideoBackend):
            from lib.video_backends.base import VideoCapabilities

            video_capabilities = VideoCapabilities(max_reference_images=9)

            def __init__(self):
                super().__init__(video_capabilities=type(self).video_capabilities)
                self.attempts = 0

            async def generate(self, request):
                self.attempts += 1
                self.calls.append(request)
                if self.attempts == 1:
                    raise _http_413_error()
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(b"paid-video")
                return _FakeVideoResult()

        backend = _RetryBackend()
        gen._video_backend = backend
        checkpoint_calls: list[int] = []

        async def _checkpoint(call_id: int) -> None:
            checkpoint_calls.append(call_id)

        await gen.generate_video_async(
            prompt="p",
            resource_type="reference_videos",
            resource_id="E1U1",
            reference_images=[ref],
            before_submit=_checkpoint,
        )

        assert backend.attempts == 2
        assert checkpoint_calls == [1]

    @pytest.mark.asyncio
    async def test_video_413_after_provider_acceptance_never_resubmits(self, tmp_path):
        gen = _build_generator(tmp_path)
        ref = _solid_png(tmp_path, "ref-after-submit.png", 16, 16)

        class _Poll413Backend(_FakeVideoBackend):
            from lib.video_backends.base import VideoCapabilities

            video_capabilities = VideoCapabilities(max_reference_images=9)

            def __init__(self):
                super().__init__(video_capabilities=type(self).video_capabilities)
                self.attempts = 0

            async def generate(self, request):
                self.attempts += 1
                if request.on_provider_resubmit_unsafe is not None:
                    request.on_provider_resubmit_unsafe()
                raise _http_413_error()

        backend = _Poll413Backend()
        gen._video_backend = backend

        with pytest.raises(httpx.HTTPStatusError):
            await gen.generate_video_async(
                prompt="p",
                resource_type="reference_videos",
                resource_id="E1U1",
                reference_images=[ref],
            )

        assert backend.attempts == 1

    @pytest.mark.asyncio
    async def test_formal_video_output_uses_staged_transaction_and_preserves_old_current_on_failure(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.version_manager import VersionManager

        monkeypatch.setattr("lib.video_backends.base.persist_api_call_id", AsyncMock())
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-current")

        class _FailAfterWrite(_FakeVideoBackend):
            async def generate(self, request):
                request.output_path.write_bytes(b"partial-new")
                raise RuntimeError("provider download failed")

        gen._video_backend = _FailAfterWrite()
        with pytest.raises(RuntimeError, match="provider download failed"):
            await gen.generate_video_async(
                prompt="p",
                resource_type="reference_videos",
                resource_id="E1U1",
                formal_output=True,
                task_id="task-output-failure",
            )

        assert current.read_bytes() == b"old-current"
        assert list(current.parent.glob(".E1U1.*.mp4")) == []

    @pytest.mark.asyncio
    async def test_formal_video_output_cleans_staging_when_ledger_settlement_fails(self, tmp_path, monkeypatch):
        from lib.version_manager import VersionManager

        monkeypatch.setattr("lib.video_backends.base.persist_api_call_id", AsyncMock())
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-current")

        class _FailingSettlementLedger(_FakeLedger):
            @asynccontextmanager
            async def record(self, **kwargs):
                async with super().record(**kwargs) as call:
                    yield call
                raise RuntimeError("ledger settlement failed")

        gen.ledger = _FailingSettlementLedger()

        with pytest.raises(RuntimeError, match="ledger settlement failed"):
            await gen.generate_video_async(
                prompt="paid request",
                resource_type="reference_videos",
                resource_id="E1U1",
                formal_output=True,
                task_id="task-ledger-failure",
            )

        assert current.read_bytes() == b"old-current"
        assert list(current.parent.glob(".E1U1.*.mp4")) == []

    @pytest.mark.asyncio
    async def test_formal_video_output_commits_file_and_history_together(self, tmp_path, monkeypatch):
        from lib.version_manager import VersionManager

        monkeypatch.setattr("lib.video_backends.base.persist_api_call_id", AsyncMock())
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)

        events = []

        async def _prepare(staged_file, duration_seconds, version_metadata):
            assert staged_file.read_bytes() == b"fake-video-data"
            assert duration_seconds == 8
            assert version_metadata["execution_request_digest"] == "d" * 64
            events.append("prepared")

        def _commit(*args):
            events.append("committed")
            return select_formal_video(gen, prompt="paid request")(*args)

        output, version, _, _ = await gen.generate_video_async(
            prompt="paid request",
            resource_type="reference_videos",
            resource_id="E1U1",
            formal_output=True,
            task_id="task-output-success",
            before_formal_commit=_prepare,
            commit_formal_output=_commit,
            execution_request_digest="d" * 64,
        )

        assert output.read_bytes() == b"fake-video-data"
        assert version == 1
        history = gen.versions.get_versions("reference_videos", "E1U1")
        assert history["versions"][0]["execution_request_digest"] == "d" * 64
        assert Path(gen.project_path / history["versions"][0]["file"]).read_bytes() == b"fake-video-data"
        assert events == ["prepared", "committed"]

    @pytest.mark.asyncio
    async def test_formal_video_without_commit_callback_keeps_paid_history_but_never_selects_it(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.version_manager import VersionManager

        monkeypatch.setattr("lib.video_backends.base.persist_api_call_id", AsyncMock())
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-current")

        with pytest.raises(RuntimeError, match="commit callback"):
            await gen.generate_video_async(
                prompt="paid request",
                resource_type="reference_videos",
                resource_id="E1U1",
                formal_output=True,
                task_id="task-missing-callback",
            )

        assert current.read_bytes() == b"old-current"
        history = gen.versions.get_versions("reference_videos", "E1U1")
        assert history["current_version"] == 1
        assert len(history["versions"]) == 2
        assert history["versions"][-1]["is_current"] is False
        assert (gen.project_path / history["versions"][-1]["file"]).read_bytes() == b"fake-video-data"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", [RuntimeError("paid output validation failed"), asyncio.CancelledError()])
    async def test_formal_video_prepare_failure_or_cancellation_archives_paid_history_without_selecting_it(
        self,
        tmp_path,
        monkeypatch,
        failure: BaseException,
    ):
        from lib.version_manager import VersionManager

        monkeypatch.setattr("lib.video_backends.base.persist_api_call_id", AsyncMock())
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-current")

        async def _fail_prepare(*_args):
            raise failure

        with pytest.raises(type(failure)):
            await gen.generate_video_async(
                prompt="paid request",
                resource_type="reference_videos",
                resource_id="E1U1",
                formal_output=True,
                task_id="task-prepare-failure",
                before_formal_commit=_fail_prepare,
            )

        assert current.read_bytes() == b"old-current"
        history = gen.versions.get_versions("reference_videos", "E1U1")
        assert history["current_version"] == 1
        assert len(history["versions"]) == 2
        assert history["versions"][-1]["is_current"] is False
        assert (gen.project_path / history["versions"][-1]["file"]).read_bytes() == b"fake-video-data"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", [RuntimeError("validation failed"), asyncio.CancelledError()])
    async def test_formal_video_prepare_failure_archives_paid_history_off_the_event_loop(
        self,
        tmp_path,
        monkeypatch,
        failure: BaseException,
    ):
        import threading

        gen = _build_generator(tmp_path)
        event_loop_thread = threading.get_ident()
        archive_threads: list[int] = []

        def _archive(**_kwargs):
            archive_threads.append(threading.get_ident())

        monkeypatch.setattr(gen.versions, "commit_staged_paid_version", _archive, raising=False)

        async def _fail_prepare(*_args):
            raise failure

        with pytest.raises(type(failure)):
            await gen._prepare_formal_video_commit(
                resource_type="reference_videos",
                resource_id="E1U1",
                prompt="paid request",
                output_path=tmp_path / "current.mp4",
                staged_output_path=tmp_path / "staged.mp4",
                duration_seconds=8,
                version_metadata={},
                before_formal_commit=_fail_prepare,
            )

        assert archive_threads
        assert archive_threads[0] != event_loop_thread

    @pytest.mark.asyncio
    async def test_video_commit_thread_keeps_event_loop_responsive_and_defers_cancellation(
        self,
        tmp_path,
        monkeypatch,
    ):
        import threading

        gen = _build_generator(tmp_path)
        started = threading.Event()
        release = threading.Event()
        expected = PaidVersionCommit(version=7, selected=True)

        def _blocked_commit(**_kwargs):
            started.set()
            assert release.wait(timeout=5)
            return expected

        monkeypatch.setattr(gen, "_commit_video_output_version_sync", _blocked_commit)
        commit_task = asyncio.create_task(
            gen._commit_video_output_version(
                resource_type="videos",
                resource_id="E1S01",
                prompt="paid request",
                output_path=tmp_path / "current.mp4",
                staged_output_path=tmp_path / "staged.mp4",
                duration_seconds=8,
                version_metadata={},
            )
        )

        assert await asyncio.to_thread(started.wait, 5)
        await asyncio.sleep(0)
        commit_task.cancel()
        await asyncio.sleep(0)
        assert not commit_task.done()

        release.set()
        assert await commit_task == expected

    @pytest.mark.asyncio
    async def test_formal_video_output_reclaims_the_same_task_path_after_interruption(self, tmp_path, monkeypatch):
        from lib.version_manager import VersionManager

        monkeypatch.setattr("lib.video_backends.base.persist_api_call_id", AsyncMock())
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        staged = task_video_staging_path(current, "task-restarted")
        staged.write_bytes(b"interrupted-download")

        class _CapturePathBackend(_FakeVideoBackend):
            async def generate(self, request):
                assert request.output_path == staged
                assert not request.output_path.exists()
                return await super().generate(request)

        gen._video_backend = _CapturePathBackend()
        await gen.generate_video_async(
            prompt="paid request",
            resource_type="reference_videos",
            resource_id="E1U1",
            formal_output=True,
            task_id="task-restarted",
            commit_formal_output=select_formal_video(gen, prompt="paid request"),
        )

        assert current.read_bytes() == b"fake-video-data"
        assert not staged.exists()

    def test_formal_video_cleanup_unlinks_a_symlink_without_touching_its_target(self, tmp_path):
        gen = _build_generator(tmp_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        paid_history = tmp_path / "paid-history.mp4"
        paid_history.write_bytes(b"paid-history")
        staged = task_video_staging_path(current, "task-symlink")
        staged.symlink_to(paid_history)

        cleanup_staged_video_output(
            gen.project_path,
            "reference_videos",
            "E1U1",
            "task-symlink",
        )

        assert not staged.exists()
        assert paid_history.read_bytes() == b"paid-history"

    def test_formal_video_cleanup_removes_a_junction_without_file_unlink(self, tmp_path, monkeypatch):
        from lib import media_generator

        gen = _build_generator(tmp_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        staged = task_video_staging_path(current, "task-junction")
        staged.mkdir()
        monkeypatch.setattr(
            media_generator.os.path,
            "isjunction",
            lambda path: Path(path) == staged,
            raising=False,
        )

        cleanup_staged_video_output(
            gen.project_path,
            "reference_videos",
            "E1U1",
            "task-junction",
        )

        assert not staged.exists()

    @pytest.mark.asyncio
    async def test_rejected_short_video_does_not_make_legacy_predecessor_reusable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from lib.narration_delivery import (
            USE_TTS,
            NarratedVideoDurationBlockedError,
            NarrationDeliveryPreparation,
            NarrationTtsStatus,
        )
        from lib.version_manager import VersionManager
        from server.services import narration_delivery_tasks

        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        output_path = gen._get_output_path("videos", "E1S01")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"legacy-video-with-unknown-tier")

        _, version, _, _ = await gen.generate_video_async(
            prompt="new request",
            resource_type="videos",
            resource_id="E1S01",
            duration_seconds=8,
        )
        monkeypatch.setattr(
            narration_delivery_tasks,
            "probe_existing_media_duration_seconds",
            AsyncMock(return_value=4.0),
        )
        narration = NarrationDeliveryPreparation(
            delivery=USE_TTS,
            unit_id="E1S01",
            speech_mode=None,
            tts_status=NarrationTtsStatus.CURRENT,
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="basis",
            actual_duration_seconds=6.2,
            problems=(),
        )
        # 重载协程本体照跑，只把它的三个协作者换成替身。
        pm = MagicMock()
        pm.load_project.return_value = {
            "name": "demo",
            "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
        }
        pm.get_project_path.return_value = gen.project_path
        pm.load_script.return_value = {
            "episode": 1,
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "narration": "旁白。", "duration_seconds": 8}],
        }
        monkeypatch.setattr(narration_delivery_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(
            narration_delivery_tasks,
            "prepare_current_narration_delivery",
            AsyncMock(return_value=narration),
        )
        monkeypatch.setattr(narration_delivery_tasks, "tts_task_in_progress", AsyncMock(return_value=False))

        with pytest.raises(NarratedVideoDurationBlockedError):
            await narration_delivery_tasks.require_generated_video_covers_current_tts(
                project_name="demo",
                script_file="episode_1.json",
                request_duration_seconds=8,
                output_path=output_path,
                versions=gen.versions,
                resource_type="videos",
                resource_id="E1S01",
                version=version,
            )

        assert output_path.read_bytes() == b"legacy-video-with-unknown-tier"
        item = {
            "generated_assets": {
                "status": "completed",
                "video_clip": "videos/scene_E1S01.mp4",
            }
        }
        assert (
            await narration_delivery_tasks.reuse_current_video_for_tier(
                project_path=gen.project_path,
                versions=gen.versions,
                item=item,
                resource_type="videos",
                resource_id="E1S01",
                request_duration_seconds=8,
                minimum_actual_duration_seconds=6.2,
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_generate_video_async_segment_id_by_resource_type(self, tmp_path):
        """视频记账 segment_id 白名单覆盖 storyboards/videos/reference_videos，其余落 None。"""
        gen = _build_generator(tmp_path)

        await gen.generate_video_async(prompt="p", resource_type="videos", resource_id="E1S01")
        assert gen.ledger.started[-1]["segment_id"] == "E1S01"

        await gen.generate_video_async(prompt="p", resource_type="reference_videos", resource_id="E1U1")
        assert gen.ledger.started[-1]["segment_id"] == "E1U1"

        # 负向边界：白名单外的 resource_type 仍落 None——守住"白名单"语义，
        # 避免日后被改成无条件透传而无人察觉。grids 只在图片记账白名单内。
        await gen.generate_video_async(prompt="p", resource_type="grids", resource_id="E1G1")
        assert gen.ledger.started[-1]["segment_id"] is None

    @pytest.mark.asyncio
    async def test_video_billed_duration_passed_to_finish_call(self, tmp_path):
        """backend 返回与请求不同的实际计费时长时，视频路径透传给 finish_call。"""
        gen = _build_generator(tmp_path)
        gen._video_backend = _FakeVideoBackend(result_duration_seconds=15)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S10",
            duration_seconds="6",
        )
        assert gen.ledger.started[-1]["duration_seconds"] == 6
        # billed_duration_seconds 由真 Ledger union 分发从结果对象提取；此处确认递交了 duration=15 的结果
        assert gen.ledger.outcomes[-1]["result"].duration_seconds == 15

    @pytest.mark.asyncio
    async def test_video_billed_duration_lands_in_ledger(self, tmp_path):
        """端到端：真 Ledger 落库，backend 返回与请求不同的实际计费时长，ApiCall 账本记录 backend 值。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from lib.db.base import Base
        from lib.db.repositories.usage_repo import UsageRepository
        from lib.ledger import Ledger

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            gen = _build_generator(tmp_path)
            gen._video_backend = _FakeVideoBackend(result_duration_seconds=15)
            gen.ledger = Ledger(session_factory=factory)

            await gen.generate_video_async(
                prompt="p",
                resource_type="videos",
                resource_id="E1S11",
                duration_seconds="6",
            )

            # 读侧直连 UsageRepository
            async with factory() as session:
                item = (await UsageRepository(session).get_calls(project_name="demo"))["items"][0]
            assert item["status"] == "success"
            assert item["duration_seconds"] == 15
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_video_generate_audio_from_config_resolver(self, tmp_path):
        """验证 generate_video_async 通过 ConfigResolver 获取 audio 设置。"""
        gen = _build_generator(tmp_path)
        gen._config = _FakeConfigResolver(video_generate_audio=False)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S03",
        )
        # VideoBackend 路径尊重 ConfigResolver 返回的值
        assert gen.ledger.started[-1]["generate_audio"] is False

    @pytest.mark.asyncio
    async def test_video_generate_audio_respects_config_true(self, tmp_path):
        """验证 video_backend 尊重 ConfigResolver 返回的 True。"""
        gen = _build_generator(tmp_path)
        gen._config = _FakeConfigResolver(video_generate_audio=True)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S04",
        )
        assert gen.ledger.started[-1]["generate_audio"] is True

    @pytest.mark.asyncio
    async def test_video_generate_audio_defaults_true_when_config_none(self, tmp_path):
        """当 self._config is None 时，fallback 默认 True，
        与 ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO 对齐（PR7 §11）。"""
        gen = _build_generator(tmp_path)
        gen._config = None

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S05",
        )
        assert gen.ledger.started[-1]["generate_audio"] is True

    @pytest.mark.asyncio
    async def test_end_image_rejected_when_backend_lacks_last_frame(self, tmp_path):
        """后端 last_frame=False 时硬失败：不下发供应商调用、不开记账，也不降级为参考图。"""
        from lib.video_backends.base import VideoCapabilities, VideoCapabilityError

        gen = _build_generator(tmp_path)
        gen._video_backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(last_frame=False))
        end_image = tmp_path / "end.png"
        end_image.write_bytes(b"fake-end-image")
        started_before = len(gen.ledger.started)

        with pytest.raises(VideoCapabilityError) as exc:
            await gen.generate_video_async(
                prompt="p",
                resource_type="videos",
                resource_id="E1S06",
                end_image=end_image,
            )

        assert exc.value.code == "video_last_frame_unsupported"
        assert gen._video_backend.calls == []
        assert len(gen.ledger.started) == started_before

    @pytest.mark.asyncio
    async def test_end_image_forwarded_when_backend_reports_tier_aware_last_frame(self, tmp_path):
        """后端实现 video_capabilities_for_tier 时按实际 service_tier 收窄决定是否转发
        end_image：pro 档放行——无请求上下文的 video_capabilities 恒 False 也不应误判丢帧。"""
        from lib.video_backends.base import VideoCapabilities

        class _TierAwareVideoBackend(_FakeVideoBackend):
            def __init__(self):
                super().__init__(video_capabilities=VideoCapabilities(last_frame=False))

            def video_capabilities_for_tier(
                self, service_tier: str, resolution: str | None = None
            ) -> VideoCapabilities:
                return VideoCapabilities(last_frame=(service_tier or "").lower() == "pro")

        gen = _build_generator(tmp_path)
        gen._video_backend = _TierAwareVideoBackend()
        end_image = tmp_path / "end.png"
        end_image.write_bytes(b"fake-end-image")

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S07",
            end_image=end_image,
            service_tier="pro",
        )
        call = gen._video_backend.calls[0]
        assert call.end_image == end_image

    @pytest.mark.asyncio
    async def test_end_image_rejected_when_tier_aware_backend_reports_std_tier(self, tmp_path):
        """同一后端，std 档时仍按能力收窄硬失败——覆盖 pro/std 两条分支。"""
        from lib.video_backends.base import VideoCapabilities, VideoCapabilityError

        class _TierAwareVideoBackend(_FakeVideoBackend):
            def __init__(self):
                super().__init__(video_capabilities=VideoCapabilities(last_frame=False))

            def video_capabilities_for_tier(
                self, service_tier: str, resolution: str | None = None
            ) -> VideoCapabilities:
                return VideoCapabilities(last_frame=(service_tier or "").lower() == "pro")

        gen = _build_generator(tmp_path)
        gen._video_backend = _TierAwareVideoBackend()
        end_image = tmp_path / "end.png"
        end_image.write_bytes(b"fake-end-image")
        started_before = len(gen.ledger.started)

        with pytest.raises(VideoCapabilityError) as exc:
            await gen.generate_video_async(
                prompt="p",
                resource_type="videos",
                resource_id="E1S08",
                end_image=end_image,
                service_tier="std",
            )

        assert exc.value.code == "video_last_frame_unsupported"
        assert gen._video_backend.calls == []
        assert len(gen.ledger.started) == started_before

    @pytest.mark.asyncio
    async def test_empty_string_end_image_normalized_to_no_end_frame(self, tmp_path):
        """遗留/直接调用者以 end_image="" 表示无尾帧：即便后端不支持 last_frame 也应正常放行，
        不误判为携带尾帧而硬失败（与 kling _build_payload 的真值判断兼容语义对齐）。"""
        from lib.video_backends.base import VideoCapabilities

        gen = _build_generator(tmp_path)
        gen._video_backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(last_frame=False))

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S09",
            end_image="",
        )

        call = gen._video_backend.calls[0]
        assert call.end_image is None


# ── 咽喉层参考图压缩接线 ────────────────────────────────────────────────────

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from lib.media_generator import _is_413  # noqa: E402
from lib.reference_compression import LADDER_STEPS, ReferencePayloadFloorError  # noqa: E402


def _http_413_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.test")
    resp = httpx.Response(status_code=413, request=req)
    return httpx.HTTPStatusError("Request Entity Too Large", request=req, response=resp)


def _noise_png(tmp_path: Path, name: str, w: int, h: int) -> Path:
    p = tmp_path / name
    Image.effect_noise((w, h), 80).convert("RGB").save(p, format="PNG")
    return p


def _solid_png(tmp_path: Path, name: str, w: int, h: int) -> Path:
    p = tmp_path / name
    Image.new("RGB", (w, h), color=(200, 100, 50)).save(p, format="PNG")
    return p


class _ConfigurableImageBackend:
    """可配置 413 失败次数的 image backend，记录每次收到的参考图路径。"""

    name = "fake-image"
    model = "img-model"
    capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def __init__(self, fail_413_times: int = 0):
        self.calls = []
        self.received_refs: list[list[Path]] = []
        self._fail_left = fail_413_times

    async def generate(self, request):
        self.calls.append(request)
        self.received_refs.append([Path(r.path) for r in request.reference_images])
        if self._fail_left > 0:
            self._fail_left -= 1
            raise _http_413_error()
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"img")
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self.name,
            model=self.model,
            usage_tokens=8,
        )


class TestIs413:
    def test_httpx_413(self):
        assert _is_413(_http_413_error()) is True

    def test_phrase_match(self):
        assert _is_413(RuntimeError("Request Entity Too Large")) is True
        assert _is_413(RuntimeError("oops: PAYLOAD TOO LARGE")) is True

    def test_byte_count_not_misread(self):
        # 修正④：不用裸 "413" 子串，避免字节数 / 请求 ID 误命中
        assert _is_413(RuntimeError("only 41300 bytes uploaded")) is False
        assert _is_413(RuntimeError("error code 413xyz")) is False

    def test_non_413_status(self):
        req = httpx.Request("POST", "https://example.test")
        resp = httpx.Response(status_code=400, request=req)
        err = httpx.HTTPStatusError("bad request", request=req, response=resp)
        assert _is_413(err) is False

    def test_sdk_status_code_attr(self):
        # OpenAI/xai 风格 SDK 异常：直接带 .status_code
        class _SdkErr(Exception):
            status_code = 413

        assert _is_413(_SdkErr("too big")) is True

    def test_sdk_code_attr(self):
        # google-genai 风格 APIError：带 .code
        class _ApiErr(Exception):
            code = 413

        assert _is_413(_ApiErr("Request payload size exceeds the limit")) is True

    def test_sdk_non_413_code_not_matched(self):
        class _ApiErr(Exception):
            code = 400

        assert _is_413(_ApiErr("bad request")) is False

    def test_string_status_code_413(self):
        # 个别 SDK / mock 把状态码给成字符串 "413"，需防御性 int 转换
        class _StrErr(Exception):
            status_code = "413"

        assert _is_413(_StrErr("too big")) is True

    def test_non_numeric_status_code_falls_back_to_phrase(self):
        # 非数字状态码不应抛 ValueError，落回短语匹配
        class _WeirdErr(Exception):
            status_code = "not-a-number"

        assert _is_413(_WeirdErr("totally unrelated")) is False
        assert _is_413(_WeirdErr("request entity too large")) is True


class TestReferenceCompressionSeam:
    async def test_backend_receives_compressed_copy_source_untouched(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend()
        gen._image_backend = backend

        src = _noise_png(tmp_path, "ref.png", 3000, 3000)
        src_bytes_before = src.read_bytes()

        await gen.generate_image_async(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            reference_images=[str(src)],
        )

        received = backend.received_refs[-1]
        assert len(received) == 1
        # backend 收到的是压缩临时副本，而非源路径
        assert received[0] != src
        # 源文件字节未被改动（只动上传副本）
        assert src.read_bytes() == src_bytes_before
        # 临时副本退出后清理
        assert not received[0].exists()

    async def test_413_retry_then_success_single_finish_call(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend(fail_413_times=1)
        gen._image_backend = backend

        src = _noise_png(tmp_path, "ref.png", 1200, 1200)
        await gen.generate_image_async(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            reference_images=[str(src)],
        )

        # 一次 413 后降档重试成功：backend 被调两次
        assert len(backend.calls) == 2
        # 只记一条 success（413 内循环重试不额外记账）
        assert len(gen.ledger.outcomes) == 1
        assert gen.ledger.outcomes[0]["status"] == "success"

    async def test_413_exhausted_raises_floor_records_failed(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend(fail_413_times=99)
        gen._image_backend = backend

        src = _noise_png(tmp_path, "ref.png", 800, 800)
        with pytest.raises(ReferencePayloadFloorError):
            await gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
                reference_images=[str(src)],
            )

        # 走完梯子（基线 + LADDER_STEPS-1 档 + 地板）= LADDER_STEPS + 1 次调用后耗尽
        assert len(backend.calls) == LADDER_STEPS + 1
        # 耗尽冒泡到外层 except 记一条 failed
        assert len(gen.ledger.outcomes) == 1
        assert gen.ledger.outcomes[0]["status"] == "failed"

    async def test_t2i_no_refs_413_not_converted_to_floor(self, tmp_path):
        # 无参考图（T2I）的 413 与参考图无关，不应被误转成 floor、也不降档
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend(fail_413_times=99)
        gen._image_backend = backend

        with pytest.raises(httpx.HTTPStatusError):
            await gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
            )
        # 单次调用，无降档重试
        assert len(backend.calls) == 1

    async def test_video_frame_not_resized_array_laddered(self, tmp_path):
        gen = _build_generator(tmp_path)

        from lib.video_backends.base import VideoCapabilities

        class _CapturingVideoBackend:
            name = "fake-video"
            model = "video-model"
            # 带参考图的请求会先过 gate_video_request，替身须声明足够的参考图容量，
            # 否则请求在触达 generate 之前就被能力校验拒掉。
            video_capabilities = VideoCapabilities(max_reference_images=9)

            def __init__(self):
                self.start_dims = None
                self.ref_dims: list[tuple[int, int]] = []

            async def generate(self, request):
                if request.start_image:
                    with Image.open(request.start_image) as im:
                        self.start_dims = im.size
                for r in request.reference_images or []:
                    with Image.open(r) as im:
                        self.ref_dims.append(im.size)
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(b"v")
                return _FakeVideoResult()

        backend = _CapturingVideoBackend()
        gen._video_backend = backend

        start = _solid_png(tmp_path, "start.png", 3000, 2000)  # FRAME：永不缩尺寸（重格式仅重编码）
        ref = _solid_png(tmp_path, "ref.png", 3000, 3000)  # ARRAY：走梯子缩到 ≤2048

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            start_image=str(start),
            reference_images=[ref],
        )

        assert backend.start_dims == (3000, 2000)  # FRAME 尺寸保持
        assert max(backend.ref_dims[0]) == 2048  # ARRAY 缩到长边 2048

    async def test_reference_audio_reaches_backend_unreordered(self, tmp_path):
        """参考音频原样透传到请求：gate 放行后若不下传，音频会在校验之后被静默丢弃。

        音频不进压缩器（specs 只收图片），故也不能跟着压缩结果重排——顺序即 prompt 里
        「音频N」的指认顺序，重排会把 A 角色的音色安到 B 角色头上。
        """
        gen = _build_generator(tmp_path)

        from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities

        class _AudioCapturingVideoBackend:
            name = "fake-video"
            model = "video-model"
            video_capabilities = VideoCapabilities(
                max_reference_images=9,
                reference_audio_mode=ReferenceAudioMode.DIRECT,
                max_reference_audio_count=3,
            )

            def __init__(self):
                self.received_audio: list[Path] | None = None

            async def generate(self, request):
                self.received_audio = request.reference_audio_files
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(b"v")
                return _FakeVideoResult()

        backend = _AudioCapturingVideoBackend()
        gen._video_backend = backend

        ref = _solid_png(tmp_path, "ref.png", 3000, 3000)
        first = tmp_path / "alice.wav"
        second = tmp_path / "bob.wav"
        first.write_bytes(b"a")
        second.write_bytes(b"b")

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            reference_images=[ref],
            reference_audio_files=[first, second],
        )

        assert backend.received_audio == [first, second]

    async def test_reference_audio_total_duration_exceeded_raises_before_backend_call(self, tmp_path):
        """caps 声明了总时长上限时，超限须在调 backend.generate（即付费请求）之前被拦截。"""
        import shutil

        if shutil.which("ffprobe") is None:
            pytest.skip("ffprobe not available")

        from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities, VideoCapabilityError
        from tests.factories import wav_bytes

        gen = _build_generator(tmp_path)

        class _AudioDurationLimitedVideoBackend:
            name = "fake-video"
            model = "video-model"
            video_capabilities = VideoCapabilities(
                max_reference_images=9,
                reference_audio_mode=ReferenceAudioMode.DIRECT,
                max_reference_audio_count=3,
                max_reference_audio_total_seconds=15.0,
            )

            def __init__(self):
                self.called = False

            async def generate(self, request):
                self.called = True
                raise AssertionError("超限请求不应到达 backend.generate")

        backend = _AudioDurationLimitedVideoBackend()
        gen._video_backend = backend

        ref = _solid_png(tmp_path, "ref.png", 3000, 3000)
        first = tmp_path / "alice.wav"
        second = tmp_path / "bob.wav"
        first.write_bytes(wav_bytes(10))
        second.write_bytes(wav_bytes(10))

        with pytest.raises(VideoCapabilityError) as exc:
            await gen.generate_video_async(
                prompt="p",
                resource_type="videos",
                resource_id="E1S01",
                reference_images=[ref],
                reference_audio_files=[first, second],
            )

        assert exc.value.code == "video_reference_audio_duration_exceeded"
        assert backend.called is False
        assert gen.ledger.outcomes == []

    async def test_total_duration_exceeded_check_skipped_when_probe_fails(self, tmp_path, monkeypatch):
        """caps 声明了总时长上限，但探测失败（ffprobe 不可用等）返回 None 时，按既有降级口径放行而非阻断。"""
        from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities

        gen = _build_generator(tmp_path)

        class _AudioDurationLimitedVideoBackend:
            name = "fake-video"
            model = "video-model"
            video_capabilities = VideoCapabilities(
                max_reference_images=9,
                reference_audio_mode=ReferenceAudioMode.DIRECT,
                max_reference_audio_count=3,
                max_reference_audio_total_seconds=15.0,
            )

            async def generate(self, request):
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(b"v")
                return _FakeVideoResult()

        gen._video_backend = _AudioDurationLimitedVideoBackend()

        async def _failing_probe(paths):
            return None

        monkeypatch.setattr("lib.media_generator.probe_reference_audio_total_seconds", _failing_probe)

        ref = _solid_png(tmp_path, "ref.png", 3000, 3000)
        audio = tmp_path / "alice.wav"
        audio.write_bytes(b"a")

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            reference_images=[ref],
            reference_audio_files=[audio],
        )

        assert gen.ledger.outcomes, "探测失败时应放行请求，不阻断到 backend"

    async def test_total_duration_not_probed_when_backend_declares_no_limit(self, tmp_path, monkeypatch):
        """未声明总时长约束的后端不该为每个请求多付一轮 ffprobe——探测按能力声明惰性触发。"""
        from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities

        gen = _build_generator(tmp_path)

        class _NoDurationLimitVideoBackend:
            name = "fake-video"
            model = "video-model"
            video_capabilities = VideoCapabilities(
                max_reference_images=9,
                reference_audio_mode=ReferenceAudioMode.DIRECT,
                max_reference_audio_count=3,
            )

            async def generate(self, request):
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(b"v")
                return _FakeVideoResult()

        gen._video_backend = _NoDurationLimitVideoBackend()

        probe_calls: list[list[Path]] = []

        async def _recording_probe(paths):
            probe_calls.append(list(paths))
            return 0.0

        monkeypatch.setattr("lib.media_generator.probe_reference_audio_total_seconds", _recording_probe)

        ref = _solid_png(tmp_path, "ref.png", 3000, 3000)
        audio = tmp_path / "alice.wav"
        audio.write_bytes(b"a")

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            reference_images=[ref],
            reference_audio_files=[audio],
        )

        assert probe_calls == []

    async def test_prompt_over_limit_raises_before_backend_call(self, tmp_path):
        """超长 prompt 在调 backend.generate（即付费请求）之前被拦截，不留记账行。"""
        from lib.video_backends.base import VideoCapabilities, VideoCapabilityError

        gen = _build_generator(tmp_path)

        class _PromptLimitedVideoBackend:
            name = "fake-video"
            model = "video-model"
            video_capabilities = VideoCapabilities(max_prompt_chars=10)

            def __init__(self):
                self.called = False

            async def generate(self, request):
                self.called = True
                raise AssertionError("超限请求不应到达 backend.generate")

        backend = _PromptLimitedVideoBackend()
        gen._video_backend = backend

        with pytest.raises(VideoCapabilityError) as exc:
            await gen.generate_video_async(prompt="x" * 11, resource_type="videos", resource_id="E1S01")

        assert exc.value.code == "video_prompt_too_long"
        assert backend.called is False
        assert gen.ledger.outcomes == []

    async def test_prompt_gating_applies_without_optional_paths(self, tmp_path):
        """prompt 长度对每个请求都适用：纯文生/首帧路径（无尾帧、参考图、参考音频）同样查能力。"""
        from lib.video_backends.base import VideoCapabilities

        gen = _build_generator(tmp_path)

        class _PromptLimitedVideoBackend:
            name = "fake-video"
            model = "video-model"
            video_capabilities = VideoCapabilities(max_prompt_chars=10)

            async def generate(self, request):
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(b"v")
                return _FakeVideoResult()

        gen._video_backend = _PromptLimitedVideoBackend()

        await gen.generate_video_async(prompt="x" * 10, resource_type="videos", resource_id="E1S01")

        assert gen.ledger.outcomes


class TestFirstFrameRatioAdaptiveOnly:
    """VideoCapabilities.first_frame_ratio_adaptive_only 的共享施加逻辑。

    覆盖点见 lib.video_frame_slots.resolve_first_frame_aspect_ratio 的调用点
    （lib.media_generator.MediaGenerator.generate_video_async）。用能力白名单测试缝
    （伪造 backend 声明该约束）覆盖施加逻辑，不依赖真实 backend 是否已声明该字段。
    """

    async def test_first_frame_task_forced_to_adaptive(self, tmp_path):
        from lib.video_backends.base import VideoCapabilities

        gen = _build_generator(tmp_path)
        backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(first_frame_ratio_adaptive_only=True))
        gen._video_backend = backend

        start = _solid_png(tmp_path, "start.png", 100, 100)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            start_image=str(start),
            aspect_ratio="16:9",
        )

        assert backend.calls[-1].aspect_ratio == "adaptive"

    async def test_no_first_frame_task_keeps_user_ratio(self, tmp_path):
        """未带首帧（纯文生 / 仅参考图）不受该约束影响，原样透传用户比例。"""
        from lib.video_backends.base import VideoCapabilities

        gen = _build_generator(tmp_path)
        backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(first_frame_ratio_adaptive_only=True))
        gen._video_backend = backend

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            aspect_ratio="16:9",
        )

        assert backend.calls[-1].aspect_ratio == "16:9"

    async def test_default_capability_leaves_existing_model_payload_unchanged(self, tmp_path):
        """默认 False（现有模型未声明该约束）：首帧任务的请求 payload 保持原样。"""
        gen = _build_generator(tmp_path)
        backend = _FakeVideoBackend()  # video_capabilities=None，等同未声明
        gen._video_backend = backend

        start = _solid_png(tmp_path, "start.png", 100, 100)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            start_image=str(start),
            aspect_ratio="16:9",
        )

        assert backend.calls[-1].aspect_ratio == "16:9"

    async def test_ledger_records_user_intent_not_adaptive_override(self, tmp_path):
        """记账沿用用户原始比例意图，与下发给 backend 的实际值分离。"""
        from lib.video_backends.base import VideoCapabilities

        gen = _build_generator(tmp_path)
        backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(first_frame_ratio_adaptive_only=True))
        gen._video_backend = backend

        start = _solid_png(tmp_path, "start.png", 100, 100)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            start_image=str(start),
            aspect_ratio="16:9",
        )

        assert gen.ledger.started[-1]["aspect_ratio"] == "16:9"
