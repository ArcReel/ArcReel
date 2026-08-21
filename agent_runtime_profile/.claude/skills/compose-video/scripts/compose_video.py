#!/usr/bin/env python3
"""
Video Composer - 使用 ffmpeg 合成最终视频

Usage:
    python compose_video.py <script_file> [--output OUTPUT] [--music MUSIC_FILE] [--subtitles]

Example:
    python compose_video.py chapter_01_script.json --output chapter_01_final.mp4
    python compose_video.py chapter_01_script.json --music bgm.mp3
    python compose_video.py chapter_01_script.json --subtitles
"""

import argparse
import functools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# Windows 控制台/管道默认 cp936，emoji 输出会抛 UnicodeEncodeError 打断报错；
# 统一把 stdout/stderr 切成 UTF-8（agent 捕获的是字节流，UTF-8 无信息损失）。
# reconfigure 本身在异常流上也可能抛错，失败时静默跳过，不让日志输出成为启动障碍。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _find_repo_root(start: Path) -> Path:
    """向上回溯定位含 pyproject.toml 的目录，覆盖源/物化/editable 三种部署形态。"""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(
        f"无法从 {start} 向上找到 pyproject.toml。"
        "请确认脚本位于 ArcReel 仓库内（源 profile 或物化版 .claude 目录都可）。"
    )


# sys.path 注入必须在 `from lib...` 之前完成，因此只能在 module 顶层执行。
PROJECT_ROOT = _find_repo_root(Path(__file__).resolve())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.project_manager import ProjectManager
from lib.reference_video.shot_parser import match_dialogue_line, match_voiceover_line
from lib.script_models import get_generated_assets
from lib.speech_rate import estimate_spoken_seconds

FFMPEG_TOOLS_HINT = "需要 ffmpeg 和 ffprobe 同时可用（脚本会自动查找 PATH 与常见安装位置）"

# Windows 上 ffmpeg/ffprobe 可能只存在于 git bash（MSYS2）的 PATH 里、或装在非 PATH
# 目录：git bash 非登录 shell 不读 ~/.bashrc，Python subprocess 也拿不到 bash 内
# POSIX 路径转换后的目录，所以除 shutil.which 外再探测常见安装位置。
_WINDOWS_TOOL_CANDIDATE_DIRS: tuple[Path, ...] = tuple(
    Path(p)
    for p in (
        r"C:\Program Files\Git\mingw64\bin",
        r"C:\Program Files\Git\usr\bin",
        r"C:\msys64\mingw64\bin",
        r"C:\msys64\usr\bin",
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
    )
)


def _find_tool(name: str) -> Path | None:
    """在 PATH 与 Windows 常见安装位置定位 ffmpeg/ffprobe，找不到返回 None。"""
    found = shutil.which(name)
    if found is not None:
        return Path(found)
    if os.name != "nt":
        return None
    for directory in _WINDOWS_TOOL_CANDIDATE_DIRS:
        candidate = directory / f"{name}.exe"
        if candidate.is_file():
            return candidate
    # 用户级安装（%LOCALAPPDATA%\ffmpeg\bin）——无需管理员权限的常见位置
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "ffmpeg" / "bin" / f"{name}.exe"
        if candidate.is_file():
            return candidate
    # winget 安装的 Gyan.FFmpeg 落在 %LOCALAPPDATA%\Microsoft\WinGet\Packages\...
    winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget_root.is_dir():
        for package_dir in winget_root.glob(f"*{name}*"):
            for bin_dir in package_dir.glob("*/bin"):
                candidate = bin_dir / f"{name}.exe"
                if candidate.is_file():
                    return candidate
    return None


@functools.cache
def _resolve_ffmpeg_tools() -> tuple[Path | None, Path | None]:
    """一次性解析 ffmpeg/ffprobe，之后所有 subprocess 调用都用解析出的绝对路径。"""
    return _find_tool("ffmpeg"), _find_tool("ffprobe")


def _require_tool(name: str) -> str:
    """返回已解析的 ffmpeg/ffprobe 绝对路径；未解析到时回退裸命令名。

    主流程在构建命令前已由 check_ffmpeg() 把关，真实运行总能拿到绝对路径；
    直接调用各拼接函数（测试）时回退到旧行为，由 subprocess 自行报错。
    """
    ffmpeg, ffprobe = _resolve_ffmpeg_tools()
    path = ffmpeg if name == "ffmpeg" else ffprobe
    return str(path) if path is not None else name


def _require_project_cwd() -> tuple[ProjectManager, str, Path]:
    """cwd 必须含 project.json，否则拒绝执行。

    替代 ProjectManager.from_cwd()：cwd 漂离项目目录时显式报错，
    而不是悄悄拼出错误的项目名继续执行。
    """
    cwd = Path.cwd().resolve()
    if not (cwd / "project.json").is_file():
        raise RuntimeError(f"必须在项目目录内运行（当前 cwd={cwd} 不含 project.json）")
    pm = ProjectManager(str(cwd.parent))
    return pm, cwd.name, cwd


def check_ffmpeg() -> bool:
    """检查 ffmpeg / ffprobe 是否可用（PATH + Windows 常见安装位置）。"""
    ffmpeg, ffprobe = _resolve_ffmpeg_tools()
    return ffmpeg is not None and ffprobe is not None


