"""场景管理路由"""

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

pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


class CreateSceneRequest(BaseModel):
    name: str
    description: str = ""


class UpdateSceneRequest(BaseModel):
    description: str | None = None
    scene_sheet: str | None = None


@router.post("/projects/{project_name}/scenes")
async def add_scene(project_name: str, req: CreateSceneRequest, _user: CurrentUser, _t: Translator):
    """添加场景"""
    try:

        def _sync():
            with project_change_source("webui"):
                ok = get_project_manager().add_project_scene(project_name, req.name, req.description)
            if not ok:
                raise HTTPException(status_code=409, detail=_t("project_scene_already_exists", name=req.name))
            data = get_project_manager().load_project(project_name)
            return {"success": True, "scene": data["scenes"][req.name]}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/scenes/{scene_name}")
async def update_scene(
    project_name: str,
    scene_name: str,
    req: UpdateSceneRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """更新场景"""
    try:

        def _sync():
            manager = get_project_manager()
            result_scene = {}

            def _mutate(project):
                if scene_name not in project.get("scenes", {}):
                    raise KeyError(scene_name)
                scene = project["scenes"][scene_name]
                if req.description is not None:
                    scene["description"] = req.description
                if req.scene_sheet is not None:
                    scene["scene_sheet"] = req.scene_sheet
                result_scene.update(scene)

            with project_change_source("webui"):
                manager.update_project(project_name, _mutate)
            return {"success": True, "scene": result_scene}

        return await asyncio.to_thread(_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("project_scene_not_found", name=scene_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/scenes/{scene_name}")
async def delete_scene(project_name: str, scene_name: str, _user: CurrentUser, _t: Translator):
    """删除场景"""
    try:

        def _sync():
            manager = get_project_manager()

            def _mutate(project):
                if scene_name not in project.get("scenes", {}):
                    raise KeyError(scene_name)
                del project["scenes"][scene_name]

            with project_change_source("webui"):
                manager.update_project(project_name, _mutate)
            return {"success": True, "message": _t("project_scene_deleted", name=scene_name)}

        return await asyncio.to_thread(_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("project_scene_not_found", name=scene_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
