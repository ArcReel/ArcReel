"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from lib.gemini_client import GeminiClient, get_shared_rate_limiter
from lib.media_generator import MediaGenerator
from lib.project_manager import ProjectManager
from lib.prompt_builders import build_character_prompt, build_clue_prompt
from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    video_prompt_to_yaml,
)


project_root = Path(__file__).parent.parent.parent.parent
pm = ProjectManager(project_root / "projects")
rate_limiter = get_shared_rate_limiter()


def get_project_manager() -> ProjectManager:
    return pm


def get_media_generator(project_name: str) -> MediaGenerator:
    project_path = get_project_manager().get_project_path(project_name)
    return MediaGenerator(project_path, rate_limiter=rate_limiter)


def get_aspect_ratio(project: dict, resource_type: str) -> str:
    content_mode = project.get("content_mode", "narration")
    custom_ratios = project.get("aspect_ratio", {})
    if resource_type in custom_ratios:
        return custom_ratios[resource_type]

    if resource_type == "characters":
        return "3:4"
    if resource_type == "clues":
        return "16:9"
    if content_mode == "narration":
        return "9:16"
    return "16:9"


def normalize_veo_duration_seconds(duration_seconds: Optional[int]) -> str:
    try:
        value = int(duration_seconds) if duration_seconds is not None else 4
    except (TypeError, ValueError):
        value = 4

    if value <= 4:
        return "4"
    if value <= 6:
        return "6"
    return "8"


def _get_items_from_script(script: dict) -> Tuple[List[dict], str, str, str]:
    content_mode = script.get("content_mode", "narration")
    if content_mode == "narration" and "segments" in script:
        return (
            script.get("segments", []),
            "segment_id",
            "characters_in_segment",
            "clues_in_segment",
        )
    return (
        script.get("scenes", []),
        "scene_id",
        "characters_in_scene",
        "clues_in_scene",
    )


def _normalize_storyboard_prompt(prompt: Union[str, dict], style: str) -> str:
    if isinstance(prompt, str):
        return prompt

    if not isinstance(prompt, dict):
        raise ValueError("prompt must be a string or object")

    if not is_structured_image_prompt(prompt):
        raise ValueError("prompt must be a string or include scene/composition")

    scene_text = str(prompt.get("scene", "")).strip()
    if not scene_text:
        raise ValueError("prompt.scene must not be empty")

    composition = prompt.get("composition") if isinstance(prompt.get("composition"), dict) else {}
    normalized_prompt = {
        "scene": scene_text,
        "composition": {
            "shot_type": str(composition.get("shot_type") or "Medium Shot"),
            "lighting": str(composition.get("lighting", "") or ""),
            "ambiance": str(composition.get("ambiance", "") or ""),
        },
    }
    return image_prompt_to_yaml(normalized_prompt, style)


def _normalize_video_prompt(prompt: Union[str, dict]) -> str:
    if isinstance(prompt, str):
        return prompt

    if not isinstance(prompt, dict):
        raise ValueError("prompt must be a string or object")

    if not is_structured_video_prompt(prompt):
        raise ValueError("prompt must be a string or include action/camera_motion")

    action_text = str(prompt.get("action", "")).strip()
    if not action_text:
        raise ValueError("prompt.action must not be empty")

    dialogue = prompt.get("dialogue", [])
    if dialogue is None:
        dialogue = []
    if not isinstance(dialogue, list):
        raise ValueError("prompt.dialogue must be an array")

    normalized_dialogue = []
    for item in dialogue:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker", "") or "").strip()
        line = str(item.get("line", "") or "").strip()
        if speaker or line:
            normalized_dialogue.append({"speaker": speaker, "line": line})

    normalized_prompt: Dict[str, Any] = {
        "action": action_text,
        "camera_motion": str(prompt.get("camera_motion", "") or "") or "Static",
        "ambiance_audio": str(prompt.get("ambiance_audio", "") or ""),
        "dialogue": normalized_dialogue,
    }
    return video_prompt_to_yaml(normalized_prompt)


