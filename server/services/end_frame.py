"""镜头尾帧快照的设置与清除服务层。

尾帧是镜头的用户意图持久属性（``end_frame_image``），不是运行时产出，故不进
``generated_assets``。设置的两条通道（上传任意图片 / 指定项目内已有图片的相对路径）
在这里汇成同一落点：归一为 PNG 后快照复制到 ``end_frames/scene_{id}.png``，剧本条目
只存该固定相对路径。源图与快照就此彻底解耦——源图重生成、版本回滚、删除都动不到
已定尾帧，结构上不存在悬空引用。

设置写快照文件 + 写字段、清除删快照文件 + 置空字段，各自整段落在同一把剧本锁
（`ProjectManager.locked_script`）临界区内完成，与对方互斥——不会出现一方写完文件、
对方抢在字段写回前把文件删掉的交错，结构上杜绝悬空引用。临界区内失败（如目标镜头
mid-flight 被删除）会跳过整段写回，不留半截状态；孤儿快照文件（如进程在文件写完、
锁释放前被杀）仍可能残留，无害，与 storyboards 现状一致，不做清理机制。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from lib.image_utils import normalize_storyboard_upload
from lib.path_safety import PathTraversalError, safe_join
from lib.project_change_hints import project_change_source
from lib.project_manager import get_project_manager
from lib.resource_paths import resource_relative_path
from lib.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.upload_finalize import write_bytes_atomic

END_FRAME_RESOURCE_TYPE = "end_frames"


class EndFrameError(Exception):
    """尾帧操作的领域错误。路由层按 (status_code, key, params) 翻译为 HTTP 响应。"""

    def __init__(self, key: str, *, status_code: int = 400, **params: object):
        super().__init__(key)
        self.key = key
        self.status_code = status_code
        self.params = params


def _locate_shot(project_name: str, script_file: str, shot_id: str) -> Path:
    """确认镜头在剧本中存在，返回项目绝对路径；不存在时抛领域错误。

    参考生视频剧本的 ``get_storyboard_items`` 返回空列表（该路径无首帧概念，
    Spec 明确排除尾帧支持），故一并落到「镜头不存在」。
    """
    manager = get_project_manager()
    try:
        project_path = manager.get_project_path(project_name)
    except FileNotFoundError as exc:
        raise EndFrameError("project_not_found", status_code=404, name=project_name) from exc

    script = manager.load_script(project_name, script_file)
    items, id_field, _, _, _ = get_storyboard_items(script)
    if find_storyboard_item(items, id_field, shot_id) is None:
        raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
    return project_path


def _snapshot_target(project_path: Path, shot_id: str) -> tuple[Path, str]:
    """返回尾帧快照的 (绝对路径, 项目内相对路径)；shot_id 拼出的路径越界时抛领域错误。"""
    relative = resource_relative_path(END_FRAME_RESOURCE_TYPE, shot_id)
    try:
        target = safe_join(project_path, relative)
    except PathTraversalError as exc:
        raise EndFrameError("invalid_resource_id", resource_id=shot_id) from exc
    return target, relative


def _write_snapshot_and_field(
    project_name: str,
    script_file: str,
    shot_id: str,
    target: Path,
    png_bytes: bytes,
    relative: str,
) -> None:
    """在剧本锁临界区内完成「写快照文件 + 写字段」，与清除操作互斥。"""
    manager = get_project_manager()
    with project_change_source("webui"):
        with manager.locked_script(project_name, script_file) as script:
            items, id_field, _, _, _ = get_storyboard_items(script)
            matched = find_storyboard_item(items, id_field, shot_id)
            if matched is None:
                raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
            write_bytes_atomic(png_bytes, target)
            matched[0]["end_frame_image"] = relative


def _clear_snapshot_and_field(project_name: str, script_file: str, shot_id: str, target: Path) -> None:
    """在剧本锁临界区内完成「置空字段 + 删快照文件」，与设置操作互斥。"""
    manager = get_project_manager()
    with project_change_source("webui"):
        with manager.locked_script(project_name, script_file) as script:
            items, id_field, _, _, _ = get_storyboard_items(script)
            matched = find_storyboard_item(items, id_field, shot_id)
            if matched is None:
                raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
            matched[0]["end_frame_image"] = None
            target.unlink(missing_ok=True)


def read_project_image(project_path: Path, source_path: str) -> bytes:
    """读取项目内已有图片的字节；路径越界或文件缺失时抛领域错误。

    不限定来源子目录——分镜图 / 角色 / 场景 / 宫格切图都可直接选用，越界防护
    由 ``safe_join`` 统一负责（与 data_validator 的路径字段校验同口径）。
    """
    normalized = source_path.strip().replace("\\", "/")
    if not normalized:
        raise EndFrameError("invalid_end_frame_source", path=source_path)
    try:
        resolved = safe_join(project_path, normalized)
    except PathTraversalError as exc:
        raise EndFrameError("invalid_end_frame_source", path=source_path) from exc
    if not resolved.is_file():
        raise EndFrameError("end_frame_source_not_found", status_code=404, path=normalized)
    return resolved.read_bytes()


async def _apply_snapshot(
    *,
    project_path: Path,
    project_name: str,
    script_file: str,
    shot_id: str,
    content: bytes,
) -> str:
    """两条设置通道的共同落点：归一为 PNG → 写固定路径 → 写回字段，返回相对路径。

    换图即原地覆盖同一路径：字段值不变，前端靠资产指纹 cache-bust。
    """
    target, relative = _snapshot_target(project_path, shot_id)

    try:
        png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content)
    except ValueError as exc:
        raise EndFrameError("invalid_image_file") from exc

    await asyncio.to_thread(_write_snapshot_and_field, project_name, script_file, shot_id, target, png_bytes, relative)
    return relative


async def set_end_frame_from_bytes(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    content: bytes,
) -> str:
    """上传通道：把上传的图片字节落成该镜头的尾帧快照。"""
    project_path = await asyncio.to_thread(_locate_shot, project_name, script_file, shot_id)
    return await _apply_snapshot(
        project_path=project_path,
        project_name=project_name,
        script_file=script_file,
        shot_id=shot_id,
        content=content,
    )


async def set_end_frame_from_project_image(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    source_path: str,
) -> str:
    """项目内选图通道：读源图字节后走与上传完全相同的归一 + 快照落点。"""
    project_path = await asyncio.to_thread(_locate_shot, project_name, script_file, shot_id)
    content = await asyncio.to_thread(read_project_image, project_path, source_path)
    return await _apply_snapshot(
        project_path=project_path,
        project_name=project_name,
        script_file=script_file,
        shot_id=shot_id,
        content=content,
    )


async def clear_end_frame(*, project_name: str, script_file: str, shot_id: str) -> None:
    """清除镜头尾帧：在同一剧本锁临界区内把字段置空并删快照文件，与设置操作互斥。"""
    project_path = await asyncio.to_thread(_locate_shot, project_name, script_file, shot_id)
    target, _ = _snapshot_target(project_path, shot_id)
    await asyncio.to_thread(_clear_snapshot_and_field, project_name, script_file, shot_id, target)
