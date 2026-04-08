"""
API call statistics routes.

Provides endpoints for querying call records and statistical summaries.
"""

from datetime import datetime

from fastapi import APIRouter, Query

from lib.usage_tracker import UsageTracker
from server.auth import CurrentUser

router = APIRouter()

_tracker = UsageTracker()


@router.get("/usage/stats")
async def get_stats(
    _user: CurrentUser,
    project_name: str | None = Query(None, description="Project name (optional)"),
    provider: str | None = Query(None, description="Filter by provider"),
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    group_by: str | None = Query(None, description="Grouping: provider"),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    if group_by == "provider":
        stats = await _tracker.get_stats_grouped_by_provider(
            project_name=project_name,
            provider=provider,
            start_date=start,
            end_date=end,
        )
    else:
        stats = await _tracker.get_stats(
            project_name=project_name,
            provider=provider,
            start_date=start,
            end_date=end,
        )
    return stats


@router.get("/usage/calls")
async def get_calls(
    _user: CurrentUser,
    project_name: str | None = Query(None, description="Project name"),
    call_type: str | None = Query(None, description="Call type (image/video)"),
    status: str | None = Query(None, description="Status (success/failed)"),
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Records per page"),
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
async def get_projects_list(_user: CurrentUser):
    projects = await _tracker.get_projects_list()
    return {"projects": projects}
