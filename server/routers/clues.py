"""
线索管理路由
"""

import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.project_manager import ProjectManager

router = APIRouter()

# 初始化项目管理器
project_root = Path(__file__).parent.parent.parent.parent
pm = ProjectManager(project_root / "projects")


def get_project_manager() -> ProjectManager:
    return pm


class CreateClueRequest(BaseModel):
    name: str
    clue_type: str  # 'prop' 或 'location'
    description: str
    importance: Optional[str] = "major"  # 'major' 或 'minor'


class UpdateClueRequest(BaseModel):
    clue_type: Optional[str] = None
    description: Optional[str] = None
    importance: Optional[str] = None
    clue_sheet: Optional[str] = None


@router.post("/projects/{project_name}/clues")
async def add_clue(project_name: str, req: CreateClueRequest):
    """添加线索"""
    try:
        project = get_project_manager().add_clue(
            project_name,
            req.name,
            req.clue_type,
            req.description,
            req.importance
        )
        return {"success": True, "clue": project["clues"][req.name]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/clues/{clue_name}")
async def update_clue(project_name: str, clue_name: str, req: UpdateClueRequest):
    """更新线索"""
    try:
        manager = get_project_manager()
        project = manager.load_project(project_name)

        if clue_name not in project["clues"]:
            raise HTTPException(status_code=404, detail=f"线索 '{clue_name}' 不存在")

        clue = project["clues"][clue_name]
        if req.clue_type is not None:
            if req.clue_type not in ["prop", "location"]:
                raise HTTPException(status_code=400, detail="线索类型必须是 'prop' 或 'location'")
            clue["type"] = req.clue_type
        if req.description is not None:
            clue["description"] = req.description
        if req.importance is not None:
            if req.importance not in ["major", "minor"]:
                raise HTTPException(status_code=400, detail="重要程度必须是 'major' 或 'minor'")
            clue["importance"] = req.importance
        if req.clue_sheet is not None:
            clue["clue_sheet"] = req.clue_sheet

        manager.save_project(project_name, project)
        return {"success": True, "clue": clue}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/clues/{clue_name}")
async def delete_clue(project_name: str, clue_name: str):
    """删除线索"""
    try:
        manager = get_project_manager()
        project = manager.load_project(project_name)

        if clue_name not in project["clues"]:
            raise HTTPException(status_code=404, detail=f"线索 '{clue_name}' 不存在")

        del project["clues"][clue_name]
        manager.save_project(project_name, project)
        return {"success": True, "message": f"线索 '{clue_name}' 已删除"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
