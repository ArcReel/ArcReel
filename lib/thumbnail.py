"""视频帧提取（首帧缩略图 / 尾帧）"""

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


@functools.cache
def _ffprobe_available() -> bool:
    """ffprobe 可执行文件是否在 PATH 中（独立检查：精简容器可能只装了 ffmpeg）。"""
    return shutil.which("ffprobe") is not None


def _reset_for_tests() -> None:
    """test helper — 清缓存让 monkeypatch shutil.which 立刻生效。"""
    _ffmpeg_available.cache_clear()
    _ffprobe_available.cache_clear()


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


async def _probe_frame_count(video_path: Path, *, count_frames: bool) -> int | None:
    """
    用 ffprobe 读取视频帧数。

    count_frames=False：从容器元数据读 ``nb_frames``，瞬时返回，但部分容器为 ``N/A``。
    count_frames=True：``-count_frames`` 全量解码统计 ``nb_read_frames``，精确但慢。
    """
    entry = "stream=nb_read_frames" if count_frames else "stream=nb_frames"
    args = ["ffprobe", "-v", "error"]
    if count_frames:
        args.append("-count_frames")
    args += [
        "-select_streams",
        "v:0",
        "-show_entries",
        entry,
        "-of",
        "csv=p=0",
        str(video_path),
    ]

    try:
        probe = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await probe.communicate()
    except (FileNotFoundError, OSError):
        return None

    if probe.returncode != 0:
        return None

    try:
        return int(stdout.decode().strip())
    except (ValueError, AttributeError):
        return None


async def _extract_frame_at_index(
    video_path: Path,
    output_path: Path,
    frame_index: int,
) -> bool:
    temp_path = output_path.with_name(f".{output_path.stem}.tmp{output_path.suffix}")
    if temp_path.exists():
        temp_path.unlink()

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"select='eq(n\\,{frame_index})'",
        "-fps_mode",
        "vfr",
        "-frames:v",
        "1",
        str(temp_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()

    if proc.returncode != 0 or not temp_path.exists() or temp_path.stat().st_size < 1:
        if temp_path.exists():
            temp_path.unlink()
        return False

    temp_path.replace(output_path)
    return True


async def extract_video_last_frame(
    video_path: Path,
    output_path: Path,
) -> Path | None:
    """
    提取视频最后一帧作为 PNG 图片。

    通过 ffprobe 获取精确总帧数，再用 select 滤镜定位最后一帧，
    避免 ``-sseof`` 的时间戳近似问题。

    优化：优先读取容器元数据 ``nb_frames``（瞬时），失败时回退到
    ``-count_frames`` 全量解码（精确但慢）。

    Args:
        video_path: 视频文件路径
        output_path: 输出图片路径（建议 .png）

    Returns:
        输出路径（成功）或 None（失败 / ffmpeg 或 ffprobe 不可用）
    """
    if not video_path.exists():
        return None

    if not _ffmpeg_available() or not _ffprobe_available():
        logger.info("ffmpeg/ffprobe 不可用，跳过尾帧提取")
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 先走快路径（容器元数据），失败再回退到全量解码
    total_frames = await _probe_frame_count(video_path, count_frames=False)
    try:
        if total_frames is not None and total_frames > 0:
            if await _extract_frame_at_index(video_path, output_path, total_frames - 1):
                return output_path

        total_frames = await _probe_frame_count(video_path, count_frames=True)
        if total_frames is None or total_frames < 1:
            return None
        if not await _extract_frame_at_index(video_path, output_path, total_frames - 1):
            return None

        return output_path
    except Exception:
        logger.warning("提取视频尾帧失败: %s", video_path, exc_info=True)
        return None
