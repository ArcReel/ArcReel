"""视频首帧缩略图提取"""

import asyncio
import functools
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


@functools.cache
def _ffmpeg_available() -> bool:
    """ffmpeg 可执行文件是否在 PATH 中（结果缓存，避免每次调用重复 shutil.which）。"""
    return shutil.which("ffmpeg") is not None


def _reset_for_tests() -> None:
    """test helper — 清缓存让 monkeypatch shutil.which 立刻生效。"""
    _ffmpeg_available.cache_clear()


async def extract_video_thumbnail(
    video_path: Path,
    thumbnail_path: Path,
) -> Path | None:
    """
    使用 ffmpeg 提取视频第一帧作为 JPEG 缩略图。

    Args:
        video_path: 视频文件路径
        thumbnail_path: 输出缩略图路径

    Returns:
        缩略图路径（成功）或 None（失败 / ffmpeg 不可用）

    Note:
        当 ffmpeg 不在 PATH 中时返回 None，让调用方走「不写 video_thumbnail
        字段」的现有分支；前端 ``<video poster>`` 在 poster 为空时浏览器会
        原生从视频流取首帧渲染，无需 server-side placeholder。
    """
    if not video_path.exists():
        return None

    if not _ffmpeg_available():
        logger.info("ffmpeg 不可用，跳过缩略图提取（前端将原生取首帧）")
        return None

    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i",
            str(video_path),
            "-vframes",
            "1",
            "-q:v",
            "2",
            "-y",
            str(thumbnail_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode != 0 or not thumbnail_path.exists():
            return None

        return thumbnail_path
    except Exception:
        logger.warning("提取视频缩略图失败: %s", video_path, exc_info=True)
        return None
