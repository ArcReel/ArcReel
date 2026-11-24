"""Typed media (audio / video) version restore as one service shared by the Web route and Agent tools.

The service owns everything a restore needs around the artifact transition itself: the generation
admission guard, the audio-in-use conflict check, media-file resolution, and post-restore
cleanup of video thumbnails.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from lib.artifacts.version_manager import VersionManager
from lib.generation.generation_admission import generation_admission_lock
from lib.infra.api_errors import BadRequestError, ConflictError
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.project_change_hints import build_change_label, emit_project_change_batch
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import resource_relative_path
from lib.script.script_skeleton import SKELETON_ENTITY_TYPES, SKELETON_ITEM_LABEL_KEYS, resolve_script_kind
from server.services.currency.artifact_version_restore import (
    UploadedVideoRestoreTarget,
    get_typed_media_restore_target,
    restore_typed_media_version,
)
from server.services.tasks.narration_delivery_tasks import active_tts_resource_ids

logger = logging.getLogger(__name__)


def resolve_media_file(resource_type: str, resource_id: str, project_path: Path) -> tuple[Path, str]:
    """返回 (current_file_absolute, relative_file_path)；资源 ID 越界时抛出领域异常。"""
    relative = resource_relative_path(resource_type, resource_id)
    try:
        current_file = safe_join(project_path, relative)
    except PathTraversalError as exc:
        raise BadRequestError("invalid_resource_id", resource_id=resource_id) from exc
    return current_file, relative


def _clear_video_thumbnail(resource_type: str, resource_id: str, project_path: Path) -> tuple[str, int] | None:
    """视频还原后内容已失效，同步删除缩略图；返回 (缩略图相对路径, fingerprint=0) 供前端置失效。"""
    if resource_type == "videos":
        thumbnail_key = f"thumbnails/scene_{resource_id}.jpg"
    elif resource_type == "reference_videos":
        thumbnail_key = f"reference_videos/thumbnails/{resource_id}.jpg"
    else:
        return None
    # hard-delete: derived -- 版本还原时清掉视频缩略图，可从视频重新生成
    (project_path / thumbnail_key).unlink(missing_ok=True)
    return thumbnail_key, 0


async def restore_typed_media_resource(
    *,
    project_manager: ProjectManager,
    versions: VersionManager,
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
) -> dict[str, Any]:
    """把一个 typed 媒体资源（audio / videos / reference_videos）切回指定历史版本。

    版本不存在抛 ``NotFoundError``，缺取证描述的生成版本或归属不唯一的上传版本抛
    ``BadRequestError``。音频正在被任务使用时抛 ``ConflictError``。成功返回还原信息、
    ``file_path`` 与 ``asset_fingerprints``。
    """
    try:
        target = await asyncio.to_thread(
            get_typed_media_restore_target,
            versions,
            project_manager=project_manager,
            project_name=project_name,
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
        )

        def _sync() -> dict[str, Any]:
            project_path = project_manager.get_project_path(project_name)
            current_file, file_path = resolve_media_file(resource_type, resource_id, project_path)
            video_kind = None
            if resource_type == "reference_videos":
                video_kind = "video_units"
            elif resource_type == "videos":
                try:
                    script = project_manager.load_script_readonly(project_name, target.script_file)
                except FileNotFoundError as exc:
                    if isinstance(target, UploadedVideoRestoreTarget):
                        raise ValueError("upload owner script disappeared during video restore") from exc
                    raise
                video_kind = resolve_script_kind(script)
            result = restore_typed_media_version(
                project_manager=project_manager,
                project_name=project_name,
                project_path=project_path,
                versions=versions,
                resource_type=resource_type,
                resource_id=resource_id,
                version=version,
                current_file=current_file,
                artifact_path=file_path,
                target=target,
            )

            asset_fingerprints: dict[str, int] = {}
            if current_file.exists():
                asset_fingerprints[file_path] = current_file.stat().st_mtime_ns
            if (cleared := _clear_video_thumbnail(resource_type, resource_id, project_path)) is not None:
                asset_fingerprints[cleared[0]] = cleared[1]

            if video_kind is not None:
                emit_project_change_batch(
                    project_name,
                    [
                        {
                            "entity_type": SKELETON_ENTITY_TYPES[video_kind],
                            "action": "video_ready",
                            "entity_id": resource_id,
                            **build_change_label(SKELETON_ITEM_LABEL_KEYS[video_kind], id=resource_id),
                            "script_file": target.script_file,
                            "episode": target.episode,
                            "focus": None,
                            "important": True,
                            "asset_fingerprints": asset_fingerprints,
                        }
                    ],
                )

            return {
                "success": True,
                **result,
                "file_path": file_path,
                "asset_fingerprints": asset_fingerprints,
            }

        async with generation_admission_lock(
            project_name=project_name,
            script_file=target.script_file,
            resource_id=resource_id,
        ):
            if resource_type == "audio":
                active_tts = await active_tts_resource_ids(
                    project_name=project_name,
                    resource_ids=(resource_id,),
                    script_file=target.script_file,
                )
                if resource_id in active_tts:
                    raise ConflictError("audio_restore_conflicts_with_active_task", resource_id=resource_id)
            return await run_noninterruptible_sync(_sync)

    except ValueError as e:
        logger.warning("版本还原请求非法: %s", e)
        raise BadRequestError("request_invalid") from e


__all__ = [
    "resolve_media_file",
    "restore_typed_media_resource",
]
