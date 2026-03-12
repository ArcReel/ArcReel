"""视频首帧缩略图提取"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def extract_video_thumbnail(
    video_path: Path,
    thumbnail_path: Path,
) -> Optional[Path]:
    """
    使用 ffmpeg 提取视频第一帧作为 JPEG 缩略图。

    Args:
        video_path: 视频文件路径
        thumbnail_path: 输出缩略图路径

    Returns:
        缩略图路径（成功）或 None（失败）
    """
    if not video_path.exists():
        return None

    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            "-y", str(thumbnail_path),
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
