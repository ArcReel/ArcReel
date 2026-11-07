import asyncio
import contextlib
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar

import httpx
import openai
import pytest

from lib.artifacts.version_manager import PaidVersionCommit, VersionManager
from lib.backends.image_backends.base import ImageCapability, ImageGenerationResult
from lib.generation.deleted_target import TARGET_DELETED_NOTE, GenerationTargetDeletedError
from lib.generation.media_generator import (
    MediaGenerator,
    cleanup_staged_video_output,
    segment_id_for,
    task_image_staging_path,
    task_video_staging_path,
)
from lib.project import project_trash
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition
from tests.fakes import (
    MP4_BYTES,
    PNG_BYTES,
    FakeConfigResolver,
    bounded_poll_clock,
    select_formal_video,
    write_backend_output,
    write_generation_targets,
)
from tests.http_capture import capture_http, only_request, request_json


class _FakeImageBackend:
    """Fake ImageBackend conforming to the protocol."""

    name = "fake-image"
    model = "img-model"
    capabilities: ClassVar[set[ImageCapability]] = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def reference_image_limit(self) -> int | None:
        return None

    def __init__(self):
        self.calls = []

    async def generate(self, request):
        self.calls.append(request)
        # Touch the output file so version tracking works
        await write_backend_output(request.output_path, b"fake-image-data")
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
        self.seed = None
        self.provenance = None


class _FakeVideoBackend:
    """Fake VideoBackend conforming to the protocol."""

    name = "fake-video"
    model = "video-model"

    def __init__(self, result_duration_seconds: int | None = None, video_capabilities=None):
        self.calls = []
        # None echoes requested duration (most backends); explicit values simulate actual provider-billed durations.
        self._result_duration_seconds = result_duration_seconds
        self.video_capabilities = video_capabilities

    async def generate(self, request):
        self.calls.append(request)
        await write_backend_output(request.output_path, b"fake-video-data")
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
        self.usage = None

    def success(self, result, *, usage=None):
        self.declared = True
        self.result = result
        self.usage = usage


class _FakeLedger:
    """Fake ledger captures bracket inputs (started) and terminal outcomes, the primary new seam.

    Match real Ledger brackets: propagate CancelledError; record failed then reraise Exception; normal exit without declared success raises RuntimeError. Real Ledger owns usage extraction through union dispatch; capture only backend result objects supplied by call sites."""

    def __init__(self):
        self.started = []
        self.outcomes = []
        self.provider_responses = []
        self._n = 0

    async def record_provider_response(self, *, call_id, body):
        self.provider_responses.append((call_id, body))

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
    write_generation_targets(gen.project_path, segments=("E1S01",), video_units=("E1U1",))
    gen.project_name = "demo"
    gen._rate_limiter = None
    gen._image_backend = _FakeImageBackend()
    gen._video_backend = _FakeVideoBackend()
    gen._user_id = "default"
    gen._config = FakeConfigResolver(requested_generate_audio=False)
    gen._image_provider_id = "fake-image"
    gen._video_provider_id = None
    gen.versions = _FakeVersions()
    gen.ledger = _FakeLedger()
    return gen


class _FakeAudioBackend:
    name = "fake-audio"
    model = "audio-model"

    async def synthesize(self, request):
        await write_backend_output(request.output_path, b"fake-audio-data")
        return object()


def _refuse_commit(*_args, **_kwargs):
    raise AssertionError("目标已删除时不应提交正式产物")


async def _refuse_prepare(*_args, **_kwargs):
    raise AssertionError("目标已删除时不应准备选片")


class TestTargetDeletedDuringGeneration:
    """If targets are deleted at generation completion, trash results at original relative paths; original paths/history stay unchanged and costs settle successfully."""

    @staticmethod
    def _assert_trashed(gen: MediaGenerator, caught, rel: str, content: bytes) -> None:
        [record] = project_trash.list_records(gen.project_path)
        assert caught.value.result == {
            "resource_type": record.object["resource_type"],
            "resource_id": record.object["resource_id"],
            "file_path": None,
            "target_deleted": True,
            "trash_record_id": record.id,
        }
        assert record.object["note"] == TARGET_DELETED_NOTE
        assert record.files == (rel,)
        assert (project_trash.trash_dir(gen.project_path) / record.id / "files" / rel).read_bytes() == content
        # Original directory contains neither formal output nor staging leftovers; no new history version.
        assert list((gen.project_path / rel).parent.iterdir()) == []
        assert not (gen.project_path / "versions").exists()
        assert [outcome["status"] for outcome in gen.ledger.outcomes] == ["success"]

    async def test_image(self, tmp_path):
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        with pytest.raises(GenerationTargetDeletedError) as caught:
            await gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S09",
                formal_output=True,
                task_id="task-image",
                commit_formal_output=_refuse_commit,
            )
        self._assert_trashed(gen, caught, "storyboards/scene_E1S09.png", b"fake-image-data")

    async def test_video(self, tmp_path):
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        with pytest.raises(GenerationTargetDeletedError) as caught:
            await gen.generate_video_async(
                prompt="p",
                resource_type="reference_videos",
                resource_id="E1U9",
                formal_output=True,
                task_id="task-video",
                before_formal_commit=_refuse_prepare,
                commit_formal_output=_refuse_commit,
            )
        self._assert_trashed(gen, caught, "reference_videos/E1U9.mp4", b"fake-video-data")

    async def test_audio(self, tmp_path):
        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        gen._audio_backend = _FakeAudioBackend()
        gen._audio_provider_id = "fake-audio"
        with pytest.raises(GenerationTargetDeletedError) as caught:
            await gen.generate_audio_async(
                text="t",
                resource_id="E1S09",
                voice="v",
                task_id="task-audio",
                before_commit=_refuse_prepare,
                commit_staged=_refuse_commit,
            )
        self._assert_trashed(gen, caught, "audio/segment_E1S09.wav", b"fake-audio-data")

    async def test_target_still_present_commits_normally(self, tmp_path):
        gen = _build_generator(tmp_path)
        committed = []

        def _commit(staged, current, _metadata):
            committed.append(current)
            return 1

        await gen.generate_image_async(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            formal_output=True,
            task_id="task-present",
            commit_formal_output=_commit,
        )
        assert committed == [gen.project_path / "storyboards" / "scene_E1S01.png"]
        assert project_trash.list_records(gen.project_path) == []


