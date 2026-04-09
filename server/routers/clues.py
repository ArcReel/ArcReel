"""
线索管理路由
"""

import asyncio
import logging

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lib import PROJECT_ROOT
from lib.i18n import get_translator
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from server.auth import CurrentUser

router = APIRouter()

# 初始化项目管理器
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


class CreateClueRequest(BaseModel):
    name: str
    clue_type: str  # 'prop' 或 'location'
    description: str
    importance: str | None = "major"  # 'major' 或 'minor'


class UpdateClueRequest(BaseModel):
    clue_type: str | None = None
    description: str | None = None
    importance: str | None = None
    clue_sheet: str | None = None


@router.post("/projects/{project_name}/clues")
async def add_clue(project_name: str, req: CreateClueRequest, _user: CurrentUser, _t=Depends(get_translator)):
    """添加线索"""
    try:

        def _sync():
            with project_change_source("webui"):
                project = get_project_manager().add_clue(
                    project_name, req.name, req.clue_type, req.description, req.importance
                )
            return {"success": True, "clue": project["clues"][req.name]}

        return await asyncio.to_thread(_sync)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/clues/{clue_name}")
async def update_clue(
    project_name: str, clue_name: str, req: UpdateClueRequest, _user: CurrentUser, _t=Depends(get_translator)
):
    """更新线索"""
    try:
        # 验证输入（纯 CPU，无需下沉到线程）
        if req.clue_type is not None and req.clue_type not in ["prop", "location"]:
            raise HTTPException(status_code=400, detail=_t("invalid_clue_type"))
        if req.importance is not None and req.importance not in ["major", "minor"]:
            raise HTTPException(status_code=400, detail=_t("invalid_importance"))

        def _sync():
            manager = get_project_manager()
            result_clue = {}

            def _mutate(project):
                if clue_name not in project.get("clues", {}):
                    raise KeyError(clue_name)
                clue = project["clues"][clue_name]
                if req.clue_type is not None:
                    clue["type"] = req.clue_type
                if req.description is not None:
                    clue["description"] = req.description
                if req.importance is not None:
                    clue["importance"] = req.importance
                if req.clue_sheet is not None:
                    clue["clue_sheet"] = req.clue_sheet
                result_clue.update(clue)

            with project_change_source("webui"):
                manager.update_project(project_name, _mutate)
            return {"success": True, "clue": result_clue}

        return await asyncio.to_thread(_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("clue_not_found", clue_name=clue_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/clues/{clue_name}")
async def delete_clue(project_name: str, clue_name: str, _user: CurrentUser, _t=Depends(get_translator)):
    """删除线索"""
    try:

        def _sync():
            manager = get_project_manager()

            def _mutate(project):
                if clue_name not in project.get("clues", {}):
                    raise KeyError(clue_name)
                del project["clues"][clue_name]

            with project_change_source("webui"):
                manager.update_project(project_name, _mutate)
            return {"success": True, "message": _t("clue_deleted", clue_name=clue_name)}

        return await asyncio.to_thread(_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("clue_not_found", clue_name=clue_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
