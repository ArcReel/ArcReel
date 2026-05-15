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
import subprocess
import sys
import tempfile
from pathlib import Path

from lib.project_manager import ProjectManager

FFMPEG_TOOLS_HINT = "需要 ffmpeg 和 ffprobe 同时可用，并且都在 PATH 中"


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

    fps = str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "30")
    if fps in {"0/0", "0", ""}:
        fps = "30"

    duration_raw = payload.get("format", {}).get("duration")
    if not duration_raw:
        raise RuntimeError(f"无法从 ffprobe 输出中获取时长: {video_path}")
    try:
        duration = float(duration_raw)
    except ValueError as exc:
        raise RuntimeError(f"无法解析视频时长: {video_path}") from exc

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


def concatenate_with_transitions(
    video_paths: list, transitions: list, output_path: Path, transition_duration: float = 0.5
):
    """
    使用转场效果拼接视频

    使用 xfade 滤镜实现转场
    """
    with tempfile.TemporaryDirectory(prefix="compose-video-") as temp_dir:
        normalized_paths = normalize_clips(video_paths, Path(temp_dir))
        if len(normalized_paths) < 2:
            concatenate_final(normalized_paths, output_path)
            return

        # 构建 filter_complex
        inputs = []
        for path in normalized_paths:
            inputs.extend(["-i", str(path.resolve())])

        # 获取每个视频的时长
        durations = [get_video_duration(p) for p in normalized_paths]

        # 构建 xfade 滤镜链
        filter_parts = []

        for i in range(len(normalized_paths) - 1):
            transition = transitions[i] if i < len(transitions) else "fade"

            # xfade 类型映射
            xfade_type = {
                "cut": None,  # 不使用转场
                "fade": "fade",
                "dissolve": "dissolve",
                "wipe": "wipeleft",
            }.get(transition, "fade")

            if xfade_type is None:
                # cut 转场，不需要 xfade
                continue

            if i == 0:
                prev_label = "[0:v]"
            else:
                prev_label = f"[v{i}]"

            next_label = f"[{i + 1}:v]"
            out_label = f"[v{i + 1}]" if i < len(normalized_paths) - 2 else "[vout]"

            # 计算偏移量
            offset = sum(durations[: i + 1]) - transition_duration * (i + 1)

            filter_parts.append(
                f"{prev_label}{next_label}xfade=transition={xfade_type}:"
                f"duration={transition_duration}:offset={offset:.3f}{out_label}"
            )

        if not filter_parts:
            concatenate_final(normalized_paths, output_path)
            return

        # 下一个容易踩的坑：这里的视频转场已经平滑，但音频仍是硬拼接；
        # 如果后面要做“听感也平滑”的转场，需要把 concat 改成 acrossfade / afade 链。
        audio_filter = (
            ";".join([f"[{i}:a]" for i in range(len(normalized_paths))])
            + f"concat=n={len(normalized_paths)}:v=0:a=1[aout]"
        )

        filter_complex = ";".join(filter_parts) + ";" + audio_filter

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
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # 加载剧本
    script = pm.load_script(project_name, script_filename)

    # 收集视频片段
    video_paths = []
    transitions = []

    for scene in script["scenes"]:
        video_clip = scene.get("generated_assets", {}).get("video_clip")
        if not video_clip:
            raise ValueError(f"场景 {scene['scene_id']} 缺少视频片段")

        video_path = project_dir / video_clip
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        video_paths.append(video_path)
        transitions.append(scene.get("transition_to_next", "cut"))

    if not video_paths:
        raise ValueError("没有可用的视频片段")

    print(f"📹 共 {len(video_paths)} 个视频片段")

    # 确定输出路径
    if output_filename is None:
        chapter = script["novel"].get("chapter", "output").replace(" ", "_")
        output_filename = f"{chapter}_final.mp4"

    output_path = project_dir / "output" / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 合成视频
    print("🎬 正在合成视频...")

    if use_transitions and any(t != "cut" for t in transitions):
        concatenate_with_transitions(video_paths, transitions, output_path)
    else:
        concatenate_simple(video_paths, output_path)

    print(f"✅ 视频合成完成: {output_path}")

    # 添加背景音乐
    if music_path:
        music_file = Path(music_path)
        if not music_file.exists():
            music_file = project_dir / music_path

        if music_file.exists():
            print("🎵 正在添加背景音乐...")
            final_output = output_path.with_stem(output_path.stem + "_with_music")
            add_background_music(output_path, music_file, final_output)
            output_path = final_output
            print(f"✅ 背景音乐添加完成: {output_path}")
        else:
            print(f"⚠️  背景音乐文件不存在: {music_path}")

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
