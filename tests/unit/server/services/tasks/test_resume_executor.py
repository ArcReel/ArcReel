"""Resume executor unit tests.

Focus:
- resume_executor directly calls generator.resume_video_async (→ backend.resume_video), never generate_video_async (→ backend.generate), avoiding duplicate charges.
- Skip storyboard/reference local-file existence validation: the provider job is already running.
- Propagate ResumeExpiredError through the call chain for worker mark_failed recognition."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from arcreel_market_core.video_backend_contract import ResumeExpiredError
from lib.artifacts.artifact_manifest import ArtifactBasis, compose_video_artifact_basis
from lib.artifacts.version_manager import PaidVersionCommit
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.infra.content_digest import canonical_json, canonical_json_digest
from lib.script.reference_video.execution_checkpoint import (
    StagedProviderMedia,
    StoryboardSubmissionCheckpoint,
)
from tests.fakes import use_project_manager, use_video_thumbnail_extractor, write_backend_output


class _FakeProjectManager:
    def __init__(self, project_path: Path, project: dict[str, Any]) -> None:
        self.project_path = project_path
        self.project = project
        self.scene_assets: list[dict[str, Any]] = []

    def load_project(self, _project_name: str) -> dict[str, Any]:
        return self.project

    def get_project_path(self, _project_name: str) -> Path:
        return self.project_path

    def update_scene_asset(self, **kwargs: Any) -> None:
        self.scene_assets.append(kwargs)


class _FakeGenerator:
    """MediaGenerator substitute: controllable resume_video_async and versions history lookup."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.resume_calls: list[dict[str, Any]] = []
        self.raises = raises
        self.versions = self  # Let generator.versions.get_versions use its own implementation.

    async def resume_video_async(self, **kwargs: Any) -> tuple[Path, int, Any, str | None]:
        self.resume_calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        output_path = kwargs.get("output_path") or Path(tempfile.gettempdir()) / "video.mp4"
        # Production backends write artifacts in worker threads; substitutes likewise avoid blocking the event loop.
        await write_backend_output(output_path, b"paid-resume-video")
        prepare = kwargs.get("before_formal_commit")
        if prepare is not None:
            metadata = {key: value for key, value in kwargs.items() if key.startswith(("execution_", "artifact_"))}
            await prepare(output_path, int(kwargs.get("duration_seconds") or 8), metadata)
        commit = kwargs.get("commit_formal_output")
        if commit is not None:
            commit.outcome = PaidVersionCommit(version=3, selected=True)
        return output_path, 3, None, "video-uri-xyz"

    def get_versions(self, _resource_type: str, _resource_id: str) -> dict[str, Any]:
        return {"versions": [{"created_at": "2026-05-26T00:00:00Z"}]}


@pytest.fixture
def fake_pm(tmp_path: Path) -> _FakeProjectManager:
    project_path = tmp_path / "projects" / "demo"
    (project_path / "videos").mkdir(parents=True, exist_ok=True)
    (project_path / "thumbnails").mkdir(parents=True, exist_ok=True)
    return _FakeProjectManager(
        project_path=project_path,
        project={"content_mode": "narration", "default_duration": 8, "aspect_ratio": "9:16"},
    )


@pytest.fixture
def video_task() -> dict[str, Any]:
    task = {
        "task_id": "T-1",
        "task_type": "video",
        "media_type": "video",
        "project_name": "demo",
        "resource_id": "E1S01",
        "provider_id": "openai",
        "provider_job_id": "openai-job-1",
        "script_file": "episode_1.json",
        "payload": {"script_file": "episode_1.json", "prompt": "p"},
    }
    task["execution_checkpoint_json"] = _storyboard_checkpoint_json()
    return task


