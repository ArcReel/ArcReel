"""Video first-frame thumbnail extraction."""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def extract_video_thumbnail(
    video_path: Path,
    thumbnail_path: Path,
) -> Path | None:
    """
    Extract the first frame of a video as a JPEG thumbnail using ffmpeg.

    Args:
        video_path: Video file path
        thumbnail_path: Output thumbnail path

    Returns:
        Thumbnail path (on success) or None (on failure)
    """
    if not video_path.exists():
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
        logger.warning("Failed to extract video thumbnail: %s", video_path, exc_info=True)
        return None