class TestSegmentIdFor:
    """segment_id_for centralizes image/video/audio accounting decisions."""

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
        # grids belongs only to image accounting allowlists; video segment accounting returns None.
        assert segment_id_for("video", "grids", "E1G1") is None

    @pytest.mark.parametrize(
        "resource_type",
        ["audio", "storyboards", "characters"],
    )
    def test_audio_unconditional(self, resource_type):
        assert segment_id_for("audio", resource_type, "E1S01") == "E1S01"

    def test_unknown_call_type_raises(self):
        # Explicitly fail unregistered accounting backends rather than silently losing segment_id.
        with pytest.raises(ValueError, match="unknown ledger channel"):
            segment_id_for("text", "storyboards", "E1S01")


class TestMediaGenerator:
    def test_get_output_path_and_invalid_type(self, tmp_path):
        gen = _build_generator(tmp_path)
        assert gen._get_output_path("storyboards", "E1S01").name == "scene_E1S01.png"
        assert gen._get_output_path("videos", "E1S01").name == "scene_E1S01.mp4"
        assert gen._get_output_path("characters", "Alice").name == "Alice.png"
        assert gen._get_output_path("reference_videos", "E1U1").name == "E1U1.mp4"
        with pytest.raises(ValueError, match=r"不支持的资源类型: bad"):
            gen._get_output_path("bad", "x")

    async def test_declarative_video_submit_poll_download_and_accounting(self, tmp_path):
        from lib.custom_provider.declarative_backend import DeclarativeVideoBackend

        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["usage"] = {
            "duration_seconds": {"paths": ["$.usage.duration"], "accept": "scalar"}
        }
        gen = _build_generator(tmp_path)
        gen._video_provider_id = "custom-1"
        gen._video_backend = DeclarativeVideoBackend(
            api_key="secret",
            base_url="https://relay.test",
            model="video-x",
            definition=definition,
            provider="custom-1",
        )

        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "video_url": "https://relay.test/files/job-42.mp4",
                        "usage": {"duration": 7.5},
                    },
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            output, _version, _ref, uri = await gen.generate_video_async(
                prompt="paper boat",
                resource_type="reference_videos",
                resource_id="E1U1",
                duration_seconds=5,
            )

        assert output.read_bytes() == b"video"
        assert uri == "https://relay.test/files/job-42.mp4"
        assert submit.call_count == 1
        assert gen.ledger.outcomes[0]["status"] == "success"
        assert gen.ledger.outcomes[0]["result"].duration_seconds == 8
        assert gen.ledger.provider_responses[-1] == (
            1,
            {
                "status": "completed",
                "video_url": "https://relay.test/files/job-42.mp4",
                "usage": {"duration": 7.5},
            },
        )

    async def test_comfyui_video_records_the_actual_seed_and_workflow_fingerprint(self, tmp_path):
        """Both values become known only after generation. Metadata finalizes before submission and cannot contain them, so merge once more before persistence."""
        from lib.custom_provider.backends import CustomVideoBackend
        from lib.custom_provider.comfyui.comfyui_backend import ComfyuiVideoBackend
        from lib.custom_provider.comfyui.request_builder import workflow_sha256

        gen = _build_generator(tmp_path)
        gen._video_provider_id = "custom-1"
        delegate = ComfyuiVideoBackend(
            provider_id="custom-1",
            model="wan-t2v",
            base_url="https://comfy.test",
            api_key="",
            definition=comfyui_endpoint_definition(),
        )
        # Inject no synthetic capabilities: request gates read delegate declarations derived from actual bindings;
        # synthetic values would bypass the very path this case protects.
        gen._video_backend = CustomVideoBackend(provider_id="custom-1", delegate=delegate, model="wan-t2v")
        history = {
            "status": {"completed": True},
            "outputs": {"9": {"gifs": [{"filename": "final.mp4", "subfolder": "video", "type": "output"}]}},
        }

        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://comfy.test/prompt").mock(
                return_value=httpx.Response(200, json={"prompt_id": "p-1"})
            )
            router.get("https://comfy.test/history/p-1").mock(return_value=httpx.Response(200, json=history))
            router.get("https://comfy.test/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            output, _version, _ref, _uri = await gen.generate_video_async(
                prompt="一只猫走过屋顶",
                resource_type="videos",
                resource_id="E1S01",
                duration_seconds=5,
            )

        sent = json.loads(submit.calls.last.request.content)["prompt"]
        assert output.read_bytes() == MP4_BYTES
        assert gen.versions.add_calls[-1]["workflow_sha256"] == workflow_sha256(sent)
        assert gen.versions.add_calls[-1]["seed"] == sent["3"]["inputs"]["seed"]

    async def test_comfyui_execution_warnings_reach_the_caller(self, tmp_path):
        """Backend execution notices must reach task result.warnings; operations-only logs are insufficient."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities
        from lib.custom_provider.backends import CustomVideoBackend
        from lib.custom_provider.comfyui.comfyui_backend import ComfyuiVideoBackend

        gen = _build_generator(tmp_path)
        gen._video_provider_id = "custom-1"
        delegate = ComfyuiVideoBackend(
            provider_id="custom-1",
            model="wan-t2v",
            base_url="https://comfy.test",
            api_key="",
            definition=comfyui_endpoint_definition(),
        )
        gen._video_backend = CustomVideoBackend(
            provider_id="custom-1", delegate=delegate, model="wan-t2v"
        ).with_video_capabilities(VideoCapabilities(text_to_video=True), overrides={"text_to_video": True})
        history = {
            "status": {"completed": True},
            "outputs": {
                "9": {
                    "gifs": [
                        {"filename": "final_00001.mp4", "subfolder": "video", "type": "output"},
                        {"filename": "final_00002.mp4", "subfolder": "video", "type": "output"},
                    ]
                }
            },
        }
        collected: list[dict] = []

        with capture_http() as router, bounded_poll_clock():
            router.post("https://comfy.test/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get("https://comfy.test/history/p-1").mock(return_value=httpx.Response(200, json=history))
            router.get("https://comfy.test/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            await gen.generate_video_async(
                prompt="一只猫走过屋顶",
                resource_type="videos",
                resource_id="E1S01",
                duration_seconds=5,
                warnings=collected,
            )

        assert collected == [{"key": "comfyui_multiple_outputs", "params": {"count": 2, "filename": "final_00001.mp4"}}]

    async def test_comfyui_image_to_image_stores_the_artifact_as_an_image_version(self, tmp_path):
        """Image-backend end-to-end: reference upload → submission → polling → registered storyboard-image version.

        Use ``generate_image_async``, never backend directly: i2i gates, reference compression and version persistence live there and are invisible to backend unit tests."""
        from lib.custom_provider.backends import CustomImageBackend
        from lib.custom_provider.comfyui.comfyui_image_backend import ComfyuiImageBackend

        definition = comfyui_endpoint_definition(media_type="image")
        definition["workflow"]["20"] = {"class_type": "LoadImage", "inputs": {"image": "draft.png"}}
        definition["bindings"] = {
            "prompt": [{"node": "6", "input": "text", "class_type": "CLIPTextEncode"}],
            "reference_images": [{"node": "20", "input": "image", "class_type": "LoadImage"}],
            "seed": [{"node": "3", "input": "seed", "class_type": "KSampler", "policy": "random"}],
            "output": [{"node": "9", "class_type": "SaveImage"}],
        }
        definition["workflow"]["9"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}}

        gen = _build_generator(tmp_path)
        gen._image_provider_id = "custom-1"
        delegate = ComfyuiImageBackend(
            provider_id="custom-1",
            model="qwen-edit",
            base_url="https://comfy.test",
            api_key="",
            definition=definition,
            job_label="job-7",
        )
        gen._image_backend = CustomImageBackend(provider_id="custom-1", delegate=delegate, model="qwen-edit")
        reference = _solid_png(tmp_path, "ref.png", 64, 64)
        history = {
            "status": {"completed": True},
            "outputs": {"9": {"images": [{"filename": "ArcReel_00001_.png", "subfolder": "", "type": "output"}]}},
        }

        with capture_http() as router:
            upload = router.post("https://comfy.test/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "job-7-reference_images-1.png", "subfolder": "arcreel"})
            )
            submit = router.post("https://comfy.test/prompt").mock(
                return_value=httpx.Response(200, json={"prompt_id": "p-1"})
            )
            router.get("https://comfy.test/history/p-1").mock(return_value=httpx.Response(200, json=history))
            router.get("https://comfy.test/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            output, version = await gen.generate_image_async(
                prompt="把这张图改成夜景",
                resource_type="storyboards",
                resource_id="E1S01",
                reference_images=[reference],
            )

        assert output.read_bytes() == PNG_BYTES
        assert version == 1
        assert upload.call_count == 1
        assert gen.versions.add_calls[-1]["resource_id"] == "E1S01"
        sent = json.loads(submit.calls.last.request.content)["prompt"]
        assert sent["20"]["inputs"]["image"] == "arcreel/job-7-reference_images-1.png"
        assert gen.ledger.outcomes[0]["status"] == "success"

    async def test_cancelled_formal_image_generation_never_replaces_the_canonical_file(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend_written = asyncio.Event()
        keep_running = asyncio.Event()

        class _BlockingImageBackend(_FakeImageBackend):
            async def generate(self, request) -> ImageGenerationResult:
                await write_backend_output(request.output_path, b"new-image")
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

    async def test_generate_image_success_and_failure(self, tmp_path):
        gen = _build_generator(tmp_path)
        output_path, version = await gen.generate_image_async(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            aspect_ratio="9:16",
        )

        assert output_path.name == "scene_E1S01.png"
        assert version == 1
        assert gen.ledger.started[0]["call_type"] == "image"
        assert gen.ledger.outcomes[0]["status"] == "success"
        # Real Ledger union dispatch extracts usage_tokens; confirm call sites supplied backend result objects.
        assert gen.ledger.outcomes[0]["result"].usage_tokens == 8

        async def _raise(request):
            raise RuntimeError("boom")

        gen._image_backend.generate = _raise
        with pytest.raises(RuntimeError):
            await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="A")

        assert any(o["status"] == "failed" for o in gen.ledger.outcomes)

    async def test_call_records_store_artifact_path_relative_to_project(self, tmp_path):
        gen = _build_generator(tmp_path)

        await gen.generate_image_async(prompt="p", resource_type="storyboards", resource_id="E1S01")
        await gen.generate_video_async(prompt="p", resource_type="videos", resource_id="E1S01")

        assert [started["output_path"] for started in gen.ledger.started] == [
            "storyboards/scene_E1S01.png",
            "videos/scene_E1S01.mp4",
        ]

    @pytest.mark.asyncio
    async def test_generate_video_returns_versioned_result_and_defaults_unparsable_duration(self, tmp_path):
        gen = _build_generator(tmp_path)

        video_path, version, video_ref, video_uri = await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            duration_seconds="bad",
        )
        assert video_path.name == "scene_E1S01.mp4"
        assert version == 1
        assert video_ref is None
        assert video_uri == "video-uri"
        # Unparseable durations bill as eight seconds.
        assert gen.ledger.started[0]["duration_seconds"] == 8

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
        from lib.artifacts.version_manager import VersionManager

        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        events: list[object] = []

        class _Backend(_FakeVideoBackend):
            async def generate(self, request):
                events.append("provider")
                return await super().generate(request)

        gen._video_backend = _Backend()

        async def _checkpoint() -> dict[str, object]:
            events.append("checkpoint")
            return {"execution_task_id": "T-1"}

        await gen.generate_video_async(
            prompt="p",
            resource_type="reference_videos",
            resource_id="E1U1",
            before_submit=_checkpoint,
        )

        assert events == ["checkpoint", "provider"]
        history = gen.versions.get_versions("reference_videos", "E1U1")
        assert history["versions"][0]["execution_task_id"] == "T-1"

    @pytest.mark.asyncio
    async def test_video_before_submit_failure_prevents_provider_call(self, tmp_path):
        gen = _build_generator(tmp_path)

        async def _checkpoint() -> None:
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
            from arcreel_market_core.video_backend_contract import VideoCapabilities

            video_capabilities = VideoCapabilities(max_reference_images=9)

            def __init__(self):
                super().__init__(video_capabilities=type(self).video_capabilities)
                self.attempts = 0

            async def generate(self, request):
                self.attempts += 1
                self.calls.append(request)
                if self.attempts == 1:
                    raise _http_413_error()
                await write_backend_output(request.output_path, b"paid-video")
                return _FakeVideoResult()

        backend = _RetryBackend()
        gen._video_backend = backend
        checkpoint_calls = 0

        async def _checkpoint() -> None:
            nonlocal checkpoint_calls
            checkpoint_calls += 1

        await gen.generate_video_async(
            prompt="p",
            resource_type="reference_videos",
            resource_id="E1U1",
            reference_images=[ref],
            before_submit=_checkpoint,
        )

        assert backend.attempts == 2
        assert checkpoint_calls == 1

    @pytest.mark.asyncio
    async def test_video_413_after_provider_acceptance_never_resubmits(self, tmp_path):
        gen = _build_generator(tmp_path)
        ref = _solid_png(tmp_path, "ref-after-submit.png", 16, 16)

        class _Poll413Backend(_FakeVideoBackend):
            from arcreel_market_core.video_backend_contract import VideoCapabilities

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
        from lib.artifacts.version_manager import VersionManager

        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-current")

        class _FailAfterWrite(_FakeVideoBackend):
            async def generate(self, request):
                await write_backend_output(request.output_path, b"partial-new")
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
        from lib.artifacts.version_manager import VersionManager

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
        from lib.artifacts.version_manager import VersionManager

        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)

        events = []

        async def _prepare(staged_file, duration_seconds, version_metadata):
            assert await asyncio.to_thread(staged_file.read_bytes) == b"fake-video-data"
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
        assert Path(gen.project_path / history["versions"][0]["file"]).read_bytes() == b"fake-video-data"  # noqa: ASYNC240 -- Test-local small-file I/O/assertions, outside production event loops
        assert events == ["prepared", "committed"]

    @pytest.mark.asyncio
    async def test_formal_video_without_commit_callback_keeps_paid_history_but_never_selects_it(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.artifacts.version_manager import VersionManager

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
        from lib.artifacts.version_manager import VersionManager

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
        from lib.artifacts.version_manager import VersionManager

        gen = _build_generator(tmp_path)
        gen.versions = VersionManager(gen.project_path)
        current = gen._get_output_path("reference_videos", "E1U1")
        current.parent.mkdir(parents=True)
        staged = task_video_staging_path(current, "task-restarted")
        staged.write_bytes(b"interrupted-download")

        class _CapturePathBackend(_FakeVideoBackend):
            async def generate(self, request):
                assert request.output_path == staged
                assert not await asyncio.to_thread(request.output_path.exists)
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
        from lib.generation import media_generator

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
    async def test_generate_video_async_segment_id_by_resource_type(self, tmp_path):
        """Video accounting segment_id allowlists storyboards/videos/reference_videos; other resources return None."""
        gen = _build_generator(tmp_path)

        await gen.generate_video_async(prompt="p", resource_type="videos", resource_id="E1S01")
        assert gen.ledger.started[-1]["segment_id"] == "E1S01"

        await gen.generate_video_async(prompt="p", resource_type="reference_videos", resource_id="E1U1")
        assert gen.ledger.started[-1]["segment_id"] == "E1U1"

        # Negative boundary: unallowlisted resource_type still returns None, retaining allowlist semantics
        # against unnoticed unconditional forwarding. grids belongs only to image accounting allowlists.
        await gen.generate_video_async(prompt="p", resource_type="grids", resource_id="E1G1")
        assert gen.ledger.started[-1]["segment_id"] is None

    @pytest.mark.asyncio
    async def test_video_billed_duration_passed_to_finish_call(self, tmp_path):
        """If backend returns actual billed duration differing from request, forward it to finish_call."""
        gen = _build_generator(tmp_path)
        gen._video_backend = _FakeVideoBackend(result_duration_seconds=15)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S10",
            duration_seconds="6",
        )
        assert gen.ledger.started[-1]["duration_seconds"] == 6
        # Real Ledger union dispatch extracts billed_duration_seconds; confirm submission of duration=15 result.
        assert gen.ledger.outcomes[-1]["result"].duration_seconds == 15

    @pytest.mark.asyncio
    async def test_video_billed_duration_lands_in_ledger(self, tmp_path):
        """End-to-end real Ledger persists backend-reported billed duration, even when different from requested duration."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from lib.billing.ledger import Ledger
        from lib.db.base import Base
        from lib.db.models.api_call import ApiCall

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

            # Read persisted rows directly.
            async with factory() as session:
                item = (await session.execute(select(ApiCall))).scalars().one()
            assert item.status == "success"
            assert item.duration_seconds == 15
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_video_generate_audio_from_config_resolver(self, tmp_path):
        """generate_video_async obtains audio configuration from ConfigResolver."""
        gen = _build_generator(tmp_path)
        gen._config = FakeConfigResolver(requested_generate_audio=False)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S03",
        )
        # VideoBackend honors ConfigResolver values.
        assert gen.ledger.started[-1]["generate_audio"] is False

    @pytest.mark.asyncio
    async def test_video_generate_audio_respects_config_true(self, tmp_path):
        """video_backend honors ConfigResolver True."""
        gen = _build_generator(tmp_path)
        gen._config = FakeConfigResolver(requested_generate_audio=True)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S04",
        )
        assert gen.ledger.started[-1]["generate_audio"] is True

    @pytest.mark.asyncio
    async def test_video_generate_audio_defaults_true_when_config_none(self, tmp_path):
        """Without self._config, fallback defaults True, matching ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO."""
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
        """last_frame=False hard-fails without provider calls, accounting or degrading to reference images."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoCapabilityError

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
        """If video_capabilities_for_tier exists, narrow actual service_tier to decide end_image: pro permits it even when context-free video_capabilities says False."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

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

    @pytest.fixture
    def user_added_video_model(self):
        """Load user-added video models under built-ins (key equals _FakeVideoBackend.model), applying parameterized capability overrides."""
        from lib.catalog import query as catalog

        def install(provider_id: str, overrides: dict[str, object], *, entry_binding: str | None = None) -> None:
            catalog.install_user_added_models(
                [
                    catalog.UserAddedModel(
                        provider_id,
                        _FakeVideoBackend.model,
                        "video",
                        entry_binding=entry_binding,
                        capability_overrides=overrides,
                    )
                ]
            )

        yield install
        catalog.install_user_added_models(())

    async def test_user_override_relaxes_execution_gate(self, tmp_path, user_added_video_model):
        """User-relaxed built-in end-frame capabilities pass execution gates and actually submit."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

        user_added_video_model("ark", {"last_frame": True})
        gen = _build_generator(tmp_path)
        gen._video_provider_id = "ark"
        gen._video_backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(last_frame=False))
        end_image = tmp_path / "end.png"
        end_image.write_bytes(b"fake-end-image")

        await gen.generate_video_async(prompt="p", resource_type="videos", resource_id="E1S08", end_image=end_image)

        assert gen._video_backend.calls[0].end_image == end_image

    async def test_user_override_narrows_execution_gate(self, tmp_path, user_added_video_model):
        """User-disabled built-in end-frame capabilities also block at execution, without backend calls or accounting."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoCapabilityError

        user_added_video_model("ark", {"last_frame": False})
        gen = _build_generator(tmp_path)
        gen._video_provider_id = "ark"
        gen._video_backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(last_frame=True))
        end_image = tmp_path / "end.png"
        end_image.write_bytes(b"fake-end-image")

        with pytest.raises(VideoCapabilityError) as exc:
            await gen.generate_video_async(prompt="p", resource_type="videos", resource_id="E1S09", end_image=end_image)

        assert exc.value.code == "video_last_frame_unsupported"
        assert gen._video_backend.calls == []
        assert gen.ledger.started == []

    async def test_request_carries_overlaid_capabilities(self, tmp_path, user_added_video_model):
        """Backend requests carry the same overlaid capabilities used by execution gates for input decisions."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

        user_added_video_model("dashscope", {"last_frame": True, "max_reference_images": 6})
        gen = _build_generator(tmp_path)
        gen._video_provider_id = "dashscope"
        gen._video_backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(last_frame=False))

        await gen.generate_video_async(prompt="p", resource_type="videos", resource_id="E1S11")

        caps = gen._video_backend.calls[0].video_capabilities
        assert caps.last_frame is True
        assert caps.max_reference_images == 6

    async def test_user_override_narrows_first_frame(self, tmp_path, user_added_video_model):
        """User-disabled built-in first-frame capabilities block first-frame requests before backend/accounting."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoCapabilityError

        user_added_video_model("kling", {"first_frame": False})
        gen = _build_generator(tmp_path)
        gen._video_provider_id = "kling"
        gen._video_backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(first_frame=True))
        start_image = tmp_path / "start.png"
        Image.new("RGB", (8, 8)).save(start_image)

        with pytest.raises(VideoCapabilityError) as exc:
            await gen.generate_video_async(
                prompt="p", resource_type="videos", resource_id="E1S12", start_image=start_image
            )

        assert exc.value.code == "video_first_frame_unsupported"
        assert gen._video_backend.calls == []
        assert gen.ledger.started == []

    async def test_user_override_keeps_tier_aware_base(self, tmp_path, user_added_video_model):
        """Apply overrides atop tier-resolved capabilities and entry-side evaluation: untouched end-frame support follows tier permission when entries accept it, while overridden reference limits also apply."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

        class _TierAwareVideoBackend(_FakeVideoBackend):
            def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None):
                return VideoCapabilities(last_frame=service_tier == "pro")

        user_added_video_model("kling", {"max_reference_images": 2}, entry_binding="kuaishou/kling-v3")
        gen = _build_generator(tmp_path)
        gen._video_provider_id = "kling"
        gen._video_backend = _TierAwareVideoBackend(video_capabilities=VideoCapabilities())
        end_image = tmp_path / "end.png"
        end_image.write_bytes(b"fake-end-image")
        refs = []
        for i in range(2):
            ref = tmp_path / f"ref{i}.png"
            Image.new("RGB", (8, 8)).save(ref)
            refs.append(ref)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S10",
            end_image=end_image,
            reference_images=refs,
            service_tier="pro",
        )

        call = gen._video_backend.calls[0]
        assert call.end_image == end_image
        assert len(call.reference_images) == 2

    @pytest.mark.asyncio
    async def test_end_image_rejected_when_tier_aware_backend_reports_std_tier(self, tmp_path):
        """The same backend at std tier still hard-fails narrowed capabilities, covering pro/std branches."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoCapabilityError

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
        """Legacy/direct callers use end_image="" for no end frame; admit even without last_frame support, matching Kling _build_payload truthiness compatibility."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

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


# ── Shared-boundary reference compression wiring ────────────────────────────────────────────────────

from PIL import Image

from lib.generation.media_generator import _is_413
from lib.references.reference_compression import LADDER_STEPS, ReferencePayloadFloorError


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
    """Image backend with configurable 413 failure count, recording every reference path."""

    name = "fake-image"
    model = "img-model"
    capabilities: ClassVar[set[ImageCapability]] = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def reference_image_limit(self) -> int | None:
        return None

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
        await write_backend_output(request.output_path, b"img")
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
        # Fix ④: never match bare "413", avoiding byte-count/request-ID false positives.
        assert _is_413(RuntimeError("only 41300 bytes uploaded")) is False
        assert _is_413(RuntimeError("error code 413xyz")) is False

    def test_non_413_status(self):
        req = httpx.Request("POST", "https://example.test")
        resp = httpx.Response(status_code=400, request=req)
        err = httpx.HTTPStatusError("bad request", request=req, response=resp)
        assert _is_413(err) is False

    def test_sdk_status_code_attr(self):
        # OpenAI/xai-style SDK exceptions expose .status_code.
        class _SdkErr(Exception):
            status_code = 413

        assert _is_413(_SdkErr("too big")) is True

    def test_sdk_code_attr(self):
        # google-genai APIError exposes .code.
        class _ApiErr(Exception):
            code = 413

        assert _is_413(_ApiErr("Request payload size exceeds the limit")) is True

    def test_sdk_non_413_code_not_matched(self):
        class _ApiErr(Exception):
            code = 400

        assert _is_413(_ApiErr("bad request")) is False

    def test_string_status_code_413(self):
        # Some SDKs/mocks expose string "413"; defensively convert to int.
        class _StrErr(Exception):
            status_code = "413"

        assert _is_413(_StrErr("too big")) is True

    def test_non_numeric_status_code_falls_back_to_phrase(self):
        # Nonnumeric statuses must not raise ValueError; fall back to phrase matching.
        class _WeirdErr(Exception):
            status_code = "not-a-number"

        assert _is_413(_WeirdErr("totally unrelated")) is False
        assert _is_413(_WeirdErr("request entity too large")) is True


class TestProjectRelativePath:
    """Normalize accounting paths to project-relative form even if project root or media paths were supplied relatively."""

    def test_absolute_material_under_relative_project_root_is_relativised(self, tmp_path, monkeypatch):
        from lib.generation.media_generator import _project_relative_path

        monkeypatch.chdir(tmp_path)
        (tmp_path / "demo" / "characters").mkdir(parents=True)

        assert _project_relative_path(Path("demo"), tmp_path / "demo" / "characters" / "a.png") == "characters/a.png"
        assert _project_relative_path(tmp_path / "demo", "demo/characters/a.png") == "characters/a.png"

    def test_material_outside_project_keeps_its_own_form(self, tmp_path):
        from lib.generation.media_generator import _project_relative_path

        assert (
            _project_relative_path(tmp_path / "demo", tmp_path / "elsewhere" / "a.png")
            == (tmp_path / "elsewhere" / "a.png").as_posix()
        )
        assert _project_relative_path(tmp_path / "demo", object()) is None


class TestReferenceCompressionSeam:
    async def test_cancel_during_compression_still_removes_the_compressed_copies(self, tmp_path, monkeypatch):
        """Cancellation during threaded compression still cleans completed temporary copies without provider calls."""
        from lib.references import reference_compression

        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend()
        gen._image_backend = backend
        src = _noise_png(tmp_path, "ref.png", 64, 64)
        compressing = threading.Event()
        release = threading.Event()
        cleaned: list[bool] = []

        @contextlib.contextmanager
        def _slow_payload(specs, *, limits, start_step=0):
            compressing.set()
            release.wait(5)
            try:
                yield start_step, []
            finally:
                cleaned.append(True)

        monkeypatch.setattr(reference_compression, "compressed_reference_payload", _slow_payload)
        task = asyncio.create_task(
            gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
                reference_images=[str(src)],
            )
        )
        await asyncio.to_thread(compressing.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned == [True]
        assert backend.calls == []

    async def test_cancel_during_compressed_copy_cleanup_keeps_the_paid_result(self, tmp_path, monkeypatch):
        """After provider returns, cancellation during threaded compressed-copy cleanup still settles cleanup, commits and returns paid results."""
        from lib.references import reference_compression

        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend()
        gen._image_backend = backend
        src = _noise_png(tmp_path, "ref.png", 64, 64)
        cleaning = threading.Event()
        release = threading.Event()

        @contextlib.contextmanager
        def _slow_cleanup_payload(specs, *, limits, start_step=0):
            try:
                yield start_step, []
            finally:
                cleaning.set()
                release.wait(5)

        monkeypatch.setattr(reference_compression, "compressed_reference_payload", _slow_cleanup_payload)
        task = asyncio.create_task(
            gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
                reference_images=[str(src)],
            )
        )
        await asyncio.to_thread(cleaning.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        release.set()

        output_path, version = await task
        assert version == 1
        assert await asyncio.to_thread(output_path.read_bytes) == b"img"
        assert len(backend.calls) == 1

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
        # Backend receives compressed temporary copies, not source paths.
        assert received[0] != src
        # Original bytes unchanged; only upload copies change.
        assert src.read_bytes() == src_bytes_before
        # Clean temporary copies on exit.
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

        # One 413 then successful downgrade calls backend twice.
        assert len(backend.calls) == 2
        # Record one success only; inner 413 retries create no extra accounting calls.
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

        # Exhaust baseline + LADDER_STEPS-1 tiers + floor after LADDER_STEPS+1 calls.
        assert len(backend.calls) == LADDER_STEPS + 1
        # Exhaustion propagates to outer except, recording one failure.
        assert len(gen.ledger.outcomes) == 1
        assert gen.ledger.outcomes[0]["status"] == "failed"

    async def test_t2i_no_refs_413_not_converted_to_floor(self, tmp_path):
        # T2I without references: 413 is unrelated to images; neither convert to floor errors nor downgrade.
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend(fail_413_times=99)
        gen._image_backend = backend

        with pytest.raises(httpx.HTTPStatusError):
            await gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
            )
        # One call without downgrade retries.
        assert len(backend.calls) == 1

    async def test_video_frame_not_resized_array_laddered(self, tmp_path):
        gen = _build_generator(tmp_path)

        from arcreel_market_core.video_backend_contract import VideoCapabilities

        class _CapturingVideoBackend:
            name = "fake-video"
            model = "video-model"
            # Reference-image requests first pass gate_video_request; substitutes must declare sufficient capacity
            # or capability guards reject before generate.
            video_capabilities = VideoCapabilities(max_reference_images=9)

            def __init__(self):
                self.start_dims = None
                self.ref_dims: list[tuple[int, int]] = []

            async def generate(self, request):
                def _read_dims() -> None:
                    if request.start_image:
                        with Image.open(request.start_image) as im:
                            self.start_dims = im.size
                    for r in request.reference_images or []:
                        with Image.open(r) as im:
                            self.ref_dims.append(im.size)

                await asyncio.to_thread(_read_dims)
                await write_backend_output(request.output_path, b"v")
                return _FakeVideoResult()

        backend = _CapturingVideoBackend()
        gen._video_backend = backend

        start = _solid_png(tmp_path, "start.png", 3000, 2000)  # FRAME never resizes; reformatting only reencodes.
        ref = _solid_png(tmp_path, "ref.png", 3000, 3000)  # ARRAY follows the ladder to ≤2048.

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            start_image=str(start),
            reference_images=[ref],
        )

        assert backend.start_dims == (3000, 2000)  # FRAME dimensions unchanged.
        assert max(backend.ref_dims[0]) == 2048  # ARRAY long edge becomes 2048.

    async def test_provider_asset_slot_is_sent_as_reference_without_upload_or_compression(self, tmp_path):
        """Asset-reference images skip reading/compression, preserving original image positions across requests, ordering and accounting inputs."""
        from unittest.mock import MagicMock

        from lib.backends.video_backends.ark import Seedance2VideoBackend
        from tests.fakes import captured_ark_clients

        gen = _build_generator(tmp_path)
        client = MagicMock()
        client.content_generation.tasks.create.return_value = MagicMock(id="cgt-asset")
        client.content_generation.tasks.get.side_effect = RuntimeError("stop after submit")
        with captured_ark_clients("lib.backends.video_backends.ark", client):
            gen._video_backend = Seedance2VideoBackend(api_key="TEST-ark-key", model="doubao-seedance-2-0-260128")
        # Send a character image large enough to require compression as an asset reference; scene images still compress inline.
        character = _noise_png(gen.project_path, "character.png", 3000, 3000)
        scene = _noise_png(gen.project_path, "scene.png", 3000, 3000)

        with pytest.raises(RuntimeError, match="stop after submit"):
            await gen.generate_video_async(
                prompt="@图片1 走进 @图片2",
                resource_type="reference_videos",
                resource_id="E1U1",
                reference_images=[character, scene],
                reference_image_asset_uris=["asset://asset-alice-1", None],
            )

        content = client.content_generation.tasks.create.call_args.kwargs["content"]
        urls = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
        assert urls[0] == "asset://asset-alice-1"
        assert urls[1].startswith("data:image/jpeg;base64,")
        assert gen.ledger.started[0]["inputs"]["reference_images"] == [
            {"path": "character.png", "label": None, "role": "array", "provider_asset": "asset://asset-alice-1"},
            {"path": "scene.png", "label": None, "role": "array"},
        ]

    async def test_provider_asset_reference_requires_a_backend_that_declares_it(self, tmp_path):
        """Backends lacking asset-reference support might ignore fields and silently send image bytes; reject before submission."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

        gen = _build_generator(tmp_path)
        backend = _FakeVideoBackend(video_capabilities=VideoCapabilities(max_reference_images=9))
        gen._video_backend = backend
        ref = _solid_png(tmp_path, "ref.png", 64, 64)

        with pytest.raises(ValueError, match="provider asset"):
            await gen.generate_video_async(
                prompt="p",
                resource_type="reference_videos",
                resource_id="E1U1",
                reference_images=[ref],
                reference_image_asset_uris=["asset://asset-alice-1"],
            )
        assert backend.calls == []

    async def test_reference_audio_reaches_backend_unreordered(self, tmp_path):
        """Forward reference audio unchanged after gate admission, or it would disappear silently after validation.

        Audio never enters image-only compression specs, so never reorder it with compression output. Order defines prompt “音频N”; reordering assigns character A's voice to B."""
        gen = _build_generator(tmp_path)

        from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities

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
                await write_backend_output(request.output_path, b"v")
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
        """Declared total-duration limits block before backend.generate paid requests."""
        from arcreel_market_core.video_backend_contract import (
            ReferenceAudioMode,
            VideoCapabilities,
            VideoCapabilityError,
        )
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
        """If declared duration limits cannot be probed (such as missing bundled ffmpeg), None follows existing degraded allowance rather than blocking."""
        from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities

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
                await write_backend_output(request.output_path, b"v")
                return _FakeVideoResult()

        gen._video_backend = _AudioDurationLimitedVideoBackend()

        async def _failing_probe(paths):
            return None

        monkeypatch.setattr("lib.generation.media_generator.probe_reference_audio_total_seconds", _failing_probe)

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
        """Without declared total-duration limits, avoid extra probe subprocesses per request; probe lazily by capabilities."""
        from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities

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
                await write_backend_output(request.output_path, b"v")
                return _FakeVideoResult()

        gen._video_backend = _NoDurationLimitVideoBackend()

        probe_calls: list[list[Path]] = []

        async def _recording_probe(paths):
            probe_calls.append(list(paths))
            return 0.0

        monkeypatch.setattr("lib.generation.media_generator.probe_reference_audio_total_seconds", _recording_probe)

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
        """Oversized prompts block before paid backend.generate calls without accounting rows."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoCapabilityError

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
        """Prompt limits apply to every request, including text-only/first-frame paths without end frames, reference images or audio."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

        gen = _build_generator(tmp_path)

        class _PromptLimitedVideoBackend:
            name = "fake-video"
            model = "video-model"
            video_capabilities = VideoCapabilities(max_prompt_chars=10)

            async def generate(self, request):
                await write_backend_output(request.output_path, b"v")
                return _FakeVideoResult()

        gen._video_backend = _PromptLimitedVideoBackend()

        await gen.generate_video_async(prompt="x" * 10, resource_type="videos", resource_id="E1S01")

        assert gen.ledger.outcomes


