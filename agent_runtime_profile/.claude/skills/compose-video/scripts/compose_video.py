#!/usr/bin/env python3
"""
Video Composer - Compose final video using ffmpeg

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
    """Check whether ffmpeg is available"""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def get_video_duration(video_path: Path) -> float:
    """Get video duration"""
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
    Simple concatenation (no transition effects)

    Uses concat demuxer for fast concatenation
    """
    # create temporary file list
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in video_paths:
            # use absolute paths to avoid ffmpeg errors with relative paths
            abs_path = path.resolve()
            f.write(f"file '{abs_path}'\n")
        list_file = f.name

    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", str(output_path)]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error: {result.stderr}")

    finally:
        Path(list_file).unlink()


def concatenate_with_transitions(
    video_paths: list, transitions: list, output_path: Path, transition_duration: float = 0.5
):
    """
    Concatenate videos with transition effects

    Uses the xfade filter to implement transitions
    """
    if len(video_paths) < 2:
        # single video; copy directly
        subprocess.run(["ffmpeg", "-y", "-i", str(video_paths[0]), "-c", "copy", str(output_path)])
        return

    # build filter_complex
    inputs = []
    for i, path in enumerate(video_paths):
        inputs.extend(["-i", str(path)])

    # get duration of each video
    durations = [get_video_duration(p) for p in video_paths]

    # build xfade filter chain
    filter_parts = []

    for i in range(len(video_paths) - 1):
        transition = transitions[i] if i < len(transitions) else "fade"

        # xfade type mapping
        xfade_type = {
            "cut": None,  # no transition
            "fade": "fade",
            "dissolve": "dissolve",
            "wipe": "wipeleft",
        }.get(transition, "fade")

        if xfade_type is None:
            # cut transition; no xfade needed
            continue

        if i == 0:
            prev_label = "[0:v]"
        else:
            prev_label = f"[v{i}]"

        next_label = f"[{i + 1}:v]"
        out_label = f"[v{i + 1}]" if i < len(video_paths) - 2 else "[vout]"

        # calculate offset
        offset = sum(durations[: i + 1]) - transition_duration * (i + 1)

        filter_parts.append(
            f"{prev_label}{next_label}xfade=transition={xfade_type}:"
            f"duration={transition_duration}:offset={offset:.3f}{out_label}"
        )

    if filter_parts:
        # audio also needs to be processed
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
        # all cut transitions; use simple concatenation
        concatenate_simple(video_paths, output_path)
        return

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"⚠️  Transition effect failed; trying simple concatenation: {result.stderr[:200]}")
        concatenate_simple(video_paths, output_path)


def add_background_music(video_path: Path, music_path: Path, output_path: Path, music_volume: float = 0.3):
    """
    Add background music

    Args:
        video_path: video file
        music_path: music file
        output_path: output file
        music_volume: background music volume (0-1)
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
        raise RuntimeError(f"Failed to add background music: {result.stderr}")


def compose_video(
    script_filename: str, output_filename: str = None, music_path: str = None, use_transitions: bool = True
) -> Path:
    """
    Compose the final video

    Args:
        script_filename: script filename
        output_filename: output filename
        music_path: background music file path
        use_transitions: whether to use transition effects

    Returns:
        output video path
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)

    # load script
    script = pm.load_script(project_name, script_filename)

    # collect video segments
    video_paths = []
    transitions = []

    for scene in script["scenes"]:
        video_clip = scene.get("generated_assets", {}).get("video_clip")
        if not video_clip:
            raise ValueError(f"Scene {scene['scene_id']} is missing a video segment")

        video_path = project_dir / video_clip
        if not video_path.exists():
            raise FileNotFoundError(f"Video file does not exist: {video_path}")

        video_paths.append(video_path)
        transitions.append(scene.get("transition_to_next", "cut"))

    if not video_paths:
        raise ValueError("No video segments available")

    print(f"📹 {len(video_paths)} video segment(s) total")

    # determine output path
    if output_filename is None:
        chapter = script["novel"].get("chapter", "output").replace(" ", "_")
        output_filename = f"{chapter}_final.mp4"

    output_path = project_dir / "output" / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # compose video
    print("🎬 Composing video...")

    if use_transitions and any(t != "cut" for t in transitions):
        concatenate_with_transitions(video_paths, transitions, output_path)
    else:
        concatenate_simple(video_paths, output_path)

    print(f"✅ Video composition complete: {output_path}")

    # add background music
    if music_path:
        music_file = Path(music_path)
        if not music_file.exists():
            music_file = project_dir / music_path

        if music_file.exists():
            print("🎵 Adding background music...")
            final_output = output_path.with_stem(output_path.stem + "_with_music")
            add_background_music(output_path, music_file, final_output)
            output_path = final_output
            print(f"✅ Background music added: {output_path}")
        else:
            print(f"⚠️  Background music file does not exist: {music_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Compose final video")
    parser.add_argument("script", help="Script filename")
    parser.add_argument("--output", help="Output filename")
    parser.add_argument("--music", help="Background music file")
    parser.add_argument("--no-transitions", action="store_true", help="Do not use transition effects")

    args = parser.parse_args()

    # check ffmpeg
    if not check_ffmpeg():
        print("❌ Error: ffmpeg is not installed or not in PATH")
        print("   Please install ffmpeg: brew install ffmpeg (macOS)")
        sys.exit(1)

    try:
        output_path = compose_video(args.script, args.output, args.music, use_transitions=not args.no_transitions)

        print(f"\n🎉 Final video: {output_path}")
        print("   Individual segments retained in: videos/")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
