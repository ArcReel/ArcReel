"""MP4 faststart：把 moov 挪到 mdat 之前，浏览器拿到文件头即可开播与拖动进度。

供应商成片与用户上传的视频常把 moov（索引）写在文件末尾：编码器边写 mdat 边累积索引，
写完才落 moov。这样的文件浏览器要先 Range 请求到文件尾取回索引才能开播。

判断顺序只读顶层 box 头（每个 box 8~16 字节），不跑 ffmpeg；需要时用随包 ffmpeg
``-c copy -movflags +faststart`` 重封装（不重编码）后原子替换原文件。

一律尽力而为：随包 ffmpeg 不可用、重封装失败或超时都保留原文件并记日志，不让生成或上传失败；
只有取消照常传播。
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

#: 走 faststart 的扩展名：mp4 / mov / m4v 同属 ISO BMFF 容器家族
FASTSTART_SUFFIXES: frozenset[str] = frozenset({".mp4", ".mov", ".m4v"})

#: 重封装只拷贝码流，耗时取决于磁盘吞吐；产物上限 2 GiB 也留足余量
DEFAULT_REMUX_DEADLINE_SECONDS = 300.0

#: 顶层 box 遍历上限：正常文件只有寥寥几个顶层 box，超出即视为无法识别，不做处理
_MAX_TOP_LEVEL_BOXES = 4096

_BOX_HEADER_BYTES = 8
_LARGE_SIZE_BYTES = 8
_QUICKTIME_BRAND = b"qt  "


@dataclass(frozen=True, slots=True)
class Mp4Layout:
    """顶层 box 的布局结论。"""

    major_brand: bytes | None
    """``ftyp`` 的主品牌；首个 box 不是 ``ftyp``（老式 QuickTime）时为 None。"""
    moov_first: bool
    """moov 是否位于首个 mdat 之前。"""

    @property
    def muxer(self) -> str:
        """重封装用的 ffmpeg 封装器：QuickTime 仍封成 mov，其余封成 mp4。"""
        if self.major_brand is None or self.major_brand == _QUICKTIME_BRAND:
            return "mov"
        return "mp4"


def _read_box_header(handle: BinaryIO, offset: int, file_size: int) -> tuple[bytes, int] | None:
    """读取 ``offset`` 处的 box 头，返回 (类型, box 总长)；头不完整或长度非法返回 None。"""
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
        # 0 表示一直延伸到文件尾
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
    """解析顶层 box，判断 moov 是否在 mdat 之前。

    文件不是可识别的 ISO BMFF（box 头残缺、长度越界）或缺少 moov / mdat 时返回 None，
    调用方据此不做任何处理。同步阻塞读，异步调用方放进线程。
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
                # major brand 紧跟 box 头；装不下时视为没有，不越界读后一个 box
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
    """moov 在 mdat 之后时，就地把 ``path`` 重封装为 faststart；返回是否发生了替换。

    重封装先写同目录的唯一临时文件，校验产物确实 moov 在前后才原子替换 ``path``；
    任何失败都保留原文件、删除临时文件并记日志。``path`` 可以没有扩展名（如下载中的 ``.part``），
    封装格式按 ``ftyp`` 品牌显式指定。

    Raises:
        asyncio.CancelledError: 所在任务被取消；子进程与临时文件已清理，原文件不变。
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
        # 清理失败（如临时文件被其他进程占用）只记日志，不让尽力而为的重封装抛错或盖过取消
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("faststart 临时文件清理失败: %s", temp_path, exc_info=True)