def _collect_reference_images(
    project: dict,
    project_path: Path,
    target_item: dict,
    *,
    char_field: str,
    clue_field: str,
    extra_reference_images: Optional[List[str]] = None,
) -> Optional[List[Path]]:
    reference_images: List[Path] = []

    for char_name in target_item.get(char_field, []):
        char_data = project.get("characters", {}).get(char_name, {})
        sheet = char_data.get("character_sheet")
        if sheet:
            path = project_path / sheet
            if path.exists():
                reference_images.append(path)

    for clue_name in target_item.get(clue_field, []):
        clue_data = project.get("clues", {}).get(clue_name, {})
        sheet = clue_data.get("clue_sheet")
        if sheet:
            path = project_path / sheet
            if path.exists():
                reference_images.append(path)

    for extra in extra_reference_images or []:
        extra_path = Path(extra)
        if not extra_path.is_absolute():
            extra_path = project_path / extra_path
        if extra_path.exists():
            reference_images.append(extra_path)

    return reference_images or None


def _get_grid_layout(scene_count: int) -> Tuple[int, int, str]:
    if scene_count <= 4:
        return (2, 2, "2x2 四宫格")
    return (2, 3, "2x3 六宫格")


def _build_grid_prompt(scenes: List[dict], style: str, id_field: str) -> str:
    scene_count = len(scenes)
    _, _, layout_name = _get_grid_layout(scene_count)

    descriptions = []
    for i, scene in enumerate(scenes, 1):
        image_prompt = scene.get("image_prompt", "")
        if not image_prompt:
            raise ValueError(f"scene missing image_prompt: {scene.get(id_field)}")

        if is_structured_image_prompt(image_prompt):
            prompt_content = image_prompt_to_yaml(image_prompt, style)
        else:
            prompt_content = str(image_prompt)

        descriptions.append(f"宫格{i}（{scene[id_field]}）：{prompt_content}")

    return (
        f"一张 16:9 横屏的多宫格分镜图，包含 {scene_count} 个连续场景。\n"
        f"采用 {layout_name} 布局，每个格子展示一个场景的关键画面。宫格之间用细黑线分隔。\n\n"
        f"{chr(10).join(descriptions)}\n\n"
        "人物必须与提供的参考图完全一致。"
    )


