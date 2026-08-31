"""Resume executor：worker `_process_resume_task` 直接调用的入口。

不走 `execute_video_task` / `execute_reference_video_task` 流水线——provider 端
job 已经在跑，本地分镜图 / 参考图是否存在不该影响接续轮询。仅复用 service
层的 finalize helpers 写回 scene/unit 资产。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
from lib.project_change_hints import project_change_source
from lib.reference_video.execution_checkpoint import (
    ReferenceExecutionIdentityError,
    VideoSubmissionCheckpoint,
    checkpoint_version_metadata,
    cleanup_staged_provider_media,
    load_task_video_checkpoint,
)
from lib.video_backends.base import ResumeEndpointChangedError
from server.services.generation_context import AudioLaneRequest, VideoLaneRequest, resolve_generation_context
from server.services.generation_tasks import (
    DEFAULT_USER_ID,
    _finalize_video_task,
    emit_generation_success_batch,
    get_project_manager,
)
from server.services.reference_video_tasks import finalize_reference_video_unit
from server.services.video_artifact_currency import (
    VideoArtifactCommitter,
    complete_video_artifact_commit,
)

logger = logging.getLogger(__name__)


def _ensure_checkpoint_endpoint_unchanged(
    checkpoint: VideoSubmissionCheckpoint,
    current_endpoint: str | None,
    *,
    job_id: str,
) -> None:
    """Submitted video jobs use an exact endpoint guard, including builtin/custom transitions."""

    if checkpoint.endpoint_guard == current_endpoint:
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
    """提交本供应商任务时的请求域名，供 backend 回放轮询；未记录时 None。

    域名不分供应商类型，一律落 ``submitted_base_url``（``provider_endpoint`` 只承载协议标识）。
    调用点在 ``_ensure_checkpoint_endpoint_unchanged`` 之后：协议标识与内置/自定义的归属已经与
    提交时逐字相等，落库的域名必定属于当下这套凭据，可直接回放。列为空（未落此值的存量任务、
    提交域名不随配置变化的供应商）时回退 None，backend 按当下配置的域名轮询。

    http(s) 形态判别是对落库值的兜底断言：写入侧只往该列放请求域名，读到别的形态说明这行来路
    不明（人工改库、迁移前两列都有值的畸形行），宁可回退到当下配置的域名，也不拿它拼轮询 URL。
    """
    submitted = task.get("submitted_base_url")
    if isinstance(submitted, str) and submitted.lower().startswith(("http://", "https://")):
        return submitted
    return None


async def execute_resume_video_task(
    task: dict[str, Any],
    *,
    job_id: str,
) -> dict[str, Any]:
    """重启自愈入口：worker `_process_resume_task` 直接调。

    1. 解析项目 + 构造 MediaGenerator（受 task["provider_id"] 锁定 payload.video_provider）
    2. 调 `generator.resume_video_async(job_id=..., ...)`——内部走 backend.resume_video
    3. finalize：写 scene asset / unit assets、抽缩略图、返回 result dict

    不读 storyboard / reference 本地图片，不调 assert_duration_supported——这些前置
    校验若失败会让本地资产缺失"卡死"已经提交给 provider 的 job（变幽灵任务）。
    """
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
        f"video_provider_{checkpoint.capability}": f"{checkpoint.provider_id}/{checkpoint.provider_model_id}"
    }
    video_request = VideoLaneRequest(capability=checkpoint.capability)

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
            audio=(AudioLaneRequest() if checkpoint.narration.delivery == "use_tts" else None),
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
        api_call_id: int | None = checkpoint.api_call_id
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

        with project_change_source("worker"):
            output_path, version, _, video_uri = await generator.resume_video_async(
                job_id=job_id,
                resource_type=resource_type,
                resource_id=resource_id,
                prompt=prompt_text,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
                resolution=resolution,
                task_id=task_id,
                api_call_id=api_call_id,
                submitted_base_url=_submitted_base_url(task),
                seed=seed,
                service_tier=service_tier,
                poll_timeout_seconds=poll_timeout_seconds,
                before_formal_commit=artifact_committer.prepare_selection,
                commit_formal_output=artifact_committer,
                **optional_kwargs,
            )

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
                    )
                else:
                    selected_result = await _finalize_video_task(
                        project_name=project_name,
                        script_file=script_file,
                        project_path=project_path,
                        resource_id=resource_id,
                        version=version,
                        video_uri=video_uri,
                        generator=generator,
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
                on_completed=_emit_success,
            )
    finally:
        try:
            if artifact_committer is not None:
                await artifact_committer.release_admission_guard()
        finally:
            await asyncio.to_thread(cleanup_staged_provider_media, project_path, checkpoint.task_id)
