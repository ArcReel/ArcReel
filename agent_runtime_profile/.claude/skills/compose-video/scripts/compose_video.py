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
import subprocess
import sys
import tempfile
from pathlib import Path

from lib.project_manager import ProjectManager


def check_ffmpeg():
    """检查 ffmpeg 是否可用"""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


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

    return float(result.stdout.strip())


def concatenate_simple(video_paths: list, output_path: Path):
    """
    简单拼接（无转场效果）

    使用 concat demuxer 进行快速拼接
    """
    # 创建临时文件列表
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in video_paths:
            # 使用绝对路径，避免 ffmpeg 解析相对路径出错
            abs_path = path.resolve()
            f.write(f"file '{abs_path}'\n")
        list_file = f.name

    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", str(output_path)]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 错误: {result.stderr}")

    finally:
        Path(list_file).unlink()


def concatenate_with_transitions(
    video_paths: list, transitions: list, output_path: Path, transition_duration: float = 0.5
):
    """
    使用转场效果拼接视频

    使用 xfade 滤镜实现转场
    """
    if len(video_paths) < 2:
        # 单个视频直接复制
        subprocess.run(["ffmpeg", "-y", "-i", str(video_paths[0]), "-c", "copy", str(output_path)])
        return

    # 构建 filter_complex
    inputs = []
    for i, path in enumerate(video_paths):
        inputs.extend(["-i", str(path)])

    # 获取每个视频的时长
    durations = [get_video_duration(p) for p in video_paths]

    # 构建 xfade 滤镜链
    filter_parts = []

    for i in range(len(video_paths) - 1):
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
        out_label = f"[v{i + 1}]" if i < len(video_paths) - 2 else "[vout]"

        # 计算偏移量
        offset = sum(durations[: i + 1]) - transition_duration * (i + 1)

        filter_parts.append(
            f"{prev_label}{next_label}xfade=transition={xfade_type}:"
            f"duration={transition_duration}:offset={offset:.3f}{out_label}"
        )

    if filter_parts:
        # 音频也需要处理
        audio_filter = (
            ";".join([f"[{i}:a]" for i in range(len(video_paths))]) + f"concat=n={len(video_paths)}:v=0:a=1[aout]"
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
            "-c:a",
            "aac",
            str(output_path),
        ]
    else:
        # 全是 cut 转场，使用简单拼接
        concatenate_simple(video_paths, output_path)
        return

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"⚠️  转场效果失败，尝试简单拼接: {result.stderr[:200]}")
        concatenate_simple(video_paths, output_path)


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

    # 检查 ffmpeg
    if not check_ffmpeg():
        print("❌ 错误: ffmpeg 未安装或不在 PATH 中")
        print("   请安装 ffmpeg: brew install ffmpeg (macOS)")
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
