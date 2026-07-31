"""音频工具：上传校验用的时长探测。"""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


@functools.cache
def _ffprobe_available() -> bool:
    """ffprobe 可执行文件是否在 PATH 中（结果缓存，避免每次调用重复 shutil.which）。"""
    return shutil.which("ffprobe") is not None


def _reset_for_tests() -> None:
    """test helper —— 清缓存让 monkeypatch shutil.which 立刻生效。"""
    _ffprobe_available.cache_clear()


async def probe_audio_duration_seconds(content: bytes, suffix: str) -> float | None:
    """探测音频字节的时长（秒）。

    ffprobe 不可用时返回 None（调用方按仓库惯例降级：跳过时长校验，不阻断上传），
    与 lib/thumbnail.py 的 ffmpeg/ffprobe 降级模式一致。

    Raises:
        ValueError: ffprobe 可用但无法解出时长（文件损坏或非音频内容）。
    """
    if not _ffprobe_available():
        logger.info("ffprobe 不可用，跳过音频时长探测")
        return None

    with tempfile.NamedTemporaryFile(dir=tempfile.gettempdir(), suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
    except (FileNotFoundError, OSError):
        logger.info("ffprobe 调用失败，跳过音频时长探测")
        return None
    finally:
        tmp_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise ValueError("音频文件无法解析")

    try:
        return float(stdout.decode().strip())
    except ValueError:
        raise ValueError("音频文件无法解析") from None
