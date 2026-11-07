"""Resume executor called directly by `VideoResumeRunner.run`.

Bypass `execute_video_task` / `execute_reference_video_task`: the provider job is already running, so local storyboard/
reference-image availability must not affect resumed polling. Reuse only service-layer finalization helpers for scene/unit writeback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
from lib.backends.resume_guard import resume_guard_accepts
from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
from lib.generation.deleted_target import GenerationTargetDeletedError
from lib.project.project_change_hints import project_change_source
from lib.script.reference_video.execution_checkpoint import (
    ReferenceExecutionIdentityError,
    VideoSubmissionCheckpoint,
    checkpoint_version_metadata,
    cleanup_staged_provider_media,
    load_task_video_checkpoint,
)
from server.services.currency.video_artifact_currency import (
    VideoArtifactCommitter,
    complete_video_artifact_commit,
)
from server.services.tasks.generation_context import VideoLaneRequest, resolve_generation_context
from server.services.tasks.generation_tasks import (
    DEFAULT_USER_ID,
    emit_generation_success_batch,
    finalize_video_task,
    get_project_manager,
)
from server.services.tasks.reference_video_tasks import finalize_reference_video_unit

logger = logging.getLogger(__name__)


def _ensure_checkpoint_endpoint_unchanged(
    checkpoint: VideoSubmissionCheckpoint,
    current_endpoint: str | None,
    *,
    job_id: str,
) -> None:
    """Compare the protocol guard frozen at submission against current configuration: allow only the same endpoint or a protocol-equivalent successor of a retired endpoint (lib.backends.resume_guard).

    Reject switches between built-in/custom providers, endpoint changes and path overrides."""

    if resume_guard_accepts(checkpoint.endpoint_guard, current_endpoint):
        return
    raise ResumeEndpointChangedError(
        job_id=job_id,
        provider=checkpoint.provider_id,
        submitted_endpoint=checkpoint.endpoint_guard or "<builtin>",
        current_endpoint=current_endpoint or "<builtin>",
    )


def _validate_resolved_checkpoint_identity(checkpoint: VideoSubmissionCheckpoint, video: Any) -> None:
    actual = (
        video.provider_model.provider_id,
        video.provider_model.model_id,
        video.backend_model,
    )
    frozen = (
        checkpoint.provider_id,
        checkpoint.provider_model_id,
        checkpoint.backend_model_id,
    )
    if actual != frozen:
        raise ReferenceExecutionIdentityError(
            "resolved provider/model/backend identity does not match the submitted video checkpoint"
        )


def _submitted_base_url(task: dict[str, Any]) -> str | None:
    """Request domain used when submitting this provider job, replayed by backend polling; None if unrecorded.

    All provider types store domains in ``submitted_base_url``; ``provider_endpoint`` holds only protocol identity.
    Call after ``_ensure_checkpoint_endpoint_unchanged``: protocol identity and built-in/custom ownership already match
    submission verbatim, so the stored domain belongs to current credentials and is safe to replay. An empty column
    (legacy jobs without this value or providers with configuration-independent submission domains) returns None,
    letting the backend poll the currently configured domain.

    Checking http(s) shape is a fallback assertion on stored values: writers store only request domains. Other forms
    indicate unknown provenance (manual database edits or malformed pre-migration rows with both columns populated).
    Prefer the current configured domain rather than constructing polling URLs from them."""
    submitted = task.get("submitted_base_url")
    if isinstance(submitted, str) and submitted.lower().startswith(("http://", "https://")):
        return submitted
    return None


async def execute_resume_video_task(
    task: dict[str, Any],
    *,
    job_id: str,
) -> dict[str, Any]:
    """Restart-recovery entry point called directly by `VideoResumeRunner.run`.

    1. Resolve project and construct MediaGenerator, locking payload.video_provider to task["provider_id"].
    2. Call `generator.resume_video_async(job_id=..., ...)`, internally using backend.resume_video.
    3. Finalize: write scene/unit assets, extract thumbnails and return a result dict.

    Do not read local storyboard/reference images or call assert_duration_supported: such precondition failures
    would let missing local assets strand an already submitted provider job as a ghost task."""
    task_type = task["task_type"]
    project_name = task["project_name"]
    resource_id = str(task["resource_id"])
    task_id = task["task_id"]
    poll_timeout_seconds = int(task.get("video_poll_timeout_seconds", DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS))
    user_id = task.get("user_id", DEFAULT_USER_ID)

    if task_type not in ("video", "reference_video"):
        raise NotImplementedError(f"resume not supported for task_type={task_type}")

    checkpoint = load_task_video_checkpoint(task)
    resolver_payload = {
        f"video_provider_{checkpoint.generation_type}": f"{checkpoint.provider_id}/{checkpoint.provider_model_id}"
    }
    # The provider job was submitted and may be charged; resume only polls it. Retrieve results even if the model went offline after submission.
    video_request = VideoLaneRequest(generation_type=checkpoint.generation_type, submitted_job=True)

    project, project_path = await asyncio.to_thread(
        lambda: (
            get_project_manager().load_project(project_name),
            get_project_manager().get_project_path(project_name),
        )
    )

    artifact_committer: VideoArtifactCommitter | None = None
    try:
        # Only the project snapshot remains live here, and solely to resolve credentials/backend construction.
        # A submitted reference job's prompt, duration, script locator, endpoint and model all come from checkpoint.
        ctx = await resolve_generation_context(
            project_name,
            resolver_payload,
            project=project,
            user_id=user_id,
            video=video_request,
        )
        generator = ctx.generator

        _validate_resolved_checkpoint_identity(checkpoint, ctx.video)
        _ensure_checkpoint_endpoint_unchanged(checkpoint, ctx.video.endpoint, job_id=job_id)
        aspect_ratio = checkpoint.aspect_ratio
        duration_seconds = checkpoint.duration_seconds
        seed = checkpoint.seed
        service_tier = checkpoint.service_tier
        prompt_text = checkpoint.prompt
        resolution = checkpoint.resolution
        optional_kwargs: dict[str, Any] = {
            "generate_audio": checkpoint.generate_audio,
            "formal_output": True,
            "visual_basis_digest": checkpoint.visual_basis_digest,
            **checkpoint_version_metadata(checkpoint),
        }
        resource_type = "reference_videos" if task_type == "reference_video" else "videos"
        script_file = checkpoint.script_file
        event_payload: dict[str, Any] = {"script_file": checkpoint.script_file}
        artifact_committer = VideoArtifactCommitter(
            project_manager=get_project_manager(),
            project_name=project_name,
            project_path=project_path,
            versions=generator.versions,
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt_text,
        )

        #: Nonblocking backend hints during resume enter task result through the same channel as initial execution.
        warnings: list[dict[str, Any]] = []
        with project_change_source("worker"):
            try:
                resumed = await generator.resume_video_async(
                    job_id=job_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=prompt_text,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_seconds,
                    resolution=resolution,
                    task_id=task_id,
                    submitted_base_url=_submitted_base_url(task),
                    seed=seed,
                    service_tier=service_tier,
                    poll_timeout_seconds=poll_timeout_seconds,
                    before_formal_commit=artifact_committer.prepare_selection,
                    commit_formal_output=artifact_committer,
                    warnings=warnings,
                    **optional_kwargs,
                )
            except GenerationTargetDeletedError as deleted:
                # Results reached project trash, so the task still succeeds; no resources were written back, so emit no generation-success event.
                return deleted.result
            output_path, version, _, video_uri = resumed

            def _emit_success() -> None:
                emit_generation_success_batch(
                    task_type=task_type,
                    project_name=project_name,
                    resource_id=resource_id,
                    payload=event_payload,
                )

            async def _finalize() -> dict[str, Any]:
                if task_type == "reference_video":
                    selected_result = await finalize_reference_video_unit(
                        project_name=project_name,
                        script_file=script_file,
                        project_path=project_path,
                        resource_id=resource_id,
                        output_path=output_path,
                        version=version,
                        video_uri=video_uri,
                        versions=generator.versions,
                        warnings=warnings,
                    )
                else:
                    selected_result = await finalize_video_task(
                        project_name=project_name,
                        script_file=script_file,
                        project_path=project_path,
                        resource_id=resource_id,
                        version=version,
                        video_uri=video_uri,
                        generator=generator,
                        warnings=warnings,
                    )
                return selected_result

            return await complete_video_artifact_commit(
                committer=artifact_committer,
                versions=generator.versions,
                resource_type=resource_type,
                resource_id=resource_id,
                version=version,
                video_uri=video_uri,
                finalize=_finalize,
                warnings=warnings,
                on_completed=_emit_success,
            )
    finally:
        try:
            if artifact_committer is not None:
                await artifact_committer.release_admission_guard()
        finally:
            await asyncio.to_thread(cleanup_staged_provider_media, project_path, checkpoint.task_id)
