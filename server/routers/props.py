"""道具管理路由"""

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


class CreatePropRequest(BaseModel):
    name: str
    description: str = ""


class UpdatePropRequest(BaseModel):
    description: str | None = None
    prop_sheet: str | None = None


@router.post("/projects/{project_name}/props")
async def add_prop(project_name: str, req: CreatePropRequest, _user: CurrentUser, _t: Translator):
    """添加道具"""
    try:

        def _sync():
            with project_change_source("webui"):
                ok = get_project_manager().add_prop(project_name, req.name, req.description)
            if not ok:
                raise HTTPException(status_code=409, detail=_t("prop_already_exists", name=req.name))
            data = get_project_manager().load_project(project_name)
            return {"success": True, "prop": data["props"][req.name]}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/props/{prop_name}")
async def update_prop(
    project_name: str,
    prop_name: str,
    req: UpdatePropRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """更新道具"""
    try:

        def _sync():
            manager = get_project_manager()
            result_prop = {}

            def _mutate(project):
                if prop_name not in project.get("props", {}):
                    raise KeyError(prop_name)
                prop = project["props"][prop_name]
                if req.description is not None:
                    prop["description"] = req.description
                if req.prop_sheet is not None:
                    prop["prop_sheet"] = req.prop_sheet
                result_prop.update(prop)

            with project_change_source("webui"):
                manager.update_project(project_name, _mutate)
            return {"success": True, "prop": result_prop}

        return await asyncio.to_thread(_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("prop_not_found", name=prop_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/props/{prop_name}")
async def delete_prop(project_name: str, prop_name: str, _user: CurrentUser, _t: Translator):
    """删除道具"""
    try:

        def _sync():
            manager = get_project_manager()

            def _mutate(project):
                if prop_name not in project.get("props", {}):
                    raise KeyError(prop_name)
                del project["props"][prop_name]

            with project_change_source("webui"):
                manager.update_project(project_name, _mutate)
            return {"success": True, "message": _t("prop_deleted", name=prop_name)}

        return await asyncio.to_thread(_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("prop_not_found", name=prop_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
