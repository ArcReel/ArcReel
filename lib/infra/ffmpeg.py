"""随包 ffmpeg 的唯一查找器。

固定使用 ``imageio-ffmpeg`` wheel 自带的 ffmpeg：不看系统 PATH、不读 ``IMAGEIO_FFMPEG_EXE``、
不提供路径配置，所有环境用同一个构建。仓库内调用 ffmpeg 的地方都经 :func:`ffmpeg_executable`
取用它；它不带 ffprobe，媒体探测见 :mod:`lib.infra.media_probe`。
"""

from __future__ import annotations

import functools
import importlib.resources
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from imageio_ffmpeg import binaries

logger = logging.getLogger(__name__)

_SELF_CHECK_TIMEOUT_SECONDS = 30.0

VersionRunner = Callable[[Path], "subprocess.CompletedProcess[bytes]"]


class FfmpegUnavailableError(RuntimeError):
    """随包 ffmpeg 在当前环境不可用；消息即不可用原因。"""


@dataclass(frozen=True, slots=True)
class FfmpegStatus:
    """随包 ffmpeg 的自检结论。``unavailable_reason`` 为 None 即可用。"""

    executable: Path | None
    version: str | None
    unavailable_reason: str | None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


def _run_version(executable: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(executable), "-version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_SELF_CHECK_TIMEOUT_SECONDS,
        check=False,
    )


def inspect_bundled_ffmpeg(binaries_dir: Path, *, run_version: VersionRunner = _run_version) -> FfmpegStatus:
    """在 ``binaries_dir`` 中定位随包 ffmpeg 并运行 ``-version`` 自检。

    imageio-ffmpeg 的平台 wheel 在该目录只放当前平台的一个 ``ffmpeg-*`` 可执行文件；
    sdist 安装或不受支持的平台没有这个文件。
    """
    candidates = sorted(entry for entry in binaries_dir.iterdir() if entry.name.startswith("ffmpeg-"))
    if len(candidates) != 1:
        return FfmpegStatus(None, None, "imageio-ffmpeg 未附带当前平台的 ffmpeg 可执行文件")
    executable = candidates[0]
    try:
        result = run_version(executable)
    except (OSError, subprocess.SubprocessError) as exc:
        return FfmpegStatus(executable, None, f"随包 ffmpeg 无法运行：{exc}")
    if result.returncode != 0:
        return FfmpegStatus(executable, None, f"随包 ffmpeg 自检失败：退出码 {result.returncode}")
    lines = result.stdout.decode(errors="replace").splitlines()
    return FfmpegStatus(executable, lines[0].strip() if lines else None, None)


@functools.cache
def ffmpeg_status() -> FfmpegStatus:
    """随包 ffmpeg 的自检结论；首次调用时自检一次，结论在进程内固定。

    服务启动时先调用一次，之后的调用只读缓存；成片渲染据此报明不可用原因。
    """
    return inspect_bundled_ffmpeg(Path(str(importlib.resources.files(binaries))))


def ffmpeg_executable() -> str:
    """随包 ffmpeg 的可执行文件路径。

    Raises:
        FfmpegUnavailableError: 自检未通过，消息为不可用原因。
    """
    status = ffmpeg_status()
    if status.executable is None or status.unavailable_reason is not None:
        raise FfmpegUnavailableError(status.unavailable_reason or "随包 ffmpeg 不可用")
    return str(status.executable)


def log_ffmpeg_status() -> FfmpegStatus:
    """执行自检并记录结论，供服务启动时调用。"""
    status = ffmpeg_status()
    if status.available:
        logger.info("随包 ffmpeg 可用：%s（%s）", status.version, status.executable)
    else:
        logger.warning("随包 ffmpeg 不可用：%s", status.unavailable_reason)
    return status


def reset_for_tests() -> None:
    """test helper —— 清除进程级自检缓存。"""
    ffmpeg_status.cache_clear()
