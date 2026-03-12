"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from lib import PROJECT_ROOT
from lib.gemini_client import GeminiClient, get_shared_rate_limiter
from lib.media_generator import MediaGenerator
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from lib.prompt_builders import build_character_prompt, build_clue_prompt
from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    video_prompt_to_yaml,
)
from lib.storyboard_sequence import (
    build_previous_storyboard_reference,
    find_storyboard_item,
    get_storyboard_items,
    resolve_previous_storyboard_path,
)
from lib.thumbnail import extract_video_thumbnail


pm = ProjectManager(PROJECT_ROOT / "projects")
rate_limiter = get_shared_rate_limiter()
logger = logging.getLogger(__name__)


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
    previous_storyboard_path: Optional[Path] = None,
) -> Optional[List[object]]:
    reference_images: List[object] = []

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

    if previous_storyboard_path and previous_storyboard_path.exists():
        reference_images.append(
            build_previous_storyboard_reference(previous_storyboard_path)
        )

    return reference_images or None


def _resolve_script_episode(project_name: str, script_file: str | None) -> int | None:
    if not script_file:
        return None
    try:
        script = get_project_manager().load_script(project_name, script_file)
    except Exception:
        return None

    episode = script.get("episode")
    if isinstance(episode, int):
        return episode
    return None


def _compute_affected_fingerprints(
    project_name: str, task_type: str, resource_id: str
) -> Dict[str, int]:
    """计算受影响文件的 mtime 指纹"""
    try:
        project_path = get_project_manager().get_project_path(project_name)
    except Exception:
        return {}

    paths: list[tuple[str, Path]] = []

    if task_type == "storyboard":
        paths.append((
            f"storyboards/scene_{resource_id}.png",
            project_path / "storyboards" / f"scene_{resource_id}.png",
        ))
    elif task_type == "video":
        paths.append((
            f"videos/scene_{resource_id}.mp4",
            project_path / "videos" / f"scene_{resource_id}.mp4",
        ))
        paths.append((
            f"thumbnails/scene_{resource_id}.jpg",
            project_path / "thumbnails" / f"scene_{resource_id}.jpg",
        ))
    elif task_type == "character":
        paths.append((
            f"characters/{resource_id}.png",
            project_path / "characters" / f"{resource_id}.png",
        ))
    elif task_type == "clue":
        paths.append((
            f"clues/{resource_id}.png",
            project_path / "clues" / f"{resource_id}.png",
        ))

    result: Dict[str, int] = {}
    for rel, abs_path in paths:
        if abs_path.exists():
            result[rel] = abs_path.stat().st_mtime_ns

    return result


# (entity_type, action, label_tpl, include_script_episode)
_TASK_CHANGE_SPECS: Dict[str, tuple] = {
    "storyboard": ("segment",   "storyboard_ready", "分镜「{}」",    True),
    "video":      ("segment",   "video_ready",      "分镜「{}」",    True),
    "character":  ("character", "updated",          "角色「{}」设计图", False),
    "clue":       ("clue",      "updated",          "线索「{}」设计图", False),
}


def _emit_generation_success_batch(
    *,
    task_type: str,
    project_name: str,
    resource_id: str,
    payload: Dict[str, Any],
) -> None:
    spec = _TASK_CHANGE_SPECS.get(task_type)
    if spec is None:
        return

    entity_type, action, label_tpl, include_script_episode = spec
    asset_fingerprints = _compute_affected_fingerprints(project_name, task_type, resource_id)

    change: Dict[str, Any] = {
        "entity_type": entity_type,
        "action": action,
        "entity_id": resource_id,
        "label": label_tpl.format(resource_id),
        "focus": None,
        "important": True,
        "asset_fingerprints": asset_fingerprints,
    }
    if include_script_episode:
        script_file = str(payload.get("script_file") or "") or None
        change["script_file"] = script_file
        change["episode"] = _resolve_script_episode(project_name, script_file)

    try:
        emit_project_change_batch(project_name, [change], source="worker")
    except Exception:
        logger.exception(
            "发送生成完成项目事件失败 project=%s task_type=%s resource_id=%s",
            project_name,
            task_type,
            resource_id,
        )


async def execute_storyboard_task(project_name: str, resource_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for storyboard task")

    prompt = payload.get("prompt")
    if prompt is None:
        raise ValueError("prompt is required for storyboard task")

    project = get_project_manager().load_project(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    script = get_project_manager().load_script(project_name, script_file)
    items, id_field, char_field, clue_field = get_storyboard_items(script)

    resolved = find_storyboard_item(items, id_field, resource_id)
    if resolved is None:
        raise ValueError(f"scene/segment not found: {resource_id}")
    target_item, _ = resolved

    previous_storyboard_path = resolve_previous_storyboard_path(
        project_path,
        items,
        id_field,
        resource_id,
    )

    prompt_text = _normalize_storyboard_prompt(prompt, project.get("style", ""))
    reference_images = _collect_reference_images(
        project,
        project_path,
        target_item,
        char_field=char_field,
        clue_field=clue_field,
        extra_reference_images=payload.get("extra_reference_images") or [],
        previous_storyboard_path=previous_storyboard_path,
    )

    generator = get_media_generator(project_name)
    aspect_ratio = get_aspect_ratio(project, "storyboards")

    _, version = await generator.generate_image_async(
        prompt=prompt_text,
        resource_type="storyboards",
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size="1K",
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


async def execute_video_task(project_name: str, resource_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
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

    _, version, _, video_uri = await generator.generate_video_async(
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

    # 提取视频首帧作为缩略图
    video_file = project_path / f"videos/scene_{resource_id}.mp4"
    thumbnail_file = project_path / f"thumbnails/scene_{resource_id}.jpg"
    if await extract_video_thumbnail(video_file, thumbnail_file):
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_thumbnail",
            asset_path=f"thumbnails/scene_{resource_id}.jpg",
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


async def execute_character_task(project_name: str, resource_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
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

    _, version = await generator.generate_image_async(
        prompt=full_prompt,
        resource_type="characters",
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size="1K",
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


async def execute_clue_task(project_name: str, resource_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
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

    _, version = await generator.generate_image_async(
        prompt=full_prompt,
        resource_type="clues",
        resource_id=resource_id,
        aspect_ratio=aspect_ratio,
        image_size="1K",
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


_TASK_EXECUTORS = {
    "storyboard": execute_storyboard_task,
    "video":      execute_video_task,
    "character":  execute_character_task,
    "clue":       execute_clue_task,
}


async def execute_generation_task(task: Dict[str, Any]) -> Dict[str, Any]:
    task_type = task.get("task_type")
    project_name = task.get("project_name")
    resource_id = str(task.get("resource_id"))
    payload = task.get("payload") or {}

    if not project_name:
        raise ValueError("task.project_name is required")

    executor = _TASK_EXECUTORS.get(task_type)
    if executor is None:
        raise ValueError(f"unsupported task_type: {task_type}")

    with project_change_source("worker"):
        result = await executor(project_name, resource_id, payload)
        _emit_generation_success_batch(
            task_type=task_type,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
        )
        return result