class TestFirstFrameRatioAdaptiveOnly:
    """Shared VideoCapabilities.first_frame_ratio_adaptive_only enforcement.

    Cover lib.backends.video_frame_slots.resolve_first_frame_aspect_ratio at MediaGenerator.generate_video_async. A backend capability seam declares this constraint synthetically to test enforcement independently of actual providers declaring it."""

    async def test_first_frame_task_forced_to_adaptive(self, tmp_path):
        from arcreel_market_core.video_backend_contract import VideoCapabilities

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
        """Without first frames (text-only/reference-only), this constraint leaves user aspect ratios unchanged."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

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
        """Default False for undeclared existing models preserves first-frame request payloads."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

        gen = _build_generator(tmp_path)
        backend = _FakeVideoBackend(
            video_capabilities=VideoCapabilities()
        )  # Default tier without this constraint declared.
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
        """Accounting retains original user aspect-ratio intent separately from actual backend values."""
        from arcreel_market_core.video_backend_contract import VideoCapabilities

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


class _BuiltinConfig:
    """Built-in assembly credential substitute answering only provider config needed by ``assemble_backend`` loading."""

    async def provider_config_with_credential(self, provider_id: str) -> tuple[dict[str, str], None]:
        return {"api_key": f"TEST-{provider_id}-key"}, None


