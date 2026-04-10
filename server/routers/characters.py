"""
角色管理路由
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib import PROJECT_ROOT
from lib.i18n import Translator
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
async def add_character(project_name: str, req: CreateCharacterRequest, _user: CurrentUser, _t: Translator):
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
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
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
    _t: Translator,
):
    """更新角色"""
    try:

        def _sync():
            manager = get_project_manager()
            result_char = {}

            def _mutate(project):
                if char_name not in project.get("characters", {}):
                    raise KeyError(char_name)
                char = project["characters"][char_name]
                if req.description is not None:
                    char["description"] = req.description
                if req.voice_style is not None:
                    char["voice_style"] = req.voice_style
                if req.character_sheet is not None:
                    char["character_sheet"] = req.character_sheet
                if req.reference_image is not None:
                    char["reference_image"] = req.reference_image
                result_char.update(char)

            with project_change_source("webui"):
                manager.update_project(project_name, _mutate)
            return {"success": True, "character": result_char}

        return await asyncio.to_thread(_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("character_not_found", char_name=char_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/characters/{char_name}")
async def delete_character(project_name: str, char_name: str, _user: CurrentUser, _t: Translator):
    """删除角色"""
    try:

        def _sync():
            manager = get_project_manager()

            def _mutate(project):
                if char_name not in project.get("characters", {}):
                    raise KeyError(char_name)
                del project["characters"][char_name]

            with project_change_source("webui"):
                manager.update_project(project_name, _mutate)
            return {"success": True, "message": _t("character_deleted", char_name=char_name)}

        return await asyncio.to_thread(_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("character_not_found", char_name=char_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