def execute_storyboard_task(project_name: str, resource_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for storyboard task")

    prompt = payload.get("prompt")
    if prompt is None:
        raise ValueError("prompt is required for storyboard task")

    project = get_project_manager().load_project(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    script = get_project_manager().load_script(project_name, script_file)
    items, id_field, char_field, clue_field = _get_items_from_script(script)

    target_item = None
    for item in items:
        if str(item.get(id_field)) == resource_id:
            target_item = item
            break

    if not target_item:
        raise ValueError(f"scene/segment not found: {resource_id}")

    prompt_text = _normalize_storyboard_prompt(prompt, project.get("style", ""))
    reference_images = _collect_reference_images(
        project,
        project_path,
        target_item,
        char_field=char_field,
        clue_field=clue_field,
        extra_reference_images=payload.get("extra_reference_images") or [],
    )

    generator = get_media_generator(project_name)
    aspect_ratio = get_aspect_ratio(project, "storyboards")

    _, version = generator.generate_image(
        prompt=prompt_text,
        resource_type="storyboards",
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size="2K",
    )

    get_project_manager().update_scene_asset(
        project_name=project_name,
        script_filename=script_file,
        scene_id=resource_id,
        asset_type="storyboard_image",
        asset_path=f"storyboards/scene_{resource_id}.png",
    )

    created_at = generator.versions.get_versions("storyboards", resource_id)["versions"][-1][
        "created_at"
    ]

    return {
        "version": version,
        "file_path": f"storyboards/scene_{resource_id}.png",
        "created_at": created_at,
        "resource_type": "storyboards",
        "resource_id": resource_id,
    }


def execute_video_task(project_name: str, resource_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for video task")

    prompt = payload.get("prompt")
    if prompt is None:
        raise ValueError("prompt is required for video task")

    project = get_project_manager().load_project(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    generator = get_media_generator(project_name)

    storyboard_file = project_path / "storyboards" / f"scene_{resource_id}.png"
    if not storyboard_file.exists():
        raise ValueError(f"storyboard not found: scene_{resource_id}.png")

    prompt_text = _normalize_video_prompt(prompt)
    aspect_ratio = get_aspect_ratio(project, "videos")
    duration_seconds = normalize_veo_duration_seconds(payload.get("duration_seconds"))

    _, version, _, video_uri = generator.generate_video(
        prompt=prompt_text,
        resource_type="videos",
        resource_id=resource_id,
        start_image=storyboard_file,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
    )

    get_project_manager().update_scene_asset(
        project_name=project_name,
        script_filename=script_file,
        scene_id=resource_id,
        asset_type="video_clip",
        asset_path=f"videos/scene_{resource_id}.mp4",
    )

    if video_uri:
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_uri",
            asset_path=video_uri,
        )

    created_at = generator.versions.get_versions("videos", resource_id)["versions"][-1][
        "created_at"
    ]

    return {
        "version": version,
        "file_path": f"videos/scene_{resource_id}.mp4",
        "created_at": created_at,
        "resource_type": "videos",
        "resource_id": resource_id,
        "video_uri": video_uri,
    }


def execute_character_task(project_name: str, resource_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(payload.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError("prompt is required for character task")

    project = get_project_manager().load_project(project_name)
    project_path = get_project_manager().get_project_path(project_name)

    if resource_id not in project.get("characters", {}):
        raise ValueError(f"character not found: {resource_id}")

    char_data = project["characters"][resource_id]
    style = project.get("style", "")
    style_description = project.get("style_description", "")
    full_prompt = build_character_prompt(resource_id, prompt, style, style_description)

    reference_images = None
    ref_path = char_data.get("reference_image")
    if ref_path:
        full_ref = project_path / ref_path
        if full_ref.exists():
            reference_images = [full_ref]

    generator = get_media_generator(project_name)
    aspect_ratio = get_aspect_ratio(project, "characters")

    _, version = generator.generate_image(
        prompt=full_prompt,
        resource_type="characters",
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size="2K",
    )

    project["characters"][resource_id]["character_sheet"] = f"characters/{resource_id}.png"
    get_project_manager().save_project(project_name, project)

    created_at = generator.versions.get_versions("characters", resource_id)["versions"][-1][
        "created_at"
    ]

    return {
        "version": version,
        "file_path": f"characters/{resource_id}.png",
        "created_at": created_at,
        "resource_type": "characters",
        "resource_id": resource_id,
    }


def execute_clue_task(project_name: str, resource_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(payload.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError("prompt is required for clue task")

    project = get_project_manager().load_project(project_name)

    if resource_id not in project.get("clues", {}):
        raise ValueError(f"clue not found: {resource_id}")

    clue_data = project["clues"][resource_id]
    style = project.get("style", "")
    style_description = project.get("style_description", "")
    clue_type = clue_data.get("type", "prop")
    full_prompt = build_clue_prompt(resource_id, prompt, clue_type, style, style_description)

    generator = get_media_generator(project_name)
    aspect_ratio = get_aspect_ratio(project, "clues")

    _, version = generator.generate_image(
        prompt=full_prompt,
        resource_type="clues",
        resource_id=resource_id,
        aspect_ratio=aspect_ratio,
        image_size="2K",
    )

    project["clues"][resource_id]["clue_sheet"] = f"clues/{resource_id}.png"
    get_project_manager().save_project(project_name, project)

    created_at = generator.versions.get_versions("clues", resource_id)["versions"][-1][
        "created_at"
    ]

    return {
        "version": version,
        "file_path": f"clues/{resource_id}.png",
        "created_at": created_at,
        "resource_type": "clues",
        "resource_id": resource_id,
    }


def execute_storyboard_grid_task(project_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    script_file = payload.get("script_file")
    batch_id = payload.get("batch_id")
    scene_ids = payload.get("scene_ids") or []

    if not script_file:
        raise ValueError("script_file is required for storyboard_grid task")
    if not isinstance(batch_id, int):
        raise ValueError("batch_id must be an int")
    if not isinstance(scene_ids, list) or not scene_ids:
        raise ValueError("scene_ids must be a non-empty list")

    script = get_project_manager().load_script(project_name, script_file)
    project = get_project_manager().load_project(project_name) if get_project_manager().project_exists(project_name) else {}
    project_path = get_project_manager().get_project_path(project_name)

    items, id_field, char_field, clue_field = _get_items_from_script(script)
    scene_lookup = {str(item.get(id_field)): item for item in items}

    selected_scenes: List[dict] = []
    for scene_id in scene_ids:
        scene = scene_lookup.get(str(scene_id))
        if not scene:
            raise ValueError(f"scene not found for storyboard_grid: {scene_id}")
        selected_scenes.append(scene)

    all_characters = set()
    all_clues = set()
    for scene in selected_scenes:
        all_characters.update(scene.get(char_field, []))
        all_clues.update(scene.get(clue_field, []))

    reference_images: List[Path] = []
    characters = project.get("characters", {})
    clues = project.get("clues", {})

    for char_name in all_characters:
        char_sheet = characters.get(char_name, {}).get("character_sheet", "")
        if not char_sheet:
            continue
        char_path = project_path / char_sheet
        if char_path.exists():
            reference_images.append(char_path)

    for clue_name in all_clues:
        clue_sheet = clues.get(clue_name, {}).get("clue_sheet", "")
        if not clue_sheet:
            continue
        clue_path = project_path / clue_sheet
        if clue_path.exists():
            reference_images.append(clue_path)

    style = project.get("style", "")
    prompt = _build_grid_prompt(selected_scenes, style, id_field)

    client = GeminiClient(rate_limiter=rate_limiter)
    output_path = project_path / "storyboards" / f"grid_{batch_id:03d}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client.generate_image(
        prompt=prompt,
        reference_images=reference_images or None,
        aspect_ratio="16:9",
        output_path=output_path,
    )

    relative_path = f"storyboards/grid_{batch_id:03d}.png"
    for scene in selected_scenes:
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=str(scene.get(id_field)),
            asset_type="storyboard_grid",
            asset_path=relative_path,
        )

    return {
        "file_path": relative_path,
        "batch_id": batch_id,
        "scene_ids": [str(scene_id) for scene_id in scene_ids],
        "resource_type": "storyboard_grid",
        "resource_id": f"batch_{batch_id}",
    }


def execute_generation_task(task: Dict[str, Any]) -> Dict[str, Any]:
    task_type = task.get("task_type")
    project_name = task.get("project_name")
    resource_id = task.get("resource_id")
    payload = task.get("payload") or {}

    if not project_name:
        raise ValueError("task.project_name is required")

    if task_type == "storyboard":
        return execute_storyboard_task(project_name, str(resource_id), payload)
    if task_type == "video":
        return execute_video_task(project_name, str(resource_id), payload)
    if task_type == "character":
        return execute_character_task(project_name, str(resource_id), payload)
    if task_type == "clue":
        return execute_clue_task(project_name, str(resource_id), payload)
    if task_type == "storyboard_grid":
        return execute_storyboard_grid_task(project_name, payload)

    raise ValueError(f"unsupported task_type: {task_type}")
