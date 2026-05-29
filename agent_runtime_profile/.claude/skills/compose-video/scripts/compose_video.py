#!/usr/bin/env python3
"""
Video Composer - 使用 ffmpeg 合成最终视频

Usage:
    python compose_video.py <script_file> [--output OUTPUT] [--music MUSIC_FILE]

Example:
    python compose_video.py chapter_01_script.json --output chapter_01_final.mp4
    python compose_video.py chapter_01_script.json --music bgm.mp3
"""

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path


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

FFMPEG_TOOLS_HINT = "需要 ffmpeg 和 ffprobe 同时可用，并且都在 PATH 中"


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


def check_ffmpeg():
    """检查 ffmpeg / ffprobe 是否可用"""
    try:
        ffmpeg = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        ffprobe = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True)
        return ffmpeg.returncode == 0 and ffprobe.returncode == 0
    except FileNotFoundError:
        return False


def run_ffmpeg(cmd: list[str], error_prefix: str) -> None:
    """执行 ffmpeg / ffprobe 命令并在失败时抛出完整错误。"""
    result = subprocess.run(cmd, capture_output=True, text=True)
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
            "ffprobe",
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
            "ffprobe",
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


def normalize_clip(
    video_path: Path,
    output_path: Path,
    *,
    target_width: int,
    target_height: int,
    target_fps: str,
) -> None:
    """先把单个片段重编码为统一中间片，再做最终拼接。"""
    media = probe_media(video_path)
    # 进入拼接链路的每个中间片都要把音视频轨归零，避免后续 concat / 转场继续放大时间戳偏移。
    video_filter = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={target_fps},format=yuv420p,setpts=PTS-STARTPTS"
    )

    if media["has_audio"]:
        filter_complex = (
            f"[0:v]{video_filter}[vout];[0:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[aout]"
        )
        cmd = [
            "ffmpeg",
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
            "ffmpeg",
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


def normalize_clips(video_paths: list[Path], temp_dir: Path) -> list[Path]:
    """将全部片段统一成可安全拼接的中间片。"""
    first = probe_media(video_paths[0])
    target_width = int(first["width"])
    target_height = int(first["height"])
    target_fps = str(first["fps"])

    normalized_paths: list[Path] = []
    for index, path in enumerate(video_paths):
        normalized_path = temp_dir / f"normalized_{index:03d}.mp4"
        normalize_clip(
            path,
            normalized_path,
            target_width=target_width,
            target_height=target_height,
            target_fps=target_fps,
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
                "ffmpeg",
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
            "ffmpeg",
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


def concatenate_simple(video_paths: list, output_path: Path):
    """
    无转场拼接

    先把片段规范化为统一的 H.264/AAC 中间片，再做最终拼接，
    避免直接 copy 原始码流时的关键帧 / 时间戳边界问题。
    """
    with tempfile.TemporaryDirectory(prefix="compose-video-") as temp_dir:
        normalized_paths = normalize_clips(video_paths, Path(temp_dir))
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
    video_paths: list, transitions: list, output_path: Path, transition_duration: float = 0.5
):
    """
    使用 xfade 滤镜实现场景间转场，cut 边界用 concat 串联以避免滤镜链断裂。
    """
    with tempfile.TemporaryDirectory(prefix="compose-video-") as temp_dir:
        normalized_paths = normalize_clips(video_paths, Path(temp_dir))
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
            "ffmpeg",
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

        result = subprocess.run(cmd, capture_output=True, text=True)

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
        "ffmpeg",
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

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"添加背景音乐失败: {result.stderr}")


def compose_video(
    script_filename: str, output_filename: str = None, music_path: str = None, use_transitions: bool = True
) -> Path:
    """
    合成最终视频

    Args:
        script_filename: 剧本文件名
        output_filename: 输出文件名
        music_path: 背景音乐文件路径
        use_transitions: 是否使用转场效果

    Returns:
        输出视频路径
    """
    pm, project_name, project_dir = _require_project_cwd()

    # 加载剧本（pm.load_script 内部已用 _safe_subpath 过滤 ../ 等逃逸尝试）
    script = pm.load_script(project_name, script_filename)

    # 仅支持 drama 模式（顶层 scenes[]）；narration/reference_video 给友好错误
    if "scenes" not in script:
        content_mode = script.get("content_mode") or "unknown"
        generation_mode = script.get("generation_mode") or "storyboard"
        raise RuntimeError(
            f"compose_video.py 目前仅支持 drama 模式（剧本顶层需有 scenes[]）；"
            f"当前剧本 content_mode={content_mode}, generation_mode={generation_mode}，"
            "请使用 Web 端剪映草稿导出"
        )

    # 收集视频片段
    video_paths = []
    transitions = []

    for scene in script["scenes"]:
        video_clip = scene.get("generated_assets", {}).get("video_clip")
        if not video_clip:
            raise ValueError(f"场景 {scene['scene_id']} 缺少视频片段")

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
        transitions.append(scene.get("transition_to_next", "cut"))

    if not video_paths:
        raise ValueError("没有可用的视频片段")

    print(f"📹 共 {len(video_paths)} 个视频片段")

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
        concatenate_with_transitions(video_paths, transitions, output_path)
    else:
        concatenate_simple(video_paths, output_path)

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

    args = parser.parse_args()

    # 检查 ffmpeg / ffprobe
    if not check_ffmpeg():
        print(f"❌ 错误: {FFMPEG_TOOLS_HINT}")
        print("   macOS 可执行: brew install ffmpeg")
        print("   安装后请确认 ffmpeg -version 和 ffprobe -version 都能执行")
        sys.exit(1)

    try:
        output_path = compose_video(args.script, args.output, args.music, use_transitions=not args.no_transitions)

        print(f"\n🎉 最终视频: {output_path}")
        print("   单独片段保留在: videos/")

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
