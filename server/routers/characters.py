"""
角色管理路由
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib import PROJECT_ROOT
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from server.auth import CurrentUser

router = APIRouter()

# 初始化项目管理器
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


class CreateCharacterRequest(BaseModel):
    name: str
    description: str
    voice_style: str | None = ""


class UpdateCharacterRequest(BaseModel):
    description: str | None = None
    voice_style: str | None = None
    character_sheet: str | None = None
    reference_image: str | None = None


@router.post("/projects/{project_name}/characters")
async def add_character(project_name: str, req: CreateCharacterRequest, _user: CurrentUser):
    """添加角色"""
    try:

        def _sync():
            with project_change_source("webui"):
                project = get_project_manager().add_project_character(
                    project_name, req.name, req.description, req.voice_style
                )
            return {"success": True, "character": project["characters"][req.name]}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/characters/{char_name}")
async def update_character(
    project_name: str,
    char_name: str,
    req: UpdateCharacterRequest,
    _user: CurrentUser,
):
    """更新角色"""
    try:

        def _sync():
            manager = get_project_manager()
            project = manager.load_project(project_name)

            if char_name not in project["characters"]:
                raise HTTPException(status_code=404, detail=f"角色 '{char_name}' 不存在")

            char = project["characters"][char_name]
            if req.description is not None:
                char["description"] = req.description
            if req.voice_style is not None:
                char["voice_style"] = req.voice_style
            if req.character_sheet is not None:
                char["character_sheet"] = req.character_sheet
            if req.reference_image is not None:
                char["reference_image"] = req.reference_image

            with project_change_source("webui"):
                manager.save_project(project_name, project)
            return {"success": True, "character": char}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/characters/{char_name}")
async def delete_character(project_name: str, char_name: str, _user: CurrentUser):
    """删除角色"""
    try:

        def _sync():
            manager = get_project_manager()
            project = manager.load_project(project_name)

            if char_name not in project["characters"]:
                raise HTTPException(status_code=404, detail=f"角色 '{char_name}' 不存在")

            del project["characters"][char_name]
            with project_change_source("webui"):
                manager.save_project(project_name, project)
            return {"success": True, "message": f"角色 '{char_name}' 已删除"}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
