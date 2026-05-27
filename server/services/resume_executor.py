"""Resume executor：worker `_process_resume_task` 直接调用的入口。

不走 `execute_video_task` / `execute_reference_video_task` 流水线——provider 端
job 已经在跑，本地 storyboard / 参考资产是否存在不该影响接续轮询。仅复用 service
层的 finalize helpers 写回 scene/unit 资产。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lib.media_generator import MediaGenerator
from lib.project_change_hints import project_change_source
from server.services.generation_tasks import (
    DEFAULT_USER_ID,
    _finalize_video_task,
    emit_generation_success_batch,
    get_aspect_ratio,
    get_media_generator,
    get_project_manager,
)
from server.services.reference_video_tasks import _finalize_reference_video_unit

logger = logging.getLogger(__name__)


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

    project, project_path = await asyncio.to_thread(
        lambda: (
            get_project_manager().load_project(project_name),
            get_project_manager().get_project_path(project_name),
        )
    )

    # require_image_backend=False：resume 路径用不到 image backend；若 image 配置在
    # submit→重启之间被破坏，整段 resume 不该被无关检查弄失败（provider job 仍在跑）。
    generator: MediaGenerator = await get_media_generator(
        project_name,
        payload=payload,
        user_id=user_id,
        require_image_backend=False,
    )

    aspect_ratio = get_aspect_ratio(project, "videos") if task_type == "video" else project.get("aspect_ratio", "9:16")
    duration_seconds = int(payload.get("duration_seconds") or project.get("default_duration") or 8)
    seed = payload.get("seed")
    service_tier = payload.get("video_provider_settings", {}).get("service_tier", "default")
    prompt_text = str(payload.get("prompt") or "")

    if task_type == "reference_video":
        resource_type = "reference_videos"
        script_file = payload["script_file"]
    else:
        resource_type = "videos"
        script_file = payload["script_file"]

    api_call_id = payload.get("api_call_id")
    if api_call_id is not None and not isinstance(api_call_id, int):
        try:
            api_call_id = int(api_call_id)
        except (ValueError, TypeError):
            api_call_id = None

    # 与 execute_generation_task 对齐：包 project_change_source('worker') 让下游
    # asset 写入挂在 worker 来源；finalize 完成后同步触发 emit_generation_success_batch
    # 推 SSE batch（带 asset_fingerprints），前端能即时刷缩略图缓存。
    with project_change_source("worker"):
        output_path, version, _, video_uri = await generator.resume_video_async(
            job_id=job_id,
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt_text,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            task_id=task_id,
            api_call_id=api_call_id,
            seed=seed,
            service_tier=service_tier,
        )

        if task_type == "reference_video":
            result = await _finalize_reference_video_unit(
                project_name=project_name,
                script_file=script_file,
                project_path=project_path,
                resource_id=resource_id,
                output_path=output_path,
                version=version,
                video_uri=video_uri,
                generator=generator,
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

        # emit_generation_success_batch 是同步函数，async caller 同步调用（不 await）
        emit_generation_success_batch(
            task_type=task_type,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
        )
        return result
