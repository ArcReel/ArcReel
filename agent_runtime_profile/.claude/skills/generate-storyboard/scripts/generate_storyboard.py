#!/usr/bin/env python3
"""
Storyboard Generator - Generate storyboard images via the generation queue

Both modes generate storyboard images through the generation worker:
- narration mode (narration + visuals): generates 9:16 portrait storyboard images
- drama mode (drama animation): generates 16:9 landscape storyboard images

Usage:
    # narration mode: submit storyboard generation tasks (default)
    python generate_storyboard.py <project_name> <script_file>
    python generate_storyboard.py <project_name> <script_file> --scene E1S05
    python generate_storyboard.py <project_name> <script_file> --segment-ids E1S01 E1S02

    # drama mode: submit storyboard generation tasks
    python generate_storyboard.py <project_name> <script_file>
    python generate_storyboard.py <project_name> <script_file> --scene E1S05
    python generate_storyboard.py <project_name> <script_file> --scene-ids E1S01 E1S02
"""

import argparse
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from lib.generation_queue_client import (
    BatchTaskResult,
    BatchTaskSpec,
    batch_enqueue_and_wait_sync,
)
from lib.project_manager import ProjectManager
from lib.prompt_utils import image_prompt_to_yaml, is_structured_image_prompt
from lib.storyboard_sequence import (
    StoryboardTaskPlan,
    build_storyboard_dependency_plan,
    get_storyboard_items,
)


class FailureRecorder:
    """Failure record manager (thread-safe)"""

    def __init__(self, output_dir: Path):
        self.output_path = output_dir / "generation_failures.json"
        self.failures: list[dict] = []
        self._lock = threading.Lock()

    def record_failure(
        self,
        scene_id: str,
        failure_type: str,  # "scene"
        error: str,
        attempts: int = 3,
        **extra,
    ):
        """Record a failure"""
        with self._lock:
            self.failures.append(
                {
                    "scene_id": scene_id,
                    "type": failure_type,
                    "error": error,
                    "attempts": attempts,
                    "timestamp": datetime.now().isoformat(),
                    **extra,
                }
            )

    def save(self):
        """Save failure records to file"""
        if not self.failures:
            return

        with self._lock:
            data = {
                "generated_at": datetime.now().isoformat(),
                "total_failures": len(self.failures),
                "failures": self.failures,
            }

            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"\n⚠️  Failure records saved: {self.output_path}")

    def get_failed_scene_ids(self) -> list[str]:
        """Get all failed scene IDs (for regeneration)"""
        return [f["scene_id"] for f in self.failures if f["type"] == "scene"]


# ==================== Prompt Building Functions ====================


def get_items_from_script(script: dict) -> tuple:
    """
    Get the scene/segment list and ID field name based on content mode

    Args:
        script: script data

    Returns:
        (items_list, id_field, char_field, clue_field) tuple
    """
    return get_storyboard_items(script)


def build_storyboard_prompt(
    segment: dict,
    characters: dict = None,
    clues: dict = None,
    style: str = "",
    style_description: str = "",
    id_field: str = "segment_id",
    char_field: str = "characters_in_segment",
    clue_field: str = "clues_in_segment",
    content_mode: str = "narration",
) -> str:
    """
    Build the storyboard task prompt (universal, applies to both narration and drama modes).

    Supports structured prompt format: if image_prompt is a dict, converts to YAML format.
    """
    image_prompt = segment.get("image_prompt", "")
    if not image_prompt:
        raise ValueError(f"Segment/scene {segment[id_field]} is missing the image_prompt field")

    # build style prefix
    style_parts = []
    if style:
        style_parts.append(f"Style: {style}")
    if style_description:
        style_parts.append(f"Visual style: {style_description}")
    style_prefix = "\n".join(style_parts) + "\n\n" if style_parts else ""

    # narration mode appends portrait composition suffix; drama mode is controlled via API aspect_ratio parameter
    composition_suffix = ""
    if content_mode == "narration":
        if is_structured_image_prompt(image_prompt):
            composition_suffix = "\nPortrait composition."
        else:
            composition_suffix = " Portrait composition."

    # detect whether it is a structured format
    if is_structured_image_prompt(image_prompt):
        yaml_prompt = image_prompt_to_yaml(image_prompt, style)
        return f"{style_prefix}{yaml_prompt}{composition_suffix}"

    return f"{style_prefix}{image_prompt}{composition_suffix}"


def _select_storyboard_items(
    items: list[dict],
    id_field: str,
    segment_ids: list[str] | None,
) -> list[dict]:
    if segment_ids:
        selected_set = {str(segment_id) for segment_id in segment_ids}
        return [item for item in items if str(item.get(id_field)) in selected_set]

    return [item for item in items if not item.get("generated_assets", {}).get("storyboard_image")]


