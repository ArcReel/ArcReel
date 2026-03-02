"""
生成 API 路由

处理分镜图、视频、人物图、线索图的生成请求。
使用 MediaGenerator 中间层自动处理版本管理。
"""

import asyncio
import logging
import os
import threading
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from lib import PROJECT_ROOT
from lib.gemini_client import get_shared_rate_limiter
from lib.media_generator import MediaGenerator
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from lib.prompt_builders import build_character_prompt, build_clue_prompt
from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    video_prompt_to_yaml,
)

router = APIRouter()

# 初始化管理器
pm = ProjectManager(PROJECT_ROOT / "projects")

# 初始化限流器（共享给 MediaGenerator）
rate_limiter = get_shared_rate_limiter()


def get_project_manager() -> ProjectManager:
    return pm

_video_semaphore: Optional[asyncio.Semaphore] = None
_video_semaphore_lock = threading.Lock()


def _read_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_video_semaphore() -> asyncio.Semaphore:
    """
    获取进程内共享的“在途视频生成”并发限制。

    注意：如果 uvicorn 使用多 worker 进程，该 semaphore 为“每进程一份”。
    """
    global _video_semaphore
    if _video_semaphore is not None:
        return _video_semaphore

    with _video_semaphore_lock:
        if _video_semaphore is not None:
            return _video_semaphore

        max_workers = _read_int_env("VIDEO_MAX_WORKERS", 2)
        if max_workers < 1:
            max_workers = 1

        _video_semaphore = asyncio.Semaphore(max_workers)
        return _video_semaphore


def get_media_generator(project_name: str) -> MediaGenerator:
    """获取项目的媒体生成器（带自动版本管理）"""
    project_path = get_project_manager().get_project_path(project_name)
    return MediaGenerator(project_path, rate_limiter=rate_limiter)


def get_aspect_ratio(project: dict, resource_type: str) -> str:
    """
    根据项目配置获取画面比例

    Args:
        project: 项目元数据
        resource_type: 资源类型 (storyboards, videos, characters, clues)

    Returns:
        画面比例字符串
    """
    content_mode = project.get("content_mode", "narration")

    # 检查自定义比例
    custom_ratios = project.get("aspect_ratio", {})
    if resource_type in custom_ratios:
        return custom_ratios[resource_type]

    # 默认比例
    if resource_type == "characters":
        return "3:4"  # 人物设计图使用 3:4 竖版
    elif resource_type == "clues":
        return "16:9"  # 线索设计图保持 16:9
    elif content_mode == "narration":
        return "9:16"  # 说书模式竖屏
    else:
        return "16:9"  # 剧集模式横屏


def normalize_veo_duration_seconds(duration_seconds: Optional[int]) -> str:
    """
    Veo 视频生成仅支持 4/6/8 秒，将输入值归一化到最近的可用值（向上取整，最大 8）。
    """
    try:
        value = int(duration_seconds) if duration_seconds is not None else 4
    except (TypeError, ValueError):
        value = 4

    if value <= 4:
        return "4"
    if value <= 6:
        return "6"
    return "8"


# ==================== 请求模型 ====================


class GenerateStoryboardRequest(BaseModel):
    prompt: Union[str, dict]
    script_file: str


class GenerateVideoRequest(BaseModel):
    prompt: Union[str, dict]
    script_file: str
    duration_seconds: Optional[int] = 4


class GenerateCharacterRequest(BaseModel):
    prompt: str


class GenerateClueRequest(BaseModel):
    prompt: str


# ==================== 分镜图生成 ====================


