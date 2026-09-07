"""
API 调用统计路由

提供调用记录查询和统计摘要接口。
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from lib.api_errors import NotFoundError, UnprocessableError
from lib.db import async_session_factory
from lib.db.repositories.usage_repo import UsageCursor, UsageCursorError, UsageFilters, UsageRepository, as_utc
from lib.i18n import Locale, translate_or
from lib.providers import CallStatus, CallType
from lib.usage_summary import UsageFilterOptions, UsageWindowTooWideError, build_summary

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


# 时间参数可表示的 UTC 区间：留出一天余量，任何时区偏移、半开右端的减一微秒与本地日折算
# 都不会越过 datetime 的边界。
_EARLIEST_INSTANT = datetime(1, 1, 2, tzinfo=UTC)
_LATEST_INSTANT = datetime(9999, 12, 30, tzinfo=UTC)


def _utc_instant(value: datetime | None) -> datetime | None:
    """时间参数换算到 UTC；落在可表示区间之外的时刻 422，而不是在换算或折日时溢出成 500。"""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not _EARLIEST_INSTANT <= aware <= _LATEST_INSTANT:
        raise UnprocessableError("usage_time_out_of_range")
    return as_utc(value)


def _multi(value: str | None) -> tuple[str, ...]:
    """逗号分隔的多选参数；空串与纯空白项丢弃，整体为空表示该维度不筛。"""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@router.get("/usage/records", response_model=UsageRecordPage)
async def list_usage_records(
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
        raise UnprocessableError("usage_cursor_invalid") from exc

    async with async_session_factory() as session:
        page = await UsageRepository(session).list_records(
            filters=UsageFilters(
                project_name=project_name,
                providers=_multi(provider),
                models=_multi(model),
                media_types=_multi(media_type),
                since=_utc_instant(since),
                until=_utc_instant(until),
            ),
            statuses=_multi(status),
            segment_ids=_multi(segment_id),
            limit=limit,
            cursor=decoded,
        )
    return UsageRecordPage.model_validate(page)


@router.get("/usage/records/{record_id}", response_model=UsageRecordDetail)
async def get_usage_record(record_id: int) -> UsageRecordDetail:
    async with async_session_factory() as session:
        record = await UsageRepository(session).get_record(record_id)
    if record is None:
        raise NotFoundError("usage_record_not_found")
    return UsageRecordDetail.model_validate(record)


# --- 汇总读接口（GET /usage/summary）---------------------------------------------------
# 设置页总览与顶栏入口共用这一次请求：KPI、日桶趋势、三维构成、需要关注与筛选候选值。
# 聚合规则在 lib/usage_summary.py，这里只负责取参、取行与形状声明。


class UsageStats(BaseModel):
    """一组调用的计数与分币种参考费用。"""

    calls: int
    success: int
    failed: int
    cancelled: int
    success_rate: float | None
    cost: dict[str, float]


class UsageRange(BaseModel):
    """趋势覆盖的本地日区间，两端均含，与 daily 首尾桶一致。"""

    since: str
    until: str


class UsageDailyBucket(BaseModel):
    date: str
    success: int
    failed: int
    cancelled: int
    cost_by_media_type: dict[str, float]


class UsageProjectRow(UsageStats):
    project_name: str


class UsageProviderRow(UsageStats):
    provider: str


class UsageModelRow(UsageStats):
    provider: str
    model: str


class UsageBreakdownOther(UsageStats):
    """构成表列表外的余量：被合并的分组数与它们的合计。"""

    groups: int


class UsageProjectBreakdown(BaseModel):
    rows: list[UsageProjectRow]
    other: UsageBreakdownOther | None


class UsageProviderBreakdown(BaseModel):
    rows: list[UsageProviderRow]
    other: UsageBreakdownOther | None


class UsageModelBreakdown(BaseModel):
    rows: list[UsageModelRow]
    other: UsageBreakdownOther | None


class UsageBreakdown(BaseModel):
    project: UsageProjectBreakdown
    provider: UsageProviderBreakdown
    model: UsageModelBreakdown


class UsageFailureRateAttention(BaseModel):
    type: Literal["failure_rate"]
    provider: str
    model: str | None
    success: int
    failed: int
    failure_rate: float
    overall_failure_rate: float


class UsageConsecutiveFailuresAttention(BaseModel):
    type: Literal["consecutive_failures"]
    project_name: str
    media_type: str
    segment_id: str
    count: int
    first_failed_at: str
    last_failed_at: str
    last_error_code: str | None


class UsageProviderOption(BaseModel):
    provider: str
    label: str


class UsageModelOption(BaseModel):
    provider: str
    model: str


class UsageFilterOptionsResponse(BaseModel):
    projects: list[str]
    providers: list[UsageProviderOption]
    models: list[UsageModelOption]


class UsageSummaryResponse(BaseModel):
    range: UsageRange | None
    primary_currency: str | None
    kpi: UsageStats
    daily: list[UsageDailyBucket]
    breakdown: UsageBreakdown
    attention: list[
        Annotated[UsageFailureRateAttention | UsageConsecutiveFailuresAttention, Field(discriminator="type")]
    ]
    filter_options: UsageFilterOptionsResponse


@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    locale: Locale,
    tz: str = Query("UTC", description="IANA 时区名，按此切天"),
    project_name: str | None = Query(None, description="项目名称（空串筛选端点试跑）"),
    provider: str | None = Query(None, description="按供应商筛选"),
    model: str | None = Query(None, description="按模型筛选"),
    media_type: CallType | None = Query(None, description="媒体类型 (image/video/text/audio)"),
    since: datetime | None = Query(None, description="起始时刻（含），ISO 8601，无时区按 UTC"),
    until: datetime | None = Query(None, description="结束时刻（不含），ISO 8601，无时区按 UTC"),
) -> dict[str, object]:
    """一次返回总览所需的全部聚合；不传 since / until 即全部时间。

    不收 ``status``：pending 不进聚合，其余三个终态一起构成 KPI 的口径，按状态再切会让
    调用次数与成功率互相矛盾。记录表的状态筛选带上来时在此被忽略。

    since / until 展开的日桶超过 ``MAX_DAILY_BUCKETS`` 返回 422：桶数由这两个时刻直接决定，
    不设上界会让一对离谱的时刻在服务端同步铺出几百万个桶。
    """
    # 解析不了的时区名直接 422，不静默回落 UTC——切天口径错了整张趋势图都会错位。
    try:
        zone = ZoneInfo(tz)
    except (KeyError, ValueError, OSError) as exc:
        raise UnprocessableError("usage_timezone_invalid") from exc
    since_utc = _utc_instant(since)
    until_utc = _utc_instant(until)
    filters = UsageFilters(
        project_name=project_name,
        providers=(provider,) if provider else (),
        models=(model,) if model else (),
        media_types=(media_type,) if media_type else (),
        since=since_utc,
        until=until_utc,
    )

    async with async_session_factory() as session:
        repo = UsageRepository(session)
        rows = await repo.fetch_summary_rows(filters=filters)
        options = await repo.fetch_usage_filter_options()

    # 仓储按默认语言给出目录里的显示名；有译名表的内置供应商按请求语言改写，自定义供应商
    # 用户自填的名字原样保留，与 /providers 目录同一张表。
    localized = UsageFilterOptions(
        projects=options.projects,
        providers=[
            (provider_id, translate_or(f"provider_name_{provider_id}", label, locale))
            for provider_id, label in options.providers
        ],
        models=options.models,
    )
    try:
        return build_summary(rows, tz=zone, since=since_utc, until=until_utc, filter_options=localized)
    except UsageWindowTooWideError as exc:
        raise UnprocessableError("usage_range_too_wide") from exc
