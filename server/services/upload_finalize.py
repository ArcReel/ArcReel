"""用户自主上传分镜图/视频的 finalize 服务层。

复用生成链路的元数据回写、缩略图与版本记录原语，让上传产出的资产
在状态推导、SSE 刷新、版本回滚上与 AI 生成的资产行为一致。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Literal

from lib.artifact_activation import (
    forget_current_resource_artifact,
)
from lib.async_thread import run_noninterruptible_sync
from lib.formal_write import formal_write_transaction
from lib.thumbnail import extract_video_thumbnail
from lib.version_manager import VersionManager
from server.services.generation_tasks import _storyboard_formal_image_callback, get_project_manager

# 版本记录里标记「用户手动上传」的 source 值；前端按此显示翻译文案
UPLOAD_VERSION_SOURCE = "manual_upload"

# 上传策略（shot_uploads 与 reference_videos 两个路由共用，避免口径漂移）。
# 视频宽松校验：只看扩展名与大小上限，宽高比/时长不阻塞——用户自主上传即自己负责。
UPLOAD_IMAGE_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp")
# 不收 webm：字节原样存为 canonical .mp4，VP8/VP9 在 Safari/剪映一侧解码不可用，
# 错误扩展名还会误导排查；mp4/mov/m4v 同属 ISO BMFF 容器家族
UPLOAD_VIDEO_EXTENSIONS: tuple[str, ...] = (".mp4", ".mov", ".m4v")
UPLOAD_IMAGE_MAX_BYTES = 30 * 1024 * 1024
UPLOAD_VIDEO_MAX_BYTES = 200 * 1024 * 1024

_COPY_CHUNK_SIZE = 1024 * 1024


class UploadValidationError(Exception):
    """上传校验失败。路由层按 (status_code, key, params) 翻译为 HTTP 响应。"""

    def __init__(self, key: str, *, status_code: int = 400, **params: object):
        super().__init__(key)
        self.key = key
        self.status_code = status_code
        self.params = params


class UploadTooLargeError(UploadValidationError):
    """上传字节数超过上限。"""

    def __init__(self, max_bytes: int):
        super().__init__("upload_too_large", status_code=413, max_mb=max_bytes // (1024 * 1024))
        self.max_bytes = max_bytes


def validate_upload(filename: str | None, declared_size: int | None, *, kind: Literal["image", "video"]) -> int:
    """按上传策略校验文件名扩展名与申报大小，返回该类型的字节上限。

    declared_size 只做 Content-Length 预拒；实际写入字节数由落盘函数兜底。
    """
    if kind == "image":
        extensions, max_bytes = UPLOAD_IMAGE_EXTENSIONS, UPLOAD_IMAGE_MAX_BYTES
        type_error_key = "unsupported_image_type"
    else:
        extensions, max_bytes = UPLOAD_VIDEO_EXTENSIONS, UPLOAD_VIDEO_MAX_BYTES
        type_error_key = "unsupported_video_type"

    if not filename:
        raise UploadValidationError("missing_filename")
    ext = Path(filename).suffix.lower()
    if ext not in extensions:
        raise UploadValidationError(type_error_key, ext=ext, allowed=", ".join(extensions))
    if declared_size is not None and declared_size > max_bytes:
        raise UploadTooLargeError(max_bytes)
    return max_bytes


def _upload_tmp_path(target: Path) -> Path:
    """同目录 dot-tmp 路径，带每次调用唯一的后缀，避免并发上传交错写同一 tmp 文件。"""
    return target.with_name(f".{target.stem}.{uuid.uuid4().hex[:8]}.tmp{target.suffix}")


def _copy_limited(src: BinaryIO, dst_path: Path, max_bytes: int) -> None:
    total = 0
    with open(dst_path, "wb") as out:
        while True:
            chunk = src.read(_COPY_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UploadTooLargeError(max_bytes)
            out.write(chunk)


async def save_uploaded_video_stream(src: BinaryIO, target: Path, *, max_bytes: int) -> None:
    """把上传流分块写入 target（不整体读入内存）。

    先写同目录 dot-tmp 再 ``Path.replace``：跨卷 rename 在 Windows 会失败，
    且避免半截文件被 canonical 路径的读取方看到。超限抛 UploadTooLargeError。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _upload_tmp_path(target)
    try:
        await asyncio.to_thread(_copy_limited, src, tmp_path, max_bytes)
        await asyncio.to_thread(tmp_path.replace, target)
    except BaseException:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
        raise


async def stage_uploaded_video_stream(src: BinaryIO, target: Path, *, max_bytes: int) -> Path:
    """把上传视频写入 canonical 同目录的唯一 staging 路径，尚不改变正式选择。"""

    staged_file = _upload_tmp_path(target)
    try:
        await save_uploaded_video_stream(src, staged_file, max_bytes=max_bytes)
        return staged_file
    except BaseException:
        await asyncio.to_thread(staged_file.unlink, missing_ok=True)
        raise