def _storyboard_checkpoint_json(
    *,
    task_id: str = "T-1",
    provider_id: str = "openai",
    provider_model_id: str = "sora-2",
    backend_model_id: str = "sora-2",
    endpoint_guard: str | None = None,
) -> str:
    media = StagedProviderMedia(
        index=0,
        role="start_image",
        logical_type="storyboard",
        logical_name="E1S01",
        kind="first_frame",
        source_locator="storyboards/scene_E1S01.png",
        staged_locator=f".arcreel/tasks/{task_id}/provider_media/000-start_image.png",
        sha256="a" * 64,
        size_bytes=1,
    )
    visual = ArtifactBasis.build(
        "artifact-visual/video-storyboard",
        kind_version=1,
        inputs={
            "resource_id": "E1S01",
            "visual_prompt": {"action": "Run.", "camera_motion": "Static"},
            "canvas": {"aspect_ratio": "9:16"},
            "frames": [{"role": "storyboard", "sha256": "a" * 64}],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = ArtifactBasis.build(
        "artifact-speech/video-duration",
        kind_version=1,
        inputs={"request_duration_seconds": 8},
    )
    return StoryboardSubmissionCheckpoint.create(
        task_id=task_id,
        project_name="demo",
        script_file="episode_1.json",
        unit_id="E1S01",
        generation_type="i2v",
        provider_id=provider_id,
        provider_model_id=provider_model_id,
        backend_model_id=backend_model_id,
        endpoint_guard=endpoint_guard,
        prompt="p",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="720p",
        generate_audio=True,
        service_tier="default",
        seed=None,
        visual_basis_digest="b" * 64,
        artifact_currency=VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=8,
            visual_basis=visual,
            speech_basis=speech,
            duration_basis=duration,
            video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
            voice_style_speakers=(),
            duration_tiers=(8,),
            reference_image_limit=None,
            parent_version=0,
        ),
        media=(media,),
        reference_audio_targets=None,
    ).to_json()


def _fake_video_context(
    fake_generator: _FakeGenerator,
    *,
    endpoint: str | None = None,
    provider_id: str = "openai",
    provider_model_id: str = "sora-2",
    backend_model_id: str = "sora-2",
):
    """Wrap the fake generator in GenerationContext.

    Declare video lane: resume reads ``ctx.video.endpoint`` for endpoint comparison as well as generator, matching production ``video=VideoLaneRequest()``."""
    from lib.config.resolver import ProviderModel
    from server.services.tasks.generation_context import GenerationContext, VideoLaneResult

    return GenerationContext(
        generator=fake_generator,
        video_lane=VideoLaneResult(
            provider_model=ProviderModel(provider_id, provider_model_id),
            backend_name="openai",
            backend_model=backend_model_id,
            resolution="720p",
            endpoint=endpoint,
        ),
    )


def _patch_resume_executor_deps(
    monkeypatch,
    fake_pm: _FakeProjectManager,
    fake_generator: _FakeGenerator,
    *,
    endpoint: str | None = None,
    provider_id: str = "openai",
    provider_model_id: str = "sora-2",
    backend_model_id: str = "sora-2",
) -> None:
    """Patch both resume_executor pm/generator sources, reimported from generation_tasks at module level."""
    from server.services.tasks import resume_executor

    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(
        resume_executor,
        "resolve_generation_context",
        AsyncMock(
            return_value=_fake_video_context(
                fake_generator,
                endpoint=endpoint,
                provider_id=provider_id,
                provider_model_id=provider_model_id,
                backend_model_id=backend_model_id,
            )
        ),
    )
    # Finalize helpers also obtain get_project_manager through generation_tasks/reference_video_tasks.
    use_project_manager(monkeypatch, fake_pm)

    # Real extract_video_thumbnail uses ffmpeg subprocesses; mock no-op removes finalize's external-tool dependency.
    async def _fake_thumb(*_args, **_kwargs):
        return False

    use_video_thumbnail_extractor(monkeypatch, _fake_thumb)


def _with_storyboard_identity(
    task: dict[str, Any],
    *,
    provider_id: str,
    endpoint_guard: str | None,
) -> dict[str, Any]:
    return {
        **task,
        "provider_id": provider_id,
        "execution_checkpoint_json": _storyboard_checkpoint_json(
            provider_id=provider_id,
            endpoint_guard=endpoint_guard,
        ),
    }


@pytest.mark.asyncio
async def test_execute_resume_video_calls_backend_resume_directly(monkeypatch, fake_pm, video_task):
    """resume_executor calls generator.resume_video_async (indirectly backend.resume_video), never generate."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    video_task["payload"].update(
        {
            "prompt": "stale enqueue prompt",
            "duration_seconds": 12,
            "video_provider_i2v": "grok/grok-imagine-video",
        }
    )
    fake_pm.project.update({"default_duration": 12, "aspect_ratio": "16:9"})
    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    result = await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert len(fake_gen.resume_calls) == 1
    call = fake_gen.resume_calls[0]
    assert call["job_id"] == "openai-job-1"
    assert call["resource_type"] == "videos"
    assert call["resource_id"] == "E1S01"
    assert call["task_id"] == "T-1"
    assert call["prompt"] == "p"
    assert call["duration_seconds"] == 8
    assert call["aspect_ratio"] == "9:16"
    assert call["resolution"] == "720p"
    assert call["generate_audio"] is True
    assert call["formal_output"] is True
    # Generator locates pending settlement calls by task_id; resume no longer forwards call coordinates.
    assert "api_call_id" not in call
    assert call["execution_provider_id"] == "openai"
    assert call["execution_provider_model_id"] == "sora-2"
    assert call["execution_backend_model_id"] == "sora-2"
    assert call["execution_visual_basis_digest"] == "b" * 64
    checkpoint = StoryboardSubmissionCheckpoint.from_json(video_task["execution_checkpoint_json"])
    assert checkpoint.artifact_visual_basis is not None
    assert checkpoint.artifact_currency is not None
    assert call["artifact_video_currency"] == checkpoint.artifact_currency.to_dict()
    assert call["execution_provider_media"][0]["source_locator"] == "storyboards/scene_E1S01.png"
    # Results carry file_path / resource_type for worker mark_succeeded.
    assert result["resource_type"] == "videos"
    assert result["file_path"] == "videos/scene_E1S01.mp4"


@pytest.mark.asyncio
async def test_execute_resume_carries_backend_warnings_into_the_result(monkeypatch, fake_pm, video_task):
    """Backend notices during resume reach task result."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    warning = {"key": "comfyui_multiple_outputs", "params": {"count": 2, "filename": "final.mp4"}}

    class _WarningGenerator(_FakeGenerator):
        async def resume_video_async(self, **kwargs: Any) -> tuple[Path, int, Any, str | None]:
            kwargs["warnings"].append(warning)
            return await super().resume_video_async(**kwargs)

    _patch_resume_executor_deps(monkeypatch, fake_pm, _WarningGenerator())

    result = await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert result["warnings"] == [warning]


@pytest.mark.asyncio
async def test_execute_resume_skips_storyboard_check(monkeypatch, fake_pm, video_task):
    """Resume does not read local storyboard files and succeeds even if the storyboard is absent."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    # Deliberately ensure the storyboard is absent.
    storyboard_dir = fake_pm.project_path / "storyboards"
    if storyboard_dir.exists():
        for f in storyboard_dir.glob("*.png"):
            f.unlink()

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    # Must not raise "storyboard not found".
    result = await execute_resume_video_task(video_task, job_id="openai-job-1")
    assert result["file_path"] == "videos/scene_E1S01.mp4"


@pytest.mark.asyncio
async def test_execute_resume_writes_scene_asset(monkeypatch, fake_pm, video_task):
    """Successful resume writes scene assets (video_clip + video_uri)."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    asset_types = {a["asset_type"] for a in fake_pm.scene_assets}
    assert "video_clip" in asset_types
    assert "video_uri" in asset_types


@pytest.mark.asyncio
async def test_execute_resume_expired_propagates(monkeypatch, fake_pm, video_task):
    """Propagate backend.resume_video ResumeExpiredError without swallowing it."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator(raises=ResumeExpiredError(job_id="openai-job-1", provider="openai"))
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    with pytest.raises(ResumeExpiredError):
        await execute_resume_video_task(video_task, job_id="openai-job-1")


@pytest.mark.asyncio
async def test_execute_resume_declares_video_lane_only(monkeypatch, fake_pm, video_task):
    """Declare video lane only, never image lane: do not construct image backends, so broken image configuration cannot affect resume (the former require_image_backend=False intent)."""
    from server.services.tasks import resume_executor
    from server.services.tasks.generation_context import VideoLaneRequest
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    async def _fake_thumb(*_args, **_kwargs):
        return False

    use_video_thumbnail_extractor(monkeypatch, _fake_thumb)

    captured: dict[str, Any] = {}

    async def _capturing_resolve_context(*args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return _fake_video_context(fake_gen)

    monkeypatch.setattr(resume_executor, "resolve_generation_context", _capturing_resolve_context)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    # Declare only video lane (VideoLaneRequest); absent image/audio lanes construct no corresponding backends.
    assert isinstance(captured["kwargs"].get("video"), VideoLaneRequest)
    assert captured["kwargs"].get("image") is None
    assert captured["kwargs"].get("audio") is None


@pytest.mark.asyncio
async def test_execute_resume_emits_project_change_batch(monkeypatch, fake_pm, video_task):
    """Successful resume synchronously emits emit_generation_success_batch for frontend SSE."""
    from server.services.tasks import resume_executor
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    calls: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(resume_executor, "emit_generation_success_batch", _capture)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert len(calls) == 1
    call = calls[0]
    assert call["task_type"] == "video"
    assert call["project_name"] == "demo"
    assert call["resource_id"] == "E1S01"


@pytest.mark.asyncio
async def test_execute_resume_failure_does_not_emit(monkeypatch, fake_pm, video_task):
    """Failed resume emits no batch because finalize did not succeed."""
    from server.services.tasks import resume_executor
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator(raises=RuntimeError("backend boom"))
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(resume_executor, "emit_generation_success_batch", lambda **kwargs: calls.append(kwargs))

    with pytest.raises(RuntimeError):
        await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert calls == []


@pytest.mark.asyncio
async def test_execute_resume_releases_selection_guard_when_generator_fails_after_prepare(
    monkeypatch,
    fake_pm,
    video_task,
):
    from server.services.tasks import resume_executor
    from server.services.tasks.resume_executor import execute_resume_video_task

    class _FailAfterPrepareGenerator(_FakeGenerator):
        async def resume_video_async(self, **kwargs: Any) -> tuple[Path, int, Any, str | None]:
            self.resume_calls.append(kwargs)
            prepare = kwargs["before_formal_commit"]
            await prepare(
                Path(tempfile.gettempdir()) / "video.mp4",
                int(kwargs["duration_seconds"]),
                {"execution_script_file": kwargs["execution_script_file"]},
            )
            raise RuntimeError("commit failed after selection preparation")

    class _GuardedCommitter:
        instance: _GuardedCommitter | None = None

        def __init__(self, **_kwargs: Any) -> None:
            self.guard_active = False
            type(self).instance = self

        async def prepare_selection(self, *_args: Any, **_kwargs: Any) -> None:
            self.guard_active = True

        async def release_admission_guard(self) -> None:
            self.guard_active = False

    fake_gen = _FailAfterPrepareGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)
    monkeypatch.setattr(resume_executor, "VideoArtifactCommitter", _GuardedCommitter)

    with pytest.raises(RuntimeError, match="commit failed after selection preparation"):
        await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert _GuardedCommitter.instance is not None
    assert not _GuardedCommitter.instance.guard_active


@pytest.mark.asyncio
async def test_execute_resume_accepts_float_string_duration(monkeypatch, fake_pm):
    """Resume ignores legacy payload duration and uses the strict checkpoint value."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    task = {
        "task_id": "T-float",
        "task_type": "video",
        "media_type": "video",
        "project_name": "demo",
        "resource_id": "E1S01",
        "provider_id": "openai",
        "provider_job_id": "openai-job-1",
        "script_file": "episode_1.json",
        "payload": {"script_file": "episode_1.json", "prompt": "p", "duration_seconds": "8.0"},
        "execution_checkpoint_json": _storyboard_checkpoint_json(task_id="T-float"),
    }
    # Must not raise ValueError.
    result = await execute_resume_video_task(task, job_id="openai-job-1")
    assert result["resource_type"] == "videos"
    assert fake_gen.resume_calls[0]["duration_seconds"] == 8


