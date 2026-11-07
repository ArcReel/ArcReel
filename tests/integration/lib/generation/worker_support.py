"""Shared substitutes/seeding tools for generation workers, restart recovery and resume collaborators."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactBasis, compose_video_artifact_basis
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.generation.generation_worker import CapacityTable


class _ExecutorStubs:
    """Constructor-injected worker executor substitutes.

    Worker gets forwarding ``execute`` / ``execute_resume`` entries; cases set behavior through ``monkeypatch.setattr(stub_executors, "generation" | "resume", fake)``. Defaults fail the test."""

    async def generation(self, task: dict[str, Any], *, claimed_provider_id: str) -> dict[str, Any]:
        raise AssertionError(f"unexpected generation execution: {task.get('task_id')}")

    async def resume(self, task: dict[str, Any], *, job_id: str) -> dict[str, Any]:
        raise AssertionError(f"unexpected resume execution: {task.get('task_id')}")

    async def execute(self, task: dict[str, Any], *, claimed_provider_id: str) -> dict[str, Any]:
        return await self.generation(task, claimed_provider_id=claimed_provider_id)

    async def execute_resume(self, task: dict[str, Any], *, job_id: str) -> dict[str, Any]:
        return await self.resume(task, job_id=job_id)


stub_executors = _ExecutorStubs()


def use_worker_project(monkeypatch, project: dict | None) -> None:
    """Worker/configuration reads through ``get_project_manager().load_project`` all return *project*; ``None`` means an empty project."""
    pm = type("PM", (), {"load_project": lambda self, name: project or {}})()
    for target in ("lib.generation.generation_worker.get_project_manager", "lib.config.resolver.get_project_manager"):
        monkeypatch.setattr(target, lambda: pm)


def capacity_table(limits: dict[str, dict[str, int]] | None = None, *, image: int = 5, video: int = 3) -> CapacityTable:
    """Build capacity tables with explicit per-provider limits and lazy defaults, image five/video three by default."""
    return CapacityTable(_limits=limits or {}, _defaults={"image": image, "video": video})


def reference_checkpoint_json(task_id: str, *, provider_id: str = "ark") -> str:
    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint

    visual = ArtifactBasis.build(
        "artifact-visual/video-reference",
        kind_version=1,
        inputs={
            "unit_id": "E1U1",
            "visual_lines": ["Run."],
            "style": "cinematic",
            "canvas": {"aspect_ratio": "9:16"},
            "request_references": [],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = ArtifactBasis.build(
        "artifact-speech/video-duration",
        kind_version=1,
        inputs={"request_duration_seconds": 8},
    )
    return ReferenceSubmissionCheckpoint.create(
        task_id=task_id,
        project_name="demo",
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
        generation_type="r2v",
        provider_id=provider_id,
        provider_model_id="model-v1",
        backend_model_id="model-v1",
        endpoint_guard=None,
        prompt="frozen",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="1080p",
        generate_audio=True,
        service_tier="default",
        seed=None,
        visual_basis_digest="a" * 64,
        artifact_currency=VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=8,
            visual_basis=visual,
            speech_basis=speech,
            duration_basis=duration,
            video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
            voice_style_speakers=(),
            duration_tiers=(8,),
            reference_image_limit=1,
            parent_version=0,
        ),
        media=(),
        reference_audio_targets=None,
    ).to_json()


def _storyboard_checkpoint_json(task_id: str, *, provider_id: str = "ark") -> str:
    from lib.script.reference_video.execution_checkpoint import (
        StagedProviderMedia,
        StoryboardSubmissionCheckpoint,
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
        script_file="scripts/episode_1.json",
        unit_id="E1S01",
        generation_type="i2v",
        provider_id=provider_id,
        provider_model_id="model-v1",
        backend_model_id="model-v1",
        endpoint_guard=None,
        prompt="frozen",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="1080p",
        generate_audio=True,
        service_tier="default",
        seed=None,
        visual_basis_digest="a" * 64,
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
        media=(
            StagedProviderMedia(
                index=0,
                role="start_image",
                logical_type="storyboard",
                logical_name="E1S01",
                kind="storyboard",
                source_locator="storyboards/E1S01.png",
                staged_locator=f".arcreel/tasks/{task_id}/provider_media/000-start_image.png",
                sha256="b" * 64,
                size_bytes=5,
            ),
        ),
        reference_audio_targets=None,
    ).to_json()


def storyboard_resume_task(task_id: str, *, provider_id: str = "ark", job_id: str = "job-1") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "task_type": "video",
        "media_type": "video",
        "provider_id": provider_id,
        "provider_job_id": job_id,
        "execution_checkpoint_json": _storyboard_checkpoint_json(task_id, provider_id=provider_id),
        "payload": {},
        "project_name": "demo",
        "resource_id": "E1S01",
        "script_file": "scripts/episode_1.json",
    }


class FakeWorkerQueue:
    def __init__(self, *, failed_rows: int = 1):
        self.released = False
        self.succeeded = []
        self.failed = []
        self.interrupted: list[str] = []
        self.claim_after_interruption = asyncio.Event()
        self._lease_calls = 0
        self._failed_rows = failed_rows
        self._orphans: list[dict] = []
        self.persisted_providers: list[tuple[str, str]] = []

    @property
    def session_factory(self):
        """Substitutes own no DB: worker queue persistence uses the current global factory, redirected to memory by ``worker_db``."""
        import lib.db

        return lib.db.safe_session_factory

    async def persist_execution_provider_id(self, task_id, provider_id):
        self.persisted_providers.append((task_id, provider_id))

    async def acquire_or_renew_worker_lease(self, name, owner_id, ttl_seconds):
        self._lease_calls += 1
        return True

    async def release_worker_lease(self, name, owner_id):
        self.released = True

    async def requeue_running_tasks(self):
        return 0

    async def list_orphan_tasks_on_start(self):
        return self._orphans

    async def claim_next_task(self, media_type, **_kwargs) -> dict[str, Any] | None:
        if self.interrupted:
            self.claim_after_interruption.set()
        return None

    async def mark_task_succeeded(self, task_id, result):
        self.succeeded.append((task_id, result))
        return 1

    async def mark_task_failed(self, task_id, error):
        self.failed.append((task_id, error))
        return self._failed_rows

    async def mark_task_interrupted(self, task_id):
        self.interrupted.append(task_id)
        return 1


async def seed_running_task(factory, task_id: str, **overrides: Any) -> None:
    """Seed a running task for real guarded UPDATE requeue paths."""
    from lib.db.models.task import Task

    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "project_name": "demo",
        "task_type": "video",
        "media_type": "video",
        "resource_id": "E1S01",
        "status": "running",
        "queued_at": now,
        "started_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    async with factory() as session:
        session.add(Task(task_id=task_id, **fields))
        await session.commit()


async def task_status(factory, task_id: str) -> str | None:
    from lib.db.models.task import Task

    async with factory() as session:
        row = await session.get(Task, task_id)
        return None if row is None else row.status


def db_queue(factory):
    """Real GenerationQueue persisted to test DB."""
    from lib.generation.generation_queue import GenerationQueue

    return GenerationQueue(session_factory=factory)


def stage_task_dir(project_path: Path, task_id: str) -> Path:
    """Create one task's provider-media staging directory and return its staging root."""
    staged = project_path / ".arcreel" / "tasks" / task_id / "provider_media"
    staged.mkdir(parents=True)
    (staged / "000-start_image.png").write_bytes(b"x")
    return staged.parent
