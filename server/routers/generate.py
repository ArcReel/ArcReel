"""
生成 API 路由

处理分镜图、视频、人物图、线索图的生成请求。
所有生成请求入队到 GenerationQueue，由 GenerationWorker 异步执行。
"""

import logging
from typing import Optional, Union

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib import PROJECT_ROOT
from lib.generation_queue import get_generation_queue
from lib.project_manager import ProjectManager
from lib.prompt_utils import (
    is_structured_image_prompt,
    is_structured_video_prompt,
)
from lib.storyboard_sequence import (
    find_storyboard_item,
    get_storyboard_items,
)
from server.auth import CurrentUser

router = APIRouter()

# 初始化管理器
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


# ==================== 请求模型 ====================


class GenerateStoryboardRequest(BaseModel):
    prompt: Union[str, dict]
    script_file: str


class GenerateVideoRequest(BaseModel):
    prompt: Union[str, dict]
    script_file: str
    duration_seconds: Optional[int] = 4
    seed: Optional[int] = None


class GenerateCharacterRequest(BaseModel):
    prompt: str


class GenerateClueRequest(BaseModel):
    prompt: str


_LEGACY_PROVIDER_NAMES: dict[str, str] = {
    "gemini": "gemini-aistudio",
    "aistudio": "gemini-aistudio",
    "vertex": "gemini-vertex",
}


def _normalize_provider_id(raw: str) -> str:
    """将旧格式 provider 名称归一化为标准 provider_id。"""
    return _LEGACY_PROVIDER_NAMES.get(raw, raw)


def _snapshot_image_backend(project_name: str) -> dict:
    """快照图片供应商配置，返回可合并到 payload 的字典。

    优先级：项目级 image_backend > 系统级 default_image_backend。
    """
    project = get_project_manager().load_project(project_name)
    project_image_backend = project.get("image_backend")  # 格式: "provider_id/model"
    if project_image_backend and "/" in project_image_backend:
        image_provider, image_model = project_image_backend.split("/", 1)
    elif project_image_backend:
        image_provider = _normalize_provider_id(project_image_backend)
        image_model = ""
    else:
        return {}  # 无项目级覆盖，使用全局默认
    return {
        "image_provider": image_provider,
        "image_model": image_model,
    }


# ==================== 分镜图生成 ====================


@router.post("/projects/{project_name}/generate/storyboard/{segment_id}")
async def generate_storyboard(
    project_name: str, segment_id: str, req: GenerateStoryboardRequest,
    _user: CurrentUser,
):
    """
    提交分镜图生成任务到队列，立即返回 task_id。

    生成由 GenerationWorker 异步执行，状态通过 SSE 推送。
    """
    try:
        get_project_manager().load_project(project_name)

        # 加载剧本验证片段存在
        script = get_project_manager().load_script(project_name, req.script_file)
        items, id_field, _, _ = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, segment_id)
        if resolved is None:
            raise HTTPException(
                status_code=404, detail=f"片段/场景 '{segment_id}' 不存在"
            )

        # 验证 prompt 格式
        if isinstance(req.prompt, dict):
            if not is_structured_image_prompt(req.prompt):
                raise HTTPException(
                    status_code=400,
                    detail="prompt 必须是字符串或包含 scene/composition 的对象",
                )
            scene_text = str(req.prompt.get("scene", "")).strip()
            if not scene_text:
                raise HTTPException(status_code=400, detail="prompt.scene 不能为空")
        elif not isinstance(req.prompt, str):
            raise HTTPException(status_code=400, detail="prompt 必须是字符串或对象")

        # 入队
        queue = get_generation_queue()
        image_snapshot = _snapshot_image_backend(project_name)
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type="storyboard",
            media_type="image",
            resource_id=segment_id,
            script_file=req.script_file,
            payload={
                "prompt": req.prompt,
                "script_file": req.script_file,
                **image_snapshot,
            },
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": f"分镜「{segment_id}」生成任务已提交",
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
async def generate_video(project_name: str, segment_id: str, req: GenerateVideoRequest, _user: CurrentUser):
    """
    提交视频生成任务到队列，立即返回 task_id。

    需要先有分镜图作为起始帧。生成由 GenerationWorker 异步执行。
    """
    try:
        get_project_manager().load_project(project_name)
        project_path = get_project_manager().get_project_path(project_name)

        # 检查分镜图是否存在
        storyboard_file = project_path / "storyboards" / f"scene_{segment_id}.png"
        if not storyboard_file.exists():
            raise HTTPException(
                status_code=400, detail=f"请先生成分镜图 scene_{segment_id}.png"
            )

        # 验证 prompt 格式
        if isinstance(req.prompt, dict):
            if not is_structured_video_prompt(req.prompt):
                raise HTTPException(
                    status_code=400,
                    detail="prompt 必须是字符串或包含 action/camera_motion 的对象",
                )
            action_text = str(req.prompt.get("action", "")).strip()
            if not action_text:
                raise HTTPException(status_code=400, detail="prompt.action 不能为空")
            dialogue = req.prompt.get("dialogue", [])
            if dialogue is not None and not isinstance(dialogue, list):
                raise HTTPException(
                    status_code=400, detail="prompt.dialogue 必须是数组"
                )
        elif not isinstance(req.prompt, str):
            raise HTTPException(status_code=400, detail="prompt 必须是字符串或对象")

        # 入队（provider 由服务层根据配置自动解析，调用方无需传递）
        queue = get_generation_queue()
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type="video",
            media_type="video",
            resource_id=segment_id,
            script_file=req.script_file,
            payload={
                "prompt": req.prompt,
                "script_file": req.script_file,
                "duration_seconds": req.duration_seconds,
                "seed": req.seed,
            },
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": f"视频「{segment_id}」生成任务已提交",
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 人物设计图生成 ====================


@router.post("/projects/{project_name}/generate/character/{char_name}")
async def generate_character(
    project_name: str, char_name: str, req: GenerateCharacterRequest,
    _user: CurrentUser,
):
    """
    提交人物设计图生成任务到队列，立即返回 task_id。
    """
    try:
        project = get_project_manager().load_project(project_name)

        # 检查人物是否存在
        if char_name not in project.get("characters", {}):
            raise HTTPException(status_code=404, detail=f"人物 '{char_name}' 不存在")

        # 入队
        queue = get_generation_queue()
        image_snapshot = _snapshot_image_backend(project_name)
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type="character",
            media_type="image",
            resource_id=char_name,
            payload={
                "prompt": req.prompt,
                **image_snapshot,
            },
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": f"角色「{char_name}」设计图生成任务已提交",
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
async def generate_clue(project_name: str, clue_name: str, req: GenerateClueRequest, _user: CurrentUser):
    """
    提交线索设计图生成任务到队列，立即返回 task_id。
    """
    try:
        project = get_project_manager().load_project(project_name)

        # 检查线索是否存在
        if clue_name not in project.get("clues", {}):
            raise HTTPException(status_code=404, detail=f"线索 '{clue_name}' 不存在")

        # 入队
        queue = get_generation_queue()
        image_snapshot = _snapshot_image_backend(project_name)
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type="clue",
            media_type="image",
            resource_id=clue_name,
            payload={
                "prompt": req.prompt,
                **image_snapshot,
            },
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": f"线索「{clue_name}」设计图生成任务已提交",
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
