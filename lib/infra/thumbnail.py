"""Video frame extraction (first-frame thumbnail / last frame)"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from lib.infra.async_thread import run_sync_transaction
from lib.infra.ffmpeg import FfmpegUnavailableError, ffmpeg_executable, local_file_input
from lib.infra.media_probe import MediaProbeError, probe_media
from lib.infra.subprocess_deadline import (
    DEFAULT_TERMINATE_GRACE_SECONDS,
    Spawner,
    SubprocessDeadlineExceeded,
    run_with_deadline,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameExtractionDeadlines:
    """ffmpeg subprocess deadlines by operation, in seconds."""

    probe: float = 30.0
    """Demux-only packet counting (media probing)."""
    extract: float = 120.0
    """Extract one frame (first-frame thumbnail / frame-index lookup)."""
    count_frames: float = 300.0
    """Full decoding to count frames."""
    grace: float = DEFAULT_TERMINATE_GRACE_SECONDS
    """Grace period for exit after terminate; kill when exceeded."""


DEFAULT_DEADLINES = FrameExtractionDeadlines()

#: Maximum long edge for first-frame thumbnails, in pixels
VIDEO_THUMBNAIL_MAX_EDGE = 1280
# Shrink the long edge to at most the limit without upscaling; -2 preserves aspect ratio and rounds the short edge to even
VIDEO_THUMBNAIL_SCALE_FILTER = (
    f"scale=w='if(gte(iw,ih),min({VIDEO_THUMBNAIL_MAX_EDGE},iw),-2)'"
    f":h='if(gte(iw,ih),-2,min({VIDEO_THUMBNAIL_MAX_EDGE},ih))'"
)


async def extract_video_thumbnail(
    video_path: Path,
    thumbnail_path: Path,
    *,
    deadlines: FrameExtractionDeadlines = DEFAULT_DEADLINES,
    spawn: Spawner | None = None,
) -> Path | None:
    """
    Use ffmpeg to extract the first video frame as a JPEG thumbnail, with the long edge at most ``VIDEO_THUMBNAIL_MAX_EDGE`` (no upscaling).

    Args:
        video_path: Video file path
        thumbnail_path: Output thumbnail path

    Returns:
        Thumbnail path on success, or None on failure / unavailable bundled ffmpeg

    Note:
        Return None when bundled ffmpeg is unavailable, letting callers take the existing
        branch that omits video_thumbnail. With an empty poster, browsers natively render
        the first video-stream frame for frontend ``<video poster>`` elements, without a server-side placeholder.
    """
    if not await asyncio.to_thread(video_path.exists):
        return None

    try:
        ffmpeg = ffmpeg_executable()
    except FfmpegUnavailableError as exc:
        logger.info("随包 ffmpeg 不可用，跳过缩略图提取（前端将原生取首帧）：%s", exc)
        return None

    await asyncio.to_thread(thumbnail_path.parent.mkdir, parents=True, exist_ok=True)

    try:
        written = await _run_ffmpeg_to_output(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                *local_file_input(video_path),
                "-vframes",
                "1",
                "-vf",
                VIDEO_THUMBNAIL_SCALE_FILTER,
                "-q:v",
                "2",
            ],
            thumbnail_path,
            deadlines=deadlines,
            spawn=spawn,
        )
        return thumbnail_path if written else None
    except Exception:
        logger.warning("提取视频缩略图失败: %s", video_path, exc_info=True)
        return None


async def _probe_frame_count(
    ffmpeg: str,
    video_path: Path,
    *,
    count_frames: bool,
    deadlines: FrameExtractionDeadlines,
    spawn: Spawner | None,
) -> int | None:
    """
    Read the frame count of the first video stream.

    count_frames=False: demux and count video packets; fast, but packet and frame counts differ for some codecs.
    count_frames=True: fully decode to count frames; accurate but slow.
    """
    if not count_frames:
        try:
            probe = await probe_media(video_path, deadline_seconds=deadlines.probe, grace=deadlines.grace, spawn=spawn)
        except (MediaProbeError, OSError):
            return None
        video = probe.first_stream("video")
        return video.packet_count if video is not None else None

    try:
        result = await run_with_deadline(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-nostats",
                "-progress",
                "pipe:1",
                *local_file_input(video_path),
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            deadline_seconds=deadlines.count_frames,
            grace=deadlines.grace,
            capture_stdout=True,
            spawn=spawn,
        )
    except SubprocessDeadlineExceeded:
        logger.warning("解码计帧超时: %s", video_path)
        return None
    except (FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    frames = [line.removeprefix("frame=") for line in result.stdout.decode().splitlines() if line.startswith("frame=")]
    try:
        return int(frames[-1]) if frames else None
    except ValueError:
        return None


async def _run_ffmpeg_to_output(
    args: list[str],
    output_path: Path,
    *,
    deadlines: FrameExtractionDeadlines,
    spawn: Spawner | None,
) -> bool:
    """ffmpeg first writes a separate temporary file in the same directory (unique per call). Atomically replace ``output_path`` only on success with nonempty output; preserve existing artifacts on failure or timeout.

    ``args`` contains ffmpeg arguments without the output path.
    """
    temp_path = output_path.with_name(f".{output_path.stem}.{uuid4().hex}.tmp{output_path.suffix}")

    result = await run_with_deadline(
        [*args, str(temp_path)],
        deadline_seconds=deadlines.extract,
        grace=deadlines.grace,
        cleanup_paths=[temp_path],
        spawn=spawn,
    )

    return await run_sync_transaction(_promote_temp_output, result.returncode == 0, temp_path, output_path)


def _promote_temp_output(succeeded: bool, temp_path: Path, output_path: Path) -> bool:
    """Atomically replace ``output_path`` when ffmpeg succeeds and the temporary file is nonempty; always clean up the temporary file."""
    try:
        if not succeeded or not temp_path.exists() or temp_path.stat().st_size < 1:
            return False
        temp_path.replace(output_path)
        return True
    finally:
        temp_path.unlink(missing_ok=True)  # hard-delete: temp -- temporary ffmpeg output


async def _extract_frame_at_index(
    ffmpeg: str,
    video_path: Path,
    output_path: Path,
    frame_index: int,
    *,
    deadlines: FrameExtractionDeadlines,
    spawn: Spawner | None,
) -> bool:
    return await _run_ffmpeg_to_output(
        [
            ffmpeg,
            "-nostdin",
            "-y",
            *local_file_input(video_path),
            "-vf",
            f"select='eq(n\\,{frame_index})'",
            "-fps_mode",
            "vfr",
            "-frames:v",
            "1",
        ],
        output_path,
        deadlines=deadlines,
        spawn=spawn,
    )


async def extract_video_last_frame(
    video_path: Path,
    output_path: Path,
    *,
    deadlines: FrameExtractionDeadlines = DEFAULT_DEADLINES,
    spawn: Spawner | None = None,
) -> Path | None:
    """
    Extract the last video frame as a PNG image.

    Obtain the total frame count, then locate the last frame with the select filter,
    avoiding ``-sseof`` timestamp approximation issues.

    Optimization: first demux and count video packets (fast); if extraction at that index fails,
    fall back to fully decoding to count frames (accurate but slow).

    Args:
        video_path: Video file path
        output_path: Output image path (.png recommended)

    Returns:
        Output path on success, or None on failure / unavailable bundled ffmpeg
    """
    if not await asyncio.to_thread(video_path.exists):
        return None

    try:
        ffmpeg = ffmpeg_executable()
    except FfmpegUnavailableError as exc:
        logger.info("随包 ffmpeg 不可用，跳过尾帧提取：%s", exc)
        return None

    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)

    # 1. Try the fast path (demux-only packet count), then fall back to full decoding on failure
    total_frames = await _probe_frame_count(ffmpeg, video_path, count_frames=False, deadlines=deadlines, spawn=spawn)
    try:
        if (
            total_frames is not None
            and total_frames > 0
            and await _extract_frame_at_index(
                ffmpeg, video_path, output_path, total_frames - 1, deadlines=deadlines, spawn=spawn
            )
        ):
            return output_path

        total_frames = await _probe_frame_count(ffmpeg, video_path, count_frames=True, deadlines=deadlines, spawn=spawn)
        if total_frames is None or total_frames < 1:
            return None
        if not await _extract_frame_at_index(
            ffmpeg, video_path, output_path, total_frames - 1, deadlines=deadlines, spawn=spawn
        ):
            return None

        return output_path
    except Exception:
        logger.warning("提取视频尾帧失败: %s", video_path, exc_info=True)
        return None


async def extract_video_frame_before(
    video_path: Path,
    output_path: Path,
    end_seconds: float,
    *,
    deadlines: FrameExtractionDeadlines = DEFAULT_DEADLINES,
    spawn: Spawner | None = None,
) -> Path | None:
    """
    Extract the last frame before source time ``end_seconds`` as a PNG: the image held at that out-point.

    Seek to one second before the out-point and decode sequentially. Discard frames starting at or after
    the out-point, overwriting the same output with each remaining frame; the final write is the last frame before the out-point.

    Returns:
        Output path on success, or None on failure / unavailable bundled ffmpeg
    """
    if end_seconds <= 0 or not await asyncio.to_thread(video_path.exists):
        return None

    try:
        ffmpeg = ffmpeg_executable()
    except FfmpegUnavailableError as exc:
        logger.info("随包 ffmpeg 不可用，跳过出点帧提取：%s", exc)
        return None

    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    seek = max(0.0, end_seconds - 1.0)
    try:
        written = await _run_ffmpeg_to_output(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                "-ss",
                f"{seek:.6f}",
                *local_file_input(video_path),
                "-map",
                "0:v:0",
                "-vf",
                f"trim=end={end_seconds - seek:.6f}",
                "-fps_mode",
                "passthrough",
                "-update",
                "1",
            ],
            output_path,
            deadlines=deadlines,
            spawn=spawn,
        )
        return output_path if written else None
    except Exception:
        logger.warning("提取视频出点帧失败: %s", video_path, exc_info=True)
        return None