def _build_storyboard_specs(
    *,
    plans: list[StoryboardTaskPlan],
    items_by_id: dict[str, dict],
    characters: dict[str, dict],
    clues: dict[str, dict],
    style: str,
    style_description: str,
    id_field: str,
    char_field: str,
    clue_field: str,
    content_mode: str,
    script_filename: str,
) -> list[BatchTaskSpec]:
    """Build BatchTaskSpec list from dependency plans, with prompts and dependency_resource_id."""
    specs: list[BatchTaskSpec] = []
    for plan in plans:
        item = items_by_id[plan.resource_id]
        prompt = build_storyboard_prompt(
            item,
            characters,
            clues,
            style,
            style_description,
            id_field,
            char_field,
            clue_field,
            content_mode=content_mode,
        )
        specs.append(
            BatchTaskSpec(
                task_type="storyboard",
                media_type="image",
                resource_id=plan.resource_id,
                payload={"prompt": prompt, "script_file": script_filename},
                script_file=script_filename,
                dependency_resource_id=plan.dependency_resource_id,
                dependency_group=plan.dependency_group,
                dependency_index=plan.dependency_index,
            )
        )
    return specs


def _load_project_metadata(pm: ProjectManager, project_name: str) -> dict | None:
    """Load project.json if available."""
    if not pm.project_exists(project_name):
        return None
    try:
        data = pm.load_project(project_name)
        print("📁 Project metadata loaded (project.json)")
        return data
    except Exception as e:
        print(f"⚠️  Unable to load project metadata: {e}")
        return None


def _collect_ordered_paths(
    successes: list[BatchTaskResult],
    plans: list[StoryboardTaskPlan],
    project_dir: Path,
) -> list[Path]:
    """Map successes back to plan order and return file paths."""
    success_map = {s.resource_id: s for s in successes}
    paths: list[Path] = []
    for plan in plans:
        br = success_map.get(plan.resource_id)
        if br:
            result = br.result or {}
            relative = result.get("file_path") or f"storyboards/scene_{plan.resource_id}.png"
            paths.append(project_dir / relative)
    return paths


def generate_storyboard_direct(
    script_filename: str,
    segment_ids: list[str] | None = None,
) -> tuple[list[Path], list[tuple[str, str]]]:
    """
    Submit storyboard generation tasks via the generation queue (works for both narration and drama modes).

    Returns:
        (list of successful paths, list of failures) tuple
    """
    pm, project_name = ProjectManager.from_cwd()
    script = pm.load_script(project_name, script_filename)
    project_dir = pm.get_project_path(project_name)
    content_mode = script.get("content_mode", "narration")
    project_data = _load_project_metadata(pm, project_name)

    items, id_field, char_field, clue_field = get_items_from_script(script)
    segments_to_process = _select_storyboard_items(items, id_field, segment_ids)

    if not segments_to_process:
        print("✨ Storyboard images for all segments have already been generated")
        return [], []

    characters = project_data.get("characters", {}) if project_data else {}
    clues = project_data.get("clues", {}) if project_data else {}
    style = project_data.get("style", "") if project_data else ""
    style_description = project_data.get("style_description", "") if project_data else ""
    items_by_id = {str(item[id_field]): item for item in items if item.get(id_field)}
    dependency_plans = build_storyboard_dependency_plan(
        items,
        id_field,
        [str(item[id_field]) for item in segments_to_process],
        script_filename,
    )

    specs = _build_storyboard_specs(
        plans=dependency_plans,
        items_by_id=items_by_id,
        characters=characters,
        clues=clues,
        style=style,
        style_description=style_description,
        id_field=id_field,
        char_field=char_field,
        clue_field=clue_field,
        content_mode=content_mode,
        script_filename=script_filename,
    )

    print(f"📷 Submitting {len(specs)} storyboard images to the generation queue...")

    recorder = FailureRecorder(project_dir / "storyboards")

    def on_success(br: BatchTaskResult) -> None:
        print(f"✅ Storyboard generation: {br.resource_id} complete")

    def on_failure(br: BatchTaskResult) -> None:
        recorder.record_failure(
            scene_id=br.resource_id,
            failure_type="scene",
            error=br.error or "unknown",
            attempts=3,
        )
        print(f"❌ Storyboard generation: {br.resource_id} failed - {br.error}")

    successes, failures = batch_enqueue_and_wait_sync(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )
    recorder.save()

    ordered_results = _collect_ordered_paths(successes, dependency_plans, project_dir)
    failure_tuples = [(f.resource_id, f.error or "unknown") for f in failures]
    return ordered_results, failure_tuples


def main():
    parser = argparse.ArgumentParser(description="Generate storyboard images")
    parser.add_argument("script", help="Script filename")

    # auxiliary parameters
    parser.add_argument("--scene", help="Specify a single scene ID (single-scene mode)")
    parser.add_argument("--scene-ids", nargs="+", help="Specify scene IDs")
    parser.add_argument("--segment-ids", nargs="+", help="Specify segment IDs (narration mode alias)")

    args = parser.parse_args()

    try:
        # detect content_mode
        pm, project_name = ProjectManager.from_cwd()
        script = pm.load_script(project_name, args.script)
        content_mode = script.get("content_mode", "narration")

        print(f"🚀 {content_mode} mode: generating storyboard images via queue")

        # merge --scene-ids and --segment-ids parameters
        if args.scene:
            segment_ids = [args.scene]
        else:
            segment_ids = args.segment_ids or args.scene_ids

        results, failed = generate_storyboard_direct(
            args.script,
            segment_ids=segment_ids,
        )
        print(f"\n📊 Generation complete: {len(results)} storyboard images")
        if failed:
            print(f"⚠️  Failed: {len(failed)}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
