"""
API 调用统计路由

提供调用记录查询和统计摘要接口。
"""

from datetime import datetime

from fastapi import APIRouter, Query

from lib.db import async_session_factory
from lib.db.repositories.usage_repo import UsageRepository
from lib.i18n import Locale, translate_or
from lib.providers import CallType

router = APIRouter()


@router.get("/usage/stats")
async def get_stats(
    locale: Locale,
    project_name: str | None = Query(None, description="项目名称（可选）"),
    provider: str | None = Query(None, description="按供应商筛选"),
    start_date: str | None = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="结束日期 (YYYY-MM-DD)"),
    group_by: str | None = Query(None, description="分组方式: provider"),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    async with async_session_factory() as session:
        repo = UsageRepository(session)
        if group_by == "provider":
            stats = await repo.get_stats_grouped_by_provider(
                project_name=project_name,
                provider=provider,
                start_date=start,
                end_date=end,
            )
            # 仓储按默认语言写入 display_name；有译名表的内置供应商按请求语言改写，
            # 未登记的（自定义供应商用户自填的名字）原样保留，与 /providers 目录同一张表。
            for stat in stats["stats"]:
                name = stat["display_name"]
                if name:
                    stat["display_name"] = translate_or(f"provider_name_{stat['provider']}", name, locale)
        else:
            stats = await repo.get_stats(
                project_name=project_name,
                provider=provider,
                start_date=start,
                end_date=end,
            )
    return stats


@router.get("/usage/calls")
async def get_calls(
    call_id: int | None = Query(None, ge=1, description="调用记录 ID"),
    project_name: str | None = Query(None, description="项目名称"),
    call_type: CallType | None = Query(None, description="调用类型 (image/video/text)"),
    status: str | None = Query(None, description="状态 (success/failed)"),
    start_date: str | None = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="结束日期 (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页记录数"),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    async with async_session_factory() as session:
        result = await UsageRepository(session).get_calls(
            call_id=call_id,
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
    async with async_session_factory() as session:
        projects = await UsageRepository(session).get_projects_list()
    return {"projects": projects}