_OPAQUE = "opaque-relay-model"


async def _builtin_video_backend(provider_id: str, model_id: str, entry_binding: str | None):
    """Construct built-in video models through production assembly; supplied ``entry_binding`` represents user-added models bound to entries."""
    from lib.backends.backend_assembly.assembler import assemble_backend
    from lib.catalog import query as catalog

    if entry_binding is not None:
        catalog.install_user_added_models(
            [catalog.UserAddedModel(provider_id, model_id, "video", entry_binding=entry_binding)]
        )
    return await assemble_backend(
        provider_id=provider_id,
        media_type="video",
        model_id=model_id,
        resolver=_BuiltinConfig(),
    )


def _custom_video_backend(endpoint: str, model_id: str, entry_binding: str | None):
    """Construct custom-provider video models through production factories."""
    from types import SimpleNamespace

    from lib.custom_provider.factory import create_custom_backend

    provider = SimpleNamespace(provider_id="custom-9", base_url="https://relay.test", api_key="TEST-relay-key")
    return create_custom_backend(
        provider=provider,
        model_id=model_id,
        endpoint=endpoint,
        entry_binding=entry_binding,
    )


@pytest.fixture
def _reset_user_added_models():
    from lib.catalog import query as catalog

    yield
    catalog.install_user_added_models(())


