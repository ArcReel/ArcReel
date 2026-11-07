"""MP4 faststart: move moov before mdat so browsers can play and seek after receiving the file header.

Provider output and uploaded videos often store moov (the index) at the end: the encoder
builds the index while writing mdat, then writes moov. Browsers must first fetch the index
with a Range request to the file tail before playback can start.

Determine ordering by reading only top-level box headers (8 to 16 bytes each), without
running ffmpeg. When needed, remux without re-encoding using bundled ffmpeg with
``-c copy -movflags +faststart``, then atomically replace the original file.

Always best effort: unavailable ffmpeg, remux failure or timeout preserves the original
and logs the issue without failing generation or upload. Only cancellation propagates.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from lib.infra.async_thread import run_sync_transaction
from lib.infra.ffmpeg import FfmpegUnavailableError, ffmpeg_executable, local_file_input
from lib.infra.subprocess_deadline import DEFAULT_TERMINATE_GRACE_SECONDS, Spawner, run_with_deadline

logger = logging.getLogger(__name__)

#: Extensions eligible for faststart: mp4 / mov / m4v belong to the ISO BMFF container family
FASTSTART_SUFFIXES: frozenset[str] = frozenset({".mp4", ".mov", ".m4v"})

#: Remuxing only copies streams and is disk-bound; allow ample time even for the 2 GiB output limit
DEFAULT_REMUX_DEADLINE_SECONDS = 300.0

#: Top-level box traversal limit: normal files have very few; exceeding it means unrecognized input, left untouched
_MAX_TOP_LEVEL_BOXES = 4096

_BOX_HEADER_BYTES = 8
_LARGE_SIZE_BYTES = 8
_QUICKTIME_BRAND = b"qt  "


@dataclass(frozen=True, slots=True)
class Mp4Layout:
    """Layout findings for top-level boxes."""

    major_brand: bytes | None
    """Major brand from ``ftyp``; None when the first box is not ``ftyp`` (legacy QuickTime)."""
    moov_first: bool
    """Whether moov precedes the first mdat."""

    @property
    def muxer(self) -> str:
        """ffmpeg muxer for remuxing: keep QuickTime as mov and use mp4 for other formats."""
        if self.major_brand is None or self.major_brand == _QUICKTIME_BRAND:
            return "mov"
        return "mp4"


def _read_box_header(handle: BinaryIO, offset: int, file_size: int) -> tuple[bytes, int] | None:
    """Read the box header at ``offset`` and return (type, total box length); return None for incomplete headers or invalid lengths."""
    handle.seek(offset)
    header = handle.read(_BOX_HEADER_BYTES)
    if len(header) < _BOX_HEADER_BYTES:
        return None
    size = int.from_bytes(header[:4], "big")
    box_type = header[4:]
    if size == 1:
        large = handle.read(_LARGE_SIZE_BYTES)
        if len(large) < _LARGE_SIZE_BYTES:
            return None
        size = int.from_bytes(large, "big")
        if size < _BOX_HEADER_BYTES + _LARGE_SIZE_BYTES:
            return None
    elif size == 0:
        # 0 means the box extends to the end of the file
        size = file_size - offset
    elif size < _BOX_HEADER_BYTES:
        return None
    if offset + size > file_size:
        return None
    return box_type, size


def _read_major_brand(handle: BinaryIO, offset: int) -> bytes | None:
    handle.seek(offset + _BOX_HEADER_BYTES)
    brand = handle.read(4)
    return brand if len(brand) == 4 else None


def read_mp4_layout(path: Path) -> Mp4Layout | None:
    """Parse top-level boxes to determine whether moov precedes mdat.

    Return None for unrecognized ISO BMFF (incomplete headers or out-of-bounds lengths)
    or missing moov / mdat; callers then leave the file untouched.
    Reads block synchronously, so async callers must use a thread.
    """
    file_size = path.stat().st_size
    major_brand: bytes | None = None
    moov_index: int | None = None
    mdat_index: int | None = None
    offset = 0
    with open(path, "rb") as handle:
        for index in range(_MAX_TOP_LEVEL_BOXES):
            if offset >= file_size:
                return None
            parsed = _read_box_header(handle, offset, file_size)
            if parsed is None:
                return None
            box_type, size = parsed
            if index == 0 and box_type == b"ftyp" and size >= _BOX_HEADER_BYTES + 4:
                # The major brand immediately follows the header; if it does not fit, treat it as absent without reading into the next box
                major_brand = _read_major_brand(handle, offset)
            elif box_type == b"moov" and moov_index is None:
                moov_index = index
            elif box_type == b"mdat" and mdat_index is None:
                mdat_index = index
            if moov_index is not None and mdat_index is not None:
                return Mp4Layout(major_brand=major_brand, moov_first=moov_index < mdat_index)
            offset += size
    return None


async def ensure_faststart(
    path: Path,
    *,
    deadline_seconds: float = DEFAULT_REMUX_DEADLINE_SECONDS,
    grace: float = DEFAULT_TERMINATE_GRACE_SECONDS,
    resolve_ffmpeg: Callable[[], str] = ffmpeg_executable,
    spawn: Spawner | None = None,
) -> bool:
    """Remux ``path`` in place for faststart when moov follows mdat; return whether the file was replaced.

    First write a unique temporary file in the same directory, verify that moov is first,
    then atomically replace ``path``. On any failure, preserve the original, delete the
    temporary file and log the issue. ``path`` may lack an extension (for example, an
    in-progress ``.part`` download); explicitly choose the muxer from the ``ftyp`` brand.

    Raises:
        asyncio.CancelledError: The task was cancelled; the subprocess and temporary file have been cleaned up, and the original is unchanged.
    """
    try:
        layout = await asyncio.to_thread(read_mp4_layout, path)
    except OSError:
        logger.warning("读取视频 box 布局失败，跳过 faststart: %s", path, exc_info=True)
        return False
    if layout is None or layout.moov_first:
        return False

    try:
        ffmpeg = resolve_ffmpeg()
    except FfmpegUnavailableError as exc:
        logger.info("随包 ffmpeg 不可用，跳过 faststart（视频需下载到文件尾才能开播）：%s", exc)
        return False

    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.faststart")
    try:
        result = await run_with_deadline(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-y",
                *local_file_input(path),
                "-map",
                "0",
                "-ignore_unknown",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-f",
                layout.muxer,
                str(temp_path),
            ],
            deadline_seconds=deadline_seconds,
            grace=grace,
            cleanup_paths=[temp_path],
            spawn=spawn,
        )
        if result.returncode != 0:
            logger.warning("faststart 重封装失败（退出码 %s），保留原文件: %s", result.returncode, path)
            return False
        remuxed = await asyncio.to_thread(read_mp4_layout, temp_path)
        if remuxed is None or not remuxed.moov_first:
            logger.warning("faststart 重封装产物仍不是 moov 在前，保留原文件: %s", path)
            return False
        await run_sync_transaction(os.replace, temp_path, path)
        return True
    except Exception:
        logger.warning("faststart 重封装出错，保留原文件: %s", path, exc_info=True)
        return False
    finally:
        # Only log cleanup failures (for example, a temporary file held by another process); best-effort remuxing must not raise or mask cancellation
        try:
            # hard-delete: temp -- temporary faststart remux output
            await run_sync_transaction(temp_path.unlink, missing_ok=True)
        except OSError:
            logger.warning("faststart 临时文件清理失败: %s", temp_path, exc_info=True)