def write_bytes_atomic(content: bytes, target: Path) -> None:
    """同步版本：把内存中的内容原子写入 target（同 dot-tmp + replace + 失败清理）。

    阻塞 I/O，供调用方自行决定线程化时机——需要与其它阻塞操作（如剧本锁）共享
    同一临界区时（见 `server/services/end_frame.py`），不能各自套一层 `asyncio.to_thread`。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _upload_tmp_path(target)
    try:
        tmp_path.write_bytes(content)
        tmp_path.replace(target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def stage_uploaded_bytes(content: bytes, target: Path) -> Path:
    """Write normalized upload bytes beside the canonical target without selecting them."""

    target.parent.mkdir(parents=True, exist_ok=True)
    staged_file = _upload_tmp_path(target)
    try:
        staged_file.write_bytes(content)
        return staged_file
    except BaseException:
        staged_file.unlink(missing_ok=True)
        raise


async def save_uploaded_bytes(content: bytes, target: Path) -> None:
    """把内存中的上传内容原子写入 target（同 dot-tmp + replace + 失败清理）。"""
    await asyncio.to_thread(write_bytes_atomic, content, target)


async def commit_manual_storyboard_upload(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    asset_path: str,
    staged_image: Path,
    current_image: Path,
    versions: VersionManager,
    original_filename: str | None,
) -> int:
    """Atomically select an uploaded storyboard with its metadata, version, and claim."""

    metadata: dict[str, str] = {"source": UPLOAD_VERSION_SOURCE}
    if original_filename:
        metadata["original_filename"] = original_filename
    outcomes = []
    commit = _storyboard_formal_image_callback(
        project_name=project_name,
        script_file=script_file,
        resource_id=shot_id,
        artifact_path=asset_path,
        prompt="",
        versions=versions,
        task_id=None,
        basis=None,
        outcome_box=outcomes,
        project_manager=get_project_manager(),
    )

    def _commit() -> int:
        version = commit(staged_image, current_image, metadata)
        if len(outcomes) != 1 or outcomes[0].version != version:
            raise RuntimeError("manual storyboard upload skipped formal image activation")
        return version

    try:
        return await run_noninterruptible_sync(_commit)
    finally:
        await asyncio.to_thread(staged_image.unlink, missing_ok=True)


ManualVideoMetadataCommit = Callable[[str | None, Callable[[Path], None]], None]


async def commit_manual_video_upload(
    *,
    project_path: Path,
    versions: VersionManager,
    resource_type: Literal["videos", "reference_videos"],
    resource_id: str,
    script_file: str,
    staged_video: Path,
    current_video: Path,
    thumbnail_file: Path,
    thumbnail_rel: str,
    original_filename: str | None,
    commit_metadata: ManualVideoMetadataCommit,
) -> int:
    """把手动视频、版本选择、缩略图、剧本元数据与 claim 清理作为一个正式提交。

    缩略图先从不可见的 staging 视频生成。正式提交随后复用 ProjectManager 的剧本/项目
    事务、VersionManager 的 staged activation 与 Manifest 自身的事务补偿；任一步失败
    都会逐层恢复旧选择。同步提交一旦开始便不可被请求取消打断。
    """

    staged_thumbnail = _upload_tmp_path(thumbnail_file)
    try:
        extracted = await extract_video_thumbnail(staged_video, staged_thumbnail)
        selected_thumbnail = thumbnail_rel if extracted is not None and staged_thumbnail.is_file() else None

        def _commit() -> int:
            committed_version: int | None = None

            def _activate(_script_path: Path) -> None:
                nonlocal committed_version

                def _forget_claim() -> None:
                    # Manual uploads have no self-verifying paid-request facts, so an older
                    # current-basis claim cannot survive their formal replacement.
                    forget_current_resource_artifact(
                        project_path,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        script_file=script_file,
                    )

                with formal_write_transaction(thumbnail_file):
                    if selected_thumbnail is None:
                        thumbnail_file.unlink(missing_ok=True)
                    else:
                        thumbnail_file.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(staged_thumbnail, thumbnail_file)

                    metadata: dict[str, str] = {"source": UPLOAD_VERSION_SOURCE}
                    if original_filename:
                        metadata["original_filename"] = original_filename
                    committed_version = versions.commit_staged_version(
                        resource_type,
                        resource_id,
                        "",
                        staged_file=staged_video,
                        current_file=current_video,
                        on_commit=_forget_claim,
                        **metadata,
                    )

            commit_metadata(selected_thumbnail, _activate)
            if committed_version is None:
                raise RuntimeError("manual video metadata commit skipped staged activation")
            return committed_version

        return await run_noninterruptible_sync(_commit)
    finally:
        await asyncio.gather(
            asyncio.to_thread(staged_video.unlink, missing_ok=True),
            asyncio.to_thread(staged_thumbnail.unlink, missing_ok=True),
        )


async def finalize_shot_video_upload(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    project_path: Path,
    video_rel: str,
    staged_video: Path,
    versions: VersionManager,
    original_filename: str | None,
) -> int:
    """通过共享正式写入 seam 提交镜头视频及其所有 sidecar。"""

    def _commit_metadata(thumb_rel: str | None, on_commit: Callable[[Path], None]) -> None:
        get_project_manager().batch_update_scene_assets(
            project_name=project_name,
            script_filename=script_file,
            updates=[
                (shot_id, "video_clip", video_rel),
                (shot_id, "video_uri", None),
                (shot_id, "video_thumbnail", thumb_rel),
            ],
            on_commit=on_commit,
        )

    return await commit_manual_video_upload(
        project_path=project_path,
        versions=versions,
        resource_type="videos",
        resource_id=shot_id,
        script_file=script_file,
        staged_video=staged_video,
        current_video=project_path / video_rel,
        thumbnail_file=project_path / "thumbnails" / f"scene_{shot_id}.jpg",
        thumbnail_rel=f"thumbnails/scene_{shot_id}.jpg",
        original_filename=original_filename,
        commit_metadata=_commit_metadata,
    )
