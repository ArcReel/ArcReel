"""
API 调用统计路由

提供调用记录查询和统计摘要接口。
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from lib.db import async_session_factory
from lib.db.repositories.usage_repo import UsageCursor, UsageCursorError, UsageFilters, UsageRepository
from lib.i18n import Locale, Translator, translate_or
from lib.providers import CallStatus, CallType

router = APIRouter()
_CALL_STATUS_DESCRIPTION = f"状态 ({'/'.join(CallStatus)})"


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
    status: CallStatus | None = Query(None, description=_CALL_STATUS_DESCRIPTION),
    start_date: str | None = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="结束日期 (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页记录数"),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    async with async_session_factory() as session:
        return await UsageRepository(session).get_calls(
            call_id=call_id,
            project_name=project_name,
            call_type=call_type,
            status=status,
            start_date=start,
            end_date=end,
            page=page,
            page_size=page_size,
        )


@router.get("/usage/projects")
async def get_projects_list():
    async with async_session_factory() as session:
        projects = await UsageRepository(session).get_projects_list()
    return {"projects": projects}


# ---------------------------------------------------------------------------
# 使用记录读接口；与上面三个返回裸 dict 的旧接口并存，新接口用 Pydantic 声明响应形状。
# ---------------------------------------------------------------------------


class UsageRecord(BaseModel):
    """一次供应商调用在列表里的投影。``user_id`` 不出现。"""

    id: int
    project_name: str
    purpose: str | None = None
    task_id: str | None = None
    task_type: str | None = None
    media_type: str
    provider: str
    model: str
    status: str
    error_code: str | None = None
    error_params: Any = None
    error_message: str | None = None
    segment_id: str | None = None
    output_path: str | None = None
    started_at: str
    finished_at: str | None = None
    duration_ms: int | None = None
    cost_amount: float
    currency: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage_tokens: int | None = None
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None
    resolution: str | None = None
    duration_seconds: int | None = None
    aspect_ratio: str | None = None
    session_id: str | None = None


class UsageRecordDetail(UsageRecord):
    """详情比列表多三项重载荷，列表里不返回。"""

    prompt: str | None = None
    inputs: Any = None
    last_provider_response: Any = None


class UsageRecordPage(BaseModel):
    """keyset 分页的一页；``next_cursor`` 为空表示已到末页。"""

    items: list[UsageRecord]
    next_cursor: str | None = None
    total: int


def _multi(value: str | None) -> tuple[str, ...]:
    """逗号分隔的多选参数；空串与纯空白项丢弃，整体为空表示该维度不筛。"""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@router.get("/usage/records", response_model=UsageRecordPage)
async def list_usage_records(
    _t: Translator,
    project_name: str | None = Query(None, description="项目名称；端点试跑记录用空串"),
    provider: str | None = Query(None, description="供应商 id，逗号分隔多选"),
    model: str | None = Query(None, description="模型，逗号分隔多选"),
    media_type: str | None = Query(None, description="媒体类型 (image/video/text/audio)，逗号分隔多选"),
    status: str | None = Query(None, description=f"{_CALL_STATUS_DESCRIPTION}，逗号分隔多选"),
    segment_id: str | None = Query(None, description="分镜 id，逗号分隔多选"),
    since: datetime | None = Query(None, description="起始时刻（含），ISO 8601，无时区按 UTC"),
    until: datetime | None = Query(None, description="结束时刻（不含），ISO 8601，无时区按 UTC"),
    limit: int = Query(20, ge=1, le=200, description="每页记录数"),
    cursor: str | None = Query(None, description="上一页返回的不透明游标"),
) -> UsageRecordPage:
    try:
        decoded = UsageCursor.decode(cursor) if cursor else None
    except UsageCursorError as exc:
        raise HTTPException(status_code=422, detail=_t("usage_cursor_invalid")) from exc

    async with async_session_factory() as session:
        page = await UsageRepository(session).list_records(
            filters=UsageFilters(
                project_name=project_name,
                providers=_multi(provider),
                models=_multi(model),
                media_types=_multi(media_type),
                since=since,
                until=until,
            ),
            statuses=_multi(status),
            segment_ids=_multi(segment_id),
            limit=limit,
            cursor=decoded,
        )
    return UsageRecordPage.model_validate(page)


@router.get("/usage/records/{record_id}", response_model=UsageRecordDetail)
async def get_usage_record(record_id: int, _t: Translator) -> UsageRecordDetail:
    async with async_session_factory() as session:
        record = await UsageRepository(session).get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=_t("usage_record_not_found"))
    return UsageRecordDetail.model_validate(record)
