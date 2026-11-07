"""Single resolver for bundled ffmpeg.

Use only ffmpeg shipped by the ``imageio-ffmpeg`` wheel: ignore system PATH and
``IMAGEIO_FFMPEG_EXE`` and expose no configurable path, ensuring the same build in
all environments. All repository callers use ``ffmpeg_executable``. It does not
include ffprobe; see ``lib.infra.media_probe`` for probing.
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
    """Bundled ffmpeg is unavailable in this environment; the message states why."""


@dataclass(frozen=True, slots=True)
class FfmpegStatus:
    """Bundled ffmpeg self-check result; ``unavailable_reason``=None means available."""

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
    """Locate bundled ffmpeg in ``binaries_dir`` and self-check with ``-version``.

    The platform wheel places exactly one platform-specific ``ffmpeg-*`` executable
    there; sdist installations and unsupported platforms have no such file.
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
    """Bundled ffmpeg self-check, run once and cached for the process.

    Startup invokes it first; later callers read the cache. Final-cut rendering
    uses it to report why the executable is unavailable.
    """
    return inspect_bundled_ffmpeg(Path(str(importlib.resources.files(binaries))))


def ffmpeg_executable() -> str:
    """Return the bundled ffmpeg executable path.

    Raises:
        FfmpegUnavailableError: Self-check failed; the message states why.
    """
    status = ffmpeg_status()
    if status.executable is None or status.unavailable_reason is not None:
        raise FfmpegUnavailableError(status.unavailable_reason or "随包 ffmpeg 不可用")
    return str(status.executable)


def local_file_input(path: Path) -> list[str]:
    """ffmpeg arguments for reading ``path`` as a local file.

    Use ``file:`` and ``-protocol_whitelist file``. Media can contain HLS/RTMP playlist
    references; the restriction stops ffmpeg from following them with network requests.
    """
    return ["-protocol_whitelist", "file", "-i", f"file:{path}"]


def log_ffmpeg_status() -> FfmpegStatus:
    """Run and log the self-check during service startup."""
    status = ffmpeg_status()
    if status.available:
        logger.info("随包 ffmpeg 可用：%s（%s）", status.version, status.executable)
    else:
        logger.warning("随包 ffmpeg 不可用：%s", status.unavailable_reason)
    return status


def reset_for_tests() -> None:
    """Clear the process-wide self-check cache for tests."""
    ffmpeg_status.cache_clear()