@pytest.mark.asyncio
async def test_execute_resume_rejects_image_task(monkeypatch, fake_pm):
    """Never dispatch non-video/reference_video tasks such as storyboards to resume; images have no resume path."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    image_task = {
        "task_id": "T-img",
        "task_type": "storyboard",
        "media_type": "image",
        "project_name": "demo",
        "resource_id": "E1S01",
        "provider_id": "gemini-aistudio",
        "provider_job_id": "x",
        "payload": {"script_file": "episode_1.json"},
    }
    with pytest.raises(NotImplementedError):
        await execute_resume_video_task(image_task, job_id="x")


#: Two narration-delivery facts recorded by schema v3 checkpoints, shaped by the pre-upgrade persisted contract.
_V3_USE_TTS_NARRATION = {
    "delivery": "use_tts",
    "tts_status": "current",
    "artifact_path": "audio/segment_E1U1.wav",
    "basis_digest": "sha256-v1:" + "b" * 64,
    "actual_duration_seconds": 10.5,
}
_V3_POST_PRODUCTION_NARRATION = {
    "delivery": "post_production",
    "tts_status": "not_applicable",
    "artifact_path": "",
    "basis_digest": None,
    "actual_duration_seconds": None,
}


def _as_schema_v3(checkpoint_json: str, narration: dict[str, Any]) -> str:
    """Rewrite current checkpoints into pre-upgrade persisted schema v3: extra narration-delivery facts, no provider-material references, and recomputed digest."""

    raw = json.loads(checkpoint_json)
    raw.pop("request_digest")
    raw.pop("provider_asset_references")
    raw["schema_version"] = 3
    raw["narration"] = narration
    raw["request_digest"] = canonical_json_digest(raw)
    return canonical_json(raw)


def _reference_checkpoint(
    project_path: Path,
    *,
    endpoint_guard: str | None = None,
    legacy_narration: dict[str, Any] | None = None,
) -> str:
    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint

    staging = project_path / ".arcreel" / "tasks" / "T-ref" / "provider_media"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "crash-leftover").write_bytes(b"staged")
    visual = ArtifactBasis.build(
        "artifact-visual/video-reference",
        kind_version=1,
        inputs={
            "unit_id": "E1U1",
            "visual_lines": ["Run."],
            "style": "cinematic",
            "canvas": {"aspect_ratio": "16:9"},
            "request_references": [],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = ArtifactBasis.build(
        "artifact-speech/video-duration",
        kind_version=1,
        inputs={"request_duration_seconds": 12},
    )
    checkpoint_json = ReferenceSubmissionCheckpoint.create(
        task_id="T-ref",
        project_name="demo",
        script_file="scripts/frozen.json",
        unit_id="E1U1",
        generation_type="r2v",
        provider_id="custom-7",
        provider_model_id="cinema-v1",
        backend_model_id="cinema-v1-resolved",
        endpoint_guard=endpoint_guard,
        prompt="frozen actual prompt",
        duration_seconds=12,
        aspect_ratio="16:9",
        resolution="1080p",
        generate_audio=False,
        service_tier="pro",
        seed=123,
        visual_basis_digest="sha256-v1:" + "a" * 64,
        artifact_currency=VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=12,
            visual_basis=visual,
            speech_basis=speech,
            duration_basis=duration,
            video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
            voice_style_speakers=(),
            duration_tiers=(12,),
            reference_image_limit=3,
            parent_version=0,
        ),
        media=(),
        reference_audio_targets=None,
    ).to_json()
    return checkpoint_json if legacy_narration is None else _as_schema_v3(checkpoint_json, legacy_narration)


@pytest.mark.asyncio
async def test_reference_resume_reads_only_strict_checkpoint_request_and_cleans_staging(monkeypatch, fake_pm):
    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import resume_executor
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    captured_context: dict[str, Any] = {}

    async def _resolve(project_name: str, payload: dict, **kwargs: Any):
        captured_context.update(project_name=project_name, payload=payload, kwargs=kwargs)
        return _fake_video_context(
            fake_gen,
            endpoint="dashscope-async-video",
            provider_id="custom-7",
            provider_model_id="cinema-v1",
            backend_model_id="cinema-v1-resolved",
        )

    monkeypatch.setattr(resume_executor, "resolve_generation_context", _resolve)
    finalize = AsyncMock(return_value={"resource_type": "reference_videos", "resource_id": "E1U1"})
    monkeypatch.setattr(resume_executor, "finalize_reference_video_unit", finalize)
    monkeypatch.setattr(resume_executor, "emit_generation_success_batch", lambda **_kwargs: None)
    task = {
        "task_id": "T-ref",
        "task_type": "reference_video",
        "media_type": "video",
        "project_name": "demo",
        "resource_id": "E1U1",
        "script_file": "scripts/frozen.json",
        "provider_id": "stale-provider",
        "provider_job_id": "job-1",
        "provider_endpoint": "stale-protocol-id",
        "submitted_base_url": "https://submitted.example/v1",
        "execution_checkpoint_json": _reference_checkpoint(
            fake_pm.project_path,
            endpoint_guard="dashscope-async-video",
        ),
        "payload": {
            "script_file": "scripts/current-wrong.json",
            "prompt": "current wrong prompt",
            "duration_seconds": 3,
            "video_provider_r2v": "wrong/model",
            "resolution": "360p",
            "generate_audio": True,
        },
    }

    result = await execute_resume_video_task(task, job_id="job-1")

    assert result["resource_type"] == "reference_videos"
    assert captured_context["payload"] == {"video_provider_r2v": "custom-7/cinema-v1"}
    assert captured_context["kwargs"]["video"].generation_type == "r2v"
    call = fake_gen.resume_calls[0]
    assert call["prompt"] == "frozen actual prompt"
    assert call["duration_seconds"] == 12
    assert call["aspect_ratio"] == "16:9"
    assert call["resolution"] == "1080p"
    assert call["generate_audio"] is False
    assert call["service_tier"] == "pro"
    assert call["seed"] == 123
    assert "api_call_id" not in call
    assert call["formal_output"] is True
    assert call["submitted_base_url"] == "https://submitted.example/v1"
    assert call["execution_prompt_sha256"]
    checkpoint = ReferenceSubmissionCheckpoint.from_json(task["execution_checkpoint_json"])
    assert call["execution_request_digest"] == checkpoint.request_digest
    assert call["execution_provider_media"] == []
    assert call["visual_basis_digest"] == checkpoint.visual_basis_digest
    assert checkpoint.artifact_visual_basis is not None
    assert checkpoint.artifact_currency is not None
    assert call["artifact_video_currency"] == checkpoint.artifact_currency.to_dict()
    finalize.assert_awaited_once()
    assert finalize.await_args.kwargs["script_file"] == "scripts/frozen.json"
    assert not (fake_pm.project_path / ".arcreel" / "tasks" / "T-ref" / "provider_media").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_narration",
    [None, _V3_USE_TTS_NARRATION, _V3_POST_PRODUCTION_NARRATION],
    ids=["schema-current", "schema-v3-use-tts", "schema-v3-post-production"],
)
async def test_reference_resume_without_tts_config_declares_no_audio_lane(
    monkeypatch, fake_pm, legacy_narration: dict[str, Any] | None
):
    """Resume interrupted video tasks normally without any project TTS configuration: declare only video lane and poll using checkpoint-frozen seconds.

    Pre-upgrade v3 checkpoints include narration-delivery facts such as use_tts; resume still declares no audio lane."""
    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import resume_executor
    from server.services.tasks.resume_executor import execute_resume_video_task

    assert not {"audio_backend", "narration_voice", "narration_speed"} & fake_pm.project.keys()
    fake_gen = _FakeGenerator()
    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    captured: dict[str, Any] = {}

    async def _resolve(project_name: str, payload: dict, **kwargs: Any):
        captured.update(kwargs)
        return _fake_video_context(
            fake_gen,
            provider_id="custom-7",
            provider_model_id="cinema-v1",
            backend_model_id="cinema-v1-resolved",
        )

    monkeypatch.setattr(resume_executor, "resolve_generation_context", _resolve)
    monkeypatch.setattr(
        resume_executor,
        "finalize_reference_video_unit",
        AsyncMock(return_value={"resource_type": "reference_videos", "resource_id": "E1U1"}),
    )
    monkeypatch.setattr(resume_executor, "emit_generation_success_batch", lambda **_kwargs: None)
    checkpoint_json = _reference_checkpoint(fake_pm.project_path, legacy_narration=legacy_narration)
    task = {
        "task_id": "T-ref",
        "task_type": "reference_video",
        "project_name": "demo",
        "resource_id": "E1U1",
        "script_file": "scripts/frozen.json",
        "execution_checkpoint_json": checkpoint_json,
        "payload": {},
    }

    result = await execute_resume_video_task(task, job_id="job-1")

    assert result == {"resource_type": "reference_videos", "resource_id": "E1U1"}
    assert captured.get("audio") is None
    assert captured["video"].generation_type == "r2v"
    call = fake_gen.resume_calls[0]
    assert call["duration_seconds"] == 12
    assert call["execution_request_digest"] == ReferenceSubmissionCheckpoint.from_json(checkpoint_json).request_digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("checkpoint_endpoint", "current_endpoint"),
    [(None, "openai-video"), ("openai-video", None), ("openai-video", "minimax-video")],
)
async def test_reference_resume_endpoint_guard_is_exact(
    monkeypatch,
    fake_pm,
    checkpoint_endpoint: str | None,
    current_endpoint: str | None,
):
    from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
    from server.services.tasks import resume_executor
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(
        resume_executor,
        "resolve_generation_context",
        AsyncMock(
            return_value=_fake_video_context(
                fake_gen,
                endpoint=current_endpoint,
                provider_id="custom-7",
                provider_model_id="cinema-v1",
                backend_model_id="cinema-v1-resolved",
            )
        ),
    )
    task = {
        "task_id": "T-ref",
        "task_type": "reference_video",
        "project_name": "demo",
        "resource_id": "E1U1",
        "script_file": "scripts/frozen.json",
        "execution_checkpoint_json": _reference_checkpoint(fake_pm.project_path, endpoint_guard=checkpoint_endpoint),
        "payload": {},
    }

    with pytest.raises(ResumeEndpointChangedError):
        await execute_resume_video_task(task, job_id="job-1")

    assert fake_gen.resume_calls == []
    assert not (fake_pm.project_path / ".arcreel" / "tasks" / "T-ref" / "provider_media").exists()


@pytest.mark.asyncio
async def test_reference_resume_rejects_resolved_model_drift_before_poll(monkeypatch, fake_pm):
    from lib.script.reference_video.execution_checkpoint import ReferenceExecutionIdentityError
    from server.services.tasks import resume_executor
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(
        resume_executor,
        "resolve_generation_context",
        AsyncMock(
            return_value=_fake_video_context(
                fake_gen,
                provider_id="custom-7",
                provider_model_id="cinema-v1",
                backend_model_id="fallback-model",
            )
        ),
    )
    task = {
        "task_id": "T-ref",
        "task_type": "reference_video",
        "project_name": "demo",
        "resource_id": "E1U1",
        "script_file": "scripts/frozen.json",
        "execution_checkpoint_json": _reference_checkpoint(fake_pm.project_path),
        "payload": {},
    }

    with pytest.raises(ReferenceExecutionIdentityError):
        await execute_resume_video_task(task, job_id="job-1")

    assert fake_gen.resume_calls == []
    assert not (fake_pm.project_path / ".arcreel" / "tasks" / "T-ref" / "provider_media").exists()


# ── Endpoint comparison gate ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_fails_when_endpoint_changed(monkeypatch, fake_pm, video_task):
    """If submission endpoint differs from the model row's current endpoint, fail explicitly without polling.

    Changing endpoint changes protocol: a new-protocol backend polling an old-protocol job could misread responses and mark a still-running, still-billable remote job failed (docs/adr/0054)."""
    from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="minimax-video", provider_id="custom-7")

    task = _with_storyboard_identity(video_task, provider_id="custom-7", endpoint_guard="openai-video")
    with pytest.raises(ResumeEndpointChangedError) as exc_info:
        await execute_resume_video_task(task, job_id="custom-job-1")

    assert exc_info.value.submitted_endpoint == "openai-video"
    assert exc_info.value.current_endpoint == "minimax-video"
    # Core regression assertion: never poll an old job with a backend using a new protocol.
    assert fake_gen.resume_calls == []


@pytest.mark.asyncio
async def test_resume_proceeds_when_endpoint_unchanged(monkeypatch, fake_pm, video_task):
    """Unchanged endpoint retains existing resume behavior."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="openai-video", provider_id="custom-7")

    task = _with_storyboard_identity(video_task, provider_id="custom-7", endpoint_guard="openai-video")
    await execute_resume_video_task(task, job_id="custom-job-1")

    assert len(fake_gen.resume_calls) == 1