@pytest.mark.usefixtures("_reset_user_added_models")
class TestEntryBoundVideoRequests:
    """Opaque IDs bound to entries produce the same actual MediaGenerator request as official models while retaining original API IDs. Production-assembled backend capabilities fill request gates; never replace request facts, intercept only outbound submission."""

    @staticmethod
    def _generator(tmp_path: Path, backend, provider_id: str) -> MediaGenerator:
        gen = _build_generator(tmp_path)
        gen._video_backend = backend
        gen._video_provider_id = provider_id
        gen._config = FakeConfigResolver(requested_generate_audio=True)
        return gen

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("model", "binding"), [(_OPAQUE, "kuaishou/kling-v2-6"), ("kling-v2-6", None)])
    async def test_kling_720p_audio_follows_the_bound_entry(self, tmp_path, model, binding):
        """Kling v2-6 audio requires 1080P; opaque v2-6 bindings also omit audio tracks at 720p."""
        gen = self._generator(tmp_path, _custom_video_backend("kling-video-pro-audio", model, binding), "custom-9")

        with capture_http() as router:
            submit = router.post(url__startswith="https://relay.test/v1/videos/").mock(
                return_value=httpx.Response(400, json={"code": 1201, "message": "stop"})
            )
            with pytest.raises(httpx.HTTPStatusError):
                await gen.generate_video_async(
                    prompt="p", resource_type="videos", resource_id="E1S01", duration_seconds=5, resolution="720p"
                )

        body = request_json(only_request(submit))
        assert body["model_name"] == model
        assert "sound" not in body

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("model", "binding"), [(_OPAQUE, "agnes/agnes-video-2.5"), ("agnes-video-2.5", None)])
    async def test_agnes_bound_to_2_5_uses_the_2_5_contract(self, tmp_path, model, binding):
        """Opaque Agnes 2.5 bindings use 2.5 contracts: five images in reference mode, never rejected by v2.0 four-image limits."""
        backend = await _builtin_video_backend("agnes", model, binding)
        gen = self._generator(tmp_path, backend, "agnes")
        refs = []
        for i in range(5):
            ref = tmp_path / f"ref{i}.png"
            ref.write_bytes(PNG_BYTES)
            refs.append(ref)

        with capture_http() as router:
            submit = router.post(url__regex=r".*/v1/videos$").mock(
                return_value=httpx.Response(400, json={"error": {"message": "stop"}})
            )
            with pytest.raises(httpx.HTTPStatusError):
                await gen.generate_video_async(
                    prompt="p",
                    resource_type="reference_videos",
                    resource_id="E1U1",
                    reference_images=refs,
                    duration_seconds=5,
                )

        body = request_json(only_request(submit))
        assert body["model"] == model
        assert body["mode"] == "reference"
        assert len(body["images"]) == 5
        assert "num_frames" not in body

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("model", "binding"), [(_OPAQUE, "xai/grok-imagine-video"), ("grok-imagine-video", None)])
    async def test_grok_bound_to_classic_rejects_first_frame_with_references(self, tmp_path, model, binding):
        """Opaque classic bindings likewise reject coexisting first frames/references before paid calls."""
        from arcreel_market_core.video_backend_contract import VideoCapabilityError

        backend = await _builtin_video_backend("grok", model, binding)
        gen = self._generator(tmp_path, backend, "grok")
        start = tmp_path / "start.png"
        start.write_bytes(PNG_BYTES)
        ref = tmp_path / "ref.png"
        ref.write_bytes(PNG_BYTES)

        with pytest.raises(VideoCapabilityError) as exc:
            await gen.generate_video_async(
                prompt="p",
                resource_type="reference_videos",
                resource_id="E1U1",
                start_image=start,
                reference_images=[ref],
                duration_seconds=5,
            )
        assert exc.value.code == "video_reference_images_with_frames_unsupported"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("model", "binding"), [(_OPAQUE, "openai/sora-2-pro"), ("sora-2-pro", None)])
    async def test_openai_bound_to_sora_2_pro_reaches_1080p(self, tmp_path, model, binding):
        """Opaque sora-2-pro bindings support 1080p sizes like official sora-2-pro."""
        gen = self._generator(tmp_path, _custom_video_backend("openai-video", model, binding), "custom-9")

        with capture_http() as router:
            submit = router.post("https://relay.test/v1/videos").mock(
                return_value=httpx.Response(400, json={"error": {"message": "stop", "type": "invalid_request_error"}})
            )
            with pytest.raises(openai.BadRequestError):
                await gen.generate_video_async(
                    prompt="p", resource_type="videos", resource_id="E1S01", duration_seconds=4, resolution="1080p"
                )

        request = only_request(submit)
        assert f'name="model"\r\n\r\n{model}'.encode() in request.content
        assert b"1080x1920" in request.content

    def test_gemini_vertex_endpoint_keeps_the_audio_switch_with_or_without_a_bound_entry(self):
        """Vertex endpoint declares switchable audio; bound entries, official names and unlisted models share evaluation rules."""
        from lib.catalog import query as catalog
        from lib.config.resolver import builtin_video_audio_track

        catalog.install_user_added_models(
            [
                catalog.UserAddedModel("gemini-vertex", _OPAQUE, "video", entry_binding="google/veo-3.1-generate-001"),
                catalog.UserAddedModel("gemini-vertex", "veo-unbound-relay", "video"),
            ]
        )
        assert builtin_video_audio_track("gemini-vertex", _OPAQUE, generation_type="i2v") == "controllable"
        assert (
            builtin_video_audio_track("gemini-vertex", "veo-3.1-generate-001", generation_type="i2v") == "controllable"
        )
        assert builtin_video_audio_track("gemini-vertex", "veo-unbound-relay", generation_type="i2v") == "controllable"

    @pytest.mark.parametrize("model", [_OPAQUE, "veo-3.1-generate-001"])
    def test_gemini_aistudio_endpoint_keeps_audio_on_even_with_a_vertex_entry(self, model):
        """AI Studio endpoint declares always-audible tracks, unchanged by Vertex entry binding or API model names."""
        from lib.catalog import query as catalog
        from lib.config.resolver import builtin_video_audio_track

        catalog.install_user_added_models(
            [catalog.UserAddedModel("gemini-aistudio", model, "video", entry_binding="google/veo-3.1-generate-001")]
        )

        assert builtin_video_audio_track("gemini-aistudio", model, generation_type="i2v") == "always_on"