def _check_subtitles_support() -> bool:
    """ffmpeg 是否带 libass（subtitles 滤镜可用）。

    官方 / gyan 完整版都带；极小化静态版可能不带。结果缓存，缺失时在 --subtitles
    前置检查里给出明确指引，而不是等滤镜报错。
    """
    result = subprocess.run(
        [_require_tool("ffmpeg"), "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return result.returncode == 0 and "subtitles" in result.stdout


def run_ffmpeg(cmd: list[str], error_prefix: str) -> None:
    """执行 ffmpeg / ffprobe 命令并在失败时抛出完整错误。"""
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"{error_prefix}: {result.stderr}")


def _resolve_fps(avg_frame_rate: object, r_frame_rate: object) -> str:
    """从 ffprobe 字段解析 fps。

    `avg_frame_rate="0/0"` 是常见的伪真值（部分 GIF→MP4 转换 / 屏幕录制软件输出），
    若用 `or` 链 fallback，下游 ffmpeg 滤镜会被喂入 `"0/0"` 直接失败。
    这里显式黑名单 `{"0/0","0",""}`，优先 avg，再 r，最后回退 `"30"`。
    """
    for value in (avg_frame_rate, r_frame_rate):
        if value is None:
            continue
        candidate = str(value).strip()
        if candidate in {"0/0", "0", ""}:
            continue
        return candidate
    return "30"


def _coerce_numeric_duration(raw: object) -> float | None:
    """把 ffprobe 的 duration 字段安全转成 float，无效值返回 None。

    部分 webm/流式封装会让 `stream.duration="N/A"`（真值字符串，`or` 无法回退），
    或返回空串 / 非数值；统一在这里过滤，让调用方走数值有效性而不是真值判断。

    同时拒绝 `nan` / `inf` 和非正数：`float("nan") <= 0.5` 是 `False`，
    会绕过 `_build_xfade_filter_complex` 的短片段降级，把 `nan` 直接传进
    xfade `offset` 参数，ffmpeg 会因此报错。
    """
    if raw is None:
        return None
    candidate = str(raw).strip()
    if not candidate or candidate.upper() == "N/A":
        return None
    try:
        value = float(candidate)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def get_video_duration(video_path: Path) -> float:
    """获取视频时长"""
    result = subprocess.run(
        [
            _require_tool("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe 执行失败。{FFMPEG_TOOLS_HINT}；若环境已满足，再检查输入媒体。原始错误: {result.stderr}"
        )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"无法解析视频时长: {video_path}") from exc


def probe_media(video_path: Path) -> dict[str, object]:
    """读取片段的基础媒体信息，用于统一中间片规格。"""
    result = subprocess.run(
        [
            _require_tool("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe 执行失败。{FFMPEG_TOOLS_HINT}；若环境已满足，再检查输入媒体。原始错误: {result.stderr}"
        )

    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"无法解析 ffprobe 输出: {video_path}") from exc

    streams = payload.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video_stream:
        raise RuntimeError(f"缺少视频流: {video_path}")

    fps = _resolve_fps(video_stream.get("avg_frame_rate"), video_stream.get("r_frame_rate"))

    # duration 优先 video stream（mkv/webm 等容器 format.duration 与 stream.duration
    # 可能相差几毫秒；atrim 静音音轨长度与 xfade offset 需要精确，必须以 stream 为准）。
    # 但 ffprobe 对部分 webm/流式封装会让 stream.duration="N/A"（真值字符串，
    # `or` 链不会回退），所以这里用数值有效性而不是真值判断逐级回退。
    duration = _coerce_numeric_duration(video_stream.get("duration"))
    if duration is None:
        duration = _coerce_numeric_duration(payload.get("format", {}).get("duration"))
    if duration is None:
        raise RuntimeError(f"无法从 ffprobe 输出中获取时长: {video_path}")

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"无法解析视频分辨率: {video_path}")

    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "has_audio": audio_stream is not None,
    }


# 字幕音频对齐（--subtitles 默认开启，--no-audio-align 关闭）：
# seedance 生成视频自带对白与氛围音（实测均值约 -21dB），纯按语速估算的时间轴会与
# 实际语音错位（长台词常溢出场景、人未开口字幕先出）。silencedetect 在 -25dB / 0.3s
# 阈值下能切出句间停顿而不会被氛围音淹没。
_SUBTITLE_SILENCE_DB = -25
_SUBTITLE_MIN_SILENCE = 0.3
_SUBTITLE_LEAD_MIN = 0.3  # 开场静音超过该值才把字幕整体后移（秒）
_SUBTITLE_GAP = 0.25  # 相邻字幕条间留白（秒），避免贴边连读
_SUBTITLE_MIN_DISPLAY = 0.3  # 单条字幕最短展示时长（秒）

_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")
_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _parse_silencedetect_output(stderr: str, duration: float) -> list[tuple[float, float]]:
    """把 silencedetect 的 stderr 解析为有声区间列表（升序、互不重叠、末尾截断到 duration）。

    silence_start / silence_end 交替出现（开头静音的起点是 0）；由静音区间反推
    有声区间：第一个静音开始前、每个静音结束后到下一段静音开始、最后一段静音之后。
    ffmpeg 可能只报 start 不报 end（流在静音中结束），未配对 start 按解析失败处理，
    整体视为有声。未检测到任何静音返回 [(0, duration)]。
    """
    silences: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in stderr.splitlines():
        m = _SILENCE_START_RE.search(line)
        if m:
            open_start = float(m.group(1))
            continue
        m = _SILENCE_END_RE.search(line)
        if m and open_start is not None:
            end = float(m.group(1))
            if end > open_start:
                silences.append((open_start, end))
            open_start = None

    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        start = max(start, cursor)
        end = max(end, start)
        if start > cursor + 1e-9:
            segments.append((cursor, start))
        cursor = end
    if cursor < duration - 1e-9:
        segments.append((cursor, duration))
    return segments


def _detect_voiced_segments(video_path: Path) -> tuple[list[tuple[float, float]], float | None]:
    """对片段跑一次 silencedetect，返回 (有声区间列表, 片段时长)。

    时长从同一份 stderr 的 Duration 行解析，避免额外 probe；ffmpeg 失败（无音频流 /
    滤镜不可用）或解析不出时长时返回 ([], None)，由调用方决定回退策略。
    """
    result = subprocess.run(
        [
            _require_tool("ffmpeg"),
            "-hide_banner",
            "-i",
            str(video_path.resolve()),
            "-af",
            f"silencedetect=noise={_SUBTITLE_SILENCE_DB:.0f}dB:d={_SUBTITLE_MIN_SILENCE}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        return [], None
    m = _FFMPEG_DURATION_RE.search(result.stderr)
    if m is None:
        return [], None
    hours, minutes, seconds = (float(g) for g in m.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    return _parse_silencedetect_output(result.stderr, duration), duration


# 语音识别字幕对齐（faster-whisper，本地 CPU 推理）：
# seedance 视频自带对白，silencedetect 只能切有声区间、拿不到「哪句话何时开口」。
# faster-whisper 可用时用 word-level 时间戳把剧本 utterances 逐条配对到真实语音边界
# （按文本相似度贪心配对，未命中回退按估算时长摆放）；模型缺失 / 下载失败 / 转写
# 无结果时自动回退到 silencedetect 对齐，绝不中断合成。
_ASR_MODEL_SIZE = "base"  # tiny/base/small/medium；中文对白 base 在 CPU int8 下性价比最高
_ASR_MATCH_THRESHOLD = 0.25  # 相似度低于该值视为配对失败，走估算回退
_ASR_LEAD_PAD = 0.10  # 配对成功后字幕比语音稍早出现（秒）
_ASR_TAIL_PAD = 0.15  # 配对成功后字幕比语音稍晚消失（秒）
_ASR_MIN_DISPLAY = 0.3  # 单条字幕最短展示时长（秒）

_ASR_MODEL: Any | None = None
_ASR_MODEL_ERROR: Exception | None = None


def _asr_load_model() -> Any | None:
    """加载 faster-whisper 模型（进程内缓存）；不可用返回 None 并记录原因。

    首次加载会下载模型（base 约 140MB），下载失败 / 未安装依赖均返回 None，
    由调用方回退到 silencedetect，不抛出异常。
    """
    global _ASR_MODEL, _ASR_MODEL_ERROR
    if _ASR_MODEL is not None:
        return _ASR_MODEL
    if _ASR_MODEL_ERROR is not None:
        return None
    if _ASR_MODEL is None and _ASR_MODEL_ERROR is None:
        print("🗣️ 首次使用将加载 faster-whisper base 模型（约 140MB，仅下载一次）...")
    try:
        from faster_whisper import WhisperModel  # pyright: ignore[reportMissingImports]
    except Exception as exc:  # 未安装 / 平台无 wheel
        _ASR_MODEL_ERROR = exc
        return None
    try:
        _ASR_MODEL = WhisperModel(_ASR_MODEL_SIZE, device="cpu", compute_type="int8")
    except Exception as exc:  # 模型下载失败 / 网络不可达
        _ASR_MODEL_ERROR = exc
        return None
    return _ASR_MODEL


def _extract_audio_for_asr(video_path: Path, wav_path: Path) -> None:
    """抽取单声道 16k PCM 供 whisper 转写；ffmpeg 失败抛异常，由调用方回退。"""
    run_ffmpeg(
        [
            _require_tool("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(video_path.resolve()),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(wav_path),
        ],
        "ffmpeg 抽取对白音频失败",
    )


def _asr_word_timestamps(video_path: Path, language: str | None) -> list[tuple[float, float, str]]:
    """对片段做本地语音识别，返回按时间升序的 (start, end, word)。

    faster-whisper 的 word_timestamps 对中文按字输出时间戳。任一步骤失败返回空列表
    （模型不可用 / 音频抽取失败 / 转写异常），由调用方决定回退策略。
    """
    model = _asr_load_model()
    if model is None:
        return []
    with tempfile.TemporaryDirectory(prefix="arcreel_asr_") as tmp:
        wav_path = Path(tmp) / "audio.wav"
        try:
            _extract_audio_for_asr(video_path, wav_path)
        except Exception:
            return []
        # vad_filter 需要 onnxruntime；缺失时退化到不带 VAD 再试一次
        for vad in (True, False):
            try:
                segments, _info = model.transcribe(
                    str(wav_path),
                    language=language if language in ("zh", "en", "ja", "ko") else None,
                    word_timestamps=True,
                    vad_filter=vad,
                )
                words: list[tuple[float, float, str]] = []
                for segment in segments:
                    for word in segment.words or []:
                        start = float(getattr(word, "start", 0.0))
                        end = float(getattr(word, "end", start))
                        token = str(getattr(word, "word", "")).strip()
                        if token and end > start:
                            words.append((start, end, token))
                if words:
                    return words
            except Exception:
                continue
    return []


def _norm_for_match(text: str) -> str:
    """归一化台词文本：去空白与标点，只留中英文与数字，供相似度配对。"""
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _find_best_word_window(
    target: str, words: list[tuple[float, float, str]], start_idx: int
) -> tuple[tuple[float, float, int] | None, float]:
    """在 words[start_idx:] 里贪心找与 target 最相似的连续窗口。

    返回 (窗口 (start, end, 窗口后一位下标), 相似度)。窗口越长长度惩罚越大，
    避免把整段语音都吞进一条字幕。
    """
    target_n = _norm_for_match(target)
    if not target_n or start_idx >= len(words):
        return None, 0.0
    best: tuple[float, float, int] | None = None
    best_score = 0.0
    max_len = min(len(words) - start_idx, max(8, len(target_n) + 6))
    for end_idx in range(start_idx + 1, start_idx + max_len + 1):
        window = words[start_idx:end_idx]
        text_n = _norm_for_match("".join(w[2] for w in window))
        if not text_n:
            continue
        ratio = SequenceMatcher(None, target_n, text_n).ratio()
        length_penalty = 1.0 - 0.4 * abs(len(target_n) - len(text_n)) / max(len(target_n), len(text_n), 1)
        score = ratio * length_penalty
        if score > best_score:
            best_score = score
            best = (window[0][0], window[-1][1], end_idx)
    return best, best_score


def _align_subtitle_spans_with_asr(
    spans: list[dict[str, object]],
    words: list[tuple[float, float, str]],
    video_duration: float,
) -> list[dict[str, object]]:
    """用 ASR 词级时间戳把字幕逐条配到真实语音边界。

    台词按顺序贪心配对：命中相似度阈值就用识别到的起止时间（前后加小段余量），
    未命中（转写错字严重 / 该句无语音）退回按估算时长从上一句末尾顺延；整体保证
    单调不重叠、不越出场景时长。配对有顺序约束，越过后不回头，防止后面的台词
    把前面句子的时间抢走。
    """
    if not spans or not words:
        return spans
    aligned: list[dict[str, object]] = []
    cursor = 0.0
    idx = 0
    for span in spans:
        text = str(span["text"])
        estimated = float(span["duration_seconds"])
        window, score = _find_best_word_window(text, words, idx)
        if window is not None and score >= _ASR_MATCH_THRESHOLD:
            start, end, end_idx = window
            start = max(start - _ASR_LEAD_PAD, cursor, 0.0)
            end = min(end + _ASR_TAIL_PAD, video_duration)
            if end - start < _ASR_MIN_DISPLAY:
                end = min(start + _ASR_MIN_DISPLAY, video_duration)
            aligned.append({"offset_seconds": start, "duration_seconds": max(end - start, 0.0), "text": text})
            cursor = end
            idx = end_idx
        else:
            start = max(cursor, 0.0)
            duration = min(max(estimated, _ASR_MIN_DISPLAY), max(video_duration - start, 0.0))
            aligned.append({"offset_seconds": start, "duration_seconds": duration, "text": text})
            cursor = start + duration
    return aligned


def _align_subtitle_spans_to_audio(
    spans: list[dict[str, object]],
    video_duration: float,
    voiced_segments: list[tuple[float, float]],
) -> list[dict[str, object]]:
    """把估算字幕时间轴对齐到片段的真实音频结构。

    - 开场静音：第一个有声区间起点晚于 _SUBTITLE_LEAD_MIN 时整体后移，避免
      「人还没开口字幕先出」
    - 溢出缩放：估算总时长超过可用时长（seedance 实际语速快于估算语速，长台词常
      溢出场景）时按比例压缩，保证每条字幕都落在场景内
    - 条间留白：除最后一条外，每条结尾让出 _SUBTITLE_GAP 秒，避免字幕贴边连读
    - 检测失败（voiced_segments 为空）退化为按场景时长缩放，仍优于纯估算
    """
    if not spans or video_duration <= 0:
        return spans
    lead = 0.0
    if voiced_segments:
        first_start = voiced_segments[0][0]
        if first_start >= _SUBTITLE_LEAD_MIN:
            lead = first_start
    available = max(video_duration - lead, 0.0)
    total_est = sum(float(span["duration_seconds"]) for span in spans)
    if total_est <= 0:
        return spans
    scale = min(1.0, available / total_est)
    aligned: list[dict[str, object]] = []
    offset = lead
    for index, span in enumerate(spans):
        scaled = float(span["duration_seconds"]) * scale
        if index == len(spans) - 1:
            # 末条不扣留白：展示到语音/场景结束，只钳到场景时长
            display = max(0.0, min(scaled, video_duration - offset))
        else:
            display = max(scaled - _SUBTITLE_GAP, min(scaled, _SUBTITLE_MIN_DISPLAY))
        aligned.append(
            {
                "offset_seconds": offset,
                "duration_seconds": display,
                "text": span["text"],
            }
        )
        offset += scaled
    return aligned


def _subtitle_spans_from_utterances(utterances: object, language: str | None) -> list[dict[str, object]]:
    """从 drama 场景的有序 utterances 派生字幕时间片（与剪映导出同口径）。

    dialogue 与 voiceover 都出字幕、按顺序摆放；每条时长按 ``estimate_spoken_seconds``
    估算、offset 在场景内累加；空 / 纯空白 text、非 dict 条目、估时长为 0（纯标点）跳过
    且不占 offset。此处不做场景时长拉伸，时间轴对齐由 ``_align_subtitle_spans_to_audio``
    在拿到片段实际时长与有声区间后统一处理。
    """
    spans: list[dict[str, object]] = []
    offset = 0.0
    for utterance in utterances if isinstance(utterances, list) else []:
        if not isinstance(utterance, dict):
            continue
        text = utterance.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        duration = estimate_spoken_seconds(text, language)
        if duration <= 0:
            continue
        spans.append({"offset_seconds": offset, "duration_seconds": duration, "text": text})
        offset += duration
    return spans


def _subtitle_spans_from_unit_shots(shots: object, language: str | None) -> list[dict[str, object]]:
    """从 reference_video unit 的 ``shots[*].text`` 提取台词/画外音生成字幕时间片。

    与剪映导出同口径（``lib.reference_video.shot_parser``）：整行匹配
    ``@[角色]：{台词}``（对话）或 ``{台词}``（画外音），镜头描述行不烧字幕。
    offset 在 unit 内累计，时长按 ``estimate_spoken_seconds`` 估算；空文本 /
    估时长为 0 的条目跳过且不占 offset（与 ``_subtitle_spans_from_utterances`` 一致）。
    """
    spans: list[dict[str, object]] = []
    offset = 0.0
    for shot in shots if isinstance(shots, list) else []:
        if not isinstance(shot, dict):
            continue
        text = shot.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        for line in text.splitlines():
            dialogue = match_dialogue_line(line)
            if dialogue is not None:
                spoken = dialogue[1]
            else:
                spoken = match_voiceover_line(line)
            if not isinstance(spoken, str) or not spoken.strip():
                continue
            duration = estimate_spoken_seconds(spoken, language)
            if duration <= 0:
                continue
            spans.append({"offset_seconds": offset, "duration_seconds": duration, "text": spoken})
            offset += duration
    return spans


def _format_srt_timestamp(seconds: float) -> str:
    """秒 → SRT 时间戳（00:00:00,000）；负值钳到 0。"""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _subtitle_font_name() -> str:
    """按平台挑选中文字体；libass 找不到该字体时会自动回退，不影响渲染。"""
    if os.name == "nt":
        return "Microsoft YaHei"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


def _subtitle_char_units(ch: str) -> float:
    """字符显示宽度单位：东亚全角字符算 1，其余按 0.6 折算。"""
    return 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.6


def _wrap_subtitle_text(text: str, max_units: float) -> str:
    """按显示宽度把长台词切成 \\N 硬换行，避免 libass 自动折行溢出画面。

    每行都硬性不超过 max_units：超长台词宁可多拆几行，也不让任何一行横向
    溢出画面（行数上限由 _render_ass 按最长台词缩字号保证）。空文本原样返回。
    """
    text = text.strip()
    if not text:
        return text
    lines: list[str] = []
    current = ""
    current_units = 0.0
    for ch in text:
        units = _subtitle_char_units(ch)
        if current and current_units + units > max_units:
            lines.append(current)
            current = ch
            current_units = units
        else:
            current += ch
            current_units += units
    if current:
        lines.append(current)
    return "\\N".join(lines)


def _escape_ass_text(text: str) -> str:
    """转义 ASS 文本里的花括号与反斜杠（防止被当成覆盖标签）。"""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _format_ass_timestamp(seconds: float) -> str:
    """秒 → ASS 时间戳（H:MM:SS.cc 百分秒）；负值钳到 0。"""
    total_cs = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _render_ass(spans: list[dict[str, object]], target_width: int, target_height: int) -> str:
    """把字幕时间片渲染为带显式样式的 UTF-8 ASS 文本。

    相比 SRT 的 libass 默认样式（字号/边距不可控，长台词会被压成贴边横幅），ASS
    直接声明 PlayResX/Y：字号、底部边距、描边都以目标分辨率的像素为准，配合 \\N
    硬换行保证字幕始终落在画面安全区内。字号按画布高度 6% 取整，边距按宽/高
    5.5% 取整，均钳到合理范围。
    """
    margin_lr = min(max(round(target_width * 0.055), 16), 80)
    margin_v = min(max(round(target_height * 0.055), 24), 140)
    max_lines = 5
    # 描边(2px)*2 + 阴影(1px) 的横向占位，按全角字宽=font_size 保守估算每行字数
    outline_budget = 8
    longest_units = max(
        (sum(_subtitle_char_units(ch) for ch in str(span["text"])) for span in spans),
        default=0.0,
    )

    def units_per_line(font_size: int) -> float:
        return max(4.0, (target_width - 2 * margin_lr - outline_budget) / font_size)

    # 字号自适应：最长台词放不进 max_lines 行时逐档缩小，保证不横向溢出
    font_size = min(max(round(target_height * 0.06), 20), 64)
    while longest_units > 0 and font_size > 20 and math.ceil(longest_units / units_per_line(font_size)) > max_lines:
        font_size -= 2
    max_units = units_per_line(font_size)
    blocks = []
    for span in spans:
        start = float(span["offset_seconds"])
        end = start + float(span["duration_seconds"])
        text = _wrap_subtitle_text(_escape_ass_text(str(span["text"])), max_units)
        blocks.append(f"Dialogue: 0,{_format_ass_timestamp(start)},{_format_ass_timestamp(end)},Default,,0,0,0,,{text}")
    events = "\n".join(blocks)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {target_width}\n"
        f"PlayResY: {target_height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{_subtitle_font_name()},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,"
        f"1,0,0,0,100,100,0,0,1,2,1,2,{margin_lr},{margin_lr},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"{events}\n"
    )


def _render_srt(spans: list[dict[str, object]]) -> str:
    """把字幕时间片渲染为 UTF-8 SRT 文本（供 subtitles 滤镜读取）。"""
    blocks = []
    for index, span in enumerate(spans, start=1):
        start = float(span["offset_seconds"])
        end = start + float(span["duration_seconds"])
        text = str(span["text"])
        blocks.append(f"{index}\n{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _escape_subtitles_filter_path(path: Path) -> str:
    """把字幕文件路径转成 subtitles 滤镜可用的 filename 值。

    实测（ffmpeg 6.x Gyan build + libass）：反斜杠换正斜杠、冒号前加反斜杠
    （否则盘符冒号会被当作选项分隔符），整个值用单引号包裹以保护空格 / 逗号；
    引号内不能再反斜杠转义逗号（实测反斜杠逗号会原样传给 libass 导致文件打不开）。
    """
    value = str(path.resolve()).replace("\\", "/").replace(":", "\\:")
    if "'" in value:
        raise ValueError(f"字幕文件路径含单引号，无法安全传给 subtitles 滤镜: {value}")
    return f"filename='{value}'"


def normalize_clip(
    video_path: Path,
    output_path: Path,
    *,
    target_width: int,
    target_height: int,
    target_fps: str,
    subtitles_path: Path | None = None,
) -> None:
    """先把单个片段重编码为统一中间片，再做最终拼接。"""
    media = probe_media(video_path)
    # 进入拼接链路的每个中间片都要把音视频轨归零，避免后续 concat / 转场继续放大时间戳偏移。
    video_filter = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={target_fps},format=yuv420p,setpts=PTS-STARTPTS"
    )

    if subtitles_path is not None:
        # 字幕在统一画布上烧录：放滤镜链尾，时间轴相对本片段（setpts 已归零），
        # 之后 concat / 转场不再重编码视频，字幕天然跟着所属场景走
        video_filter += f",subtitles={_escape_subtitles_filter_path(subtitles_path)}"

    if media["has_audio"]:
        filter_complex = (
            f"[0:v]{video_filter}[vout];[0:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[aout]"
        )
        cmd = [
            _require_tool("ffmpeg"),
            "-y",
            "-i",
            str(video_path.resolve()),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_path),
        ]
    else:
        filter_complex = (
            f"[0:v]{video_filter}[vout];[1:a]atrim=duration={float(media['duration']):.6f},asetpts=PTS-STARTPTS[aout]"
        )
        cmd = [
            _require_tool("ffmpeg"),
            "-y",
            "-i",
            str(video_path.resolve()),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_path),
        ]

    run_ffmpeg(cmd, "ffmpeg 规范化片段失败")


def normalize_clips(
    video_paths: list[Path],
    temp_dir: Path,
    subtitle_spanss: list[list[dict[str, object]]] | None = None,
) -> list[Path]:
    """将全部片段统一成可安全拼接的中间片。"""
    first = probe_media(video_paths[0])
    target_width = int(first["width"])
    target_height = int(first["height"])
    target_fps = str(first["fps"])

    normalized_paths: list[Path] = []
    for index, path in enumerate(video_paths):
        normalized_path = temp_dir / f"normalized_{index:03d}.mp4"
        subtitles_path = None
        if subtitle_spanss is not None and index < len(subtitle_spanss) and subtitle_spanss[index]:
            ass_path = temp_dir / f"subtitles_{index:03d}.ass"
            ass_path.write_text(_render_ass(subtitle_spanss[index], target_width, target_height), encoding="utf-8")
            subtitles_path = ass_path
        normalize_clip(
            path,
            normalized_path,
            target_width=target_width,
            target_height=target_height,
            target_fps=target_fps,
            subtitles_path=subtitles_path,
        )
        normalized_paths.append(normalized_path)
    return normalized_paths


def concatenate_final(video_paths: list[Path], output_path: Path):
    """对统一规格的中间片做最终拼接，并确保视频轨从 0 开始。"""
    if not video_paths:
        raise ValueError("没有可用的视频片段")

    if len(video_paths) == 1:
        # 单段直接 remux：concat filter 要求 n>=2，否则 ffmpeg 报参数错误；
        # 中间片在 normalize_clip 内已统一编码 + setpts 归零，这里只需补 +faststart
        run_ffmpeg(
            [
                _require_tool("ffmpeg"),
                "-y",
                "-i",
                str(video_paths[0].resolve()),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            "ffmpeg 单段最终输出失败",
        )
        return

    inputs: list[str] = []
    filter_inputs: list[str] = []
    for index, path in enumerate(video_paths):
        inputs.extend(["-i", str(path.resolve())])
        filter_inputs.append(f"[{index}:v][{index}:a]")

    # 仅让中间片归零还不够；最终成片如果不是从 0 开始，QuickTime 停在 0.00s 仍会先黑一下。
    # concat demuxer + stream copy 会让最终视频轨保留正的 start_time，
    # QuickTime 停在 0.00s 时会先显示黑屏；这里对统一中间片做一次最终编码，
    # 让音视频轨都从 0 开始。
    filter_complex = "".join(filter_inputs) + f"concat=n={len(video_paths)}:v=1:a=1[vout][aout]"
    run_ffmpeg(
        [
            _require_tool("ffmpeg"),
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        "ffmpeg 拼接失败",
    )


def concatenate_simple(
    video_paths: list,
    output_path: Path,
    subtitle_spanss: list[list[dict[str, object]]] | None = None,
):
    """
    无转场拼接

    先把片段规范化为统一的 H.264/AAC 中间片，再做最终拼接，
    避免直接 copy 原始码流时的关键帧 / 时间戳边界问题。
    """
    with tempfile.TemporaryDirectory(prefix="compose-video-") as temp_dir:
        normalized_paths = normalize_clips(video_paths, Path(temp_dir), subtitle_spanss=subtitle_spanss)
        concatenate_final(normalized_paths, output_path)


_XFADE_TYPE_MAP: dict[str, str] = {
    "fade": "fade",
    "dissolve": "dissolve",
    "wipe": "wipeleft",
}


def _build_xfade_filter_complex(
    durations: list[float],
    transitions: list[str],
    transition_duration: float,
) -> str | None:
    """按 cut 边界把片段切成 group，组内 xfade + acrossfade，组间 concat 串联。

    - 单段或全 cut 序列：返回 None，由调用方走 concatenate_final 的纯 concat 路径
    - 短片段（duration <= transition_duration）所触边界自动降级为 cut，避免 xfade
      offset 为负
    - video 走 xfade chain、audio 走 acrossfade chain，两者每个边界都消耗
      transition_duration 秒，组内总时长一致 → 音画同步
    - 组间用 concat=v=1:a=1 串联，避开"全局 xfade offset 在 cut 边界累加错位"
    """
    n = len(durations)
    if n < 2:
        return None

    # 计算每个边界的有效转场类型（None 表示走 cut）
    boundary_xfade: list[str | None] = []
    for i in range(n - 1):
        transition = transitions[i] if i < len(transitions) else "fade"
        if transition == "cut":
            boundary_xfade.append(None)
            continue
        xfade = _XFADE_TYPE_MAP.get(transition, "fade")
        if durations[i] <= transition_duration or durations[i + 1] <= transition_duration:
            boundary_xfade.append(None)
            continue
        boundary_xfade.append(xfade)

    # 中段双侧 xfade 守卫：相邻 xfade 让中段同时承担入场 + 出场两个转场，合计需要
    # 2*transition_duration 秒；单边界守卫只看单侧会漏判，导致 xfade 时段交叉。
    # 对两侧都是 xfade 且 duration < 2*td 的中段，降左侧边界为 cut（保留右侧）。
    # 从左向右遍历、原地修改，链式短中段逐个降级；恰好等于 2*td 视为足够不降级。
    for i in range(1, n - 1):
        if (
            boundary_xfade[i - 1] is not None
            and boundary_xfade[i] is not None
            and durations[i] < 2 * transition_duration
        ):
            boundary_xfade[i - 1] = None

    if all(b is None for b in boundary_xfade):
        return None

    # 按 cut 边界把片段索引切成 group（每个 group 内部边界都是 xfade）
    groups: list[list[int]] = []
    current: list[int] = [0]
    for i, b in enumerate(boundary_xfade):
        if b is None:
            groups.append(current)
            current = [i + 1]
        else:
            current.append(i + 1)
    groups.append(current)

    filter_parts: list[str] = []
    group_outputs: list[tuple[str, str]] = []

    for gi, group in enumerate(groups):
        if len(group) == 1:
            idx = group[0]
            group_outputs.append((f"[{idx}:v]", f"[{idx}:a]"))
            continue

        group_durations = [durations[j] for j in group]

        # video xfade chain：offset 在组内累加，索引从 group 起点起算
        prev_v = f"[{group[0]}:v]"
        for k in range(1, len(group)):
            xfade_type = boundary_xfade[group[k] - 1]
            assert xfade_type is not None
            offset = sum(group_durations[:k]) - k * transition_duration
            out_v = f"[g{gi}v]" if k == len(group) - 1 else f"[g{gi}v{k}]"
            filter_parts.append(
                f"{prev_v}[{group[k]}:v]xfade=transition={xfade_type}:"
                f"duration={transition_duration}:offset={offset:.3f}{out_v}"
            )
            prev_v = out_v

        # audio acrossfade chain：与 video xfade 一一对应，每个边界消耗 transition_duration
        prev_a = f"[{group[0]}:a]"
        for k in range(1, len(group)):
            out_a = f"[g{gi}a]" if k == len(group) - 1 else f"[g{gi}a{k}]"
            filter_parts.append(f"{prev_a}[{group[k]}:a]acrossfade=d={transition_duration}:c1=tri:c2=tri{out_a}")
            prev_a = out_a

        group_outputs.append((f"[g{gi}v]", f"[g{gi}a]"))

    if len(group_outputs) == 1:
        v_label, a_label = group_outputs[0]
        filter_parts.append(f"{v_label}null[vout]")
        filter_parts.append(f"{a_label}anull[aout]")
    else:
        concat_inputs = "".join(f"{v}{a}" for v, a in group_outputs)
        filter_parts.append(f"{concat_inputs}concat=n={len(group_outputs)}:v=1:a=1[vout][aout]")

    return ";".join(filter_parts)


def concatenate_with_transitions(
    video_paths: list,
    transitions: list,
    output_path: Path,
    transition_duration: float = 0.5,
    subtitle_spanss: list[list[dict[str, object]]] | None = None,
):
    """
    使用 xfade 滤镜实现场景间转场，cut 边界用 concat 串联以避免滤镜链断裂。
    """
    with tempfile.TemporaryDirectory(prefix="compose-video-") as temp_dir:
        normalized_paths = normalize_clips(video_paths, Path(temp_dir), subtitle_spanss=subtitle_spanss)
        if len(normalized_paths) < 2:
            concatenate_final(normalized_paths, output_path)
            return

        # xfade offset 必须取 video stream 时长：归一化后的 MP4 因 AAC priming /
        # 容器取整，format.duration 可能比 stream.duration 长几毫秒，把它直接当
        # offset 喂给 xfade 会让转场触发时机偏晚，看上去几乎"没淡出"。
        # 复用 probe_media 的 stream-优先 + N/A 回退逻辑，而不是走 get_video_duration（仅 format.duration）。
        durations = [float(probe_media(p)["duration"]) for p in normalized_paths]
        filter_complex = _build_xfade_filter_complex(durations, transitions, transition_duration)

        if filter_complex is None:
            concatenate_final(normalized_paths, output_path)
            return

        inputs: list[str] = []
        for path in normalized_paths:
            inputs.extend(["-i", str(path.resolve())])

        cmd = [
            _require_tool("ffmpeg"),
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        if result.returncode != 0:
            print(f"⚠️  转场效果失败，尝试简单拼接: {result.stderr[:200]}")
            concatenate_final(normalized_paths, output_path)


def add_background_music(video_path: Path, music_path: Path, output_path: Path, music_volume: float = 0.3):
    """
    添加背景音乐

    Args:
        video_path: 视频文件
        music_path: 音乐文件
        output_path: 输出文件
        music_volume: 背景音乐音量 (0-1)
    """
    cmd = [
        _require_tool("ffmpeg"),
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(music_path),
        "-filter_complex",
        f"[1:a]volume={music_volume}[bg];[0:a][bg]amix=inputs=2:duration=first[aout]",
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    if result.returncode != 0:
        raise RuntimeError(f"添加背景音乐失败: {result.stderr}")


def _project_source_language(pm: ProjectManager, project_name: str) -> str | None:
    """项目源语言，决定字幕语速；缺失回退 None（用默认语速）。"""
    project = pm.load_project(project_name)
    value = project.get("source_language") or (project.get("overview") or {}).get("language")
    return value if isinstance(value, str) and value.strip() else None


def compose_video(
    script_filename: str,
    output_filename: str = None,
    music_path: str = None,
    use_transitions: bool = True,
    subtitles: bool = False,
    audio_align: bool = True,
    asr: bool = True,
) -> Path:
    """
    合成最终视频

    Args:
        script_filename: 剧本文件名
        output_filename: 输出文件名
        music_path: 背景音乐文件路径
        use_transitions: 是否使用转场效果
        subtitles: 是否把台词 / 画外音烧录为字幕（需 ffmpeg 带 libass）
        audio_align: 是否按真实音频对齐字幕时间轴（开场静音偏移 + 溢出缩放；
            检测失败自动回退为按场景时长缩放）
        asr: audio_align 开启时是否先用 faster-whisper 语音识别把每条字幕配到
            真实语音边界；模型不可用 / 转写失败自动回退到 silencedetect 对齐

    Returns:
        输出视频路径
    """
    pm, project_name, project_dir = _require_project_cwd()

    if subtitles and not _check_subtitles_support():
        raise RuntimeError(
            "当前 ffmpeg 未编译 libass，无法烧录字幕（subtitles 滤镜不可用）。"
            "请换用完整版 ffmpeg（如 gyan.dev 的 full/essentials build，或 winget install --id Gyan.FFmpeg -e）"
        )

    # 加载剧本（pm.load_script 内部已用 _safe_subpath 过滤 ../ 等逃逸尝试）
    script = pm.load_script(project_name, script_filename)

    # 剧本顶层骨架：drama 读 scenes[]，reference_video 路线读 video_units[]；
    # 其余（narration/ad）给友好错误。
    if "scenes" in script:
        items = script["scenes"]
        item_kind = "场景"
        item_id_key = "scene_id"
        subtitle_source = "utterances"
    elif "video_units" in script:
        items = script["video_units"]
        item_kind = "视频单元"
        item_id_key = "unit_id"
        subtitle_source = "shots"
    else:
        content_mode = script.get("content_mode") or "unknown"
        actual_keys = [k for k in ("segments", "shots", "video_units") if k in script]
        raise RuntimeError(
            f"compose_video.py 仅支持 drama（顶层 scenes[]）与 reference_video（顶层 video_units[]）；"
            f"当前剧本 content_mode={content_mode}，实际结构含 {actual_keys or ['无法识别']}，"
            "请使用 Web 端剪映草稿导出"
        )

    # 收集视频片段
    video_paths = []
    transitions = []

    for item in items:
        video_clip = get_generated_assets(item).get("video_clip")
        if not video_clip:
            raise ValueError(f"{item_kind} {item.get(item_id_key)} 缺少视频片段")

        # 与 --music / output 同样的围栏：剧本里 video_clip 写成绝对路径或 ../
        # 形式时，未 resolve 的 `project_dir / video_clip` 会落到项目外（且字面
        # 前缀能骗过 is_relative_to），ffmpeg 会真的去读项目外文件
        candidate = Path(video_clip)
        video_path = (candidate if candidate.is_absolute() else project_dir / candidate).resolve()
        if not video_path.is_relative_to(project_dir):
            raise ValueError(f"视频文件必须位于项目目录内，收到: {video_clip}")
        if not video_path.is_file():
            raise FileNotFoundError(f"视频文件不存在或不是普通文件: {video_path}")

        video_paths.append(video_path)
        transitions.append(item.get("transition_to_next", "cut"))

    if not video_paths:
        raise ValueError("没有可用的视频片段")

    print(f"📹 共 {len(video_paths)} 个视频片段")

    # 字幕：按场景 utterances 派生时间片（与剪映导出同口径），与 video_paths 一一对应；
    # 默认再按真实音频对齐时间轴（开场静音偏移 + 溢出缩放），检测失败时按场景时长缩放
    subtitle_spanss = None
    if subtitles:
        language = _project_source_language(pm, project_name)
        subtitle_spanss = []
        asr_scenes = 0
        for item, video_path in zip(items, video_paths):
            if subtitle_source == "shots":
                spans = _subtitle_spans_from_unit_shots(item.get("shots"), language)
            else:
                spans = _subtitle_spans_from_utterances(item.get("utterances"), language)
            if audio_align and spans:
                asr_words: list[tuple[float, float, str]] = []
                if asr:
                    asr_words = _asr_word_timestamps(video_path, language)
                if asr_words:
                    spans = _align_subtitle_spans_with_asr(spans, asr_words, get_video_duration(video_path))
                    asr_scenes += 1
                else:
                    voiced, detected_duration = _detect_voiced_segments(video_path)
                    duration = detected_duration if detected_duration is not None else get_video_duration(video_path)
                    spans = _align_subtitle_spans_to_audio(spans, duration, voiced)
            subtitle_spanss.append(spans)
        non_empty = sum(1 for spans in subtitle_spanss if spans)
        if audio_align:
            if asr and non_empty > 0 and asr_scenes == non_empty:
                align_note = "，已用 faster-whisper 语音识别按真实语音对齐时间轴"
            elif asr:
                align_note = "，已按真实音频对齐时间轴（faster-whisper 不可用，回退 silencedetect）"
            else:
                align_note = "，已按真实音频对齐时间轴"
        else:
            align_note = ""
        print(f"📝 字幕：{non_empty}/{len(video_paths)} 个片段含台词，将烧录到画面底部{align_note}")

    # 确定输出路径：强制落在 project_dir/output/ 内，拒绝 ../ 逃逸
    if output_filename is None:
        chapter = script["novel"].get("chapter", "output").replace(" ", "_")
        output_filename = f"{chapter}_final.mp4"

    # 防御 output/ 软链接绕过：若 `project_dir/output` 本身指向项目外目录，
    # resolve 后的 output_dir 会落到项目外，is_relative_to 校验同样会放行——
    # 与 source/ 对称，这里在 resolve 前显式拒绝。
    output_dir_unresolved = project_dir / "output"
    if output_dir_unresolved.is_symlink():
        raise ValueError(f"output/ 不能是符号链接（避免合成产物落到项目外）: {output_dir_unresolved}")
    output_dir = output_dir_unresolved.resolve()
    output_path = (output_dir / output_filename).resolve()
    if not output_path.is_relative_to(output_dir):
        raise ValueError(f"输出文件名逃逸到 output/ 之外: {output_filename}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # music 路径围栏 + 存在性 fail-fast 前置校验：不要让用户等到视频拼完才发现
    # BGM 路径越界或文件缺失（自动化场景下静默 warning 容易把失败当成功处理）
    music_file: Path | None = None
    if music_path:
        # 相对路径基于 project_dir 解析；绝对路径必须本身在 project_dir 内
        candidate = Path(music_path)
        music_file = (candidate if candidate.is_absolute() else project_dir / music_path).resolve()
        if not music_file.is_relative_to(project_dir):
            raise ValueError(f"BGM 文件必须位于项目目录内，收到: {music_path}")
        if not music_file.is_file():
            raise FileNotFoundError(f"BGM 文件不存在或不是普通文件: {music_file}")

    # 合成视频
    print("🎬 正在合成视频...")

    if use_transitions and any(t != "cut" for t in transitions):
        concatenate_with_transitions(video_paths, transitions, output_path, subtitle_spanss=subtitle_spanss)
    else:
        concatenate_simple(video_paths, output_path, subtitle_spanss=subtitle_spanss)

    print(f"✅ 视频合成完成: {output_path}")

    # 添加背景音乐（存在性已在前置校验保证）
    if music_file is not None:
        print("🎵 正在添加背景音乐...")
        final_output = output_path.with_stem(output_path.stem + "_with_music")
        add_background_music(output_path, music_file, final_output)
        output_path = final_output
        print(f"✅ 背景音乐添加完成: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="合成最终视频")
    parser.add_argument("script", help="剧本文件名")
    parser.add_argument("--output", help="输出文件名")
    parser.add_argument("--music", help="背景音乐文件")
    parser.add_argument("--no-transitions", action="store_true", help="不使用转场效果")
    parser.add_argument("--subtitles", action="store_true", help="把台词/画外音烧录为字幕")
    parser.add_argument("--no-audio-align", action="store_true", help="字幕不按真实音频对齐（仅按语速估算）")
    parser.add_argument(
        "--no-asr", action="store_true", help="不使用 faster-whisper 语音识别对齐字幕（回退 silencedetect）"
    )

    args = parser.parse_args()

    # 检查 ffmpeg / ffprobe
    if not check_ffmpeg():
        print(f"❌ 错误: {FFMPEG_TOOLS_HINT}")
        if os.name == "nt":
            print("   Windows 可执行: winget install --id Gyan.FFmpeg -e")
            print("   或从 https://www.gyan.dev/ffmpeg/builds/ 下载 essentials 版，解压后把 bin 目录加入 PATH")
            print("   安装后请重启 git bash / 终端再试")
        else:
            print("   macOS 可执行: brew install ffmpeg")
            print("   Linux 可执行: sudo apt install ffmpeg")
        print("   安装后请确认 ffmpeg -version 和 ffprobe -version 都能执行")
        sys.exit(1)

    try:
        output_path = compose_video(
            args.script,
            args.output,
            args.music,
            use_transitions=not args.no_transitions,
            subtitles=args.subtitles,
            audio_align=not args.no_audio_align,
            asr=not args.no_asr,
        )

        print(f"\n🎉 最终视频: {output_path}")
        print("   单独片段保留在: videos/")

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
