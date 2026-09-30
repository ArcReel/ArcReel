"""视频帧提取（首帧缩略图 / 尾帧）"""

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from lib.infra.ffmpeg import FfmpegUnavailableError, ffmpeg_executable
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
    """ffmpeg 子进程按操作分档的 deadline（秒）。"""

    probe: float = 30.0
    """只解复用读包数（媒体探测）。"""
    extract: float = 120.0
    """抽取单帧（首帧缩略图 / 按帧号定位）。"""
    count_frames: float = 300.0
    """全量解码计帧。"""
    grace: float = DEFAULT_TERMINATE_GRACE_SECONDS
    """terminate 后等待退出的宽限期，超出即 kill。"""


DEFAULT_DEADLINES = FrameExtractionDeadlines()


async def extract_video_thumbnail(
    video_path: Path,
    thumbnail_path: Path,
    *,
    deadlines: FrameExtractionDeadlines = DEFAULT_DEADLINES,
    spawn: Spawner | None = None,
) -> Path | None:
    """
    使用 ffmpeg 提取视频第一帧作为 JPEG 缩略图。

    Args:
        video_path: 视频文件路径
        thumbnail_path: 输出缩略图路径

    Returns:
        缩略图路径（成功）或 None（失败 / 随包 ffmpeg 不可用）

    Note:
        随包 ffmpeg 不可用时返回 None，让调用方走「不写 video_thumbnail
        字段」的现有分支；前端 ``<video poster>`` 在 poster 为空时浏览器会
        原生从视频流取首帧渲染，无需 server-side placeholder。
    """
    if not video_path.exists():  # noqa: ASYNC240 -- 输入视频存在性检查，本地元数据；抽帧本身走 run_with_deadline 子进程
        return None

    try:
        ffmpeg = ffmpeg_executable()
    except FfmpegUnavailableError as exc:
        logger.info("随包 ffmpeg 不可用，跳过缩略图提取（前端将原生取首帧）：%s", exc)
        return None

    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        written = await _run_ffmpeg_to_output(
            [ffmpeg, "-nostdin", "-y", "-i", str(video_path), "-vframes", "1", "-q:v", "2"],
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
    读取首个视频流的帧数。

    count_frames=False：只解复用数视频包，快，但少数编码的包数与帧数不一致。
    count_frames=True：全量解码计帧，精确但慢。
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
                "-i",
                str(video_path),
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
    """ffmpeg 先写同目录的独立临时文件（每次调用唯一），成功且非空才原子替换 ``output_path``；失败或超时不动已有产物。

    ``args`` 为不含输出路径的 ffmpeg 参数。
    """
    temp_path = output_path.with_name(f".{output_path.stem}.{uuid4().hex}.tmp{output_path.suffix}")

    result = await run_with_deadline(
        [*args, str(temp_path)],
        deadline_seconds=deadlines.extract,
        grace=deadlines.grace,
        cleanup_paths=[temp_path],
        spawn=spawn,
    )

    try:
        if result.returncode != 0 or not temp_path.exists() or temp_path.stat().st_size < 1:
            return False
        temp_path.replace(output_path)
        return True
    finally:
        temp_path.unlink(missing_ok=True)


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
            "-i",
            str(video_path),
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
    提取视频最后一帧作为 PNG 图片。

    先取得总帧数，再用 select 滤镜定位最后一帧，避免 ``-sseof`` 的时间戳近似问题。

    优化：优先只解复用数视频包（快），按该帧号抽不出时回退到全量解码计帧（精确但慢）。

    Args:
        video_path: 视频文件路径
        output_path: 输出图片路径（建议 .png）

    Returns:
        输出路径（成功）或 None（失败 / 随包 ffmpeg 不可用）
    """
    if not video_path.exists():  # noqa: ASYNC240 -- 输入视频存在性检查，本地元数据；抽帧本身走 run_with_deadline 子进程
        return None

    try:
        ffmpeg = ffmpeg_executable()
    except FfmpegUnavailableError as exc:
        logger.info("随包 ffmpeg 不可用，跳过尾帧提取：%s", exc)
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 先走快路径（只解复用数包），失败再回退到全量解码
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
