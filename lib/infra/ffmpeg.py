"""随包 ffmpeg 的唯一查找器。

固定使用 ``imageio-ffmpeg`` wheel 自带的 ffmpeg：不看系统 PATH、不读 ``IMAGEIO_FFMPEG_EXE``、
不提供路径配置，所有环境用同一个构建。仓库内调用 ffmpeg 的地方都经 :func:`ffmpeg_executable`
取用它；它不带 ffprobe，媒体探测见 :mod:`lib.infra.media_probe`。

自检时顺带枚举随包的编码器与过滤器，供渲染链路做组件检查（见 :meth:`FfmpegStatus.component_gaps`）。
枚举失败时组件状态为 ``unknown``：unknown 不是 missing，检测失败既不报缺也不冒充可用。
"""

from __future__ import annotations

import functools
import importlib.resources
import logging
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from imageio_ffmpeg import binaries

logger = logging.getLogger(__name__)

_SELF_CHECK_TIMEOUT_SECONDS = 30.0
_LISTING_TIMEOUT_SECONDS = 30.0

VersionRunner = Callable[[Path], "subprocess.CompletedProcess[bytes]"]
ListingRunner = Callable[[Path, str], "subprocess.CompletedProcess[bytes]"]

# -encoders 行是 6 个标志位加名称（如 " V....D libx264"）；-filters 行是 3 个标志位加名称（如 " TSC aap"）。
# 图例行（如 " V..... = Video"）与分隔线（" ------"）的名称位不是标识符，按标识符正则自然排除。
_ENCODER_NAME_RE = re.compile(r"^\s*[VASFXBD.]{6}\s+([A-Za-z0-9_-]+)\s")
_FILTER_NAME_RE = re.compile(r"^\s*[TVNASZC|.]{3}\s+([A-Za-z0-9_.-]+)\s")


class FfmpegUnavailableError(RuntimeError):
    """随包 ffmpeg 在当前环境不可用；消息即不可用原因。"""


@dataclass(frozen=True, slots=True)
class FfmpegStatus:
    """随包 ffmpeg 的自检结论。``unavailable_reason`` 为 None 即可用。"""

    executable: Path | None
    version: str | None
    unavailable_reason: str | None
    listed_encoders: frozenset[str] | None = None
    """``-encoders`` 枚举出的编码器名称；None 表示枚举失败，各编码器状态未知。"""
    listed_filters: frozenset[str] | None = None
    """``-filters`` 枚举出的过滤器名称；None 表示枚举失败，各过滤器状态未知。"""

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    def component_state(self, name: str, kind: str) -> str:
        """组件三态：available（在枚举中）/ missing（枚举成功但不含）/ unknown（未枚举到）。"""
        listed = self.listed_encoders if kind == "encoder" else self.listed_filters
        if listed is None:
            return "unknown"
        return "available" if name in listed else "missing"

    def component_gaps(self, needed: Mapping[str, Iterable[str]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """对照必需组件清单返回 (missing, unknown)；只有 missing 构成阻塞。"""
        missing: list[str] = []
        unknown: list[str] = []
        for kind, names in needed.items():
            for name in sorted(names):
                state = self.component_state(name, kind)
                if state == "missing":
                    missing.append(name)
                elif state == "unknown":
                    unknown.append(name)
        return tuple(missing), tuple(unknown)


def _run_version(executable: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(executable), "-version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_SELF_CHECK_TIMEOUT_SECONDS,
        check=False,
    )


def _run_listing(executable: Path, flag: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(executable), "-hide_banner", flag],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_LISTING_TIMEOUT_SECONDS,
        check=False,
    )


def _parse_component_names(output: str, line_re: re.Pattern[str]) -> frozenset[str] | None:
    """从 ``-encoders``/``-filters`` 输出提取组件名；一个都解析不出时视为枚举失败。"""
    names = {match.group(1) for line in output.splitlines() if (match := line_re.match(line))}
    return frozenset(names) if names else None


def inspect_bundled_ffmpeg(
    binaries_dir: Path,
    *,
    run_version: VersionRunner = _run_version,
    run_listing: ListingRunner = _run_listing,
) -> FfmpegStatus:
    """在 ``binaries_dir`` 中定位随包 ffmpeg 并运行 ``-version`` 自检。

    imageio-ffmpeg 的平台 wheel 在该目录只放当前平台的一个 ``ffmpeg-*`` 可执行文件；
    sdist 安装或不受支持的平台没有这个文件。自检通过后顺带枚举编码器与过滤器。
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

    def _listed(flag: str, line_re: re.Pattern[str]) -> frozenset[str] | None:
        try:
            listing = run_listing(executable, flag)
        except (OSError, subprocess.SubprocessError):
            return None
        if listing.returncode != 0:
            return None
        return _parse_component_names(listing.stdout.decode(errors="replace"), line_re)

    return FfmpegStatus(
        executable,
        lines[0].strip() if lines else None,
        None,
        listed_encoders=_listed("-encoders", _ENCODER_NAME_RE),
        listed_filters=_listed("-filters", _FILTER_NAME_RE),
    )


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


def local_file_input(path: Path) -> list[str]:
    """把 ``path`` 作为本地文件输入的 ffmpeg 参数。

    输入经 ``file:`` 协议打开并限定 ``-protocol_whitelist file``：媒体文件可能嵌套
    HLS/RTMP 等播放列表引用，限定后 ffmpeg 不跟随其中的协议发起网络请求。
    """
    return ["-protocol_whitelist", "file", "-i", f"file:{path}"]


def log_ffmpeg_status(components: Mapping[str, Iterable[str]] | None = None) -> FfmpegStatus:
    """执行自检并记录结论，供服务启动时调用。

    给出 ``components``（按 kind 的必需组件清单）时，缺失的组件一并列出。
    """
    status = ffmpeg_status()
    if status.available:
        logger.info("随包 ffmpeg 可用：%s（%s）", status.version, status.executable)
        if components:
            missing, _ = status.component_gaps(components)
            if missing:
                logger.warning("随包 ffmpeg 缺少组件：%s", "、".join(missing))
    else:
        logger.warning("随包 ffmpeg 不可用：%s", status.unavailable_reason)
    return status


def reset_for_tests() -> None:
    """test helper —— 清除进程级自检缓存。"""
    ffmpeg_status.cache_clear()
