"""用户自主上传分镜图/视频的 finalize 服务层。

复用生成链路的元数据回写、缩略图与版本记录原语，让上传产出的资产
在状态推导、SSE 刷新、版本回滚上与 AI 生成的资产行为一致。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import BinaryIO, Literal

from lib.thumbnail import extract_video_thumbnail
from lib.version_manager import VersionManager
from server.services.generation_tasks import get_project_manager

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


async def save_uploaded_bytes(content: bytes, target: Path) -> None:
    """把内存中的上传内容原子写入 target（同 dot-tmp + replace + 失败清理）。"""

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _upload_tmp_path(target)
        try:
            tmp_path.write_bytes(content)
            tmp_path.replace(target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    await asyncio.to_thread(_write)


def record_upload_version(
    *,
    versions: VersionManager,
    resource_type: str,
    resource_id: str,
    current_file: Path,
    original_filename: str | None,
) -> int:
    """把刚写入 canonical 路径的上传文件登记为新版本，返回版本号。

    调用方须在覆写 canonical 文件**之前**先调 ``ensure_current_tracked``
    补登旧文件（镜像 MediaGenerator 的版本顺序），否则旧版本字节会丢失。
    """
    metadata: dict[str, str] = {"source": UPLOAD_VERSION_SOURCE}
    if original_filename:
        metadata["original_filename"] = original_filename
    return versions.add_version(
        resource_type=resource_type,
        resource_id=resource_id,
        prompt="",
        source_file=current_file,
        **metadata,
    )


async def finalize_shot_storyboard_upload(
    *, project_name: str, script_file: str, shot_id: str, asset_path: str
) -> None:
    """分镜图上传后的剧本元数据回写（status 由 update_scene_status 自动推导）。"""
    await asyncio.to_thread(
        get_project_manager().update_scene_asset,
        project_name=project_name,
        script_filename=script_file,
        scene_id=shot_id,
        asset_type="storyboard_image",
        asset_path=asset_path,
    )


async def finalize_shot_video_upload(
    *, project_name: str, script_file: str, shot_id: str, project_path: Path, video_rel: str
) -> None:
    """镜头视频上传后的 finalize：抽缩略图 + 单次锁内回写 video_clip / video_uri / video_thumbnail。

    旧 video_uri 指向上一次生成的 provider 端产物，与本地新文件不再对应，必须清空。
    """
    video_file = project_path / video_rel
    thumbnail_file = project_path / "thumbnails" / f"scene_{shot_id}.jpg"
    if await extract_video_thumbnail(video_file, thumbnail_file):
        thumb_rel: str | None = f"thumbnails/scene_{shot_id}.jpg"
    else:
        thumbnail_file.unlink(missing_ok=True)
        thumb_rel = None

    await asyncio.to_thread(
        get_project_manager().batch_update_scene_assets,
        project_name=project_name,
        script_filename=script_file,
        updates=[
            (shot_id, "video_clip", video_rel),
            (shot_id, "video_uri", None),
            (shot_id, "video_thumbnail", thumb_rel),
        ],
    )
