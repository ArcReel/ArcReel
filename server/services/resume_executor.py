"""Resume executor：worker `_process_resume_task` 直接调用的入口。

不走 `execute_video_task` / `execute_reference_video_task` 流水线——provider 端
job 已经在跑，本地 storyboard / 参考资产是否存在不该影响接续轮询。仅复用 service
层的 finalize helpers 写回 scene/unit 资产。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lib.narration_delivery import USE_TTS
from lib.project_change_hints import project_change_source
from lib.reference_video.execution_checkpoint import (
    ReferenceExecutionIdentityError,
    ReferenceSubmissionCheckpoint,
    checkpoint_version_metadata,
    cleanup_staged_provider_media,
    load_task_reference_checkpoint,
)
from lib.video_backends.base import ResumeEndpointChangedError
from server.services.generation_context import VideoLaneRequest, resolve_generation_context
from server.services.generation_tasks import (
    DEFAULT_USER_ID,
    _finalize_video_task,
    emit_generation_success_batch,
    get_aspect_ratio,
    get_project_manager,
)
from server.services.narration_delivery_tasks import require_generated_video_covers_current_tts
from server.services.reference_video_tasks import _finalize_reference_video_unit

logger = logging.getLogger(__name__)


def _ensure_endpoint_unchanged(task: dict[str, Any], current_endpoint: str | None, *, job_id: str) -> None:
    """提交本 job 的 endpoint 与模型行当下的 endpoint 不同即显式失败，不接续轮询。

    身份闸（``lib.config.resolver._ensure_video_identity_resolvable``）只校验媒体类型仍是
    video，同媒体类型内的 endpoint 切换（如 openai-video → minimax-video）会直接放行，于是
    新协议 backend 拿到旧协议下创建的 job id：轻则报错指向新协议、用户无法归因，重则轮询
    响应被误读、任务被标失败而远端 job 仍在跑仍在计费。

    两侧任一为空即跳过：内置供应商无 endpoint 维度（其持久化值是请求域名，走
    ``_submitted_base_url`` 那条消费分支），本列未持久化的存量任务同样无从比对，
    行为与现状一致。
    """
    submitted_endpoint = task.get("provider_endpoint")
    if not submitted_endpoint or not current_endpoint or submitted_endpoint == current_endpoint:
        return
    raise ResumeEndpointChangedError(
        job_id=job_id,
        provider=str(task.get("provider_id") or "unknown"),
        submitted_endpoint=str(submitted_endpoint),
        current_endpoint=current_endpoint,
    )


def _ensure_checkpoint_endpoint_unchanged(
    checkpoint: ReferenceSubmissionCheckpoint,
    current_endpoint: str | None,
    *,
    job_id: str,
) -> None:
    """Reference submissions use an exact endpoint guard, including builtin/custom transitions."""

    if checkpoint.endpoint_guard == current_endpoint:
        return
    raise ResumeEndpointChangedError(
        job_id=job_id,
        provider=checkpoint.provider_id,
        submitted_endpoint=checkpoint.endpoint_guard or "<builtin>",
        current_endpoint=current_endpoint or "<builtin>",
    )


def _validate_resolved_checkpoint_identity(checkpoint: ReferenceSubmissionCheckpoint, video: Any) -> None:
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
            "resolved provider/model/backend identity does not match the submitted reference checkpoint"
        )


def _is_request_domain(value: Any) -> bool:
    """落库值是否为请求域名形态——两列都可能躺着 endpoint 标识，只有 http(s) 前缀的才是域名。"""
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def _submitted_base_url(task: dict[str, Any], current_endpoint: str | None) -> str | None:
    """提交本 job 时的请求域名，供 backend 回放轮询；无从判定时 None。

    域名按供应商类型分落两列，读取时按当下的供应商类型选列——``current_endpoint`` 非空即当下
    是自定义供应商，取专列 ``submitted_base_url``（该类供应商的 ``provider_endpoint`` 位被协议
    标识占用，归比对闸消费）；为空即当下是内置供应商，无协议维度，域名就在 ``provider_endpoint``。
    空值口径与 ``_ensure_endpoint_unchanged`` 一致取 falsy，否则空串会让两条分支都不生效。

    按当下类型选列而非先读专列，是为了拦住跨类切换：任务由自定义供应商提交、模型行在途被改成
    内置供应商时，专列里躺着的是另一个供应商的域名，拿它配内置凭据轮询只会把 404（可归因为任务
    过期）换成更难归因的认证或连接错误；反向切换同理，域名形态确认拦住把 endpoint 标识当域名用。
    选中的列没有域名（未落此值的存量任务、非 dashscope 协议的自定义供应商、跨类切换）时回退
    None，backend 按当下配置的域名轮询。
    """
    if current_endpoint:
        submitted = task.get("submitted_base_url")
        return str(submitted) if _is_request_domain(submitted) else None
    endpoint_column = task.get("provider_endpoint")
    if _is_request_domain(endpoint_column):
        return str(endpoint_column)
    return None


async def execute_resume_video_task(task: dict[str, Any], *, job_id: str) -> dict[str, Any]:
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
    payload = task.get("payload") or {}
    user_id = task.get("user_id", DEFAULT_USER_ID)

    if task_type not in ("video", "reference_video"):
        raise NotImplementedError(f"resume not supported for task_type={task_type}")

    checkpoint: ReferenceSubmissionCheckpoint | None = None
    if task_type == "reference_video":
        checkpoint = load_task_reference_checkpoint(task)
        resolver_payload = {
            f"video_provider_{checkpoint.capability}": f"{checkpoint.provider_id}/{checkpoint.provider_model_id}"
        }
        video_request = VideoLaneRequest(capability=checkpoint.capability)
    else:
        resolver_payload = payload
        video_request = VideoLaneRequest()

    project, project_path = await asyncio.to_thread(
        lambda: (
            get_project_manager().load_project(project_name),
            get_project_manager().get_project_path(project_name),
        )
    )

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

        if checkpoint is not None:
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
            resource_type = "reference_videos"
            script_file = checkpoint.script_file
            api_call_id: int | None = checkpoint.api_call_id
            event_payload: dict[str, Any] = {"script_file": checkpoint.script_file}
        else:
            _ensure_endpoint_unchanged(task, ctx.video.endpoint, job_id=job_id)
            aspect_ratio = get_aspect_ratio(project, "videos")
            try:
                duration_seconds = int(float(payload.get("duration_seconds") or project.get("default_duration") or 8))
            except (ValueError, TypeError):
                duration_seconds = 8
            seed = payload.get("seed")
            raw_vp_settings = payload.get("video_provider_settings")
            vp_settings = raw_vp_settings if isinstance(raw_vp_settings, dict) else {}
            service_tier = vp_settings.get("service_tier", "default")
            raw_prompt = payload.get("prompt")
            prompt_text = raw_prompt if isinstance(raw_prompt, str) else ""
            raw_resolution = payload.get("resolution")
            resolution = raw_resolution if isinstance(raw_resolution, str) else None
            raw_generate_audio = payload.get("generate_audio")
            optional_kwargs = {}
            if isinstance(raw_generate_audio, bool):
                optional_kwargs["generate_audio"] = raw_generate_audio
            resource_type = "videos"
            script_file = payload["script_file"]
            api_call_id = payload.get("api_call_id")
            if api_call_id is not None and not isinstance(api_call_id, int):
                try:
                    api_call_id = int(api_call_id)
                except (ValueError, TypeError):
                    api_call_id = None
            event_payload = payload

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
                submitted_base_url=_submitted_base_url(task, ctx.video.endpoint),
                seed=seed,
                service_tier=service_tier,
                **optional_kwargs,
            )

            if checkpoint is not None:
                if checkpoint.narration.delivery == USE_TTS:
                    await require_generated_video_covers_current_tts(
                        project_name=project_name,
                        script_file=script_file,
                        request_duration_seconds=duration_seconds,
                        output_path=output_path,
                        versions=generator.versions,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        version=version,
                    )
                result = await _finalize_reference_video_unit(
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
                result = await _finalize_video_task(
                    project_name=project_name,
                    script_file=script_file,
                    project_path=project_path,
                    resource_id=resource_id,
                    version=version,
                    video_uri=video_uri,
                    generator=generator,
                )

            emit_generation_success_batch(
                task_type=task_type,
                project_name=project_name,
                resource_id=resource_id,
                payload=event_payload,
            )
            return result
    finally:
        if checkpoint is not None:
            await asyncio.to_thread(cleanup_staged_provider_media, project_path, checkpoint.task_id)