@pytest.mark.asyncio
async def test_resume_rejects_builtin_checkpoint_when_current_backend_is_custom(monkeypatch, fake_pm, video_task):
    """A null checkpoint guard means builtin submit and cannot be replayed through a custom protocol."""
    from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="minimax-video")

    with pytest.raises(ResumeEndpointChangedError):
        await execute_resume_video_task(video_task, job_id="custom-job-1")

    assert fake_gen.resume_calls == []


@pytest.mark.asyncio
async def test_resume_proceeds_for_builtin_provider_without_endpoint(monkeypatch, fake_pm, video_task):
    """Built-in providers have no endpoint dimension (both empty), so the gate does not intervene."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert len(fake_gen.resume_calls) == 1


# ── Submission-domain replay ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_replays_submitted_base_url_for_builtin(monkeypatch, fake_pm, video_task):
    """Built-in provider restart resume forwards persisted submission domain into the backend; configuration changes still poll the original host."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    task = {
        **video_task,
        "provider_id": "dashscope",
        "provider_endpoint": None,
        "submitted_base_url": "https://maas-a.example.com/ws-1/api/v1",
    }
    await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] == "https://maas-a.example.com/ws-1/api/v1"


@pytest.mark.asyncio
async def test_resume_replays_submitted_base_url_with_uppercase_scheme(monkeypatch, fake_pm, video_task):
    """Domain-form checks ignore scheme casing: persist user base_url unchanged; uppercase schemes still count as domains."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    task = {**video_task, "provider_id": "dashscope", "submitted_base_url": "HTTPS://maas-a.example.com/ws-1/api/v1"}
    await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] == "HTTPS://maas-a.example.com/ws-1/api/v1"


@pytest.mark.asyncio
async def test_resume_replays_submitted_base_url_for_custom(monkeypatch, fake_pm, video_task):
    """Custom-provider restart resume stores protocol and domain separately; base_url edits in flight still poll the submission-time domain."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(
        monkeypatch,
        fake_pm,
        fake_gen,
        endpoint="dashscope-async-video",
        provider_id="custom-7",
    )

    task = {
        **_with_storyboard_identity(
            video_task,
            provider_id="custom-7",
            endpoint_guard="dashscope-async-video",
        ),
        "provider_endpoint": "dashscope-async-video",
        "submitted_base_url": "https://custom-a.example.com/api/v1",
    }
    await execute_resume_video_task(task, job_id="custom-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] == "https://custom-a.example.com/api/v1"


@pytest.mark.asyncio
async def test_resume_does_not_read_domain_from_endpoint_column(monkeypatch, fake_pm, video_task):
    """Protocol identifiers do not participate in domain replay; tasks lacking stored domains poll current configuration."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="openai-video", provider_id="custom-7")

    task = _with_storyboard_identity(video_task, provider_id="custom-7", endpoint_guard="openai-video")
    task["provider_endpoint"] = "openai-video"
    task["submitted_base_url"] = None
    await execute_resume_video_task(task, job_id="custom-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] is None


@pytest.mark.asyncio
async def test_resume_ignores_non_domain_value_in_base_url_column(monkeypatch, fake_pm, video_task):
    """If the dedicated column contains a nondomain value (such as an unexplained manual DB edit), do not replay; poll current configuration."""
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    task = {**video_task, "provider_id": "dashscope", "submitted_base_url": "dashscope-async-video"}
    await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] is None


@pytest.mark.asyncio
async def test_resume_fails_when_custom_endpoint_changed_even_with_base_url(monkeypatch, fake_pm, video_task):
    """Protocol mismatch still fails explicitly; domain replay does not permit cross-protocol resume."""
    from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="minimax-video", provider_id="custom-7")

    task = {
        **_with_storyboard_identity(
            video_task,
            provider_id="custom-7",
            endpoint_guard="dashscope-async-video",
        ),
        "provider_endpoint": "dashscope-async-video",
        "submitted_base_url": "https://custom-a.example.com/api/v1",
    }
    with pytest.raises(ResumeEndpointChangedError):
        await execute_resume_video_task(task, job_id="custom-job-1")

    assert fake_gen.resume_calls == []


@pytest.mark.asyncio
async def test_resume_does_not_replay_domain_across_provider_kind_switch(monkeypatch, fake_pm, video_task):
    """If a custom-provider task's model row changes to built-in while in flight, the comparison gate blocks before domain replay.

    Stored domains belong to submission credentials; pairing another provider class's credentials would replace an attributable 404 with authentication/connection errors. Built-in/custom switches compare unequal, so replay is unreachable."""
    from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None, provider_id="custom-7")

    task = {
        **_with_storyboard_identity(
            video_task,
            provider_id="custom-7",
            endpoint_guard="dashscope-async-video",
        ),
        "provider_endpoint": "dashscope-async-video",
        "submitted_base_url": "https://custom-a.example.com/api/v1",
    }
    with pytest.raises(ResumeEndpointChangedError):
        await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls == []


@pytest.mark.asyncio
async def test_resume_fails_when_builtin_task_switched_to_custom_provider(monkeypatch, fake_pm, video_task):
    """If a built-in-provider task (without protocol identifier) changes to custom in flight, the comparison gate blocks.

    Prefer explicit failure to polling old provider jobs with new-protocol backends."""
    from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
    from server.services.tasks.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="dashscope-async-video")

    task = {
        **video_task,
        "provider_id": "dashscope",
        "provider_endpoint": None,
        "submitted_base_url": "https://maas-a.example.com/ws-1/api/v1",
    }
    with pytest.raises(ResumeEndpointChangedError):
        await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls == []
