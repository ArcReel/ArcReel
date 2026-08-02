"""音频工具：上传校验用的时长探测。"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import tempfile
from pathlib import Path

from lib.path_safety import safe_resolve

logger = logging.getLogger(__name__)

# 角色参考音频约束（上传与 TTS 生成样本同口径）：wav/mp3、2-10 秒、≤15MB。
# 出处见 lib/audio_backends/dashscope.py 与 server/routers/files.py 引用处。
AUDIO_REFERENCE_MAX_BYTES = 15 * 1024 * 1024
AUDIO_REFERENCE_MIN_SECONDS = 2.0
AUDIO_REFERENCE_MAX_SECONDS = 10.0

_FFPROBE_TIMEOUT_SECONDS = 10.0

# ffprobe 的 format_name 是逗号分隔的候选容器列表（如 m4a 探测出
# "mov,mp4,m4a,3gp,3g2,mj2"），按扩展名要求其中必须含指定 token，
# 防止「有音轨但容器不是 wav/mp3」的文件（如把 m4a 改名为 .wav）蒙混过关。
_CONTAINER_FORMAT_TOKENS = {
    ".wav": {"wav"},
    ".mp3": {"mp3"},
}


def resolve_audio_ref_path(project_dir: Path, audio_refs_dir: Path, rel_path: str | None) -> Path | None:
    """解析 reference_audio 字段值，仅当其确实落在 characters/refs_audio 内才返回。

    该字段可经资产 PATCH 被写成项目内任意字符串（extra_string_fields 只做类型校验），
    单靠 safe_resolve 只保证不越界出项目目录，还不足以防止被诱导删除 project.json
    等项目内其它文件，故额外校验父目录命中 refs_audio。上传替换（files.py）与 TTS
    生成样本确认落盘（generate.py）共用同一份判定。
    """
    resolved = safe_resolve(project_dir, rel_path)
    if resolved is None:
        return None
    if os.path.realpath(resolved.parent) != os.path.realpath(audio_refs_dir):
        return None
    return resolved


def resolve_stale_reference_audio(
    project_dir: Path, audio_refs_dir: Path, old_audio: str | None, new_path: Path
) -> Path | None:
    """替换角色参考音频时，识别出「换掉后就没有指针指向」的旧文件。

    音频不像参考图强制统一扩展名，替换时新旧扩展名可能不同，旧文件需显式清理避免孤儿。
    返回 None 表示无需清理：旧指针为空、指向 refs_audio 之外（见
    :func:`resolve_audio_ref_path`），或大小写不敏感文件系统上旧指针与新文件名只是大小写
    不同却指向同一 inode（如 ``Alice.WAV`` 与 ``Alice.wav``）——此时新内容即将原地覆盖该
    文件，不能再当孤儿删掉，否则会把刚写入的新样本一并删除。
    """
    if not isinstance(old_audio, str) or not old_audio:
        return None
    resolved_old = resolve_audio_ref_path(project_dir, audio_refs_dir, old_audio)
    if resolved_old is None:
        return None
    if new_path.exists() and resolved_old.samefile(new_path):
        return None
    return resolved_old


def discard_stale_reference_audio(stale_path: Path | None) -> None:
    """删除已无指针指向的旧参考音频；删除失败只告警不抛。

    调用点必须在新文件已落盘且角色字段已指向它之后——此时删旧文件才不会留下「字段指向
    已删文件」的中间态。物理删除失败（权限/IO 错误，含 Windows 文件占用）不应让本次替换
    报错：新文件与字段都已成功提交，此时再抛异常会让调用方误以为整次替换失败并重试，而
    重试时旧指针已指向新文件，旧文件反而成为找不到指针的孤儿。
    """
    if stale_path is None:
        return
    try:
        stale_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("旧参考音频物理删除失败，可能残留孤儿文件：%s", stale_path, exc_info=True)


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
        ValueError: ffprobe 可用但无法解出时长、超时、容器内没有音频流
            （如把视频文件改名为 .wav/.mp3 上传），或探测出的容器格式与
            扩展名不符（如把 m4a/aac 改名为 .wav 上传）。
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

        expected_tokens = _CONTAINER_FORMAT_TOKENS.get(suffix.lower())
        if expected_tokens is not None:
            format_name_out = await _run_ffprobe(
                ["-show_entries", "format=format_name", "-of", "csv=p=0", str(tmp_path)]
            )
            detected_tokens = {token.strip() for token in format_name_out.decode().strip().split(",")}
            if not detected_tokens & expected_tokens:
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


async def _probe_existing_audio_duration_seconds(path: Path) -> float | None:
    """探测磁盘上已落盘参考音频文件的时长（秒）。

    与 :func:`probe_audio_duration_seconds` 的字节输入版本不同：本函数直接对已存在文件探测，
    不写临时文件、不做容器/音轨校验——这些文件已在上传期完成过格式校验，此处只需要时长数字。
    ffprobe 不可用或探测失败时返回 None，与仓库既有降级口径一致（跳过校验，不阻断）。
    """
    if not _ffprobe_available():
        return None
    try:
        duration_out = await _run_ffprobe(["-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    except (FileNotFoundError, OSError, ValueError):
        return None
    try:
        return float(duration_out.decode().strip())
    except ValueError:
        return None


async def probe_reference_audio_total_seconds(paths: list[Path]) -> float | None:
    """探测多段参考音频文件的总时长（秒），供请求期总时长能力校验使用。

    任一文件时长探测失败（ffprobe 不可用、文件损坏）都返回 None 而非部分求和：半截总时长会
    让调用方误判「未超限」而放行本该拦截的请求，比跳过校验更危险。
    """
    total = 0.0
    for path in paths:
        duration = await _probe_existing_audio_duration_seconds(path)
        if duration is None:
            return None
        total += duration
    return total
