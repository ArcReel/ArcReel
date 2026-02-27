"""
人物管理路由
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib import PROJECT_ROOT
from lib.project_manager import ProjectManager

router = APIRouter()

# 初始化项目管理器
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


class CreateCharacterRequest(BaseModel):
    name: str
    description: str
    voice_style: Optional[str] = ""


class UpdateCharacterRequest(BaseModel):
    description: Optional[str] = None
    voice_style: Optional[str] = None
    character_sheet: Optional[str] = None
    reference_image: Optional[str] = None


@router.post("/projects/{project_name}/characters")
async def add_character(project_name: str, req: CreateCharacterRequest):
    """添加人物"""
    try:
        project = get_project_manager().add_project_character(
            project_name, req.name, req.description, req.voice_style
        )
        return {"success": True, "character": project["characters"][req.name]}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/characters/{char_name}")
async def update_character(
    project_name: str, char_name: str, req: UpdateCharacterRequest
):
    """更新人物"""
    try:
        manager = get_project_manager()
        project = manager.load_project(project_name)

        if char_name not in project["characters"]:
            raise HTTPException(status_code=404, detail=f"人物 '{char_name}' 不存在")

        char = project["characters"][char_name]
        if req.description is not None:
            char["description"] = req.description
        if req.voice_style is not None:
            char["voice_style"] = req.voice_style
        if req.character_sheet is not None:
            char["character_sheet"] = req.character_sheet
        if req.reference_image is not None:
            char["reference_image"] = req.reference_image

        manager.save_project(project_name, project)
        return {"success": True, "character": char}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/characters/{char_name}")
async def delete_character(project_name: str, char_name: str):
    """删除人物"""
    try:
        manager = get_project_manager()
        project = manager.load_project(project_name)

        if char_name not in project["characters"]:
            raise HTTPException(status_code=404, detail=f"人物 '{char_name}' 不存在")

        del project["characters"][char_name]
        manager.save_project(project_name, project)
        return {"success": True, "message": f"人物 '{char_name}' 已删除"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
