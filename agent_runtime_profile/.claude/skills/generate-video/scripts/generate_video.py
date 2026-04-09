#!/usr/bin/env python3
"""
Video Generator - Generate video shots using the Veo 3.1 API

Usage:
    # Generate by episode (recommended)
    python generate_video.py episode_N.json --episode N

    # Resume from checkpoint
    python generate_video.py episode_N.json --episode N --resume

    # Single scene mode
    python generate_video.py episode_N.json --scene SCENE_ID

    # Batch mode (generate each scene independently)
    python generate_video.py episode_N.json --all

Each scene is generated as an independent video using the storyboard image as the starting frame,
then concatenated using ffmpeg.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from lib.generation_queue_client import (
    BatchTaskResult,
    BatchTaskSpec,
    batch_enqueue_and_wait_sync,
)
from lib.generation_queue_client import (
    enqueue_and_wait_sync as enqueue_and_wait,
)
from lib.project_manager import ProjectManager
from lib.prompt_utils import is_structured_video_prompt, video_prompt_to_yaml

# ============================================================================
# Prompt Building
# ============================================================================


def get_video_prompt(item: dict) -> str:
    """
    Get the video generation prompt

    Supports structured prompt format: if video_prompt is a dict, convert to YAML format.

    Args:
        item: segment/scene dictionary

    Returns:
        video_prompt string (may be in YAML format or plain string)
    """
    prompt = item.get("video_prompt")
    if not prompt:
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"Segment/scene is missing the video_prompt field: {item_id}")

    # detect whether it is a structured format
    if is_structured_video_prompt(prompt):
        # convert to YAML format
        return video_prompt_to_yaml(prompt)

    # avoid passing dict directly which would cause type errors downstream
    if isinstance(prompt, dict):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"Segment/scene video_prompt is an object but does not match the structured format specification: {item_id}")

    if not isinstance(prompt, str):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise TypeError(f"Segment/scene video_prompt has invalid type (expected str or dict): {item_id}")

    return prompt


def get_items_from_script(script: dict) -> tuple:
    """
    Get the scene/segment list and related field names based on content mode

    Args:
        script: script data

    Returns:
        (items_list, id_field, char_field, clue_field) tuple
    """
    content_mode = script.get("content_mode", "narration")
    if content_mode == "narration" and "segments" in script:
        return (script["segments"], "segment_id", "characters_in_segment", "clues_in_segment")
    return (script.get("scenes", []), "scene_id", "characters_in_scene", "clues_in_scene")


def parse_scene_ids(scenes_arg: str) -> list:
    """Parse a comma-separated list of scene IDs"""
    return [s.strip() for s in scenes_arg.split(",") if s.strip()]


DEFAULT_DURATIONS_FALLBACK = [4, 8]


def get_supported_durations(project: dict) -> list[int]:
    """Get the list of durations supported by the current video model from the project configuration or registry."""
    durations = project.get("_supported_durations")
    if durations and isinstance(durations, list):
        return durations
    # Resolve from registry via project's video_backend
    video_backend = project.get("video_backend")
    if video_backend and isinstance(video_backend, str) and "/" in video_backend:
        try:
            from lib.config.registry import PROVIDER_REGISTRY

            provider_id, model_id = video_backend.split("/", 1)
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta:
                model_info = provider_meta.models.get(model_id)
                if model_info and model_info.supported_durations:
                    return list(model_info.supported_durations)
        except ImportError:
            pass  # fall back to DEFAULT_DURATIONS_FALLBACK when registry is unavailable (e.g., running standalone)
    return DEFAULT_DURATIONS_FALLBACK


def validate_duration(duration: int, supported_durations: list[int] | None = None) -> str:
    """
    Validate and return a valid duration parameter.

    Args:
        duration: input duration (seconds)
        supported_durations: list of durations supported by the current video model

    Returns:
        valid duration string
    """
    valid = supported_durations or DEFAULT_DURATIONS_FALLBACK
    if duration in valid:
        return str(duration)
    # round up to the nearest valid value
    for d in sorted(valid):
        if d >= duration:
            return str(d)
    return str(max(valid))


# ============================================================================
# Checkpoint Management
# ============================================================================


def get_checkpoint_path(project_dir: Path, episode: int) -> Path:
    """Get the checkpoint file path"""
    return project_dir / "videos" / f".checkpoint_ep{episode}.json"


def load_checkpoint(project_dir: Path, episode: int) -> dict | None:
    """
    Load checkpoint

    Returns:
        checkpoint dict or None
    """
    checkpoint_path = get_checkpoint_path(project_dir, episode)
    if checkpoint_path.exists():
        with open(checkpoint_path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_checkpoint(project_dir: Path, episode: int, completed_scenes: list, started_at: str):
    """Save checkpoint"""
    checkpoint_path = get_checkpoint_path(project_dir, episode)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "episode": episode,
        "completed_scenes": completed_scenes,
        "started_at": started_at,
        "updated_at": datetime.now().isoformat(),
    }

    with open(checkpoint_path, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


def clear_checkpoint(project_dir: Path, episode: int):
    """Clear checkpoint"""
    checkpoint_path = get_checkpoint_path(project_dir, episode)
    if checkpoint_path.exists():
        checkpoint_path.unlink()


# ============================================================================
# FFmpeg Concatenation
# ============================================================================


def concatenate_videos(video_paths: list, output_path: Path) -> Path:
    """
    Concatenate multiple video segments using ffmpeg

    Args:
        video_paths: list of video file paths
        output_path: output path

    Returns:
        output video path
    """
    if len(video_paths) == 1:
        # only one segment; copy directly
        import shutil

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(video_paths[0], output_path)
        return output_path

    # create temporary file list
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for video_path in video_paths:
            f.write(f"file '{video_path}'\n")
        list_file = f.name

    try:
        # use ffmpeg concat demuxer
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", str(output_path)]
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"✅ Video concatenated: {output_path}")
        return output_path
    finally:
        Path(list_file).unlink()


# ============================================================================
# Batch Task Building Helpers
# ============================================================================


def _build_video_specs(
    *,
    items: list,
    id_field: str,
    content_mode: str,
    script_filename: str,
    project_dir: Path,
    project: dict | None = None,
    skip_ids: list[str] | None = None,
) -> tuple[list[BatchTaskSpec], dict[str, int]]:
    """
    Build BatchTaskSpec list and resource_id -> order_index mapping from scene/segment list.

    Skips items missing storyboard images or with invalid prompts, and prints warnings.

    Returns:
        (specs, order_map)  order_map: resource_id -> index in original items
    """
    _project = project or {}
    item_type = "segment" if content_mode == "narration" else "scene"
    default_duration = _project.get("default_duration") or (4 if content_mode == "narration" else 8)
    supported = get_supported_durations(_project)
    skip_set = set(skip_ids or [])

    specs: list[BatchTaskSpec] = []
    order_map: dict[str, int] = {}

    for idx, item in enumerate(items):
        item_id = item.get(id_field) or item.get("scene_id") or item.get("segment_id") or f"item_{idx}"

        if item_id in skip_set:
            continue

        storyboard_image = (item.get("generated_assets") or {}).get("storyboard_image")
        if not storyboard_image:
            print(f"⚠️  {item_type} {item_id} has no storyboard image, skipping")
            continue
        storyboard_path = project_dir / storyboard_image
        if not storyboard_path.exists():
            print(f"⚠️  Storyboard image does not exist: {storyboard_path}, skipping")
            continue

        try:
            prompt = get_video_prompt(item)
        except Exception as e:
            print(f"⚠️  {item_type} {item_id} has an invalid video_prompt, skipping: {e}")
            continue

        duration = item.get("duration_seconds", default_duration)
        duration_str = validate_duration(duration, supported)

        specs.append(
            BatchTaskSpec(
                task_type="video",
                media_type="video",
                resource_id=item_id,
                payload={
                    "prompt": prompt,
                    "script_file": script_filename,
                    "duration_seconds": int(duration_str),
                },
                script_file=script_filename,
            )
        )
        order_map[item_id] = idx

    return specs, order_map


def _scan_completed_items(
    items: list,
    id_field: str,
    item_type: str,
    completed_scenes: list[str],
    videos_dir: Path,
) -> tuple[list[Path | None], list[str]]:
    """Scan items for already-completed videos; return ordered paths and done IDs."""
    ordered_paths: list[Path | None] = [None] * len(items)
    already_done: list[str] = []
    for idx, item in enumerate(items):
        item_id = item.get(id_field, item.get("scene_id", f"item_{idx}"))
        video_output = videos_dir / f"scene_{item_id}.mp4"
        if item_id in completed_scenes and video_output.exists():
            print(f"  [{idx + 1}/{len(items)}] {item_type} {item_id} ✓ done")
            ordered_paths[idx] = video_output
            already_done.append(item_id)
        elif item_id in completed_scenes:
            completed_scenes.remove(item_id)
    return ordered_paths, already_done


def _submit_and_wait_with_checkpoint(
    *,
    project_name: str,
    project_dir: Path,
    specs: list[BatchTaskSpec],
    order_map: dict[str, int],
    ordered_paths: list[Path | None],
    completed_scenes: list[str],
    save_fn: Callable[[], None],
    item_type: str,
) -> list[BatchTaskResult]:
    """Submit specs via batch_enqueue_and_wait_sync with checkpoint on each success."""
    print(f"\n🚀 Submitting {len(specs)} videos to the generation queue...\n")

    def on_success(br: BatchTaskResult) -> None:
        result = br.result or {}
        relative_path = result.get("file_path") or f"videos/scene_{br.resource_id}.mp4"
        output_path = project_dir / relative_path
        ordered_paths[order_map[br.resource_id]] = output_path
        completed_scenes.append(br.resource_id)
        save_fn()
        print(f"    ✅ Complete: {output_path.name}")

    def on_failure(br: BatchTaskResult) -> None:
        print(f"    ❌ {br.resource_id} failed: {br.error}")

    _, failures = batch_enqueue_and_wait_sync(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )

    if failures:
        print(f"\n⚠️  {len(failures)} {item_type}(s) failed to generate:")
        for f in failures:
            print(f"   - {f.resource_id}: {f.error}")
        print("    💡 Use the --resume parameter to continue from here")
        raise RuntimeError(f"{len(failures)} {item_type}(s) failed to generate")

    return failures


# ============================================================================
# Episode Video Generation (each scene generated independently)
# ============================================================================


def generate_episode_video(
    script_filename: str,
    episode: int,
    resume: bool = False,
) -> list[Path]:
    """
    Generate video clips for all scenes in the specified episode.

    Each scene is generated as an independent video using the storyboard image as the starting frame.
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)
    project = pm.load_project(project_name)
    script = pm.load_script(project_name, script_filename)
    content_mode = script.get("content_mode", "narration")
    all_items, id_field, _, _ = get_items_from_script(script)

    episode_items = [s for s in all_items if s.get("episode", 1) == episode]
    if not episode_items:
        raise ValueError(f"No scenes/segments found for episode {episode}")

    item_type = "segment" if content_mode == "narration" else "scene"
    print(f"📋 Episode {episode} has {len(episode_items)} {item_type}(s)")

    # Checkpoint
    completed_scenes: list[str] = []
    started_at = datetime.now().isoformat()
    if resume:
        checkpoint = load_checkpoint(project_dir, episode)
        if checkpoint:
            completed_scenes = checkpoint.get("completed_scenes", [])
            started_at = checkpoint.get("started_at", started_at)
            print(f"🔄 Resuming from checkpoint; {len(completed_scenes)} scene(s) already complete")
        else:
            print("⚠️  No checkpoint found; starting from the beginning")

    videos_dir = project_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    ordered_video_paths, already_done_ids = _scan_completed_items(
        episode_items,
        id_field,
        item_type,
        completed_scenes,
        videos_dir,
    )
    specs, order_map = _build_video_specs(
        items=episode_items,
        id_field=id_field,
        content_mode=content_mode,
        script_filename=script_filename,
        project_dir=project_dir,
        project=project,
        skip_ids=already_done_ids,
    )

    if not specs and not any(ordered_video_paths):
        raise RuntimeError("No video segments available to generate")

    if specs:
        _submit_and_wait_with_checkpoint(
            project_name=project_name,
            project_dir=project_dir,
            specs=specs,
            order_map=order_map,
            ordered_paths=ordered_video_paths,
            completed_scenes=completed_scenes,
            save_fn=lambda: save_checkpoint(project_dir, episode, completed_scenes, started_at),
            item_type=item_type,
        )

    scene_videos = [p for p in ordered_video_paths if p is not None]
    if not scene_videos:
        raise RuntimeError("No video segments were generated")

    clear_checkpoint(project_dir, episode)
    print(f"\n🎉 Episode {episode} video generation complete; {len(scene_videos)} segment(s)")
    return scene_videos