@router.post("/projects/{project_name}/generate/storyboard/{segment_id}")
async def generate_storyboard(
    project_name: str, segment_id: str, req: GenerateStoryboardRequest
):
    """
    生成分镜图（首次生成或重新生成）

    使用 MediaGenerator 自动处理版本管理。
    """
    try:
        with project_change_source("webui"):
            project = get_project_manager().load_project(project_name)
            project_path = get_project_manager().get_project_path(project_name)
            generator = get_media_generator(project_name)

            # 获取画面比例
            aspect_ratio = get_aspect_ratio(project, "storyboards")

            # 加载剧本获取参考图
            script = get_project_manager().load_script(project_name, req.script_file)
            content_mode = script.get(
                "content_mode", project.get("content_mode", "narration")
            )

            # 查找 segment/scene 获取参考角色和线索
            items = script.get("segments" if content_mode == "narration" else "scenes", [])
            id_field = "segment_id" if content_mode == "narration" else "scene_id"
            target_item = None
            for item in items:
                if item.get(id_field) == segment_id:
                    target_item = item
                    break

            if not target_item:
                raise HTTPException(
                    status_code=404, detail=f"片段/场景 '{segment_id}' 不存在"
                )

            # 收集参考图
            reference_images = []
            chars_field = (
                "characters_in_segment"
                if content_mode == "narration"
                else "characters_in_scene"
            )
            clues_field = (
                "clues_in_segment" if content_mode == "narration" else "clues_in_scene"
            )

            for char_name in target_item.get(chars_field, []):
                char_data = project.get("characters", {}).get(char_name, {})
                sheet = char_data.get("character_sheet")
                if sheet:
                    sheet_path = project_path / sheet
                    if sheet_path.exists():
                        reference_images.append(sheet_path)

            for clue_name in target_item.get(clues_field, []):
                clue_data = project.get("clues", {}).get(clue_name, {})
                sheet = clue_data.get("clue_sheet")
                if sheet:
                    sheet_path = project_path / sheet
                    if sheet_path.exists():
                        reference_images.append(sheet_path)

            # 兼容 prompt 旧格式（字符串）与新格式（结构化对象）
            prompt_text: str
            if isinstance(req.prompt, str):
                prompt_text = req.prompt
            elif isinstance(req.prompt, dict):
                if not is_structured_image_prompt(req.prompt):
                    raise HTTPException(
                        status_code=400,
                        detail="prompt 必须是字符串或包含 scene/composition 的对象",
                    )
                scene_text = str(req.prompt.get("scene", "")).strip()
                if not scene_text:
                    raise HTTPException(status_code=400, detail="prompt.scene 不能为空")
                composition = (
                    req.prompt.get("composition")
                    if isinstance(req.prompt.get("composition"), dict)
                    else {}
                )
                normalized_prompt = {
                    "scene": scene_text,
                    "composition": {
                        "shot_type": str(composition.get("shot_type") or "Medium Shot"),
                        "lighting": str(composition.get("lighting", "") or ""),
                        "ambiance": str(composition.get("ambiance", "") or ""),
                    },
                }
                prompt_text = image_prompt_to_yaml(
                    normalized_prompt, project.get("style", "")
                )
            else:
                raise HTTPException(status_code=400, detail="prompt 必须是字符串或对象")

            # 使用 MediaGenerator 生成图片（自动处理版本管理）
            _, new_version = await generator.generate_image_async(
                prompt=prompt_text,
                resource_type="storyboards",
                resource_id=segment_id,
                reference_images=reference_images if reference_images else None,
                aspect_ratio=aspect_ratio,
                image_size="1K",
            )

            # 更新剧本中的 generated_assets
            get_project_manager().update_scene_asset(
                project_name=project_name,
                script_filename=req.script_file,
                scene_id=segment_id,
                asset_type="storyboard_image",
                asset_path=f"storyboards/scene_{segment_id}.png",
            )

        return {
            "success": True,
            "version": new_version,
            "file_path": f"storyboards/scene_{segment_id}.png",
            "created_at": generator.versions.get_versions("storyboards", segment_id)[
                "versions"
            ][-1]["created_at"],
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 视频生成 ====================


@router.post("/projects/{project_name}/generate/video/{segment_id}")
async def generate_video(project_name: str, segment_id: str, req: GenerateVideoRequest):
    """
    生成视频（首次生成或重新生成）

    使用 MediaGenerator 自动处理版本管理。
    需要先有分镜图作为起始帧。
    """
    sem = _get_video_semaphore()
    await sem.acquire()
    try:
        with project_change_source("webui"):
            project = get_project_manager().load_project(project_name)
            project_path = get_project_manager().get_project_path(project_name)
            generator = get_media_generator(project_name)

            # 获取画面比例
            aspect_ratio = get_aspect_ratio(project, "videos")
            duration_seconds = normalize_veo_duration_seconds(req.duration_seconds)

            # 检查分镜图是否存在
            storyboard_file = project_path / "storyboards" / f"scene_{segment_id}.png"
            if not storyboard_file.exists():
                raise HTTPException(
                    status_code=400, detail=f"请先生成分镜图 scene_{segment_id}.png"
                )

            # 兼容 prompt 旧格式（字符串）与新格式（结构化对象）
            prompt_text: str
            if isinstance(req.prompt, str):
                prompt_text = req.prompt
            elif isinstance(req.prompt, dict):
                if not is_structured_video_prompt(req.prompt):
                    raise HTTPException(
                        status_code=400,
                        detail="prompt 必须是字符串或包含 action/camera_motion 的对象",
                    )
                action_text = str(req.prompt.get("action", "")).strip()
                if not action_text:
                    raise HTTPException(status_code=400, detail="prompt.action 不能为空")
                dialogue = req.prompt.get("dialogue", [])
                if dialogue is None:
                    dialogue = []
                if not isinstance(dialogue, list):
                    raise HTTPException(
                        status_code=400, detail="prompt.dialogue 必须是数组"
                    )
                normalized_dialogue = []
                for item in dialogue:
                    if not isinstance(item, dict):
                        continue
                    speaker = str(item.get("speaker", "") or "").strip()
                    line = str(item.get("line", "") or "").strip()
                    if speaker or line:
                        normalized_dialogue.append({"speaker": speaker, "line": line})

                normalized_prompt: dict[str, Any] = {
                    "action": action_text,
                    "camera_motion": str(req.prompt.get("camera_motion", "") or ""),
                    "ambiance_audio": str(req.prompt.get("ambiance_audio", "") or ""),
                    "dialogue": normalized_dialogue,
                }
                if not normalized_prompt["camera_motion"]:
                    normalized_prompt["camera_motion"] = "Static"
                prompt_text = video_prompt_to_yaml(normalized_prompt)
            else:
                raise HTTPException(status_code=400, detail="prompt 必须是字符串或对象")

            # 使用 MediaGenerator 生成视频（自动处理版本管理）
            _, new_version, _, video_uri = await generator.generate_video_async(
                prompt=prompt_text,
                resource_type="videos",
                resource_id=segment_id,
                start_image=storyboard_file,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
            )

            # 更新剧本中的 generated_assets
            get_project_manager().update_scene_asset(
                project_name=project_name,
                script_filename=req.script_file,
                scene_id=segment_id,
                asset_type="video_clip",
                asset_path=f"videos/scene_{segment_id}.mp4",
            )

            # 保存 video_uri 用于后续扩展
            if video_uri:
                get_project_manager().update_scene_asset(
                    project_name=project_name,
                    script_filename=req.script_file,
                    scene_id=segment_id,
                    asset_type="video_uri",
                    asset_path=video_uri,
                )

        return {
            "success": True,
            "version": new_version,
            "file_path": f"videos/scene_{segment_id}.mp4",
            "created_at": generator.versions.get_versions("videos", segment_id)[
                "versions"
            ][-1]["created_at"],
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sem.release()


# ==================== 人物设计图生成 ====================


@router.post("/projects/{project_name}/generate/character/{char_name}")
async def generate_character(
    project_name: str, char_name: str, req: GenerateCharacterRequest
):
    """
    生成人物设计图（首次生成或重新生成）

    使用 MediaGenerator 自动处理版本管理。
    若人物有 reference_image，自动作为参考图传入。
    """
    try:
        with project_change_source("webui"):
            project = get_project_manager().load_project(project_name)
            project_path = get_project_manager().get_project_path(project_name)
            generator = get_media_generator(project_name)

            # 检查人物是否存在
            if char_name not in project.get("characters", {}):
                raise HTTPException(status_code=404, detail=f"人物 '{char_name}' 不存在")

            char_data = project["characters"][char_name]

            # 获取画面比例（人物设计图 3:4）
            aspect_ratio = get_aspect_ratio(project, "characters")

            # 使用共享库构建 Prompt（确保与 Skill 侧一致）
            style = project.get("style", "")
            style_description = project.get("style_description", "")
            full_prompt = build_character_prompt(
                char_name, req.prompt, style, style_description
            )

            # 读取参考图（如果存在）
            reference_images = None
            ref_path = char_data.get("reference_image")
            if ref_path:
                ref_full_path = project_path / ref_path
                if ref_full_path.exists():
                    reference_images = [ref_full_path]

            # 使用 MediaGenerator 生成图片（自动处理版本管理）
            _, new_version = await generator.generate_image_async(
                prompt=full_prompt,
                resource_type="characters",
                resource_id=char_name,
                reference_images=reference_images,
                aspect_ratio=aspect_ratio,
                image_size="1K",
            )

            # 更新 project.json 中的 character_sheet
            project["characters"][char_name]["character_sheet"] = (
                f"characters/{char_name}.png"
            )
            get_project_manager().save_project(project_name, project)

        return {
            "success": True,
            "version": new_version,
            "file_path": f"characters/{char_name}.png",
            "created_at": generator.versions.get_versions("characters", char_name)[
                "versions"
            ][-1]["created_at"],
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 线索设计图生成 ====================


@router.post("/projects/{project_name}/generate/clue/{clue_name}")
async def generate_clue(project_name: str, clue_name: str, req: GenerateClueRequest):
    """
    生成线索设计图（首次生成或重新生成）

    使用 MediaGenerator 自动处理版本管理。
    """
    try:
        with project_change_source("webui"):
            project = get_project_manager().load_project(project_name)
            generator = get_media_generator(project_name)

            # 检查线索是否存在
            if clue_name not in project.get("clues", {}):
                raise HTTPException(status_code=404, detail=f"线索 '{clue_name}' 不存在")

            clue_data = project["clues"][clue_name]

            # 获取画面比例（设计图始终 16:9）
            aspect_ratio = get_aspect_ratio(project, "clues")

            # 使用共享库构建 Prompt（确保与 Skill 侧一致）
            style = project.get("style", "")
            style_description = project.get("style_description", "")
            clue_type = clue_data.get("type", "prop")
            full_prompt = build_clue_prompt(
                clue_name, req.prompt, clue_type, style, style_description
            )

            # 使用 MediaGenerator 生成图片（自动处理版本管理）
            _, new_version = await generator.generate_image_async(
                prompt=full_prompt,
                resource_type="clues",
                resource_id=clue_name,
                aspect_ratio=aspect_ratio,
                image_size="1K",
            )

            # 更新 project.json 中的 clue_sheet
            project["clues"][clue_name]["clue_sheet"] = f"clues/{clue_name}.png"
            get_project_manager().save_project(project_name, project)

        return {
            "success": True,
            "version": new_version,
            "file_path": f"clues/{clue_name}.png",
            "created_at": generator.versions.get_versions("clues", clue_name)[
                "versions"
            ][-1]["created_at"],
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
