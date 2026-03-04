"""
API 调用统计路由

提供调用记录查询和统计摘要接口。
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from lib.usage_tracker import UsageTracker

router = APIRouter()

_tracker = UsageTracker()


@router.get("/usage/stats")
async def get_stats(
    project_name: Optional[str] = Query(None, description="项目名称（可选）"),
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    stats = await _tracker.get_stats(
        project_name=project_name,
        start_date=start,
        end_date=end,
    )
    return stats


@router.get("/usage/calls")
async def get_calls(
    project_name: Optional[str] = Query(None, description="项目名称"),
    call_type: Optional[str] = Query(None, description="调用类型 (image/video)"),
    status: Optional[str] = Query(None, description="状态 (success/failed)"),
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页记录数"),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    result = await _tracker.get_calls(
        project_name=project_name,
        call_type=call_type,
        status=status,
        start_date=start,
        end_date=end,
        page=page,
        page_size=page_size,
    )
    return result


@router.get("/usage/projects")
async def get_projects_list():
    projects = await _tracker.get_projects_list()
    return {"projects": projects}