# ============================================================================
# Single Scene Generation
# ============================================================================


def generate_scene_video(script_filename: str, scene_id: str) -> Path:
    """
    Generate a video for a single scene/segment

    Args:
        script_filename: script filename
        scene_id: scene/segment ID

    Returns:
        path to the generated video
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)
    project = pm.load_project(project_name)

    # load script
    script = pm.load_script(project_name, script_filename)
    content_mode = script.get("content_mode", "narration")
    all_items, id_field, _, _ = get_items_from_script(script)

    # find the specified scene/segment
    item = None
    for s in all_items:
        if s.get(id_field) == scene_id or s.get("scene_id") == scene_id:
            item = s
            break

    if not item:
        raise ValueError(f"Scene/segment '{scene_id}' does not exist")

    # check storyboard image
    storyboard_image = item.get("generated_assets", {}).get("storyboard_image")
    if not storyboard_image:
        raise ValueError(f"Scene/segment '{scene_id}' has no storyboard image; please run generate-storyboard first")

    storyboard_path = project_dir / storyboard_image
    if not storyboard_path.exists():
        raise FileNotFoundError(f"Storyboard image does not exist: {storyboard_path}")

    # use video_prompt field directly
    prompt = get_video_prompt(item)

    # get duration (project configuration takes priority; narration mode defaults to 4s, drama mode to 8s)
    default_duration = project.get("default_duration") or (4 if content_mode == "narration" else 8)
    duration = item.get("duration_seconds", default_duration)
    supported = get_supported_durations(project)
    duration_str = validate_duration(duration, supported)

    print(f"🎬 Generating video: scene/segment {scene_id}")
    print("   Estimated wait time: 1-6 minutes")

    queued = enqueue_and_wait(
        project_name=project_name,
        task_type="video",
        media_type="video",
        resource_id=scene_id,
        payload={
            "prompt": prompt,
            "script_file": script_filename,
            "duration_seconds": int(duration_str),
        },
        script_file=script_filename,
        source="skill",
    )
    result = queued.get("result") or {}
    relative_path = result.get("file_path") or f"videos/scene_{scene_id}.mp4"
    output_path = project_dir / relative_path

    print(f"✅ Video saved: {output_path}")
    return output_path


def generate_all_videos(script_filename: str) -> list:
    """
    Generate videos for all pending scenes (independent mode)

    Returns:
        list of generated video paths
    """
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)
    project = pm.load_project(project_name)

    # load script
    script = pm.load_script(project_name, script_filename)
    content_mode = script.get("content_mode", "narration")
    all_items, id_field, _, _ = get_items_from_script(script)

    pending_items = [item for item in all_items if not (item.get("generated_assets") or {}).get("video_clip")]

    if not pending_items:
        print("✨ Videos for all scenes/segments have already been generated")
        return []

    item_type = "segment" if content_mode == "narration" else "scene"
    print(f"📋 {len(pending_items)} {item_type}(s) pending video generation")
    print("⚠️  Each video may take 1-6 minutes; please be patient")
    print("💡 Recommended: use --episode N mode to generate and auto-concatenate")

    specs, _ = _build_video_specs(
        items=pending_items,
        id_field=id_field,
        content_mode=content_mode,
        script_filename=script_filename,
        project_dir=project_dir,
        project=project,
    )

    if not specs:
        print("⚠️  No video tasks available to generate (possibly missing storyboard images or prompts)")
        return []

    print(f"\n🚀 Submitting {len(specs)} videos to the generation queue...\n")

    result_paths: list[Path] = []

    def on_success(br: BatchTaskResult) -> None:
        result = br.result or {}
        relative_path = result.get("file_path") or f"videos/scene_{br.resource_id}.mp4"
        output_path = project_dir / relative_path
        result_paths.append(output_path)
        print(f"✅ Complete: {output_path.name}")

    def on_failure(br: BatchTaskResult) -> None:
        print(f"❌ {br.resource_id} failed: {br.error}")

    _, failures = batch_enqueue_and_wait_sync(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )

    if failures:
        print(f"\n⚠️  {len(failures)} {item_type}(s) failed to generate:")
        for f in failures:
            print(f"   - {f.resource_id}: {f.error}")

    print(f"\n🎉 Batch video generation complete; {len(result_paths)} video(s)")
    return result_paths


def generate_selected_videos(
    script_filename: str,
    scene_ids: list,
    resume: bool = False,
) -> list:
    """
    Generate videos for specified scenes

    Args:
        script_filename: script filename
        scene_ids: list of scene IDs
        resume: whether to resume from checkpoint

    Returns:
        list of generated video paths
    """
    import hashlib

    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)
    project = pm.load_project(project_name)
    script = pm.load_script(project_name, script_filename)
    content_mode = script.get("content_mode", "narration")
    all_items, id_field, _, _ = get_items_from_script(script)

    # filter the specified scenes
    items_by_id = {}
    for item in all_items:
        items_by_id[item.get(id_field, "")] = item
        if "scene_id" in item:
            items_by_id[item["scene_id"]] = item

    selected_items = []
    for scene_id in scene_ids:
        if scene_id in items_by_id:
            selected_items.append(items_by_id[scene_id])
        else:
            print(f"⚠️  Scene/segment '{scene_id}' does not exist, skipping")

    if not selected_items:
        raise ValueError("No valid scenes/segments found")

    item_type = "segment" if content_mode == "narration" else "scene"
    print(f"📋 {len(selected_items)} {item_type}(s) selected")

    # Checkpoint
    scenes_hash = hashlib.md5(",".join(scene_ids).encode()).hexdigest()[:8]
    checkpoint_path = project_dir / "videos" / f".checkpoint_selected_{scenes_hash}.json"
    completed_scenes: list[str] = []
    started_at = datetime.now().isoformat()

    if resume and checkpoint_path.exists():
        with open(checkpoint_path, encoding="utf-8") as f:
            checkpoint = json.load(f)
            completed_scenes = checkpoint.get("completed_scenes", [])
            started_at = checkpoint.get("started_at", started_at)
            print(f"🔄 Resuming from checkpoint; {len(completed_scenes)} scene(s) already complete")

    videos_dir = project_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    ordered_results, already_done_ids = _scan_completed_items(
        selected_items,
        id_field,
        item_type,
        completed_scenes,
        videos_dir,
    )
    specs, order_map = _build_video_specs(
        items=selected_items,
        id_field=id_field,
        content_mode=content_mode,
        script_filename=script_filename,
        project_dir=project_dir,
        project=project,
        skip_ids=already_done_ids,
    )

    if specs:

        def _save():
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            with open(checkpoint_path, "w", encoding="utf-8") as f_ckpt:
                json.dump(
                    {
                        "scene_ids": scene_ids,
                        "completed_scenes": completed_scenes,
                        "started_at": started_at,
                        "updated_at": datetime.now().isoformat(),
                    },
                    f_ckpt,
                    ensure_ascii=False,
                    indent=2,
                )

        _submit_and_wait_with_checkpoint(
            project_name=project_name,
            project_dir=project_dir,
            specs=specs,
            order_map=order_map,
            ordered_paths=ordered_results,
            completed_scenes=completed_scenes,
            save_fn=_save,
            item_type=item_type,
        )

    final_results = [p for p in ordered_results if p is not None]

    # clear checkpoint after all complete
    if checkpoint_path.exists():
        checkpoint_path.unlink()

    print(f"\n🎉 Batch video generation complete; {len(final_results)} video(s)")
    return final_results


# ============================================================================
# CLI
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Generate video shots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate by episode (recommended)
  python generate_video.py episode_1.json --episode 1

  # Resume from checkpoint
  python generate_video.py episode_1.json --episode 1 --resume

  # Single scene mode
  python generate_video.py episode_1.json --scene E1S1

  # Batch selection mode
  python generate_video.py episode_1.json --scenes E1S01,E1S05,E1S10

  # Batch mode (generate independently)
  python generate_video.py episode_1.json --all
        """,
    )
    parser.add_argument("script", help="Script filename")

    # mode selection
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--scene", help="Specify scene ID (single-scene mode)")
    mode_group.add_argument("--scenes", help="Specify multiple scene IDs (comma-separated), e.g.: E1S01,E1S05,E1S10")
    mode_group.add_argument("--all", action="store_true", help="Generate all pending scenes (independent mode)")
    mode_group.add_argument("--episode", type=int, help="Generate and concatenate by episode (recommended)")

    # other options
    parser.add_argument("--resume", action="store_true", help="Resume from the last interruption")

    args = parser.parse_args()

    try:
        if args.scene:
            generate_scene_video(args.script, args.scene)
        elif args.scenes:
            scene_ids = parse_scene_ids(args.scenes)
            generate_selected_videos(
                args.script,
                scene_ids,
                resume=args.resume,
            )
        elif args.all:
            generate_all_videos(args.script)
        elif args.episode:
            generate_episode_video(
                args.script,
                args.episode,
                resume=args.resume,
            )
        else:
            print("Please specify a mode: --scene, --scenes, --all, or --episode")
            print("Use --help to see help")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
