"""音频工具：上传校验用的时长探测。"""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_FFPROBE_TIMEOUT_SECONDS = 10.0


@functools.cache
def _ffprobe_available() -> bool:
    """ffprobe 可执行文件是否在 PATH 中（结果缓存，避免每次调用重复 shutil.which）。"""
    return shutil.which("ffprobe") is not None


def _reset_for_tests() -> None:
    """test helper —— 清缓存让 monkeypatch shutil.which 立刻生效。"""
    _ffprobe_available.cache_clear()


async def _run_ffprobe(extra_args: list[str]) -> bytes:
    """执行一次 ffprobe 子进程，返回 stdout；超时/非零退出统一按不可解析处理。

    `-protocol_whitelist file` 限制 ffprobe 只读本地文件：上传字节可能嵌套
    HLS/RTMP 等播放列表引用，ffprobe 默认会跟随其中的协议自动发起网络请求
    （对内网地址同样生效），不加白名单会把这个探测调用变成 SSRF 跳板。
    超时同样按 ValueError 处理，避免损坏文件让 ffprobe 挂起占用请求。
    """
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        *extra_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_FFPROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError("音频文件无法解析") from None

    if proc.returncode != 0:
        raise ValueError("音频文件无法解析")
    return stdout


async def probe_audio_duration_seconds(content: bytes, suffix: str) -> float | None:
    """探测音频字节的时长（秒），并确认其中确有可解码的音频流。

    ffprobe 不可用时返回 None（调用方按仓库惯例降级：跳过时长校验，不阻断上传），
    与 lib/thumbnail.py 的 ffmpeg/ffprobe 降级模式一致。

    Raises:
        ValueError: ffprobe 可用但无法解出时长、超时，或容器内没有音频流
            （如把视频文件改名为 .wav/.mp3 上传）。
    """
    if not _ffprobe_available():
        logger.info("ffprobe 不可用，跳过音频时长探测")
        return None

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=tempfile.gettempdir(), suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
    except OSError:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise

    try:
        stream_types = await _run_ffprobe(
            ["-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(tmp_path)]
        )
        if b"audio" not in stream_types:
            raise ValueError("音频文件无法解析")

        duration_out = await _run_ffprobe(["-show_entries", "format=duration", "-of", "csv=p=0", str(tmp_path)])
    except (FileNotFoundError, OSError):
        logger.info("ffprobe 调用失败，跳过音频时长探测")
        return None
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        return float(duration_out.decode().strip())
    except ValueError:
        raise ValueError("音频文件无法解析") from None
