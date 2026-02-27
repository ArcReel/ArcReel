"""
API 调用统计路由

提供调用记录查询和统计摘要接口。
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from lib.usage_tracker import UsageTracker

router = APIRouter()

# 数据库路径（存放在 projects 目录下）
project_root = Path(__file__).parent.parent.parent.parent
db_path = project_root / "projects" / ".api_usage.db"


def get_usage_tracker() -> UsageTracker:
    """获取 UsageTracker 实例"""
    return UsageTracker(db_path)


# ==================== 统计摘要 ====================

@router.get("/usage/stats")
async def get_stats(
    project_name: Optional[str] = Query(None, description="项目名称（可选）"),
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
):
    """
    获取统计摘要

    Returns:
        - total_cost: 总费用（美元）
        - image_count: 图片调用次数
        - video_count: 视频调用次数
        - failed_count: 失败次数
        - total_count: 总调用次数
    """
    tracker = get_usage_tracker()

    # 解析日期
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    stats = tracker.get_stats(
        project_name=project_name,
        start_date=start,
        end_date=end,
    )

    return stats


# ==================== 调用记录列表 ====================

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
    """
    获取调用记录列表（分页）

    Returns:
        - items: 调用记录列表
        - total: 总记录数
        - page: 当前页码
        - page_size: 每页大小
    """
    tracker = get_usage_tracker()

    # 解析日期
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    result = tracker.get_calls(
        project_name=project_name,
        call_type=call_type,
        status=status,
        start_date=start,
        end_date=end,
        page=page,
        page_size=page_size,
    )

    return result


# ==================== 项目列表 ====================

@router.get("/usage/projects")
async def get_projects_list():
    """
    获取有调用记录的项目列表

    用于前端筛选下拉框。

    Returns:
        项目名称列表
    """
    tracker = get_usage_tracker()
    projects = tracker.get_projects_list()
    return {"projects": projects}
